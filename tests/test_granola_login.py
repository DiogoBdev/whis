"""A guarda do login da Granola: DPAPI de verdade, renovação por HTTP falso e a vez de cada processo.

O DPAPI é o do Windows de quem roda a suíte — é ele que se quer provar. A
renovação fala com um `httpx.MockTransport`; nada sai para a Granola.
"""

from __future__ import annotations

import sys

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from whis.configuracao import carregar
from whis.granola_login import (
    MARGEM_S,
    NOME_DO_ARQUIVO,
    GuardaDoLogin,
    pasta_do_login,
    vez_da_granola,
)
from whis.portas import ErroDaGranola

AGORA = 1_000_000.0
CLIENTE = OAuthClientInformationFull.model_validate(
    {"client_id": "c1", "redirect_uris": ["http://127.0.0.1:33418/callback"]}
)
TOKEN = OAuthToken(access_token="segredo-de-acesso", expires_in=21600, refresh_token="r1")


class Granola:
    """Os três endereços que a renovação visita, e o que o POST recebeu."""

    def __init__(self, *, status: int = 200, corpo: dict | None = None, sem_rede: bool = False) -> None:
        self.status = status
        self.corpo = corpo if corpo is not None else {
            "access_token": "novo-acesso",
            "token_type": "Bearer",
            "expires_in": 21600,
            "refresh_token": "r2",
        }
        self.sem_rede = sem_rede
        self.formularios: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.sem_rede:
            raise httpx.ConnectError("sem rede", request=request)
        url = str(request.url)
        if url == "https://mcp.granola.ai/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"authorization_servers": ["https://mcp-auth.granola.ai"]})
        if url == "https://mcp-auth.granola.ai/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"token_endpoint": "https://mcp-auth.granola.ai/oauth2/token"})
        if url == "https://mcp-auth.granola.ai/oauth2/token":
            self.formularios.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(self.status, json=self.corpo)
        return httpx.Response(404)


def guarda(pasta: Path, *, agora: float = AGORA, granola: Granola | None = None) -> GuardaDoLogin:
    transporte = httpx.MockTransport(granola) if granola is not None else None
    return GuardaDoLogin(pasta, agora=lambda: agora, transporte=transporte)


def logado(pasta: Path) -> None:
    primeira = guarda(pasta)
    asyncio.run(primeira.set_client_info(CLIENTE))
    asyncio.run(primeira.set_tokens(TOKEN))


VENCIDO = AGORA + TOKEN.expires_in - MARGEM_S + 1


def test_ida_e_volta_pelo_dpapi(tmp_path: Path):
    logado(tmp_path)

    outra = guarda(tmp_path)

    assert asyncio.run(outra.get_client_info()).client_id == "c1"
    assert asyncio.run(outra.get_tokens()).access_token == "segredo-de-acesso"


def test_o_arquivo_nao_tem_o_token_em_claro(tmp_path: Path):
    logado(tmp_path)

    bruto = (tmp_path / NOME_DO_ARQUIVO).read_bytes()

    assert b"segredo-de-acesso" not in bruto and b"r1" not in bruto


def test_token_valido_nao_renova(tmp_path: Path):
    logado(tmp_path)
    granola = Granola()

    tokens = asyncio.run(guarda(tmp_path, agora=VENCIDO - 2, granola=granola).get_tokens())

    assert tokens.access_token == "segredo-de-acesso"
    assert granola.formularios == []


def test_token_vencido_renova_e_grava_o_refresh_novo(tmp_path: Path):
    logado(tmp_path)
    granola = Granola()

    tokens = asyncio.run(guarda(tmp_path, agora=VENCIDO, granola=granola).get_tokens())

    assert tokens.access_token == "novo-acesso"
    assert granola.formularios == [
        {
            "grant_type": "refresh_token",
            "refresh_token": "r1",
            "client_id": "c1",
            "resource": "https://mcp.granola.ai/mcp",
        }
    ]
    relida = asyncio.run(guarda(tmp_path, agora=VENCIDO).get_tokens())
    assert relida.access_token == "novo-acesso" and relida.refresh_token == "r2"


def test_resposta_sem_refresh_mantem_o_anterior(tmp_path: Path):
    logado(tmp_path)
    granola = Granola(corpo={"access_token": "novo-acesso", "token_type": "Bearer", "expires_in": 21600})

    asyncio.run(guarda(tmp_path, agora=VENCIDO, granola=granola).get_tokens())

    assert asyncio.run(guarda(tmp_path, agora=VENCIDO).get_tokens()).refresh_token == "r1"


@pytest.mark.parametrize("status", [400, 401])
def test_renovacao_recusada_apaga_tokens_e_mantem_o_cliente(tmp_path: Path, status: int):
    logado(tmp_path)
    granola = Granola(status=status, corpo={"error": "invalid_grant"})

    assert asyncio.run(guarda(tmp_path, agora=VENCIDO, granola=granola).get_tokens()) is None

    depois = guarda(tmp_path, agora=AGORA)
    assert asyncio.run(depois.get_tokens()) is None
    assert asyncio.run(depois.get_client_info()).client_id == "c1"


@pytest.mark.parametrize("granola", [Granola(sem_rede=True), Granola(status=503, corpo={})])
def test_renovacao_sem_rede_levanta_e_nao_apaga(tmp_path: Path, granola: Granola):
    logado(tmp_path)

    with pytest.raises(ErroDaGranola):
        asyncio.run(guarda(tmp_path, agora=VENCIDO, granola=granola).get_tokens())

    assert asyncio.run(guarda(tmp_path, agora=AGORA).get_tokens()).refresh_token == "r1"


def test_vencido_sem_refresh_e_sem_login(tmp_path: Path):
    primeira = guarda(tmp_path)
    asyncio.run(primeira.set_client_info(CLIENTE))
    asyncio.run(primeira.set_tokens(OAuthToken(access_token="a", expires_in=3600)))

    assert asyncio.run(guarda(tmp_path, agora=AGORA + 3600).get_tokens()) is None


def test_arquivo_ilegivel_e_sem_login(tmp_path: Path):
    (tmp_path / NOME_DO_ARQUIVO).write_bytes(b"isto nao foi cifrado pelo DPAPI")

    leitora = guarda(tmp_path)

    assert asyncio.run(leitora.get_tokens()) is None
    assert asyncio.run(leitora.get_client_info()) is None


def test_o_arquivo_e_trocado_inteiro_sem_sobra(tmp_path: Path):
    logado(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == [NOME_DO_ARQUIVO]


def test_a_segunda_vez_espera_e_desiste(tmp_path: Path):
    with vez_da_granola(tmp_path):
        with pytest.raises(ErroDaGranola, match="Outra puxada"):
            with vez_da_granola(tmp_path, espera_s=0.3):
                pass

    with vez_da_granola(tmp_path, espera_s=0.3):
        pass


def test_o_login_mora_ao_lado_da_configuracao(arquivo_de_configuracao: Path):
    assert pasta_do_login(carregar(arquivo_de_configuracao)) == arquivo_de_configuracao.parent


@pytest.mark.skipif(sys.platform != "win32", reason="decifra com a DPAPI do Windows")
def test_o_conteudo_decifrado_e_o_json_esperado(tmp_path: Path):
    logado(tmp_path)

    import win32crypt

    dados = json.loads(win32crypt.CryptUnprotectData((tmp_path / NOME_DO_ARQUIVO).read_bytes(), None, None, None, 0)[1])

    assert set(dados) == {"cliente", "tokens", "expira_em"}
    assert dados["expira_em"] == AGORA + TOKEN.expires_in - MARGEM_S
