"""A procura de lojas Shopify lentas: só Shopify de verdade, lenta medida, sem cliente de agência nem o que ele pôs em Nunca contatar."""

from __future__ import annotations

import sys

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from whis import lojas_shopify
from whis.lojas_shopify import CAMINHO_DA_LISTA_DE_LOJAS, Candidata, PaginaInicial
from whis.medida_no_celular import MedidaNoCelular

AGORA = datetime(2026, 10, 18, 12, 30, tzinfo=timezone(timedelta(hours=-3)))
SHOPIFY = '<script>Shopify.theme = {"name":"Dawn","schema_name":"Dawn","schema_version":"15.0.0"};</script>'

AGENCIAS = """\
# Agências Shopify no Brasil

**1. Aloft** · Criciúma/SC · https://aloftagencia.com
- Contato: contato@aloftagencia.com (site).
- Prova: Lojas: Juicy Sneakers (juicysneakers.com.br), perfil em instagram.com/aloft.

**2. Alfinet** · São Paulo/SP · https://alfinet.com.br
- Contato: contato@alfinet.com.br.
"""

LISTA = """\
---
tipo: pesquisa
---

# Lojas Shopify lentas no Brasil

## Nunca contatar

Um domínio por linha.

- nunca.com.br
- empregodele.com.br
- ramo: álbu, fotolivro

## Outubro de 2026 (busca de 17/10)

**1. Já Listada** · Recife/PE · https://jalistada.com.br
- Contato: contato@jalistada.com.br
"""


def _loja(nome: str, site: str, contato: str = "", ramo: str = "cosméticos") -> Candidata:
    return Candidata(nome, "São Paulo/SP", f"https://{site}", contato or f"contato@{site}", ramo, "Vende online.")


CANDIDATAS = (
    _loja("Loja Lenta", "lenta.com.br"),
    _loja("Juicy", "juicysneakers.com.br"),
    _loja("Álbuns da Ana", "albunsdaana.com.br", ramo="álbuns de fotos"),
    _loja("Do Emprego", "empregodele.com.br", ramo="presentes"),
    _loja("Sem E-mail", "semmail.com.br", contato="maria@semmail.com.br"),
    _loja("Woo", "woo.com.br"),
    _loja("Rápida", "rapida.com.br"),
    _loja("Com Senha", "senha.com.br"),
    _loja("Cliente da Aloft", "cliente.com.br"),
    _loja("Tema da Alfinet", "temaalfinet.com.br"),
    _loja("Nunca", "nunca.com.br"),
    _loja("Já Listada", "jalistada.com.br"),
    _loja("Mais Lenta", "maislenta.com.br"),
    _loja("Linka o Instagram", "insta.com.br"),
)

PAGINAS = {
    "lenta.com.br": PaginaInicial(SHOPIFY, "https://lenta.com.br/"),
    "woo.com.br": PaginaInicial("<html>WooCommerce</html>", "https://woo.com.br/"),
    "rapida.com.br": PaginaInicial(SHOPIFY, "https://rapida.com.br/"),
    "senha.com.br": PaginaInicial(SHOPIFY, "https://senha.com.br/password"),
    "cliente.com.br": PaginaInicial(SHOPIFY + '<a href="https://www.aloftagencia.com/">feito pela Aloft</a>', "https://cliente.com.br/"),
    "temaalfinet.com.br": PaginaInicial(
        '<script>Shopify.theme = {"name":"x","schema_name":"Tema Base - Alfinet","schema_version":"1.0.0"};</script>', "https://temaalfinet.com.br/"
    ),
    "maislenta.com.br": PaginaInicial(SHOPIFY, "https://maislenta.com.br/"),
    "insta.com.br": PaginaInicial(SHOPIFY + '<a href="https://instagram.com/insta">insta</a>', "https://insta.com.br/"),
}
MEDIDAS = {
    "lenta.com.br": MedidaNoCelular(9.4, 3.2, 140),
    "rapida.com.br": MedidaNoCelular(2.1, 0.9, 40),
    "maislenta.com.br": MedidaNoCelular(16.3, 5.6, 448),
    "insta.com.br": MedidaNoCelular(6.0, 2.0, 100),
}


class _Busca:
    def __init__(self, achadas=CANDIDATAS):
        self.achadas = achadas
        self.pedidos: list[list[str]] = []
        self.ramos: list[tuple[str, ...]] = []

    def buscar(self, conhecidas, ramos_fora=()):
        self.pedidos.append(list(conhecidas))
        self.ramos.append(tuple(ramos_fora))
        return tuple(self.achadas)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "cacada").mkdir(parents=True)
    (vault / "cacada" / "agencias-shopify.md").write_text(AGENCIAS, encoding="utf-8")
    (vault / CAMINHO_DA_LISTA_DE_LOJAS).write_text(LISTA, encoding="utf-8")
    return vault


