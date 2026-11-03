"""Os três adaptadores do Claude no jornal, sem tocar a rede.

A única parte que fala com o Claude é `_mensagens`; aqui ela devolve mensagens
gravadas no formato que o SDK entrega — texto, uso de ferramenta, resultado de
ferramenta, `RateLimitEvent` e `ResultMessage` com `structured_output`. Os
testes marcados `rede` falam com o Claude de verdade e só rodam quando pedidos.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from whis.jornal_claude import (
    AMBIENTE_DA_PUBLICACAO,
    ESQUEMA_DA_FORMACAO,
    esquema_da_formacao,
    SISTEMA_ESCOLHA,
    SISTEMA_FORMACAO,
    EditorClaude,
    FormacaoClaude,
    PublicacaoClaude,
    ambiente_do_cli,
    esquema_da_escolha,
    extrair_url,
    interpretar_escolha,
    interpretar_formacao,
    ler_janelas,
    montar_pedido_de_escolha,
    montar_pedido_de_formacao,
    pedido_de_primeira_publicacao,
    pedido_de_republicacao,
)
from whis.portas import (
    Candidato,
    ErroDeEditor,
    ErroDeFormacao,
    ErroDePublicacao,
    Janelas,
    PortaEditor,
    PortaFormacao,
    PortaPublicacao,
)
from whis.claude_cli import VARIAVEIS_DE_API_PAGA, caminho_do_cli

DIA = date(2026, 9, 25)
URL = "https://claude.ai/artifact/AYhYVZDhevQdWv4YaNuoge"


def _candidato(id: str, secao: str, fonte: str, titulo: str = "", resumo: str = "") -> Candidato:
    return Candidato(
        id=id,
        secao=secao,
        fonte=fonte,
        titulo=titulo or f"Título do feed {id}",
        link=f"https://exemplo.com/{id}",
        resumo=resumo or f"Resumo do feed {id}.",
    )


CANDIDATOS = {
    "tecnologia": (
        _candidato("tecnologia-1", "tecnologia", "Ars Technica"),
        _candidato("tecnologia-2", "tecnologia", "The Verge"),
        _candidato("tecnologia-3", "tecnologia", "Ars Technica"),
    ),
    "carros": (
        _candidato("carros-1", "carros", "Carscoops"),
        _candidato("carros-2", "carros", "Autoesporte"),
    ),
}
LIMITES = {"tecnologia": (1, 3), "carros": (1, 2)}
TIME = (
    _candidato("time-1", "time", "Corinthians"),
    _candidato("time-2", "time", "Corinthians"),
    _candidato("time-3", "time", "Corinthians"),
)


def _item(id: str, titulo: str = "Título em português", resumo: str = "Duas frases. Em português.") -> dict:
    return {"id": id, "titulo": titulo, "resumo": resumo}


def _ids(itens) -> list[str]:
    return [item.candidato.id for item in itens]


def _resultado(**extras) -> ResultMessage:
    campos = {
        "subtype": "success",
        "duration_ms": 9800,
        "duration_api_ms": 9000,
        "is_error": False,
        "num_turns": 2,
        "session_id": "sessao-de-teste",
        "total_cost_usd": 0.4012,
        "usage": {"input_tokens": 5100, "output_tokens": 900, "cache_read_input_tokens": 300},
    }
    return ResultMessage(**{**campos, **extras})


def _limite(raw: dict) -> RateLimitEvent:
    return RateLimitEvent(
        rate_limit_info=RateLimitInfo(status="allowed", raw=raw),
        uuid="u",
        session_id="sessao-de-teste",
    )


def _janelas(cinco: float, sete: float) -> RateLimitEvent:
    return _limite(
        {
            "status": "allowed",
            "unifiedWindows": {
                "five_hour": {"utilization": cinco, "resetsAt": 1790308200},
                "seven_day": {"utilization": sete, "resetsAt": 1790420400},
            },
        }
    )


def _texto(texto: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=texto)], model="claude-haiku-4-5-20251001")


def _uso(nome: str, entrada: dict, id: str = "t1") -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id=id, name=nome, input=entrada)], model="claude-haiku-4-5-20251001"
    )


def _resposta_da_ferramenta(conteudo: str, id: str = "t1") -> UserMessage:
    return UserMessage(content=[ToolResultBlock(tool_use_id=id, content=conteudo)])


class _Gravado:
    """Troca a rede por mensagens gravadas e guarda o prompt e as opções de cada chamada."""

    def __init__(self, mensagens: list, **extras) -> None:
        super().__init__(**extras)
        self.mensagens = mensagens
        self.prompts: list[str] = []
        self.opcoes: list = []

    async def _mensagens(self, prompt, opcoes):
        self.prompts.append(prompt)
        self.opcoes.append(opcoes)
        for mensagem in self.mensagens:
            if isinstance(mensagem, BaseException):
                raise mensagem
            if mensagem == "dormir":
                await asyncio.sleep(5)
                continue
            yield mensagem


class EditorGravado(_Gravado, EditorClaude):
    pass


class FormacaoGravada(_Gravado, FormacaoClaude):
    pass


class PublicacaoGravada(_Gravado, PublicacaoClaude):
    pass


@pytest.fixture(autouse=True)
def _sem_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in VARIAVEIS_DE_API_PAGA:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture
def edicao(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    pasta = tmp_path / "edicao"
    (pasta / "img").mkdir(parents=True)
    html = pasta / "jornal.html"
    html.write_text("<html><title>Jornal do Whis</title></html>", encoding="utf-8")
    capas = {}
    for numero in (1, 2):
        capa = pasta / "img" / f"{numero}.jpg"
        capa.write_bytes(b"\xff\xd8\xff")
        capas[f"img/{numero}.jpg"] = capa
    return html, capas


# --- as portas -------------------------------------------------------------------


def test_os_tres_adaptadores_cumprem_as_portas():
    assert isinstance(EditorClaude(), PortaEditor)
    assert isinstance(FormacaoClaude(), PortaFormacao)
    assert isinstance(PublicacaoClaude(), PortaPublicacao)


# --- a escolha: validação estrutural ---------------------------------------------


def test_escolha_com_id_inexistente_descarta_o_item():
    dados = {"secoes": {"tecnologia": [_item("tecnologia-99"), _item("tecnologia-1")], "carros": []}, "time": []}

    escolha = interpretar_escolha(dados, CANDIDATOS, LIMITES, TIME)

    assert _ids(escolha.secoes["tecnologia"]) == ["tecnologia-1"]


def test_escolha_com_id_de_outra_secao_descarta_o_item():
    dados = {"secoes": {"tecnologia": [_item("carros-1")], "carros": [_item("tecnologia-2")]}, "time": [_item("carros-2")]}

    escolha = interpretar_escolha(dados, CANDIDATOS, LIMITES, TIME)

    assert escolha.secoes["tecnologia"] == ()
    assert escolha.secoes["carros"] == ()
    assert escolha.time == ()


def test_escolha_com_titulo_e_resumo_vazios_usa_os_do_feed():
    dados = {"secoes": {"tecnologia": [_item("tecnologia-2", titulo=" ", resumo="")]}, "time": []}

    item = interpretar_escolha(dados, CANDIDATOS, LIMITES, TIME).secoes["tecnologia"][0]

    assert item.titulo == "Título do feed tecnologia-2"
    assert item.resumo == "Resumo do feed tecnologia-2."


def test_escolha_leva_titulo_e_resumo_do_modelo():
    dados = {"secoes": {"carros": [_item("carros-2", "BYD produz na Bahia", "Começou. Na Bahia.")]}, "time": []}

    item = interpretar_escolha(dados, CANDIDATOS, LIMITES, TIME).secoes["carros"][0]

    assert (item.titulo, item.resumo) == ("BYD produz na Bahia", "Começou. Na Bahia.")
    assert item.candidato is CANDIDATOS["carros"][1]


def test_escolha_alem_do_maximo_e_cortada():
    dados = {"secoes": {"carros": [_item("carros-1"), _item("carros-2")]}, "time": []}

    escolha = interpretar_escolha(dados, CANDIDATOS, {"carros": (1, 1)}, TIME)

    assert _ids(escolha.secoes["carros"]) == ["carros-1"]


def test_escolha_aceita_no_maximo_dois_itens_da_mesma_fonte_por_secao():
    candidatos = {
        "brasil": tuple(_candidato(f"brasil-{n}", "brasil", "Folha Poder") for n in range(1, 5))
        + (_candidato("brasil-5", "brasil", "G1 Política"),)
    }
    dados = {"secoes": {"brasil": [_item(f"brasil-{n}") for n in range(1, 6)]}, "time": []}

    escolha = interpretar_escolha(dados, candidatos, {"brasil": (3, 5)}, ())

    assert _ids(escolha.secoes["brasil"]) == ["brasil-1", "brasil-2", "brasil-5"]


def test_escolha_do_time_tem_no_maximo_dois_itens():
    dados = {"secoes": {}, "time": [_item("time-1"), _item("time-2"), _item("time-3")]}

    escolha = interpretar_escolha(dados, CANDIDATOS, LIMITES, TIME)

    assert _ids(escolha.time) == ["time-1", "time-2"]


def test_escolha_traz_toda_secao_recebida_mesmo_vazia():
    escolha = interpretar_escolha({"secoes": {}, "time": []}, CANDIDATOS, LIMITES, TIME)

    assert escolha.secoes == {"tecnologia": (), "carros": ()}


def test_escolha_com_id_repetido_entra_uma_vez():
    dados = {"secoes": {"tecnologia": [_item("tecnologia-2"), _item("tecnologia-2")]}, "time": []}

    escolha = interpretar_escolha(dados, CANDIDATOS, LIMITES, TIME)

    assert _ids(escolha.secoes["tecnologia"]) == ["tecnologia-2"]


# --- a escolha: a chamada --------------------------------------------------------


def test_pedido_da_escolha_leva_ids_limites_time_e_destaque():
    candidatos = {"musica": (Candidato("musica-1", "musica", "Guitar World", "Nord Stage 5", "https://x", destaque=True),)}

    pedido = montar_pedido_de_escolha(candidatos, {"musica": (3, 6)}, TIME)

    assert '"musica-1"' in pedido
    assert '"destaque": true' in pedido
    assert '"musica": {"min": 3, "max": 6}' in pedido
    assert "Corinthians" in pedido
    assert '"time-3"' in pedido


def test_esquema_da_escolha_pede_uma_lista_por_secao_e_a_do_time():
    esquema = esquema_da_escolha(CANDIDATOS)

    assert esquema["required"] == ["secoes", "time"]
    assert esquema["properties"]["secoes"]["required"] == ["tecnologia", "carros"]


def candidatos_com_x() -> dict[str, tuple[Candidato, ...]]:
    return {
        "ia": (
            Candidato("ia-1", "ia", "Simon Willison", "Blog", "https://simonwillison.net/x"),
            Candidato("ia-x1", "ia", "@bcherny", "Tip", "https://x.com/bcherny/status/1", curtidas=900),
            Candidato("ia-x2", "ia", "@simonw", "Tip 2", "https://x.com/simonw/status/2", curtidas=40),
        ),
        "tecnologia": (Candidato("tecnologia-1", "tecnologia", "HN", "N", "https://e.com/1"),),
    }


def test_o_esquema_so_pede_a_dica_quando_ia_tem_post_do_x():
    com_x = esquema_da_escolha(candidatos_com_x())
    sem_x = esquema_da_escolha({"ia": candidatos_com_x()["ia"][:1], "tecnologia": ()})

    assert set(com_x["properties"]["dica"]["required"]) == {"id", "titulo", "como_aplicar"}
    assert com_x["required"] == ["secoes", "time"]
    assert "dica" not in sem_x["properties"]


def test_o_pedido_leva_as_curtidas_e_o_prompt_a_regra_da_secao_e_a_dica():
    pedido = montar_pedido_de_escolha(candidatos_com_x(), {}, ())

    assert '"curtidas": 900' in pedido
    assert '"curtidas"' not in pedido.split('"tecnologia"')[-1]
    assert "Dev com IA" in SISTEMA_ESCOLHA
    assert '"dica"' in SISTEMA_ESCOLHA and "como_aplicar" in SISTEMA_ESCOLHA


def test_dica_valida_sai_da_lista_da_secao():
    dados = {
        "secoes": {"ia": [_item("ia-x1"), _item("ia-x2")], "tecnologia": []},
        "time": [],
        "dica": {"id": "ia-x1", "titulo": "Teste no CLAUDE.md", "como_aplicar": "Ponha o comando de teste no CLAUDE.md."},
    }

    escolha = interpretar_escolha(dados, candidatos_com_x(), {}, ())

    assert escolha.dica.candidato.id == "ia-x1"
    assert (escolha.dica.titulo, escolha.dica.como_aplicar) == ("Teste no CLAUDE.md", "Ponha o comando de teste no CLAUDE.md.")
    assert [i.candidato.id for i in escolha.secoes["ia"]] == ["ia-x2"]


@pytest.mark.parametrize(
    "dica",
    [
        {"id": "ia-1", "titulo": "T", "como_aplicar": "C"},
        {"id": "tecnologia-1", "titulo": "T", "como_aplicar": "C"},
        {"id": "ia-x9", "titulo": "T", "como_aplicar": "C"},
        {"id": "ia-x1", "titulo": "T", "como_aplicar": "  "},
        "ia-x1",
    ],
)
def test_dica_invalida_cai_e_a_lista_fica_inteira(dica):
    dados = {"secoes": {"ia": [_item("ia-x1")], "tecnologia": []}, "time": [], "dica": dica}

    escolha = interpretar_escolha(dados, candidatos_com_x(), {}, ())

    assert escolha.dica is None
    assert [i.candidato.id for i in escolha.secoes["ia"]] == ["ia-x1"]


def test_dica_sem_titulo_usa_o_do_post():
    dados = {"secoes": {"ia": [], "tecnologia": []}, "time": [], "dica": {"id": "ia-x2", "titulo": "", "como_aplicar": "Use."}}

    assert interpretar_escolha(dados, candidatos_com_x(), {}, ()).dica.titulo == "Tip 2"


def test_escolha_le_a_saida_estruturada_e_traz_a_medida():
    estruturada = {"secoes": {"tecnologia": [_item("tecnologia-1")], "carros": [_item("carros-2")]}, "time": []}
    editor = EditorGravado(
        [
            _janelas(0.30, 0.604),
            _uso("StructuredOutput", estruturada),
            _janelas(0.31, 0.61),
            _resultado(structured_output=estruturada, result='{"secoes": ...}'),
        ]
    )

    escolha = editor.escolher(CANDIDATOS, LIMITES, TIME)

    assert _ids(escolha.secoes["tecnologia"]) == ["tecnologia-1"]
    assert _ids(escolha.secoes["carros"]) == ["carros-2"]
    assert escolha.medida.custo_usd == pytest.approx(0.4012)
    assert escolha.medida.segundos >= 0
    assert escolha.medida.plano_primeiro == Janelas(cinco_horas=30, sete_dias=60)
    assert escolha.medida.plano_ultimo == Janelas(cinco_horas=31, sete_dias=61)


def test_escolha_e_um_turno_sem_ferramenta_com_saida_estruturada():
    estruturada = {"secoes": {}, "time": []}
    editor = EditorGravado([_resultado(structured_output=estruturada)], modelo="claude-opus-5")

    editor.escolher(CANDIDATOS, LIMITES, TIME)

    opcoes = editor.opcoes[0]
    assert opcoes.system_prompt == SISTEMA_ESCOLHA
    assert opcoes.model == "claude-opus-5"
    assert opcoes.tools == [] and opcoes.allowed_tools == []
    assert opcoes.max_turns == 1
    assert opcoes.output_format == {"type": "json_schema", "schema": esquema_da_escolha(CANDIDATOS)}
    assert opcoes.skills == [] and opcoes.setting_sources == []
    assert opcoes.strict_mcp_config is True
    assert opcoes.permission_mode == "dontAsk"
    assert opcoes.stderr is not None


def test_escolha_sem_saida_estruturada_vira_erro_de_editor():
    editor = EditorGravado([_texto('{"secoes": {"tecnologia": []'), _resultado(result='{"secoes": {')])

    with pytest.raises(ErroDeEditor, match="saída estruturada"):
        editor.escolher(CANDIDATOS, LIMITES, TIME)


def test_escolha_que_termina_em_erro_vira_erro_de_editor():
    editor = EditorGravado(
        [_resultado(is_error=True, subtype="error_max_structured_output_retries", errors=["schema"])]
    )

    with pytest.raises(ErroDeEditor, match="error_max_structured_output_retries"):
        editor.escolher(CANDIDATOS, LIMITES, TIME)


def test_escolha_com_o_sdk_reclamando_vira_erro_de_editor():
    editor = EditorGravado([CLIConnectionError("CLI sumiu")])

    with pytest.raises(ErroDeEditor, match="CLIConnectionError"):
        editor.escolher(CANDIDATOS, LIMITES, TIME)


def test_escolha_que_estoura_o_tempo_vira_erro_de_editor():
    editor = EditorGravado(["dormir"], timeout_s=0.05)

    with pytest.raises(ErroDeEditor, match="não terminou"):
        editor.escolher(CANDIDATOS, LIMITES, TIME)


# --- o consumo do plano ------------------------------------------------------------


def test_janelas_viram_pontos_inteiros_arredondados():
    assert ler_janelas(_janelas(0.345, 0.626)) == Janelas(cinco_horas=34, sete_dias=63)


def test_evento_sem_unified_windows_nao_tem_leitura():
    evento = _limite({"status": "allowed", "rateLimitType": "five_hour", "resetsAt": 1790308200})

    assert ler_janelas(evento) is None


def test_janela_sem_utilization_fica_vazia_sem_erro():
    evento = _limite({"unifiedWindows": {"five_hour": {"resetsAt": 1}, "seven_day": {"utilization": 0.5}}})

    assert ler_janelas(evento) == Janelas(cinco_horas=None, sete_dias=50)


def test_chamada_com_rate_limit_sem_unified_windows_deixa_o_plano_sem_leitura():
    estruturada = {"secoes": {}, "time": []}
    editor = EditorGravado(
        [_limite({"status": "allowed", "rateLimitType": "five_hour"}), _resultado(structured_output=estruturada)]
    )

    medida = editor.escolher(CANDIDATOS, LIMITES, TIME).medida

    assert medida.plano_primeiro is None
    assert medida.plano_ultimo is None
    assert medida.custo_usd == pytest.approx(0.4012)


def test_custo_e_plano_ficam_no_log(caplog: pytest.LogCaptureFixture):
    estruturada = {"secoes": {}, "time": []}
    editor = EditorGravado([_janelas(0.30, 0.60), _janelas(0.31, 0.60), _resultado(structured_output=estruturada)])

    with caplog.at_level(logging.INFO, logger="whis.jornal"):
        editor.escolher(CANDIDATOS, LIMITES, TIME)

    assert "US$ 0.4012" in caplog.text
    assert "assinatura não cobra por token" in caplog.text
    assert "5 h 30% · 7 d 60% → 5 h 31% · 7 d 60%" in caplog.text


# --- a credencial, o CLI e o ambiente ----------------------------------------------


@pytest.mark.parametrize(
    ("adaptador", "erro", "chamar"),
    [
        (EditorGravado, ErroDeEditor, lambda a, e: a.escolher(CANDIDATOS, LIMITES, TIME)),
        (FormacaoGravada, ErroDeFormacao, lambda a, e: a.ensinar(DIA, "")),
        (PublicacaoGravada, ErroDePublicacao, lambda a, e: a.publicar(e[0], e[1], None)),
    ],
)
def test_api_key_paga_no_ambiente_recusa_antes_de_chamar(
    adaptador, erro, chamar, edicao, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-paga-de-teste")
    instancia = adaptador([_resultado()])

    with pytest.raises(erro, match="OAuth"):
        chamar(instancia, edicao)

    assert instancia.prompts == []


@pytest.mark.parametrize("adaptador", [EditorGravado, FormacaoGravada, PublicacaoGravada])
def test_cli_resolvido_vai_nas_opcoes(adaptador):
    instancia = adaptador([], cli=r"D:\claude\claude.exe")

    assert instancia.cli == r"D:\claude\claude.exe"
    opcoes = instancia._opcoes(sistema="s", ferramentas=[], max_turns=1)
    assert opcoes.cli_path == r"D:\claude\claude.exe"


def test_cli_preenchido_vence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert caminho_do_cli("  C:/meu/claude.exe ") == "C:/meu/claude.exe"


def test_cli_vazio_cai_no_do_npm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from whis import plataforma

    monkeypatch.setattr(plataforma, "WINDOWS", True)  # a busca do npm é a do Windows
    exe = tmp_path / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert caminho_do_cli("") == str(exe)
    assert EditorClaude(cli=None).cli == str(exe)


def test_sem_cli_do_npm_fica_o_empacotado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from whis import plataforma

    monkeypatch.setattr(plataforma, "WINDOWS", True)  # a busca do npm é a do Windows
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert caminho_do_cli(None) is None


def test_ambiente_herdado_de_outra_sessao_vai_vazio(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sessao-do-pai")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9/proxy")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token-da-conta")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")

    ambiente = ambiente_do_cli({"CLAUDE_CODE_ARTIFACT": "1"})

    assert ambiente["CLAUDE_CODE_SESSION_ID"] == ""
    assert ambiente["ANTHROPIC_BASE_URL"] == ""
    assert ambiente["CLAUDE_CODE_ARTIFACT"] == "1"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in ambiente
    assert "CLAUDE_CODE_ENTRYPOINT" not in ambiente
    assert os.environ["CLAUDE_CODE_SESSION_ID"] == "sessao-do-pai"


# --- a formação --------------------------------------------------------------------


def _aula(titulo: str, fontes: list[str], trilha: str, **figura) -> dict:
    return {"titulo": titulo, "texto": f"Texto de {titulo}. Por que isso importa: importa.", "fontes": fontes, "trilha": trilha, **figura}


FORMACAO_COMPLETA = {
    "negocios": _aula("Proposta de valor", ["https://en.wikipedia.org/wiki/Value_proposition"], "negócios: proposta de valor"),
    "carros": [
        _aula("Motor rotativo", ["https://en.wikipedia.org/wiki/Wankel_engine"], "carros: motor rotativo"),
        _aula("O que é JDM", ["https://en.wikipedia.org/wiki/Japanese_domestic_market"], "carros: o que é JDM"),
    ],
}


def test_aula_sem_url_e_descartada():
    dados = {**FORMACAO_COMPLETA, "carros": [FORMACAO_COMPLETA["carros"][0], _aula("Turbo", [], "carros: turbo")]}

    formacao = interpretar_formacao(dados, DIA)

    assert [aula.titulo for aula in formacao.carros] == ["Motor rotativo"]


def test_fonte_que_nao_e_http_nao_conta_como_url():
    dados = {**FORMACAO_COMPLETA, "negocios": _aula("Margem", ["livro de finanças", "ftp://x"], "negócios: margem")}

    assert interpretar_formacao(dados, DIA).negocios is None


def test_trilha_so_ganha_as_linhas_das_aulas_aceitas():
    dados = {**FORMACAO_COMPLETA, "negocios": _aula("Sem fonte", [], "negócios: sem fonte")}

    formacao = interpretar_formacao(dados, DIA)

    assert [aula.trilha for aula in formacao.aulas()] == [
        "- 2026-09-25 — carros: motor rotativo",
        "- 2026-09-25 — carros: o que é JDM",
    ]


def test_linha_da_trilha_sem_area_ganha_a_area():
    dados = {**FORMACAO_COMPLETA, "negocios": _aula("Caixa", ["https://exemplo.com"], "fluxo de caixa")}

    assert interpretar_formacao(dados, DIA).negocios.trilha == "- 2026-09-25 — negócios: fluxo de caixa"


def test_area_escrita_sem_acento_nao_se_repete_na_trilha():
    dados = {**FORMACAO_COMPLETA, "negocios": _aula("Preço", ["https://exemplo.com"], "negocios: preço")}

    assert interpretar_formacao(dados, DIA).negocios.trilha == "- 2026-09-25 — negócios: preço"


SVG_SIMPLES = '<svg viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8"/></svg>'


def test_aula_de_foto_guarda_a_pagina_da_foto_se_ela_e_fonte():
    wiki = "https://en.wikipedia.org/wiki/Mazda_RX-7"
    dados = {**FORMACAO_COMPLETA, "carros": [_aula("RX-7", [wiki], "carros: rx-7", visual="foto", foto_de=wiki, esquema="")]}

    [aula] = interpretar_formacao(dados, DIA).carros

    assert (aula.visual, aula.foto_de, aula.esquema) == ("foto", wiki, "")


def test_carro_com_foto_de_fora_das_fontes_usa_a_wikipedia_das_fontes():
    wiki = "https://en.wikipedia.org/wiki/Mazda_RX-7"
    dados = {**FORMACAO_COMPLETA, "carros": [_aula("RX-7", ["https://blog.com/rx7", wiki], "carros: rx-7", visual="foto", foto_de="https://outro.com/x")]}

    [aula] = interpretar_formacao(dados, DIA).carros

    assert (aula.titulo, aula.visual, aula.foto_de) == ("RX-7", "foto", wiki)


def test_carro_nunca_sai_com_desenho_so_com_foto():
    wiki = "https://en.wikipedia.org/wiki/Downforce"
    dados = {**FORMACAO_COMPLETA, "carros": [_aula("Downforce", [wiki], "carros: downforce", visual="esquema", foto_de="", esquema=SVG_SIMPLES)]}

    [aula] = interpretar_formacao(dados, DIA).carros

    assert (aula.visual, aula.foto_de, aula.esquema) == ("foto", wiki, "")


def test_negocio_com_foto_de_fora_das_fontes_fica_sem_figura():
    dados = {**FORMACAO_COMPLETA, "negocios": _aula("Margem", ["https://exemplo.com"], "negócios: margem", visual="foto", foto_de="https://outro.com/x")}

    aula = interpretar_formacao(dados, DIA).negocios

    assert (aula.titulo, aula.visual, aula.foto_de) == ("Margem", "", "")


def test_aula_de_esquema_guarda_o_svg_validado():
    dados = {**FORMACAO_COMPLETA, "negocios": _aula("Margem", ["https://exemplo.com"], "negócios: margem", visual="esquema", foto_de="", esquema=SVG_SIMPLES)}

    aula = interpretar_formacao(dados, DIA).negocios

    assert (aula.visual, aula.esquema, aula.foto_de) == ("esquema", SVG_SIMPLES, "")


def test_esquema_recusado_vira_aula_sem_figura_mas_a_aula_fica():
    dados = {**FORMACAO_COMPLETA, "negocios": _aula("Margem", ["https://exemplo.com"], "negócios: margem", visual="esquema", esquema="<svg><script/></svg>")}

    aula = interpretar_formacao(dados, DIA).negocios

    assert aula.titulo == "Margem" and (aula.visual, aula.esquema) == ("", "")


def test_a_historia_dos_carros_roda_entre_paises_e_o_editor_nao_fala_de_musica():
    assert all(pais in SISTEMA_FORMACAO for pais in ("Alemanha", "França", "Inglaterra", "Itália", "Japão"))
    assert "não é só japonesa" in SISTEMA_FORMACAO
    assert "música" not in SISTEMA_ESCOLHA.lower() and "músico" not in SISTEMA_ESCOLHA


def test_a_regra_da_figura_esta_no_prompt_e_no_esquema():
    assert "esquema" in SISTEMA_FORMACAO and "Wikipédia" in SISTEMA_FORMACAO
    assert "Nunca desenhe carro" in SISTEMA_FORMACAO
    campos = ESQUEMA_DA_FORMACAO["properties"]["carros"]["items"]["properties"]
    assert campos["visual"]["enum"] == ["foto", "esquema"] and {"foto_de", "esquema"} <= set(campos)


def test_o_esquema_e_o_pedido_seguem_as_vagas_do_dia():
    esquema = esquema_da_formacao({"negocios": 0, "carros": 1})

    assert list(esquema["properties"]) == ["carros"] and esquema["required"] == ["carros"]
    assert esquema["properties"]["carros"]["maxItems"] == 1
    assert "Hoje: 0 aula(s) de negócios e 1 de carros." in montar_pedido_de_formacao(DIA, "", {"negocios": 0, "carros": 1})


def test_aula_de_area_sem_vaga_e_ignorada():
    formacao = interpretar_formacao(FORMACAO_COMPLETA, DIA, {"negocios": 0, "carros": 1})

    assert formacao.negocios is None
    assert [aula.titulo for aula in formacao.carros] == ["Motor rotativo"]


def test_formacao_com_vagas_manda_o_esquema_delas():
    formacao = FormacaoGravada([_resultado(structured_output={"carros": FORMACAO_COMPLETA["carros"][:1]})])

    resultado = formacao.ensinar(DIA, "", {"negocios": 0, "carros": 1})

    assert formacao.opcoes[0].output_format["schema"] == esquema_da_formacao({"negocios": 0, "carros": 1})
    assert resultado.negocios is None and len(resultado.carros) == 1


def test_formacao_fica_com_no_maximo_duas_aulas_de_carro():
    terceira = _aula("Câmbio", ["https://exemplo.com/cambio"], "carros: câmbio")
    dados = {**FORMACAO_COMPLETA, "carros": FORMACAO_COMPLETA["carros"] + [terceira]}

    assert len(interpretar_formacao(dados, DIA).carros) == 2


def test_formacao_sem_nenhuma_aula_aceita_vira_erro():
    dados = {"negocios": _aula("A", [], "negócios: a"), "carros": [_aula("B", [], "carros: b")]}

    with pytest.raises(ErroDeFormacao, match="descartadas"):
        interpretar_formacao(dados, DIA)


def test_pedido_da_formacao_leva_a_data_e_a_trilha():
    pedido = montar_pedido_de_formacao(DIA, "- 2026-09-24 — carros: o que é JDM\n")

    assert "2026-09-25" in pedido
    assert "carros: o que é JDM" in pedido
    assert "primeiro dia" in montar_pedido_de_formacao(DIA, "  ")


def test_formacao_e_agentica_com_busca_na_web_e_saida_estruturada():
    formacao = FormacaoGravada(
        [
            _janelas(0.31, 0.60),
            _uso("WebSearch", {"query": "motor rotativo"}),
            _uso("WebFetch", {"url": "https://en.wikipedia.org/wiki/Wankel_engine"}, id="t2"),
            _resultado(structured_output=FORMACAO_COMPLETA, total_cost_usd=0.24),
        ]
    )

    resultado = formacao.ensinar(DIA, "")

    opcoes = formacao.opcoes[0]
    assert opcoes.system_prompt == SISTEMA_FORMACAO
    assert opcoes.tools == ["WebSearch", "WebFetch"]
    assert opcoes.allowed_tools == ["WebSearch", "WebFetch"]
    assert opcoes.max_turns == 30
    assert opcoes.output_format == {"type": "json_schema", "schema": ESQUEMA_DA_FORMACAO}
    assert formacao.timeout_s == 420
    assert resultado.negocios.titulo == "Proposta de valor"
    assert len(resultado.carros) == 2
    assert resultado.medida.custo_usd == pytest.approx(0.24)
    assert resultado.medida.plano_primeiro == Janelas(31, 60)


def test_formacao_sem_saida_estruturada_vira_erro_de_formacao():
    formacao = FormacaoGravada([_texto("Não consegui."), _resultado(result="Não consegui.")])

    with pytest.raises(ErroDeFormacao, match="saída estruturada"):
        formacao.ensinar(DIA, "")


# --- a publicação ------------------------------------------------------------------


def test_primeira_publicacao_nao_leva_url_e_manda_o_mapa_de_arquivos(edicao):
    html, capas = edicao

    pedido = pedido_de_primeira_publicacao(html, capas)

    assert "claude.ai" not in pedido
    assert "action publish" in pedido
    assert "icon 'newspaper'" in pedido
    assert "description 'Jornal diário do Whis'" in pedido
    assert f'"img/1.jpg": "{capas["img/1.jpg"].as_posix()}"' in pedido
    assert "\\" not in pedido
    assert "capabilities" in pedido and "force" not in pedido


def test_republicacao_le_lista_e_publica_no_mesmo_endereco_sem_force(edicao):
    html, capas = edicao

    pedido = pedido_de_republicacao(html, capas, URL)

    leitura = pedido.index("action read")
    listagem = pedido.index("scope files")
    publicacao = pedido.index("action publish")
    assert leitura < listagem < publicacao
    assert f"url={URL}" in pedido[publicacao:]
    assert "Read" in pedido[listagem:publicacao] and html.as_posix() in pedido[listagem:publicacao]
    assert "null" in pedido[publicacao:]
    assert "overwrite_unread" in pedido[publicacao:]
    assert "SEM force" in pedido
    assert f'"img/2.jpg": "{capas["img/2.jpg"].as_posix()}"' in pedido


def test_os_dois_pedidos_de_publicacao_declaram_o_banco_da_pagina(edicao):
    html, capas = edicao

    for pedido in (pedido_de_primeira_publicacao(html, capas), pedido_de_republicacao(html, capas, URL)):
        assert 'capabilities exatamente {"db": {}}' in pedido
        assert "sem capabilities" not in pedido.lower() and "não passe capabilities" not in pedido.lower()


def test_publicacao_usa_haiku_com_artifact_e_read_na_pasta_da_edicao(edicao):
    html, capas = edicao
    publicacao = PublicacaoGravada([_texto(URL), _resultado(result=URL)])

    publicacao.publicar(html, capas, None)

    opcoes = publicacao.opcoes[0]
    assert opcoes.model == "haiku"
    assert opcoes.tools == ["Artifact", "Read"] and opcoes.allowed_tools == ["Artifact", "Read"]
    assert opcoes.max_turns == 12
    assert Path(opcoes.cwd) == html.parent
    assert opcoes.env["CLAUDE_CODE_ARTIFACT"] == "1"
    assert opcoes.env["CLAUDE_CODE_ARTIFACT_AUTO_OPEN"] == "0"
    assert opcoes.output_format is None
    assert "claude.ai" not in publicacao.prompts[0]


def test_publicacao_com_url_atual_manda_o_pedido_de_republicacao(edicao):
    html, capas = edicao
    publicacao = PublicacaoGravada([_resultado(result=URL)])

    feita = publicacao.publicar(html, capas, URL)

    assert feita.url == URL
    assert f"action read, url={URL}" in publicacao.prompts[0]


def test_url_do_artifact_e_extraida_do_texto():
    assert extrair_url(f"Publicado: {URL}.") == URL
    longa = "https://claude.ai/code/artifact/fa2271bd-06a5-4f10-b962-272912fdb96f"
    assert extrair_url(f"pronto → {longa}") == longa
    assert extrair_url("Não consegui publicar.") is None


def test_url_confirmada_pela_ferramenta_vence_o_texto(edicao):
    html, capas = edicao
    nova = "https://claude.ai/artifact/NovoEndereco123"
    publicacao = PublicacaoGravada(
        [
            _uso("Artifact", {"action": "publish"}),
            _resposta_da_ferramenta(f"Published {html.as_posix()} at {nova} (Version 1)"),
            _resultado(result=f"Veja também {URL}"),
        ]
    )

    assert publicacao.publicar(html, capas, None).url == nova


def test_listagem_de_arquivos_nao_conta_como_publicacao(edicao):
    html, capas = edicao
    publicacao = PublicacaoGravada(
        [
            _resposta_da_ferramenta(f"Published files at {URL} (version 1790296959-7d9b), 3 files by path"),
            _resultado(result="Nothing was published."),
        ]
    )

    with pytest.raises(ErroDePublicacao):
        publicacao.publicar(html, capas, URL)


def test_publicacao_sem_url_vira_erro_de_publicacao(edicao):
    html, capas = edicao
    publicacao = PublicacaoGravada([_texto("A ferramenta recusou."), _resultado(result="A ferramenta recusou.")])

    with pytest.raises(ErroDePublicacao, match="URL"):
        publicacao.publicar(html, capas, None)


def test_publicacao_traz_a_medida(edicao):
    html, capas = edicao
    publicacao = PublicacaoGravada(
        [_janelas(0.31, 0.61), _resultado(result=URL, total_cost_usd=0.07)]
    )

    feita = publicacao.publicar(html, capas, None)

    assert feita.medida.custo_usd == pytest.approx(0.07)
    assert feita.medida.plano_ultimo == Janelas(31, 61)


# --- a única conversa de verdade -----------------------------------------------------


def _ha_credencial_do_claude() -> bool:
    if any(os.environ.get(nome) for nome in VARIAVEIS_DE_API_PAGA):
        return False
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    return (Path.home() / ".claude" / ".credentials.json").is_file()


_SEM_CONTA = pytest.mark.skipif(
    not _ha_credencial_do_claude(),
    reason="sem conta Claude logada nesta máquina (rode `claude login`)",
)

_CANDIDATOS_REAIS = {
    "tecnologia": (
        _candidato("tecnologia-1", "tecnologia", "Ars Technica", "Python 3.15 ships with a JIT enabled by default", "The CPython team says the JIT speeds up common workloads by 10 to 20 percent."),
        _candidato("tecnologia-2", "tecnologia", "The Verge", "Celebrity launches her own phone case brand", "A pop star announced a line of phone cases."),
        _candidato("tecnologia-3", "tecnologia", "Tom's Hardware", "AMD announces Zen 6 desktop CPUs for early 2027", "AMD confirmed Zen 6 desktop processors will ship in the first quarter of 2027."),
    ),
    "carros": (
        _candidato("carros-1", "carros", "Carscoops", "Toyota confirms new GR86 with turbo engine", "Toyota said the next GR86 will use a turbocharged three-cylinder engine."),
        _candidato("carros-2", "carros", "Autoesporte", "BYD inicia produção do Dolphin Mini na Bahia", "A fábrica de Camaçari começou a montar o compacto elétrico nesta semana."),
    ),
}


@pytest.mark.rede
@_SEM_CONTA
def test_escolha_real_com_cinco_candidatos():
    """Fala com o Claude de verdade, pela conta do usuário. Gasta a assinatura."""
    escolha = EditorClaude(timeout_s=180.0).escolher(
        _CANDIDATOS_REAIS, {"tecnologia": (1, 2), "carros": (1, 2)}, ()
    )

    recebidos = {c.id for lista in _CANDIDATOS_REAIS.values() for c in lista}
    escolhidos = [item for itens in escolha.secoes.values() for item in itens]
    assert escolhidos
    assert all(item.candidato.id in recebidos for item in escolhidos)
    assert escolha.medida.custo_usd is not None


@pytest.mark.rede
@_SEM_CONTA
def test_janelas_do_plano_chegam_com_o_cli_do_npm():
    """Precisa do `claude.exe` do npm (2.1.274 ou mais novo). Gasta a assinatura."""
    if caminho_do_cli(None) is None:
        pytest.skip("sem o claude.exe do npm nesta máquina")

    escolha = EditorClaude(modelo="haiku", timeout_s=180.0).escolher(
        {"carros": _CANDIDATOS_REAIS["carros"]}, {"carros": (1, 1)}, ()
    )

    assert escolha.medida.plano_ultimo is not None
    assert escolha.medida.plano_ultimo.cinco_horas is not None
