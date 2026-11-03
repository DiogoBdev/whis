"""O painel de configuração, sem Tk: os campos como dados e a lógica por porta injetada.

`CAMPOS` precisa cobrir toda chave do modelo e nenhuma a mais, com o tipo
certo para os quatro segredos, os dois caminhos e as listas. O painel entra
com um formulário dublê: preencher → salvar → arquivo relido; erro de
validação aparece na tela e nada é escrito; o segredo digitado nunca passa
por log.
"""

from __future__ import annotations

import sys

import tomllib
from pathlib import Path

import pytest

from whis import painel as modulo
from whis.configuracao import (
    MODELO_DE_CONFIGURACAO,
    Configuracao,
    Tipo,
    carregar,
)
from whis.painel import (
    CAMPOS,
    MENSAGEM_DE_SALVO,
    SEGREDOS,
    Campo,
    Painel,
    campos_do_modelo,
    montar_configuracao,
    texto_do_valor,
    textos_de,
)
from whis.palheta import Palheta

CONTEXTO = 'Ele disse "ok".\nRevisão só na quarta \\ sem exceção'


class FormularioDublê:
    """Guarda o que o painel mandou mostrar e devolve o que o teste digitou."""

    def __init__(self) -> None:
        self.campos: tuple[Campo, ...] = ()
        self.textos: dict[Campo, object] = {}
        self.erros: list[str] = []
        self.sucessos: list[str] = []
        self.fechado = False

    def mostrar(self, campos, textos) -> None:
        self.campos = campos
        self.textos = dict(textos)

    def digitar(self, caminho: str, texto) -> None:
        campo = por_caminho(caminho)
        self.textos[campo] = texto

    def ler(self):
        return dict(self.textos)

    def mostrar_erro(self, mensagem: str) -> None:
        self.erros.append(mensagem)

    def mostrar_sucesso(self, mensagem: str) -> None:
        self.sucessos.append(mensagem)

    def fechar(self) -> None:
        self.fechado = True


def por_caminho(caminho: str) -> Campo:
    return next(campo for campo in CAMPOS if campo.caminho == caminho)


# -- CAMPOS -------------------------------------------------------------------------


def test_campos_cobrem_toda_chave_do_modelo_e_nenhuma_a_mais():
    dados = tomllib.loads(MODELO_DE_CONFIGURACAO.format(vault="V"))
    do_modelo = {
        (f"[{secao}].{nome}" if isinstance(valor, dict) and secao != "horarios" else secao)
        for secao, valor in dados.items()
        for nome in (valor.keys() if isinstance(valor, dict) else [None])
    }
    do_modelo.discard("daily")
    do_modelo |= {"[daily].ativa", "[daily].horarios"}

    assert {campo.caminho for campo in CAMPOS} == do_modelo
    assert len(CAMPOS) == 85


def test_os_oito_segredos_os_tres_caminhos_e_as_listas_tem_o_tipo_certo():
    por_tipo = {tipo: {campo.caminho for campo in CAMPOS if campo.tipo is tipo} for tipo in Tipo}

    assert por_tipo[Tipo.SEGREDO] == set(SEGREDOS)
    assert por_tipo[Tipo.CAMINHO] == {"vault", "[git].raiz", "[linkedin].exportacao"}
    assert por_tipo[Tipo.LISTA] == {
        "[privacidade].excluir", "[privacidade].pastas_privadas",
        "[git].excluir", "[git].autores", "[slack].conversas_excluidas", "[slack].so_com_mencao",
        "[cacada].plataformas_sem_conta", "[cacada].plataformas_pagas", "[cacada].horarios_da_vigia_de_freelas",
    }
    assert por_tipo[Tipo.HORARIOS] == {"[daily].horarios"}
    assert por_tipo[Tipo.BOOLEANO] == {
        "[daily].ativa",
        "[noticias].ativa",
        "[cacada].enviar_sozinho",
        "[cacada].pacote_completo",
        "[cacada].propostas_auto_exclusivos",
        "[cacada].discord",
        "[cacada].x_ideias",
        "[cacada].x_botao_responder",
    }
    assert por_tipo[Tipo.INTEIRO] == {
        "[busca].maximo_de_notas", "[git].profundidade", "[cacada].teto_semanal", "[cacada].maximo_de_pacotes",
        "[linkedin].posts_por_vez", "[cacada].maximo_de_envios", "[cacada].limite_minutos", "[cacada].limite_plano",
        "[cacada].encaixe_minimo_tentar", "[cacada].piso_freela", "[cacada].propostas_auto_horas",
        "[cacada].propostas_auto_max_propostas", "[cacada].propostas_auto_por_dia", "[cacada].propostas_auto_reserva",
        "[cacada].propostas_auto_para_aprovar", "[cacada].propostas_auto_valor_hora", "[cacada].agencias_por_dia",
        "[cacada].max_propostas_freelancer", "[cacada].lojas_por_dia", "[cacada].lojas_redesenho_por_dia", "[cacada].envios_frios_por_dia",
        "[cacada].discord_por_dia", "[cacada].instagram_por_dia", "[cacada].x_por_dia",
    }
    assert por_tipo[Tipo.HORA] == {
        "[diario].inicio_do_dia", "[diario].hora_da_tarefa", "[noticias].horario", "[granola].horario", "[cacada].hora_do_lembrete",
        "[cacada].horario_da_procura",
    }


