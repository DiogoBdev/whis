# Whis

A personal assistant that lives in the Windows system tray, keeps its memory in an Obsidian vault, and does its thinking with the Claude Agent SDK on the owner's own Claude subscription.

Whis is a single-user tool built for one developer's day: a command palette on a global hotkey, the daily stand-up turned into slides, a morning news edition, meeting notes pulled from Granola, and a long-running job and freelance hunt. Everything it produces is a Markdown note in the vault; the only servers involved are the ones the owner already uses.

The product surface (palette descriptions, notes, config comments, docs) is in Portuguese, because that is the language its user works in. The code is Python.

## What it does

**Palette.** `Ctrl+Space` opens a Raycast-style window from any application: a text field and a list of actions. Commands fire and the window disappears; results arrive as notes in the vault and a Windows toast. `ask` answers a question from the vault (with citations), `open` jumps to a note in Obsidian, `prompts` and `stash` keep a library of reusable prompts in `prompts/`, and `settings` edits every key of the config file in a form.

**Daily.** The owner writes the stand-up in one note, `daily/atual.md` (sections *O que eu fiz*, *O que estou fazendo*, *Fazer*, *Dúvidas*, *Pontos*). Palette shortcuts `did`, `doing`, `done`, `note`, `doubt` and `do` append or move lines in that note. At the scheduled time Whis renders the note as a slide deck, republished at the same URL, and keeps a dated copy in `daily/`.

**Journal.** A daily news edition built from RSS/Atom feeds, an ESPN scoreboard, a Wikipedia "on this day" item and a *Dev with AI* section read from X through the owner's own logged-in Chrome (Playwright, no API). Sources live in `noticias/fontes.md`, which the owner edits. The edition is written as a note and published as a private page.

**Meetings.** A collector that pulls the last 30 days of meeting summaries from Granola over its official MCP server (OAuth login in the browser, tokens encrypted with DPAPI) and writes each one once to `reunioes/`.

**Hunt.** A long-running search for jobs and freelance gigs, started only by the owner. It reads public job APIs and listings (remote-job boards, Gupy, GitHub issue boards, Hacker News "Who is hiring", Workana, 99Freelas, Get on Board, Freelancer.com, LinkedIn public search, plus the open web via Claude), builds a profile from the local git history and the vault, triages every opportunity, and leaves a report plus a ready-to-send package for each good match (proposal, price, how to apply, and a concept page for web gigs). It pauses on the Claude plan's usage window and resumes from a checkpoint. Optional, off by default: creating accounts on the listed sites and submitting low-odds applications on Gupy and common ATSs, with the policy written in code (never freelance, never LinkedIn, never twice) and an append-only action log.

**LinkedIn.** Reads the owner's data export, writes a profile analysis and post drafts into `linkedin/`, and publishes a draft through the official API only on an explicit gesture.

**Also present.** A passive-source diary (`whis diario`) that summarises a day from local git, Linear and Slack; a Slack DM agent (`whis atender`) that answers questions from the vault over Socket Mode; and a watchdog scheduled task that raises a toast when a scheduled piece did not run.

## Architecture

