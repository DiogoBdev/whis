"""O painel de configuração: as 44 chaves do `config.toml`, segredos inclusive.

Não fere a regra da entrega 3: o valor de um segredo vai do teclado do
usuário para o arquivo, sem passar por modelo nenhum — e nunca por log. O
painel é a única superfície do inventário que resolve "configurar" de
verdade, porque o usuário não acha o arquivo quando precisa.

A descrição dos campos é dados (`CAMPOS`), derivada do modelo: a ajuda de
cada chave é o comentário que já está lá. `Painel` é a lógica — texto de cada
campo, validação com as mesmas mensagens de `configuracao`, salvar com backup
— e enxerga a tela só por `PortaFormulario`; `FormularioTk` é a casca fina que
desenha isso num `Toplevel` do Tk da palheta. O teste monta o painel com um
formulário dublê e nunca abre janela.
"""

from __future__ import annotations

import sys as _sys

_FAMILIA = "Helvetica Neue" if _sys.platform == "darwin" else "Segoe UI"

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .configuracao import (
    CHAVES,
    DIAS_DA_SEMANA,
    MODELO_DE_CONFIGURACAO,
    Chave,
    Configuracao,
    ConfiguracaoInvalida,
    Tipo,
    _LINHA_DE_CHAVE,
    _LINHA_DE_SECAO,
    carregar_texto,
    escrever,
    gerar_toml,
)

_log = logging.getLogger("whis.painel")

TITULO = "Whis — configuração"

#: Os onze segredos, para o teste conferir que são os que a spec lista.
SEGREDOS = (
    "[linear].api_key",
    "[slack].user_token",
    "[slack].bot_token",
    "[slack].app_token",
    "[cacada].cloudflare_token",
    "[cacada].senha_de_app",
    "[cacada].resend_chave",
    "[cacada].senha_de_app_antiga",
    "[cacada].cpf",
    "[linkedin].client_secret",
    "[celular].telegram_token",
    "[x].api_key",
    "[x].api_key_secret",
    "[x].access_token",
    "[x].access_token_secret",
)

MENSAGEM_DE_SALVO = (
    "Salvo. A palheta já usa a configuração nova; reinicie a bandeja para mudanças de "
    "atalho e caminhos valerem lá."
)

#: O que aparece no lugar de um segredo enquanto o campo está escondido.
MARCA_DO_SEGREDO = "•"


@dataclass(frozen=True)
class Campo:
    """Uma linha do painel: a chave, o rótulo e a ajuda tirada do modelo."""

    chave: Chave
    ajuda: str

    @property
    def rotulo(self) -> str:
        return self.chave.caminho()

    @property
    def tipo(self) -> Tipo:
        return self.chave.tipo

    @property
    def caminho(self) -> str:
        return self.chave.caminho()


def campos_do_modelo(modelo: str = MODELO_DE_CONFIGURACAO) -> tuple[Campo, ...]:
    """Lê o modelo e casa cada `chave = valor  # ajuda` com a `Chave` correspondente.

    Uma chave no modelo sem `Chave`, ou o contrário, é erro de programação e
    estoura aqui — não na tela do usuário.
    """
    por_caminho = {(chave.secao, chave.nome): chave for chave in CHAVES}
    vistas: set[Chave] = set()
    campos: list[Campo] = []
    secao = ""
    for linha in modelo.format(vault="").splitlines():
        cabecalho = _LINHA_DE_SECAO.match(linha)
        if cabecalho is not None:
            secao = cabecalho["secao"]
            continue
        campo = _LINHA_DE_CHAVE.match(linha)
        if campo is None:
            continue
        chave = por_caminho.get((secao, campo["chave"]))
        if chave is None:
            raise RuntimeError(f"O modelo tem a chave {secao}.{campo['chave']}, que CHAVES não conhece.")
        ajuda = (campo["comentario"] or "").strip().lstrip("#").strip()
        campos.append(Campo(chave, ajuda))
        vistas.add(chave)
    faltando = [chave.caminho() for chave in CHAVES if chave not in vistas]
    if faltando:
        raise RuntimeError(f"CHAVES tem {', '.join(faltando)}, que o modelo não traz.")
    return tuple(campos)


CAMPOS: tuple[Campo, ...] = campos_do_modelo()


# -- os valores como texto -------------------------------------------------------


