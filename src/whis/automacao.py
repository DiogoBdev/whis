r"""As amarrações com o Windows: subir com o logon, agendar, avisar.

Enquanto a bandeja depender de alguém digitar um comando, a palheta não está
no atalho quando o usuário precisa dela.

Este módulo faz poucas coisas, todas **explícitas e reversíveis**:

- `instalar_inicializacao()` põe um atalho na pasta de Inicialização do usuário,
  apontando para `pythonw.exe -m whis bandeja` (sem console piscando), e outro
  no Menu Iniciar com a AUMID — sem esse segundo, o Windows recusa qualquer
  toast de um app não empacotado;
- `agendar_daily()` registra a tarefa `Whis-Daily`, com um gatilho semanal por
  dia da tabela `[daily].horarios`, rodando `whis daily --agendada` (que sai
  sem gerar nada quando `[daily].ativa` é falso);
- `agendar_noticias()` registra a tarefa diária `Whis-Noticias` no horário de
  `[noticias].horario`, rodando `whis news --agendada` (que sai sem gerar nada
  quando `[noticias].ativa` é falso);
- `agendar_reunioes()` registra a tarefa diária `Whis-Reunioes` no horário de
  `[granola].horario`, rodando `whis meetings --agendada` (que nunca abre o
  navegador para o login da Granola);
- `agendar_vigia()` registra `Whis-Vigia`, um script PowerShell autossuficiente
  que consulta o Agendador e dispara toast quando uma peça do Whis não subiu;
- `agendar_vigia_de_freelas()` registra `Whis-Vigia-Freelas`, que várias vezes
  por dia roda `whis vigia-freelas --agendada` e avisa o projeto novo do nicho;
- `agendar_procura()` registra `Whis-Procura`, que uma vez por dia roda
  `whis procurar --agendada` e busca agências e lojas quando a fila acaba;
- `agendar_posts_do_instagram()` registra `Whis-Instagram`, que todo dia às 19:00
  roda `whis instagram-postar --agendada` e publica o post do dia da @velozo.dev;
- `remover_legado()` tira da máquina o atalho e as tarefas que chamam um
  módulo ou um comando que não existe mais.

Nada disso acontece sozinho: são subcomandos que o usuário chama, e cada um tem
o par `--remover`.

As tarefas são registradas pelo módulo PowerShell `ScheduledTasks`, e não por
`schtasks`: só ele liga `-StartWhenAvailable`, sem o qual a daily de um dia em
que o PC estava desligado no horário simplesmente não acontece. O registro mora
em `agendar_tarefa.ps1`, ao lado deste módulo.

O trabalho sujo (criar `.lnk`, falar com o Agendador) sai por `executar`, um
callable injetável — é o que permite provar o comando montado sem mexer no
Windows da máquina de teste.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import launchd as _launchd
from . import sem_console as _sem_console
from .plataforma import MACOS
from .configuracao import Configuracao, caminho_padrao
from .fontes import analisar_hora

_log = logging.getLogger("whis.automacao")

NOME_DO_ATALHO = "Whis.lnk"
NOME_DO_ATALHO_DO_ATENDENTE = "Whis Atendente.lnk"
NOME_DO_ATALHO_DO_MENU = "Whis.lnk"
NOME_DA_TAREFA_DA_DAILY = "Whis-Daily"
NOME_DA_TAREFA_DO_VIGIA = "Whis-Vigia"
NOME_DA_TAREFA_DO_JORNAL = "Whis-Noticias"
NOME_DA_TAREFA_DAS_REUNIOES = "Whis-Reunioes"
NOME_DA_TAREFA_DO_LEMBRETE = "Whis-Lembrete-Cacada"
NOME_DA_TAREFA_DA_VIGIA_DE_FREELAS = "Whis-Vigia-Freelas"
NOME_DA_TAREFA_DA_PROCURA = "Whis-Procura"
NOME_DA_TAREFA_DO_INSTAGRAM = "Whis-Instagram"
HORA_DOS_POSTS_DO_INSTAGRAM = "19:00"
#: A busca de agências e a de lojas chamam o Claude com busca na web (até 40 e 30 min) e medem as lojas.
LIMITE_DA_PROCURA_MIN = 90

#: O script que registra uma tarefa pelo módulo `ScheduledTasks`, com os
#: gatilhos como `Dia=HH:MM` (dia em inglês, ou `Daily`) separados por vírgula.
SCRIPT_DE_AGENDAMENTO = Path(__file__).with_name("agendar_tarefa.ps1")

#: O vigia: não chama Python, não lê `config.toml`, não importa nada do Whis.
SCRIPT_DO_VIGIA = Path(__file__).with_name("vigia.ps1")

#: Quanto depois de cada daily o vigia confere se ela nasceu.
ATRASO_DO_VIGIA = timedelta(minutes=10)

#: A ronda diária do vigia, independente da daily — é a que pega a bandeja morta.
HORA_DA_RONDA_DO_VIGIA = "09:00"

#: `New-ScheduledTaskTrigger -DaysOfWeek` só fala inglês.
_DIAS_EM_INGLES = {
    "segunda": "Monday",
    "terca": "Tuesday",
    "quarta": "Wednesday",
    "quinta": "Thursday",
    "sexta": "Friday",
    "sabado": "Saturday",
    "domingo": "Sunday",
}

#: O Application User Model ID do Whis. É a identidade que o Windows exige de um
#: app não empacotado para mostrar toast: precisa existir um atalho no Menu
#: Iniciar com esta propriedade, e o toast é disparado em nome dela.
AUMID = "Whis.Agente"

#: O script que grava o `.lnk` com a AUMID — o `WScript.Shell` não sabe fazê-lo.
SCRIPT_DO_ATALHO_COM_AUMID = Path(__file__).with_name("atalho_com_aumid.ps1")

#: O atalho do nome que o projeto usava antes de virar Whis, em 2026-09-09: chama
#: `-m second_brain`, que não existe mais, e falha calado desde então.
NOME_DO_ATALHO_LEGADO = "Second Brain.lnk"

#: Tarefas que chamam o que não existe mais: `SecondBrain-Limpeza`, do nome
#: antigo (`-m second_brain`), e `Whis-Limpeza`, que rodava `whis limpar` até a
#: gravação sair do Whis, em 2026-09-30.
TAREFAS_LEGADAS = ("SecondBrain-Limpeza", "Whis-Limpeza")

#: Assinatura de quem executa um processo externo (injetável em teste).
Executor = Callable[[list[str]], subprocess.CompletedProcess]


class ErroDeAutomacao(RuntimeError):
    """O Windows recusou criar o atalho ou a tarefa agendada."""


def _rodar(comando: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        comando, capture_output=True, text=True, check=False, **_sem_console.opcoes()
    )


# -- subir com o Windows ----------------------------------------------------


def pasta_de_inicializacao() -> Path:
    """`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup`."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def caminho_do_atalho() -> Path:
    if MACOS:
        return _launchd.caminho_do_plist("bandeja")
    return pasta_de_inicializacao() / NOME_DO_ATALHO


