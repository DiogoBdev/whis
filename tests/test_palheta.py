"""A palheta: registro, despacho e o contrato "dispara e some" — sem janela e sem Tk.

Cada comando roda pela costura de verdade contra um vault temporário, com as
portas fake (`PublicacaoDoDeckFake`, `BuscaFake`) e o thread de trabalho síncrono. O que
se prova é o que sobra: a nota no vault, o fio em `conversas.json`, o toast
montado (com e sem botão) e que nada escapa da zona `respostas/`. A janela
entra por dublê; o filtro e o destaque da lista são funções puras; o Tk de
verdade tem um teste próprio no fim, que pula onde não há tela.
"""

from __future__ import annotations

from whis.atalho_global import ATALHO_PADRAO

import json
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fixtures.leitura import arquivos_do_vault, corpo, ler_frontmatter
from whis import __main__ as cli
from whis import avisos
from whis import bandeja as _bandeja
from whis import palheta as modulo
from whis import vault as _vault
from whis.bandeja import Bandeja
from whis.busca import Citacao, Resposta
from whis.busca_claude import PROMPT_DA_BUSCA
from whis.configuracao import MODELO_DE_CONFIGURACAO, ConfiguracaoInvalida, carregar
from whis.conversa import NOME_DO_ARQUIVO
from fixtures.daily import NOTA_DA_DAILY, escrever_nota_da_daily
from whis.fakes import (
    RESPOSTA_PADRAO,
    URL_DO_DECK_FAKE,
    BuscaFake,
    PublicacaoDoDeckFake,
)
from whis.janela_da_palheta import Acao, Efeito, Linha, comando_destacado, comando_pelo_rotulo, completar, filtrar
from whis.palheta import (
    ORDEM_DAS_ACOES,
    Comando,
    ComandoDesconhecido,
    ListaDoFazendo,
    Palheta,
    Resultado,
    comandos_padrao,
    interpretar,
)
from test_bandeja import IconeDublê

FUSO = timezone(timedelta(hours=-3))
MOMENTO = datetime(2026, 9, 12, 14, 3, tzinfo=FUSO)
PERGUNTA = "o que ficou decidido sobre a retenção de 180 dias?"
NOTA_DA_RESPOSTA = "respostas/2026-09-12-1403-o-que-ficou-decidido-sobre-a-retencao-de-180-dias.md"
COMANDOS = (
    "daily", "did", "doing", "done", "news", "meetings", "hunt", "accounts", "linkedin", "posts", "note", "doubt", "do", "ask", "open", "settings", "prompts",
    "stash",
)


class AvisoEspiao:
    def __init__(self) -> None:
        self.avisos: list[tuple[str, str, tuple[str, str] | None]] = []

    def __call__(self, titulo: str, texto: str, *, botao=None, executar=None) -> bool:
        self.avisos.append((titulo, texto, botao))
        return True

    def ultimo(self) -> tuple[str, str, tuple[str, str] | None]:
        assert self.avisos, "nenhum toast saiu"
        return self.avisos[-1]


class JanelaDublê:
    """A porta da janela sem Tk: guarda se está visível, as ações e o que foi enviado."""

    def __init__(self) -> None:
        self.visivel = False
        self.acoes: tuple[Acao, ...] = ()
        self._callback = None

    def mostrar(self) -> None:
        self.visivel = True

    def esconder(self) -> None:
        self.visivel = False

    def ao_enviar(self, callback) -> None:
        self._callback = callback

    def definir_acoes(self, acoes) -> None:
        self.acoes = tuple(acoes)

    def digitar_e_enter(self, texto: str) -> None:
        """O que a janela de verdade faz no Enter: some e só então despacha."""
        self.esconder()
        if texto.strip():
            self._callback(texto)

    def clicar(self, nome: str) -> None:
        self.esconder()
        self._callback(nome)

    def esc(self) -> None:
        self.esconder()


@pytest.fixture
def aviso() -> AvisoEspiao:
    return AvisoEspiao()


@pytest.fixture
def montar(configuracao_do_diario, aviso: AvisoEspiao):
    """Uma palheta com os comandos de verdade sobre as portas fake, síncrona."""

    def construir(*, busca=None, publicacao=None, simular_daily=False, lancados=None, abrir_painel=None, extras=()):
        configuracao = configuracao_do_diario()
        comandos = comandos_padrao(
            publicacao_do_deck=lambda _atual: publicacao or PublicacaoDoDeckFake(),
            busca=lambda _atual: busca or BuscaFake(),
            simular_daily=simular_daily,
            lancar=(lancados if lancados is not None else []).append,
            relogio=lambda: MOMENTO,
        )
        return Palheta(
            (*comandos, *extras),
            configuracao,
            avisar=aviso,
            iniciar_thread=lambda alvo: alvo(),
            abrir_painel=abrir_painel,
        )

    return construir


# -- interpretar --------------------------------------------------------------


REGISTRO = (
    Comando("daily", "gera", lambda a, c: None),
    Comando("note", "anota", lambda a, c: None, parametro="text"),
)


def test_interpretar_separa_o_comando_do_argumento():
    comando, argumento = interpretar("note puxar a retenção  de 180 dias ", REGISTRO)

    assert comando.nome == "note"
    assert argumento == "puxar a retenção  de 180 dias"


def test_interpretar_nao_diferencia_maiusculas():
    assert interpretar("DAILY", REGISTRO)[0].nome == "daily"
    assert interpretar("  Note x", REGISTRO)[0].nome == "note"


