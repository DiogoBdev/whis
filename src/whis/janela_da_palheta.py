"""A janela da palheta, copiada do Raycast: campo grande, lista de ações, rodapé com as teclas.

Tk, que já vem com o Python. A janela é um `overrideredirect` com cantos
arredondados pelo DWM do Windows 11 (`dwm.py`; sem ele, `-transparentcolor`),
e o fundo é o acrílico simulado de `fundo_desfocado.py`: a cada abertura, com
a janela ainda escondida, a tela atrás dela é capturada, desfocada e
escurecida, e vai como imagem embaixo de tudo — é o que deixa ver o que está
atrás como manchas, como no Raycast, já que o DWM deste build não desfoca as
janelas atrás de um popup e um `-alpha` mostraria o de trás nítido. O desenho
é o do Raycast de propósito, a pedido do usuário: painel quase preto, borda
fina de 1 px, o campo em cima, uma linha por ação — glifo, título, descrição
e, à direita, o nome do comando — com a selecionada num realce arredondado
que não encosta na borda, e um rodapé com o nome e as teclas. O que é roxo é o
que é do Whis.

Tudo é desenhado no canvas; o único widget dentro dele é o `Text` de uma linha
(Segoe UI 18) com placeholder — opaco, ele ganha a cor média da faixa
desfocada embaixo dele — em que a primeira palavra ganha cor quando é um
comando, ou o texto inteiro, quando é o rótulo de uma ação. A lista é
filtrada pelo que se digita (pelo nome do comando e por qualquer palavra do
rótulo). ↑/↓ escolhem; Tab completa o campo com o comando da selecionada (e,
já completo, passa à próxima); Enter executa o texto digitado — um rótulo
inteiro vale como o comando — ou, com o campo vazio, a ação selecionada;
clicar numa linha executa; Esc e perder o foco escondem. Cabem sete linhas; o
resto rola com a seleção, com a roda do mouse ou arrastando a barra da direita.

Uma ação pode abrir uma **lista** (`Acao.abre`), como a dos prompts: a janela
não some, troca os comandos pelas linhas que a `Lista` devolve para o texto
digitado, mostra o detalhe da selecionada embaixo e liga as teclas dos
`Gesto`s dela, com as dicas no rodapé. Esc, ou Backspace com o campo vazio,
volta aos comandos. O fundo é capturado já na altura da lista cheia: com a
janela no ar, a captura da tela a pegaria junto.

O Tk só aceita chamadas do thread que o criou. O atalho global e o menu da
bandeja vivem em outros threads, então tudo que eles pedem entra por `pedir`,
numa fila que o thread do Tk esvazia a cada 50 ms — o arranjo provado pela
sonda da camada 3. Nada longo roda no handler: quem despacha devolve na hora.

`PortaJanela` é o que a palheta e a bandeja enxergam; o teste monta um dublê.
O filtro, o destaque e o completar são funções puras, testadas sem Tk.
"""

from __future__ import annotations

import logging
import queue
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

_log = logging.getLogger("whis.janela_da_palheta")

LARGURA = 720
RAIO = 12
MARGEM = 12
ALTURA_DO_CAMPO = 52
ALTURA_DA_LINHA = 40
ALTURA_DO_RODAPE = 40
#: O realce da linha selecionada fica este tanto para dentro da borda do painel.
RECUO_DA_LINHA = 8
import sys as _sys

_MAC = _sys.platform == "darwin"
#: A fonte do sistema: Segoe UI no Windows, Helvetica Neue no Mac (o Tk do Mac
#: desenha pontos maiores, por isso os tamanhos sobem um pouco lá).
_FAMILIA = "Helvetica Neue" if _MAC else "Segoe UI"
_ESCALA = 1.25 if _MAC else 1.0
FONTE = (_FAMILIA, round(18 * _ESCALA))
FONTE_DA_LINHA = (_FAMILIA, round(11 * _ESCALA))
FONTE_DA_DESCRICAO = (_FAMILIA, round(9 * _ESCALA))
FONTE_DO_ICONE = (_FAMILIA, round(12 * _ESCALA))
FONTE_DO_RODAPE = (_FAMILIA, round(9 * _ESCALA))

FUNDO = "#0e0e10"
FUNDO_SELECIONADO = "#232327"
SEPARADOR = "#222226"
BORDA = "#4a4380"
DESTAQUE = "#8b7cf6"
TEXTO = "#f2f2f2"
TEXTO_APAGADO = "#8a8a92"
TECLA = "#1c1c20"
BORDA_DA_TECLA = "#36363c"
#: Uma cor que nenhum pixel do painel usa: é o que o Windows torna transparente
#: nos cantos, no fallback sem os cantos do DWM.
COR_TRANSPARENTE = "#010203"

#: Quanto o `FUNDO` cobre o desfoque do que está atrás: 1.0 é um painel opaco.
OPACIDADE_DO_FUNDO = 0.84

PLACEHOLDER = "Type a command…"

#: Quantas linhas cabem de uma vez, nos comandos e numa lista; o resto rola.
MAXIMO_DE_LINHAS = 7
LARGURA_DA_BARRA = 4
#: A faixa da direita em que um clique pega a barra de rolagem, e não a linha.
ALCANCE_DA_BARRA = 14
COR_DA_BARRA = "#3a3a42"
LINHAS_DO_DETALHE = 7
ALTURA_DO_DETALHE = 150
LARGURA_DO_DETALHE_EM_CARACTERES = 108