- **Python 3.12**, Windows 11 only. The tray icon is `pystray`; the palette is a Tk window with a simulated acrylic background; the global hotkey is `RegisterHotKey` through `pywin32`.
- **Claude Agent SDK** for every model call, reusing the credentials of the installed `claude` CLI (the owner's Pro/Max subscription). Whis refuses to run a model call when `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set, so nothing is ever billed outside the subscription.
- **Obsidian vault** as the only durable store. Folders are split by ownership: the agent owns and may regenerate `diario/`, `daily/`, `respostas/`, `noticias/`, `reunioes/`, `cacada/`, `linkedin/`; `notas/` is the owner's and never touched; a few notes (`daily/atual.md`, `noticias/fontes.md`, `cacada/criterios.md`) are created once from a template and then belong to the owner.
- **Windows scheduled tasks** (`schtasks`, via PowerShell scripts shipped in the package) run the daily, the journal, the meeting pull, the hunt reminder and the watchdog. Each task calls `python -m whis <command> --agendada` and reports through toasts.
- **Ports and adapters.** Every external service (Claude, Linear, Slack, Granola, the browser) sits behind a small port with a fake implementation; `--fakes` runs any command end to end without network or subscription spend, and the test suite (`pytest`) only touches the real services in tests marked `rede`.
- Secrets created by Whis itself (site passwords, the LinkedIn token, Granola tokens) are encrypted with DPAPI and stored next to `config.toml` in `%APPDATA%\whis`; run state such as the hunt checkpoint and the action log lives in `%LOCALAPPDATA%\whis`.

## Getting started

Prerequisites: Windows 11, Python 3.12, Obsidian with a vault, and the Claude Code CLI installed and logged in to a Claude subscription. Google Chrome is needed for the X section of the journal and for the hunt's browser agent.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .[dev]

# writes %APPDATA%\whis\config.toml with every key commented
.venv\Scripts\python.exe -m whis init

# start the tray icon and the palette (Ctrl+Space)
.venv\Scripts\python.exe -m whis bandeja

# create the logon shortcut; add --daily, --news, --meetings, --hunt to schedule the tasks
.venv\Scripts\python.exe -m whis windows
```

### macOS

Whis also runs on macOS. The Windows pieces have Mac counterparts chosen in `src/whis/plataforma.py`:

| Windows | macOS |
| --- | --- |
| `%APPDATA%\whis` | `~/Library/Application Support/whis` |
| `%LOCALAPPDATA%\whis` | `~/Library/Application Support/whis/local` |
| DPAPI (`cofre.bin`, `granola.json`) | Fernet, with the key in the login Keychain (service `whis`) |
| toast | Notification Center (`terminal-notifier` if installed, so the button opens the note; otherwise `osascript`) |
| `RegisterHotKey`, `Ctrl+Space` | Carbon `RegisterEventHotKey`, `Alt+Space` (Option+Space) by default |
| scheduled tasks + Startup shortcut | LaunchAgents in `~/Library/LaunchAgents/dev.whis.*.plist` |
| `vigia.ps1` | `vigia.sh` |

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m whis init
.venv/bin/python -m whis bandeja
.venv/bin/python -m whis mac --daily --news   # same flags as `whis windows`
```

Pasting a prompt into the previous app (`prompts`) sends Cmd+V through System Events, which needs the terminal (or Python) to have Accessibility permission. `brew install terminal-notifier` makes notification clicks open the note in Obsidian.

The configuration file lives at `%APPDATA%\whis\config.toml` (`~/Library/Application Support/whis/config.toml` on macOS) (override with `WHIS_CONFIG`). The only required key is `vault`, the path of the Obsidian vault; every integration is off until its keys are filled in. `python -m whis init` writes the whole template with a comment per key, and `settings` in the palette edits it in place. Logs go to `bandeja.log` next to the config file.

Run the test suite with `.venv\Scripts\python.exe -m pytest -q`.

## Command-line reference

```
python -m whis init [--forcar]
python -m whis bandeja [--fakes]
python -m whis windows [--daily] [--news] [--meetings] [--hunt] [--atendente] [--remover] [--remover-legado]
python -m whis diario [YYYY-MM-DD] [--simular]
python -m whis daily [--anotar TEXTO] [--simular] [--fakes] [--agendada]
python -m whis news [--agendada] [--fakes] [--simular]
python -m whis x-login
python -m whis meetings [--agendada] [--fakes]
python -m whis hunt [--simular] [--fakes] [--do-zero] [--freelas]
python -m whis contas [--so SITE] [--senha SITE]
python -m whis navegador [url]
python -m whis lembrete [--agendada]
python -m whis candidaturas
python -m whis linkedin [--fakes]
python -m whis linkedin-login
python -m whis linkedin-publicar <nota>
python -m whis perguntar "<pergunta>" [--sem-fio]
python -m whis atender
```

`--fakes` swaps every adapter for its fake; `--simular` builds the output in a temporary folder without writing to the vault or publishing; `--agendada` is what the scheduled tasks pass (no browser, toast on completion). Exit codes distinguish "the note was written", "written but partial" and "not written", and are documented in each command's `--help`.

## Design principles

- **Local by default.** No server, no database, no background listener. Collectors run between an explicit start and stop; the only continuous process is the tray icon. Decision recorded in `docs/adr/0001`.
- **The vault is the interface.** Whis writes Markdown and the owner reads it in Obsidian, on the PC or the phone. Notes that the owner edits are never rewritten by the agent.
- **Who reads is not who acts.** Content fetched from the outside (job listings, e-mails, pages) reaches a model that has no tools to act; the decision of what may be sent automatically is a rule in `politica.py`, not a prompt. Every action taken outside the PC is written to an append-only log as an intention before it happens and gets its outcome afterwards (`docs/adr/0004`).
- **Spend only the subscription.** Model calls go through the owner's Claude plan; paid API keys in the environment are a hard error. Long runs watch the plan's usage window and stop before the weekly reset.
- **Honest exit codes and a watchdog outside the system.** Each scheduled command says whether its note was born, and a separate scheduled script checks that the pieces ran, because the thing that reports Whis is down cannot be Whis.
- **Built for agents to work on.** `AGENTS.md` points coding agents at `CONTEXT.md` (the domain glossary, in Portuguese), `docs/adr/` (decisions), `docs/smoke-*.md` (manual validation scripts for what tests cannot cover: scheduled tasks, toasts, real OAuth logins) and `.scratch/` (specs, research and issue tickets per feature, kept as the historical record of how each piece was decided).

## Repository layout

```
src/whis/        the package: one module per seam (bandeja, palheta, daily, jornal, cacada, ...)
tests/           pytest suite; fixtures under tests/fixtures
docs/adr/        architecture decision records
docs/smoke-*.md  manual validation scripts, one per integration
docs/agents/     conventions for coding agents (issue tracker, triage labels, domain docs)
CONTEXT.md       domain glossary: the words the code and the docs use
.scratch/        specs, research and tickets, by feature
```