def caminho_do_atalho_do_atendente() -> Path:
    if MACOS:
        return _launchd.caminho_do_plist("atendente")
    return pasta_de_inicializacao() / NOME_DO_ATALHO_DO_ATENDENTE


def pasta_do_menu_iniciar() -> Path:
    """`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs` — onde o toast procura a AUMID."""
    return pasta_de_inicializacao().parent


def caminho_do_atalho_do_menu() -> Path:
    return pasta_do_menu_iniciar() / NOME_DO_ATALHO_DO_MENU


def caminho_do_atalho_legado() -> Path:
    return pasta_de_inicializacao() / NOME_DO_ATALHO_LEGADO


def interpretador_sem_console() -> Path:
    """`pythonw.exe` do ambiente atual — o que não abre janela preta no logon.

    Cai para o `python.exe` corrente se o `pythonw` não existir: melhor uma
    bandeja com console piscando do que bandeja nenhuma.
    """
    atual = Path(sys.executable)
    sem_console = atual.with_name("pythonw.exe")
    return sem_console if sem_console.is_file() else atual


def instalar_inicializacao(*, executar: Executor = _rodar) -> Path:
    """Cria o atalho de inicialização da bandeja e devolve onde ele ficou.

    Cria também o atalho do Menu Iniciar com a AUMID. São dois porque servem a
    coisas diferentes: o da Inicialização faz a bandeja subir no logon; o do
    Menu Iniciar é o que autoriza o Whis a mostrar toast, e o usuário ainda
    ganha um "Whis" na busca do Windows.
    """
    atalho = _criar_atalho(
        caminho_do_atalho(),
        argumentos="-m whis bandeja",
        descricao="Whis — a palheta na bandeja",
        executar=executar,
    )
    _criar_atalho_com_aumid(
        caminho_do_atalho_do_menu(),
        argumentos="-m whis bandeja",
        descricao="Whis — o agente na bandeja",
        executar=executar,
    )
    return atalho


