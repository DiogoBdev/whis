"""O login da Granola: tokens guardados com DPAPI, renovados antes do SDK, e a vez de cada processo.

O SDK `mcp` carrega o token guardado sem a hora em que ele vence e o trata
como válido; vencido, o servidor responde 401 e o SDK refaz o login inteiro
pelo navegador, sem tentar o `refresh_token` (`mcp/client/auth/oauth2.py`,
`_initialize` e `async_auth_flow`, versão 2.0.0). Por isso a guarda renova
sozinha quando `expira_em` passou, e só então entrega o token.

O `refresh_token` da Granola muda a cada uso: a tarefa agendada e a palheta
renovando juntas invalidariam uma à outra. `vez_da_granola` faz cada processo
esperar a sua vez.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .configuracao import Configuracao
from .instancia import OutraInstancia, Trava
from .portas import ErroDaGranola

_log = logging.getLogger("whis.granola")

NOME_DO_ARQUIVO = "granola.json"
NOME_DA_TRAVA = "granola.lock"
URL_DO_MCP = "https://mcp.granola.ai/mcp"
URL_DOS_METADADOS = "https://mcp.granola.ai/.well-known/oauth-protected-resource"

#: Folga para não entregar ao SDK um token que vence no meio da puxada.
MARGEM_S = 300

ESPERA_PELA_VEZ_S = 120.0


def pasta_do_login(configuracao: Configuracao) -> Path:
    """Ao lado da configuração, como o log e a trava da bandeja."""
    return configuracao.origem.parent if configuracao.origem else Path.home()


def _cifrar(dados: bytes) -> bytes:
    """DPAPI no Windows; Fernet com a chave no Keychain no macOS (`plataforma`)."""
    from .plataforma import cifrar

    return cifrar(dados, "Whis Granola")


def _decifrar(dados: bytes) -> bytes:
    from .plataforma import decifrar

    return decifrar(dados, "Whis Granola")


class GuardaDoLogin:
    """O `TokenStorage` do SDK `mcp` sobre `granola.json`, cifrado com DPAPI.

    Só o usuário do Windows que gravou o arquivo consegue abri-lo. Guarda o
    cliente registrado (DCR), os tokens e `expira_em`; um arquivo que não abre
    conta como "sem login", e o próximo login o regrava.
    """

    def __init__(
        self,
        pasta: Path | str,
        *,
        agora: Callable[[], float] = time.time,
        transporte: httpx.AsyncBaseTransport | None = None,
        cifrar: Callable[[bytes], bytes] = _cifrar,
        decifrar: Callable[[bytes], bytes] = _decifrar,
    ) -> None:
        self.pasta = Path(pasta)
        self.arquivo = self.pasta / NOME_DO_ARQUIVO
        self._agora = agora
        self._transporte = transporte
        self._cifrar = cifrar
        self._decifrar = decifrar

    async def get_tokens(self) -> OAuthToken | None:
        dados = self._ler()
        if not dados.get("tokens"):
            return None
        tokens = OAuthToken.model_validate(dados["tokens"])
        expira_em = dados.get("expira_em")
        if expira_em is None or self._agora() < expira_em:
            return tokens
        cliente = dados.get("cliente") or {}
        if not tokens.refresh_token or not cliente.get("client_id"):
            return None
        novos = await self._renovar(tokens.refresh_token, cliente["client_id"], cliente.get("client_secret"))
        if novos is None:
            dados["tokens"] = None
            dados["expira_em"] = None
            self._gravar(dados)
            return None
        return self._guardar_tokens(dados, novos)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._guardar_tokens(self._ler(), tokens)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        cliente = self._ler().get("cliente")
        return OAuthClientInformationFull.model_validate(cliente) if cliente else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        dados = self._ler()
        dados["cliente"] = client_info.model_dump(mode="json", exclude_none=True)
        self._gravar(dados)

    def _guardar_tokens(self, dados: dict, tokens: OAuthToken) -> OAuthToken:
        anterior = dados.get("tokens") or {}
        if not tokens.refresh_token and anterior.get("refresh_token"):
            tokens = tokens.model_copy(update={"refresh_token": anterior["refresh_token"]})
        dados["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        dados["expira_em"] = (
            self._agora() + tokens.expires_in - MARGEM_S if tokens.expires_in else None
        )
        self._gravar(dados)
        return tokens

    async def _renovar(
        self, refresh_token: str, client_id: str, client_secret: str | None
    ) -> OAuthToken | None:
        """O token novo; `None` quando a Granola recusa (é preciso entrar de novo)."""
        formulario = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "resource": URL_DO_MCP,
        }
        if client_secret:
            formulario["client_secret"] = client_secret
        try:
            async with httpx.AsyncClient(transport=self._transporte, timeout=30) as http:
                endpoint = await _endpoint_do_token(http)
                resposta = await http.post(endpoint, data=formulario)
        except httpx.HTTPError as erro:
            raise ErroDaGranola(f"Sem resposta da Granola ao renovar o login: {erro}") from erro
        if resposta.status_code in (400, 401):
            _log.warning(
                "A Granola recusou renovar o login (HTTP %d); será preciso entrar de novo.",
                resposta.status_code,
            )
            return None
        if resposta.status_code != 200:
            raise ErroDaGranola(f"A Granola respondeu HTTP {resposta.status_code} ao renovar o login.")
        _log.info("Login da Granola renovado.")
        return OAuthToken.model_validate(resposta.json())

    def _ler(self) -> dict:
        if not self.arquivo.is_file():
            return {}
        try:
            dados = json.loads(self._decifrar(self.arquivo.read_bytes()).decode("utf-8"))
        except Exception as erro:  # noqa: BLE001 — de outro usuário, de outra máquina ou corrompido
            _log.warning(
                "O login guardado da Granola não abriu (%s); será preciso entrar de novo.",
                type(erro).__name__,
            )
            return {}
        return dados if isinstance(dados, dict) else {}

    def _gravar(self, dados: dict) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        temporario = self.arquivo.with_name(self.arquivo.name + ".tmp")
        temporario.write_bytes(self._cifrar(json.dumps(dados).encode("utf-8")))
        os.replace(temporario, self.arquivo)


async def _endpoint_do_token(http: httpx.AsyncClient) -> str:
    """O `token_endpoint`, descoberto pelos metadados públicos do MCP e do servidor de login."""
    try:
        recurso = await http.get(URL_DOS_METADADOS)
        recurso.raise_for_status()
        servidor = recurso.json()["authorization_servers"][0].rstrip("/")
        metadados = await http.get(f"{servidor}/.well-known/oauth-authorization-server")
        metadados.raise_for_status()
        return metadados.json()["token_endpoint"]
    except (httpx.HTTPStatusError, KeyError, IndexError, ValueError) as erro:
        raise ErroDaGranola(f"Os metadados de login da Granola não vieram como esperado: {erro}") from erro


@contextmanager
def vez_da_granola(
    pasta: Path | str,
    *,
    espera_s: float = ESPERA_PELA_VEZ_S,
    dormir: Callable[[float], None] = time.sleep,
) -> Iterator[None]:
    """Só um processo por vez fala com a Granola; os outros esperam até `espera_s`."""
    trava = Trava(Path(pasta) / NOME_DA_TRAVA)
    limite = time.monotonic() + espera_s
    while True:
        try:
            trava.adquirir()
            break
        except OutraInstancia:
            if time.monotonic() >= limite:
                raise ErroDaGranola(
                    "Outra puxada da Granola está em andamento; tente de novo em instantes."
                ) from None
            dormir(0.25)
    try:
        yield
    finally:
        trava.liberar()
