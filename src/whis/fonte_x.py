"""O X no jornal: um Chrome com o perfil da conta do Whis lê as páginas e traz os posts.

O X não tem leitura grátis nem RSS; ele escolheu navegar com uma conta só do
Whis e aceitou o risco dos termos (ADR 0003). O leitor só lê: abre cada página
uma vez por edição, rola, guarda o HTML dos posts e fecha. A leitura do HTML
é Python puro, para os testes não precisarem de navegador.
"""

from __future__ import annotations

import logging
import os
import random
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode, urlsplit

from .fontes_do_jornal import FonteDoX
from .portas import Candidato

_log = logging.getLogger("whis.fonte_x")

HOME = "https://x.com/home"
BUSCA = "https://x.com/search"
LOGIN = "https://x.com/login"

#: O X responde erro HTTP ao Chrome sem janela (visto em 2026-09-30, Chrome 154).
SEM_JANELA = False
ROLAGENS = 4
PAUSA_S = (1.5, 3.0)
ESPERA_DA_PAGINA_MS = 20_000
ESPERA_DO_LOGIN_S = 600

JANELA = timedelta(hours=24)
TOLERANCIA_NO_FUTURO = timedelta(hours=1)
TETO_DO_X = 30
TAMANHO_DO_TITULO = 120
TAMANHO_DO_RESUMO = 500

SELETOR_DO_POST = 'article[data-testid="tweet"]'
SELETOR_DE_VAZIO = '[data-testid="emptyState"]'
ABAS = {
    "seguindo": re.compile(r"^(Following|Seguindo)$", re.I),
    "para-voce": re.compile(r"^(For you|Para você)$", re.I),
}

_HOSTS_DO_X = frozenset({"x.com", "twitter.com"})
_STATUS = re.compile(r"^/(?P<autor>\w{1,15})/status/(?P<id>\d+)/?$")
_VAZIOS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})
_ROTULOS_DE_ANUNCIO = frozenset({"Ad", "Promoted", "Boosted", "Anúncio", "Promovido", "Impulsionado"})
_CAMPO_DE_LOGIN = re.compile(r'<input\b[^>]*\b(?:name="username_or_email"|autocomplete="username)')
_CURTIDAS_NO_ROTULO = re.compile(r"(\d[\d,.]*)\s+Likes?\b", re.I)
_CURTIDAS_CURTAS = re.compile(r"^(\d+(?:[.,]\d+)?)\s*([KM]?)$", re.I)
_MULTIPLOS = {"": 1, "K": 1_000, "M": 1_000_000}


@dataclass(frozen=True)
class PostDoX:
    """Um post como a página mostra: o link canônico, o @, a hora, o texto visível e as curtidas."""

    link: str
    autor: str
    texto: str
    data: datetime
    curtidas: int | None = None


@dataclass(frozen=True)
class LeituraDoX:
    """Os posts de cada página, pelo nome da linha de `fontes.md`, e as páginas que falharam."""

    posts: dict[str, tuple[PostDoX, ...]] = field(default_factory=dict)
    falhas: dict[str, str] = field(default_factory=dict)


class ErroDoX(RuntimeError):
    """O X não pôde ser lido nesta edição: sem navegador, sem Chrome, perfil ocupado."""


class LoginDoXVenceu(ErroDoX):
    """O X mandou para a tela de login: a sessão do perfil do Whis caiu."""


@runtime_checkable
class PortaDoX(Protocol):
    def ler(self, fontes: Sequence[FonteDoX], hoje: date) -> LeituraDoX:
        """Uma visita a cada página. Falha de uma vai para `falhas`; o resto levanta `ErroDoX`."""
        ...


def e_do_x(link: str) -> bool:
    host = (urlsplit(link).hostname or "").removeprefix("www.").removeprefix("mobile.")
    return host in _HOSTS_DO_X


def url_da_busca(consulta: str, hoje: date, *, recentes: bool = False) -> str:
    """A busca das últimas 24 a 48 h, sem respostas; `recentes` abre a aba Mais recentes em vez de Principais."""
    ontem = hoje - timedelta(days=1)
    pedido = f"{consulta} -filter:replies since:{ontem.isoformat()}"
    return f"{BUSCA}?{urlencode({'q': pedido, 'src': 'typed_query', 'f': 'live' if recentes else 'top'})}"