def _criar_atalho_com_aumid(
    destino: Path, *, argumentos: str, descricao: str, executar: Executor
) -> Path:
    """Escreve um `.lnk` com `System.AppUserModel.ID` pelo script empacotado.

    O `WScript.Shell` não grava essa propriedade; é preciso abrir o atalho como
    `IPropertyStore`, e isso mora no `.ps1` ao lado deste módulo. No Mac não
    há AUMID: a notificação sai sem atalho nenhum.
    """
    if MACOS:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    resultado = executar(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_DO_ATALHO_COM_AUMID),
            "-Atalho",
            str(destino),
            "-Alvo",
            str(interpretador_sem_console()),
            "-Argumentos",
            argumentos,
            "-Diretorio",
            str(Path(__file__).resolve().parents[2]),
            "-Descricao",
            descricao,
            "-Aumid",
            AUMID,
        ]
    )
    if resultado.returncode != 0:
        raise ErroDeAutomacao(
            f"não foi possível criar o atalho com AUMID em {destino}: "
            f"{(resultado.stderr or resultado.stdout or '').strip()}"
        )
    _log.info("Atalho do Menu Iniciar criado em %s (AUMID %s)", destino, AUMID)
    return destino


def instalar_atendente(*, executar: Executor = _rodar) -> Path:
    """Cria o atalho de inicialização do atendente, ao lado do da bandeja.

    É um atalho separado, e não um argumento a mais no da bandeja, porque são
    dois processos, cada um com a sua trava de instância.
    """
    return _criar_atalho(
        caminho_do_atalho_do_atendente(),
        argumentos="-m whis atender",
        descricao="Whis — o agente na DM do Slack",
        executar=executar,
    )