def texto_do_valor(configuracao: Configuracao, campo: Campo) -> str | dict[str, str]:
    """O valor de um campo como o formulário o mostra.

    Lista é uma entrada por linha; a tabela de horários é um texto por dia
    (vazio = sem daily naquele dia); caminho vazio é `""`; booleano é `true`
    ou `false`, como no TOML.
    """
    valor = getattr(configuracao, campo.chave.atributo)
    if campo.tipo is Tipo.BOOLEANO:
        return "true" if valor else "false"
    if campo.tipo is Tipo.HORARIOS:
        horas = dict(valor)
        return {dia: horas.get(dia, "") for dia in DIAS_DA_SEMANA}
    if campo.tipo is Tipo.LISTA:
        return "\n".join(valor)
    if campo.tipo is Tipo.CAMINHO:
        return Path(valor).as_posix() if valor else ""
    return "" if valor is None else str(valor)


def textos_de(configuracao: Configuracao) -> dict[Campo, str | dict[str, str]]:
    return {campo: texto_do_valor(configuracao, campo) for campo in CAMPOS}


def valor_do_texto(campo: Campo, texto: str | Mapping[str, str]) -> object:
    """O que vai para o TOML a partir do que foi digitado.

    Um inteiro que não é inteiro vai como string, de propósito: é `carregar`
    quem recusa, com a mensagem que o arquivo daria. O painel não inventa
    validação própria.
    """
    if campo.tipo is Tipo.HORARIOS:
        horas = texto if isinstance(texto, Mapping) else {}
        return tuple((dia, hora.strip()) for dia, hora in horas.items() if hora.strip())
    assert isinstance(texto, str)
    if campo.tipo is Tipo.LISTA:
        return tuple(linha.strip() for linha in texto.splitlines() if linha.strip())
    if campo.tipo is Tipo.INTEIRO:
        limpo = texto.strip()
        return int(limpo) if limpo.lstrip("-").isdigit() else limpo
    if campo.tipo is Tipo.CAMINHO:
        return texto.strip().replace("\\", "/")
    if campo.tipo is Tipo.SEGREDO:
        return texto.strip()
    if campo.tipo is Tipo.BOOLEANO:
        limpo = texto.strip().lower()
        return {"true": True, "false": False}.get(limpo, limpo)
    return texto


def montar_configuracao(
    textos: Mapping[Campo, str | Mapping[str, str]], *, origem: Path
) -> Configuracao:
    """Os textos do formulário viram `Configuracao` pelo mesmo `carregar` do arquivo."""
    valores = {campo.chave: valor_do_texto(campo, textos[campo]) for campo in CAMPOS}
    return carregar_texto(gerar_toml(valores), origem=origem)


# -- a porta e a lógica ------------------------------------------------------------


class PortaFormulario(Protocol):
    """O que o painel precisa da tela — o `Toplevel` do Tk ou um dublê."""

    def mostrar(self, campos: tuple[Campo, ...], textos: Mapping[Campo, object]) -> None: ...

    def ler(self) -> dict[Campo, str | dict[str, str]]: ...

    def mostrar_erro(self, mensagem: str) -> None: ...

    def mostrar_sucesso(self, mensagem: str) -> None: ...

    def fechar(self) -> None: ...


class Painel:
    """Abrir, salvar, fechar — sem saber o que é um widget.

    `ao_salvar` recebe a configuração nova: é por ele que a palheta passa a
    usar o que acabou de ser salvo, sem reiniciar nada.
    """

    def __init__(
        self,
        configuracao: Configuracao,
        formulario: PortaFormulario,
        *,
        destino: Path | None = None,
        ao_salvar: Callable[[Configuracao], None] | None = None,
        gravar: Callable[[Configuracao, Path], Path] = escrever,
    ) -> None:
        self.configuracao = configuracao
        self._formulario = formulario
        self._destino = Path(destino or configuracao.origem or "config.toml")
        self._ao_salvar = ao_salvar
        self._gravar = gravar

    @property
    def destino(self) -> Path:
        return self._destino

    def abrir(self) -> None:
        self._formulario.mostrar(CAMPOS, textos_de(self.configuracao))

    def salvar(self) -> Configuracao | None:
        """Valida, grava com backup e recarrega. Erro fica na tela, e nada é escrito."""
        try:
            nova = montar_configuracao(self._formulario.ler(), origem=self._destino)
            self._gravar(nova, self._destino)
        except ConfiguracaoInvalida as erro:
            self._formulario.mostrar_erro(str(erro))
            return None
        except OSError as erro:
            self._formulario.mostrar_erro(f"Não foi possível escrever {self._destino}: {erro}")
            return None
        self.configuracao = nova
        if self._ao_salvar is not None:
            self._ao_salvar(nova)
        _log.info("Configuração salva pelo painel em %s.", self._destino)
        self._formulario.mostrar_sucesso(MENSAGEM_DE_SALVO)
        return nova

    def fechar(self) -> None:
        self._formulario.fechar()


