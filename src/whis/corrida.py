r"""O que deixa a caçada correr por horas: o PC acordado, a trava, o andamento e o checkpoint.

A caçada é a primeira peça do Whis que roda horas, e não minutos (Q35 da
interface: *"um comando de 8 horas atravessa suspensão, logoff e reboot"*).
Quatro garantias moram aqui, nenhuma conhece vaga nem Claude:

- **o PC não dorme** enquanto ela corre (`SetThreadExecutionState`, o pedido
  que o próprio Windows oferece; vale só enquanto o processo vive, e some
  sozinho se ele morrer);
- **uma de cada vez**: a mesma trava de faixa da bandeja (`instancia.Trava`),
  num arquivo próprio, e o PID de quem segura;
- **o andamento em disco**: uma linha de texto que a palheta lê para dizer em
  que etapa ela está, sem ninguém abrir log;
- **o checkpoint**: o que cada etapa já produziu, num JSON gravado inteiro a
  cada passo (`.tmp` + `os.replace`), para a corrida seguinte retomar de onde
  esta parou em vez de gastar a assinatura de novo.

Tudo mora em `%LOCALAPPDATA%\whis\cacada\`, fora do vault: é estado de
máquina, não memória.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .instancia import OutraInstancia, Trava

_log = logging.getLogger("whis.corrida")

NOME_DA_TRAVA = "cacada.lock"
NOME_DO_ANDAMENTO = "andamento.json"
NOME_DO_CHECKPOINT = "checkpoint.json"

#: `SetThreadExecutionState`: o sistema não dorme e o pedido vale até o fim do processo.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class CacadaEmAndamento(RuntimeError):
    """Outra caçada já está correndo nesta máquina."""


def pasta_da_cacada() -> Path:
    r"""`%LOCALAPPDATA%\whis\cacada` — o estado da corrida, fora do vault."""
    from .plataforma import pasta_local_do_whis

    return pasta_local_do_whis() / "cacada"


# -- o PC acordado ------------------------------------------------------------


_caffeinate = None


def _caffeinate_ligar() -> bool:
    """No macOS: `caffeinate -i -w <pid>` segura a suspensão por inatividade enquanto este processo viver."""
    global _caffeinate
    import os
    import subprocess

    if _caffeinate is not None and _caffeinate.poll() is None:
        return True
    try:
        _caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except OSError as erro:
        _log.warning("Não consegui pedir ao macOS para não suspender: %s", erro)
        return False
    _log.info("O Mac fica acordado enquanto a caçada corre.")
    return True


def _caffeinate_desligar() -> None:
    global _caffeinate
    if _caffeinate is not None:
        _caffeinate.terminate()
        _caffeinate = None


def manter_acordado(*, pedir: Callable[[int], int] | None = None) -> bool:
    """Pede ao Windows para não suspender enquanto este processo viver.

    Não impede a tela de apagar nem o logoff; impede a suspensão por
    inatividade, que é o que mataria a caçada no meio da madrugada. Fora do
    Windows (desenvolvimento) não faz nada e devolve `False`.
    """
    if pedir is None:
        if sys.platform == "darwin":
            return _caffeinate_ligar()
        if sys.platform != "win32":
            return False
        import ctypes

        pedir = ctypes.windll.kernel32.SetThreadExecutionState  # type: ignore[attr-defined]
    try:
        anterior = pedir(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception as erro:  # noqa: BLE001 — sem isso a caçada corre do mesmo jeito
        _log.warning("Não consegui pedir ao Windows para não suspender: %s", erro)
        return False
    if not anterior:
        _log.warning("O Windows recusou o pedido para não suspender durante a caçada.")
        return False
    _log.info("O PC fica acordado enquanto a caçada corre.")
    return True


def liberar_sono(*, pedir: Callable[[int], int] | None = None) -> None:
    """Devolve ao Windows o direito de suspender. O fim do processo faria o mesmo."""
    if pedir is None:
        if sys.platform == "darwin":
            _caffeinate_desligar()
            return
        if sys.platform != "win32":
            return
        import ctypes

        pedir = ctypes.windll.kernel32.SetThreadExecutionState  # type: ignore[attr-defined]
    try:
        pedir(ES_CONTINUOUS)
    except Exception:  # noqa: BLE001 — o processo está saindo de todo jeito
        pass


# -- uma de cada vez ------------------------------------------------------------


class TravaDaCacada:
    """A trava de faixa da bandeja, com a mensagem da caçada."""

    def __init__(self, pasta: Path) -> None:
        self._trava = Trava(Path(pasta) / NOME_DA_TRAVA)

    def __enter__(self) -> "TravaDaCacada":
        try:
            self._trava.adquirir()
        except OutraInstancia as erro:
            dono = self._trava.pid_registrado()
            de_quem = f" (processo {dono})" if dono else ""
            raise CacadaEmAndamento(f"já existe uma caçada correndo nesta máquina{de_quem}.") from erro
        return self

    def __exit__(self, *_: object) -> None:
        self._trava.liberar()


def cacada_correndo(pasta: Path) -> bool:
    """Alguém segura a trava agora? Tenta tomá-la e devolve na hora."""
    trava = Trava(Path(pasta) / NOME_DA_TRAVA)
    try:
        trava.adquirir()
    except OutraInstancia:
        return True
    except OSError:
        return False
    trava.liberar()
    return False


# -- o andamento ------------------------------------------------------------------


def escrever_andamento(pasta: Path, etapa: str, detalhe: str = "", *, agora: datetime | None = None) -> None:
    """A linha que a palheta mostra: a etapa, o detalhe e a hora. Falhar aqui não para nada."""
    momento = (agora or datetime.now().astimezone()).isoformat(timespec="seconds")
    dados = {"etapa": etapa, "detalhe": detalhe, "quando": momento, "pid": os.getpid()}
    try:
        _gravar_json(Path(pasta) / NOME_DO_ANDAMENTO, dados)
    except OSError as erro:
        _log.debug("O andamento da caçada não foi gravado: %s", erro)


def ler_andamento(pasta: Path) -> dict[str, str] | None:
    arquivo = Path(pasta) / NOME_DO_ANDAMENTO
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {chave: str(valor) for chave, valor in dados.items()} if isinstance(dados, dict) else None


# -- o checkpoint ------------------------------------------------------------------


class Checkpoint:
    """O que a corrida já produziu, por etapa, num JSON gravado inteiro a cada passo.

    `dados` é um dicionário livre de JSON; quem dá sentido às chaves é a
    caçada. `abrir` devolve o checkpoint anterior quando ele existe e não
    terminou — é isso que é retomar.
    """

    def __init__(self, arquivo: Path, dados: dict[str, Any] | None = None) -> None:
        self.arquivo = Path(arquivo)
        self.dados: dict[str, Any] = dados if dados is not None else {}

    @classmethod
    def abrir(cls, pasta: Path, *, do_zero: bool = False) -> "Checkpoint":
        arquivo = Path(pasta) / NOME_DO_CHECKPOINT
        if do_zero:
            return cls(arquivo)
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(arquivo)
        except (OSError, ValueError) as erro:
            _log.warning("Checkpoint da caçada ilegível (%s): começando do zero.", erro)
            return cls(arquivo)
        if not isinstance(dados, dict) or dados.get("terminada"):
            return cls(arquivo)
        _log.info("Retomando a caçada de %s, parada em: %s.", dados.get("inicio", "?"), dados.get("etapa", "?"))
        return cls(arquivo, dados)

    @property
    def retomada(self) -> bool:
        return bool(self.dados.get("inicio"))

    def get(self, chave: str, padrao: Any = None) -> Any:
        return self.dados.get(chave, padrao)

    def guardar(self, **campos: Any) -> None:
        self.dados.update(campos)
        _gravar_json(self.arquivo, self.dados)

    def terminar(self) -> None:
        self.guardar(terminada=True)


def _gravar_json(arquivo: Path, dados: dict[str, Any]) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_name(arquivo.name + ".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporario, arquivo)


# -- a virada semanal do plano ---------------------------------------------------------

NOME_DA_VIRADA = "virada.json"


def guardar_virada(pasta: Path, quando: datetime) -> None:
    """A última hora de virada semanal lida do plano: ela se repete toda semana na mesma hora."""
    try:
        _gravar_json(Path(pasta) / NOME_DA_VIRADA, {"vira_sete_dias": quando.isoformat()})
    except OSError as erro:
        _log.debug("A virada do plano não foi gravada: %s", erro)


def ler_virada(pasta: Path) -> datetime | None:
    try:
        dados = json.loads((Path(pasta) / NOME_DA_VIRADA).read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(dados["vira_sete_dias"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