def test_texto_vazio_e_comando_desconhecido_listam_os_que_existem():
    with pytest.raises(ComandoDesconhecido, match="Escreva um comando"):
        interpretar("   ", REGISTRO)
    with pytest.raises(ComandoDesconhecido) as erro:
        interpretar("voar alto", REGISTRO)

    mensagem = str(erro.value)
    assert "'voar'" in mensagem
    assert "daily" in mensagem and "note <text>" in mensagem


# -- daily ----------------------------------------------------------------------


def test_daily_pela_palheta_publica_os_slides_e_avisa_com_o_link(montar, caminho_do_vault: Path, aviso):
    escrever_nota_da_daily(caminho_do_vault)
    publicacao = PublicacaoDoDeckFake()
    palheta = montar(publicacao=publicacao)

    assert palheta.despachar("daily") is True

    assert publicacao.chamadas == 1
    assert aviso.ultimo() == ("A daily está pronta", "4 slides no link de sempre.", ("Abrir slides", URL_DO_DECK_FAKE))


def test_o_toast_da_daily_e_o_mesmo_do_subcomando(montar, caminho_do_vault: Path, aviso, monkeypatch):
    """Um lugar só monta o toast: o subcomando e a palheta dizem a mesma coisa."""
    escrever_nota_da_daily(caminho_do_vault)
    montar().despachar("daily")
    da_palheta = aviso.ultimo()
    monkeypatch.setattr(avisos, "avisar", aviso)

    cli.main(["daily"])

    assert aviso.ultimo() == da_palheta


def test_daily_com_a_bandeja_em_fakes_simula_sem_publicar_nem_escrever(montar, caminho_do_vault: Path, aviso):
    arquivo = escrever_nota_da_daily(caminho_do_vault)
    publicacao = PublicacaoDoDeckFake()
    palheta = montar(publicacao=publicacao, simular_daily=True)

    palheta.despachar("daily")

    assert publicacao.chamadas == 0
    assert arquivo.read_bytes().decode("utf-8") == NOTA_DA_DAILY
    assert aviso.ultimo() == ("Simulação da daily", "Simulação: 4 slides, nada publicado.", None)


def test_daily_com_a_nota_vazia_avisa_com_o_botao_da_nota(montar, caminho_do_vault: Path, aviso):
    montar().despachar("daily")

    titulo, _texto, botao = aviso.ultimo()
    assert titulo == "A nota da daily está vazia"
    assert botao == ("Abrir no Obsidian", f"obsidian://open?vault={caminho_do_vault.name}&file=daily%2Fatual")


# -- note e doubt -----------------------------------------------------------


def test_note_escreve_em_pontos_pelo_mesmo_caminho_do_subcomando(montar, caminho_do_vault: Path, aviso):
    palheta = montar()

    palheta.despachar("note puxar a retenção de 180 dias")

    nota = (caminho_do_vault / "daily" / "atual.md").read_text(encoding="utf-8")
    assert nota.endswith("## Pontos\n- puxar a retenção de 180 dias\n")
    titulo, texto, botao = aviso.ultimo()
    assert titulo == "Anotado em Pontos"
    assert texto == "puxar a retenção de 180 dias"
    assert botao[1].endswith("&file=daily%2Fatual")


def test_doubt_escreve_em_duvidas_e_so_a_pedido(montar, caminho_do_vault: Path, aviso):
    palheta = montar()

    palheta.despachar("doubt o pix pendente expira sozinho?")

    nota = (caminho_do_vault / "daily" / "atual.md").read_text(encoding="utf-8")
    assert "## Dúvidas\n- o pix pendente expira sozinho?\n\n## Pontos\n" in nota
    assert aviso.ultimo()[0] == "Anotado em Dúvidas"


def test_note_acrescenta_no_fim_de_pontos_sem_mudar_o_resto(montar, caminho_do_vault: Path):
    palheta = montar()
    _vault.preparar(caminho_do_vault)
    antiga = (
        "---\ntipo: daily-atual\n---\n\n## O que eu fiz\n\n- feito\n\n## O que estou fazendo\n\n"
        "## Dúvidas\n\n- uma dúvida\n\n## Falar\n\n- escrito à mão\n\n## Pontos\n\n- ponto antigo\n"
    )
    (caminho_do_vault / "daily" / "atual.md").write_bytes(antiga.encode("utf-8"))

    palheta.despachar("note ponto novo")

    texto = (caminho_do_vault / "daily" / "atual.md").read_bytes().decode("utf-8")
    assert texto == antiga + "- ponto novo\n"


def test_do_escreve_a_caixa_em_fazer_para_ele_marcar(montar, caminho_do_vault: Path, aviso):
    palheta = montar()

    palheta.despachar("do responder o ribeiro")

    nota = (caminho_do_vault / "daily" / "atual.md").read_text(encoding="utf-8")
    assert "## Fazer\n- [ ] responder o ribeiro\n\n## Dúvidas\n" in nota
    titulo, texto, botao = aviso.ultimo()
    assert titulo == "Anotado em Fazer"
    assert texto == "responder o ribeiro"
    assert botao[1].endswith("&file=daily%2Fatual")


