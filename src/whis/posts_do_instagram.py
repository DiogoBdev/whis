r"""Os posts da @velozo.dev no Instagram, publicados sozinhos no dia e na hora do calendário (pedido dele em 31/10).

Os Reels e os carrosséis são feitos no `velozo-reels`, e o `pagina/calendario.json` de lá diz o dia de
cada um (19h, de domingo a quinta o Reel e na sexta o carrossel). A tarefa `Whis-Instagram` roda todo dia
às 19:00 e publica o item do dia, um por dia no máximo, pelo instagram.com num Chrome com perfil só do
Whis (`%LOCALAPPDATA%\whis\navegador-instagram`), logado uma vez à mão com `whis instagram-login`, como o
X da ADR 0003. A API oficial da Meta substitui o navegador quando ele criar o app (ADR 0007): quem
publica é uma `PortaDePublicacao`, e o resto não muda.

O estado (`cacada\instagram-posts.json`) vira `enviando` antes do clique em Compartilhar. O que o
Instagram não confirmou vira `incerto` e nunca se repete. Falha antes do clique (login vencido, arquivo
que não subiu) manda um aviso só, sem nova tentativa. Item de um dia que passou não sai atrasado. Nunca
abre direct, curtida nem perfil de outra pessoa.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Protocol

_log = logging.getLogger("whis.posts_do_instagram")

ANO_DO_CALENDARIO = 2026
HORA_PADRAO = time(19, 0)
CALENDARIO = Path(r"C:\Users\gabri\development\velozo-reels\pagina\calendario.json")
TEXTOS = Path(r"C:\Users\gabri\development\velozo-reels")
NOME_DO_ESTADO = "instagram-posts.json"
USUARIO = "velozo.dev"

REEL = "reel"
CARROSSEL = "carrossel"

ENVIANDO = "enviando"
PUBLICADO = "publicado"
INCERTO = "incerto"
FALHOU = "falhou"
PERDIDO = "perdido"
#: O que conta como "já saiu hoje" e nunca é repetido sozinho.
SAIU_OU_TALVEZ = (ENVIANDO, PUBLICADO, INCERTO)

#: Publicados à mão antes do Whis: nunca saem de novo, mesmo sem o arquivo de estado.
JA_PUBLICADOS = {"03-aqui-e-o-gabriel": "https://www.instagram.com/p/DfLH_oYnDIj/"}

MAXIMO_DE_SLIDES = 20
TAMANHO_DO_SLIDE = (1080, 1350)
LIMITE_DA_LEGENDA = 2200

HOME = "https://www.instagram.com/"
LOGIN = "https://www.instagram.com/accounts/login/"
ESPERA_DO_LOGIN_S = 900
ESPERA_DA_PAGINA_MS = 30_000
ESPERA_DO_PASSO_MS = 20_000
#: Vídeo de 16 MB sobe e é processado antes do "foi compartilhado".
ESPERA_DO_COMPARTILHAR_MS = 360_000
JANELA_FORA_DA_TELA = ("--window-position=-32000,-32000", "--window-size=1280,900")

_DIA = re.compile(r"(\d{1,2})/(\d{1,2})\s*$")
_HORA = re.compile(r"^(\d{1,2})(?:[:h](\d{2}))?h?$")
_SLIDE = re.compile(r"^\d{2}\.png$")
_CODIGO = re.compile(r"/(?:p|reel|reels)/([A-Za-z0-9_-]{5,})/")
_CODIGO_NA_RESPOSTA = re.compile(r'"code"\s*:\s*"([A-Za-z0-9_-]{5,})"')
#: "Compartilhando" (em andamento) não casa: só o particípio.
_COMPARTILHADO = re.compile(r"compartilhad[oa]\b|has been shared|^(?:post|reel) shared", re.I)


class ErroDoInstagram(RuntimeError):
    """O Chrome não abriu, o login venceu ou a tela não ficou como o pedido; nada foi compartilhado."""


class LoginVencido(ErroDoInstagram):
    pass


class ConteudoIncompleto(ErroDoInstagram):
    """Falta o vídeo, a capa, um slide ou a legenda no disco."""


# -- o calendário e o conteúdo ----------------------------------------------------------------------


@dataclass(frozen=True)
class Postagem:
    pasta: str
    tipo: str
    dia: date
    hora: time = HORA_PADRAO
    titulo: str = ""
    corte: bool = False

    @property
    def nome(self) -> str:
        return f"{'Reel' if self.tipo == REEL else 'Carrossel'} {self.pasta}"

    @property
    def momento(self) -> datetime:
        return datetime.combine(self.dia, self.hora)


@dataclass(frozen=True)
class Conteudo:
    arquivos: tuple[Path, ...]
    legenda: str
    capa: Path | None = None


def ler_calendario(caminho: Path = CALENDARIO) -> list[Postagem]:
    """Os Reels e os carrosséis do `calendario.json`, em ordem de dia; `hora` ausente ou sem número é 19h."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    postagens = [
        _postagem(item, REEL) for item in dados.get("reels", [])
    ] + [_postagem(item, CARROSSEL) for item in dados.get("posts", [])]
    return sorted(postagens, key=lambda postagem: postagem.momento)