def _criar_atalho(destino: Path, *, argumentos: str, descricao: str, executar: Executor) -> Path:
    """Escreve um `.lnk` na pasta de Inicialização apontando para o pacote.

    O `.lnk` é escrito pelo `WScript.Shell` via PowerShell: é a forma de criar
    atalho no Windows sem dependência nova (o ADR 0001 pede projeto fino), e o
    resultado é um atalho de verdade — o usuário vê, move e apaga pelo Explorer.
    """
    if MACOS:
        import shlex

        return _launchd.escrever_e_carregar(
            destino.stem,
            [str(interpretador_sem_console()), *shlex.split(argumentos)],
            diretorio=_diretorio_do_projeto(),
            gatilhos=None,
            executar=executar,
            destino=destino,
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    interpretador = interpretador_sem_console()
    diretorio = Path(__file__).resolve().parents[2]

    script = (
        "$atalho = (New-Object -ComObject WScript.Shell)."
        f"CreateShortcut('{destino}'); "
        f"$atalho.TargetPath = '{interpretador}'; "
        f"$atalho.Arguments = '{argumentos}'; "
        f"$atalho.WorkingDirectory = '{diretorio}'; "
        f"$atalho.Description = '{descricao}'; "
        "$atalho.Save()"
    )
    resultado = executar(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    )
    if resultado.returncode != 0:
        raise ErroDeAutomacao(
            f"não foi possível criar o atalho em {destino}: "
            f"{(resultado.stderr or resultado.stdout or '').strip()}"
        )
    _log.info("Atalho de inicialização criado em %s", destino)
    return destino


def remover_inicializacao() -> Path | None:
    """Tira os atalhos da bandeja (Inicialização e Menu Iniciar).

    Devolve o caminho do atalho de Inicialização removido, ou `None` se ele não
    existia — o do Menu Iniciar vai junto, em silêncio.
    """
    _remover_atalho(caminho_do_atalho_do_menu())
    return _remover_atalho(caminho_do_atalho())


def remover_atendente() -> Path | None:
    """Tira o atalho do atendente da inicialização. Devolve o caminho removido, ou `None`."""
    return _remover_atalho(caminho_do_atalho_do_atendente())


def _remover_atalho(destino: Path) -> Path | None:
    if MACOS:
        return destino if _launchd.remover(destino, executar=_rodar) else None
    if not destino.exists():
        return None
    destino.unlink()
    _log.info("Atalho de inicialização removido de %s", destino)
    return destino


def inicializacao_instalada() -> bool:
    return caminho_do_atalho().exists()


def atendente_instalado() -> bool:
    return caminho_do_atalho_do_atendente().exists()


# -- as tarefas agendadas ---------------------------------------------------


def caminho_do_powershell() -> str:
    """O `powershell.exe` 5.1 do Windows, por caminho completo.

    Uma tarefa agendada não herda o `PATH` da sessão de quem a criou; o caminho
    completo é o que garante que o vigia sobe em qualquer logon.
    """
    raiz = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    completo = raiz / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(completo) if completo.is_file() else "powershell.exe"


def _diretorio_do_projeto() -> Path:
    return Path(__file__).resolve().parents[2]


def _registrar_tarefa(
    nome: str,
    *,
    executavel: str,
    argumentos: str,
    gatilhos: list[str],
    executar: Executor,
    limite_minutos: int | None = None,
) -> None:
    """Registra (ou substitui) uma tarefa pelo `agendar_tarefa.ps1`.

    Os gatilhos vão numa string só, separados por vírgula: em modo `-File` o
    PowerShell entrega cada argumento como texto literal, e um `[string[]]`
    receberia só o primeiro. Sem `limite_minutos`, o Agendador para a tarefa em 30 min.
    """
    if not gatilhos:
        raise ErroDeAutomacao(f"a tarefa '{nome}' precisa de pelo menos um gatilho.")
    if MACOS:
        _launchd.registrar_tarefa(
            nome,
            executavel=executavel,
            argumentos=argumentos,
            diretorio=_diretorio_do_projeto(),
            gatilhos=gatilhos,
            executar=executar,
        )
        return
    limite = ["-LimiteMinutos", str(limite_minutos)] if limite_minutos else []
    resultado = executar(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_DE_AGENDAMENTO),
            "-Nome",
            nome,
            "-Executavel",
            executavel,
            "-Argumentos",
            argumentos,
            "-Diretorio",
            str(_diretorio_do_projeto()),
            "-Gatilhos",
            ",".join(gatilhos),
            *limite,
        ]
    )
    if resultado.returncode != 0:
        raise ErroDeAutomacao(
            f"não foi possível registrar a tarefa '{nome}' no Agendador: "
            f"{(resultado.stderr or resultado.stdout or '').strip()}"
        )
    _log.info("Tarefa '%s' registrada com gatilhos %s", nome, ", ".join(gatilhos))


def _desregistrar_tarefa(nome: str, *, executar: Executor) -> bool:
    if MACOS:
        return _launchd.desregistrar_tarefa(nome, executar=executar)
    resultado = executar(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Unregister-ScheduledTask -TaskName '{nome}' -Confirm:$false -ErrorAction Stop",
        ]
    )
    if resultado.returncode == 0:
        _log.info("Tarefa '%s' removida do Agendador.", nome)
        return True
    return False


def _tarefa_agendada(nome: str, *, executar: Executor) -> bool:
    if MACOS:
        return _launchd.tarefa_agendada(nome, executar=executar)
    resultado = executar(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Get-ScheduledTask -TaskName '{nome}' -ErrorAction Stop | Out-Null",
        ]
    )
    return resultado.returncode == 0


def _argumentos_do_whis(subcomando: str, config: Path) -> str:
    """`-m whis <subcomando> --config "<arquivo>"` — sempre explícito.

    A tarefa agendada não herda `WHIS_CONFIG` da sessão de quem a criou; sem o
    caminho no comando, ela leria outro arquivo (ou nenhum) e falharia calada.
    """
    return f'-m whis {subcomando} --config "{config}"'


# -- a daily agendada -------------------------------------------------------


def gatilhos_da_daily(configuracao: Configuracao) -> list[str]:
    """Um `Dia=HH:MM` por dia da tabela `[daily].horarios`, em inglês."""
    return [f"{_DIAS_EM_INGLES[dia]}={hora}" for dia, hora in configuracao.daily_horarios]