# -- a casca Tk ---------------------------------------------------------------------


class FormularioTk:
    """A tela: um `Toplevel` rolável com uma linha por chave.

    `Entry(show="•")` para os segredos, botão de pasta para os caminhos,
    `Text` para as listas, sete campos `HH:MM` para os horários, caixa de
    marcar para os booleanos. Nada aqui
    decide nada: ler e mostrar, e chamar o painel nos botões.
    """

    def __init__(self, raiz, *, escolher_pasta=None) -> None:
        import tkinter as tk
        from tkinter import filedialog, ttk

        self._tk = tk
        self._ttk = ttk
        self._escolher_pasta = escolher_pasta or (lambda: filedialog.askdirectory(parent=self._janela))
        self._janela = tk.Toplevel(raiz)
        self._janela.title(TITULO)
        self._janela.geometry("760x640")
        self._janela.attributes("-topmost", True)
        self._entradas: dict[Campo, object] = {}
        self._painel: Painel | None = None
        self._aberto = True
        self._janela.protocol("WM_DELETE_WINDOW", self.fechar)

    def ligar(self, painel: Painel) -> None:
        self._painel = painel

    def aberto(self) -> bool:
        return self._aberto

    def trazer_para_frente(self) -> None:
        self._janela.deiconify()
        self._janela.lift()
        self._janela.focus_force()

    def mostrar(self, campos: tuple[Campo, ...], textos: Mapping[Campo, object]) -> None:
        tk, ttk = self._tk, self._ttk
        janela = self._janela

        rodape = ttk.Frame(janela, padding=8)
        rodape.pack(side="bottom", fill="x")
        self._status = ttk.Label(rodape, text="", wraplength=700, justify="left")
        self._status.pack(side="top", fill="x", pady=(0, 6))
        ttk.Button(rodape, text="Salvar", command=self._salvar).pack(side="right", padx=4)
        ttk.Button(rodape, text="Fechar", command=self.fechar).pack(side="right")

        tela = tk.Canvas(janela, highlightthickness=0)
        barra = ttk.Scrollbar(janela, orient="vertical", command=tela.yview)
        tela.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        tela.pack(side="left", fill="both", expand=True)
        conteudo = ttk.Frame(tela, padding=10)
        conteudo_id = tela.create_window((0, 0), window=conteudo, anchor="nw")
        conteudo.bind("<Configure>", lambda _e: tela.configure(scrollregion=tela.bbox("all")))
        tela.bind("<Configure>", lambda e: tela.itemconfigure(conteudo_id, width=e.width))
        tela.bind_all("<MouseWheel>", lambda e: tela.yview_scroll(-int(e.delta / 120), "units"))
        conteudo.columnconfigure(1, weight=1)

        linha = 0
        secao_atual = None
        for campo in campos:
            if campo.chave.secao != secao_atual:
                secao_atual = campo.chave.secao
                titulo = f"[{secao_atual}]" if secao_atual else "geral"
                ttk.Label(conteudo, text=titulo, font=(_FAMILIA, 10, "bold")).grid(
                    row=linha, column=0, columnspan=3, sticky="w", pady=(12, 2)
                )
                linha += 1
            ttk.Label(conteudo, text=campo.chave.nome).grid(row=linha, column=0, sticky="nw", padx=(0, 8), pady=2)
            self._entradas[campo] = self._desenhar_entrada(conteudo, campo, textos[campo], linha)
            if campo.ajuda:
                ttk.Label(conteudo, text=campo.ajuda, foreground="#666", wraplength=520, justify="left").grid(
                    row=linha + 1, column=1, columnspan=2, sticky="w", pady=(0, 4)
                )
            linha += 2
        self.trazer_para_frente()

    def _desenhar_entrada(self, pai, campo: Campo, texto, linha: int):
        tk, ttk = self._tk, self._ttk
        if campo.tipo is Tipo.HORARIOS:
            quadro = ttk.Frame(pai)
            quadro.grid(row=linha, column=1, columnspan=2, sticky="w")
            entradas: dict[str, object] = {}
            for coluna, dia in enumerate(DIAS_DA_SEMANA):
                ttk.Label(quadro, text=dia).grid(row=0, column=coluna, padx=2)
                entrada = ttk.Entry(quadro, width=6)
                entrada.insert(0, texto.get(dia, ""))
                entrada.grid(row=1, column=coluna, padx=2)
                entradas[dia] = entrada
            return entradas
        if campo.tipo is Tipo.BOOLEANO:
            marcado = tk.BooleanVar(pai, value=texto == "true")
            ttk.Checkbutton(pai, variable=marcado).grid(row=linha, column=1, sticky="w")
            return marcado
        if campo.tipo is Tipo.LISTA:
            caixa = tk.Text(pai, height=3, width=60, font=(_FAMILIA, 9))
            caixa.insert("1.0", texto)
            caixa.grid(row=linha, column=1, columnspan=2, sticky="ew")
            return caixa
        entrada = ttk.Entry(pai, show=MARCA_DO_SEGREDO if campo.tipo is Tipo.SEGREDO else "")
        entrada.insert(0, texto)
        entrada.grid(row=linha, column=1, sticky="ew")
        if campo.tipo is Tipo.CAMINHO:
            ttk.Button(pai, text="Pasta…", command=lambda e=entrada: self._escolher(e)).grid(
                row=linha, column=2, padx=(4, 0)
            )
        elif campo.tipo is Tipo.SEGREDO:
            ttk.Button(pai, text="Mostrar", command=lambda e=entrada: self._alternar_segredo(e)).grid(
                row=linha, column=2, padx=(4, 0)
            )
        return entrada

    def _escolher(self, entrada) -> None:
        pasta = self._escolher_pasta()
        if pasta:
            entrada.delete(0, "end")
            entrada.insert(0, Path(pasta).as_posix())

    def _alternar_segredo(self, entrada) -> None:
        entrada.configure(show="" if entrada.cget("show") else MARCA_DO_SEGREDO)

    def ler(self) -> dict[Campo, str | dict[str, str]]:
        textos: dict[Campo, str | dict[str, str]] = {}
        for campo, entrada in self._entradas.items():
            if campo.tipo is Tipo.HORARIOS:
                textos[campo] = {dia: e.get() for dia, e in entrada.items()}
            elif campo.tipo is Tipo.LISTA:
                textos[campo] = entrada.get("1.0", "end-1c")
            elif campo.tipo is Tipo.BOOLEANO:
                textos[campo] = "true" if entrada.get() else "false"
            else:
                textos[campo] = entrada.get()
        return textos

    def mostrar_erro(self, mensagem: str) -> None:
        self._status.configure(text=mensagem, foreground="#b00020")

    def mostrar_sucesso(self, mensagem: str) -> None:
        self._status.configure(text=mensagem, foreground="#1b5e20")

    def fechar(self) -> None:
        if not self._aberto:
            return
        self._aberto = False
        try:
            self._tk.Misc.unbind_all(self._janela, "<MouseWheel>")
            self._janela.destroy()
        except self._tk.TclError:
            pass

    def _salvar(self) -> None:
        if self._painel is not None:
            self._painel.salvar()


_abertos: dict[int, FormularioTk] = {}


def abrir_painel(raiz, palheta, *, destino: Path | None = None) -> Painel | None:
    """Abre o painel sobre o `Tk` da palheta — ou traz para frente o que já está aberto.

    Salvar recarrega a configuração da palheta na hora; o que a bandeja já
    leu (atalho, caminhos) só muda ao reiniciar, e a tela diz isso.
    """
    aberto = _abertos.get(id(raiz))
    if aberto is not None and aberto.aberto():
        aberto.trazer_para_frente()
        return None

    formulario = FormularioTk(raiz)
    _abertos[id(raiz)] = formulario

    def recarregar(nova: Configuracao) -> None:
        palheta.configuracao = nova

    painel = Painel(
        palheta.configuracao,
        formulario,
        destino=destino or palheta.configuracao.origem,
        ao_salvar=recarregar,
    )
    formulario.ligar(painel)
    painel.abrir()
    return painel
