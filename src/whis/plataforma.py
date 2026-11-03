r"""O que muda entre o Windows e o macOS: pastas, cofre, aviso, clipboard, Chrome.

O Whis nasceu no Windows (`%APPDATA%`, DPAPI, toast, `schtasks`). No macOS as
mesmas peças viram:

- configuração (o `%APPDATA%\whis`): `~/Library/Application Support/whis`;
- estado local (o `%LOCALAPPDATA%\whis`): `~/Library/Application Support/whis/local`;
- cofre (DPAPI): Fernet com a chave guardada no Keychain do usuário;
- toast: `terminal-notifier` se existir (com botão que abre a URI), senão `osascript`;
- clipboard: `pbcopy`/`pbpaste`, e o colar é um Cmd+V pelo System Events;
- Chrome: `/Applications/Google Chrome.app`.

Quem precisa decidir pelo sistema pergunta aqui, e não a `sys.platform` solto.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger("whis.plataforma")

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

_SUPORTE_MAC = Path.home() / "Library" / "Application Support"


def pasta_de_config() -> Path:
    r"""A base de `whis/config.toml`: `%APPDATA%` no Windows, Application Support no Mac.

    `APPDATA` definida vence em qualquer sistema — é como a suíte isola a máquina.
    """
    appdata = os.environ.get("APPDATA")
    if MACOS and not appdata:
        return _SUPORTE_MAC
    return Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"


def pasta_local_do_whis() -> Path:
    r"""O estado fora do vault: `%LOCALAPPDATA%\whis` no Windows; `Application Support/whis/local` no Mac."""
    base = os.environ.get("LOCALAPPDATA")
    if MACOS and not base:
        return _SUPORTE_MAC / "whis" / "local"
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "whis"


# --- cofre -------------------------------------------------------------------

_SERVICO_DO_KEYCHAIN = "whis"


def _chave_do_keychain(conta: str) -> bytes:
    """A chave Fernet de `conta` no Keychain; criada na primeira vez."""
    from cryptography.fernet import Fernet

    lido = subprocess.run(
        ["security", "find-generic-password", "-s", _SERVICO_DO_KEYCHAIN, "-a", conta, "-w"],
        capture_output=True,
        text=True,
    )
    if lido.returncode == 0 and lido.stdout.strip():
        return lido.stdout.strip().encode("ascii")
    chave = Fernet.generate_key()
    gravado = subprocess.run(
        [
            "security", "add-generic-password", "-U",
            "-s", _SERVICO_DO_KEYCHAIN, "-a", conta, "-w", chave.decode("ascii"),
        ],
        capture_output=True,
        text=True,
    )
    if gravado.returncode != 0:
        raise RuntimeError(f"Não consegui gravar a chave do Whis no Keychain: {gravado.stderr.strip()}")
    return chave


def cifrar(dados: bytes, descricao: str) -> bytes:
    """Cifra para o usuário atual: DPAPI no Windows, Fernet + Keychain no Mac."""
    if WINDOWS:
        import win32crypt

        return win32crypt.CryptProtectData(dados, descricao, None, None, None, 0)
    from cryptography.fernet import Fernet

    return Fernet(_chave_do_keychain(descricao)).encrypt(dados)


def decifrar(dados: bytes, descricao: str) -> bytes:
    if WINDOWS:
        import win32crypt

        return win32crypt.CryptUnprotectData(dados, None, None, None, 0)[1]
    from cryptography.fernet import Fernet

    return Fernet(_chave_do_keychain(descricao)).decrypt(dados)


# --- aviso -------------------------------------------------------------------


def _aspas_applescript(texto: str) -> str:
    return '"' + texto.replace("\\", "\\\\").replace('"', '\\"') + '"'


def comando_de_aviso_mac(titulo: str, texto: str, uri: str | None) -> list[str]:
    """A notificação do macOS: `terminal-notifier` abre a URI no clique; `osascript` não."""
    notifier = shutil.which("terminal-notifier")
    if notifier:
        comando = [notifier, "-title", "Whis", "-subtitle", titulo, "-message", texto, "-group", "whis"]
        if uri:
            comando += ["-open", uri]
        return comando
    script = f"display notification {_aspas_applescript(texto)} with title \"Whis\" subtitle {_aspas_applescript(titulo)}"
    return ["osascript", "-e", script]


# --- clipboard ---------------------------------------------------------------


def ler_clipboard_mac() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def copiar_clipboard_mac(texto: str) -> None:
    subprocess.run(["pbcopy"], input=texto, text=True, check=True)


def app_da_frente_mac() -> str | None:
    """O nome do app em primeiro plano (para devolver o foco depois da palheta)."""
    r = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() or None


def colar_no_app_mac(app: str | None) -> None:
    """Ativa `app` e manda Cmd+V (precisa de permissão de Acessibilidade)."""
    linhas = []
    if app:
        linhas.append(f"tell application {_aspas_applescript(app)} to activate")
        linhas.append("delay 0.12")
    linhas.append('tell application "System Events" to keystroke "v" using command down')
    comando = ["osascript"]
    for linha in linhas:
        comando += ["-e", linha]
    subprocess.run(comando, capture_output=True, text=True)


# --- abrir arquivo / URI -----------------------------------------------------


def comando_de_abrir(alvo: str) -> list[str]:
    """`open` no Mac, `xdg-open` no Linux (o Windows usa `os.startfile`)."""
    return ["open", alvo] if MACOS else ["xdg-open", alvo]


# --- Chrome ------------------------------------------------------------------


def candidatos_do_chrome() -> list[Path]:
    if MACOS:
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        ]
    bases = (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA"))
    return [Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe" for base in bases if base]