def agendar_daily(configuracao: Configuracao, *, executar: Executor = _rodar) -> str:
    """Registra `Whis-Daily`: um gatilho semanal por dia configurado.

    A hora da tabela já é a hora em que a nota deve nascer (13:55 para a
    reunião das 14:00); nada é descontado aqui. Com `-StartWhenAvailable`, o
    dia em que o PC estava desligado às 13:55 ganha a daily assim que ligar.
    """
    gatilhos = gatilhos_da_daily(configuracao)
    if not gatilhos:
        raise ErroDeAutomacao(
            "a tabela [daily].horarios está vazia — não há dia nem hora para agendar. "
            'Preencha-a no config.toml (por exemplo horarios = { segunda = "13:55" }).'
        )
    _registrar_tarefa(
        NOME_DA_TAREFA_DA_DAILY,
        executavel=str(interpretador_sem_console()),
        argumentos=_argumentos_do_whis(
            "daily --agendada", configuracao.origem or caminho_padrao()
        ),
        gatilhos=gatilhos,
        executar=executar,
    )
    return NOME_DA_TAREFA_DA_DAILY


def desagendar_daily(*, executar: Executor = _rodar) -> bool:
    return _desregistrar_tarefa(NOME_DA_TAREFA_DA_DAILY, executar=executar)


def daily_agendada(*, executar: Executor = _rodar) -> bool:
    return _tarefa_agendada(NOME_DA_TAREFA_DA_DAILY, executar=executar)


# -- o jornal agendado ------------------------------------------------------


def agendar_noticias(configuracao: Configuracao, *, executar: Executor = _rodar) -> str:
    """Registra `Whis-Noticias`: um gatilho diário em `[noticias].horario`.

    A edição leva uns 7 minutos, folgada no teto de 30 min do
    `agendar_tarefa.ps1`. Com `-StartWhenAvailable`, a manhã em que o PC estava
    desligado ganha o jornal assim que ele ligar.
    """
    _registrar_tarefa(
        NOME_DA_TAREFA_DO_JORNAL,
        executavel=str(interpretador_sem_console()),
        argumentos=_argumentos_do_whis(
            "news --agendada", configuracao.origem or caminho_padrao()
        ),
        gatilhos=[f"Daily={configuracao.noticias_horario}"],
        executar=executar,
    )
    return NOME_DA_TAREFA_DO_JORNAL


def desagendar_noticias(*, executar: Executor = _rodar) -> bool:
    return _desregistrar_tarefa(NOME_DA_TAREFA_DO_JORNAL, executar=executar)


def noticias_agendada(*, executar: Executor = _rodar) -> bool:
    return _tarefa_agendada(NOME_DA_TAREFA_DO_JORNAL, executar=executar)


# -- as reuniões da Granola -------------------------------------------------


def agendar_reunioes(configuracao: Configuracao, *, executar: Executor = _rodar) -> str:
    """Registra `Whis-Reunioes`: um gatilho diário em `[granola].horario`.

    Com `-StartWhenAvailable`, o dia em que o PC estava desligado na hora puxa
    assim que ele ligar — o que importa porque a Granola grátis só mostra 30
    dias de reuniões.
    """
    _registrar_tarefa(
        NOME_DA_TAREFA_DAS_REUNIOES,
        executavel=str(interpretador_sem_console()),
        argumentos=_argumentos_do_whis(
            "meetings --agendada", configuracao.origem or caminho_padrao()
        ),
        gatilhos=[f"Daily={configuracao.granola_horario}"],
        executar=executar,
    )
    return NOME_DA_TAREFA_DAS_REUNIOES


def desagendar_reunioes(*, executar: Executor = _rodar) -> bool:
    return _desregistrar_tarefa(NOME_DA_TAREFA_DAS_REUNIOES, executar=executar)


def reunioes_agendada(*, executar: Executor = _rodar) -> bool:
    return _tarefa_agendada(NOME_DA_TAREFA_DAS_REUNIOES, executar=executar)


# -- o vigia ----------------------------------------------------------------


def agendar_lembrete(configuracao: Configuracao, *, executar: Executor = _rodar) -> str:
    """Registra `Whis-Lembrete-Cacada`: todo dia em `[cacada].hora_do_lembrete`, um toast se a semana vira logo.

    Só lembra: a caçada continua sendo só quando ele chama.
    """
    _registrar_tarefa(
        NOME_DA_TAREFA_DO_LEMBRETE,
        executavel=str(interpretador_sem_console()),
        argumentos=_argumentos_do_whis("lembrete --agendada", configuracao.origem or caminho_padrao()),
        gatilhos=[f"Daily={configuracao.cacada_hora_do_lembrete}"],
        executar=executar,
    )
    return NOME_DA_TAREFA_DO_LEMBRETE