#: A cadência com que o thread do Tk olha a fila de pedidos de outros threads.
INTERVALO_DA_FILA_MS = 50

#: Quanto esperar depois de perder o foco antes de esconder: o foco pode estar
#: só trocando de widget dentro da própria janela.
ATRASO_DO_FOCO_MS = 150


@dataclass(frozen=True)
class Acao:
    """Uma linha da lista: o nome do comando, o glifo, o título, a descrição e se leva argumento.

    `abre`, quando há, faz a ação trocar a palheta por uma `Lista` em vez de despachar.
    """

    nome: str
    icone: str
    rotulo: str | Callable[[], str]
    descricao: str = ""
    aceita_argumento: bool = False
    abre: Callable[[], "Lista"] | None = None

    def rotulo_atual(self) -> str:
        return self.rotulo() if callable(self.rotulo) else self.rotulo


@dataclass(frozen=True)
class Linha:
    """Uma linha de uma `Lista`: glifo, título, descrição e o texto da direita."""

    icone: str
    titulo: str
    descricao: str = ""
    direita: str = ""


@dataclass(frozen=True)
class Efeito:
    """O que um gesto que mantém a lista aberta pede à janela depois de rodar."""

    selecionar: int | None = None
    mensagem: str = ""


@dataclass(frozen=True)
class Gesto:
    """Uma tecla da lista: a sequência do Tk, como ela aparece no rodapé e o que faz.

    `rodar(indice, texto)` recebe a linha selecionada (ou `None`) e o texto do
    campo. Com `esconde`, a janela some antes de rodar — é o que devolve o foco
    a quem estava atrás; sem, a lista é relida e redesenhada depois.
    """

    tecla: str
    desenho: str
    dica: str
    rodar: Callable[[int | None, str], Efeito | None]
    esconde: bool = False


class Lista(Protocol):
    nome: str
    placeholder: str
    vazia: str
    gestos: Sequence[Gesto]

    def linhas(self, texto: str) -> Sequence[Linha]: ...

    def detalhe(self, indice: int) -> str: ...


def filtrar(acoes: Sequence[Acao], texto: str) -> tuple[Acao, ...]:
    """As ações que a primeira palavra digitada começa: pelo nome do comando primeiro, depois
    por qualquer palavra do rótulo; todas com o campo vazio.

    O nome exato passa à frente de quem só começa igual — senão `do` + Tab
    completaria `doubt`, que é o que a lista traria selecionado.
    """
    palavras = texto.split()
    if not palavras:
        return tuple(acoes)
    prefixo = palavras[0].casefold()
    pelo_nome = tuple(
        sorted(
            (acao for acao in acoes if acao.nome.casefold().startswith(prefixo)),
            key=lambda acao: acao.nome.casefold() != prefixo,
        )
    )
    pelo_rotulo = tuple(
        acao
        for acao in acoes
        if acao not in pelo_nome
        and any(palavra.startswith(prefixo) for palavra in acao.rotulo_atual().casefold().split())
    )
    return pelo_nome + pelo_rotulo


def comando_pelo_rotulo(texto: str, acoes: Sequence[Acao]) -> str | None:
    """O nome do comando cujo rótulo é o texto digitado (sem diferenciar maiúsculas), ou `None`."""
    procurado = " ".join(texto.split()).casefold()
    if not procurado:
        return None
    return next((acao.nome for acao in acoes if acao.rotulo_atual().casefold() == procurado), None)


def completar(acao: Acao) -> str:
    """O que o Tab põe no campo: o comando, e um espaço quando ele aceita argumento."""
    return f"{acao.nome} " if acao.aceita_argumento else acao.nome


def comando_destacado(texto: str, acoes: Sequence[Acao]) -> tuple[int, int] | None:
    """`(inicio, fim)` do destaque: a primeira palavra quando é exatamente um comando, ou o
    texto inteiro quando é exatamente um rótulo; senão `None`."""
    sem_indentacao = texto.lstrip()
    inicio = len(texto) - len(sem_indentacao)
    miolo = sem_indentacao.rstrip()
    if not miolo:
        return None
    palavra = miolo.split(None, 1)[0]
    if palavra.casefold() in {acao.nome.casefold() for acao in acoes}:
        return inicio, inicio + len(palavra)
    if comando_pelo_rotulo(miolo, acoes) is not None:
        return inicio, inicio + len(miolo)
    return None


def acao_que_abre(texto: str, acoes: Sequence[Acao]) -> tuple[Acao, str] | None:
    """A ação de lista que o texto chama — pelo nome, com o resto como busca, ou pelo rótulo inteiro."""
    miolo = " ".join(texto.split())
    if not miolo:
        return None
    primeira, _, resto = miolo.partition(" ")
    for acao in acoes:
        if acao.abre is None:
            continue
        if acao.nome.casefold() == primeira.casefold():
            return acao, resto
        if acao.rotulo_atual().casefold() == miolo.casefold():
            return acao, ""
    return None


