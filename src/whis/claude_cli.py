"""O que todo adaptador do Claude divide: a recusa da API paga, o stderr e o CLI.

Os adaptadores (`busca_claude`, `resumo_dia_claude`, `jornal_claude`) falam
com o Claude pelo **Claude Agent SDK**, que reusa as credenciais do `claude`
CLI — a conta Pro/Max do usuário por OAuth, como manda o ADR 0001. Cada um
recusa a chamada com o seu próprio erro quando uma variável de
`VARIAVEIS_DE_API_PAGA` está no ambiente.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_log = logging.getLogger("whis.claude")

#: Variáveis de ambiente que fariam o Claude cobrar por fora da conta do usuário.
VARIAVEIS_DE_API_PAGA = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: O prazo para o CLI responder ao `initialize` do SDK. O SDK espera 60 s, e em 18/10 o CLI desta
#: máquina levava de 32 a 48 s só para dizer "ok"; com o Chrome da caçada aberto, passou dos 60 e a
#: proposta automática caiu com "Control request timeout: initialize".
INICIO_DO_CLI_MS = 180_000


def dar_tempo_ao_inicio() -> None:
    """O SDK lê o prazo do `initialize` de `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` no ambiente deste processo."""
    os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", str(INICIO_DO_CLI_MS))


dar_tempo_ao_inicio()


def registrar_stderr_do_cli(linha: str) -> None:
    """O stderr do CLI do Claude vai ao log, em vez de ser herdado do pai.

    Sem este callback o SDK deixa o filho herdar o stderr do processo — e sob
    `pythonw.exe` não há stderr: o Python entrega ao filho um pipe com a ponta
    de leitura fechada, e um processo Node que escreva uma linha ali morre com
    EPIPE no meio da pergunta (provado com `node -e` sob `pythonw`, ticket 13).
    Com o callback, o SDK abre um pipe de verdade e lê o que vier.
    """
    _log.debug("claude: %s", linha.rstrip())


def caminho_do_cli(preenchido: str | None) -> str | None:
    """O `claude.exe` que os adaptadores do jornal passam como `cli_path`.

    O CLI empacotado no SDK (2.1.238) não emite as janelas do plano e deixa o
    `files` do Artifact atrás de um flag remoto; o do npm (2.1.274 ou mais novo)
    faz as duas coisas. Vence `[claude].cli` preenchido; sem ele, o do npm se
    existir; senão `None`, e o SDK cai no empacotado. O `.cmd` do npm não serve:
    o SDK recusa script batch, tem de ser o `.exe` de `bin/`.
    """
    if preenchido and preenchido.strip():
        return preenchido.strip()
    from .plataforma import WINDOWS

    if not WINDOWS:
        # No Mac o SDK acha o `claude` do PATH (instalador nativo ou npm) sozinho.
        import shutil

        return shutil.which("claude")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    do_npm = (
        Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    )
    return str(do_npm) if do_npm.is_file() else None
