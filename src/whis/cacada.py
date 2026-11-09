"""A caçada: lê as fontes, monta o perfil, faz a triagem e deixa tudo mastigado no vault.

`cacar` é a costura da entrega 1 de `.scratch/vagas-e-freelas/`. Corre por
horas, chamada à mão na véspera da virada semanal do plano, e **não envia nada
a ninguém**: o que sai é o relatório `cacada/<corrida>.md` e, para cada chance
boa, um pacote `cacada/<corrida>/<slug>.md` com a proposta pronta e, no freela
web, a amostra. Ele lê, ajusta e envia.

As etapas, na ordem que gasta menos se a corrida parar no meio (research 07):

1. **perfil** — evidências da máquina (`evidencias`) e *Sobre mim* → o perfil;
2. **coleta** — as fontes em Python (`fontes_da_cacada`) e o explorador na web;
3. **triagem** — tudo, em lotes, sem ferramenta;
4. **preparo** — as chances boas, da melhor para a pior;
5. **apostas** — a mensagem curta de cada tentar a sorte.

Cada etapa grava o que produziu no checkpoint (`corrida.Checkpoint`), e o
relatório é reescrito ao fim de cada uma — se a corrida parar, o que já
existe está no vault, e a próxima chamada retoma de onde parou.

O plano manda no ritmo: a janela de 5 h que bate **pausa** a corrida até ela
abrir (com o PC acordado); a semanal no teto (100%, o padrão), ou a menos de 5 min de
virar, **encerra** — a semana nova é a do trabalho dele. Longe da virada (mais
de 24 h), o `LimiteDaCacada` também encerra: tantos minutos ou tantos pontos do
plano gastos nesta chamada, e a próxima retoma.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from . import vault as _vault
from .cacada_claude import ErroDaCacada, LimiteDoPlano, Plano
from .configuracao import Configuracao
from .corrida import Checkpoint, escrever_andamento
from .criterios_da_cacada import CAMINHO_DOS_CRITERIOS, PASTA_CACADA, Criterios, FonteDaCacada, ler_criterios
from .freelas_rapidos import MODO_FREELAS, PEDIDO_DA_RODADA_EXTRA, criterios_de_freelas, rebaixar_fora_do_nicho
from .oportunidades import (
    CHANCE_BOA,
    DESCARTADA,
    FREELA,
    TENTAR_A_SORTE,
    Aposta,
    Oportunidade,
    Pacote,
    Perfil,
    Veredito,
    link_canonico,
)
from .precos import conferir_piso, pedido_do_piso
from .prova_do_portfolio import conferir_prova, pedido_do_portfolio, sem_a_vitrine

_log = logging.getLogger("whis.cacada")

CAMINHO_DO_PERFIL = f"{PASTA_CACADA}/perfil.md"
CAMINHO_DO_PERFIL_DE_FREELAS = f"{PASTA_CACADA}/perfil-freelas.md"
ZONAS_DA_CACADA = (PASTA_CACADA,)

LOTE_DA_TRIAGEM = 20
LOTE_DAS_APOSTAS = 8
MAXIMO_DE_APOSTAS = 60
#: Fora da véspera (a semana vira em mais de 36 h), a corrida para mais cedo: a cota é do trabalho.
TETO_FORA_DA_VESPERA = 70
VESPERA = timedelta(hours=36)
MARGEM_DA_VIRADA = timedelta(minutes=5)
MAXIMO_DA_PAUSA = timedelta(hours=5, minutes=15)
PAUSA_SEM_HORA = timedelta(minutes=30)
FOLGA_DEPOIS_DA_VIRADA = timedelta(minutes=2)
PAUSAS_POR_CHAMADA = 3
RODADAS_EXTRAS_DE_BUSCA = 30
RODADAS_EXTRAS_MINIMAS = 3
IDADE_MAXIMA_DA_RETOMADA = timedelta(days=4)
PERTO_DA_VIRADA = timedelta(hours=24)
#: A caçada agendada para com a janela de 5 h neste ponto: o resto da sessão nunca é dela.
TETO_DA_SESSAO_AGENDADA = 90


class Parada(RuntimeError):
    """A corrida encerra antes do fim, com o que já tem: o motivo vai para o relatório."""


class LimiteBatido(Parada):
    """O limite de tempo ou de plano desta corrida bateu: ela para entre chamadas e fica retomável."""


@dataclass(frozen=True)
class LimiteDaCacada:
    """Quanto esta corrida pode durar (`minutos`) e gastar do plano semanal (`plano`, em pontos); 0 = sem esse limite.

    `automatico`: com a virada da semana do plano a menos de 24 h do início, nenhum
    dos dois vale — a madrugada antes da virada é para gastar o que sobrou.
    """

    minutos: int = 0
    plano: int = 0
    automatico: bool = False

    @property
    def algum(self) -> bool:
        return bool(self.minutos or self.plano)

    @classmethod
    def escolher(
        cls, configuracao: Configuracao, *, minutos: int | None = None, plano: int | None = None, sem_limite: bool = False
    ) -> LimiteDaCacada:
        """O da linha de comando por cima do `[cacada]`; qualquer opção dada desliga o automático."""
        if sem_limite:
            return cls()
        return cls(
            minutos=configuracao.cacada_limite_minutos if minutos is None else minutos,
            plano=configuracao.cacada_limite_plano if plano is None else plano,
            automatico=minutos is None and plano is None,
        )

    def descrever(self) -> str:
        partes = [f"{self.minutos} min" if self.minutos else "", f"{self.plano} pontos do plano semanal" if self.plano else ""]
        return " ou ".join(p for p in partes if p)


class JanelaEsgotada(RuntimeError):
    """A janela de 5 h acabou durante o extra do modo freelas: o extra encerra, a corrida não."""


class PortaPerfil(Protocol):
    ultimo_plano: Plano | None
    custo_usd: float

    def montar(self, evidencias: str, procuro: str, sobre_mim: str) -> Perfil: ...


class PortaExplorador(Protocol):
    ultimo_plano: Plano | None
    custo_usd: float

    def explorar(
        self, pedido: str, perfil: Perfil, procuro: str, sobre_mim: str, ja_vistas: Sequence[str], nome: str
    ) -> tuple[Oportunidade, ...]: ...


class PortaTriagem(Protocol):
    ultimo_plano: Plano | None
    custo_usd: float

    def triar(self, oportunidades: Sequence[Oportunidade], perfil: Perfil, procuro: str, sobre_mim: str) -> tuple[Veredito, ...]: ...


class PortaApostas(Protocol):
    ultimo_plano: Plano | None
    custo_usd: float

    def escrever(
        self, oportunidades: Sequence[tuple[Oportunidade, Veredito]], perfil: Perfil, procuro: str, sobre_mim: str
    ) -> tuple[Aposta, ...]: ...


class PortaPreparo(Protocol):
    ultimo_plano: Plano | None
    custo_usd: float

    def preparar(
        self, oportunidade: Oportunidade, veredito: Veredito, pagina: str, perfil: Perfil, procuro: str, sobre_mim: str
    ) -> Pacote: ...

    def resumir(
        self, oportunidade: Oportunidade, veredito: Veredito, pagina: str, perfil: Perfil, procuro: str, sobre_mim: str
    ) -> Pacote: ...


@dataclass
class PortasDaCacada:
    """Os cinco adaptadores do Claude e, para os testes, a rede, a coleta e as amostras."""

    perfil: PortaPerfil
    explorador: PortaExplorador
    triagem: PortaTriagem
    apostas: PortaApostas
    preparo: PortaPreparo
    rede: httpx.BaseTransport | None = None
    coletar: Callable[..., Any] | None = None
    ler_detalhe: Callable[[Oportunidade], str] | None = None
    evidencias: Callable[[Configuracao], str] | None = None
    amostras: bool = True
    navegador: Any | None = None
    registro: Any | None = None
    ler_caixa: Callable[[], Sequence[Any]] | None = None
    navegador_logado: Any | None = None

    def todas(self) -> tuple[Any, ...]:
        portas = (self.perfil, self.explorador, self.triagem, self.apostas, self.preparo)
        return portas + ((self.navegador,) if self.navegador is not None else ())


@dataclass
class ResultadoDaCacada:
    """O que a corrida deixou: o relatório, as contagens e, quando parou, o porquê."""

    relatorio: str
    corrida: str
    chances_boas: int = 0
    tentar_a_sorte: int = 0
    descartadas: int = 0
    pacotes: int = 0
    fontes_com_falha: dict[str, str] = field(default_factory=dict)
    parada: str = ""
    perguntas: tuple[str, ...] = ()
    custo_usd: float = 0.0
    duracao_s: float = 0.0
    plano_antes: int | None = None
    plano_depois: int | None = None
    enviadas: int = 0
    precisam_de_voce: int = 0
    sobrou_plano: bool = False
    com_limite: bool = False
    limite_batido: str = ""

    @property
    def completa(self) -> bool:
        return not self.parada


# -- a guarda do plano --------------------------------------------------------------------------


class GuardaDoPlano:
    """Lê o plano depois de cada chamada e decide: segue, pausa ou para.

    `atravessa_virada` (modo freelas, no principal): em vez de parar na virada
    da semana ou no teto semanal da véspera, espera a semana virar e segue, uma
    vez só. `fim_do_extra` (modo freelas, no extra): nada passa desta hora, e o
    plano acabando encerra o extra com `JanelaEsgotada` em vez de parar a corrida.

    `limite` (`LimiteDaCacada`) conta desde a criação da guarda, isto é, desta
    chamada de `hunt` (retomar ganha limite novo), e as pausas da janela de 5 h
    contam no tempo. O automático decide uma vez só, quando a virada fica
    conhecida (`virada_conhecida`, ou o primeiro plano lido): decidir de novo
    com a virada chegando mudaria a regra no meio da corrida. Sem virada
    conhecida, os limites valem.
    """

    def __init__(
        self,
        teto_semanal: int,
        *,
        relogio: Callable[[], datetime],
        dormir: Callable[[float], None],
        ao_pausar: Callable[[str], None] | None = None,
        limite: LimiteDaCacada | None = None,
        virada_conhecida: datetime | None = None,
    ) -> None:
        self.teto_semanal = teto_semanal
        self._relogio = relogio
        self._dormir = dormir
        self._ao_pausar = ao_pausar
        self.plano: Plano | None = None
        self.primeiro: Plano | None = None
        self.pausas: list[str] = []
        self.atravessa_virada = False
        self.virada_atravessada = False
        self.fim_do_extra: datetime | None = None
        self.limite = limite or LimiteDaCacada()
        self.inicio = relogio()
        self.limite_batido = ""
        self._virada_conhecida = virada_conhecida
        self._sem_limite_pela_virada: bool | None = None if self.limite.automatico else False
        #: A caçada agendada (`agendar`): não espera a janela de 5 h, para nela, e tem teto semanal próprio.
        self.agendada = False
        self.teto_da_sessao: int | None = None
        self.teto_fora_da_vespera = TETO_FORA_DA_VESPERA
        self.vespera = VESPERA

    def agendar(self, teto_semanal_agendado: int) -> None:
        """A caçada que roda sozinha de madrugada: o plano dele vem primeiro.

        Fora das 24 h antes da virada, para com a semana em `teto_semanal_agendado`;
        nunca espera a janela de 5 h abrir (para, e a próxima noite retoma) e para
        com ela em `TETO_DA_SESSAO_AGENDADA`; nunca atravessa a virada da semana.
        """
        self.agendada = True
        self.atravessa_virada = False
        self.teto_da_sessao = TETO_DA_SESSAO_AGENDADA
        self.teto_fora_da_vespera = min(TETO_FORA_DA_VESPERA, teto_semanal_agendado)
        self.vespera = PERTO_DA_VIRADA

    def agora(self) -> datetime:
        return self._relogio()

    def ler(self, porta: Any) -> None:
        plano = getattr(porta, "ultimo_plano", None)
        if plano is not None:
            self.plano = plano
            self.primeiro = self.primeiro or plano

    def teto(self) -> int:
        vira = self.plano.vira_sete_dias if self.plano else None
        if vira is not None and vira - self._relogio() > self.vespera:
            return min(self.teto_semanal, self.teto_fora_da_vespera)
        if vira is None and self.agendada:
            return min(self.teto_semanal, self.teto_fora_da_vespera)
        return self.teto_semanal

    def limitada(self) -> bool:
        if not self.limite.algum:
            return False
        if self._sem_limite_pela_virada is None:
            vira = (self.plano.vira_sete_dias if self.plano else None) or self._virada_conhecida
            if vira is not None:
                self._sem_limite_pela_virada = vira - self.inicio < PERTO_DA_VIRADA
        return not self._sem_limite_pela_virada

    def plano_gasto(self) -> int | None:
        if self.primeiro is None or self.plano is None or self.primeiro.sete_dias is None or self.plano.sete_dias is None:
            return None
        return max(0, self.plano.sete_dias - self.primeiro.sete_dias)

    def fim_pelo_limite(self) -> datetime | None:
        if not self.limitada() or not self.limite.minutos:
            return None
        return self.inicio + timedelta(minutes=self.limite.minutos)

    def limite_estourado(self) -> str:
        """O motivo, quando o limite desta corrida já bateu; vazio quando não."""
        if not self.limitada():
            return ""
        fim = self.fim_pelo_limite()
        if fim is not None and self._relogio() >= fim:
            correu = int((self._relogio() - self.inicio).total_seconds() // 60)
            return f"o limite de tempo desta caçada bateu: correu {correu} min (o limite é {self.limite.minutos} min)"
        gasto = self.plano_gasto()
        if self.limite.plano and gasto is not None and gasto >= self.limite.plano:
            antes = self.primeiro.sete_dias if self.primeiro else None
            agora = self.plano.sete_dias if self.plano else None
            return (
                f"o limite de plano desta caçada bateu: gastou {gasto} pontos do plano semanal, "
                f"de {antes}% a {agora}% (o limite é {self.limite.plano})"
            )
        return ""

    def conferir(self, *, limite: bool = True) -> None:
        """Levanta `Parada` quando a semana passou do teto ou está para virar, e `LimiteBatido` no limite da corrida."""
        if limite and (motivo := self.limite_estourado()):
            self.limite_batido = motivo
            if self.fim_do_extra is not None:
                raise JanelaEsgotada(motivo)
            raise LimiteBatido(motivo)
        if self.plano is None:
            return
        sessao = self.plano.cinco_horas
        if self.teto_da_sessao is not None and sessao is not None and sessao >= self.teto_da_sessao:
            motivo = (
                f"a janela de 5 h do plano chegou a {sessao}% (a caçada agendada para em {self.teto_da_sessao}% "
                "e retoma na próxima noite)"
            )
            if self.fim_do_extra is not None:
                raise JanelaEsgotada(motivo)
            raise Parada(motivo)
        semanal = self.plano.sete_dias
        vira = self.plano.vira_sete_dias
        no_teto = semanal is not None and semanal >= self.teto()
        virando = vira is not None and vira - self._relogio() < MARGEM_DA_VIRADA
        if self.fim_do_extra is not None:
            if no_teto or virando or self._relogio() >= self.fim_do_extra:
                raise JanelaEsgotada("o plano da semana acabou ou a virada chegou")
            return
        if (no_teto or virando) and self._pode_atravessar(vira):
            self._esperar_virada(vira)
            return
        if no_teto:
            raise Parada(f"o plano semanal chegou a {semanal}% (o teto desta corrida é {self.teto()}%)")
        if virando:
            raise Parada("a semana do plano está para virar: daqui em diante a cota é a do trabalho")

    def chamar(self, porta: Any, funcao: Callable[[], Any]) -> Any:
        """Chama o Claude com paciência: espera a janela de 5 h abrir; para na semanal."""
        for tentativa in range(PAUSAS_POR_CHAMADA + 1):
            self.conferir()
            try:
                resultado = funcao()
            except LimiteDoPlano as limite:
                self.ler(porta)
                if self.fim_do_extra is not None and (limite.semanal or limite.retoma_em is None or limite.retoma_em >= self.fim_do_extra):
                    raise JanelaEsgotada("o plano acabou antes da virada") from limite
                if limite.semanal:
                    vira = limite.retoma_em or (self.plano.vira_sete_dias if self.plano else None)
                    if self._pode_atravessar(vira):
                        self._esperar_virada(vira)
                        continue
                    raise Parada("o limite semanal do plano bateu") from limite
                if self.agendada:
                    motivo = "a janela de 5 h do plano bateu (a caçada agendada não espera: retoma na próxima noite)"
                    if self.fim_do_extra is not None:
                        raise JanelaEsgotada(motivo) from limite
                    raise Parada(motivo) from limite
                if tentativa >= PAUSAS_POR_CHAMADA:
                    raise Parada("a janela de 5 h bateu de novo depois de três pausas") from limite
                self._pausar(limite)
                continue
            self.ler(porta)
            if self.fim_do_extra is None:
                self.conferir(limite=False)
            return resultado
        raise Parada("a janela de 5 h não abriu")

    def _pode_atravessar(self, vira: datetime | None) -> bool:
        return (
            self.atravessa_virada
            and not self.limitada()
            and not self.virada_atravessada
            and vira is not None
            and vira - self._relogio() <= VESPERA
        )

    def _esperar_virada(self, vira: datetime) -> None:
        self.virada_atravessada = True
        agora = self._relogio()
        espera = max(vira + FOLGA_DEPOIS_DA_VIRADA - agora, timedelta(minutes=1))
        aviso = f"pausada até {(agora + espera).astimezone():%H:%M} (a semana do plano vira; o principal continua depois)"
        self.pausas.append(aviso)
        _log.warning("Caçada %s.", aviso)
        if self._ao_pausar is not None:
            self._ao_pausar(aviso)
        self._dormir(espera.total_seconds())
        if self.plano is not None:
            self.plano = replace(self.plano, sete_dias=None, vira_sete_dias=None)

    def _pausar(self, limite: LimiteDoPlano) -> None:
        agora = self._relogio()
        alvo = (limite.retoma_em + FOLGA_DEPOIS_DA_VIRADA) if limite.retoma_em else agora + PAUSA_SEM_HORA
        espera = min(max(alvo - agora, timedelta(minutes=1)), MAXIMO_DA_PAUSA)
        retoma = (agora + espera).astimezone()
        aviso = f"pausada até {retoma:%H:%M} (a janela de 5 h do plano bateu)"
        self.pausas.append(aviso)
        _log.warning("Caçada %s.", aviso)
        if self._ao_pausar is not None:
            self._ao_pausar(aviso)
        self._dormir(espera.total_seconds())


# -- a corrida -------------------------------------------------------------------------------------


def cacar(
    configuracao: Configuracao,
    portas: PortasDaCacada,
    *,
    pasta_do_estado: Path,
    do_zero: bool = False,
    modo: str = "",
    relogio: Callable[[], datetime] | None = None,
    dormir: Callable[[float], None] = time.sleep,
    ao_pausar: Callable[[str], None] | None = None,
    limite: LimiteDaCacada | None = None,
    agendada: bool = False,
) -> ResultadoDaCacada:
    """A corrida inteira, retomando do checkpoint quando há um inacabado e recente do mesmo modo.

    `modo="freelas"` é a caçada de freelas rápidos (`freelas_rapidos`). `limite`
    ausente é sem limite; o `hunt` passa o de `LimiteDaCacada.escolher`.
    `agendada` é a que roda sozinha de madrugada (`GuardaDoPlano.agendar`).
    """
    agora = relogio or (lambda: datetime.now().astimezone())
    inicio_monotonico = time.monotonic()
    raiz = _vault.preparar(configuracao.vault)

    checkpoint = Checkpoint.abrir(pasta_do_estado, do_zero=do_zero)
    if checkpoint.retomada and _velho(checkpoint.get("inicio"), agora()):
        _log.info("O checkpoint da caçada tem mais de %d dias: começando do zero.", IDADE_MAXIMA_DA_RETOMADA.days)
        checkpoint = Checkpoint.abrir(pasta_do_estado, do_zero=True)
    if checkpoint.retomada and checkpoint.get("modo", "") != modo:
        _log.info("A caçada inacabada é de outro modo: começando do zero.")
        checkpoint = Checkpoint.abrir(pasta_do_estado, do_zero=True)
    if not checkpoint.retomada:
        momento = agora()
        checkpoint.guardar(
            inicio=momento.isoformat(timespec="seconds"), corrida=f"{momento:%Y-%m-%d-%H%M}", etapa="perfil", modo=modo
        )

    criterios = _criterios_do_modo(configuracao, raiz, modo, checkpoint.get("cobertos") or ())
    estado = _Estado(configuracao, raiz, criterios, checkpoint, pasta_do_estado)
    guarda = GuardaDoPlano(
        configuracao.cacada_teto_semanal,
        relogio=agora,
        dormir=dormir,
        ao_pausar=ao_pausar,
        limite=limite,
        virada_conhecida=_virada_conhecida(pasta_do_estado, agora()) if limite and limite.automatico else None,
    )
    guarda.atravessa_virada = modo == MODO_FREELAS
    if agendada:
        guarda.agendar(configuracao.cacada_teto_da_cacada_agendada)
    parada = ""
    try:
        _etapa_do_perfil(estado, portas, guarda)
        _etapa_da_coleta(estado, portas, guarda, agora())
        _etapa_da_triagem(estado, portas, guarda)
        _escrever_relatorio(estado, guarda, parada="", em_andamento="preparando os pacotes")
        _etapa_do_preparo(estado, portas, guarda)
        _escrever_relatorio(estado, guarda, parada="", em_andamento="escrevendo as mensagens de tentar a sorte")
        _etapa_das_apostas(estado, portas, guarda)
        if estado.checkpoint.get("modo") == MODO_FREELAS:
            sobra = "o que sobra do limite desta caçada" if guarda.limitada() else "o que sobra do plano até a virada da semana"
            _escrever_relatorio(estado, guarda, parada="", em_andamento=f"usando {sobra}")
            _etapa_extra(estado, portas, guarda)
        guarda.atravessa_virada = False
        _etapa_do_envio(estado, portas, guarda)
        _etapa_da_caixa(estado, portas)
    except Parada as motivo:
        parada = str(motivo)
        _log.warning("A caçada parou antes do fim: %s.", parada)

    _etapa_do_funil(estado, portas)
    if not parada:
        checkpoint.guardar(etapa="terminada")
        checkpoint.terminar()
    escrever_andamento(pasta_do_estado, "terminada" if not parada else "parada", parada)
    caminho = _escrever_relatorio(estado, guarda, parada=parada)
    _depois_da_corrida(estado, portas, guarda)
    custo = sum(float(getattr(porta, "custo_usd", 0.0) or 0.0) for porta in portas.todas())
    vereditos = estado.vereditos()
    return ResultadoDaCacada(
        relatorio=caminho,
        corrida=estado.corrida,
        chances_boas=sum(1 for v in vereditos.values() if v.classe == CHANCE_BOA),
        tentar_a_sorte=len(tentar_a_sorte(estado).ficam),
        descartadas=sum(1 for v in vereditos.values() if v.classe == DESCARTADA),
        pacotes=len(estado.pacotes()),
        fontes_com_falha=dict(checkpoint.get("falhas") or {}),
        parada=parada,
        perguntas=estado.perguntas(),
        custo_usd=custo,
        duracao_s=round(time.monotonic() - inicio_monotonico, 1),
        enviadas=sum(1 for e in (checkpoint.get("envios") or {}).values() if e.get("status") == "feito"),
        precisam_de_voce=sum(1 for e in (checkpoint.get("envios") or {}).values() if e.get("status") == "precisa_de_voce"),
        plano_antes=guarda.primeiro.sete_dias if guarda.primeiro else None,
        plano_depois=guarda.plano.sete_dias if guarda.plano else None,
        sobrou_plano=bool(checkpoint.get("sobrou_plano")),
        com_limite=guarda.limitada(),
        limite_batido=guarda.limite_batido,
    )


def _criterios_do_modo(configuracao: Configuracao, raiz: Path, modo: str, cobertos: Sequence[str]) -> Criterios:
    criterios = ler_criterios(raiz)
    criterios = replace(criterios, portfolio=sem_a_vitrine(criterios.portfolio))
    if configuracao.cacada_piso_freela > 0:
        criterios = replace(criterios, procuro=f"{criterios.procuro.rstrip()}\n{pedido_do_piso(configuracao.cacada_piso_freela)}")
    if criterios.portfolio:
        criterios = replace(criterios, procuro=f"{criterios.procuro.rstrip()}\n{pedido_do_portfolio(criterios.portfolio)}")
    if modo != MODO_FREELAS:
        return criterios
    return criterios_de_freelas(
        criterios,
        configuracao.cacada_plataformas_sem_conta,
        cobertos=cobertos,
        plataformas_pagas=configuracao.cacada_plataformas_pagas,
        nicho=configuracao.cacada_nicho_freela,
    )


def _virada_conhecida(pasta: Path, agora: datetime) -> datetime | None:
    from .corrida import ler_virada
    from .lembrete import proxima_virada

    lida = ler_virada(pasta)
    return proxima_virada(lida, agora) if lida is not None else None


class _Estado:
    """O checkpoint com cara de dados da caçada."""

    def __init__(self, configuracao: Configuracao, raiz: Path, criterios: Criterios, checkpoint: Checkpoint, pasta: Path) -> None:
        self.configuracao = configuracao
        self.raiz = raiz
        self.criterios = criterios
        self.checkpoint = checkpoint
        self.pasta = pasta

    @property
    def corrida(self) -> str:
        return str(self.checkpoint.get("corrida"))

    def andamento(self, etapa: str, detalhe: str = "") -> None:
        self.checkpoint.guardar(etapa=etapa)
        escrever_andamento(self.pasta, etapa, detalhe)

    def perfil(self) -> Perfil | None:
        dados = self.checkpoint.get("perfil")
        return Perfil.de_json(dados) if isinstance(dados, dict) else None

    def oportunidades(self) -> dict[str, Oportunidade]:
        return {o["id"]: Oportunidade.de_json(o) for o in self.checkpoint.get("oportunidades") or []}

    def vereditos(self) -> dict[str, Veredito]:
        return {v["id"]: Veredito.de_json(v) for v in self.checkpoint.get("vereditos") or []}

    def pacotes(self) -> dict[str, dict[str, Any]]:
        return dict(self.checkpoint.get("pacotes") or {})

    def apostas(self) -> dict[str, Aposta]:
        return {chave: Aposta.de_json(valor) for chave, valor in (self.checkpoint.get("apostas") or {}).items()}

    def perguntas(self) -> tuple[str, ...]:
        """O que o perfil não conseguiu saber e só ele responde."""
        perfil = self.perfil()
        return tuple(perfil.perguntas) if perfil else ()

    def escrever(self, relativo: str, conteudo: str) -> Path:
        if relativo == CAMINHO_DOS_CRITERIOS:
            raise _vault.ForaDaZonaDePropriedade("cacada/criterios.md é dele: a caçada nunca escreve nela")
        return _vault.escrever(self.raiz, relativo, conteudo, zonas=ZONAS_DA_CACADA)


# -- as etapas --------------------------------------------------------------------------------------


def _etapa_do_perfil(estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano) -> None:
    if estado.perfil() is not None:
        return
    estado.andamento("perfil", "lendo o git e o vault")
    from .evidencias import montar_evidencias

    evidencias = (portas.evidencias or montar_evidencias)(estado.configuracao)
    criterios = estado.criterios
    try:
        perfil = guarda.chamar(portas.perfil, lambda: portas.perfil.montar(evidencias, criterios.procuro, criterios.sobre_mim))
    except ErroDaCacada as erro:
        _log.warning("O perfil não saiu (%s): a caçada segue com Sobre mim.", erro)
        perfil = Perfil(resumo=f"(o perfil automático não saiu: {erro}) Ver Sobre mim.")
    estado.checkpoint.guardar(perfil=perfil.para_json())
    if estado.checkpoint.get("modo") == MODO_FREELAS:
        nota = montar_nota_do_perfil(perfil, estado.corrida, freelas=True, nicho=estado.configuracao.cacada_nicho_freela)
        estado.escrever(CAMINHO_DO_PERFIL_DE_FREELAS, nota)
    else:
        estado.escrever(CAMINHO_DO_PERFIL, montar_nota_do_perfil(perfil, estado.corrida))


def _etapa_da_coleta(estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano, agora: datetime) -> None:
    if estado.checkpoint.get("coletada"):
        return
    from .fontes_da_cacada import coletar, fonte_paga, fonte_sem_conta

    criterios = estado.criterios
    ja_vistas = links_ja_vistos(estado.raiz, excluir=estado.corrida)
    estado.andamento("coleta", "lendo as fontes")
    sem_conta = estado.configuracao.cacada_plataformas_sem_conta
    pagas = estado.configuracao.cacada_plataformas_pagas
    coleta = (portas.coletar or coletar)(
        [f for f in criterios.fontes if not fonte_sem_conta(f, sem_conta) and not fonte_paga(f, pagas)], agora, rede=portas.rede, ao_ler=lambda nome: escrever_andamento(estado.pasta, "coleta", nome)
    )
    lidas: dict[str, int] = {nome: len(lista) for nome, lista in coleta.por_fonte.items()}
    falhas: dict[str, str] = dict(coleta.falhas)
    todas: dict[str, Oportunidade] = {o.id: o for o in coleta.todas()}

    perfil = estado.perfil() or Perfil(resumo="")
    if portas.navegador_logado is not None:
        estado.andamento("coleta", "lendo os sites logados no Chrome da caçada")
        for oportunidade in _coleta_logada(estado, portas, perfil, lidas):
            todas.setdefault(oportunidade.id, oportunidade)
        criterios = estado.criterios
    for fonte in (f for f in criterios.fontes if f.tipo == "web"):
        estado.andamento("coleta", f"explorando a web: {fonte.nome}")
        vistas_agora = sorted(ja_vistas | {o.link for o in todas.values()})
        try:
            achadas = guarda.chamar(
                portas.explorador,
                lambda fonte=fonte: portas.explorador.explorar(
                    fonte.argumento, perfil, criterios.procuro, criterios.sobre_mim, vistas_agora, fonte.nome
                ),
            )
        except ErroDaCacada as erro:
            falhas[fonte.nome] = str(erro)[:200]
            continue
        lidas[fonte.nome] = len(achadas)
        for oportunidade in achadas:
            todas.setdefault(oportunidade.id, oportunidade)

    novas = [o for o in todas.values() if o.link not in ja_vistas]
    repetidas = len(todas) - len(novas)
    estado.andamento("coleta", "conferindo se dá para propor")
    novas = _tirar_o_que_nao_da(estado, portas, guarda, novas)
    _log.info("Caçada: %d oportunidade(s) coletada(s), %d nova(s).", len(todas), len(novas))
    estado.checkpoint.guardar(
        oportunidades=[o.para_json() for o in novas],
        lidas=lidas,
        falhas=falhas,
        repetidas=repetidas,
        coletada=True,
    )


def _coleta_logada(estado: _Estado, portas: PortasDaCacada, perfil: Perfil, lidas: dict[str, int]) -> list[Oportunidade]:
    """O login de cada site para o relatório, a VintePila logada e as buscas web sem os sites cobertos.

    O Chrome fecha antes do explorador: o Claude não roda com ele aberto.
    """
    from . import navegador_logado as _logado
    from .fontes_da_cacada import coletar_vintepila, tipos_pagos

    achadas: list[Oportunidade] = []
    with portas.navegador_logado.abrir() as sessao:
        pagas = set(estado.configuracao.cacada_plataformas_pagas) | tipos_pagos(estado.configuracao.cacada_plataformas_pagas)
        logins = {site: _logado.estado_do_site(sessao, site) for site in _logado.NOMES if site not in pagas}
        estado.checkpoint.guardar(logins=logins)
        if logins.get(_logado.VINTEPILA) == _logado.LOGADO:
            vintepila = coletar_vintepila(sessao, perfil.stack)
            if not vintepila.aviso:
                lidas["VintePila (logado)"] = len(vintepila.oportunidades)
            achadas += vintepila.oportunidades
            estado.checkpoint.guardar(vintepila_aviso=vintepila.aviso, vintepila_cheios=vintepila.cheios)
    cobertos = [site for site in (_logado.NOVENTA_E_NOVE, _logado.VINTEPILA) if logins.get(site) == _logado.LOGADO]
    estado.checkpoint.guardar(cobertos=cobertos)
    estado.criterios = _criterios_do_modo(estado.configuracao, estado.raiz, estado.checkpoint.get("modo", ""), cobertos)
    return achadas


def _tirar_o_que_nao_da(
    estado: _Estado,
    portas: PortasDaCacada,
    guarda: GuardaDoPlano,
    oportunidades: Sequence[Oportunidade],
    sessao: Any | None = None,
) -> list[Oportunidade]:
    """Tira o que ele não consegue propor (travado na conta grátis ou já fechado) e soma no checkpoint.

    Com o Chrome da caçada (`portas.navegador_logado`), a conferência nos sites em
    que ele está logado é pela página logada; o Chrome só liga se alguma página
    for pedida, e fecha no fim.
    """
    from .fontes_da_cacada import filtrar_o_que_da

    if sessao is None and portas.navegador_logado is not None:
        with portas.navegador_logado.abrir() as aberta:
            return _tirar_o_que_nao_da(estado, portas, guarda, oportunidades, aberta)
    filtrado = filtrar_o_que_da(
        oportunidades,
        estado.configuracao.cacada_usuario_freelancer,
        plataformas_sem_conta=estado.configuracao.cacada_plataformas_sem_conta,
        plataformas_pagas=estado.configuracao.cacada_plataformas_pagas,
        rede=portas.rede,
        dormir=guarda._dormir,
        agora=guarda._relogio(),
        sessao=sessao,
        max_propostas_freelancer=estado.configuracao.cacada_max_propostas_freelancer,
    )
    checkpoint = estado.checkpoint
    if filtrado.lances is not None:
        checkpoint.guardar(lances_freelancer=list(filtrado.lances))
    if filtrado.conexoes is not None:
        checkpoint.guardar(conexoes_99freelas=filtrado.conexoes)
    checkpoint.guardar(
        freelancer_sem_skill=int(checkpoint.get("freelancer_sem_skill") or 0) + filtrado.sem_skill,
        freelancer_so_para_quem_paga=int(checkpoint.get("freelancer_so_para_quem_paga") or 0) + filtrado.so_para_quem_paga,
        fechadas=int(checkpoint.get("fechadas") or 0) + filtrado.fechadas,
        sem_conta=int(checkpoint.get("sem_conta") or 0) + filtrado.sem_conta,
        com_malware=int(checkpoint.get("com_malware") or 0) + filtrado.com_malware,
        conta_recente=int(checkpoint.get("conta_recente") or 0) + filtrado.conta_recente,
        freelancer_so_para_selecionados=int(checkpoint.get("freelancer_so_para_selecionados") or 0)
        + filtrado.so_para_selecionados,
        freelancer_repetidos=int(checkpoint.get("freelancer_repetidos") or 0) + filtrado.repetidos,
        freelancer_lotados=int(checkpoint.get("freelancer_lotados") or 0) + filtrado.lotados,
        ja_propostas=int(checkpoint.get("ja_propostas") or 0) + filtrado.ja_propostas,
        pagas=int(checkpoint.get("pagas") or 0) + filtrado.pagas,
    )
    return list(filtrado.ficam)


def _etapa_da_triagem(estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano) -> None:
    oportunidades = estado.oportunidades()
    perfil = estado.perfil() or Perfil(resumo="")
    criterios = estado.criterios
    tentativas: dict[str, int] = dict(estado.checkpoint.get("tentativas_da_triagem") or {})
    while True:
        vereditos = estado.vereditos()
        pendentes = [o for i, o in oportunidades.items() if i not in vereditos]
        if not pendentes:
            return
        lote = pendentes[:LOTE_DA_TRIAGEM]
        estado.andamento("triagem", f"{len(vereditos)} de {len(oportunidades)} triadas")
        try:
            novos = guarda.chamar(portas.triagem, lambda: portas.triagem.triar(lote, perfil, criterios.procuro, criterios.sobre_mim))
        except ErroDaCacada as erro:
            _log.warning("Um lote da triagem falhou: %s", erro)
            novos = ()
        if estado.checkpoint.get("modo") == MODO_FREELAS:
            novos = rebaixar_fora_do_nicho(novos, oportunidades, estado.configuracao.cacada_nicho_freela)
        recebidos = {v.id for v in novos}
        sem_resposta = []
        for oportunidade in lote:
            if oportunidade.id in recebidos:
                continue
            tentativas[oportunidade.id] = tentativas.get(oportunidade.id, 0) + 1
            if tentativas[oportunidade.id] >= 2:
                sem_resposta.append(
                    Veredito(oportunidade.id, DESCARTADA, "a triagem não respondeu sobre ela (abra o link se quiser ver)")
                )
        lista = [v.para_json() for v in (*estado.vereditos().values(), *novos, *sem_resposta)]
        estado.checkpoint.guardar(vereditos=lista, tentativas_da_triagem=tentativas)


def _etapa_do_preparo(estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano) -> None:
    oportunidades = estado.oportunidades()
    vereditos = estado.vereditos()
    perfil = estado.perfil() or Perfil(resumo="")
    criterios = estado.criterios
    completo = estado.configuracao.cacada_pacote_completo
    from .fontes_da_cacada import ler_detalhe

    pacotes = estado.pacotes()
    sem_pacote = [o for o, _ in _ordenadas(oportunidades, vereditos, CHANCE_BOA) if o.id not in pacotes]
    if sem_pacote:
        estado.andamento("preparo", "conferindo se as chances boas ainda estão abertas")
        ficam = {o.id: o for o in _tirar_o_que_nao_da(estado, portas, guarda, sem_pacote)}
        sairam = {o.id for o in sem_pacote} - ficam.keys()
        oportunidades = {i: ficam.get(i, o) for i, o in oportunidades.items() if i not in sairam}
        estado.checkpoint.guardar(oportunidades=[o.para_json() for o in oportunidades.values()])
    chances = _ordenadas(oportunidades, vereditos, CHANCE_BOA)[: estado.configuracao.cacada_maximo_de_pacotes]

    for numero, (oportunidade, veredito) in enumerate(chances, 1):
        pacotes = estado.pacotes()
        if oportunidade.id in pacotes:
            continue
        estado.andamento("preparo", f"{numero} de {len(chances)}: {oportunidade.titulo[:60]}")
        pagina = (portas.ler_detalhe or (lambda o: ler_detalhe(o, rede=portas.rede)))(oportunidade)
        fazer = portas.preparo.preparar if completo else portas.preparo.resumir
        try:
            pacote = guarda.chamar(
                portas.preparo,
                lambda: fazer(oportunidade, veredito, pagina, perfil, criterios.procuro, criterios.sobre_mim),
            )
        except ErroDaCacada as erro:
            _log.warning("O pacote de %s não saiu: %s", oportunidade.link, erro)
            pacotes[oportunidade.id] = {"falha": str(erro)[:300]}
            estado.checkpoint.guardar(pacotes=pacotes)
            continue
        pacote = _com_piso(estado, oportunidade, pacote)
        caminho = caminho_do_pacote(estado.corrida, oportunidade)
        amostra = _amostra(estado, portas, oportunidade, pacote, caminho) if pacote.amostra_html else {}
        estado.escrever(caminho, montar_pacote(oportunidade, veredito, pacote, amostra, completo=completo))
        pacotes[oportunidade.id] = {"caminho": caminho, **amostra, "pacote": {**pacote.para_json(), "amostra_html": ""}}
        estado.checkpoint.guardar(pacotes=pacotes)


def _etapa_extra(estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano) -> None:
    """Modo freelas: com o principal pronto antes da virada da semana, gasta o que sobra até ela.

    Rodadas na web enquanto cada uma trouxer freela novo (até
    `RODADAS_EXTRAS_DE_BUSCA`, e no mínimo `RODADAS_EXTRAS_MINIMAS`), com triagem e resumo do que chegou, e o pacote
    completo (proposta e, no freela web, a amostra) de cada chance boa nova;
    quando a busca seca, o pacote completo das de tentar a sorte, da melhor para
    a pior. Só para quando o plano acaba ou a virada chega; se o trabalho acabar
    antes, marca `sobrou_plano` e quem chamou pode emendar uma caçada de vagas. A janela de 5 h pode pausar se reabrir antes da virada; o plano
    acabando ou a virada chegando encerram o extra, e a corrida termina inteira.
    Se o principal só acabou depois da virada, não há extra.
    """
    if guarda.virada_atravessada:
        return
    plano = guarda.plano
    if plano is not None and plano.vira_sete_dias is not None:
        fim = plano.vira_sete_dias - MARGEM_DA_VIRADA
    elif plano is not None and plano.vira_cinco_horas is not None:
        fim = plano.vira_cinco_horas
    else:
        fim = guarda.agora() + timedelta(hours=5)
    if (pelo_limite := guarda.fim_pelo_limite()) is not None:
        fim = min(fim, pelo_limite)

    def sobra() -> bool:
        return guarda.agora() < fim and not guarda.limite_estourado()

    guarda.fim_do_extra = fim
    try:
        while sobra() and int(estado.checkpoint.get("rodadas_extras") or 0) < RODADAS_EXTRAS_DE_BUSCA:
            novas = _rodada_extra_de_busca(estado, portas, guarda, sobra)
            feitas = int(estado.checkpoint.get("rodadas_extras") or 0) + 1
            if not novas and feitas >= RODADAS_EXTRAS_MINIMAS:
                feitas = RODADAS_EXTRAS_DE_BUSCA
            estado.checkpoint.guardar(rodadas_extras=feitas)
            if novas:
                _etapa_da_triagem(estado, portas, guarda)
                _etapa_do_preparo(estado, portas, guarda)
            _caprichar(estado, portas, guarda, sobra, CHANCE_BOA)
        _caprichar(estado, portas, guarda, sobra, CHANCE_BOA)
        _caprichar(estado, portas, guarda, sobra, TENTAR_A_SORTE, pares=tentar_a_sorte(estado).ficam)
        estado.checkpoint.guardar(sobrou_plano=sobra())
    except JanelaEsgotada as motivo:
        _log.info("O extra da caçada de freelas encerrou: %s.", motivo)
    finally:
        guarda.fim_do_extra = None
        guarda.limite_batido = guarda.limite_batido or guarda.limite_estourado()


def _rodada_extra_de_busca(
    estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano, sobra: Callable[[], bool]
) -> int:
    criterios = estado.criterios
    perfil = estado.perfil() or Perfil(resumo="")
    ja_vistas = links_ja_vistos(estado.raiz, excluir=estado.corrida)
    novas = 0
    for fonte in (f for f in criterios.fontes if f.tipo == "web"):
        if not sobra():
            break
        oportunidades = estado.oportunidades()
        vistas = ja_vistas | {o.link for o in oportunidades.values()}
        estado.andamento("extra", f"buscando mais: {fonte.nome}")
        try:
            achadas = guarda.chamar(
                portas.explorador,
                lambda fonte=fonte, vistas=sorted(vistas): portas.explorador.explorar(
                    f"{fonte.argumento} {PEDIDO_DA_RODADA_EXTRA}", perfil, criterios.procuro, criterios.sobre_mim, vistas, fonte.nome
                ),
            )
        except ErroDaCacada as erro:
            _log.warning("A rodada extra em %s falhou: %s", fonte.nome, erro)
            continue
        chegaram = {o.id: o for o in achadas if o.link not in vistas and o.id not in oportunidades}
        if chegaram:
            chegaram = {o.id: o for o in _tirar_o_que_nao_da(estado, portas, guarda, list(chegaram.values()))}
        if chegaram:
            novas += len(chegaram)
            estado.checkpoint.guardar(oportunidades=[o.para_json() for o in (*oportunidades.values(), *chegaram.values())])
    return novas


def _caprichar(
    estado: _Estado,
    portas: PortasDaCacada,
    guarda: GuardaDoPlano,
    sobra: Callable[[], bool],
    classe: str,
    *,
    pares: Sequence[tuple[Oportunidade, Veredito]] | None = None,
) -> None:
    from .fontes_da_cacada import ler_detalhe

    perfil = estado.perfil() or Perfil(resumo="")
    criterios = estado.criterios
    if pares is None:
        pares = _ordenadas(estado.oportunidades(), estado.vereditos(), classe)
    for oportunidade, veredito in pares:
        if not sobra():
            return
        pacotes = estado.pacotes()
        atual = pacotes.get(oportunidade.id) or {}
        if atual.get("completo"):
            continue
        estado.andamento("extra", f"caprichando: {oportunidade.titulo[:60]}")
        pagina = (portas.ler_detalhe or (lambda o: ler_detalhe(o, rede=portas.rede)))(oportunidade)
        try:
            pacote = guarda.chamar(
                portas.preparo,
                lambda: portas.preparo.preparar(oportunidade, veredito, pagina, perfil, criterios.procuro, criterios.sobre_mim),
            )
        except ErroDaCacada as erro:
            _log.warning("O pacote completo de %s não saiu: %s", oportunidade.link, erro)
            continue
        pacote = _com_piso(estado, oportunidade, pacote)
        caminho = atual.get("caminho") or caminho_do_pacote(estado.corrida, oportunidade)
        amostra = _amostra(estado, portas, oportunidade, pacote, caminho) if pacote.amostra_html else {}
        estado.escrever(caminho, montar_pacote(oportunidade, veredito, pacote, amostra))
        pacotes[oportunidade.id] = {
            "caminho": caminho,
            **amostra,
            "pacote": {**pacote.para_json(), "amostra_html": ""},
            "completo": True,
        }
        estado.checkpoint.guardar(pacotes=pacotes)


def _com_piso(estado: _Estado, oportunidade: Oportunidade, pacote: Pacote) -> Pacote:
    """O piso de preço e a peça do portfólio conferidos em Python: só no freela."""
    if oportunidade.tipo != FREELA:
        return pacote
    return replace(
        pacote,
        preco=conferir_piso(pacote.preco, oportunidade.faixa, estado.configuracao.cacada_piso_freela),
        prova=conferir_prova(pacote.prova, oportunidade, estado.criterios.portfolio),
    )


def _amostra(
    estado: _Estado, portas: PortasDaCacada, oportunidade: Oportunidade, pacote: Pacote, caminho: str
) -> dict[str, str]:
    from . import amostras

    pasta = f"{caminho.removesuffix('.md')}-amostra"
    arquivo = amostras.gravar(estado.raiz, pasta, pacote.amostra_html)
    info = {"amostra": f"{pasta}/index.html"}
    if not portas.amostras:
        return info
    if amostras.tirar_print(arquivo) is not None:
        info["print"] = f"{pasta}/amostra.png"
    publicada = amostras.publicar(estado.configuracao, arquivo, Path(pasta).name)
    if isinstance(publicada, str):
        info["sem_link"] = publicada
    elif publicada.url:
        info["url"] = publicada.url
    return info


def _etapa_das_apostas(estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano) -> None:
    if not (estado.configuracao.cacada_pacote_completo or estado.configuracao.cacada_enviar_sozinho):
        return
    perfil = estado.perfil() or Perfil(resumo="")
    criterios = estado.criterios
    alvo = tentar_a_sorte(estado).ficam[:MAXIMO_DE_APOSTAS]
    tentadas: set[str] = set(estado.checkpoint.get("apostas_tentadas") or [])
    while True:
        feitas = estado.apostas()
        pendentes = [(o, v) for o, v in alvo if o.id not in feitas and o.id not in tentadas]
        if not pendentes:
            return
        lote = pendentes[:LOTE_DAS_APOSTAS]
        estado.andamento("apostas", f"{len(feitas)} de {len(alvo)} mensagens prontas")
        try:
            novas = guarda.chamar(portas.apostas, lambda: portas.apostas.escrever(lote, perfil, criterios.procuro, criterios.sobre_mim))
        except ErroDaCacada as erro:
            _log.warning("Um lote de mensagens de tentar a sorte falhou: %s", erro)
            novas = ()
        tentadas |= {o.id for o, _ in lote}
        todas = {**{k: v.para_json() for k, v in feitas.items()}, **{a.id: a.para_json() for a in novas}}
        estado.checkpoint.guardar(apostas=todas, apostas_tentadas=sorted(tentadas))


def _etapa_do_envio(estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano) -> None:
    """Entrega 2: as de tentar a sorte que a política deixa, uma por uma, com o registro antes e depois.

    Quem decide se pode é `politica.pode_enviar_sozinho`, em código. Desligado
    (`[cacada].enviar_sozinho = false`, o padrão), esta etapa não faz nada.
    """
    configuracao = estado.configuracao
    if not configuracao.cacada_enviar_sozinho or portas.navegador is None or portas.registro is None:
        return
    from .acoes import CANDIDATURA, FALHOU
    from .contas import dados_dele
    from .politica import pode_enviar_sozinho

    dados = dados_dele(configuracao, estado.criterios)
    apostas = estado.apostas()
    envios: dict[str, dict[str, str]] = dict(estado.checkpoint.get("envios") or {})
    feitos = sum(1 for e in envios.values() if e.get("status") == "feito")
    for oportunidade, veredito in tentar_a_sorte(estado).ficam:
        if feitos >= configuracao.cacada_maximo_de_envios:
            break
        if oportunidade.id in envios or oportunidade.id not in apostas:
            continue
        pode, motivo = pode_enviar_sozinho(oportunidade, veredito, portas.registro, ligado=True)
        if not pode:
            continue
        estado.andamento("envio", f"candidatando: {oportunidade.titulo[:60]}")
        mensagem = apostas[oportunidade.id].mensagem
        id_ = portas.registro.intencao(CANDIDATURA, oportunidade.link, texto=mensagem, detalhe=oportunidade.titulo)
        try:
            desfecho = guarda.chamar(
                portas.navegador, lambda: portas.navegador.candidatar(oportunidade.link, oportunidade.titulo, mensagem, dados)
            )
        except ErroDaCacada as erro:
            portas.registro.concluir(id_, FALHOU, str(erro)[:200])
            envios[oportunidade.id] = {"status": "falhou", "motivo": str(erro)[:200]}
            estado.checkpoint.guardar(envios=envios)
            continue
        except Parada:
            portas.registro.concluir(id_, FALHOU, "a caçada parou no meio da candidatura: confira no site")
            raise
        status = desfecho.status if desfecho.status in ("feito", "precisa_de_voce") else "falhou"
        portas.registro.concluir(id_, status, desfecho.motivo)
        envios[oportunidade.id] = {"status": status, "motivo": desfecho.motivo, "falta": desfecho.o_que_falta}
        feitos += status == "feito"
        estado.checkpoint.guardar(envios=envios)


def _etapa_da_caixa(estado: _Estado, portas: PortasDaCacada) -> None:
    """O que chegou no Gmail dedicado: só a lista, para ele ler. Nada é respondido."""
    if portas.ler_caixa is None or estado.checkpoint.get("caixa") is not None:
        return
    estado.andamento("caixa", "lendo o Gmail dedicado")
    try:
        mensagens = portas.ler_caixa()
    except Exception as erro:  # noqa: BLE001 — a caixa não derruba a caçada
        estado.checkpoint.guardar(caixa={"falha": str(erro)[:200]})
        return
    lista = [
        {"remetente": m.remetente, "assunto": m.assunto, "quando": m.quando}
        for m in sorted(mensagens, key=lambda m: m.quando, reverse=True)[:40]
    ]
    estado.checkpoint.guardar(caixa={"mensagens": lista})


def _etapa_do_funil(estado: _Estado, portas: PortasDaCacada) -> None:
    """O que os sites mostram das propostas de freela que esperam resposta; só com a corrida já sem Claude."""
    from .funil import ler_mandadas, situacao_nos_sites

    esperando = [m for m in ler_mandadas(estado.raiz) if m.esperando]
    if not esperando:
        return
    if portas.navegador_logado is None:
        estado.checkpoint.guardar(funil={"motivo": "sem o Chrome da caçada nesta corrida"})
        return
    estado.andamento("funil", "lendo nos sites as propostas que esperam resposta")
    try:
        with portas.navegador_logado.abrir() as sessao:
            situacoes = situacao_nos_sites(esperando, sessao)
            motivo = sessao.motivo
    except Exception as erro:  # noqa: BLE001 — o funil não derruba a caçada
        _log.warning("O funil não leu os sites: %s", erro)
        situacoes, motivo = {}, f"o Chrome da caçada falhou ({erro})"
    estado.checkpoint.guardar(funil={"situacoes": situacoes, "motivo": f"sem o Chrome da caçada: {motivo}" if motivo else ""})


def _depois_da_corrida(estado: _Estado, portas: PortasDaCacada, guarda: GuardaDoPlano) -> None:
    """O acompanhamento das candidaturas e a hora da virada semanal, para o lembrete da véspera."""
    from . import acompanhamento
    from .corrida import guardar_virada

    caixa = estado.checkpoint.get("caixa")
    mensagens = caixa.get("mensagens") or [] if isinstance(caixa, dict) else []
    try:
        acompanhamento.atualizar(estado.raiz, registro=portas.registro, mensagens=mensagens)
    except Exception as erro:  # noqa: BLE001 — o acompanhamento não derruba a caçada
        _log.warning("O acompanhamento das candidaturas não saiu: %s", erro)
    if guarda.plano is not None and guarda.plano.vira_sete_dias is not None:
        guardar_virada(estado.pasta, guarda.plano.vira_sete_dias)


@dataclass
class TentarASorte:
    """As de tentar a sorte que ele consegue mandar: acima do encaixe mínimo e dentro do orçamento de cada site."""

    ficam: list[tuple[Oportunidade, Veredito]] = field(default_factory=list)
    abaixo: list[tuple[Oportunidade, Veredito]] = field(default_factory=list)
    sem_orcamento: dict[str, list[tuple[Oportunidade, Veredito]]] = field(default_factory=dict)
    sem_lances: bool = False


def tentar_a_sorte(estado: _Estado) -> TentarASorte:
    """Da melhor para a pior, cada site fica nas que dá para pagar.

    Freelancer.com: uma por lance restante (lido logado); 99Freelas: até as
    conexões do estoque, pelo custo de cada proposta (1 quando não se sabe).
    Sem a leitura logada do site, não há corte.
    """
    from .fontes_da_cacada import custo_em_conexoes, e_da_99freelas, e_do_freelancer

    minimo = estado.configuracao.cacada_encaixe_minimo_tentar
    lances = estado.checkpoint.get("lances_freelancer")
    conexoes = estado.checkpoint.get("conexoes_99freelas")
    saida = TentarASorte(sem_lances=bool(lances) and int(lances[0]) == 0)
    usados = gastas = 0
    for par in _ordenadas(estado.oportunidades(), estado.vereditos(), TENTAR_A_SORTE):
        oportunidade, veredito = par
        if veredito.encaixe < minimo:
            saida.abaixo.append(par)
        elif lances and e_do_freelancer(oportunidade.link):
            if usados < int(lances[0]):
                usados += 1
                saida.ficam.append(par)
            else:
                saida.sem_orcamento.setdefault("Freelancer.com", []).append(par)
        elif conexoes is not None and e_da_99freelas(oportunidade.link):
            custo = custo_em_conexoes(oportunidade) or 1
            if gastas + custo <= int(conexoes):
                gastas += custo
                saida.ficam.append(par)
            else:
                saida.sem_orcamento.setdefault("99Freelas", []).append(par)
        else:
            saida.ficam.append(par)
    return saida


def _ordenadas(
    oportunidades: dict[str, Oportunidade], vereditos: dict[str, Veredito], classe: str
) -> list[tuple[Oportunidade, Veredito]]:
    pares = [(oportunidades[i], v) for i, v in vereditos.items() if v.classe == classe and i in oportunidades]
    return sorted(pares, key=lambda par: par[1].encaixe, reverse=True)


# -- o que já foi visto ---------------------------------------------------------------------------

_LINK_MD = re.compile(r"\]\((https?://[^)\s]+)\)")
#: Oportunidade de corrida abandonada que nem foi triada não conta como vista.
MARCA_SEM_TRIAGEM = "ainda sem triagem"


def links_ja_vistos(vault: Path, *, excluir: str = "") -> set[str]:
    """Os links que já apareceram em caçadas anteriores — relatórios e pacotes —, canônicos."""
    pasta = Path(vault) / PASTA_CACADA
    vistos: set[str] = set()
    if not pasta.is_dir():
        return vistos
    for arquivo in pasta.rglob("*.md"):
        relativo = arquivo.relative_to(vault).as_posix()
        if relativo in (CAMINHO_DOS_CRITERIOS, CAMINHO_DO_PERFIL, CAMINHO_DO_PERFIL_DE_FREELAS) or (excluir and excluir in relativo):
            continue
        try:
            texto = arquivo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for linha in texto.splitlines():
            if MARCA_SEM_TRIAGEM in linha:
                continue
            vistos.update(link_canonico(link) for link in _LINK_MD.findall(linha))
    return vistos


# -- as notas -----------------------------------------------------------------------------------------


def caminho_do_relatorio(corrida: str) -> str:
    return f"{PASTA_CACADA}/{corrida}.md"


def caminho_do_pacote(corrida: str, oportunidade: Oportunidade) -> str:
    return f"{PASTA_CACADA}/{corrida}/{_vault._slug(oportunidade.titulo, 'oportunidade')[:50]}-{oportunidade.id[:6]}.md"


def montar_nota_do_perfil(perfil: Perfil, corrida: str, *, freelas: bool = False, nicho: str = "") -> str:
    if freelas:
        titulo = f"# O perfil da caçada de freela de {nicho.strip().title()}" if nicho.strip() else "# O perfil da caçada de freelas"
    else:
        titulo = "# O perfil que a caçada usa"
    linhas = [
        "---",
        "tipo: perfil-da-cacada",
        f'corrida: "{corrida}"',
        "---",
        "",
        titulo,
        "",
        "Montado pelo Whis a cada caçada, a partir do git, do vault e de *Sobre mim* em [[cacada/criterios]]. "
        "Para corrigir, escreva em *Sobre mim*: esta nota é regenerada.",
        "",
        perfil.resumo.strip() or "_(vazio)_",
        "",
        f"**Stack:** {', '.join(perfil.stack) or '—'}",
        "",
        f"**Senioridade estimada:** {perfil.senioridade or '—'}",
        "",
    ]
    if perfil.perguntas:
        linhas += ["## O que falta você dizer", "", *(f"- {p}" for p in perfil.perguntas), ""]
    return "\n".join(linhas)


def montar_pacote(
    oportunidade: Oportunidade, veredito: Veredito, pacote: Pacote, amostra: dict[str, str], *, completo: bool = True
) -> str:
    def lista(itens: Sequence[str], vazio: str = "_(nenhum)_") -> list[str]:
        return [f"- {item}" for item in itens] if itens else [vazio]

    cabecalho = " · ".join(
        parte
        for parte in (
            "Freela" if oportunidade.tipo == FREELA else "Vaga",
            oportunidade.fonte,
            oportunidade.local,
            oportunidade.regime,
            oportunidade.faixa,
            f"publicada {oportunidade.publicada[:10]}" if oportunidade.publicada else "",
            f"encaixe {veredito.encaixe}",
        )
        if parte
    )
    empresa = f" — {oportunidade.empresa}" if oportunidade.empresa else ""
    linhas = [
        "---",
        "tipo: pacote",
        "status: preparada",
        f"titulo: {_vault._texto_yaml(oportunidade.titulo)}",
        f"empresa: {_vault._texto_yaml(oportunidade.empresa)}",
        f'link: "{oportunidade.link}"',
        f"encaixe: {veredito.encaixe}",
        "---",
        "",
        "> Quando enviar, troque `status: preparada` acima por `enviada` (e depois `entrevista`, `proposta`, "
        "`recusada` ou `fechado`): é o que [[cacada/candidaturas]] acompanha.",
        "",
        f"# {oportunidade.titulo}{empresa}",
        "",
        f"[Abrir a oportunidade]({oportunidade.link}) · {cabecalho}",
        "",
        f"> {veredito.motivo}" if veredito.motivo else "",
        "",
        "## O que pedem",
        "",
        pacote.resumo or "_(sem resumo)_",
        "",
        "## Por que casa",
        "",
        pacote.por_que_casa or "—",
        "",
    ]
    if pacote.prova:
        linhas += ["## Prova para citar", "", pacote.prova, ""]
    if pacote.o_que_estica:
        linhas += ["## Ponto fraco", "", pacote.o_que_estica, ""]
    if not completo:
        linhas += ["## Salário", "", pacote.preco or "—", ""]
        return "\n".join(linhas)
    linhas += ["## Alertas", "", *lista(pacote.alertas), ""]
    linhas += ["## Preço e prazo", "", f"- **Preço / pretensão:** {pacote.preco or '—'}", f"- **Prazo:** {pacote.prazo or '—'}", ""]
    linhas += ["## Proposta pronta", "", "```text", pacote.proposta.strip() or "(vazia)", "```", ""]
    if pacote.formulario:
        linhas += ["## Respostas de formulário", "", *lista(pacote.formulario), ""]
    linhas += ["## Como enviar", "", pacote.como_enviar or "—", ""]
    linhas += ["## Plano de execução", "", *lista(pacote.plano), ""]
    if pacote.perguntas:
        linhas += ["## Perguntas para o cliente", "", *lista(pacote.perguntas), ""]
    if amostra:
        linhas += ["## Amostra", ""]
        if amostra.get("url"):
            linhas.append(f"- Link público (sem seu nome; sai do ar em 30 dias): {amostra['url']}")
        elif amostra.get("sem_link"):
            linhas.append(f"- Sem link público: {amostra['sem_link']}.")
        linhas.append(f"- Arquivo: `{amostra['amostra']}` (abra no navegador)")
        if amostra.get("print"):
            linhas += ["- Print para anexar (é o que vai na Workana, que não aceita link):", "", f"![[{amostra['print']}]]"]
        linhas.append("")
    return "\n".join(linhas)


def _escrever_relatorio(estado: _Estado, guarda: GuardaDoPlano, *, parada: str, em_andamento: str = "") -> str:
    caminho = caminho_do_relatorio(estado.corrida)
    estado.escrever(caminho, montar_relatorio(estado, guarda, parada=parada, em_andamento=em_andamento))
    return caminho


def montar_relatorio(estado: _Estado, guarda: GuardaDoPlano, *, parada: str, em_andamento: str = "") -> str:
    from .fontes_da_cacada import ORGANIZACOES_BLOQUEADAS, fonte_paga, fonte_sem_conta, tipos_pagos
    from .navegador_logado import NOMES, frase_do_estado

    oportunidades = estado.oportunidades()
    vereditos = estado.vereditos()
    pacotes = estado.pacotes()
    apostas = estado.apostas()
    chances = _ordenadas(oportunidades, vereditos, CHANCE_BOA)
    orcamento = tentar_a_sorte(estado)
    sorte = orcamento.ficam
    descartadas = _ordenadas(oportunidades, vereditos, DESCARTADA)
    sem_veredito = [o for i, o in oportunidades.items() if i not in vereditos]
    inicio = _data(estado.checkpoint.get("inicio"))

    nome = "Caçada de freelas rápidos" if estado.checkpoint.get("modo") == MODO_FREELAS else "Caçada"
    titulo = f"# {nome} de {_vault._data_por_extenso(inicio.date())}, {inicio:%H:%M}" if inicio else f"# {nome}"
    plano = ""
    if guarda.primeiro and guarda.plano and guarda.primeiro.sete_dias is not None:
        plano = f" · plano semanal {guarda.primeiro.sete_dias}% → {guarda.plano.sete_dias}%"
    linhas = [
        "---",
        "tipo: cacada",
        f"chances_boas: {len(chances)}",
        f"tentar_a_sorte: {len(sorte)}",
        f"descartadas: {len(descartadas)}",
        f"completa: {'false' if parada or em_andamento else 'true'}",
        "---",
        "",
        titulo,
        "",
        f"**{len(chances)} chances boas · {len(sorte)} para tentar a sorte · {len(descartadas)} descartadas**{plano}",
        "",
        _frase_do_envio(estado),
        "",
    ]
    if parada and parada == guarda.limite_batido:
        linhas += [
            "> [!warning] A caçada parou no limite desta corrida",
            f"> {parada}. O que está aqui vale; rode `hunt` de novo para continuar de onde parou "
            "(com `--sem-limite`, ela vai até o plano acabar).",
            "",
        ]
    elif parada:
        linhas += [
            "> [!warning] A caçada parou antes do fim",
            f"> {parada}. O que está aqui vale; rode `hunt` de novo (na véspera da virada da semana) para continuar de onde parou.",
            "",
        ]
    elif em_andamento:
        linhas += [f"> [!info] A caçada ainda está correndo: {em_andamento}. Esta nota é reescrita ao fim de cada etapa.", ""]
    elif guarda.limite_batido:
        linhas += [
            "> [!info] O extra parou no limite desta corrida",
            f"> {guarda.limite_batido}. A caçada principal terminou inteira.",
            "",
        ]
    if guarda.limitada():
        porque = "a semana do plano não vira nas próximas 24 h" if guarda.limite.automatico else "pedido na linha de comando"
        linhas += [
            f"- Limite desta corrida: {guarda.limite.descrever()} ({porque}; `hunt --sem-limite` vai até o plano acabar).",
            "",
        ]
    for pausa in guarda.pausas:
        linhas.append(f"- Ficou {pausa}.")
    if guarda.pausas:
        linhas.append("")

    perguntas = estado.perguntas()
    em_branco = estado.criterios.respostas_em_branco()
    if perguntas or em_branco:
        linhas += ["## Antes de tudo: responda em [[cacada/criterios]]", "", "Escreva em *Sobre mim*; a próxima caçada usa.", ""]
        linhas += [f"- {p}" for p in perguntas]
        if em_branco:
            linhas.append(f"- Ainda em branco em *Sobre mim*: {', '.join(em_branco)}.")
        linhas.append("")

    linhas += ["## Chances boas", ""]
    if not chances:
        linhas += ["_Nenhuma nesta caçada._", ""]
    for numero, (oportunidade, veredito) in enumerate(chances, 1):
        empresa = f" — {oportunidade.empresa}" if oportunidade.empresa else ""
        linhas += [f"### {numero}. {oportunidade.titulo}{empresa}", "", _linha_da_oportunidade(oportunidade, veredito), ""]
        if veredito.motivo:
            linhas += [veredito.motivo, ""]
        pacote = pacotes.get(oportunidade.id) or {}
        if pacote.get("caminho"):
            if pacote.get("pacote", {}).get("proposta"):
                linhas.append(f"→ **[[{pacote['caminho'].removesuffix('.md')}|Abrir o pacote]]** (proposta pronta, preço, como enviar)")
            else:
                linhas.append(f"→ **[[{pacote['caminho'].removesuffix('.md')}|Abrir o resumo]]** (o que pedem, ponto fraco, salário)")
            if pacote.get("url"):
                linhas.append(f"→ Amostra no ar: {pacote['url']}")
            elif pacote.get("amostra"):
                linhas.append(f"→ Amostra: `{pacote['amostra']}`")
        elif pacote.get("falha"):
            linhas.append(f"→ O pacote não saiu ({pacote['falha']}). Abra a oportunidade e decida.")
        else:
            linhas.append("→ Sem pacote nesta corrida (teto de pacotes ou a corrida parou antes).")
        linhas.append("")

    linhas += ["## Tentar a sorte", ""]
    fora_do_freelancer = orcamento.sem_orcamento.get("Freelancer.com", [])
    if orcamento.sem_lances and fora_do_freelancer:
        linhas += [
            f"- **Freelancer.com: sem lances este mês; {len(fora_do_freelancer)} projetos ficaram de fora até a renovação.**",
            "",
        ]
    for site, fora in orcamento.sem_orcamento.items():
        if site == "Freelancer.com" and orcamento.sem_lances:
            continue
        falta = "lances" if site == "Freelancer.com" else "conexões"
        linhas.append(f"> [!note]- {site}: {len(fora)} ficaram de fora, sem {falta} para todas (clique para ver)")
        linhas += [f"> - [{_curto(o.titulo)}]({o.link}) — encaixe {v.encaixe}" for o, v in fora]
        linhas.append("")
    if not sorte:
        linhas += ["_Nenhuma nesta caçada._", ""]
    for oportunidade, veredito in sorte:
        empresa = f" — {oportunidade.empresa}" if oportunidade.empresa else ""
        linhas += [f"### {oportunidade.titulo}{empresa}", "", _linha_da_oportunidade(oportunidade, veredito)]
        if veredito.motivo:
            linhas += ["", veredito.motivo]
        envio = (estado.checkpoint.get("envios") or {}).get(oportunidade.id)
        if envio:
            rotulo = {"feito": "**Enviada pelo Whis.**", "precisa_de_voce": "**O Whis começou e precisa de você:**"}.get(
                envio.get("status", ""), "**O Whis tentou enviar e não conseguiu:**"
            )
            linhas += ["", f"{rotulo} {envio.get('falta') or envio.get('motivo', '')}".rstrip()]
        pacote = pacotes.get(oportunidade.id) or {}
        if pacote.get("caminho"):
            linhas += ["", f"→ **[[{pacote['caminho'].removesuffix('.md')}|Abrir o pacote]]** (proposta pronta, preço, como enviar)"]
        aposta = apostas.get(oportunidade.id)
        if aposta is not None:
            linhas += ["", "> [!quote]- Mensagem pronta (clique para abrir)"]
            linhas += [f"> {linha}" if linha else ">" for linha in aposta.mensagem.strip().splitlines()]
            if aposta.como_enviar:
                linhas += [">", f"> **Como enviar:** {aposta.como_enviar}"]
        linhas.append("")

    linhas += ["## Descartadas", ""]
    if descartadas or sem_veredito or orcamento.abaixo:
        quantas = len(descartadas) + len(sem_veredito) + len(orcamento.abaixo)
        linhas.append(f"> [!note]- {quantas} descartadas ou sem triagem (clique para ver)")
        for oportunidade, veredito in descartadas:
            linhas.append(f"> - [{_curto(oportunidade.titulo)}]({oportunidade.link}) — {oportunidade.fonte} — {veredito.motivo}")
        for oportunidade, veredito in orcamento.abaixo:
            linhas.append(
                f"> - [{_curto(oportunidade.titulo)}]({oportunidade.link}) — {oportunidade.fonte} — "
                f"tentar a sorte com encaixe {veredito.encaixe}, abaixo do mínimo"
            )
        for oportunidade in sem_veredito:
            linhas.append(f"> - [{_curto(oportunidade.titulo)}]({oportunidade.link}) — {oportunidade.fonte} — {MARCA_SEM_TRIAGEM}")
    else:
        linhas.append("_Nenhuma._")
    linhas.append("")

    caixa = estado.checkpoint.get("caixa")
    if isinstance(caixa, dict):
        linhas += ["## Caixa do Gmail dedicado", ""]
        if caixa.get("falha"):
            linhas.append(f"_Não deu para ler: {caixa['falha']}_")
        elif caixa.get("mensagens"):
            linhas.append("Só a lista, para você abrir e responder; o Whis não responde ninguém.")
            linhas.append("")
            linhas += [f"- {m['quando'][:16].replace('T', ' ')} — {m['remetente']} — {m['assunto']}" for m in caixa["mensagens"]]
        else:
            linhas.append("_Nada nos últimos 14 dias._")
        linhas.append("")

    from .funil import ler_mandadas, montar_funil

    mandadas = ler_mandadas(estado.raiz)
    if mandadas:
        funil = estado.checkpoint.get("funil") or {}
        linhas += montar_funil(mandadas, funil.get("situacoes") or {}, motivo=funil.get("motivo", ""))

    linhas += ["## Fontes", ""]
    lidas: dict[str, int] = estado.checkpoint.get("lidas") or {}
    falhas: dict[str, str] = estado.checkpoint.get("falhas") or {}
    plataformas_sem_conta = estado.configuracao.cacada_plataformas_sem_conta
    for fonte in estado.criterios.fontes:
        if fonte_sem_conta(fonte, plataformas_sem_conta):
            linhas.append(f"- {fonte.nome}: pulada, sua conta nesse site não está ativa ([cacada].plataformas_sem_conta)")
        elif fonte_paga(fonte, estado.configuracao.cacada_plataformas_pagas):
            linhas.append(f"- {fonte.nome}: pulada, responder lá pede plano pago ([cacada].plataformas_pagas)")
        elif fonte.nome in falhas:
            linhas.append(f"- {fonte.nome}: **não respondeu** — {falhas[fonte.nome]}")
        elif fonte.nome in lidas:
            linhas.append(f"- {fonte.nome}: {lidas[fonte.nome]} lida(s)")
        else:
            linhas.append(f"- {fonte.nome}: não chegou a ser lida")
    logins = estado.checkpoint.get("logins")
    for site, situacao in (logins or {}).items():
        linhas.append(f"- {frase_do_estado(site, situacao)}")
    if logins is not None:
        pagas = set(estado.configuracao.cacada_plataformas_pagas) | tipos_pagos(estado.configuracao.cacada_plataformas_pagas)
        for site in (s for s in NOMES if s in pagas):
            linhas.append(f"- {NOMES[site]}: responder lá pede plano pago ([cacada].plataformas_pagas); nada de lá")
    for nome, quantas in lidas.items():
        if nome not in {fonte.nome for fonte in estado.criterios.fontes}:
            linhas.append(f"- {nome}: {quantas} lida(s)")
    repetidas = estado.checkpoint.get("repetidas")
    if repetidas:
        linhas.append(f"- {repetidas} já tinham aparecido em caçadas anteriores e ficaram de fora.")
    if fechadas := estado.checkpoint.get("fechadas"):
        linhas.append(f"- {fechadas} já estavam fechadas quando a caçada conferiu e ficaram de fora.")
    if sem_skill := estado.checkpoint.get("freelancer_sem_skill"):
        linhas.append(
            f"- {sem_skill} do Freelancer.com ficaram de fora porque pedem skill que não está no seu perfil "
            "(a conta grátis não propõe neles)."
        )
    if sem_conta := estado.checkpoint.get("sem_conta"):
        sites = ", ".join(plataformas_sem_conta) or "site sem conta ativa"
        linhas.append(
            f"- {sem_conta} ficaram de fora porque vêm de site em que sua conta não está ativa ({sites}); "
            "quando a conta valer, tire o nome de [cacada].plataformas_sem_conta."
        )
    if orcamento.abaixo:
        linhas.append(
            f"- {len(orcamento.abaixo)} de tentar a sorte com encaixe abaixo de "
            f"{estado.configuracao.cacada_encaixe_minimo_tentar} ficaram de fora ([cacada].encaixe_minimo_tentar)."
        )
    if pagas := estado.checkpoint.get("pagas"):
        sites = ", ".join(estado.configuracao.cacada_plataformas_pagas) or "site pago"
        linhas.append(
            f"- {pagas} ficaram de fora porque vêm de site em que responder pede plano pago ({sites}); "
            "quando assinar, tire o nome de [cacada].plataformas_pagas."
        )
    if aviso := estado.checkpoint.get("vintepila_aviso"):
        linhas.append(f"- VintePila: nada de lá nesta caçada — {aviso}.")
    if cheios := estado.checkpoint.get("vintepila_cheios"):
        linhas.append(f"- {cheios} da VintePila ficaram de fora porque já têm todos os candidatos (5 de 5).")
    if com_malware := estado.checkpoint.get("com_malware"):
        linhas.append(
            f"- {com_malware} bounties de organização com malware conhecido ({', '.join(ORGANIZACOES_BLOQUEADAS)}) "
            "ficaram de fora."
        )
    if conta_recente := estado.checkpoint.get("conta_recente"):
        linhas.append(f"- {conta_recente} bounties de conta do GitHub recente ou sem histórico ficaram de fora.")
    if lotados := estado.checkpoint.get("freelancer_lotados"):
        linhas.append(
            f"- {lotados} do Freelancer.com ficaram de fora porque já tinham "
            f"{estado.configuracao.cacada_max_propostas_freelancer} propostas ou mais ([cacada].max_propostas_freelancer)."
        )
    if so_para_quem_paga := estado.checkpoint.get("freelancer_so_para_quem_paga"):
        linhas.append(f"- {so_para_quem_paga} do Freelancer.com ficaram de fora porque são só para Preferred Freelancer.")
    if selecionados := estado.checkpoint.get("freelancer_so_para_selecionados"):
        linhas.append(
            f"- {selecionados} do Freelancer.com ficaram de fora porque, logado, a página diz que são só para "
            "freelancers selecionados (pedem saldo de US$ 20)."
        )
    if repetidos := estado.checkpoint.get("freelancer_repetidos"):
        linhas.append(f"- {repetidos} anúncios repetidos do Freelancer.com (mesmo cliente e mesmo título) viraram um.")
    if ja_propostas := estado.checkpoint.get("ja_propostas"):
        linhas.append(f"- {ja_propostas} da 99Freelas ficaram de fora porque você já mandou proposta neles.")
    if (conexoes := estado.checkpoint.get("conexoes_99freelas")) is not None:
        linhas.append(f"- 99Freelas: {conexoes} conexões no estoque (visto logado).")
    if lances := estado.checkpoint.get("lances_freelancer"):
        linhas.append(f"- Freelancer.com: {lances[0]} de {lances[1]} lances restantes este mês (visto logado).")
    for aviso in estado.criterios.avisos:
        linhas.append(f"- Aviso: {aviso}")
    linhas += ["", "O perfil usado está em [[cacada/perfil]]; os critérios, em [[cacada/criterios]].", ""]
    return "\n".join(linha for linha in linhas if linha is not None)


def _frase_do_envio(estado: _Estado) -> str:
    envios = (estado.checkpoint.get("envios") or {}).values()
    feitos = sum(1 for e in envios if e.get("status") == "feito")
    if not feitos:
        return "Nada foi enviado a ninguém: tudo aqui espera você ler, ajustar e mandar."
    return (
        f"O Whis enviou sozinho {feitos} candidatura(s) de tentar a sorte (marcadas abaixo, e no registro de ações); "
        "as chances boas e o resto esperam você."
    )


def _linha_da_oportunidade(oportunidade: Oportunidade, veredito: Veredito) -> str:
    partes = [
        "Freela" if oportunidade.tipo == FREELA else "Vaga",
        oportunidade.fonte,
        oportunidade.local,
        oportunidade.regime,
        oportunidade.faixa or ("faixa desconhecida" if oportunidade.tipo != FREELA else "orçamento não informado"),
        f"encaixe {veredito.encaixe}",
    ]
    return " · ".join(p for p in partes if p) + f" · [Abrir]({oportunidade.link})"


def _curto(texto: str, tamanho: int = 90) -> str:
    texto = texto.replace("[", "(").replace("]", ")")
    return texto if len(texto) <= tamanho else texto[: tamanho - 1] + "…"


def _data(texto: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto))
    except (TypeError, ValueError):
        return None


def _velho(inicio: object, agora: datetime) -> bool:
    momento = _data(inicio)
    if momento is None:
        return True
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return agora - momento > IDADE_MAXIMA_DA_RETOMADA


def fontes_lidas_aqui(fontes: Sequence[FonteDaCacada]) -> list[FonteDaCacada]:
    from .fontes_da_cacada import TIPOS_LIDOS_AQUI

    return [fonte for fonte in fontes if fonte.tipo in TIPOS_LIDOS_AQUI]
