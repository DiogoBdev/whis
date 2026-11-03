r"""O cofre: segredos que o Whis cria por ele — senhas das contas novas, o token do LinkedIn.

Um JSON cifrado com a DPAPI do Windows (`CryptProtectData`, do mesmo jeito que
o login da Granola em `granola.json`): só o usuário do Windows que gravou
consegue ler, e nada vai em claro para o disco nem para o log. Mora ao lado do
`config.toml`, em `%APPDATA%\whis\`. A troca é inteira (`.tmp` + `os.replace`).

Fora do Windows (desenvolvimento) não existe DPAPI: quem testa injeta
`cifrar`/`decifrar`.
"""

from __future__ import annotations

import json
import os
import secrets
import string
from collections.abc import Callable
from pathlib import Path
from typing import Any

Transformar = Callable[[bytes], bytes]


def _cifrar_dpapi(dados: bytes) -> bytes:
    """DPAPI no Windows; Fernet com a chave no Keychain no macOS (`plataforma`)."""
    from .plataforma import cifrar

    return cifrar(dados, "Whis cofre")


def _decifrar_dpapi(dados: bytes) -> bytes:
    from .plataforma import decifrar

    return decifrar(dados, "Whis cofre")


class Cofre:
    """Um dicionário de segredos por chave (`"conta:workana"`, `"linkedin"`), cifrado em disco."""

    def __init__(self, arquivo: Path, *, cifrar: Transformar | None = None, decifrar: Transformar | None = None) -> None:
        self.arquivo = Path(arquivo)
        self._cifrar = cifrar or _cifrar_dpapi
        self._decifrar = decifrar or _decifrar_dpapi

    def ler(self) -> dict[str, Any]:
        if not self.arquivo.is_file():
            return {}
        dados = json.loads(self._decifrar(self.arquivo.read_bytes()).decode("utf-8"))
        return dados if isinstance(dados, dict) else {}

    def obter(self, chave: str) -> Any:
        return self.ler().get(chave)

    def guardar(self, chave: str, valor: Any) -> None:
        dados = self.ler()
        dados[chave] = valor
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.arquivo.with_name(self.arquivo.name + ".tmp")
        temporario.write_bytes(self._cifrar(json.dumps(dados, ensure_ascii=False).encode("utf-8")))
        os.replace(temporario, self.arquivo)


def cofre_padrao(configuracao) -> Cofre:
    base = configuracao.origem.parent if getattr(configuracao, "origem", None) else Path.home()
    return Cofre(base / "cofre.bin")


def senha_nova(tamanho: int = 20) -> str:
    """Uma senha forte que satisfaz as regras comuns: maiúscula, minúscula, dígito e símbolo."""
    simbolos = "!@#$%*-_+?"
    todos = string.ascii_letters + string.digits + simbolos
    while True:
        senha = "".join(secrets.choice(todos) for _ in range(tamanho))
        if (
            any(c.islower() for c in senha)
            and any(c.isupper() for c in senha)
            and any(c.isdigit() for c in senha)
            and any(c in simbolos for c in senha)
        ):
            return senha