def _buscar(tmp_path: Path, porta: _Busca, *, na_fila: int = 0, agora: datetime = AGORA, medidas: list | None = None):
    medidas = [] if medidas is None else medidas

    def medir(url: str):
        medidas.append(url)
        return MEDIDAS.get(lojas_shopify.dominio(url))

    return lojas_shopify.buscar_se_faltar(
        _vault(tmp_path) if not (tmp_path / "vault").exists() else tmp_path / "vault",
        porta,
        estado=tmp_path / "lojas.json",
        na_fila=na_fila,
        abrir=lambda url: PAGINAS.get(lojas_shopify.dominio(url)),
        medir_no_celular=medir,
        agora=agora,
    )


def test_so_a_shopify_lenta_com_email_da_empresa_entra_na_lista_a_mais_lenta_primeiro(tmp_path: Path):
    porta = _Busca()
    medidas: list[str] = []

    resultado = _buscar(tmp_path, porta, medidas=medidas)

    assert [loja.nome for loja in resultado.novas] == ["Mais Lenta", "Loja Lenta", "Linka o Instagram"]
    assert resultado.novas[0].email == "contato@maislenta.com.br" and resultado.novas[0].tema == "Dawn 15.0.0"
    assert resultado.descartadas == {
        "loja feita por uma agência das listas": 1,
        "ramo em Nunca contatar": 1,
        "em Nunca contatar": 2,
        "já está na lista": 1,
        "sem e-mail da empresa": 1,
        "não é Shopify": 1,
        "a página não é lenta nem tem o tema de fábrica": 1,
        "fechada com senha": 1,
        "o site leva o nome ou o link de uma agência das listas": 2,
    }
    assert sorted(lojas_shopify.dominio(url) for url in medidas) == ["insta.com.br", "lenta.com.br", "maislenta.com.br", "rapida.com.br"]
    conhecidas = porta.pedidos[0]
    assert {"juicysneakers.com.br", "aloftagencia.com", "nunca.com.br", "jalistada.com.br", "empregodele.com.br"} <= set(conhecidas)
    assert porta.ramos == [("albu", "fotolivro")]
    assert "instagram.com" not in conhecidas
    nota = (tmp_path / "vault" / CAMINHO_DA_LISTA_DE_LOJAS).read_text(encoding="utf-8")
    assert "## Outubro de 2026 (busca de 18/10)" in nota
    assert "**1. Mais Lenta** · São Paulo/SP · https://maislenta.com.br" in nota
    assert "- Medida em 18/10: no celular em 4G, a maior imagem apareceu em 16,3 s (LCP); 5,6 MB em 448 pedidos; tema Dawn 15.0.0" in nota
    assert "- nunca.com.br" in nota, "a seção Nunca contatar fica como ele deixou"


def test_busca_so_com_a_fila_curta_uma_vez_por_dia(tmp_path: Path):
    porta = _Busca(achadas=())

    cheia = _buscar(tmp_path, porta, na_fila=5)
    curta = _buscar(tmp_path, porta, na_fila=4)
    mesmo_dia = _buscar(tmp_path, porta, agora=AGORA + timedelta(hours=3))
    seis_dias = _buscar(tmp_path, porta, agora=AGORA + timedelta(days=6))
    sete_dias = _buscar(tmp_path, porta, agora=AGORA + timedelta(days=7))

    assert cheia.ja_feita and "5 loja(s)" in cheia.motivo
    assert not curta.ja_feita and curta.novas == ()
    assert mesmo_dia.ja_feita and "hoje" in mesmo_dia.motivo
    assert seis_dias.ja_feita and "não achou nenhuma nova" in seis_dias.motivo
    assert not sete_dias.ja_feita and len(porta.pedidos) == 2