def texto_do_detalhe(texto: str) -> str:
    """O começo do texto quebrado na largura do painel, com `…` quando não cabe."""
    visuais: list[str] = []
    for linha in texto.splitlines() or [""]:
        visuais.extend(textwrap.wrap(linha, LARGURA_DO_DETALHE_EM_CARACTERES) or [""])
    if len(visuais) <= LINHAS_DO_DETALHE:
        return "\n".join(visuais)
    return "\n".join([*visuais[: LINHAS_DO_DETALHE - 1], visuais[LINHAS_DO_DETALHE - 1] + " …"])


class PortaJanela(Protocol):
    """O que a palheta precisa de uma janela — Tk ou dublê."""

    def mostrar(self) -> None: ...

    def esconder(self) -> None: ...

    def ao_enviar(self, callback: Callable[[str], object]) -> None: ...

    def definir_acoes(self, acoes: Sequence[Acao]) -> None: ...


class JanelaDaPalheta:
    """A janela de verdade. Cria o `Tk` — só uma por processo, no thread principal."""

    def __init__(self) -> None:
        import tkinter as tk

        raiz = tk.Tk()
        raiz.withdraw()
        raiz.title("Whis")
        raiz.overrideredirect(True)
        raiz.attributes("-topmost", True)
        raiz.configure(background=FUNDO)

        tela = tk.Canvas(raiz, highlightthickness=0, bd=0, background=FUNDO)
        tela.pack(fill="both", expand=True)
        tela.bind("<Motion>", self._ao_mover_o_mouse)
        tela.bind("<Button-1>", self._ao_clicar)
        tela.bind("<B1-Motion>", self._ao_arrastar)
        tela.bind("<MouseWheel>", self._roda)

        campo = tk.Text(
            tela,
            height=1,
            font=FONTE,
            wrap="none",
            relief="flat",
            bd=0,
            highlightthickness=0,
            background=FUNDO,
            foreground=TEXTO,
            insertbackground=TEXTO,
            padx=8,
            pady=6,
            undo=False,
        )
        campo.tag_configure("comando", foreground=DESTAQUE, font=(*FONTE, "bold"))
        campo.bind("<Return>", self._enter)
        campo.bind("<KP_Enter>", self._enter)
        campo.bind("<Escape>", self._esc)
        campo.bind("<BackSpace>", self._apagar)
        campo.bind("<Up>", lambda _evento: self._mover_selecao(-1))
        campo.bind("<Down>", lambda _evento: self._mover_selecao(1))
        campo.bind("<Tab>", self._tab)
        campo.bind("<MouseWheel>", self._roda)
        campo.bind("<KeyRelease>", lambda _evento: self._atualizar())
        campo.bind("<FocusOut>", self._ao_perder_o_foco)

        placeholder = tk.Label(
            campo, text=PLACEHOLDER, font=FONTE, background=FUNDO, foreground=TEXTO_APAGADO
        )
        placeholder.bind("<Button-1>", lambda _evento: campo.focus_set())

        # O campo entra no canvas uma vez só: apagá-lo a cada redesenho desmontaria
        # o widget, e com ele o foco e o cursor.
        janela_do_campo = tela.create_window(
            MARGEM, MARGEM, anchor="nw", window=campo, width=LARGURA - 2 * MARGEM, height=ALTURA_DO_CAMPO
        )

        self._tk = tk
        self._raiz = raiz
        self._tela = tela
        self._campo = campo
        self._janela_do_campo = janela_do_campo
        self._placeholder = placeholder
        self._fila: queue.Queue[Callable[[], object]] = queue.Queue()
        self._callback: Callable[[str], object] | None = None
        self._acoes: tuple[Acao, ...] = ()
        self._visiveis: tuple[Acao, ...] = ()
        self._lista: Lista | None = None
        self._linhas_da_lista: tuple[Linha, ...] | None = None
        #: As teclas dos gestos da lista aberta, para desligar ao sair dela.
        self._teclas_ligadas: list[str] = []
        #: A primeira linha desenhada: a lista rola quando a seleção sai da vista.
        self._primeira = 0
        self._arrastando = False
        self._mensagem = ""
        #: Os realces das linhas desenhadas, um item do canvas por linha visível.
        self._linhas: list[int] = []
        self._topo_das_linhas = 0
        self._posicao = (0, 0)
        self._altura = 0
        #: A imagem de fundo precisa de uma referência viva, ou o Tk a solta.
        self._foto = None
        self._selecionada = 0
        self._visivel = False
        self._encerrada = False
        self._vestida = False
        self._desenhar()

    @property
    def raiz(self):
        """O `Tk` — é dele que o painel de configuração nasce como `Toplevel`."""
        return self._raiz

    # -- a porta ------------------------------------------------------------

    def ao_enviar(self, callback: Callable[[str], object]) -> None:
        self._callback = callback

    def definir_acoes(self, acoes: Sequence[Acao]) -> None:
        self._acoes = tuple(acoes)
        self._atualizar()

    def mostrar(self) -> None:
        if not self._visivel:
            self._lembrar_janela_anterior()
        self._sair_da_lista()
        self._campo.delete("1.0", "end")
        self._selecionada = 0
        # A lista pode ter mudado com a janela escondida (o painel liga e
        # desliga ações): é sempre redesenhada ao abrir.
        self._visiveis = ()
        self._atualizar()
        self._pintar_o_fundo()
        self._raiz.deiconify()
        self._raiz.lift()
        self._vestir()
        self._campo.focus_force()
        self._visivel = True

    def esconder(self) -> None:
        self._raiz.withdraw()
        self._visivel = False

    def visivel(self) -> bool:
        return self._visivel

    def texto(self) -> str:
        return self._campo.get("1.0", "end-1c")

    def acoes_visiveis(self) -> tuple[Acao, ...]:
        return self._visiveis

    def titulos_desenhados(self) -> list[str]:
        """Os títulos como estão na tela agora (para o teste ver um rótulo dinâmico mudar)."""
        return [self._tela.itemcget(item, "text") for item in self._tela.find_withtag("titulo")]

    def selecionada(self) -> Acao | None:
        if not self._visiveis:
            return None
        return self._visiveis[min(self._selecionada, len(self._visiveis) - 1)]

    def lista_aberta(self) -> Lista | None:
        return self._lista

    def linhas_da_lista(self) -> tuple[Linha, ...]:
        return self._linhas_da_lista or ()

    def indice_selecionado(self) -> int | None:
        total = self._total()
        return min(self._selecionada, total - 1) if total else None

    def detalhe_desenhado(self) -> str:
        itens = self._tela.find_withtag("detalhe")
        return self._tela.itemcget(itens[0], "text") if itens else ""

    def mensagem(self) -> str:
        return self._mensagem

    def abrir_lista(self, lista: Lista, texto: str = "") -> None:
        """Troca os comandos pela lista, com a janela no ar (abre se estiver escondida)."""
        if not self._visivel:
            self.mostrar()
        self._sair_da_lista()
        self._lista = lista
        for gesto in lista.gestos:
            if gesto.tecla == "<Return>":
                continue
            for sequencia in _variantes(gesto.tecla):
                self._campo.bind(sequencia, lambda _evento, g=gesto: self._gesto(g))
                self._teclas_ligadas.append(sequencia)
        self._placeholder.configure(text=lista.placeholder)
        self._campo.delete("1.0", "end")
        self._campo.insert("1.0", texto)
        self._campo.mark_set("insert", "end")
        self._atualizar()

    def digitar(self, texto: str) -> None:
        """Põe texto no campo como se tivesse sido digitado (para a sonda e o teste)."""
        self._campo.delete("1.0", "end")
        self._campo.insert("1.0", texto)
        self._atualizar()

    def pedir(self, acao: Callable[[], object]) -> None:
        """Roda `acao` no thread do Tk — a única porta para os outros threads."""
        self._fila.put(acao)

    def executar(self) -> None:
        """O `mainloop`, com a fila sondada a cada 50 ms. Devolve quando `encerrar` roda."""
        self._raiz.after(INTERVALO_DA_FILA_MS, self._sondar)
        self._raiz.mainloop()

    def encerrar(self) -> None:
        if self._encerrada:
            return
        self._encerrada = True
        self._raiz.after(0, self._raiz.destroy)

    # -- o desenho ------------------------------------------------------------

    def _lembrar_janela_anterior(self) -> None:
        from . import area_de_transferencia

        try:
            area_de_transferencia.lembrar_janela_anterior()
        except Exception as erro:  # noqa: BLE001 — sem ela, colar vai para quem ficar ativo
            _log.debug("Sem a janela anterior: %s", erro)

    def _pintar_o_fundo(self) -> None:
        """O acrílico simulado, antes de a janela aparecer: a tela atrás dela, desfocada e escurecida."""
        from PIL import ImageTk

        from . import fundo_desfocado

        x, y = self._posicao
        cabe_na_tela = self._raiz.winfo_screenheight() - y
        altura = max(self._altura, min(_altura_da_lista_cheia(), cabe_na_tela))
        try:
            atras = fundo_desfocado.capturar_atras(x, y, LARGURA, altura)
        except Exception as erro:  # noqa: BLE001 — sem captura o painel é liso, nunca ausente
            _log.debug("Sem o desfoque do fundo: %s", erro)
            atras = fundo_desfocado.fundo_liso(LARGURA, altura, FUNDO)
        imagem = fundo_desfocado.desfocar(
            atras, cor=FUNDO, opacidade=OPACIDADE_DO_FUNDO, raio_dos_cantos=RAIO, cor_de_fora=COR_TRANSPARENTE
        )
        if _MAC:
            # No Mac a janela é transparente de verdade: os cantos viram alfa zero.
            from PIL import Image, ImageDraw

            mascara = Image.new("L", imagem.size, 0)
            ImageDraw.Draw(mascara).rounded_rectangle((0, 0, imagem.width - 1, imagem.height - 1), RAIO, fill=255)
            imagem = imagem.convert("RGBA")
            imagem.putalpha(mascara)
        self._foto = ImageTk.PhotoImage(imagem, master=self._raiz)
        self._tela.delete("fundo")
        self._tela.create_image(0, 0, anchor="nw", image=self._foto, tags=("fundo",))
        self._tela.tag_lower("fundo")
        cor_do_campo = fundo_desfocado.cor_media(
            imagem, (MARGEM, MARGEM, LARGURA - MARGEM, MARGEM + ALTURA_DO_CAMPO)
        )
        self._campo.configure(background=cor_do_campo)
        self._placeholder.configure(background=cor_do_campo)

    def _vestir(self) -> None:
        """Uma vez, com a janela já mapeada (só então existe o HWND que o DWM enxerga)."""
        if self._vestida:
            return
        self._vestida = True
        if _MAC:
            try:
                self._raiz.attributes("-transparent", True)
                self._raiz.configure(background="systemTransparent")
                self._tela.configure(background="systemTransparent")
            except self._tk.TclError as erro:
                _log.debug("Sem a janela transparente do Mac: %s", erro)
            return
        from . import dwm

        if dwm.arredondar_cantos(dwm.hwnd_da_raiz(self._raiz)):
            return
        # Sem o DWM, os cantos de fora da imagem de fundo (na cor-chave) somem por chave de cor.
        self._raiz.configure(background=COR_TRANSPARENTE)
        self._tela.configure(background=COR_TRANSPARENTE)
        try:
            self._raiz.attributes("-transparentcolor", COR_TRANSPARENTE)
        except self._tk.TclError:  # fora do Windows os cantos ficam quadrados
            pass

    def _atualizar(self) -> None:
        """Refaz o destaque, o placeholder e a lista a partir do texto de agora."""
        texto = self.texto()
        if "\n" in texto:
            texto = texto.replace("\n", " ")
            self._campo.delete("1.0", "end")
            self._campo.insert("1.0", texto)

        self._campo.tag_remove("comando", "1.0", "end")
        if self._lista is None:
            faixa = comando_destacado(texto, self._acoes)
            if faixa is not None:
                self._campo.tag_add("comando", f"1.{faixa[0]}", f"1.{faixa[1]}")

        self._mostrar_placeholder(not texto)

        if self._lista is not None:
            linhas = tuple(self._lista.linhas(texto))
            if linhas != self._linhas_da_lista:
                self._linhas_da_lista = linhas
                self._selecionada = 0
                self._primeira = 0
                self._desenhar()
            return

        visiveis = filtrar(self._acoes, texto)
        if visiveis != self._visiveis:
            self._visiveis = visiveis
            self._selecionada = 0
            self._primeira = 0
            self._desenhar()
        else:
            self._pintar_selecao()

    def _mostrar_placeholder(self, mostrar: bool) -> None:
        if mostrar:
            self._placeholder.place(x=8, y=6)
        else:
            self._placeholder.place_forget()

    def _linhas_de_agora(self) -> tuple[Linha, ...]:
        if self._lista is not None:
            return self._linhas_da_lista or ()
        return tuple(Linha(acao.icone, acao.rotulo_atual(), acao.descricao, acao.nome) for acao in self._visiveis)

    def _total(self) -> int:
        return len(self._linhas_da_lista or ()) if self._lista is not None else len(self._visiveis)

    def _cabem(self) -> int:
        return MAXIMO_DE_LINHAS

    def rola(self) -> bool:
        return self._total() > self._cabem()

    def primeira_desenhada(self) -> int:
        return self._primeira

    def _desenhar(self) -> None:
        tela = self._tela
        todas = self._linhas_de_agora()
        desenhadas = todas[self._primeira : self._primeira + self._cabem()]
        topo_da_lista = MARGEM + ALTURA_DO_CAMPO + 14
        linhas_de_altura = len(desenhadas) or (1 if self._lista is not None else 0)
        altura_da_lista = linhas_de_altura * ALTURA_DA_LINHA + 8 if linhas_de_altura else 0
        altura_do_detalhe = ALTURA_DO_DETALHE if self._lista is not None and todas else 0
        self._painel(topo_da_lista + altura_da_lista + altura_do_detalhe + ALTURA_DO_RODAPE)

        self._linhas = []
        self._topo_das_linhas = topo_da_lista + 4
        if self._lista is not None and not todas:
            tela.create_text(
                RECUO_DA_LINHA + 22,
                self._topo_das_linhas + ALTURA_DA_LINHA // 2,
                text=self._lista.vazia,
                font=FONTE_DA_DESCRICAO,
                fill=TEXTO_APAGADO,
                anchor="w",
                tags=("desenho",),
            )
        for indice, linha in enumerate(desenhadas):
            y = self._topo_das_linhas + indice * ALTURA_DA_LINHA
            centro = y + ALTURA_DA_LINHA // 2
            marca = f"linha{indice}"
            realce = tela.create_polygon(
                *_cantos_arredondados(RECUO_DA_LINHA, y, LARGURA - RECUO_DA_LINHA, y + ALTURA_DA_LINHA, 8),
                smooth=True,
                fill="",
                outline="",
                tags=(marca, "desenho"),
            )
            tela.create_text(
                RECUO_DA_LINHA + 22, centro, text=linha.icone, font=FONTE_DO_ICONE, fill=DESTAQUE, tags=(marca, "desenho")
            )
            direita = tela.create_text(
                LARGURA - RECUO_DA_LINHA - 16,
                centro,
                text=linha.direita,
                font=FONTE_DA_DESCRICAO,
                fill=DESTAQUE,
                anchor="e",
                tags=(marca, "desenho"),
            )
            limite = tela.bbox(direita)[0] - 16 if linha.direita else LARGURA - RECUO_DA_LINHA - 16
            titulo = tela.create_text(
                RECUO_DA_LINHA + 44,
                centro,
                text=self._caber(linha.titulo, FONTE_DA_LINHA, limite - RECUO_DA_LINHA - 44),
                font=FONTE_DA_LINHA,
                fill=TEXTO,
                anchor="w",
                tags=(marca, "titulo", "desenho"),
            )
            inicio_da_descricao = tela.bbox(titulo)[2] + 12
            tela.create_text(
                inicio_da_descricao,
                centro + 1,
                text=self._caber(linha.descricao, FONTE_DA_DESCRICAO, limite - inicio_da_descricao),
                font=FONTE_DA_DESCRICAO,
                fill=TEXTO_APAGADO,
                anchor="w",
                tags=(marca, "desenho"),
            )
            self._linhas.append(realce)
        if altura_do_detalhe:
            topo_do_detalhe = topo_da_lista + altura_da_lista
            tela.create_line(1, topo_do_detalhe, LARGURA - 1, topo_do_detalhe, fill=SEPARADOR, tags=("desenho",))
            tela.create_text(
                MARGEM + 10,
                topo_do_detalhe + 12,
                text="",
                font=FONTE_DA_DESCRICAO,
                fill=TEXTO,
                anchor="nw",
                tags=("desenho", "detalhe"),
            )
        if len(todas) > len(desenhadas):
            self._desenhar_a_barra(len(todas), len(desenhadas))
        tela.tag_lower("fundo")
        self._pintar_selecao()

    def _faixa_da_barra(self) -> tuple[int, int]:
        topo = self._topo_das_linhas
        return topo, topo + self._cabem() * ALTURA_DA_LINHA

    def _desenhar_a_barra(self, total: int, cabem: int) -> None:
        topo, fundo = self._faixa_da_barra()
        altura = fundo - topo
        polegar = max(24, altura * cabem // total)
        inicio = topo + (altura - polegar) * self._primeira // max(total - cabem, 1)
        direita = LARGURA - 3
        self._tela.create_polygon(
            *_cantos_arredondados(direita - LARGURA_DA_BARRA, inicio, direita, inicio + polegar, 2),
            smooth=True,
            fill=COR_DA_BARRA,
            outline="",
            tags=("desenho", "barra"),
        )

    def _rolar_para(self, y: int) -> None:
        """Põe o polegar da barra sob `y` e leva a seleção junto quando ela sai da vista."""
        topo, fundo = self._faixa_da_barra()
        total, cabem = self._total(), self._cabem()
        fracao = min(max((y - topo) / max(fundo - topo, 1), 0.0), 1.0)
        primeira = round(fracao * (total - cabem))
        if primeira == self._primeira:
            return
        self._primeira = primeira
        self._selecionada = min(max(self._selecionada, primeira), primeira + cabem - 1)
        self._desenhar()

    def _caber(self, texto: str, fonte, largura: int) -> str:
        """O texto cortado com `…` para caber em `largura` pixels na fonte dada."""
        from tkinter import font as tkfont

        if largura <= 0 or not texto:
            return ""
        medida = tkfont.Font(root=self._raiz, font=fonte)
        if medida.measure(texto) <= largura:
            return texto
        while texto and medida.measure(texto + "…") > largura:
            texto = texto[:-1]
        return texto.rstrip() + "…" if texto else ""

    def _painel(self, altura: int) -> None:
        raiz, tela = self._raiz, self._tela
        x = (raiz.winfo_screenwidth() - LARGURA) // 2
        y = raiz.winfo_screenheight() // 4
        raiz.geometry(f"{LARGURA}x{altura}+{x}+{y}")
        self._posicao = (x, y)
        self._altura = altura
        tela.configure(width=LARGURA, height=altura)
        tela.delete("desenho")
        tela.create_polygon(
            *_cantos_arredondados(1, 1, LARGURA - 1, altura - 1, RAIO),
            smooth=True,
            fill="",
            outline=BORDA,
            width=1,
            tags=("desenho",),
        )
        separador = MARGEM + ALTURA_DO_CAMPO + 7
        tela.create_line(1, separador, LARGURA - 1, separador, fill=SEPARADOR, tags=("desenho",))
        self._rodape(altura)

    def _rodape(self, altura: int) -> None:
        tela = self._tela
        topo = altura - ALTURA_DO_RODAPE
        centro = topo + ALTURA_DO_RODAPE // 2
        tela.create_line(1, topo, LARGURA - 1, topo, fill=SEPARADOR, tags=("desenho",))
        tela.create_text(MARGEM + 6, centro, text="◆", font=FONTE_DO_RODAPE, fill=DESTAQUE, anchor="w", tags=("desenho",))

        x = LARGURA - MARGEM - 6
        if self._lista is not None:
            for indice, gesto in enumerate(reversed(tuple(self._lista.gestos))):
                if indice:
                    tela.create_line(x, centro - 8, x, centro + 8, fill=BORDA_DA_TECLA, tags=("desenho",))
                    x -= 12
                x = self._tecla(x, centro, gesto.desenho)
                x = self._dica(x, centro, gesto.dica)
            # A mensagem de um gesto toma o lugar do caminho: ao lado dele não cabe.
            texto, cor = (self._mensagem, DESTAQUE) if self._mensagem else (f"Whis  ›  {self._lista.nome}", TEXTO_APAGADO)
            tela.create_text(
                MARGEM + 22,
                centro,
                text=self._caber(texto, FONTE_DO_RODAPE, x - MARGEM - 22),
                font=FONTE_DO_RODAPE,
                fill=cor,
                anchor="w",
                tags=("desenho",),
            )
            return
        tela.create_text(MARGEM + 22, centro, text="Whis", font=FONTE_DO_RODAPE, fill=TEXTO_APAGADO, anchor="w", tags=("desenho",))
        x = self._tecla(x, centro, "Tab")
        x = self._dica(x, centro, "Complete")
        tela.create_line(x, centro - 8, x, centro + 8, fill=BORDA_DA_TECLA, tags=("desenho",))
        x = self._tecla(x - 12, centro, "↵")
        self._dica(x, centro, "Run")

    def _tecla(self, x_direita: int, centro: int, texto: str) -> int:
        """Uma tecla desenhada como no Raycast, encostada em `x_direita`; devolve onde a próxima acaba."""
        tela = self._tela
        item = tela.create_text(
            x_direita - 7, centro, text=texto, font=FONTE_DO_RODAPE, fill=TEXTO_APAGADO, anchor="e", tags=("desenho",)
        )
        esquerda = tela.bbox(item)[0] - 7
        caixa = tela.create_polygon(
            *_cantos_arredondados(esquerda, centro - 10, x_direita, centro + 10, 4),
            smooth=True,
            fill=TECLA,
            outline=BORDA_DA_TECLA,
            width=1,
            tags=("desenho",),
        )
        tela.tag_lower(caixa, item)
        return esquerda - 6

    def _dica(self, x_direita: int, centro: int, texto: str) -> int:
        item = self._tela.create_text(
            x_direita, centro, text=texto, font=FONTE_DO_RODAPE, fill=TEXTO, anchor="e", tags=("desenho",)
        )
        return self._tela.bbox(item)[0] - 12

    def _pintar_selecao(self) -> None:
        for indice, realce in enumerate(self._linhas):
            cor = FUNDO_SELECIONADO if indice + self._primeira == self._selecionada else ""
            self._tela.itemconfigure(realce, fill=cor)
        if self._lista is not None and self._tela.find_withtag("detalhe"):
            indice = self.indice_selecionado()
            texto = texto_do_detalhe(self._lista.detalhe(indice)) if indice is not None else ""
            self._tela.itemconfigure("detalhe", text=texto)

    # -- os gestos ------------------------------------------------------------

    def _mover_selecao(self, passo: int) -> str:
        total = self._total()
        if total:
            self._selecionar((self._selecionada + passo) % total)
        return "break"

    def _selecionar(self, indice: int) -> None:
        self._selecionada = indice
        cabem = self._cabem()
        if indice < self._primeira:
            self._primeira = indice
            self._desenhar()
        elif indice >= self._primeira + cabem:
            self._primeira = indice - cabem + 1
            self._desenhar()
        else:
            self._pintar_selecao()

    def _linha_em(self, y: int) -> int | None:
        """A linha da lista sob a coordenada `y` do canvas, ou `None` fora dela."""
        deslocamento = y - self._topo_das_linhas
        if deslocamento < 0:
            return None
        indice = deslocamento // ALTURA_DA_LINHA
        return indice + self._primeira if indice < len(self._linhas) else None

    def _ao_mover_o_mouse(self, evento) -> None:
        if evento.x >= LARGURA - ALCANCE_DA_BARRA and self.rola():
            return
        indice = self._linha_em(evento.y)
        if indice is not None and indice != self._selecionada:
            self._apontar(indice)

    def _ao_clicar(self, evento) -> None:
        self._arrastando = self.rola() and evento.x >= LARGURA - ALCANCE_DA_BARRA
        if self._arrastando:
            self._rolar_para(evento.y)
            return
        indice = self._linha_em(evento.y)
        if indice is not None:
            self._clicar(indice)

    def _ao_arrastar(self, evento) -> None:
        if self._arrastando:
            self._rolar_para(evento.y)

    def _apontar(self, indice: int) -> None:
        self._selecionada = indice
        self._pintar_selecao()

    def _clicar(self, indice: int) -> None:
        if self._lista is not None:
            if indice < self._total() and self._lista.gestos:
                self._selecionada = indice
                self._gesto(self._lista.gestos[0])
            return
        if indice < len(self._visiveis):
            acao = self._visiveis[indice]
            if acao.abre is not None:
                self.abrir_lista(acao.abre())
                return
            self._despachar(acao.nome)

    def _roda(self, evento) -> str:
        return self._mover_selecao(-1 if evento.delta > 0 else 1)

    def _esc(self, _evento=None) -> str:
        if self._lista is not None:
            self._voltar_aos_comandos()
        else:
            self.esconder()
        return "break"

    def _apagar(self, _evento=None) -> str | None:
        if self._lista is not None and not self.texto():
            self._voltar_aos_comandos()
            return "break"
        return None

    def _voltar_aos_comandos(self) -> None:
        self._sair_da_lista()
        self._campo.delete("1.0", "end")
        self._selecionada = 0
        self._visiveis = ()
        self._atualizar()

    def _sair_da_lista(self) -> None:
        for sequencia in self._teclas_ligadas:
            self._campo.unbind(sequencia)
        self._teclas_ligadas = []
        self._lista = None
        self._linhas_da_lista = None
        self._primeira = 0
        self._mensagem = ""
        self._placeholder.configure(text=PLACEHOLDER)

    def _gesto(self, gesto: Gesto) -> str:
        lista = self._lista
        if lista is None:
            return "break"
        indice = self.indice_selecionado()
        texto = self.texto()
        if gesto.esconde:
            self.esconder()
        try:
            efeito = gesto.rodar(indice, texto)
        except Exception:  # noqa: BLE001 — o handler nunca deixa exceção subir ao Tk
            _log.exception("O gesto %s da lista %s falhou.", gesto.dica, lista.nome)
            efeito = Efeito(mensagem=f"{gesto.dica} failed — see the log")
        if gesto.esconde or self._lista is not lista:
            return "break"
        self._mensagem = efeito.mensagem if efeito else ""
        texto_de_agora = self.texto()
        self._mostrar_placeholder(not texto_de_agora)
        self._linhas_da_lista = tuple(lista.linhas(texto_de_agora))
        total = self._total()
        alvo = efeito.selecionar if efeito and efeito.selecionar is not None else self._selecionada
        self._selecionada = max(0, min(alvo, total - 1)) if total else 0
        self._primeira = max(0, self._selecionada - MAXIMO_DE_LINHAS + 1)
        self._desenhar()
        return "break"

    def _tab(self, _evento=None) -> str:
        if self._lista is not None:
            return "break"
        acao = self.selecionada()
        if acao is None:
            return "break"
        if self.texto() == completar(acao):
            self._mover_selecao(1)
            acao = self.selecionada()
        self.digitar(completar(acao))
        self._campo.mark_set("insert", "end")
        return "break"

    def _enter(self, _evento=None) -> str:
        if self._lista is not None:
            principal = next((gesto for gesto in self._lista.gestos if gesto.tecla == "<Return>"), None)
            if principal is not None:
                self._gesto(principal)
            return "break"
        texto = self.texto().strip()
        chamada = acao_que_abre(texto, self._acoes)
        if chamada is None and not texto and (acao := self.selecionada()) is not None and acao.abre is not None:
            chamada = (acao, "")
        if chamada is not None:
            acao, busca = chamada
            self.abrir_lista(acao.abre(), busca)
            return "break"
        if texto:
            self._despachar(comando_pelo_rotulo(texto, self._acoes) or texto)
        elif (acao := self.selecionada()) is not None:
            self._despachar(acao.nome)
        else:
            self.esconder()
        return "break"

    def _despachar(self, texto: str) -> None:
        self.esconder()
        if self._callback is None:
            return
        try:
            self._callback(texto)
        except Exception:  # noqa: BLE001 — o handler nunca deixa exceção subir ao Tk
            _log.exception("O despacho da palheta falhou.")

    def _sondar(self) -> None:
        try:
            while True:
                acao = self._fila.get_nowait()
                try:
                    acao()
                except Exception:  # noqa: BLE001 — um pedido com erro não para a fila
                    _log.exception("Um pedido à janela da palheta falhou.")
        except queue.Empty:
            pass
        if not self._encerrada:
            try:
                self._raiz.after(INTERVALO_DA_FILA_MS, self._sondar)
            except self._tk.TclError:
                pass

    def _ao_perder_o_foco(self, _evento=None) -> None:
        self._raiz.after(ATRASO_DO_FOCO_MS, self._esconder_se_sem_foco)

    def _esconder_se_sem_foco(self) -> None:
        try:
            com_foco = self._raiz.focus_get()
        except (self._tk.TclError, KeyError):
            com_foco = None
        if self._visivel and com_foco is None:
            self.esconder()


def _variantes(tecla: str) -> tuple[str, ...]:
    """`<Control-f>` também como `<Control-F>`: com o Caps Lock ligado o Tk manda a maiúscula."""
    *modificadores, final = tecla.strip("<>").split("-")
    if len(final) == 1 and final.isalpha():
        return (tecla, "<" + "-".join([*modificadores, final.swapcase()]) + ">")
    return (tecla,)


def _altura_da_lista_cheia() -> int:
    return (
        MARGEM
        + ALTURA_DO_CAMPO
        + 14
        + MAXIMO_DE_LINHAS * ALTURA_DA_LINHA
        + 8
        + ALTURA_DO_DETALHE
        + ALTURA_DO_RODAPE
    )


def _cantos_arredondados(x1: int, y1: int, x2: int, y2: int, raio: int) -> list[int]:
    """Os pontos de um retângulo de cantos arredondados para `create_polygon(smooth=True)`."""
    return [
        x1 + raio, y1, x2 - raio, y1, x2, y1, x2, y1 + raio,
        x2, y2 - raio, x2, y2, x2 - raio, y2, x1 + raio, y2,
        x1, y2, x1, y2 - raio, x1, y1 + raio, x1, y1,
    ]