def test_o_que_o_note_anota_vira_o_slide_pontos_da_daily(montar, caminho_do_vault: Path, aviso):
    """Desde 2026-10-07 o `note` escreve em *Pontos*, que vai para o deck no lugar de *Falar*."""
    publicacao = PublicacaoDoDeckFake()
    palheta = montar(publicacao=publicacao)
    escrever_nota_da_daily(caminho_do_vault)

    palheta.despachar("note falar que mandei msg pro lucas sobre o CRM")
    assert aviso.ultimo()[:2] == ("Anotado em Pontos", "falar que mandei msg pro lucas sobre o CRM")
    palheta.despachar("daily")

    assert "falar que mandei msg pro lucas sobre o CRM" in publicacao.conteudos["project/slides/pontos.html"]
    assert all("também não vai" not in conteudo for conteudo in publicacao.conteudos.values())


def test_did_e_doing_escrevem_em_fiz_e_fazendo_sem_caixa(montar, caminho_do_vault: Path, aviso):
    palheta = montar()
    escrever_nota_da_daily(caminho_do_vault)

    palheta.despachar("did FN-131 deploy do frete")
    assert aviso.ultimo()[:2] == ("Anotado em O que eu fiz", "FN-131 deploy do frete")
    palheta.despachar("doing FN-140 revisão do checkout")
    titulo, texto, botao = aviso.ultimo()
    assert (titulo, texto) == ("Anotado em O que estou fazendo", "FN-140 revisão do checkout")
    assert botao[1].endswith("&file=daily%2Fatual")

    nota = (caminho_do_vault / "daily" / "atual.md").read_bytes().decode("utf-8")
    assert "\t- subiu em produção\n- FN-131 deploy do frete\n\n## O que estou fazendo\n" in nota
    assert "- FN-130 diário automático\n- FN-140 revisão do checkout\n\n## Fazer\n" in nota


def test_done_abre_a_lista_e_fora_da_janela_explica(montar, aviso):
    palheta = montar()
    done = next(comando for comando in palheta.comandos if comando.nome == "done")

    assert isinstance(done.lista(palheta.configuracao), ListaDoFazendo)
    assert done.parametro == "search"
    palheta.despachar("done")
    assert aviso.ultimo()[0] == "Done"


@pytest.mark.parametrize("comando", ["did", "doing", "note", "doubt", "do"])
def test_anotar_sem_texto_pede_o_texto_e_nao_escreve(montar, caminho_do_vault: Path, aviso, comando):
    palheta = montar()

    palheta.despachar(comando)

    assert not (caminho_do_vault / "daily" / "atual.md").exists()
    titulo, texto, botao = aviso.ultimo()
    assert titulo == "Anotar o quê?" and comando in texto and botao is None


# -- done ------------------------------------------------------------------------

NOTA_DO_DONE = (
    "---\ntipo: daily-atual\n---\n\n"
    "## O que eu fiz\n\n- já estava aqui\n\n"
    "## O que estou fazendo\n\n"
    "- Revisão do checkout\n"
    "\t- botão do pix\n"
    "- Ajustes do flywheel\n"
    "\n## Pontos\n\n- sexta saio cedo\n"
)


@pytest.fixture
def lista_do_fazendo(configuracao_do_diario, caminho_do_vault: Path) -> ListaDoFazendo:
    escrever_nota_da_daily(caminho_do_vault, NOTA_DO_DONE)
    return ListaDoFazendo(configuracao_do_diario())


def nota_viva_de(vault: Path) -> str:
    return (vault / "daily" / "atual.md").read_bytes().decode("utf-8")


def test_a_lista_do_done_mostra_os_itens_de_fazendo_com_o_caminho_dos_pais(lista_do_fazendo):
    assert lista_do_fazendo.linhas("") == [
        Linha("◔", "Revisão do checkout"),
        Linha("◔", "botão do pix", "em: Revisão do checkout"),
        Linha("◔", "Ajustes do flywheel"),
    ]
    assert lista_do_fazendo.detalhe(0) == "- Revisão do checkout\n\t- botão do pix"
    assert lista_do_fazendo.nome == "Done"
    assert [gesto.tecla for gesto in lista_do_fazendo.gestos] == ["<Return>"]


def test_a_lista_do_done_filtra_sem_acento_nem_maiuscula(lista_do_fazendo):
    assert [linha.titulo for linha in lista_do_fazendo.linhas("REVISAO")] == ["Revisão do checkout"]
    assert lista_do_fazendo.linhas("vagas") == [] and lista_do_fazendo.vazia == "No item matches"


def test_enter_move_para_fiz_e_a_lista_fica_aberta_sem_o_item(lista_do_fazendo, caminho_do_vault: Path):
    lista_do_fazendo.linhas("")
    [enter] = lista_do_fazendo.gestos

    efeito = enter.rodar(2, "")

    assert not enter.esconde
    assert efeito == Efeito(mensagem="Moved: Ajustes do flywheel")
    assert "- já estava aqui\n- Ajustes do flywheel\n\n## O que estou fazendo\n" in nota_viva_de(caminho_do_vault)
    assert [linha.titulo for linha in lista_do_fazendo.linhas("")] == ["Revisão do checkout", "botão do pix"]


def test_enter_numa_busca_move_o_item_filtrado(lista_do_fazendo, caminho_do_vault: Path):
    lista_do_fazendo.linhas("pix")

    lista_do_fazendo.gestos[0].rodar(0, "pix")

    assert "- já estava aqui\n- Revisão do checkout\n\t- botão do pix\n" in nota_viva_de(caminho_do_vault)


