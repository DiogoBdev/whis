r"""As tarefas e a inicialização no macOS, pelo `launchd`.

No Windows o Whis usa o Agendador de Tarefas e atalhos na pasta Inicialização
(`automacao.py`). No Mac as duas coisas são o mesmo mecanismo: um `.plist` em
`~/Library/LaunchAgents`, carregado no domínio `gui/<uid>` do usuário.

- tarefa agendada (`Whis-Daily` etc.) → `dev.whis.whis-daily.plist` com
  `StartCalendarInterval` (um dicionário por gatilho `Dia=HH:MM`) e
  `RunAtLoad` para o gatilho `Logon`. O `launchd` roda o horário perdido
  quando o Mac acorda, como o `-StartWhenAvailable` do Windows;
- atalho de inicialização (`Whis.lnk`) → `dev.whis.bandeja.plist` com
  `RunAtLoad`.

O trabalho sujo sai por `executar`, injetável como em `automacao`.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

_log = logging.getLogger("whis.launchd")

Executor = Callable[[list[str]], subprocess.CompletedProcess]

PREFIXO = "dev.whis."

_DIAS = {
    "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
    "Thursday": 4, "Friday": 5, "Saturday": 6,
}


def pasta_dos_agentes() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def rotulo(nome: str) -> str:
    """`Whis-Daily` → `dev.whis.whis-daily`."""
    return PREFIXO + nome.lower()


def caminho_do_plist(nome: str) -> Path:
    return pasta_dos_agentes() / f"{rotulo(nome)}.plist"


def _dominio() -> str:
    return f"gui/{os.getuid()}"


def pasta_dos_logs() -> Path:
    from .plataforma import pasta_local_do_whis

    return pasta_local_do_whis() / "tarefas"


def intervalos(gatilhos: list[str]) -> tuple[list[dict[str, int]], bool]:
    """Os gatilhos do Windows (`Monday=13:55`, `Daily=07:00`, `Logon`) em `StartCalendarInterval`."""
    calendario: list[dict[str, int]] = []
    no_logon = False
    for gatilho in gatilhos:
        if gatilho == "Logon":
            no_logon = True
            continue
        dia, _, hora = gatilho.partition("=")
        horas, minutos = (int(parte) for parte in hora.split(":"))
        item = {"Hour": horas, "Minute": minutos}
        if dia != "Daily":
            item["Weekday"] = _DIAS[dia]
        calendario.append(item)
    return calendario, no_logon


def escrever_e_carregar(
    nome: str,
    programa: list[str],
    *,
    diretorio: Path,
    gatilhos: list[str] | None,
    executar: Executor,
    destino: Path | None = None,
) -> Path:
    """Escreve o `.plist` e o (re)carrega no `launchd`. `gatilhos=None` é "só no login"."""
    destino = destino or caminho_do_plist(nome)
    rot = destino.stem
    pasta_dos_logs().mkdir(parents=True, exist_ok=True)
    dados: dict = {
        "Label": rot,
        "ProgramArguments": programa,
        "WorkingDirectory": str(diretorio),
        "StandardOutPath": str(pasta_dos_logs() / f"{rot}.log"),
        "StandardErrorPath": str(pasta_dos_logs() / f"{rot}.log"),
        "EnvironmentVariables": {
            # o launchd não herda o PATH do shell: sem isso o `claude` e o `git` somem
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "LANG": "pt_BR.UTF-8",
            "PYTHONIOENCODING": "utf-8",
        },
    }
    if gatilhos is None:
        dados["RunAtLoad"] = True
    else:
        calendario, no_logon = intervalos(gatilhos)
        if calendario:
            dados["StartCalendarInterval"] = calendario
        if no_logon:
            dados["RunAtLoad"] = True
    destino.parent.mkdir(parents=True, exist_ok=True)
    executar(["launchctl", "bootout", f"{_dominio()}/{rot}"])
    with destino.open("wb") as arquivo:
        plistlib.dump(dados, arquivo)
    resultado = executar(["launchctl", "bootstrap", _dominio(), str(destino)])
    if resultado.returncode != 0:
        from .automacao import ErroDeAutomacao

        raise ErroDeAutomacao(
            f"o launchd recusou {destino}: {(resultado.stderr or resultado.stdout or '').strip()}"
        )
    _log.info("Agente '%s' carregado no launchd (%s).", rot, destino)
    return destino


def registrar_tarefa(
    nome: str, *, executavel: str, argumentos: str, diretorio: Path, gatilhos: list[str], executar: Executor
) -> None:
    escrever_e_carregar(
        nome,
        [executavel, *shlex.split(argumentos)],
        diretorio=diretorio,
        gatilhos=gatilhos,
        executar=executar,
    )


def remover(destino: Path, *, executar: Executor) -> bool:
    """Descarrega e apaga o `.plist`. Devolve se ele existia."""
    executar(["launchctl", "bootout", f"{_dominio()}/{destino.stem}"])
    if not destino.exists():
        return False
    destino.unlink()
    _log.info("Agente '%s' removido do launchd.", destino.stem)
    return True


def desregistrar_tarefa(nome: str, *, executar: Executor) -> bool:
    return remover(caminho_do_plist(nome), executar=executar)


def tarefa_agendada(nome: str, *, executar: Executor) -> bool:
    if not caminho_do_plist(nome).exists():
        return False
    return executar(["launchctl", "print", f"{_dominio()}/{rotulo(nome)}"]).returncode == 0