def test_a_ajuda_de_cada_campo_e_o_comentario_do_modelo():
    assert por_caminho("[linear].api_key").ajuda == "Personal API Key read-only; vazio = fonte desligada"
    assert por_caminho("[palheta].atalho").ajuda.startswith("o atalho global que abre a palheta")
    assert all(campo.ajuda for campo in CAMPOS), [campo.caminho for campo in CAMPOS if not campo.ajuda]


def test_modelo_e_chaves_fora_de_sincronia_estouram_ao_importar():
    with pytest.raises(RuntimeError, match="CHAVES não conhece"):
        campos_do_modelo(MODELO_DE_CONFIGURACAO + "\n[nova]\nchave = 1  # x\n")
    sem_modelo = "\n".join(
        linha for linha in MODELO_DE_CONFIGURACAO.splitlines() if not linha.startswith("modelo = ")
    )
    with pytest.raises(RuntimeError, match="modelo não traz"):
        campos_do_modelo(sem_modelo)


# -- os valores como texto -----------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="caminho C:\\ só vira barra normal no Windows")
def test_lista_vira_uma_linha_por_item_e_horarios_um_texto_por_dia():
    configuracao = Configuracao(
        vault=Path("V"),
        privacidade_excluir=("a", "b"), daily_horarios=(("segunda", "13:55"), ("sexta", "10:25")),
    )

    assert texto_do_valor(configuracao, por_caminho("[privacidade].excluir")) == "a\nb"
    assert texto_do_valor(configuracao, por_caminho("[daily].horarios")) == {
        "segunda": "13:55", "terca": "", "quarta": "", "quinta": "", "sexta": "10:25", "sabado": "", "domingo": "",
    }
    assert texto_do_valor(configuracao, por_caminho("[git].raiz")) == ""
    assert texto_do_valor(replace_vault(configuracao), por_caminho("vault")) == "C:/x/v"


def replace_vault(configuracao):
    from dataclasses import replace

    return replace(configuracao, vault=Path(r"C:\x\v"))


# -- o painel por porta injetada --------------------------------------------------


@pytest.fixture
def cenario(arquivo_de_configuracao: Path):
    configuracao = carregar(arquivo_de_configuracao)
    formulario = FormularioDublê()
    salvas: list[Configuracao] = []
    painel = Painel(configuracao, formulario, ao_salvar=salvas.append)
    painel.abrir()
    return painel, formulario, salvas, arquivo_de_configuracao


def test_abrir_mostra_todos_os_campos_com_os_valores_atuais(cenario):
    painel, formulario, _salvas, arquivo = cenario

    assert formulario.campos == CAMPOS
    assert formulario.textos[por_caminho("[busca].maximo_de_notas")] == "40"
    assert formulario.textos[por_caminho("vault")] == carregar(arquivo).vault.as_posix()
    assert formulario.textos[por_caminho("[linear].api_key")] == ""


def test_preencher_e_salvar_reescreve_o_arquivo_e_recarrega(cenario):
    painel, formulario, salvas, arquivo = cenario
    formulario.digitar("[linear].api_key", "  lin-secreta-123  ")
    formulario.digitar("[claude].modelo", CONTEXTO)
    formulario.digitar("[privacidade].excluir", "senha\n\n  cpf  \n")
    formulario.digitar("[daily].horarios", {"segunda": "13:55", "terca": "", "quarta": "10:25", "quinta": "", "sexta": "", "sabado": "", "domingo": ""})
    formulario.digitar("[git].raiz", r"C:\Users\x\dev")
    formulario.digitar("[busca].maximo_de_notas", " 3 ")

    nova = painel.salvar()

    relida = carregar(arquivo)
    assert nova is not None and salvas == [nova]
    assert relida.linear_api_key == "lin-secreta-123"
    assert relida.claude_modelo == CONTEXTO
    assert relida.privacidade_excluir == ("senha", "cpf")
    assert relida.daily_horarios == (("segunda", "13:55"), ("quarta", "10:25"))
    assert relida.git_raiz == Path("C:/Users/x/dev")
    assert relida.busca_maximo_de_notas == 3
    assert painel.configuracao == relida
    assert formulario.sucessos == [MENSAGEM_DE_SALVO] and "reinicie a bandeja" in MENSAGEM_DE_SALVO
    assert formulario.erros == []
    assert any(p.name.startswith("config.toml.bak-") for p in arquivo.parent.iterdir())