def _postagem(item: dict, tipo: str) -> Postagem:
    dia = _DIA.search(str(item.get("dia", "")))
    if not dia:
        raise ValueError(f"dia sem dd/mm no calendário: {item!r}")
    return Postagem(
        pasta=str(item["pasta"]),
        tipo=tipo,
        dia=date(ANO_DO_CALENDARIO, int(dia.group(2)), int(dia.group(1))),
        hora=_hora(str(item.get("hora", ""))),
        titulo=str(item.get("titulo", "")),
        corte=tipo == REEL and item.get("tipo") == "corte",
    )


def _hora(texto: str) -> time:
    casou = _HORA.match(texto.strip().lower())
    if not casou:
        return HORA_PADRAO
    return time(int(casou.group(1)), int(casou.group(2) or 0))


def ler_legenda(markdown: str) -> str:
    """A seção `## Legenda` (ou `## Legenda do post`) até o próximo `## `, como o `pagina/montar.py` lê."""
    casou = re.search(r"^## Legenda[^\n]*\n(.*?)(?=^## |\Z)", markdown.replace("\r\n", "\n"), re.S | re.M)
    return casou.group(1).strip() if casou else ""


def conteudo_da_postagem(postagem: Postagem, *, midias: Path | None = None, textos: Path = TEXTOS) -> Conteudo:
    """Os arquivos exatos e a legenda; o Reel sai sem narração (`-sem-voz.mp4`), menos os cortes de podcast."""
    midias = midias or pasta_das_midias()
    if postagem.tipo == REEL:
        sufixo = "" if postagem.corte else "-sem-voz"
        video = midias / "reels" / f"{postagem.pasta}{sufixo}.mp4"
        capa = midias / "reels" / f"{postagem.pasta}-capa.jpg"
        faltam = [caminho.name for caminho in (video, capa) if not caminho.is_file()]
        arquivos: tuple[Path, ...] = (video,)
        markdown = textos / "reels" / postagem.pasta / "legenda.md"
    else:
        pasta = midias / "posts" / postagem.pasta
        arquivos = tuple(sorted(caminho for caminho in pasta.glob("*.png") if _SLIDE.match(caminho.name))) if pasta.is_dir() else ()
        capa = None
        faltam = [] if 2 <= len(arquivos) <= MAXIMO_DE_SLIDES else [f"{len(arquivos)} slides em {pasta}"]
        markdown = textos / "posts" / postagem.pasta / "legenda.md"
    legenda = ler_legenda(markdown.read_text(encoding="utf-8")) if markdown.is_file() else ""
    if not legenda:
        faltam.append(f"a legenda de {markdown}")
    elif len(legenda) > LIMITE_DA_LEGENDA:
        faltam.append(f"uma legenda de até {LIMITE_DA_LEGENDA} caracteres (tem {len(legenda)})")
    if faltam:
        raise ConteudoIncompleto(f"{postagem.nome}: falta {', '.join(faltam)}")
    return Conteudo(arquivos=arquivos, legenda=legenda, capa=capa)


def slides_fora_do_tamanho(conteudo: Conteudo) -> list[str]:
    from PIL import Image

    errados = []
    for arquivo in conteudo.arquivos:
        if arquivo.suffix.lower() == ".png":
            with Image.open(arquivo) as imagem:
                if imagem.size != TAMANHO_DO_SLIDE:
                    errados.append(f"{arquivo.name} {imagem.size[0]}×{imagem.size[1]}")
    return errados


# -- o estado e a escolha do dia ---------------------------------------------------------------------


def pasta_do_perfil() -> Path:
    from .plataforma import pasta_local_do_whis

    return pasta_local_do_whis() / "navegador-instagram"


def pasta_das_midias() -> Path:
    """`.scratch/instagram/` do repositório, onde o `velozo-reels` grava os vídeos, as capas e os slides."""
    return Path(__file__).resolve().parents[2] / ".scratch" / "instagram"


def pasta_das_execucoes() -> Path:
    """As fotos de cada ensaio e de cada envio, na pasta do repositório (a sessão do Claude lê daqui)."""
    return pasta_das_midias() / "execucoes"


def caminho_do_estado() -> Path:
    from .corrida import pasta_da_cacada

    return pasta_da_cacada() / NOME_DO_ESTADO


def ler_estado(caminho: Path) -> dict[str, dict]:
    if not caminho.is_file():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return dict(dados.get("posts", {}))