def desagendar_lembrete(*, executar: Executor = _rodar) -> bool:
    return _desregistrar_tarefa(NOME_DA_TAREFA_DO_LEMBRETE, executar=executar)


def agendar_vigia_de_freelas(configuracao: Configuracao, *, executar: Executor = _rodar) -> str:
    """Registra `Whis-Vigia-Freelas`: um gatilho diário em cada hora de `[cacada].horarios_da_vigia_de_freelas`.

    São vários gatilhos diários, e não uma repetição, porque o
    `agendar_tarefa.ps1` só sabe `Dia=HH:MM`; de madrugada ela não acorda o PC à toa.
    """
    _registrar_tarefa(
        NOME_DA_TAREFA_DA_VIGIA_DE_FREELAS,
        executavel=str(interpretador_sem_console()),
        argumentos=_argumentos_do_whis("vigia-freelas --agendada", configuracao.origem or caminho_padrao()),
        gatilhos=[f"Daily={hora}" for hora in configuracao.cacada_horarios_da_vigia_de_freelas],
        executar=executar,
    )
    return NOME_DA_TAREFA_DA_VIGIA_DE_FREELAS


def desagendar_vigia_de_freelas(*, executar: Executor = _rodar) -> bool:
    return _desregistrar_tarefa(NOME_DA_TAREFA_DA_VIGIA_DE_FREELAS, executar=executar)


def vigia_de_freelas_agendada(*, executar: Executor = _rodar) -> bool:
    return _tarefa_agendada(NOME_DA_TAREFA_DA_VIGIA_DE_FREELAS, executar=executar)


def agendar_procura(configuracao: Configuracao, *, executar: Executor = _rodar) -> str:
    """Registra `Whis-Procura`: um gatilho diário em `[cacada].horario_da_procura`, com 90 min de teto.

    Fora da vigia porque a busca na web passa dos 30 min que a vigia tem. Com
    `-StartWhenAvailable`, o dia em que o PC estava desligado no horário busca
    assim que ele ligar; sem fila curta, ela termina em segundos.
    """
    _registrar_tarefa(
        NOME_DA_TAREFA_DA_PROCURA,
        executavel=str(interpretador_sem_console()),
        argumentos=_argumentos_do_whis("procurar --agendada", configuracao.origem or caminho_padrao()),
        gatilhos=[f"Daily={configuracao.cacada_horario_da_procura}"],
        executar=executar,
        limite_minutos=LIMITE_DA_PROCURA_MIN,
    )
    return NOME_DA_TAREFA_DA_PROCURA


def desagendar_procura(*, executar: Executor = _rodar) -> bool:
    return _desregistrar_tarefa(NOME_DA_TAREFA_DA_PROCURA, executar=executar)


def agendar_posts_do_instagram(configuracao: Configuracao, *, executar: Executor = _rodar) -> str:
    """Registra `Whis-Instagram`: todo dia às 19:00, o Reel ou o carrossel do dia do calendário do velozo-reels.

    Dia sem item termina sem abrir o Chrome. Com `-StartWhenAvailable`, o PC ligado depois das 19h
    ainda posta o item do mesmo dia; o de um dia que passou só vira aviso.
    """
    _registrar_tarefa(
        NOME_DA_TAREFA_DO_INSTAGRAM,
        executavel=str(interpretador_sem_console()),
        argumentos=_argumentos_do_whis("instagram-postar --agendada", configuracao.origem or caminho_padrao()),
        gatilhos=[f"Daily={HORA_DOS_POSTS_DO_INSTAGRAM}"],
        executar=executar,
    )
    return NOME_DA_TAREFA_DO_INSTAGRAM


def desagendar_posts_do_instagram(*, executar: Executor = _rodar) -> bool:
    return _desregistrar_tarefa(NOME_DA_TAREFA_DO_INSTAGRAM, executar=executar)


def _depois(hora: str, atraso: timedelta) -> str:
    """`HH:MM` somado ao atraso, sem virar o dia.

    `23:55` + 10 min dá `00:05` **no mesmo dia da semana** do gatilho — o
    vigia rodaria antes da daily, não depois. É um caso que a tabela do usuário
    não tem, e virar o gatilho para o dia seguinte custaria mais do que vale.
    """
    momento = datetime.combine(date.min, analisar_hora(hora)) + atraso
    return momento.strftime("%H:%M")


