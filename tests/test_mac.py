"""O caminho do macOS: launchd no lugar do Agendador, Carbon no lugar do RegisterHotKey, notificação do sistema."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from whis import automacao, launchd, plataforma
from whis.atalho_global import _para_o_mac, analisar_atalho


@pytest.fixture
def no_mac(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Religa o caminho do Mac e guarda os comandos que iriam ao `launchctl`."""
    monkeypatch.setattr(plataforma, "MACOS", True)
    monkeypatch.setattr(automacao, "MACOS", True)
    return []


def _executor(chamadas: list[list[str]]):
    def executar(comando: list[str]) -> subprocess.CompletedProcess:
        chamadas.append(comando)
        return subprocess.CompletedProcess(comando, 0, "", "")

    return executar


def test_gatilhos_viram_start_calendar_interval():
    calendario, no_logon = launchd.intervalos(["Monday=13:55", "Daily=07:05", "Logon"])
    assert calendario == [{"Hour": 13, "Minute": 55, "Weekday": 1}, {"Hour": 7, "Minute": 5}]
    assert no_logon is True


def test_a_tarefa_vira_um_plist_carregado_no_launchd(no_mac, configuracao):
    executar = _executor(no_mac)
    automacao.agendar_noticias(configuracao, executar=executar)

    plist = launchd.caminho_do_plist(automacao.NOME_DA_TAREFA_DO_JORNAL)
    dados = plistlib.loads(plist.read_bytes())
    assert dados["Label"] == "dev.whis.whis-noticias"
    assert dados["ProgramArguments"][1:4] == ["-m", "whis", "news"]
    assert "--agendada" in dados["ProgramArguments"]
    assert dados["StartCalendarInterval"][0].keys() == {"Hour", "Minute"}
    assert ["launchctl", "bootstrap"] == no_mac[-1][:2]

    assert automacao.desagendar_noticias(executar=executar) is True
    assert not plist.exists()


def test_a_bandeja_sobe_no_login_por_um_agente_com_run_at_load(no_mac):
    destino = automacao.instalar_inicializacao(executar=_executor(no_mac))
    assert destino == launchd.caminho_do_plist("bandeja")
    dados = plistlib.loads(Path(destino).read_bytes())
    assert dados["RunAtLoad"] is True
    assert dados["ProgramArguments"][-2:] == ["whis", "bandeja"]


def test_o_aviso_do_mac_usa_osascript_sem_terminal_notifier(monkeypatch):
    monkeypatch.setattr(plataforma.shutil, "which", lambda _nome: None)
    comando = plataforma.comando_de_aviso_mac('Daily "pronta"', "abre", "obsidian://x")
    assert comando[0] == "osascript"
    assert '\\"pronta\\"' in comando[2]


def test_o_aviso_do_mac_abre_a_uri_pelo_terminal_notifier(monkeypatch):
    monkeypatch.setattr(plataforma.shutil, "which", lambda _nome: "/opt/homebrew/bin/terminal-notifier")
    comando = plataforma.comando_de_aviso_mac("t", "x", "obsidian://open?vault=v")
    assert comando[-2:] == ["-open", "obsidian://open?vault=v"]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("alt+space", (0x0800, 0x31)), ("cmd+shift+k", (0x0100 | 0x0200, 0x28)), ("ctrl+f5", (0x1000, 0x60))],
)
def test_o_atalho_vira_modificadores_e_tecla_do_carbon(texto, esperado):
    assert _para_o_mac(analisar_atalho(texto)) == esperado
