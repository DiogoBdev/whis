"""A caçada agendada: roda sozinha de madrugada sem deixar o dono sem plano."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from whis.__main__ import _cacada_agendada_fora_de_hora, _freelas_nesta_noite
from whis.cacada import GuardaDoPlano, JanelaEsgotada, Parada
from whis.cacada_claude import LimiteDoPlano, Plano
from whis.freelas_rapidos import do_nicho, pedido_de_freelas
from whis.oportunidades import Oportunidade

AGORA = datetime(2026, 11, 10, 1, 30, tzinfo=UTC)


def _guarda(agendada: bool = True) -> GuardaDoPlano:
    guarda = GuardaDoPlano(100, relogio=lambda: AGORA, dormir=lambda _s: pytest.fail("a agendada não dorme"))
    if agendada:
        guarda.agendar(50)
    return guarda


def test_fora_da_vespera_para_no_teto_da_agendada():
    guarda = _guarda()
    guarda.plano = Plano(sete_dias=49, vira_sete_dias=AGORA + timedelta(days=4))
    guarda.conferir()
    guarda.plano = Plano(sete_dias=50, vira_sete_dias=AGORA + timedelta(days=4))
    with pytest.raises(Parada, match="50%"):
        guarda.conferir()


def test_a_30_h_da_virada_ainda_vale_o_teto_da_agendada():
    guarda = _guarda()
    guarda.plano = Plano(sete_dias=60, vira_sete_dias=AGORA + timedelta(hours=30))
    with pytest.raises(Parada):
        guarda.conferir()


def test_na_vespera_gasta_o_que_sobraria_e_nao_atravessa_a_virada():
    guarda = _guarda()
    guarda.atravessa_virada = True  # mesmo que o modo freelas peça, a agendada não atravessa
    guarda.agendar(50)
    guarda.plano = Plano(sete_dias=90, vira_sete_dias=AGORA + timedelta(hours=6))
    guarda.conferir()
    guarda.plano = Plano(sete_dias=10, vira_sete_dias=AGORA + timedelta(minutes=4))
    with pytest.raises(Parada, match="virar"):
        guarda.conferir()


def test_sem_virada_conhecida_fica_no_teto_da_agendada():
    guarda = _guarda()
    guarda.plano = Plano(sete_dias=55)
    with pytest.raises(Parada):
        guarda.conferir()


def test_para_com_a_janela_de_5h_em_90():
    guarda = _guarda()
    guarda.plano = Plano(cinco_horas=89, sete_dias=10, vira_sete_dias=AGORA + timedelta(days=4))
    guarda.conferir()
    guarda.plano = Plano(cinco_horas=90, sete_dias=10, vira_sete_dias=AGORA + timedelta(days=4))
    with pytest.raises(Parada, match="5 h"):
        guarda.conferir()
    guarda.fim_do_extra = AGORA + timedelta(hours=1)
    with pytest.raises(JanelaEsgotada):
        guarda.conferir()


def test_nunca_espera_a_janela_de_5h_abrir():
    guarda = _guarda()

    def bate():
        raise LimiteDoPlano("5h", tipo="five_hour", retoma_em=AGORA + timedelta(hours=2))

    with pytest.raises(Parada, match="não espera"):
        guarda.chamar(object(), bate)


def test_a_manual_continua_pausando_na_janela_de_5h():
    pausas: list[float] = []
    guarda = GuardaDoPlano(100, relogio=lambda: AGORA, dormir=pausas.append)
    chamadas = iter([LimiteDoPlano("5h", tipo="five_hour", retoma_em=AGORA + timedelta(hours=1)), None])

    def talvez():
        erro = next(chamadas)
        if erro:
            raise erro
        return "ok"

    assert guarda.chamar(object(), talvez) == "ok"
    assert pausas


@pytest.mark.parametrize(
    ("hora", "comeca"),
    [((1, 30), True), ((2, 25), True), ((2, 31), False), ((8, 10), False), ((1, 29), False)],
)
def test_nao_comeca_mais_de_1h_atrasada(hora, comeca):
    agora = datetime(2026, 11, 10, *hora, tzinfo=UTC)
    assert (_cacada_agendada_fora_de_hora("01:30", agora) == "") is comeca


def test_sem_horario_nao_comeca():
    assert "vazio" in _cacada_agendada_fora_de_hora("", AGORA)


def test_alternar_troca_freelas_e_vagas_a_cada_noite():
    hoje, amanha = date(2026, 11, 10), date(2026, 11, 11)
    assert _freelas_nesta_noite("alternar", hoje) != _freelas_nesta_noite("alternar", amanha)
    assert _freelas_nesta_noite("freelas", hoje) and _freelas_nesta_noite("freelas", amanha)
    assert not _freelas_nesta_noite("vagas", hoje)


def _freela(titulo: str, descricao: str = "") -> Oportunidade:
    return Oportunidade(id="x", fonte="t", tipo="freela", titulo=titulo, link="https://x", descricao=descricao)


def test_o_nicho_sistemas_casa_manutencao_e_erp_e_nao_landing_page():
    assert do_nicho(_freela("Manutenção de site em Laravel"), "sistemas")
    assert do_nicho(_freela("Website maintenance retainer"), "sistemas")
    assert do_nicho(_freela("Desenvolver ERP para distribuidora"), "sistemas")
    assert do_nicho(_freela("Integração de API com o sistema"), "sistemas")
    assert not do_nicho(_freela("Landing page para curso"), "sistemas")


def test_o_pedido_do_nicho_sistemas_abre_excecao_para_manutencao_mensal_e_erp_por_marco():
    pedido = pedido_de_freelas("sistemas")
    assert "manutenção mensal" in pedido
    assert "pago por marco" in pedido
    assert "e-commerce muito perto" not in pedido
    assert "e-commerce muito perto" in pedido_de_freelas("shopify")
