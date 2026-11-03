"""As chaves da busca no `config.toml`: a pasta privada e o teto de notas.

As duas nascem com padrão — configuração antiga continua carregando —, e a
pasta privada é validada com rigor: uma pasta que não aponta para dentro do
vault não protege nada, e o usuário acharia que está coberto.
"""

from __future__ import annotations

import sys

import tomllib
from pathlib import Path

import pytest

from whis.__main__ import MODELO_DE_CONFIGURACAO
from whis.configuracao import ConfiguracaoInvalida, carregar


def escrever(tmp_path: Path, corpo: str) -> Path:
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(f'vault = "V"\n{corpo}', encoding="utf-8")
    return arquivo


def test_configuracao_sem_as_chaves_da_busca_continua_carregando(tmp_path: Path):
    configuracao = carregar(escrever(tmp_path, ""))

    assert configuracao.privacidade_pastas_privadas == ()
    assert configuracao.busca_maximo_de_notas == 40


def test_chaves_da_busca_chegam_achatadas_e_normalizadas(tmp_path: Path):
    arquivo = escrever(
        tmp_path,
        "\n".join(
            [
                "",
                "[privacidade]",
                'pastas_privadas = ["notas/pessoal/", "notas\\\\terapia", "./saude"]',
                "",
                "[busca]",
                "maximo_de_notas = 12",
                "",
            ]
        ),
    )

    configuracao = carregar(arquivo)

    assert configuracao.privacidade_pastas_privadas == (
        "notas/pessoal",
        "notas/terapia",
        "saude",
    )
    assert configuracao.busca_maximo_de_notas == 12


def test_uma_pasta_privada_so_tambem_vale_como_texto(tmp_path: Path):
    configuracao = carregar(
        escrever(tmp_path, '\n[privacidade]\npastas_privadas = "notas/pessoal"\n')
    )

    assert configuracao.privacidade_pastas_privadas == ("notas/pessoal",)


@pytest.mark.parametrize(
    "pasta", [pytest.param('"C:/fora/do/vault"', marks=pytest.mark.skipif(sys.platform != "win32", reason="C:/ só é absoluto no Windows")), '"/fora"', '"../fora"', '"notas/../../fora"', '""', '"/"']
)
def test_pasta_privada_fora_do_vault_e_configuracao_invalida(tmp_path: Path, pasta: str):
    with pytest.raises(ConfiguracaoInvalida) as erro:
        carregar(escrever(tmp_path, f"\n[privacidade]\npastas_privadas = [{pasta}]\n"))

    assert "[privacidade].pastas_privadas" in str(erro.value)
    assert "dentro do vault" in str(erro.value)


@pytest.mark.parametrize("valor", ["0", "-3", '"muitas"'])
def test_maximo_de_notas_invalido_e_configuracao_invalida(tmp_path: Path, valor: str):
    with pytest.raises(ConfiguracaoInvalida) as erro:
        carregar(escrever(tmp_path, f"\n[busca]\nmaximo_de_notas = {valor}\n"))

    assert "[busca].maximo_de_notas" in str(erro.value)


def test_o_modelo_de_configuracao_traz_as_duas_chaves_com_o_padrao():
    dados = tomllib.loads(MODELO_DE_CONFIGURACAO.format(vault="V"))

    assert dados["privacidade"]["pastas_privadas"] == []
    assert dados["busca"]["maximo_de_notas"] == 40