def gravar_estado(caminho: Path, posts: dict[str, dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps({"posts": posts}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, caminho)


def situacao(posts: dict[str, dict], pasta: str) -> str:
    if pasta in JA_PUBLICADOS:
        return PUBLICADO
    return str(posts.get(pasta, {}).get("situacao", ""))


def saiu_no_dia(posts: dict[str, dict], dia: date) -> str:
    """A pasta que já saiu (ou talvez tenha saído) nesse dia, ou vazio."""
    for pasta, registro in posts.items():
        quando = str(registro.get("quando", ""))
        if registro.get("situacao") in SAIU_OU_TALVEZ and quando[:10] == dia.isoformat():
            return pasta
    return ""


def escolher(
    postagens: Sequence[Postagem], posts: dict[str, dict], agora: datetime, *, pasta: str = "", agendada: bool = True
) -> tuple[Postagem | None, str]:
    """O item a publicar agora e, sem item, o porquê.

    Sem `pasta`: o do dia de hoje, depois da hora dele, nunca de um dia que passou. Com `pasta` (à mão):
    aquele, se ainda não saiu. Nos dois, um por dia. A tarefa agendada não tenta de novo o que falhou.
    """
    hoje = agora.date()
    ja = saiu_no_dia(posts, hoje)
    if ja:
        return None, f"hoje já saiu {ja}"
    if pasta:
        postagem = next((p for p in postagens if p.pasta == pasta), None)
        if postagem is None:
            return None, f"{pasta} não está no calendário"
        estado = situacao(posts, pasta)
        if estado in SAIU_OU_TALVEZ:
            return None, f"{pasta} já está como {estado}"
        return postagem, ""
    do_dia = [p for p in postagens if p.dia == hoje]
    if not do_dia:
        return None, "nada no calendário hoje"
    postagem = do_dia[0]
    if agora.time() < postagem.hora:
        return None, f"{postagem.pasta} é às {postagem.hora:%H:%M}"
    estado = situacao(posts, postagem.pasta)
    if estado and (agendada or estado != FALHOU):
        return None, f"{postagem.pasta} já está como {estado}"
    return postagem, ""


def perdidas(postagens: Sequence[Postagem], posts: dict[str, dict], agora: datetime) -> list[Postagem]:
    """Itens de dias que passaram sem sair e sem aviso ainda (o PC estava desligado às 19h)."""
    return [p for p in postagens if p.dia < agora.date() and not situacao(posts, p.pasta)]


def proxima(postagens: Sequence[Postagem], posts: dict[str, dict], agora: datetime) -> Postagem | None:
    return next((p for p in postagens if p.dia >= agora.date() and not situacao(posts, p.pasta)), None)


# -- quem publica -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Envio:
    confirmado: bool
    link: str = ""
    fotos: tuple[str, ...] = ()
    avisos: tuple[str, ...] = ()


class PortaDePublicacao(Protocol):
    def publicar(
        self, postagem: Postagem, conteudo: Conteudo, *, compartilhar: bool, antes_do_clique: Callable[[], None]
    ) -> Envio:
        """Monta o post; com `compartilhar`, chama `antes_do_clique` e clica. Erro antes do clique levanta `ErroDoInstagram`."""
        ...


@dataclass
class Resultado:
    postagem: Postagem | None = None
    situacao: str = ""
    motivo: str = ""
    link: str = ""
    fotos: tuple[str, ...] = ()
    avisos: tuple[str, ...] = ()
    perdidas: list[Postagem] = field(default_factory=list)


ENSAIO = "ensaio"


def publicar_do_dia(
    porta: PortaDePublicacao,
    *,
    estado: Path,
    avisar: Callable[[str], object],
    agora: datetime,
    postagens: Sequence[Postagem] | None = None,
    pasta: str = "",
    agendada: bool = False,
    ensaio: bool = False,
    midias: Path | None = None,
    textos: Path = TEXTOS,
) -> Resultado:
    """O item de hoje (ou `pasta`) no ar, com o estado e o aviso no celular; `ensaio` para antes de Compartilhar."""
    postagens = list(postagens) if postagens is not None else ler_calendario()
    posts = ler_estado(estado)
    resultado = Resultado()
    if ensaio:
        postagem = next((p for p in postagens if p.pasta == pasta), None) if pasta else proxima(postagens, posts, agora)
        if postagem is None:
            return Resultado(motivo=f"{pasta or 'nenhum item'} para ensaiar")
    else:
        resultado.perdidas = perdidas(postagens, posts, agora) if not pasta else []
        for perdida in resultado.perdidas:
            posts[perdida.pasta] = {"situacao": PERDIDO, "quando": agora.isoformat(timespec="seconds")}
            avisar(
                f"Instagram: o {perdida.nome} era de {perdida.dia:%d/%m} às {perdida.hora:%H:%M} e não saiu "
                f"(o PC estava desligado?). O Whis não posta atrasado. Para postar agora: "
                f"whis instagram-postar --pasta {perdida.pasta}"
            )
        if resultado.perdidas:
            gravar_estado(estado, posts)
        postagem, motivo = escolher(postagens, posts, agora, pasta=pasta, agendada=agendada)
        if postagem is None:
            resultado.motivo = motivo
            return resultado
    resultado.postagem = postagem

    try:
        conteudo = conteudo_da_postagem(postagem, midias=midias, textos=textos)
    except ConteudoIncompleto as erro:
        return _falhou(resultado, posts, estado, avisar, agora, str(erro), registrar=not ensaio)

    if ensaio:
        try:
            envio = porta.publicar(postagem, conteudo, compartilhar=False, antes_do_clique=lambda: None)
        except ErroDoInstagram as erro:
            resultado.situacao, resultado.motivo = FALHOU, str(erro)
            return resultado
        resultado.situacao, resultado.fotos, resultado.avisos = ENSAIO, envio.fotos, envio.avisos
        return resultado

    clicou = False

    def antes_do_clique() -> None:
        nonlocal clicou
        posts[postagem.pasta] = {"situacao": ENVIANDO, "quando": agora.isoformat(timespec="seconds")}
        gravar_estado(estado, posts)
        clicou = True

    try:
        envio = porta.publicar(postagem, conteudo, compartilhar=True, antes_do_clique=antes_do_clique)
    except Exception as erro:  # noqa: BLE001 — depois do clique, qualquer erro é "talvez tenha saído"
        if not clicou:
            if not isinstance(erro, ErroDoInstagram):
                _log.exception("O post do Instagram falhou antes do clique.")
            return _falhou(resultado, posts, estado, avisar, agora, str(erro) or type(erro).__name__)
        _log.exception("O post do Instagram falhou depois do clique.")
        envio = Envio(confirmado=False)
    resultado.fotos, resultado.avisos = envio.fotos, envio.avisos
    registro = posts[postagem.pasta]
    if envio.confirmado:
        registro.update(situacao=PUBLICADO, link=envio.link)
        resultado.situacao, resultado.link = PUBLICADO, envio.link
        linhas = [f"Instagram: saiu o {postagem.nome}.", postagem.titulo, envio.link or "O link não apareceu: confira no perfil."]
        linhas += [f"Atenção: {aviso}" for aviso in envio.avisos]
        avisar("\n".join(linha for linha in linhas if linha))
    else:
        registro.update(situacao=INCERTO)
        resultado.situacao = INCERTO
        avisar(
            f"Instagram: cliquei em Compartilhar no {postagem.nome}, mas o Instagram não confirmou. Confira o "
            f"perfil (instagram.com/{USUARIO}). O Whis não tenta de novo."
        )
    gravar_estado(estado, posts)
    return resultado


def _falhou(
    resultado: Resultado, posts: dict, estado: Path, avisar: Callable[[str], object], agora: datetime, motivo: str, *, registrar: bool = True
) -> Resultado:
    resultado.situacao, resultado.motivo = FALHOU, motivo
    if not registrar:
        return resultado
    postagem = resultado.postagem
    posts[postagem.pasta] = {"situacao": FALHOU, "quando": agora.isoformat(timespec="seconds"), "motivo": motivo}
    gravar_estado(estado, posts)
    conserto = (
        "Entre de novo com: whis instagram-login, e depois"
        if isinstance(motivo, str) and "login" in motivo.lower()
        else "Para tentar de novo:"
    )
    avisar(
        f"Instagram: o {postagem.nome} não saiu: {motivo}. Nada foi publicado. "
        f"{conserto} whis instagram-postar --pasta {postagem.pasta}"
    )
    return resultado


# -- o login ----------------------------------------------------------------------------------------


def abrir_login(
    perfil: Path | None = None,
    *,
    esperar_s: float = ESPERA_DO_LOGIN_S,
    abrir: Callable[[list[str]], subprocess.Popen] = subprocess.Popen,
    conferir: Callable[[Path], bool] | None = None,
) -> bool:
    """Abre um Chrome comum no login do Instagram e, quando ele fecha, confere a sessão no perfil.

    Comum, sem Playwright, como o `x-login`: o login com Google ou Facebook recusa navegador
    controlado por automação. O Whis nunca vê a senha.
    """
    from .fonte_x import caminho_do_chrome

    perfil = Path(perfil) if perfil else pasta_do_perfil()
    chrome = caminho_do_chrome()
    if chrome is None:
        _log.warning("O login do Instagram não abriu: o Chrome não foi encontrado")
        return False
    perfil.mkdir(parents=True, exist_ok=True)
    processo = abrir([str(chrome), f"--user-data-dir={perfil}", "--no-first-run", "--no-default-browser-check", LOGIN])
    try:
        processo.wait(timeout=esperar_s)
    except subprocess.TimeoutExpired:
        _log.warning("O login do Instagram não terminou em %.0f s: a janela continua aberta", esperar_s)
        return False
    return (conferir or sessao_valida)(perfil)


def sessao_valida(perfil: Path | None = None) -> bool:
    """O Chrome do Whis abre logado na @velozo.dev? Abre por alguns segundos, fora da tela."""
    try:
        with SessaoDoInstagram(Path(perfil) if perfil else pasta_do_perfil()) as sessao:
            sessao.conferir_conta()
            return True
    except ErroDoInstagram as erro:
        _log.warning("A sessão do Instagram não se confirmou: %s", erro)
        return False


def fechar_chrome_do_perfil(perfil: Path, *, executar: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """Fecha a janela do login que ele deixou aberta: com ela, o Chrome do Whis não abre o perfil."""
    import sys

    if sys.platform == "darwin":
        # `pkill -f` pelo caminho do perfil: TERM (o Chrome fecha limpo), e KILL no que sobrar.
        try:
            achou = executar(["pkill", "-f", f"--user-data-dir={perfil}"], capture_output=True, text=True, check=False)
            if achou.returncode == 0:
                import time

                time.sleep(3)
                executar(["pkill", "-9", "-f", f"--user-data-dir={perfil}"], capture_output=True, text=True, check=False)
                _log.info("Fechei o Chrome que estava aberto no perfil do Instagram.")
        except (OSError, subprocess.SubprocessError) as erro:
            _log.info("Não deu para conferir o Chrome aberto no perfil do Instagram: %s", erro)
        return
    alvo = str(perfil).lower().replace("'", "''")
    script = (
        "$p = @(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -and $_.CommandLine.ToLower().Contains('{alvo}') }}); "
        "if ($p.Count -eq 0) { exit 0 }; "
        "foreach ($x in $p) { if ($x.CommandLine -notmatch '--type=') { "
        "$g = Get-Process -Id $x.ProcessId -ErrorAction SilentlyContinue; if ($g) { [void]$g.CloseMainWindow() } } }; "
        "$fim = (Get-Date).AddSeconds(15); "
        "while ((Get-Date) -lt $fim -and (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 500 }; "
        "Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue | Stop-Process -Force; "
        "Write-Output $p.Count"
    )
    try:
        saida = executar(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, check=False, timeout=60,
        )
        if (saida.stdout or "").strip() not in ("", "0"):
            _log.info("Fechei o Chrome que estava aberto no perfil do Instagram (%s processos).", saida.stdout.strip())
    except (OSError, subprocess.SubprocessError) as erro:
        _log.info("Não deu para conferir o Chrome aberto no perfil do Instagram: %s", erro)


# -- o Chrome do Instagram ------------------------------------------------------------------------------


_TEXTO_AVANCAR = re.compile(r"^(Avançar|Next)$")
_TEXTO_COMPARTILHAR = re.compile(r"^(Compartilhar|Share)$")
_TEXTO_DO_COMPUTADOR = re.compile(r"^(Selecionar do computador|Select from computer)$")
_TEXTO_AGORA_NAO = re.compile(r"^(Agora não|Not now)$")
_TEXTO_OK = re.compile(r"^OK$")
_TEXTO_POST = re.compile(r"^(Post|Publicação)$")
_ROTULO_DE_IA = re.compile(r"r[óo]tulo de IA|AI label|conte[úu]do gerado por IA|AI[- ]generated", re.I)
SELETOR_LOGADO = 'a[href*="/direct/inbox"]'
SELETOR_DO_LOGIN = 'input[type="password"]'
SELETOR_CRIAR = 'svg[aria-label="Novo post"], svg[aria-label="Nova publicação"], svg[aria-label="New post"]'
SELETOR_PUBLICACAO = 'svg[aria-label="Publicação"], svg[aria-label="Post"]'
SELETOR_CORTE = 'svg[aria-label="Selecionar corte"], svg[aria-label="Select crop"]'
SELETOR_LEGENDA = 'div[role="dialog"] div[contenteditable="true"]'


class SessaoDoInstagram:
    """Uma abertura do Chrome do Whis no Instagram, fora da tela."""

    def __init__(self, perfil: Path, *, fotos: Path | None = None) -> None:
        self.perfil = perfil
        self.fotos = fotos
        self.tiradas: list[str] = []
        self._gerente = None
        self._contexto = None
        self.pagina = None

    def __enter__(self) -> SessaoDoInstagram:
        fechar_chrome_do_perfil(self.perfil)
        try:
            from playwright.sync_api import sync_playwright

            self._gerente = sync_playwright().start()
        except Exception as erro:  # noqa: BLE001 — sem Playwright não há post
            raise ErroDoInstagram(f"o Playwright não abriu: {_motivo(erro)}") from erro
        self.perfil.mkdir(parents=True, exist_ok=True)
        try:
            self._contexto = self._gerente.chromium.launch_persistent_context(
                str(self.perfil),
                channel="chrome",
                headless=False,
                args=list(JANELA_FORA_DA_TELA),
                locale="pt-BR",
                viewport={"width": 1280, "height": 900},
            )
        except Exception as erro:  # noqa: BLE001 — o Playwright só diz o porquê no texto
            self._parar()
            texto = str(erro)
            if "ProcessSingleton" in texto or "already in use" in texto:
                raise ErroDoInstagram("o perfil do Instagram do Whis está aberto em outro Chrome") from erro
            raise ErroDoInstagram(f"o Chrome não abriu: {_primeira_linha(texto)}") from erro
        self.pagina = self._contexto.pages[0] if self._contexto.pages else self._contexto.new_page()
        return self

    def __exit__(self, *_: object) -> None:
        if self._contexto is not None:
            try:
                self._contexto.close()
            except Exception as erro:  # noqa: BLE001 — fechar mal não desfaz o que já foi feito
                _log.debug("O Chrome do Instagram não fechou limpo: %s", erro)
        self._contexto = self.pagina = None
        self._parar()

    def _parar(self) -> None:
        if self._gerente is not None:
            try:
                self._gerente.stop()
            except Exception:  # noqa: BLE001 — o processo do Playwright sai de todo jeito
                pass
        self._gerente = None

    # -- a conta ---------------------------------------------------------------------------------

    def conferir_conta(self, usuario: str = USUARIO) -> set[str]:
        """Abre o perfil da @velozo.dev, confere que é a conta logada e devolve os códigos dos posts da grade."""
        pagina = self.pagina
        try:
            pagina.goto(f"{HOME}{usuario}/", wait_until="domcontentloaded", timeout=ESPERA_DA_PAGINA_MS)
            pagina.locator(f'{SELETOR_LOGADO}, {SELETOR_DO_LOGIN}, a[href="/accounts/edit/"]').first.wait_for(
                timeout=ESPERA_DA_PAGINA_MS
            )
        except Exception as erro:  # noqa: BLE001 — página que não carrega não posta
            self.amostra("perfil")
            raise ErroDoInstagram(f"o perfil não abriu: {_motivo(erro)}") from erro
        if "/accounts/login" in pagina.url or pagina.locator(SELETOR_DO_LOGIN).count():
            raise LoginVencido("o login do Instagram venceu no Chrome do Whis")
        self.dispensar_avisos()
        try:
            pagina.locator('a[href="/accounts/edit/"]').first.wait_for(timeout=10_000)
        except Exception as erro:  # noqa: BLE001 — sem "Editar perfil" é outra conta
            self.amostra("perfil")
            raise ErroDoInstagram(f"o Chrome do Whis não está logado na @{usuario}") from erro
        return self.codigos_da_grade()

    def codigos_da_grade(self) -> set[str]:
        try:
            self.pagina.locator('a[href*="/p/"], a[href*="/reel/"]').first.wait_for(timeout=10_000)
        except Exception:  # noqa: BLE001 — perfil sem post
            pass
        links = self.pagina.locator('a[href*="/p/"], a[href*="/reel/"]').evaluate_all("els => els.map(e => e.getAttribute('href'))")
        return {casou.group(1) for link in links if (casou := _CODIGO.search(link or ""))}

    def dispensar_avisos(self) -> None:
        for _ in range(2):
            botao = self.pagina.get_by_role("button", name=_TEXTO_AGORA_NAO)
            try:
                botao.first.click(timeout=2_500)
            except Exception:  # noqa: BLE001 — sem aviso na tela
                return

    # -- o post ----------------------------------------------------------------------------------

    def montar(self, postagem: Postagem, conteudo: Conteudo) -> list[str]:
        """Do Criar até a legenda escrita e conferida; devolve os avisos. Nada é compartilhado aqui."""
        pagina = self.pagina
        avisos: list[str] = []
        self._passo("abrir o Criar", lambda: pagina.locator(SELETOR_CRIAR).first.click(timeout=ESPERA_DO_PASSO_MS))
        try:
            pagina.get_by_role("button", name=_TEXTO_DO_COMPUTADOR).first.wait_for(timeout=4_000)
        except Exception:  # noqa: BLE001 — o Criar abriu o menu (Post, Live...) em vez do diálogo
            self._passo(
                "escolher Post no menu do Criar",
                lambda: pagina.locator(SELETOR_PUBLICACAO).or_(pagina.get_by_text(_TEXTO_POST, exact=False)).first.click(
                    timeout=ESPERA_DO_PASSO_MS
                ),
            )

        def escolher_arquivos() -> None:
            with pagina.expect_file_chooser(timeout=ESPERA_DO_PASSO_MS) as escolha:
                pagina.get_by_role("button", name=_TEXTO_DO_COMPUTADOR).first.click(timeout=ESPERA_DO_PASSO_MS)
            escolha.value.set_files([str(arquivo) for arquivo in conteudo.arquivos])

        self._passo("escolher os arquivos", escolher_arquivos)
        if postagem.tipo == REEL:
            try:
                pagina.get_by_role("button", name=_TEXTO_OK).first.click(timeout=8_000)
            except Exception:  # noqa: BLE001 — o aviso "vídeos viram reels" só aparece às vezes
                pass
        corte = "Original" if postagem.tipo == REEL else "4:5"

        def escolher_corte() -> None:
            pagina.locator(SELETOR_CORTE).first.click(timeout=ESPERA_DO_PASSO_MS)
            pagina.locator('div[role="dialog"]').get_by_text(corte, exact=True).first.click(timeout=ESPERA_DO_PASSO_MS)

        self._passo(f"escolher o corte {corte}", escolher_corte)
        if postagem.tipo == CARROSSEL:
            pagina.wait_for_timeout(1_000)
        self.foto("1-corte")
        self._passo("avançar do corte", lambda: self._avancar())
        if postagem.tipo == REEL and conteudo.capa is not None:
            try:
                with pagina.expect_file_chooser(timeout=10_000) as escolha:
                    pagina.locator('div[role="dialog"]').get_by_text(_TEXTO_DO_COMPUTADOR).last.click(timeout=10_000)
                escolha.value.set_files(str(conteudo.capa))
                pagina.wait_for_timeout(2_000)
            except Exception as erro:  # noqa: BLE001 — sem a capa, o Instagram usa o primeiro quadro
                _log.warning("A capa do Reel não subiu: %s", _motivo(erro))
                avisos.append("a capa não subiu; troque pelo app (Editar > Editar capa)")
        self.foto("2-edicao")
        self._passo("avançar da edição", lambda: self._avancar())
        self._passo("escrever a legenda", lambda: self._escrever(conteudo.legenda))
        lida = pagina.locator(SELETOR_LEGENDA).first.inner_text()
        if _linhas(lida) != _linhas(conteudo.legenda):
            self.foto("3-legenda-errada")
            raise ErroDoInstagram("a legenda na caixa não ficou igual ao texto do legenda.md")
        if self._rotulo_de_ia_ligado():
            raise ErroDoInstagram("o rótulo de IA está ligado")
        self.amostra("legenda")
        self.foto("3-legenda")
        try:
            pagina.get_by_role("button", name=_TEXTO_COMPARTILHAR).first.click(trial=True, timeout=10_000)
        except Exception as erro:  # noqa: BLE001 — botão coberto ou sumido: nada é compartilhado
            raise ErroDoInstagram(f"o botão Compartilhar não está clicável: {_motivo(erro)}") from erro
        return avisos

    def compartilhar(self, postagem: Postagem, antes: set[str], antes_do_clique: Callable[[], None], usuario: str = USUARIO) -> Envio:
        pagina = self.pagina
        codigos: list[str] = []

        def ouvir(resposta) -> None:
            if "configure" not in resposta.url or resposta.request.method != "POST":
                return
            try:
                casou = _CODIGO_NA_RESPOSTA.search(resposta.text())
            except Exception:  # noqa: BLE001 — a grade do perfil é o plano B
                return
            if casou:
                codigos.append(casou.group(1))

        pagina.on("response", ouvir)
        antes_do_clique()
        pagina.get_by_role("button", name=_TEXTO_COMPARTILHAR).first.click(timeout=10_000)
        try:
            pagina.locator('div[role="dialog"]').get_by_text(_COMPARTILHADO).first.wait_for(timeout=ESPERA_DO_COMPARTILHAR_MS)
        except Exception as erro:  # noqa: BLE001 — sem a confirmação, fica incerto
            _log.warning("O Instagram não confirmou o post: %s", _motivo(erro))
            self.foto("4-sem-confirmacao")
            return Envio(confirmado=False)
        self.foto("4-compartilhado")
        codigo = codigos[-1] if codigos else ""
        if not codigo:
            try:
                pagina.goto(f"{HOME}{usuario}/", wait_until="domcontentloaded", timeout=ESPERA_DA_PAGINA_MS)
                pagina.wait_for_timeout(4_000)
                novos = self.codigos_da_grade() - antes - {_codigo(link) for link in JA_PUBLICADOS.values()}
                codigo = next(iter(novos)) if len(novos) == 1 else ""
            except Exception as erro:  # noqa: BLE001 — o post saiu; só o link que não
                _log.info("O link do post novo não apareceu na grade: %s", _motivo(erro))
        caminho = "reel" if postagem.tipo == REEL else "p"
        return Envio(confirmado=True, link=f"{HOME}{caminho}/{codigo}/" if codigo else "")

    # -- miúdos ----------------------------------------------------------------------------------

    def _avancar(self) -> None:
        self.pagina.locator('div[role="dialog"]').get_by_role("button", name=_TEXTO_AVANCAR).first.click(timeout=ESPERA_DO_PASSO_MS)
        self.pagina.wait_for_timeout(1_500)

    def _escrever(self, legenda: str) -> None:
        caixa = self.pagina.locator(SELETOR_LEGENDA).first
        caixa.click(timeout=ESPERA_DO_PASSO_MS)
        for numero, linha in enumerate(legenda.split("\n")):
            if numero:
                self.pagina.keyboard.press("Enter")
            if linha:
                self.pagina.keyboard.insert_text(linha)
        self.pagina.wait_for_timeout(1_000)

    def _rotulo_de_ia_ligado(self) -> bool:
        dialogo = self.pagina.locator('div[role="dialog"]')
        try:
            avancadas = dialogo.get_by_text(re.compile(r"^(Configurações avançadas|Advanced settings)$")).first
            if avancadas.count():
                avancadas.click(timeout=5_000)
                self.pagina.wait_for_timeout(800)
        except Exception:  # noqa: BLE001 — sem a seção, sem rótulo para conferir
            pass
        rotulo = dialogo.get_by_text(_ROTULO_DE_IA).first
        if not rotulo.count():
            return False
        chave = rotulo.locator("xpath=ancestor::*[.//input[@type='checkbox']][1]//input[@type='checkbox']").first
        try:
            return chave.count() > 0 and chave.is_checked()
        except Exception:  # noqa: BLE001 — a chave não se lê: melhor não compartilhar
            return True

    def _passo(self, nome: str, acao: Callable[[], object]) -> None:
        try:
            acao()
        except ErroDoInstagram:
            raise
        except Exception as erro:  # noqa: BLE001 — passo que não deu certo para tudo antes do clique
            self.foto(f"erro-{re.sub(r'[^a-z0-9]+', '-', nome.lower())}")
            self.amostra("erro")
            raise ErroDoInstagram(f"não deu para {nome}: {_motivo(erro)}") from erro

    def foto(self, nome: str) -> None:
        if self.fotos is None or self.pagina is None:
            return
        try:
            self.fotos.mkdir(parents=True, exist_ok=True)
            arquivo = self.fotos / f"{nome}.png"
            self.pagina.screenshot(path=str(arquivo))
            self.tiradas.append(str(arquivo))
        except Exception as erro:  # noqa: BLE001 — a foto é só a prova visual
            _log.info("A foto %s não saiu: %s", nome, _motivo(erro))

    def amostra(self, nome: str) -> None:
        """O HTML do diálogo (ou da página), para ver o que mudou quando um passo quebra."""
        if self.fotos is None or self.pagina is None:
            return
        try:
            self.fotos.mkdir(parents=True, exist_ok=True)
            dialogo = self.pagina.locator('div[role="dialog"]')
            html = dialogo.last.evaluate("e => e.outerHTML") if dialogo.count() else self.pagina.content()
            (self.fotos / f"amostra-{nome}.html").write_text(html, encoding="utf-8")
        except Exception as erro:  # noqa: BLE001 — a amostra é só diagnóstico
            _log.info("A amostra %s não saiu: %s", nome, _motivo(erro))


class PublicadorDoNavegador:
    """A porta de hoje: o instagram.com no Chrome do Whis. A da API da Meta entra no lugar dela."""

    def __init__(self, *, perfil: Path | None = None, execucoes: Path | None = None, usuario: str = USUARIO) -> None:
        self.perfil = Path(perfil) if perfil else pasta_do_perfil()
        self.execucoes = Path(execucoes) if execucoes else pasta_das_execucoes()
        self.usuario = usuario

    def publicar(
        self, postagem: Postagem, conteudo: Conteudo, *, compartilhar: bool, antes_do_clique: Callable[[], None]
    ) -> Envio:
        modo = "envio" if compartilhar else "ensaio"
        fotos = self.execucoes / f"{datetime.now():%Y%m%d-%H%M%S}-{modo}-{postagem.pasta}"
        with SessaoDoInstagram(self.perfil, fotos=fotos) as sessao:
            antes = sessao.conferir_conta(self.usuario)
            avisos = sessao.montar(postagem, conteudo)
            if not compartilhar:
                return Envio(confirmado=False, fotos=tuple(sessao.tiradas), avisos=tuple(avisos))
            envio = sessao.compartilhar(postagem, antes, antes_do_clique, self.usuario)
            return Envio(confirmado=envio.confirmado, link=envio.link, fotos=tuple(sessao.tiradas), avisos=tuple(avisos))


def _linhas(texto: str) -> list[str]:
    return [linha.strip() for linha in texto.replace("\r", "").replace("\xa0", " ").split("\n") if linha.strip()]


def _codigo(link: str) -> str:
    casou = _CODIGO.search(link)
    return casou.group(1) if casou else ""


def _motivo(erro: BaseException) -> str:
    if type(erro).__name__ == "TimeoutError":
        return "tempo esgotado"
    return _primeira_linha(str(erro)) or type(erro).__name__


def _primeira_linha(texto: str) -> str:
    return texto.strip().splitlines()[0][:160] if texto.strip() else ""