def test_o_teto_de_medidas_segura_a_busca(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(lojas_shopify, "MEDIDAS_POR_BUSCA", 1)
    medidas: list[str] = []

    resultado = _buscar(tmp_path, _Busca(), medidas=medidas)

    assert len(medidas) == 1 and len(resultado.novas) == 1
    assert resultado.descartadas["passou do teto de medidas da busca"] >= 1


def test_o_claude_da_busca_so_le_e_recebe_os_ramos_fora():
    sistema = lojas_shopify.SISTEMA_DAS_LOJAS

    assert "Só leitura" in sistema and "nunca mande mensagem" in sistema
    assert "RAMOS FORA" in sistema and "Shopify" in sistema
    assert lojas_shopify.LojasClaude.etapa


def test_a_medida_no_celular_vira_texto_com_virgula_e_o_tempo_arredondado():
    medida = MedidaNoCelular(9.6, 3.24, 140)

    assert medida.lenta and not MedidaNoCelular(4.0, 1, 10).lenta
    assert medida.segundos() == "10 segundos"
    assert "apareceu em 9,6 s" in medida.como_texto() and "3,2 MB em 140 pedidos" in medida.como_texto()


def test_o_comando_de_teste_da_procura_nao_usa_claude_nem_o_vault(arquivo_de_configuracao: Path, caminho_do_vault: Path, monkeypatch, capsys):
    from whis.__main__ import main

    monkeypatch.setenv("LOCALAPPDATA", str(caminho_do_vault.parent / "local"))

    assert main(["procurar", "--fakes"]) == 0

    saida = capsys.readouterr().out
    assert "Agências: 2 nova(s)" in saida
    assert "Lojas: 1 nova(s)" in saida and "sem e-mail da empresa: 1" in saida
    assert not (caminho_do_vault / CAMINHO_DA_LISTA_DE_LOJAS).exists()
    assert not (caminho_do_vault.parent / "local" / "whis" / "cacada" / "lojas.json").exists()


def test_a_procura_de_verdade_conta_a_fila_das_lojas_e_as_agencias_a_cada_dois_dias(tmp_path: Path, monkeypatch, capsys):
    from datetime import date

    from whis import agencias, lojas_email
    from whis.__main__ import main

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    estado = agencias.caminho_do_estado()
    estado.parent.mkdir(parents=True)
    estado.write_text(f'{{"dia": "{date.today().isoformat()}"}}', encoding="utf-8")
    monkeypatch.setattr(lojas_email, "na_fila", lambda vault, estado: 12)
    monkeypatch.setattr(agencias.AgenciasClaude, "buscar", lambda self, conhecidas: pytest.fail("buscou agência antes de 2 dias"))
    monkeypatch.setattr(lojas_shopify.LojasClaude, "buscar", lambda self, conhecidas: pytest.fail("buscou loja com a fila cheia"))
    from whis import instagram

    monkeypatch.setattr(instagram.InstagramClaude, "buscar", lambda self, quantos, conhecidos, voz: ())
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(f'vault = "{(tmp_path / "vault").as_posix()}"\n', encoding="utf-8")

    assert main(["procurar", "--config", str(arquivo)]) == 0

    saida = capsys.readouterr().out
    assert "Agências: sem busca hoje, a última foi em " in saida
    assert "Lojas: sem busca hoje, a fila ainda tem 12 loja(s) para escrever." in saida


@pytest.mark.skipif(sys.platform != "win32", reason="cria o atalho de verdade pelo PowerShell")
def test_whis_windows_liga_e_desliga_a_procura(tmp_path: Path, monkeypatch, capsys):
    from whis import automacao
    from whis.__main__ import main

    chamadas: list[str] = []
    monkeypatch.setattr(automacao, "agendar_procura", lambda configuracao: chamadas.append("agendar") or "Whis-Procura")
    monkeypatch.setattr(automacao, "desagendar_procura", lambda: chamadas.append("desagendar") or True)
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(f'vault = "{tmp_path.as_posix()}"\n', encoding="utf-8")

    assert main(["windows", "--procura", "--config", str(arquivo)]) == 0
    assert "Procura agendada como 'Whis-Procura': todo dia às 12:30" in capsys.readouterr().out
    assert main(["windows", "--remover", "--procura"]) == 0
    assert "Tarefa 'Whis-Procura' removida do Agendador." in capsys.readouterr().out
    assert chamadas == ["agendar", "desagendar"]


def test_a_tarefa_da_procura_tem_90_minutos_e_as_outras_continuam_com_30(tmp_path: Path):
    import subprocess

    from whis import automacao
    from whis.configuracao import Configuracao

    comandos: list[list[str]] = []

    def executar(comando):
        comandos.append(comando)
        return subprocess.CompletedProcess(comando, 0, "", "")

    configuracao = Configuracao(vault=tmp_path, origem=tmp_path / "config.toml", cacada_horario_da_procura="13:00")
    assert automacao.agendar_procura(configuracao, executar=executar) == "Whis-Procura"
    automacao.agendar_vigia_de_freelas(configuracao, executar=executar)

    procura, vigia = comandos
    assert "procurar --agendada" in " ".join(procura) and "Daily=13:00" in procura
    assert procura[-2:] == ["-LimiteMinutos", "90"]
    assert "-LimiteMinutos" not in vigia
    script = automacao.SCRIPT_DE_AGENDAMENTO.read_text(encoding="utf-8-sig")
    assert "[int]$LimiteMinutos = 30" in script and "-Minutes $LimiteMinutos" in script