def test_erro_de_validacao_aparece_na_tela_e_nada_e_escrito(cenario):
    painel, formulario, salvas, arquivo = cenario
    antes = arquivo.read_text(encoding="utf-8")
    formulario.digitar("[busca].maximo_de_notas", "sete")

    assert painel.salvar() is None

    [erro] = formulario.erros
    assert "'[busca].maximo_de_notas' precisa ser um número inteiro de notas" in erro
    assert str(arquivo) in erro
    assert arquivo.read_text(encoding="utf-8") == antes
    assert salvas == [] and formulario.sucessos == []
    assert [p.name for p in arquivo.parent.iterdir() if p.name.startswith("config.toml")] == ["config.toml"]


@pytest.mark.parametrize(
    ("caminho", "texto", "trecho"),
    [
        ("[slack].bot_token", "xoxp-errado", "[slack].bot_token"),
        ("[daily].horarios", {"segunda": "25:00", "terca": "", "quarta": "", "quinta": "", "sexta": "", "sabado": "", "domingo": ""}, "horarios.segunda"),
        ("[palheta].atalho", "space", "[palheta].atalho"),
        ("[privacidade].pastas_privadas", "../fora", "pastas_privadas"),
        ("vault", "", "vault"),
    ],
)
def test_as_mensagens_sao_as_mesmas_de_configuracao(cenario, caminho, texto, trecho):
    painel, formulario, _salvas, _arquivo = cenario
    formulario.digitar(caminho, texto)

    painel.salvar()

    assert trecho in formulario.erros[0]


def test_o_segredo_digitado_nunca_aparece_em_log(cenario, caplog, tmp_path: Path):
    painel, formulario, _salvas, arquivo = cenario
    from whis import registro

    registro.acrescentar_arquivo(painel.configuracao)
    formulario.digitar("[linear].api_key", "lin_api_MUITO_SECRETA")
    formulario.digitar("[slack].app_token", "xapp-outro-segredo")

    with caplog.at_level("DEBUG", logger="whis"):
        painel.salvar()

    assert carregar(arquivo).linear_api_key == "lin_api_MUITO_SECRETA"
    assert "MUITO_SECRETA" not in caplog.text and "xapp-outro-segredo" not in caplog.text
    log = arquivo.parent / "bandeja.log"
    assert "MUITO_SECRETA" not in log.read_text(encoding="utf-8")


def test_fechar_fecha_o_formulario(cenario):
    painel, formulario, _salvas, _arquivo = cenario

    painel.fechar()

    assert formulario.fechado


def test_montar_configuracao_valida_sem_tocar_o_disco(arquivo_de_configuracao: Path):
    textos = textos_de(carregar(arquivo_de_configuracao))
    textos[por_caminho("[busca].maximo_de_notas")] = "0"

    with pytest.raises(Exception, match="maximo_de_notas"):
        montar_configuracao(textos, origem=arquivo_de_configuracao)


# -- a costura com a palheta ---------------------------------------------------------


def test_salvar_recarrega_a_configuracao_da_palheta(arquivo_de_configuracao: Path, monkeypatch):
    """`abrir_painel` liga o painel à palheta; o Tk entra por um formulário dublê."""
    formularios: list[FormularioDublê] = []

    def formulario_dublê(_raiz):
        formulario = FormularioDublê()
        formulario.ligar = lambda painel: None
        formulario.aberto = lambda: not formulario.fechado
        formulario.trazer_para_frente = lambda: None
        formularios.append(formulario)
        return formulario

    monkeypatch.setattr(modulo, "FormularioTk", formulario_dublê)
    modulo._abertos.clear()
    palheta = Palheta((), carregar(arquivo_de_configuracao), iniciar_thread=lambda f: f())

    painel = modulo.abrir_painel(object(), palheta)
    [formulario] = formularios
    formulario.digitar("[busca].maximo_de_notas", "12")
    painel.salvar()

    assert palheta.configuracao.busca_maximo_de_notas == 12
    assert carregar(arquivo_de_configuracao).busca_maximo_de_notas == 12
    assert modulo.abrir_painel(object(), palheta) is None or len(formularios) == 2
