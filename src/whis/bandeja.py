"""O ícone na bandeja do Windows: a casa da palheta.

Como o usuário usa::

    python -m whis bandeja

- **Clique esquerdo** no ícone: abre a palheta, como o atalho global.
- **Clique direito**: menu com "Abrir a palheta", "Configurar" e "Sair".

A bandeja **hospeda a palheta**: o Tk roda no thread principal (`mainloop`),
o `pystray` vai para um thread secundário por `run_detached`, e o atalho
global tem thread e fila de mensagens próprios — o arranjo provado pela sonda
da camada 3.

Um atalho que o Windows recusa vira toast e linha no log em
`%APPDATA%\\whis\\bandeja.log`, e a bandeja sobe do mesmo jeito.

Com o bot do Telegram em `[celular]`, a bandeja também ouve os botões das
mensagens (o `celular.Ouvinte`, numa thread própria): é ela que está sempre
ligada no notebook quando ele toca "Enviar esta proposta" no celular.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from . import avisos as _avisos
from . import registro as _registro
from .configuracao import Configuracao
from .instancia import OutraInstancia, Trava, caminho_da_trava

_log = logging.getLogger("whis.bandeja")

#: Diâmetro do ícone. 64 px cobre bem o tamanho que o Windows pede na bandeja.
TAMANHO = 64

COR = (150, 150, 150, 255)

TITULO = "Whis"


def desenhar_icone() -> Image.Image:
    """Um círculo vazado cinza."""
    imagem = Image.new("RGBA", (TAMANHO, TAMANHO), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(imagem)
    desenho.ellipse((8, 8, TAMANHO - 8, TAMANHO - 8), outline=COR, width=6)
    return imagem


@dataclass(frozen=True)
class ItemDoMenu:
    """Uma linha do menu da bandeja, como dados — o `pystray` só a desenha.

    `acao` vazia com `separador` é a linha divisória.
    """

    rotulo: str
    acao: Callable[..., object] | None = None
    padrao: bool = False
    separador: bool = False


SEPARADOR = ItemDoMenu("", separador=True)

ROTULO_DA_PALHETA = "Abrir a palheta"
ROTULO_DE_CONFIGURAR = "Configurar"


class Bandeja:
    """O ícone e o menu.

    `icone` é injetável para o teste montar a bandeja sem bandeja nenhuma.
    `abrir_palheta`, `abrir_configuracao` e `ao_sair` são os ganchos de quem
    hospeda a palheta.
    """

    def __init__(
        self,
        *,
        icone=None,
        abrir_palheta: Callable[[], None] | None = None,
        abrir_configuracao: Callable[[], None] | None = None,
        ao_sair: Callable[[], None] | None = None,
    ) -> None:
        self._abrir_palheta = abrir_palheta
        self._abrir_configuracao = abrir_configuracao
        self._ao_sair = ao_sair
        self._icone = icone if icone is not None else self._construir_icone()

    def executar(self, hospedeiro=None) -> None:
        """Mostra o ícone e devolve o controle só quando o usuário sai.

        Com `hospedeiro` (a janela da palheta, dona do `mainloop`), o ícone
        vai para um thread secundário e é o hospedeiro que segura o thread
        principal até `encerrar`.
        """
        if hospedeiro is None:
            self._icone.run()
            return
        self._icone.run_detached()
        hospedeiro.executar()

    def sair(self, *_ignorado) -> None:
        self._icone.visible = False
        self._icone.stop()
        if self._ao_sair is not None:
            self._ao_sair()

    def abrir_palheta(self, *_ignorado) -> None:
        if self._abrir_palheta is not None:
            self._abrir_palheta()

    def abrir_configuracao(self, *_ignorado) -> None:
        if self._abrir_configuracao is not None:
            self._abrir_configuracao()

    def itens_do_menu(self) -> tuple[ItemDoMenu, ...]:
        """O menu como dados, na ordem em que aparece.

        "Abrir a palheta" é o item padrão (o clique esquerdo) e "Configurar"
        vem antes de "Sair" — cada um só quando quem hospeda a bandeja o ligou.
        """
        itens: list[ItemDoMenu] = []
        if self._abrir_palheta is not None:
            itens.append(ItemDoMenu(ROTULO_DA_PALHETA, self.abrir_palheta, padrao=True))
        if self._abrir_configuracao is not None:
            itens.append(ItemDoMenu(ROTULO_DE_CONFIGURAR, self.abrir_configuracao))
        if itens:
            itens.append(SEPARADOR)
        itens.append(ItemDoMenu("Sair", self.sair))
        return tuple(itens)

    def _construir_icone(self):
        import pystray  # importado aqui: o teste monta a bandeja sem tocar no Windows

        def desenhar(item: ItemDoMenu):
            if item.separador:
                return pystray.Menu.SEPARATOR
            return pystray.MenuItem(item.rotulo, item.acao, default=item.padrao)

        return pystray.Icon(
            "whis",
            icon=desenhar_icone(),
            title=TITULO,
            menu=pystray.Menu(*(desenhar(item) for item in self.itens_do_menu())),
        )


def arquivo_de_log(configuracao: Configuracao) -> Path:
    """Onde a bandeja escreve o log — ver `whis.registro`."""
    return _registro.arquivo_de_log(configuracao)


def configurar_logs(configuracao: Configuracao, *, verboso: bool = False) -> Path:
    """Manda os logs para o arquivo (e para o console, se houver um).

    Sem console — que é o caso do atalho de logon, com `pythonw.exe` — o
    arquivo é a única forma de diagnosticar o que aconteceu.
    """
    return _registro.acrescentar_arquivo(configuracao, verboso=verboso)


def executar(
    configuracao: Configuracao,
    *,
    palheta,
    verboso: bool = False,
    atalho=None,
    painel: Callable[[object], object] | None = None,
    ouvinte=None,
) -> int:
    """Sobe a bandeja com a palheta hospedada. É o que o subcomando chama.

    A trava de instância única vem antes de qualquer coisa: a segunda bandeja
    não conseguiria o atalho que a primeira segura.

    Cria a janela Tk no thread principal, registra `atalho` (um
    `atalho_global.Atalho`) num thread próprio, e liga o menu e a palheta a
    ela. `painel(raiz)` abre o painel de configuração sobre o `Tk` da janela;
    sem ele, `configurar` avisa que não há painel. Um atalho que o Windows
    recusa (já tomado por outro programa) vira log e toast, e a bandeja sobe
    do mesmo jeito — o item de menu ainda abre a palheta.
    """
    trava = Trava(caminho_da_trava(configuracao))
    try:
        trava.adquirir()
    except OutraInstancia as erro:
        print(f"{erro}")
        return 1

    destino = configurar_logs(configuracao, verboso=verboso)
    _log.info("Bandeja iniciada. Log em %s", destino)
    print(f"Whis na bandeja. Log em {destino}", flush=True)

    from .atalho_global import criar_atalho_global
    from .janela_da_palheta import Acao, JanelaDaPalheta

    janela = JanelaDaPalheta()
    janela.ao_enviar(palheta.despachar)

    def abre(comando):
        if comando.lista is None:
            return None
        return lambda: comando.lista(palheta.configuracao)

    def definir_acoes() -> None:
        janela.definir_acoes(
            [
                Acao(
                    comando.nome,
                    comando.icone,
                    comando.rotulo_atual,
                    comando.descricao,
                    bool(comando.parametro),
                    abre=abre(comando),
                )
                for comando in palheta.acoes()
            ]
        )

    definir_acoes()

    def abrir_palheta() -> None:
        # O painel pode ter desligado a daily desde a última abertura.
        janela.pedir(lambda: (definir_acoes(), janela.mostrar()))

    abrir_configuracao: Callable[[], None] | None = None
    if painel is not None:

        def abrir_configuracao() -> None:
            janela.pedir(lambda: painel(janela.raiz))

        palheta.abrir_painel = abrir_configuracao

    hotkey = criar_atalho_global(atalho, abrir_palheta) if atalho is not None else None

    def encerrar() -> None:
        if hotkey is not None:
            hotkey.parar()
        if ouvinte is not None:
            ouvinte.parar()
        janela.pedir(janela.encerrar)

    bandeja = Bandeja(
        abrir_palheta=abrir_palheta,
        abrir_configuracao=abrir_configuracao,
        ao_sair=encerrar,
    )
    if hotkey is not None:
        if hotkey.iniciar():
            print(f"Palheta no atalho {atalho.texto}.", flush=True)
        else:
            _log.error(
                "O atalho %s da palheta não foi registrado — outro programa deve estar "
                "usando a combinação. Troque [palheta].atalho na configuração; enquanto "
                "isso, a palheta abre pelo menu da bandeja.",
                atalho.texto,
            )
            _avisos.avisar(
                "O atalho da palheta não registrou",
                f"{atalho.texto} já está em uso por outro programa. A palheta abre pelo "
                f"menu da bandeja; troque [palheta].atalho para ter o atalho de volta.",
            )
    if ouvinte is not None:
        ouvinte.iniciar()
    try:
        bandeja.executar(hospedeiro=janela)
    except KeyboardInterrupt:
        bandeja.sair()
    finally:
        if hotkey is not None:
            hotkey.parar()
        trava.liberar()
    return 0