def test_a_nota_mudada_entre_ler_e_mover_nao_mexe_e_avisa_no_rodape(lista_do_fazendo, caminho_do_vault: Path):
    lista_do_fazendo.linhas("")
    mudada = NOTA_DO_DONE.replace("- Ajustes do flywheel\n", "- Ajustes do flywheel v2\n")
    escrever_nota_da_daily(caminho_do_vault, mudada)

    efeito = lista_do_fazendo.gestos[0].rodar(2, "")

    assert efeito == Efeito(mensagem="The note changed — try again")
    assert nota_viva_de(caminho_do_vault) == mudada


def test_sem_selecao_o_enter_nao_faz_nada(lista_do_fazendo, caminho_do_vault: Path):
    lista_do_fazendo.linhas("vagas")

    assert lista_do_fazendo.gestos[0].rodar(None, "vagas") is None
    assert nota_viva_de(caminho_do_vault) == NOTA_DO_DONE


def test_a_lista_vazia_ensina_o_doing(configuracao_do_diario, caminho_do_vault: Path):
    escrever_nota_da_daily(caminho_do_vault, "---\ntipo: daily-atual\n---\n\n## O que estou fazendo\n\n")
    lista = ListaDoFazendo(configuracao_do_diario())

    assert lista.linhas("") == []
    assert lista.vazia == "Nothing in O que estou fazendo — type: doing <text>"


# -- ask -----------------------------------------------------------------------


def test_ask_vira_nota_em_respostas_com_as_fontes_recolhidas(montar, caminho_do_vault: Path, aviso):
    palheta = montar()

    palheta.despachar(f"ask {PERGUNTA}")

    nota = caminho_do_vault / NOTA_DA_RESPOSTA
    assert nota.is_file()
    frontmatter = ler_frontmatter(nota)
    assert frontmatter["tipo"] == "resposta"
    assert frontmatter["pergunta"] == PERGUNTA
    assert "data" not in frontmatter, "campo `data` vira link da Daily notes do Obsidian"
    texto = corpo(nota).lstrip("\n")
    assert texto.startswith(f"# {PERGUNTA}\n\n{RESPOSTA_PADRAO.texto}\n")
    assert "> [!info]- Fontes\n> - `reunioes/2026-09-03-1403-reuniao.md` — retenção de 180 dias\n" in texto
    assert texto.index("[!info]- Fontes") > texto.index(RESPOSTA_PADRAO.texto)


def test_o_toast_da_pergunta_traz_o_comeco_da_resposta_e_o_botao_da_nota(montar, caminho_do_vault: Path, aviso):
    palheta = montar()

    palheta.despachar(f"ask {PERGUNTA}")

    titulo, texto, botao = aviso.ultimo()
    assert titulo == "Whis"
    assert texto == RESPOSTA_PADRAO.texto
    assert botao == (
        "Abrir no Obsidian",
        f"obsidian://open?vault={caminho_do_vault.name}&file="
        "respostas%2F2026-09-12-1403-o-que-ficou-decidido-sobre-a-retencao-de-180-dias",
    )


def test_resposta_longa_e_encurtada_no_toast_mas_inteira_na_nota(montar, caminho_do_vault: Path, aviso):
    longa = "Frase. " * 100
    palheta = montar(busca=BuscaFake(Resposta(texto=longa, citacoes=(Citacao("daily/x.md"),))))

    palheta.despachar("ask conta tudo")

    _titulo, texto, _botao = aviso.ultimo()
    assert len(texto) <= modulo.LIMITE_DO_TEXTO_DO_TOAST and texto.endswith("…")
    assert longa.strip() in corpo(caminho_do_vault / "respostas" / "2026-09-12-1403-conta-tudo.md")


def test_nao_achou_tambem_vira_nota_dizendo_que_nao_achou(montar, caminho_do_vault: Path, aviso):
    palheta = montar(busca=BuscaFake(Resposta(texto="", achou=False)))

    palheta.despachar("ask cadê o link coringa")

    nota = caminho_do_vault / "respostas" / "2026-09-12-1403-cade-o-link-coringa.md"
    assert ler_frontmatter(nota)["achou"] == "false"
    assert "Não achei nada no vault" in corpo(nota)
    assert "> [!info]- Fontes\n> _(nenhuma" in corpo(nota)
    titulo, _texto, botao = aviso.ultimo()
    assert titulo == "Não achei"
    assert botao is not None


def test_o_fio_da_palheta_guarda_a_pergunta_e_a_resposta(montar, caminho_do_vault: Path):
    busca = BuscaFake()
    palheta = montar(busca=busca)
    palheta.despachar(f"ask {PERGUNTA}")

    palheta.despachar("ask e quem levantou isso?")

    arquivo = palheta.configuracao.origem.parent / NOME_DO_ARQUIVO
    fios = json.loads(arquivo.read_text(encoding="utf-8"))["conversas"]
    assert [turno["texto"] for turno in fios["palheta"]] == [
        PERGUNTA, RESPOSTA_PADRAO.texto, "e quem levantou isso?", RESPOSTA_PADRAO.texto,
    ]
    assert [turno.texto for turno in busca.historico_recebido] == [PERGUNTA, RESPOSTA_PADRAO.texto]
    assert not list(Path(caminho_do_vault).rglob(NOME_DO_ARQUIVO))


def test_ask_so_escreve_em_respostas(montar, caminho_do_vault: Path):
    palheta = montar()
    _vault.preparar(caminho_do_vault)
    antes = arquivos_do_vault(caminho_do_vault)

    palheta.despachar(f"ask {PERGUNTA}")

    assert arquivos_do_vault(caminho_do_vault) - antes == {NOTA_DA_RESPOSTA}


