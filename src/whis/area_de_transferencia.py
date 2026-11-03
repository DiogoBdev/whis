"""Clipboard e colar na janela de antes, pelo `pywin32`.

A palheta rouba o foco ao abrir; para colar um prompt onde o usuário estava, a
janela de primeiro plano é guardada ao abrir (`lembrar_janela_anterior`) e,
depois de a palheta sumir, volta ao primeiro plano e recebe um Ctrl+V.
`SetForegroundWindow` só é aceito porque o processo recebeu a última entrada
(o Enter na palheta) — chamado de outro contexto, o Windows recusa.

A suíte nunca chega aqui: o `conftest` troca estas funções.
"""

from __future__ import annotations

import logging
import threading
import time

_log = logging.getLogger("whis.area_de_transferencia")

#: Quanto esperar entre devolver o foco e mandar o Ctrl+V: a janela precisa
#: estar ativa de fato para a tecla chegar nela.
ESPERA_ANTES_DE_COLAR = 0.12

_TENTATIVAS_DE_ABRIR = 10

_anterior: int | str | None = None


def _no_mac() -> bool:
    import sys

    return sys.platform == "darwin"


def lembrar_janela_anterior() -> None:
    global _anterior
    if _no_mac():
        from .plataforma import app_da_frente_mac

        _anterior = app_da_frente_mac()
        return
    import win32gui

    _anterior = win32gui.GetForegroundWindow() or None


def ler() -> str:
    if _no_mac():
        from .plataforma import ler_clipboard_mac

        return ler_clipboard_mac()
    import win32clipboard
    import win32con

    _abrir()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return ""
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
    finally:
        win32clipboard.CloseClipboard()


def copiar(texto: str) -> None:
    if _no_mac():
        from .plataforma import copiar_clipboard_mac

        copiar_clipboard_mac(texto)
        return
    import win32clipboard
    import win32con

    windows = texto.replace("\r\n", "\n").replace("\n", "\r\n")
    _abrir()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, windows)
    finally:
        win32clipboard.CloseClipboard()


def colar(texto: str) -> None:
    """Copia e, num thread, devolve o foco à janela anterior e manda Ctrl+V."""
    copiar(texto)
    alvo = _anterior
    threading.Thread(target=lambda: _colar_em(alvo), name="whis-colar", daemon=True).start()


def _colar_em(alvo) -> None:
    if _no_mac():
        from .plataforma import colar_no_app_mac

        colar_no_app_mac(alvo)
        return
    import win32api
    import win32con
    import win32gui

    if alvo:
        try:
            win32gui.SetForegroundWindow(alvo)
        except Exception as erro:  # noqa: BLE001 — sem foco, o Ctrl+V vai para quem estiver ativo
            _log.warning("Não consegui devolver o foco à janela anterior: %s", erro)
    time.sleep(ESPERA_ANTES_DE_COLAR)
    tecla_v = ord("V")
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(tecla_v, 0, 0, 0)
    win32api.keybd_event(tecla_v, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def _abrir() -> None:
    """Outro programa pode estar segurando o clipboard por um instante."""
    import pywintypes
    import win32clipboard

    for tentativa in range(_TENTATIVAS_DE_ABRIR):
        try:
            win32clipboard.OpenClipboard()
            return
        except pywintypes.error:
            if tentativa == _TENTATIVAS_DE_ABRIR - 1:
                raise
            time.sleep(0.03)