def ler_post(html: str) -> PostDoX | None:
    """O post de um `article` do X, ou `None` para anúncio e para o que não tem permalink."""
    leitor = _LeitorDoArtigo()
    leitor.feed(html)
    leitor.close()
    if leitor.anuncio or leitor.status is None or not leitor.datahora:
        return None
    try:
        data = datetime.fromisoformat(leitor.datahora.replace("Z", "+00:00"))
    except ValueError:
        return None
    autor = leitor.status["autor"]
    return PostDoX(
        link=f"https://x.com/{autor.lower()}/status/{leitor.status['id']}",
        autor=autor,
        texto=" ".join("".join(leitor.texto).split()),
        data=data,
        curtidas=_curtidas(leitor.rotulo_da_curtida, "".join(leitor.curtida_visivel)),
    )


def candidatos_do_x(
    secao: str,
    posts: Iterable[PostDoX],
    agora: datetime,
    ja_publicados: frozenset[str] = frozenset(),
    *,
    teto: int = TETO_DO_X,
) -> tuple[Candidato, ...]:
    """Os posts que valem candidatos: das últimas 24 h, únicos, inéditos, com texto, os mais curtidos."""
    desde, ate = agora - JANELA, agora + TOLERANCIA_NO_FUTURO
    vistos: set[str] = set()
    unicos: list[PostDoX] = []
    for post in posts:
        status = post.link.rstrip("/").rsplit("/", 1)[-1]
        if status in vistos or post.link in ja_publicados or not post.texto:
            continue
        if not desde <= post.data <= ate:
            continue
        vistos.add(status)
        unicos.append(post)
    unicos.sort(key=lambda post: post.curtidas or 0, reverse=True)
    return tuple(
        Candidato(
            id=f"{secao}-x{numero}",
            secao=secao,
            fonte=f"@{post.autor}",
            titulo=_cortar(post.texto, TAMANHO_DO_TITULO),
            link=post.link,
            resumo=_cortar(post.texto, TAMANHO_DO_RESUMO),
            data=post.data,
            curtidas=post.curtidas,
        )
        for numero, post in enumerate(unicos[:teto], 1)
    )


def _playwright():
    from playwright.sync_api import sync_playwright

    return sync_playwright()


