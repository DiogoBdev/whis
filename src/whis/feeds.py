"""A coleta do jornal: feeds de `noticias/fontes.md` → candidatos por seção.

Lê RSS 2.0, RSS 0.91 e Atom com `xml.etree`, sem dependência nova, e prevê as
pegadinhas que as fontes reais mostraram (research/03): o UOL manda `pubDate`
em português e declara o charset só no cabeçalho HTTP; a Folha embrulha o link
em `redir.folha.com.br/…*https://…`; o YouTube devolve 404 intermitente para
canais que respondem minutos depois; e a rede inteira pode sumir por um
instante e voltar. Daí a segunda rodada para todos os que falharam — e só quem
falha nas duas vai para `fontes_com_falha`, com o código, para o rodapé
distinguir "deu erro" de "não tinha nada novo".

A coleta não escolhe nada: corta a janela de 24 h, deduplica, corta opinião
pela URL, tira o que já saiu e marca `destaque` no que cita um nome que ele
acompanha. Escolher é do editor.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

from .fontes_do_jornal import Fonte, FontesDoJornal
from .portas import Candidato

_log = logging.getLogger("whis.feeds")

UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
#: Alguns feeds recusam navegador sem cookie e aceitam um leitor que se identifica.
UA_LEITOR = "Whis/0.1 (+https://github.com/bielvelozo/whis; feed reader)"
ACEITA = "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"

TEMPO_LIMITE_S = 12.0
FEEDS_EM_PARALELO = 10
JANELA = timedelta(hours=24)
#: Relógio de fonte adiantado não pode tirar do jornal a notícia de agora há pouco.
TOLERANCIA_NO_FUTURO = timedelta(hours=1)
ITENS_LIDOS_POR_FEED = 40
TETO_POR_SECAO = 30
TETO_DO_TIME = 8
TAMANHO_DO_RESUMO = 220
TAMANHO_DA_CHAVE_DE_TITULO = 60
SECAO_DO_TIME = "time"

#: Coluna, blog, podcast e artigo são opinião; o jornal é de notícia.
OPINIAO = re.compile(r"/(?:colunas?|opiniao|blogs?|podcast|artigo)/", re.I)

_CODIGOS_DE_NOVA_TENTATIVA = frozenset({401, 403, 202})

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_PORTUGUES_PARA_INGLES = {
    "seg": "Mon", "ter": "Tue", "qua": "Wed", "qui": "Thu", "sex": "Fri", "sáb": "Sat", "sab": "Sat",
    "dom": "Sun", "fev": "Feb", "abr": "Apr", "mai": "May", "ago": "Aug", "set": "Sep", "out": "Oct",
    "dez": "Dec",
}

_TAG = re.compile(r"<[^>]+>")
_IMG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)", re.I)
_EXTENSAO_DE_IMAGEM = re.compile(r"\.(?:jpe?g|png|webp|gif|avif)(?:[?#]|$)", re.I)
_DECLARA_CODIFICACAO = re.compile(rb"^(?:\xef\xbb\xbf)?<\?xml[^>]*\bencoding\s*=", re.I)


@dataclass(frozen=True)
class ItemDeFeed:
    """Um item como o feed o descreve, antes de qualquer corte."""

    titulo: str
    link: str
    data: datetime
    resumo: str = ""
    imagem: str | None = None


@dataclass(frozen=True)
class Coleta:
    """Os candidatos de cada seção, os do time e as fontes que não responderam."""

    candidatos: dict[str, tuple[Candidato, ...]]
    time: tuple[Candidato, ...]
    fontes_com_falha: tuple[str, ...]


class FeedInvalido(ValueError):
    """O corpo não é RSS nem Atom."""


class _Falha(Exception):
    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class _Tarefa:
    secao: str
    fonte: Fonte


def coletar(
    fontes: FontesDoJornal,
    agora: datetime,
    *,
    ja_publicados: frozenset[str] = frozenset(),
    transporte: httpx.BaseTransport | None = None,
    espera_da_segunda_rodada_s: float = 3.0,
) -> Coleta:
    """Baixa todos os feeds e devolve os candidatos cortados e marcados."""
    agora = _em_utc(agora)
    tarefas = [_Tarefa(secao.id, fonte) for secao in fontes.secoes for fonte in secao.fontes]
    if fontes.time is not None:
        tarefas.append(_Tarefa(SECAO_DO_TIME, fontes.time))

    inicio = time.monotonic()
    with httpx.Client(
        transport=transporte, follow_redirects=True, timeout=TEMPO_LIMITE_S
    ) as cliente:
        lidos, falhas = _rodada(cliente, tarefas)
        if falhas:
            _log.info(
                "%d feed(s) falharam; segunda rodada em %.0f s", len(falhas), espera_da_segunda_rodada_s
            )
            if espera_da_segunda_rodada_s > 0:
                time.sleep(espera_da_segunda_rodada_s)
            recuperados, falhas = _rodada(cliente, list(falhas))
            lidos.update(recuperados)

    fontes_com_falha = tuple(
        f"{tarefa.fonte.nome} ({falhas[tarefa]})" for tarefa in tarefas if tarefa in falhas
    )
    for falha in fontes_com_falha:
        _log.warning("Feed sem resposta nas duas rodadas: %s", falha)

    nomes = _padrao_dos_nomes(fontes.nomes)
    candidatos = {
        secao.id: _candidatos(
            secao.id,
            [(t.fonte.nome, lidos.get(t, ())) for t in tarefas if t.secao == secao.id],
            agora,
            ja_publicados,
            nomes,
            teto=TETO_POR_SECAO,
            por_rodizio=True,
        )
        for secao in fontes.secoes
    }
    do_time = _candidatos(
        SECAO_DO_TIME,
        [(t.fonte.nome, lidos.get(t, ())) for t in tarefas if t.secao == SECAO_DO_TIME],
        agora,
        ja_publicados,
        nomes,
        teto=TETO_DO_TIME,
        por_rodizio=False,
    )
    _log.info(
        "Coleta em %.1f s: %s; time %d; %d fonte(s) com falha",
        time.monotonic() - inicio,
        ", ".join(f"{secao} {len(itens)}" for secao, itens in candidatos.items()),
        len(do_time),
        len(fontes_com_falha),
    )
    return Coleta(candidatos=candidatos, time=do_time, fontes_com_falha=fontes_com_falha)


_E_COMERCIAL_CRU = re.compile(rb"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)")


def interpretar_feed(corpo: bytes) -> tuple[ItemDeFeed, ...]:
    """Os primeiros itens de um RSS 2.0/0.91 ou Atom, sem os que não têm data.

    Levanta `FeedInvalido` (ou `ET.ParseError`) quando o corpo não é feed.
    """
    try:
        raiz = ET.fromstring(corpo)
    except ET.ParseError:
        # Feed com "&" cru num endereço (o Real Work From Anywhere, 2026-11-09): o
        # navegador lê, o XML estrito não. Escapa só o "&" que não abre entidade.
        raiz = ET.fromstring(_E_COMERCIAL_CRU.sub(b"&amp;", corpo))
    tipo = raiz.tag.rsplit("}", 1)[-1].lower()
    if tipo == "rss":
        canal = raiz.find("channel")
        entradas = canal.findall("item") if canal is not None else []
        ler = _item_rss
    elif tipo == "feed":
        entradas = raiz.findall("atom:entry", _NS)
        ler = _entrada_atom
    else:
        raise FeedInvalido(f"raiz <{tipo}> não é RSS nem Atom")
    itens = (ler(entrada) for entrada in entradas[:ITENS_LIDOS_POR_FEED])
    return tuple(item for item in itens if item is not None)


def _rodada(
    cliente: httpx.Client, tarefas: list[_Tarefa]
) -> tuple[dict[_Tarefa, tuple[ItemDeFeed, ...]], dict[_Tarefa, str]]:
    lidos: dict[_Tarefa, tuple[ItemDeFeed, ...]] = {}
    falhas: dict[_Tarefa, str] = {}
    if not tarefas:
        return lidos, falhas
    with ThreadPoolExecutor(max_workers=min(FEEDS_EM_PARALELO, len(tarefas))) as grupo:
        resultados = grupo.map(lambda tarefa: _ler_feed(cliente, tarefa.fonte), tarefas)
        for tarefa, (itens, motivo) in zip(tarefas, resultados):
            if motivo is None:
                lidos[tarefa] = itens
            else:
                _log.info("Feed %s falhou: %s", tarefa.fonte.nome, motivo)
                falhas[tarefa] = motivo
    return lidos, falhas


def _ler_feed(cliente: httpx.Client, fonte: Fonte) -> tuple[tuple[ItemDeFeed, ...], str | None]:
    try:
        return interpretar_feed(_baixar(cliente, fonte.url)), None
    except _Falha as falha:
        return (), falha.motivo
    except Exception as erro:
        return (), type(erro).__name__


def _baixar(cliente: httpx.Client, url: str) -> bytes:
    resposta: httpx.Response | None = None
    for agente in (UA_NAVEGADOR, UA_LEITOR):
        resposta = cliente.get(url, headers={"User-Agent": agente, "Accept": ACEITA})
        if resposta.status_code not in _CODIGOS_DE_NOVA_TENTATIVA and (
            resposta.status_code >= 400 or resposta.content.strip()
        ):
            break
    assert resposta is not None
    if resposta.status_code >= 400 or resposta.status_code in _CODIGOS_DE_NOVA_TENTATIVA:
        raise _Falha(f"HTTP {resposta.status_code}")
    if not resposta.content.strip():
        raise _Falha("vazio")
    return _corpo_xml(resposta)


def _corpo_xml(resposta: httpx.Response) -> bytes:
    """Bytes que o expat decodifica certo.

    Sem `encoding` na declaração XML, o expat supõe UTF-8 — e o UOL manda
    ISO-8859-1 declarado só no cabeçalho HTTP. Nesse caso o texto é
    decodificado pelo httpx (que lê o cabeçalho) e volta em UTF-8.
    """
    bruto = resposta.content.lstrip()
    if _DECLARA_CODIFICACAO.match(bruto):
        return bruto
    return resposta.text.lstrip("﻿ \t\r\n").encode("utf-8")


def _item_rss(item: ET.Element) -> ItemDeFeed | None:
    data = _data(_texto(item.find("pubDate"))) or _data(_texto(item.find("dc:date", _NS)))
    titulo = _limpo(_texto(item.find("title")))
    link = _desembrulhar(_texto(item.find("link")) or _guid_permalink(item))
    if data is None or not titulo or not link:
        return None
    resumo = _limpo(
        _texto(item.find("description")) or _texto(item.find("content:encoded", _NS)),
        TAMANHO_DO_RESUMO,
    )
    corpos = (_texto(item.find("content:encoded", _NS)), _texto(item.find("description")))
    return ItemDeFeed(titulo=titulo, link=link, data=data, resumo=resumo, imagem=_imagem(item, corpos))


def _entrada_atom(entrada: ET.Element) -> ItemDeFeed | None:
    data = _data(_texto(entrada.find("atom:published", _NS))) or _data(
        _texto(entrada.find("atom:updated", _NS))
    )
    titulo = _limpo(_texto(entrada.find("atom:title", _NS)))
    link = _desembrulhar(_link_atom(entrada))
    if data is None or not titulo or not link:
        return None
    resumo = _limpo(
        _texto(entrada.find("atom:summary", _NS))
        or _texto(entrada.find("atom:content", _NS))
        or _texto(entrada.find("media:group/media:description", _NS)),
        TAMANHO_DO_RESUMO,
    )
    corpos = (_texto(entrada.find("atom:content", _NS)), _texto(entrada.find("atom:summary", _NS)))
    return ItemDeFeed(titulo=titulo, link=link, data=data, resumo=resumo, imagem=_imagem(entrada, corpos))


def _link_atom(entrada: ET.Element) -> str:
    for link in entrada.findall("atom:link", _NS):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href", "").strip()
    return ""


def _guid_permalink(item: ET.Element) -> str:
    guid = item.find("guid")
    if guid is not None and guid.get("isPermaLink", "true").lower() == "true":
        valor = _texto(guid)
        if valor.startswith(("http://", "https://")):
            return valor
    return ""


def _desembrulhar(link: str) -> str:
    if "redir.folha.com.br" in link and "*" in link:
        return link.split("*", 1)[1]
    return link


def _imagem(elemento: ET.Element, corpos: Iterable[str]) -> str | None:
    for caminho in (
        "media:thumbnail",
        "media:content",
        "media:group/media:thumbnail",
        "media:group/media:content",
        "enclosure",
    ):
        for midia in elemento.iterfind(caminho, _NS):
            url = _url_de_imagem(midia.get("url", ""))
            tipo = f"{midia.get('type', '')} {midia.get('medium', '')}".lower()
            if url and (
                "image" in tipo or caminho.endswith("thumbnail") or _EXTENSAO_DE_IMAGEM.search(url)
            ) and "svg" not in tipo:
                return url
    for corpo in corpos:
        for src in _IMG.findall(corpo or ""):
            url = _url_de_imagem(html.unescape(src))
            if url:
                return url
    return None


def _url_de_imagem(url: str) -> str | None:
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return None
    caminho = url.split("?", 1)[0].split("#", 1)[0]
    if caminho.lower().endswith(".svg"):
        return None
    return url


def _data(texto: str) -> datetime | None:
    texto = texto.strip()
    if not texto:
        return None
    for candidato in (texto, _data_em_ingles(texto)):
        for interpretar in (parsedate_to_datetime, _iso):
            try:
                data = interpretar(candidato)
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
            if data is not None:
                return _em_utc(data)
    return None


def _iso(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def _data_em_ingles(texto: str) -> str:
    return re.sub(
        r"[A-Za-zÀ-ÿ]{3}",
        lambda casou: _PORTUGUES_PARA_INGLES.get(casou.group(0).lower(), casou.group(0)),
        texto,
    )


def _em_utc(data: datetime) -> datetime:
    return (data if data.tzinfo else data.replace(tzinfo=UTC)).astimezone(UTC)


def _texto(elemento: ET.Element | None) -> str:
    if elemento is None:
        return ""
    return (elemento.text or "").strip()


def _limpo(texto: str, tamanho: int | None = None) -> str:
    limpo = html.unescape(_TAG.sub(" ", html.unescape(texto or "")))
    limpo = _TAG.sub(" ", limpo)
    limpo = re.sub(r"\s+", " ", limpo).strip()
    if tamanho is not None and len(limpo) > tamanho:
        limpo = limpo[: tamanho - 1].rstrip() + "…"
    return limpo


def _chave_do_titulo(titulo: str) -> str:
    return re.sub(r"[\W_]+", "", titulo.casefold())[:TAMANHO_DA_CHAVE_DE_TITULO]


def _padrao_dos_nomes(nomes: tuple[str, ...]) -> re.Pattern[str] | None:
    if not nomes:
        return None
    alternativas = "|".join(re.escape(nome) for nome in sorted(nomes, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternativas})(?!\w)", re.IGNORECASE)


def _candidatos(
    secao: str,
    por_fonte: list[tuple[str, tuple[ItemDeFeed, ...]]],
    agora: datetime,
    ja_publicados: frozenset[str],
    nomes: re.Pattern[str] | None,
    *,
    teto: int,
    por_rodizio: bool,
) -> tuple[Candidato, ...]:
    desde = agora - JANELA
    ate = agora + TOLERANCIA_NO_FUTURO
    juntos = [
        (fonte, item)
        for fonte, itens in por_fonte
        for item in itens
        if desde <= item.data <= ate
        and not OPINIAO.search(item.link)
        and item.link not in ja_publicados
    ]
    juntos.sort(key=lambda par: par[1].data, reverse=True)

    vistos: set[str] = set()
    unicos: list[tuple[str, ItemDeFeed, bool]] = []
    for fonte, item in juntos:
        chave = _chave_do_titulo(item.titulo)
        if item.link in vistos or (chave and chave in vistos):
            continue
        vistos.update({item.link, chave})
        destaque = bool(nomes and nomes.search(f"{item.titulo} {item.resumo}"))
        unicos.append((fonte, item, destaque))

    if por_rodizio:
        unicos = _destaques_e_rodizio(unicos)
    return tuple(
        Candidato(
            id=f"{secao}-{n}",
            secao=secao,
            fonte=fonte,
            titulo=item.titulo,
            link=item.link,
            resumo=item.resumo,
            data=item.data,
            imagem=item.imagem,
            destaque=destaque,
        )
        for n, (fonte, item, destaque) in enumerate(unicos[:teto], 1)
    )


def _destaques_e_rodizio(
    unicos: list[tuple[str, ItemDeFeed, bool]],
) -> list[tuple[str, ItemDeFeed, bool]]:
    """Destaques primeiro; depois a mais nova de cada fonte, a segunda de cada uma…

    Sem o rodízio, uma fonte que publica 40 itens por dia ocuparia as 30
    vagas sozinha e a fonte pequena nunca chegaria ao editor. `unicos` já vem
    do mais novo para o mais velho, e cada rodada sai nessa mesma ordem.
    """
    destaques = [trio for trio in unicos if trio[2]]
    por_fonte: dict[str, list[tuple[str, ItemDeFeed, bool]]] = {}
    for trio in unicos:
        if not trio[2]:
            por_fonte.setdefault(trio[0], []).append(trio)
    rodadas = max((len(itens) for itens in por_fonte.values()), default=0)
    ordenados = list(destaques)
    for rodada in range(rodadas):
        da_rodada = [itens[rodada] for itens in por_fonte.values() if rodada < len(itens)]
        ordenados.extend(sorted(da_rodada, key=lambda trio: trio[1].data, reverse=True))
    return ordenados