def test_a_zona_das_respostas_nao_alcanca_nenhuma_outra_pasta(caminho_do_vault: Path):
    raiz = _vault.preparar(caminho_do_vault)
    assert (raiz / "respostas").is_dir()

    for fora in ("notas/x.md", "daily/atual.md", "respostas/../notas/x.md", "diario/x.md"):
        with pytest.raises(_vault.ForaDaZonaDePropriedade):
            _vault.escrever(raiz, fora, "x", zonas=_vault.ZONAS_DAS_RESPOSTAS)
    assert _vault.escrever(raiz, "respostas/ok.md", "x", zonas=_vault.ZONAS_DAS_RESPOSTAS).is_file()


def test_ask_sem_pergunta_nao_chama_a_busca(montar, aviso):
    busca = BuscaFake()
    palheta = montar(busca=busca)

    palheta.despachar("ask")

    assert busca.perguntas_recebidas == []
    assert aviso.ultimo()[0] == "Perguntar o quê?"


# -- open, settings -------------------------------------------------------------


def test_open_lanca_a_uri_da_nota_viva_sem_toast(montar, caminho_do_vault: Path, aviso):
    lancados: list[str] = []
    palheta = montar(lancados=lancados)

    palheta.despachar("open")

    assert lancados == [f"obsidian://open?vault={caminho_do_vault.name}&file=daily%2Fatual"]
    assert aviso.avisos == []


def test_settings_abre_o_painel_injetado_sem_toast(montar, aviso):
    aberturas: list[str] = []
    palheta = montar(abrir_painel=lambda: aberturas.append("painel"))

    palheta.despachar("settings")

    assert aberturas == ["painel"]
    assert aviso.avisos == []


def test_o_painel_e_preenchido_depois_por_quem_hospeda(montar, aviso):
    palheta = montar()
    palheta.despachar("settings")
    assert aviso.ultimo()[0] == "O comando falhou"
    aberturas: list[str] = []

    palheta.abrir_painel = lambda: aberturas.append("painel")
    palheta.despachar("settings")

    assert aberturas == ["painel"]


def test_texto_vazio_nao_faz_nada_e_comando_desconhecido_diz_o_que_existe(montar, aviso):
    palheta = montar()

    assert palheta.despachar("   ") is False
    assert aviso.avisos == []

    palheta.despachar("vagas python remoto")
    titulo, texto, _ = aviso.ultimo()
    assert titulo == "Não entendi"
    assert "'vagas'" in texto and "ask <question>" in texto


def test_sao_os_dezoito_comandos_e_nenhum_futuro_nem_ajuda(montar):
    palheta = montar()

    assert {comando.nome for comando in palheta.comandos} == set(COMANDOS)
    assert [acao.nome for acao in palheta.acoes()] == list(ORDEM_DAS_ACOES)
    assert [acao.rotulo_atual() for acao in palheta.acoes()[:5]] == [
        "Open note", "Generate daily", "Did", "Doing", "Mark done",
    ]
    assert all(acao.icone and acao.descricao for acao in palheta.acoes())


# -- o contrato: um por vez, falha vira toast ----------------------------------


def test_um_despacho_enquanto_outro_roda_e_recusado_com_toast(montar, aviso):
    resultados: list[bool] = []
    palheta = montar()

    def lento(_argumento, _configuracao):
        resultados.append(palheta.despachar("open"))
        assert palheta.ocupada()
        return Resultado("Lento", "terminei")

    palheta.comandos = (*palheta.comandos, Comando("lento", "demora", lento))

    assert palheta.despachar("lento") is True

    assert resultados == [False]
    titulos = [titulo for titulo, _, _ in aviso.avisos]
    assert titulos == ["Ainda estou no comando anterior", "Lento"]
    assert "open" in aviso.avisos[0][1]
    assert not palheta.ocupada()


def test_erro_do_comando_vira_toast_e_libera_a_palheta(montar, aviso, caplog):
    def quebra(_argumento, _configuracao):
        raise RuntimeError("o Claude não respondeu em 240s")

    palheta = montar(extras=(Comando("quebra", "falha", quebra),))

    palheta.despachar("quebra")  # não levanta

    titulo, texto, botao = aviso.ultimo()
    assert titulo == "O comando falhou"
    assert texto == "o Claude não respondeu em 240s"
    assert botao is None
    assert not palheta.ocupada()
    assert "falhou" in caplog.text


def test_a_falha_da_busca_chega_ao_usuario_pela_mensagem_dela(montar, aviso):
    from whis.busca import ErroDeBusca

    palheta = montar(busca=BuscaFake(erro=ErroDeBusca("sem conta logada")))

    palheta.despachar("ask x")

    assert aviso.ultimo()[:2] == ("O comando falhou", "sem conta logada")


def test_a_busca_com_o_login_do_claude_vencido_pede_o_login(montar, aviso):
    from whis.busca import ErroDeBusca

    palheta = montar(
        busca=BuscaFake(
            erro=ErroDeBusca(
                "Falha ao falar com o Claude (ResultError): Claude Code returned an error result: "
                "Failed to authenticate: OAuth session expired and could not be refreshed (exit code: 1)"
            )
        )
    )

    palheta.despachar("ask x")

    titulo, texto, botao = aviso.ultimo()
    assert titulo == "O login do Claude venceu"
    assert texto == "Rode 'claude auth login' num terminal. Depois repita o comando."
    assert botao is None