class LeitorDoX:
    """O leitor de verdade: o Chrome instalado, num perfil persistente só do Whis.

    `amostras` é uma pasta onde cada `article` lido e cada página vazia ficam
    gravados — é assim que o protótipo tira as fixtures e que se descobre o que
    o X mudou quando a leitura quebra.
    """

    def __init__(
        self,
        *,
        perfil: Path | None = None,
        sem_janela: bool = SEM_JANELA,
        amostras: Path | None = None,
        dormir: Callable[[float], None] = time.sleep,
        sortear: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.perfil = Path(perfil) if perfil else pasta_do_perfil()
        self.sem_janela = sem_janela
        self.amostras = Path(amostras) if amostras else None
        self._dormir = dormir
        self._sortear = sortear

    def ler(self, fontes: Sequence[FonteDoX], hoje: date) -> LeituraDoX:
        if not fontes:
            return LeituraDoX()
        try:
            gerente = _playwright()
        except ImportError as erro:
            raise ErroDoX("o Playwright não está instalado") from erro
        inicio = time.monotonic()
        with gerente as playwright:
            contexto = _abrir_contexto(playwright, self.perfil, sem_janela=self.sem_janela)
            try:
                pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
                posts: dict[str, tuple[PostDoX, ...]] = {}
                falhas: dict[str, str] = {}
                for fonte in fontes:
                    try:
                        posts[fonte.nome] = self._ler_pagina(pagina, fonte, hoje)
                    except LoginDoXVenceu:
                        raise
                    except Exception as erro:  # noqa: BLE001 — uma página não derruba as outras
                        falhas[fonte.nome] = _motivo(erro)
                        _log.warning("X: %s falhou: %s", fonte.nome, falhas[fonte.nome])
            finally:
                _fechar(contexto)
        _log.info(
            "X lido em %.1f s: %s; %d página(s) com falha",
            time.monotonic() - inicio,
            ", ".join(f"{nome} {len(lidos)}" for nome, lidos in posts.items()),
            len(falhas),
        )
        return LeituraDoX(posts=posts, falhas=falhas)

    def _ler_pagina(self, pagina, fonte: FonteDoX, hoje: date) -> tuple[PostDoX, ...]:
        from playwright.sync_api import TimeoutError as TempoEsgotado

        if fonte.tipo in ("busca", "recentes"):
            pagina.goto(url_da_busca(fonte.consulta, hoje, recentes=fonte.tipo == "recentes"), wait_until="domcontentloaded")
        else:
            pagina.goto(HOME, wait_until="domcontentloaded")
            _conferir_login(pagina)
            pagina.get_by_role("tab", name=ABAS[fonte.tipo]).click(timeout=ESPERA_DA_PAGINA_MS)
        _conferir_login(pagina)
        try:
            pagina.wait_for_selector(f"{SELETOR_DO_POST}, {SELETOR_DE_VAZIO}", timeout=ESPERA_DA_PAGINA_MS)
        except TempoEsgotado as erro:
            _conferir_login(pagina)
            html = pagina.content()
            self._guardar(fonte, "pagina", [html])
            if pede_login(html):
                raise LoginDoXVenceu("o X pediu login") from erro
            raise ErroDoX("a página do X mudou: sem post nem aviso de vazio") from erro
        if not pagina.locator(SELETOR_DO_POST).count():
            self._guardar(fonte, "vazia", [pagina.content()])
            return ()

        htmls: list[str] = []
        for rolagem in range(ROLAGENS + 1):
            htmls += pagina.locator(SELETOR_DO_POST).evaluate_all("els => els.map(e => e.outerHTML)")
            if rolagem < ROLAGENS:
                pagina.mouse.wheel(0, 2400)
                self._dormir(self._sortear(*PAUSA_S))
        self._guardar(fonte, "post", htmls)
        lidos: dict[str, PostDoX] = {}
        for html in htmls:
            post = ler_post(html)
            if post is not None:
                lidos.setdefault(post.link, post)
        return tuple(lidos.values())

    def _guardar(self, fonte: FonteDoX, tipo: str, htmls: Sequence[str]) -> None:
        if self.amostras is None:
            return
        pasta = self.amostras / re.sub(r"[^\w-]+", "-", fonte.nome.lower()).strip("-")
        pasta.mkdir(parents=True, exist_ok=True)
        for numero, html in enumerate(dict.fromkeys(htmls), 1):
            (pasta / f"{tipo}-{numero:03d}.html").write_text(html, encoding="utf-8")


def abrir_login(
    perfil: Path | None = None,
    *,
    esperar_s: float = ESPERA_DO_LOGIN_S,
    abrir: Callable[[list[str]], subprocess.Popen] = subprocess.Popen,
    conferir: Callable[[Path], bool] | None = None,
) -> bool:
    """Abre um Chrome comum no login do X e, quando ele fecha, confere se a sessão ficou no perfil.

    Comum, sem Playwright: o Google recusa entrar num Chrome controlado por
    automação ("This browser or app may not be secure"). O perfil é o mesmo
    que o leitor abre depois, então a sessão vale para ele.
    """
    perfil = Path(perfil) if perfil else pasta_do_perfil()
    chrome = caminho_do_chrome()
    if chrome is None:
        _log.warning("O login do X não abriu: o Chrome não foi encontrado")
        return False
    perfil.mkdir(parents=True, exist_ok=True)
    processo = abrir([str(chrome), f"--user-data-dir={perfil}", "--no-first-run", "--no-default-browser-check", LOGIN])
    try:
        processo.wait(timeout=esperar_s)
    except subprocess.TimeoutExpired:
        _log.warning("O login do X não terminou em %.0f s: a janela continua aberta", esperar_s)
        return False
    return (conferir or sessao_valida)(perfil)


def sessao_valida(perfil: Path | None = None) -> bool:
    """A home do X abre logada neste perfil? Abre o Chrome do leitor por alguns segundos."""
    with _playwright() as playwright:
        contexto = _abrir_contexto(playwright, Path(perfil) if perfil else pasta_do_perfil(), sem_janela=SEM_JANELA)
        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            pagina.goto(HOME, wait_until="domcontentloaded")
            pagina.wait_for_selector(SELETOR_DO_POST, timeout=ESPERA_DA_PAGINA_MS)
            return "/login" not in pagina.url and "/i/flow/" not in pagina.url
        except Exception as erro:  # noqa: BLE001 — qualquer falha aqui é "não está logado"
            _log.warning("A sessão do X não se confirmou: %s", _primeira_linha(str(erro)))
            return False
        finally:
            _fechar(contexto)


def caminho_do_chrome() -> Path | None:
    from .plataforma import candidatos_do_chrome

    return next((caminho for caminho in candidatos_do_chrome() if caminho.is_file()), None)


def pasta_do_perfil() -> Path:
    from .plataforma import pasta_local_do_whis

    return pasta_local_do_whis() / "navegador-x"


def _abrir_contexto(playwright, perfil: Path, *, sem_janela: bool):
    perfil.mkdir(parents=True, exist_ok=True)
    try:
        return playwright.chromium.launch_persistent_context(
            str(perfil), channel="chrome", headless=sem_janela, locale="en-US"
        )
    except Exception as erro:  # noqa: BLE001 — o Playwright só diz o porquê no texto
        texto = str(erro)
        if "is not found" in texto or "Executable doesn't exist" in texto:
            raise ErroDoX("o Chrome não foi encontrado") from erro
        if "ProcessSingleton" in texto or "already in use" in texto:
            raise ErroDoX("o perfil do X está em uso por outro processo") from erro
        raise ErroDoX(f"o Chrome não abriu: {_primeira_linha(texto)}") from erro


def pede_login(html: str) -> bool:
    """A busca deslogada não muda de endereço: abre por cima a caixa de entrar, com o campo de usuário."""
    return _CAMPO_DE_LOGIN.search(html) is not None


def _conferir_login(pagina) -> None:
    if "/login" in pagina.url or "/i/flow/" in pagina.url:
        raise LoginDoXVenceu("o X pediu login")


def _fechar(contexto) -> None:
    try:
        contexto.close()
    except Exception as erro:  # noqa: BLE001 — fechar mal não desfaz o que já foi lido
        _log.debug("O Chrome do X não fechou limpo: %s", erro)


def _motivo(erro: Exception) -> str:
    if type(erro).__name__ == "TimeoutError":
        return "tempo esgotado"
    return _primeira_linha(str(erro)) or type(erro).__name__


def _primeira_linha(texto: str) -> str:
    return texto.strip().splitlines()[0][:160] if texto.strip() else ""


def _cortar(texto: str, tamanho: int) -> str:
    if len(texto) <= tamanho:
        return texto
    corte = texto[: tamanho - 1].rsplit(" ", 1)[0] or texto[: tamanho - 1]
    return corte.rstrip(" ,.;:") + "…"


def _curtidas(rotulo: str | None, visivel: str) -> int | None:
    no_rotulo = _CURTIDAS_NO_ROTULO.search(rotulo or "")
    if no_rotulo:
        return int(re.sub(r"[,.]", "", no_rotulo[1]))
    curto = _CURTIDAS_CURTAS.match(visivel.strip())
    if not curto:
        return None
    return round(float(curto[1].replace(",", ".")) * _MULTIPLOS[curto[2].upper()])


class _LeitorDoArtigo(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._pilha: list[str] = []
        self._ancoras_de_status: list[tuple[int, re.Match[str]]] = []
        self._texto_em: int | None = None
        self._texto_lido = False
        self._curtida_em: int | None = None
        self.status: re.Match[str] | None = None
        self.datahora: str | None = None
        self.texto: list[str] = []
        self.rotulo_da_curtida: str | None = None
        self.curtida_visivel: list[str] = []
        self.anuncio = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = {nome: valor or "" for nome, valor in attrs}
        testid = atributos.get("data-testid", "")
        if tag == "img" and self._texto_em is not None and atributos.get("alt"):
            self.texto.append(atributos["alt"])
        if tag in _VAZIOS:
            return
        self._pilha.append(tag)
        profundidade = len(self._pilha)
        if tag == "a":
            casou = _STATUS.match(atributos.get("href", ""))
            if casou:
                self._ancoras_de_status.append((profundidade, casou))
        elif tag == "time" and self.status is None and self._ancoras_de_status:
            self.status = self._ancoras_de_status[-1][1]
            self.datahora = atributos.get("datetime")
        if testid == "tweetText" and self._texto_em is None and not self._texto_lido:
            self._texto_em = profundidade
        elif testid in ("like", "unlike") and self.rotulo_da_curtida is None:
            self._curtida_em = profundidade
            self.rotulo_da_curtida = atributos.get("aria-label", "")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VAZIOS or tag not in self._pilha:
            return
        while self._pilha:
            profundidade = len(self._pilha)
            aberta = self._pilha.pop()
            if self._ancoras_de_status and self._ancoras_de_status[-1][0] == profundidade:
                self._ancoras_de_status.pop()
            if self._texto_em == profundidade:
                self._texto_em, self._texto_lido = None, True
            if self._curtida_em == profundidade:
                self._curtida_em = None
            if aberta == tag:
                break

    def handle_data(self, dados: str) -> None:
        if self._texto_em is not None:
            self.texto.append(dados)
            return
        if self._curtida_em is not None:
            self.curtida_visivel.append(dados)
        if dados.strip() in _ROTULOS_DE_ANUNCIO:
            self.anuncio = True
