"""A URI `obsidian://` — o único jeito de abrir a nota certa nesta máquina.

`.md` não tem programa associado aqui, então `os.startfile` de uma nota nunca
abriu o Obsidian. A URI abre a nota no vault certo, com o Obsidian aberto ou
não, e é o que o botão do toast e o menu da bandeja usam.

Mora em módulo próprio, e não em `vault.py`, porque a bandeja precisa dela e a
bandeja não pode conhecer o vault (é regra da spec, provada por teste).
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from . import sem_console as _sem_console
from .configuracao import Configuracao

_log = logging.getLogger("whis.obsidian")

#: Quem lança um caminho ou URI no Windows (injetável em teste).
Lancador = Callable[[str], None]


def nome_do_vault(configuracao: Configuracao) -> str:
    """O que vai em `vault=`: `[obsidian].vault`, ou o nome da pasta."""
    return configuracao.obsidian_vault or Path(configuracao.vault).name


def uri_da_nota(configuracao: Configuracao, caminho_relativo: str) -> str:
    """`obsidian://open?vault=...&file=...` para uma nota do vault.

    O `file` vai sem `.md` e com cada caractere fora do alfabeto escapado
    (`/` vira `%2F`, espaço vira `%20`): o Obsidian não entende `+` como
    espaço, e uma URI com a barra crua abre a nota errada ou nenhuma.
    """
    nota = caminho_relativo.replace("\\", "/").removeprefix("./").removesuffix(".md")
    return (
        f"obsidian://open?vault={quote(nome_do_vault(configuracao), safe='')}"
        f"&file={quote(nota, safe='')}"
    )


def lancar(alvo: str, *, iniciar=None, executar=subprocess.run) -> None:
    """`ShellExecute` (via `os.startfile`), que aceita caminho e URI.

    Se ele recusar a URI, `cmd /c start` é a segunda porta: é o mesmo
    `ShellExecute`, mas com o `start` resolvendo o protocolo registrado.
    """
    iniciar = iniciar or getattr(os, "startfile", None)
    if iniciar is None:  # macOS (`open`) ou Linux (`xdg-open`)
        from .plataforma import comando_de_abrir

        executar(comando_de_abrir(alvo), check=False)
        return
    try:
        iniciar(alvo)
    except OSError as erro:
        if "://" in alvo:
            _log.info("startfile recusou a URI (%s); tentando por 'start'.", erro)
            executar(["cmd", "/c", "start", "", alvo], check=False, **_sem_console.opcoes())
            return
        _log.error("Não foi possível abrir %s: %s", alvo, erro)
