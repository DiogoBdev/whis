"""O toast do Windows: o aviso que chega mesmo com a bandeja fora do ar.

O balão que a bandeja mostra hoje é o `Shell_NotifyIcon` legado do `pystray`:
funciona sem pré-requisito, mas nunca terá botão e morre com a bandeja. Um
toast de verdade (`Windows.UI.Notifications`) é outro mecanismo: exige a AUMID
com atalho no Menu Iniciar (camada 0) e, em troca, aceita um botão que abre
outro programa por URI — é assim que "abrir a nota no Obsidian" vai funcionar.

O disparo mora em `avisar.ps1`, chamado por `executar` injetável como tudo em
`automacao`. Falhar em avisar **nunca** derruba quem chamou: um toast é
conveniência, e a daily que já nasceu não pode morrer porque o aviso não saiu.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .automacao import AUMID, Executor, _rodar

_log = logging.getLogger("whis.avisos")

#: O script que monta o XML do toast (com escape) e o mostra em nome da AUMID.
SCRIPT_DO_AVISO = Path(__file__).with_name("avisar.ps1")


def avisar(
    titulo: str,
    texto: str,
    *,
    botao: tuple[str, str] | None = None,
    executar: Executor = _rodar,
) -> bool:
    """Mostra um toast com `titulo` e `texto`; `botao` é `(rótulo, URI)`.

    O botão usa `activationType="protocol"`: o Windows abre a URI (por exemplo
    `obsidian://open?...`) sem que o Whis precise estar vivo para atender o
    clique. Devolve `False` — e loga — se o toast não saiu.
    """
    from .plataforma import MACOS, comando_de_aviso_mac

    if MACOS:
        comando = comando_de_aviso_mac(titulo, texto, botao[1] if botao else None)
        botao = None
    else:
        comando = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_DO_AVISO),
            "-Titulo",
            titulo,
            "-Texto",
            texto,
            "-Aumid",
            AUMID,
        ]
    if botao is not None:
        rotulo, uri = botao
        comando += ["-RotuloDoBotao", rotulo, "-UriDoBotao", uri]

    try:
        resultado = executar(comando)
    except Exception as erro:  # noqa: BLE001 — o aviso nunca derruba quem chamou
        _log.warning("O toast '%s' não saiu: %s", titulo, erro)
        return False
    if resultado.returncode != 0:
        _log.warning(
            "O toast '%s' não saiu (código %d): %s",
            titulo,
            resultado.returncode,
            (resultado.stderr or resultado.stdout or "").strip(),
        )
        return False
    _log.info("Toast mostrado: %s", titulo)
    return True