# -- a janela -------------------------------------------------------------------

ACOES = (
    Acao("open", "▤", "Open note", "daily/atual.md in Obsidian"),
    Acao("daily", "☀", "Generate daily"),
    Acao("did", "✓", "Did", aceita_argumento=True),
    Acao("doing", "◔", "Doing", aceita_argumento=True),
    Acao("done", "☑", "Mark done", aceita_argumento=True),
    Acao("news", "📰", "Generate news"),
    Acao("meetings", "◷", "Pull meetings"),
    Acao("hunt", "⌖", "Hunt jobs & gigs"),
    Acao("accounts", "⚿", "Site accounts"),
    Acao("linkedin", "in", "LinkedIn"),
    Acao("posts", "✎", "Publish a post", aceita_argumento=True),
    Acao("prompts", "❝", "Search prompts", aceita_argumento=True),
    Acao("stash", "⧉", "Stash prompt", aceita_argumento=True),
    Acao("ask", "?", "Ask", aceita_argumento=True),
    Acao("note", "✎", "Note", aceita_argumento=True),
    Acao("doubt", "¿", "Doubt", aceita_argumento=True),
    Acao("do", "☐", "To-do", aceita_argumento=True),
    Acao("settings", "⚙", "Settings"),
)


def test_com_o_campo_vazio_todas_as_acoes_aparecem_nas_tres_pedidas_primeiro():
    assert [acao.nome for acao in filtrar(ACOES, "")] == list(ORDEM_DAS_ACOES)
    assert filtrar(ACOES, "  ")[:3] == ACOES[:3]


def test_a_lista_filtra_pelo_comeco_da_primeira_palavra():
    assert [acao.nome for acao in filtrar(ACOES, "d")] == ["daily", "did", "doing", "done", "doubt", "do"]
    assert [acao.nome for acao in filtrar(ACOES, "Do")] == ["do", "doing", "done", "doubt"]


def test_o_nome_exato_vem_na_frente_de_quem_so_comeca_igual():
    """Digitar `do` e apertar Tab tem que completar `do `, não `doubt `."""
    assert [acao.nome for acao in filtrar(ACOES, "do")] == ["do", "doing", "done", "doubt"]
    assert completar(filtrar(ACOES, "do")[0]) == "do "
    assert [acao.nome for acao in filtrar(ACOES, "ask qual foi")] == ["ask"]
    assert filtrar(ACOES, "vagas") == ()


def test_o_rotulo_tambem_filtra_e_o_nome_do_comando_vem_primeiro():
    assert [acao.nome for acao in filtrar(ACOES, "generate")] == ["daily", "news"]
    assert [acao.nome for acao in filtrar(ACOES, "generate daily")] == ["daily", "news"]
    assert [acao.nome for acao in filtrar(ACOES, "Open note")] == ["open"]
    assert [acao.nome for acao in filtrar(ACOES, "no")] == ["note", "open"]
    assert [acao.nome for acao in filtrar(ACOES, "d")] == ["daily", "did", "doing", "done", "doubt", "do"]


def test_a_primeira_palavra_ganha_destaque_so_quando_e_um_comando():
    assert comando_destacado("ask qual foi a decisão", ACOES) == (0, 3)
    assert comando_destacado("  Note  x", ACOES) == (2, 6)
    assert comando_destacado("daily", ACOES) == (0, 5)
    assert comando_destacado("dail", ACOES) is None
    assert comando_destacado("vagas python", ACOES) is None
    assert comando_destacado("", ACOES) is None


def test_o_rotulo_inteiro_ganha_destaque_quando_e_ele_que_casa():
    assert comando_destacado("generate daily", ACOES) == (0, 14)
    assert comando_destacado("  Generate Daily ", ACOES) == (2, 16)
    assert comando_destacado("generate daily agora", ACOES) is None
    assert comando_destacado("generate", ACOES) is None


def test_o_texto_igual_ao_rotulo_vira_o_comando():
    assert comando_pelo_rotulo("generate daily", ACOES) == "daily"
    assert comando_pelo_rotulo("  GENERATE   DAILY ", ACOES) == "daily"
    assert comando_pelo_rotulo("Ask", ACOES) == "ask"
    assert comando_pelo_rotulo("daily", ACOES) is None
    assert comando_pelo_rotulo("vagas", ACOES) is None
    assert comando_pelo_rotulo("", ACOES) is None


def test_tab_completa_o_comando_e_deixa_o_espaco_quando_ha_argumento():
    assert completar(ACOES[1]) == "daily"
    assert completar(ACOES[4]) == "done "
    assert completar(ACOES[7]) == "hunt"
    assert completar(ACOES[13]) == "ask "
    assert completar(ACOES[14]) == "note "
    assert completar(ACOES[16]) == "do "
    assert completar(ACOES[17]) == "settings"


def test_o_rotulo_de_uma_acao_pode_ser_funcao():
    """É como a bandeja entrega o rótulo: `Comando.rotulo_atual`, chamado ao desenhar."""
    assert Acao("daily", "☀", lambda: "Generate daily").rotulo_atual() == "Generate daily"


def test_a_janela_some_no_enter_e_o_comando_e_despachado(montar, aviso):
    palheta = montar(abrir_painel=lambda: None)
    janela = JanelaDublê()
    janela.ao_enviar(palheta.despachar)
    janela.mostrar()

    janela.digitar_e_enter("ask cadê")

    assert not janela.visivel
    assert aviso.ultimo()[0] == "Whis"


