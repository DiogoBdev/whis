"""O atalho global de teclado que abre a palheta de qualquer janela.

`RegisterHotKey` do Windows, pelo `pywin32` que já está na venv — sem hook de
teclado: o sistema só avisa quando **aquela** combinação é pressionada, e nada
do que o usuário digita passa por aqui. A `WM_HOTKEY` chega à fila de mensagens
do thread que registrou, e por isso o atalho vive num thread próprio com o seu
próprio `GetMessage`: o `pystray` consome as mensagens do thread dele, e o Tk
as do principal — nenhum dos dois entregaria a nossa (sonda em
`.scratch/interface/research/sonda-palheta.py`).

O que este módulo sabe é analisar a combinação e rodar o laço. Quem registra e
quem lê a fila entram por porta injetada, para o laço ser provado sem tocar no
teclado da máquina de teste.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

_log = logging.getLogger("whis.atalho_global")

#: Os modificadores e as mensagens de `winuser.h`, escritos aqui para o parse
#: não depender do `pywin32` (a configuração o importa em toda máquina).
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
#: Sem ele, segurar a tecla dispara a palheta várias vezes pelo auto-repeat.
MOD_NOREPEAT = 0x4000
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312

#: O identificador que o Windows devolve em `wParam` — só temos um atalho.
ID_DO_ATALHO = 1

import sys as _sys

#: No Mac, Ctrl+Space é o atalho do sistema para trocar o idioma do teclado.
ATALHO_PADRAO = "alt+space" if _sys.platform == "darwin" else "ctrl+space"

_MODIFICADORES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    # nomes do Mac: no macOS "win" e "cmd" são a tecla Command, e "alt" é a Option
    "cmd": MOD_WIN,
    "command": MOD_WIN,
    "option": MOD_ALT,
}

_TECLAS = {
    "space": 0x20,
    "espaco": 0x20,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
    **{letra: ord(letra.upper()) for letra in "abcdefghijklmnopqrstuvwxyz"},
    **{digito: ord(digito) for digito in "0123456789"},
}


class AtalhoInvalido(ValueError):
    """A combinação escrita na configuração não é um atalho que o Windows aceite."""


@dataclass(frozen=True)
class Atalho:
    """Uma combinação já traduzida para o que `RegisterHotKey` espera."""

    modificadores: int
    tecla: int
    texto: str


def analisar_atalho(texto: str) -> Atalho:
    """`"ctrl+alt+space"` → `Atalho`. Maiúsculas e espaços não importam.

    Exige pelo menos um modificador: uma tecla solta como atalho global
    engoliria aquela letra em todo programa da máquina.
    """
    partes = [parte.strip().casefold() for parte in texto.split("+")]
    if not texto.strip() or any(not parte for parte in partes):
        raise AtalhoInvalido("o atalho veio vazio ou com um '+' sobrando")

    *modificadores, ultima = partes
    combinados = 0
    for parte in modificadores:
        if parte not in _MODIFICADORES:
            raise AtalhoInvalido(
                f"'{parte}' não é modificador; antes da tecla só valem "
                f"{', '.join(sorted(set(_MODIFICADORES)))}"
            )
        if combinados & _MODIFICADORES[parte]:
            raise AtalhoInvalido(f"'{parte}' aparece duas vezes")
        combinados |= _MODIFICADORES[parte]

    if ultima in _MODIFICADORES:
        raise AtalhoInvalido("o atalho precisa terminar numa tecla, não num modificador")
    if ultima not in _TECLAS:
        raise AtalhoInvalido(
            f"'{ultima}' não é uma tecla conhecida; valem letra, dígito, space e f1..f12"
        )
    if not combinados:
        raise AtalhoInvalido("o atalho precisa de pelo menos um modificador (ctrl, alt, shift, win)")

    return Atalho(modificadores=combinados, tecla=_TECLAS[ultima], texto="+".join(partes))


#: `registrar(id, modificadores, tecla) -> registrou?` — o adaptador real
#: traduz a exceção do `pywin32` em `False`.
Registrador = Callable[[int, int, int], object]
#: `proxima_mensagem() -> (mensagem, wparam)`, ou `None` quando a fila fechou.
LeitorDeMensagens = Callable[[], tuple[int, int] | None]


class AtalhoGlobal:
    """O thread que registra a combinação e chama `ao_disparar` a cada tecla.

    `iniciar()` só devolve depois que o registro aconteceu (ou falhou), para a
    bandeja saber na hora se o atalho está tomado por outro programa.
    `ao_disparar` roda **no thread do atalho**: quem precisa do Tk enfileira.
    """

    def __init__(
        self,
        atalho: Atalho,
        ao_disparar: Callable[[], None],
        *,
        registrar: Registrador | None = None,
        desregistrar: Callable[[int], None] | None = None,
        proxima_mensagem: LeitorDeMensagens | None = None,
        id_do_thread: Callable[[], int] | None = None,
        encerrar_thread: Callable[[int], None] | None = None,
    ) -> None:
        self.atalho = atalho
        self._ao_disparar = ao_disparar
        self._registrar = registrar or _registrar_no_windows
        self._desregistrar = desregistrar or _desregistrar_no_windows
        self._proxima_mensagem = proxima_mensagem or _proxima_mensagem_do_windows
        self._id_do_thread = id_do_thread or _id_do_thread_atual
        self._encerrar_thread = encerrar_thread or _encerrar_thread_do_windows
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._pronto = threading.Event()
        self.registrado = False
        self.disparos = 0

    def iniciar(self, *, espera_s: float = 5.0) -> bool:
        """Sobe o thread e devolve se o Windows aceitou a combinação."""
        self._thread = threading.Thread(target=self._laco, name="whis-atalho", daemon=True)
        self._thread.start()
        self._pronto.wait(espera_s)
        return self.registrado

    def parar(self, *, espera_s: float = 3.0) -> None:
        """Encerra o laço (`WM_QUIT` no thread dele) e desregistra a combinação."""
        if self.registrado and self._tid is not None and self._thread is not None and self._thread.is_alive():
            try:
                self._encerrar_thread(self._tid)
            except Exception as erro:  # noqa: BLE001 — o processo está saindo
                _log.debug("Não foi possível avisar o thread do atalho: %s", erro)
        if self._thread is not None:
            self._thread.join(espera_s)

    def _laco(self) -> None:
        self._tid = self._id_do_thread()
        try:
            self.registrado = bool(
                self._registrar(ID_DO_ATALHO, self.atalho.modificadores | MOD_NOREPEAT, self.atalho.tecla)
            )
        except Exception as erro:  # noqa: BLE001 — falhar aqui não pode derrubar a bandeja
            _log.error("Não foi possível registrar o atalho %s: %s", self.atalho.texto, erro)
            self.registrado = False
        self._pronto.set()
        if not self.registrado:
            _log.warning("O atalho %s não foi registrado — a palheta abre só pelo menu.", self.atalho.texto)
            return
        _log.info("Atalho global %s registrado.", self.atalho.texto)

        try:
            while True:
                mensagem = self._proxima_mensagem()
                if mensagem is None:
                    break
                tipo, wparam = mensagem
                if tipo == WM_QUIT:
                    break
                if tipo == WM_HOTKEY and wparam == ID_DO_ATALHO:
                    self.disparos += 1
                    try:
                        self._ao_disparar()
                    except Exception:  # noqa: BLE001 — um disparo com erro não mata o atalho
                        _log.exception("O atalho disparou, mas abrir a palheta falhou.")
        finally:
            try:
                self._desregistrar(ID_DO_ATALHO)
                _log.info("Atalho global %s desregistrado.", self.atalho.texto)
            except Exception as erro:  # noqa: BLE001
                _log.debug("Erro ao desregistrar o atalho: %s", erro)


# -- os adaptadores do Windows ---------------------------------------------


def _registrar_no_windows(id_: int, modificadores: int, tecla: int) -> bool:
    import pywintypes
    import win32gui

    try:
        win32gui.RegisterHotKey(None, id_, modificadores, tecla)
    except pywintypes.error as erro:
        _log.warning("RegisterHotKey recusou a combinação: %s", erro)
        return False
    return True


def _desregistrar_no_windows(id_: int) -> None:
    import win32gui

    win32gui.UnregisterHotKey(None, id_)


def _proxima_mensagem_do_windows() -> tuple[int, int] | None:
    """`GetMessage` do thread do atalho: `(mensagem, wParam)`, ou `None` no `WM_QUIT`."""
    import win32gui

    retorno, mensagem = win32gui.GetMessage(None, 0, 0)
    if retorno == 0:
        return None
    _hwnd, tipo, wparam, _lparam, _tempo, _ponto = mensagem
    return tipo, wparam


def _id_do_thread_atual() -> int:
    import win32api

    return win32api.GetCurrentThreadId()


def _encerrar_thread_do_windows(tid: int) -> None:
    import win32api

    win32api.PostThreadMessage(tid, WM_QUIT, 0, 0)


# -- o macOS ---------------------------------------------------------------
#
# No Mac o atalho é o `RegisterEventHotKey` do Carbon, por `ctypes`: como o
# `RegisterHotKey` do Windows, o sistema só avisa da combinação registrada e
# não pede a permissão de Acessibilidade. O evento chega ao laço de eventos do
# aplicativo — o `mainloop` do Tk no thread principal —, então `iniciar` tem de
# ser chamado no thread principal, antes do `mainloop`.

_CMD_KEY = 0x0100
_SHIFT_KEY = 0x0200
_OPTION_KEY = 0x0800
_CONTROL_KEY = 0x1000

#: Os códigos virtuais do teclado ANSI (`Events.h`, `kVK_*`).
_TECLAS_DO_MAC = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07,
    "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10,
    "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "9": 0x19,
    "7": 0x1A, "8": 0x1C, "0": 0x1D, "o": 0x1F, "u": 0x20, "i": 0x22, "p": 0x23, "l": 0x25,
    "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E, "space": 0x31, "espaco": 0x31,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62,
    "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
}


def _para_o_mac(atalho: Atalho) -> tuple[int, int]:
    """`(modificadores do Carbon, código virtual)` a partir do `Atalho` analisado."""
    modificadores = 0
    if atalho.modificadores & MOD_CONTROL:
        modificadores |= _CONTROL_KEY
    if atalho.modificadores & MOD_ALT:
        modificadores |= _OPTION_KEY
    if atalho.modificadores & MOD_SHIFT:
        modificadores |= _SHIFT_KEY
    if atalho.modificadores & MOD_WIN:
        modificadores |= _CMD_KEY
    return modificadores, _TECLAS_DO_MAC[atalho.texto.rsplit("+", 1)[-1]]


class AtalhoGlobalDoMac:
    """O mesmo contrato do `AtalhoGlobal` (`iniciar`, `parar`, `registrado`), pelo Carbon."""

    def __init__(self, atalho: Atalho, ao_disparar: Callable[[], None]) -> None:
        self.atalho = atalho
        self._ao_disparar = ao_disparar
        self.registrado = False
        self.disparos = 0
        self._referencia = None
        self._tratador = None  # segura o callback do ctypes vivo

    def iniciar(self, *, espera_s: float = 5.0) -> bool:  # noqa: ARG002 — mesmo contrato do Windows
        import ctypes

        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")

        class EventTypeSpec(ctypes.Structure):
            _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

        class EventHotKeyID(ctypes.Structure):
            _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

        Tratador = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

        def tratar(_chamada, _evento, _dados) -> int:
            self.disparos += 1
            try:
                self._ao_disparar()
            except Exception:  # noqa: BLE001 — um disparo com erro não mata o atalho
                _log.exception("O atalho disparou, mas abrir a palheta falhou.")
            return 0

        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p, Tratador, ctypes.c_uint32, ctypes.POINTER(EventTypeSpec),
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]

        alvo = carbon.GetApplicationEventTarget()
        self._tratador = Tratador(tratar)
        tipo = EventTypeSpec(int.from_bytes(b"keyb", "big"), 5)  # kEventHotKeyPressed
        manipulador = ctypes.c_void_p()
        estado = carbon.InstallEventHandler(alvo, self._tratador, 1, ctypes.byref(tipo), None, ctypes.byref(manipulador))
        if estado != 0:
            _log.error("InstallEventHandler recusou (OSStatus %d).", estado)
            return False

        modificadores, tecla = _para_o_mac(self.atalho)
        referencia = ctypes.c_void_p()
        estado = carbon.RegisterEventHotKey(
            tecla, modificadores, EventHotKeyID(int.from_bytes(b"whis", "big"), ID_DO_ATALHO),
            alvo, 0, ctypes.byref(referencia),
        )
        if estado != 0:
            _log.warning("RegisterEventHotKey recusou %s (OSStatus %d).", self.atalho.texto, estado)
            return False
        self._carbon = carbon
        self._referencia = referencia
        self.registrado = True
        _log.info("Atalho global %s registrado (Carbon).", self.atalho.texto)
        return True

    def parar(self, *, espera_s: float = 3.0) -> None:  # noqa: ARG002
        if self._referencia is not None:
            try:
                self._carbon.UnregisterEventHotKey(self._referencia)
            except Exception as erro:  # noqa: BLE001 — o processo está saindo
                _log.debug("Erro ao desregistrar o atalho: %s", erro)
            self._referencia = None
            self.registrado = False


def criar_atalho_global(atalho: Atalho, ao_disparar: Callable[[], None]):
    """O `AtalhoGlobal` do Windows ou o `AtalhoGlobalDoMac`, conforme o sistema."""
    import sys

    if sys.platform == "darwin":
        return AtalhoGlobalDoMac(atalho, ao_disparar)
    return AtalhoGlobal(atalho, ao_disparar)