def gatilhos_do_vigia(configuracao: Configuracao) -> list[str]:
    """Dez minutos depois de cada daily, a ronda diária e o logon, que é quando a bandeja pode não ter subido."""
    gatilhos = [
        f"{_DIAS_EM_INGLES[dia]}={_depois(hora, ATRASO_DO_VIGIA)}"
        for dia, hora in configuracao.daily_horarios
    ]
    gatilhos.append(f"Daily={HORA_DA_RONDA_DO_VIGIA}")
    gatilhos.append("Logon")
    return gatilhos


def tarefas_vigiadas(*, executar: Executor = _rodar) -> list[str]:
    """`Whis-Daily` sempre; `Whis-Noticias` e `Whis-Reunioes` só se existem no Agendador.

    Passar um nome que não existe faria o vigia reclamar de uma tarefa que o
    usuário nunca pediu.
    """
    nomes = [NOME_DA_TAREFA_DA_DAILY]
    if noticias_agendada(executar=executar):
        nomes.append(NOME_DA_TAREFA_DO_JORNAL)
    if reunioes_agendada(executar=executar):
        nomes.append(NOME_DA_TAREFA_DAS_REUNIOES)
    return nomes


def agendar_vigia(configuracao: Configuracao, *, executar: Executor = _rodar) -> list[str]:
    """Registra `Whis-Vigia` e devolve os nomes que ele passa a vigiar.

    O vigia roda `vigia.ps1` direto pelo `powershell.exe`, sem Python no
    caminho: o que ele vigia é o Whis morrendo, e um vigia que dependesse do
    Whis morreria junto.
    """
    vigiadas = tarefas_vigiadas(executar=executar)
    if MACOS:
        # o vigia.sh, irmão do vigia.ps1: zsh puro, sem Python
        _registrar_tarefa(
            NOME_DA_TAREFA_DO_VIGIA,
            executavel="/bin/zsh",
            argumentos=(
                f'"{SCRIPT_DO_VIGIA.with_suffix(".sh")}" {caminho_do_atalho().stem} '
                + ",".join(_launchd.rotulo(nome) for nome in vigiadas)
            ),
            gatilhos=gatilhos_do_vigia(configuracao),
            executar=executar,
        )
        return vigiadas
    argumentos = (
        f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{SCRIPT_DO_VIGIA}" '
        f"-Aumid {AUMID} -Tarefas {','.join(vigiadas)}"
    )
    _registrar_tarefa(
        NOME_DA_TAREFA_DO_VIGIA,
        executavel=caminho_do_powershell(),
        argumentos=argumentos,
        gatilhos=gatilhos_do_vigia(configuracao),
        executar=executar,
    )
    return vigiadas


def desagendar_vigia(*, executar: Executor = _rodar) -> bool:
    return _desregistrar_tarefa(NOME_DA_TAREFA_DO_VIGIA, executar=executar)


def vigia_agendado(*, executar: Executor = _rodar) -> bool:
    return _tarefa_agendada(NOME_DA_TAREFA_DO_VIGIA, executar=executar)


# -- o nome antigo ----------------------------------------------------------


@dataclass(frozen=True)
class Legado:
    """O que `remover_legado` encontrou e tirou da máquina."""

    atalho: Path | None
    tarefas: tuple[str, ...]


def remover_legado(*, executar: Executor = _rodar) -> Legado:
    """Apaga o atalho `Second Brain.lnk` e as tarefas de `TAREFAS_LEGADAS`.

    Todos chamam o que não existe mais: o atalho faz a bandeja não subir no
    logon e cada tarefa falha todo dia no horário dela — sem console, logo sem erro
    em lugar nenhum. Nada é recriado aqui; o atalho novo é de
    `instalar_inicializacao`. `tarefas` são as que existiam e saíram.
    """
    if MACOS:  # o nome antigo nunca existiu no Mac
        return Legado(atalho=None, tarefas=())
    atalho = _remover_atalho(caminho_do_atalho_legado())
    removidas = []
    for nome in TAREFAS_LEGADAS:
        if executar(["schtasks", "/Delete", "/TN", nome, "/F"]).returncode == 0:
            _log.info("Tarefa legada '%s' removida do Agendador.", nome)
            removidas.append(nome)
    return Legado(atalho=atalho, tarefas=tuple(removidas))