def test_enter_vazio_so_esconde_e_esc_tambem(montar, aviso):
    palheta = montar()
    janela = JanelaDublê()
    janela.ao_enviar(palheta.despachar)

    janela.mostrar()
    janela.digitar_e_enter("")
    assert not janela.visivel

    janela.mostrar()
    janela.esc()
    assert not janela.visivel
    assert aviso.avisos == []


def test_clicar_numa_acao_despacha_o_nome_dela(montar, caminho_do_vault: Path, aviso):
    lancados: list[str] = []
    palheta = montar(lancados=lancados)
    janela = JanelaDublê()
    janela.ao_enviar(palheta.despachar)
    janela.definir_acoes([Acao(c.nome, c.icone, c.rotulo_atual, c.descricao, bool(c.parametro)) for c in palheta.acoes()])

    janela.clicar("open")

    assert lancados and lancados[0].endswith("daily%2Fatual")
    assert not janela.visivel


def test_a_janela_de_verdade_filtra_destaca_e_some_no_enter_e_no_esc():
    """Tk de verdade, quando há tela: digitar filtra e destaca; Enter/Esc/↓/Tab fazem o que devem."""
    tk = pytest.importorskip("tkinter")
    from whis.janela_da_palheta import JanelaDaPalheta

    try:
        janela = JanelaDaPalheta()
    except tk.TclError as erro:  # sem display
        pytest.skip(f"sem tela para o Tk: {erro}")
    enviados: list[str] = []
    janela.ao_enviar(enviados.append)
    janela.definir_acoes(ACOES)
    try:
        janela.mostrar()
        janela.raiz.update()
        assert janela.visivel()
        assert [acao.nome for acao in janela.acoes_visiveis()] == list(ORDEM_DAS_ACOES)
        assert janela.selecionada().nome == "open"

        janela.digitar("ask qual foi")
        assert [acao.nome for acao in janela.acoes_visiveis()] == ["ask"]
        assert janela._campo.tag_ranges("comando") and str(janela._campo.get("comando.first", "comando.last")) == "ask"

        janela.digitar("generate daily")
        assert [acao.nome for acao in janela.acoes_visiveis()] == ["daily", "news"]
        assert str(janela._campo.get("comando.first", "comando.last")) == "generate daily"

        janela.digitar("vag")
        assert janela.acoes_visiveis() == () and not janela._campo.tag_ranges("comando")

        janela._enter()
        assert not janela.visivel() and enviados == ["vag"]

        janela.mostrar()
        janela.raiz.update()
        janela.digitar("Generate daily")
        janela._enter()
        assert not janela.visivel() and enviados == ["vag", "daily"]

        janela.mostrar()
        janela.raiz.update()
        janela._tab()
        assert janela.texto() == "open" and janela.selecionada().nome == "open"
        janela.digitar("no")
        assert [acao.nome for acao in janela.acoes_visiveis()] == ["note", "open"]
        janela._tab()
        assert janela.texto() == "note " and janela.selecionada().nome == "note"
        janela._tab()
        assert janela.texto() == "open" and [acao.nome for acao in janela.acoes_visiveis()] == ["open"]
        janela._campo.event_generate("<Escape>")
        janela.raiz.update()
        assert not janela.visivel() and enviados == ["vag", "daily"]

        janela.mostrar()
        janela.raiz.update()
        janela._mover_selecao(1)
        assert janela.selecionada().nome == "daily"
        janela._enter()
        assert not janela.visivel() and enviados == ["vag", "daily", "daily"]

        janela.mostrar()
        janela.raiz.update()
        janela._campo.event_generate("<Escape>")
        janela.raiz.update()
        assert not janela.visivel() and enviados == ["vag", "daily", "daily"]

        chamadas: list[str] = []
        janela.pedir(lambda: chamadas.append("de outro thread"))
        janela._sondar()
        assert chamadas == ["de outro thread"]

        janela.definir_acoes(ACOES[:3])
        janela.mostrar()
        janela.raiz.update()
        assert janela.titulos_desenhados() == ["Open note", "Generate daily", "Did"]
        janela._clicar(2)
        assert enviados[-1] == "did"
    finally:
        janela.raiz.destroy()


# -- a configuração e o prompt ----------------------------------------------------


def _config_com(tmp_path: Path, corpo_do_toml: str) -> Path:
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(f'vault = "V"\n{corpo_do_toml}', encoding="utf-8")
    return arquivo


def test_o_atalho_da_palheta_vem_da_configuracao_com_padrao(tmp_path: Path):
    assert carregar(_config_com(tmp_path, "")).palheta_atalho == ATALHO_PADRAO
    assert carregar(_config_com(tmp_path, '[palheta]\natalho = "Ctrl + Shift + K"\n')).palheta_atalho == "ctrl+shift+k"


def test_atalho_invalido_e_configuracao_invalida(tmp_path: Path):
    with pytest.raises(ConfiguracaoInvalida) as erro:
        carregar(_config_com(tmp_path, '[palheta]\natalho = "space"\n'))

    assert "[palheta].atalho" in str(erro.value) and "modificador" in str(erro.value)


def test_o_modelo_traz_o_atalho_padrao_comentado():
    dados = tomllib.loads(MODELO_DE_CONFIGURACAO.format(vault="V"))

    assert dados["palheta"]["atalho"] == ATALHO_PADRAO
    assert "# o atalho global" in MODELO_DE_CONFIGURACAO


def test_o_prompt_tem_a_voz_do_whis_e_um_teto_estrutural_sem_fontes_no_texto():
    assert "Whis" in PROMPT_DA_BUSCA and "sereno" in PROMPT_DA_BUSCA and "sem firula" in PROMPT_DA_BUSCA
    assert "TRÊS frases ou 400 caracteres" in PROMPT_DA_BUSCA
    assert 'O campo "resposta" NÃO traz lista de fontes' in PROMPT_DA_BUSCA
    assert 'As fontes vão SÓ em "citacoes"' in PROMPT_DA_BUSCA
    assert "celular" not in PROMPT_DA_BUSCA


# -- a bandeja hospeda a palheta --------------------------------------------------


class IconeDestacavel(IconeDublê):
    def __init__(self) -> None:
        super().__init__()
        self.destacado = False

    def run_detached(self) -> None:
        self.destacado = True


class HospedeiroDublê:
    def __init__(self) -> None:
        self.executou = False

    def executar(self) -> None:
        self.executou = True


def rotulos(bandeja: Bandeja) -> list[str]:
    return [item.rotulo for item in bandeja.itens_do_menu() if not item.separador]


def test_o_menu_tem_abrir_a_palheta_primeiro_e_configurar_antes_de_sair():
    abertos: list[str] = []
    bandeja = Bandeja(
        icone=IconeDestacavel(),
        abrir_palheta=lambda: abertos.append("palheta"),
        abrir_configuracao=lambda: abertos.append("configuracao"),
    )

    assert rotulos(bandeja) == ["Abrir a palheta", "Configurar", "Sair"]
    bandeja.abrir_palheta()
    bandeja.abrir_configuracao()
    assert abertos == ["palheta", "configuracao"]


def test_com_hospedeiro_o_icone_vai_para_o_thread_secundario_e_sair_encerra_os_tres():
    icone = IconeDestacavel()
    hospedeiro = HospedeiroDublê()
    encerrados: list[str] = []
    bandeja = Bandeja(icone=icone, abrir_palheta=lambda: None, ao_sair=lambda: encerrados.append("atalho e janela"))

    bandeja.executar(hospedeiro=hospedeiro)
    assert icone.destacado and not icone.rodou and hospedeiro.executou

    bandeja.sair()
    assert icone.parou and encerrados == ["atalho e janela"]


def test_sem_hospedeiro_o_icone_roda_no_thread_principal():
    icone = IconeDublê()

    Bandeja(icone=icone).executar()

    assert icone.rodou


def test_o_subcomando_bandeja_monta_a_palheta_com_os_fakes_e_o_atalho(configuracao_do_diario, monkeypatch, caminho_do_vault: Path):
    configuracao_do_diario()
    chamadas: list[dict] = []
    monkeypatch.setattr(_bandeja, "executar", lambda configuracao, **extras: chamadas.append(extras) or 0)

    assert cli.main(["bandeja", "--fakes"]) == 0

    [extras] = chamadas
    palheta = extras["palheta"]
    assert isinstance(palheta, Palheta)
    assert extras["atalho"].texto == ATALHO_PADRAO
    escrever_nota_da_daily(caminho_do_vault)
    daily = next(comando for comando in palheta.comandos if comando.nome == "daily")
    resultado = daily.rodar("", palheta.configuracao)
    assert resultado.titulo == "Simulação da daily" and resultado.link is None
    assert sorted(p.name for p in (caminho_do_vault / "daily").iterdir()) == ["atual.md"]


def test_a_janela_de_verdade_mostra_sete_linhas_e_rola_com_a_selecao_e_a_barra():
    tk = pytest.importorskip("tkinter")
    from whis.janela_da_palheta import ALTURA_DA_LINHA, LARGURA, MAXIMO_DE_LINHAS, JanelaDaPalheta

    try:
        janela = JanelaDaPalheta()
    except tk.TclError as erro:
        pytest.skip(f"sem tela para o Tk: {erro}")
    janela.definir_acoes(ACOES)
    try:
        janela.mostrar()
        janela.raiz.update()
        assert len(ACOES) > MAXIMO_DE_LINHAS and janela.rola()
        assert len(janela.titulos_desenhados()) == MAXIMO_DE_LINHAS
        assert janela._tela.find_withtag("barra")

        for _ in range(len(ACOES) - 1):
            janela._mover_selecao(1)
        assert janela.selecionada().nome == "settings"
        assert janela.titulos_desenhados()[-1] == "Settings"
        assert janela.primeira_desenhada() == len(ACOES) - MAXIMO_DE_LINHAS

        topo = janela._topo_das_linhas
        janela._ao_clicar(type("Evento", (), {"x": LARGURA - 5, "y": topo})())
        assert janela.primeira_desenhada() == 0
        assert janela.titulos_desenhados()[0] == "Open note"
        assert janela.selecionada().nome == ACOES[MAXIMO_DE_LINHAS - 1].nome
        janela._ao_arrastar(type("Evento", (), {"x": 300, "y": topo + MAXIMO_DE_LINHAS * ALTURA_DA_LINHA})())
        assert janela.primeira_desenhada() == len(ACOES) - MAXIMO_DE_LINHAS

        janela.digitar("d")
        assert not janela.rola() and not janela._tela.find_withtag("barra")
        assert janela.primeira_desenhada() == 0
    finally:
        janela.raiz.destroy()
