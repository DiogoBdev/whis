r"""Entrada de linha de comando do Whis.

Nove subcomandos sobre a mesma configuração local e sobre as costuras do
projeto — `diario.gerar_diario` para o dia civil, `daily.gerar_daily` para os
slides da daily, `pergunta.responder` para a busca no vault,
`jornal.gerar_jornal` para o jornal de notícias e `reunioes.puxar_reunioes`
para as reuniões da Granola::

    python -m whis init
    python -m whis bandeja
    python -m whis windows [--daily] [--news] [--meetings] [--hunt] [--vigia-freelas] [--procura] [--instagram] [--remover] [--remover-legado]
    python -m whis diario [YYYY-MM-DD] [--simular]
    python -m whis daily [--anotar TEXTO] [--simular] [--fakes] [--agendada]
    python -m whis news [--agendada] [--fakes] [--simular]
    python -m whis meetings [--agendada] [--fakes]
    python -m whis hunt [--simular] [--fakes] [--do-zero] [--freelas] [--limite-minutos N] [--limite-plano N] [--sem-limite]
    python -m whis linkedin [--fakes] | linkedin-login | linkedin-publicar <nota>
    python -m whis contas [--so SITE] [--senha SITE] | navegador [url]
    python -m whis lembrete [--agendada] | candidaturas
    python -m whis vigia-freelas [--agendada] [--fakes] | celular [--teste | --pendentes]
    python -m whis o-que-pedem [--nicho NICHO] | agencias [--forcar] [--fakes]
    python -m whis procurar [--agendada] [--forcar] [--fakes] | instagram [--forcar] [--fakes]
    python -m whis vigia-x [--fakes] | ideia-x [--forcar] [--fakes]
    python -m whis instagram-login | instagram-postar [--agendada] [--simular [--data AAAA-MM-DD]] [--ensaio] [--pasta P]
    python -m whis perguntar "<pergunta>" [--sem-fio]
    python -m whis atender

`atender` é o agente de verdade: fica no ar escutando a DM do bot no Slack por
Socket Mode e responde ali, com o fio de 24 horas por conversa. `perguntar`
existe **para teste e diagnóstico**: responde no terminal, citando as notas, e
continua o fio do terminal entre uma chamada e outra.

`windows` amarra o Whis à máquina: o atalho de logon da bandeja sempre;
`--daily` agenda a daily nos horários de `[daily].horarios` (e o vigia, que
avisa por toast quando uma peça do Whis não subiu); `--news` agenda o jornal
todo dia em `[noticias].horario` (e reagenda o vigia, se existe, para olhar
por ele); `--meetings` agenda a puxada das reuniões da Granola em
`[granola].horario`, do mesmo jeito. As tarefas rodam mesmo que o PC
estivesse desligado na hora.

`meetings` puxa da Granola as reuniões dos últimos 30 dias que ainda não estão
em `reunioes/`. No terminal ele pode abrir o navegador para o login;
`--agendada` (a tarefa) nunca abre, e só avisa quando chegou nota ou falhou.
`--fakes` usa uma Granola de mentira e escreve num vault temporário.

Por padrão o comando usa os **adaptadores reais** — Claude (conta do usuário,
OAuth) para os resumos, git/Linear/Slack para as fontes.
`--fakes` troca todos pelas portas fake: é como exercitar as costuras inteiras
sem gastar crédito nem rede.

`news` gera o jornal do dia: a nota em `noticias/` e a página publicada como
artifact, no mesmo endereço de sempre; `--simular` e `--fakes` montam nota e
página numa pasta temporária, sem publicar nem escrever no vault (`--fakes`
também sem chamar o Claude).

`diario` e `news` saem com um código que diz **se a nota nasceu, e inteira**:
`0` completa, `3` parcial (uma fonte ou o Claude falhou — a nota existe,
marcada), `1` não nasceu (todas as fontes falharam, nenhuma configurada, ou
não havia nada no período). `daily` tem os seus: `0` com os slides no ar ou
com a nota vazia, `3` quando só a cópia nasceu. `2` é do argparse: erro de uso.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import palheta as _palheta
from . import registro as _registro
from . import vault as _vault
from .atalho_global import analisar_atalho
from .atendente import ErroDeDM, PortaDM
from .busca import ErroDeBusca, PortaBusca, Resposta
from .coleta import TodasAsFontesFalharam
from .configuracao import (
    MODELO_DE_CONFIGURACAO,
    Configuracao,
    ConfiguracaoInvalida,
    caminho_padrao,
    carregar,
)
from .conversa import Conversas
from .daily import ResultadoDaDaily, gerar_daily
from .jornal import JornalNaoNasceu, PortasDoJornal, ResultadoDoJornal, gerar_jornal
from .diario import NenhumaFonteConfigurada, ResultadoDoDiario, gerar_diario
from .fakes import (
    BuscaFake,
    EditorFake,
    FormacaoFake,
    LeitorDoXFake,
    MarcacoesFake,
    PublicacaoFake,
    QuizFake,
    ResumoDoDiaFake,
    ReunioesFake,
    fontes_de_exemplo,
)
from .fontes import PortaFonte, data_do_diario
from .pergunta import PerguntaVazia, responder
from .portas import ErroDaGranola, PortaPublicacaoDoDeck, PortaResumoDoDia, PortaReunioes
from .reunioes import ResultadoDasReunioes, puxar_reunioes

_log = logging.getLogger("whis")

#: Códigos de saída de `diario` e `daily`. `2` fica de fora de propósito: é o
#: que o argparse devolve para erro de uso, e um script que confundisse "data
#: digitada errada" com "nota parcial" leria a daily errada.
SAIDA_COMPLETA = 0
SAIDA_NAO_NASCEU = 1
SAIDA_DE_USO = 2
SAIDA_PARCIAL = 3

CODIGOS_DE_SAIDA = f"""\
códigos de saída:
  {SAIDA_COMPLETA}  a nota nasceu inteira
  {SAIDA_PARCIAL}  a nota nasceu parcial: uma fonte ou o Claude falhou, e a nota diz qual
  {SAIDA_NAO_NASCEU}  a nota não nasceu: todas as fontes falharam, nenhuma está
     configurada, ou não havia nada no período
  2  erro de uso (argumento inválido)
o rastro de toda execução fica em bandeja.log, ao lado do config.toml"""

CODIGOS_DA_DAILY = f"""\
códigos de saída:
  {SAIDA_COMPLETA}  os slides saíram no link de sempre, ou a nota estava vazia e nada foi publicado
  {SAIDA_PARCIAL}  os slides não saíram: a cópia em daily/ nasceu com o aviso e o toast abre a nota
  {SAIDA_NAO_NASCEU}  não rodou: configuração inválida
  2  erro de uso (argumento inválido)
o rastro de toda execução fica em bandeja.log, ao lado do config.toml"""

CODIGOS_DA_PERGUNTA = f"""\
códigos de saída:
  {SAIDA_COMPLETA}  respondeu, citando o vault ou uma fonte
  {SAIDA_PARCIAL}  respondeu sem achar: nenhuma nota sustenta uma resposta, e ele disse isso
  {SAIDA_NAO_NASCEU}  não conseguiu: sem conta Claude, API key paga no ambiente, pergunta vazia
     ou configuração inválida
  2  erro de uso (argumento inválido)
a pergunta, as citações e o custo equivalente ficam em bandeja.log, ao lado do config.toml"""


def forcar_utf8_na_saida() -> None:
    """Faz `stdout`/`stderr` aguentarem o texto em português fora do console.

    Quando a saída não é um console interativo — pipe, redirecionamento para
    arquivo, captura pela bandeja ou por outro processo — o Python no Windows
    escolhe cp1252 e qualquer acento derruba o comando com `UnicodeEncodeError`.
    Aqui isso morderia sempre: "Diário", "simulação", "reunião" aparecem
    em toda saída. Só a CLI mexe nos fluxos do processo: quem importa o módulo como biblioteca não é
    afetado.
    """
    for fluxo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is None:
            continue
        try:
            reconfigurar(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # fluxo fechado ou não reconfigurável
            pass


def configurar_logs(verboso: bool = False) -> None:
    """Logs legíveis dos comandos: fontes, resumo, escrita."""
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    if not verboso:
        logging.getLogger("httpx").setLevel(logging.WARNING)


def montar_fontes(configuracao: Configuracao, *, fakes: bool = False) -> list[PortaFonte]:
    """As fontes passivas ligadas na configuração, na ordem git, Linear, Slack.

    Chave vazia é fonte desligada, e o import de cada coletor mora dentro do
    `if`: enquanto o módulo de uma fonte não existir, ela simplesmente não é
    importada, e o diário nasce com as outras.
    """
    if fakes:
        _log.warning("Usando fontes FAKE — nada é lido do git, do Linear nem do Slack.")
        return list(fontes_de_exemplo())

    fontes: list[PortaFonte] = []
    if configuracao.git_raiz:
        from . import fonte_git

        fontes.append(fonte_git.de_configuracao(configuracao))
    if configuracao.linear_api_key:
        from . import fonte_linear

        fontes.append(fonte_linear.de_configuracao(configuracao))
    if configuracao.slack_user_token:
        from . import fonte_slack

        fontes.append(fonte_slack.de_configuracao(configuracao))
    if not fontes:
        raise NenhumaFonteConfigurada(
            "Nenhuma fonte passiva está ligada na configuração"
            f"{f' ({configuracao.origem})' if configuracao.origem else ''}. "
            "Preencha [git].raiz, [linear].api_key ou [slack].user_token — "
            "ou rode com --fakes para ver o comando funcionando sem rede."
        )
    _log.info("Fontes ligadas: %s.", ", ".join(fonte.nome for fonte in fontes))
    return fontes


def montar_resumo_do_dia(
    configuracao: Configuracao, *, fakes: bool = False
) -> PortaResumoDoDia:
    """O resumo do dia: o Claude da conta do usuário, ou o fake."""
    if fakes:
        _log.warning("Usando resumo do dia FAKE — nada é enviado ao Claude.")
        return ResumoDoDiaFake()

    from .resumo_dia_claude import ResumoDoDiaClaude

    return ResumoDoDiaClaude(modelo=configuracao.claude_modelo or None)


def montar_publicacao_do_deck(configuracao: Configuracao) -> PortaPublicacaoDoDeck:
    """A publicação do deck da daily: o Haiku da conta do usuário, pelo CLI de `[claude].cli`.

    Não há fake aqui de propósito: `--fakes` na daily é simulação, e uma
    publicação fake que gravasse a cópia deixaria um `deck:` falso no vault.
    """
    from .deck_claude import PublicacaoDoDeckClaude

    return PublicacaoDoDeckClaude(cli=configuracao.claude_cli or None)


def montar_busca(configuracao: Configuracao, *, fakes: bool = False) -> PortaBusca:
    """A busca: o Claude da conta do usuário com o vault e as fontes ao vivo, ou o fake.

    As fontes ao vivo são os mesmos coletores da daily, pelas mesmas fábricas
    — é o que mantém a blocklist do Slack e o `so_com_mencao` valendo numa
    consulta ao vivo. Nenhuma fonte ligada não é erro aqui: a busca responde
    só com o vault.
    """
    if fakes:
        _log.warning("Usando busca FAKE — nada é enviado ao Claude.")
        return BuscaFake()

    from .busca_claude import BuscaClaude
    from .fontes_ao_vivo import FontesAoVivo

    try:
        fontes = montar_fontes(configuracao)
    except NenhumaFonteConfigurada:
        fontes = []
        _log.info("Nenhuma fonte ao vivo ligada: a busca responde só com o vault.")
    return BuscaClaude(
        modelo=configuracao.claude_modelo or None,
        fontes_ao_vivo=FontesAoVivo(fontes, excluir=configuracao.privacidade_excluir),
    )


def montar_portas_do_jornal(configuracao: Configuracao, *, fakes: bool = False) -> PortasDoJornal:
    """As portas do jornal: o Claude da conta do usuário e o Chrome do X, ou os fakes.

    Os adaptadores do jornal usam o CLI de `[claude].cli` (vazio = o do npm,
    se houver): é o que emite as janelas do plano e aceita as capas no artifact.
    """
    if fakes:
        _log.warning("Usando editor, formação, publicação, quiz, marcações e X FAKE — nada vai ao Claude nem ao X.")
        return PortasDoJornal(
            EditorFake(), FormacaoFake(), PublicacaoFake(), LeitorDoXFake(), MarcacoesFake(), QuizFake()
        )

    from .fonte_x import LeitorDoX
    from .jornal_claude import EditorClaude, FormacaoClaude, PublicacaoClaude
    from .marcacoes_claude import MarcacoesClaude
    from .quiz_claude import QuizClaude

    modelo = configuracao.claude_modelo or None
    cli = configuracao.claude_cli or None
    return PortasDoJornal(
        editor=EditorClaude(modelo=modelo, cli=cli),
        formacao=FormacaoClaude(modelo=modelo, cli=cli),
        publicacao=PublicacaoClaude(cli=cli),
        leitor_do_x=LeitorDoX(),
        marcacoes=MarcacoesClaude(cli=cli),
        quiz=QuizClaude(modelo=modelo, cli=cli),
    )


def montar_reunioes(
    configuracao: Configuracao, *, fakes: bool = False, pode_logar: bool = True
) -> PortaReunioes:
    """A Granola da conta do usuário, pelo MCP oficial, ou o fake.

    O login mora ao lado da configuração (`granola.json`, cifrado para o
    usuário do Windows). `pode_logar=False` é a tarefa agendada: sem login, ela
    não abre navegador nenhum.
    """
    if fakes:
        _log.warning("Usando a Granola FAKE — nada é pedido à Granola.")
        return ReunioesFake()

    from .granola import GranolaMCP
    from .granola_login import GuardaDoLogin, pasta_do_login

    pasta = pasta_do_login(configuracao)
    return GranolaMCP(GuardaDoLogin(pasta), pasta=pasta, pode_logar=pode_logar)


def montar_dm(configuracao: Configuracao) -> PortaDM:
    """A DM do bot no Slack, por Socket Mode. Levanta `ErroDeDM` sem os tokens."""
    from .dm_slack import de_configuracao

    return de_configuracao(configuracao)


def montar_portas_da_cacada(configuracao: Configuracao, *, fakes: bool = False, a_fundo: bool = False):
    """Os cinco adaptadores do Claude na caçada (a conta do usuário), ou os fakes.

    `a_fundo` dá ao explorador da web mais turnos e mais tempo (modo freelas rápidos).
    """
    if fakes:
        from .fakes_da_cacada import portas_fake

        _log.warning("Usando a caçada FAKE — nada é lido da rede nem enviado ao Claude.")
        return portas_fake()

    from .cacada import PortasDaCacada
    from .cacada_claude import ApostasClaude, ExploradorClaude, PerfilClaude, PreparoClaude, TriagemClaude

    from .acoes import Registro
    from .corrida import pasta_da_cacada
    from .navegador_logado import NavegadorLogado

    opcoes = {"modelo": configuracao.claude_modelo or None, "cli": configuracao.claude_cli or None}
    navegador = None
    if configuracao.cacada_enviar_sozinho:
        from .navegador_agente import AgenteDoNavegador

        navegador = AgenteDoNavegador(**opcoes)
    return PortasDaCacada(
        perfil=PerfilClaude(**opcoes),
        explorador=ExploradorClaude(a_fundo=a_fundo, **opcoes),
        triagem=TriagemClaude(**opcoes),
        apostas=ApostasClaude(**opcoes),
        preparo=PreparoClaude(**opcoes),
        navegador=navegador,
        registro=Registro(pasta_da_cacada()),
        ler_caixa=montar_leitura_da_caixa(configuracao),
        navegador_logado=NavegadorLogado(),
    )


def montar_leitura_da_caixa(configuracao: Configuracao):
    """A leitura do Gmail dedicado, só leitura; `None` sem e-mail ou sem senha de app."""
    if not (configuracao.cacada_email and configuracao.cacada_senha_de_app):
        return None
    from .caixa import ler_caixa

    return lambda: ler_caixa(configuracao.cacada_email, configuracao.cacada_senha_de_app)


def montar_linkedin_api(configuracao: Configuracao):
    """A API oficial do LinkedIn com o login guardado no cofre, ao lado do config."""
    from .cofre import cofre_padrao
    from .linkedin import LinkedInAPI

    return LinkedInAPI(configuracao.linkedin_client_id, configuracao.linkedin_client_secret, cofre_padrao(configuracao))


def preparar_linkedin_de(configuracao: Configuracao, *, fakes: bool = False):
    """`linkedin`: a análise do perfil (se há export) e os rascunhos de post. Nada é publicado."""
    from .cacada import CAMINHO_DO_PERFIL
    from .criterios_da_cacada import ler_criterios
    from .evidencias import montar_evidencias
    from .linkedin import preparar_linkedin

    raiz = _vault.preparar(configuracao.vault)
    criterios = ler_criterios(raiz)
    perfil = raiz / CAMINHO_DO_PERFIL
    if fakes:
        from .fakes_da_cacada import AnaliseDoPerfilFake, PostsFake

        analise, posts, evidencias = AnaliseDoPerfilFake(), PostsFake(), "(evidências de exemplo)"
    else:
        from .linkedin import AnaliseDoPerfilClaude, PostsClaude

        opcoes = {"modelo": configuracao.claude_modelo or None, "cli": configuracao.claude_cli or None}
        analise, posts, evidencias = AnaliseDoPerfilClaude(**opcoes), PostsClaude(**opcoes), montar_evidencias(configuracao)
    return preparar_linkedin(
        raiz,
        exportacao=configuracao.linkedin_exportacao,
        analise=analise,
        posts=posts,
        quantos=configuracao.linkedin_posts_por_vez,
        evidencias=evidencias,
        perfil_do_whis=perfil.read_text(encoding="utf-8") if perfil.is_file() else "",
        procuro=criterios.procuro,
        sobre_mim=criterios.sobre_mim,
        hoje=date.today(),
    )


# -- diario -----------------------------------------------------------------


def _comando_diario(args: argparse.Namespace) -> int:
    """Lê as fontes passivas do dia e escreve `diario/YYYY-MM-DD.md`.

    Sem data, o dia é deduzido do relógio pela janela da configuração: às 02:00
    de quinta, com `inicio_do_dia = "04:00"`, o diário a gerar ainda é o de
    quarta. Registra também no arquivo de log, porque quem costuma chamar este
    comando é a tarefa agendada, que não tem console.
    """
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)

    data = args.data or data_do_diario(
        datetime.now().astimezone(), configuracao.diario_inicio_do_dia
    )
    fontes = montar_fontes(configuracao, fakes=args.fakes)
    # Em simulação o resumo não é chamado; o fake entra como marcador para que
    # conferir o filtro não dependa de credencial nem do adaptador do Claude.
    resumo = montar_resumo_do_dia(configuracao, fakes=args.fakes or args.simular)

    resultado = gerar_diario(
        data,
        configuracao=configuracao,
        fontes=fontes,
        resumo=resumo,
        simular=args.simular,
    )
    _relatar_diario(resultado)
    return _codigo_de_saida(
        f"Diário de {data.isoformat()}",
        vazio=resultado.vazio,
        parcial=bool(resultado.fontes_com_falha or resultado.falha_do_resumo),
    )


def _codigo_de_saida(nota: str, *, vazio: bool, parcial: bool) -> int:
    """O código que diz se a nota nasceu, e inteira — e o registra no log.

    A linha no log é o que sobra de uma execução que ninguém viu: o terminal
    já fechou, mas o arquivo diz se a daily daquele dia nasceu sem o Slack ou
    não nasceu.
    """
    if vazio:
        _log.info(
            "%s: a nota não nasceu, nada no período (saída %d).", nota, SAIDA_NAO_NASCEU
        )
        return SAIDA_NAO_NASCEU
    if parcial:
        _log.warning("%s: a nota nasceu parcial (saída %d).", nota, SAIDA_PARCIAL)
        return SAIDA_PARCIAL
    _log.info("%s: a nota nasceu inteira (saída %d).", nota, SAIDA_COMPLETA)
    return SAIDA_COMPLETA


def _relatar_diario(resultado: ResultadoDoDiario) -> None:
    if resultado.vazio:
        print(
            f"Nada aconteceu em {resultado.data.isoformat()} — nenhum diário foi escrito."
        )
        return

    if resultado.simulado:
        print(f"Simulação do diário de {resultado.data.isoformat()} — nada foi escrito.")
        print(f"Janela: {resultado.janela.como_texto()}")
        for evento in resultado.eventos:
            referencia = f" ({evento.referencia})" if evento.referencia else ""
            print(f"  {evento.momento:%H:%M}  [{evento.fonte}]{referencia} {evento.titulo}")
        if not resultado.eventos:
            print("  (nenhum evento)")
    else:
        print(f"Diário: {resultado.nota}")

    for reuniao in resultado.reunioes:
        print(f"  reunião: {reuniao}")
    if resultado.excluidos:
        print(f"O filtro de exclusão removeu {resultado.excluidos} evento(s).")
    for falha in resultado.fontes_com_falha:
        print(f"Fonte '{falha.fonte}' não entrou: {falha.motivo}")
    if resultado.falha_do_resumo:
        print(f"Sem narrativa neste diário: {resultado.falha_do_resumo}")


# -- daily ------------------------------------------------------------------


def _comando_daily(args: argparse.Namespace) -> int:
    """Publica os slides da nota viva no link de sempre e grava a cópia em `daily/`.

    O que sai no terminal são os slides como foram, e o link; quem chama na
    hora agendada é a tarefa `Whis-Daily`, e o toast com o botão é o único
    sinal de que os slides saíram.
    """
    configuracao = _configuracao(args)
    if args.anotar is not None:
        return _anotar_ponto(configuracao, args.anotar)

    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if args.agendada and not configuracao.daily_ativa:
        _log.info(
            "Daily desligada em [daily].ativa de %s — a tarefa agendada não gerou nada.",
            configuracao.origem,
        )
        return SAIDA_COMPLETA
    simular = args.simular or args.fakes
    resultado = gerar_daily(
        datetime.now().astimezone(),
        configuracao=configuracao,
        publicacao=None if simular else montar_publicacao_do_deck(configuracao),
        simular=simular,
    )
    _relatar_daily(resultado)
    if not simular:
        _palheta.avisar_resultado(configuracao, _palheta.resultado_da_daily(resultado))
    return _codigo_da_daily(resultado)


def _codigo_da_daily(resultado: ResultadoDaDaily) -> int:
    """0 com os slides no ar ou com a nota vazia; 3 quando só a cópia nasceu."""
    if resultado.simulado or resultado.vazio or resultado.publicado:
        _log.info("Daily de %s: saída %d.", resultado.data.isoformat(), SAIDA_COMPLETA)
        return SAIDA_COMPLETA
    _log.warning(
        "Daily de %s: os slides não saíram (saída %d).", resultado.data.isoformat(), SAIDA_PARCIAL
    )
    return SAIDA_PARCIAL


# -- news -------------------------------------------------------------------


def _comando_x_login(_args: argparse.Namespace) -> int:
    from . import fonte_x

    print(
        "Abrindo o Chrome do Whis no login do X. Entre na sua conta (pode ser pelo Google) e, "
        "quando a home aparecer, feche a janela."
    )
    if fonte_x.abrir_login():
        print("Sessão do X salva: a próxima edição do jornal lê o X.")
        return 0
    print("Login não concluído: a home do X não abriu logada nesse perfil. Rode de novo e entre antes de fechar.")
    return 1


def _comando_instagram_login(_args: argparse.Namespace) -> int:
    from . import posts_do_instagram

    print(
        f"Abrindo o Chrome do Whis no login do Instagram. Entre na @{posts_do_instagram.USUARIO} e, "
        "quando o feed aparecer, feche a janela."
    )
    if posts_do_instagram.abrir_login():
        print(f"Sessão salva: o Whis posta na @{posts_do_instagram.USUARIO} no dia e na hora do calendário.")
        return 0
    print(f"Login não concluído: o Chrome do Whis não abriu na @{posts_do_instagram.USUARIO}. Rode de novo.")
    return 1


def _comando_instagram_postar(args: argparse.Namespace) -> int:
    """O post do dia da @velozo.dev; `--simular` mostra o plano sem abrir o Chrome, `--ensaio` para antes de Compartilhar."""
    from . import celular
    from . import posts_do_instagram as posts

    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    agora = datetime.now()
    if args.data:
        if not args.simular:
            print("--data só vale com --simular.")
            return SAIDA_DE_USO
        agora = datetime.combine(args.data, posts.HORA_PADRAO) + timedelta(minutes=1)
    postagens = posts.ler_calendario()
    estado = posts.caminho_do_estado()
    if args.simular:
        return _simular_instagram(postagens, posts.ler_estado(estado), agora, pasta=args.pasta, agendada=args.agendada)
    resultado = posts.publicar_do_dia(
        posts.PublicadorDoNavegador(),
        estado=estado,
        avisar=lambda texto: celular.avisar(configuracao, texto) or print(f"(sem Telegram) {texto}"),
        agora=agora,
        postagens=postagens,
        pasta=args.pasta or "",
        agendada=args.agendada,
        ensaio=args.ensaio,
    )
    for perdida in resultado.perdidas:
        print(f"Perdido: {perdida.nome} ({perdida.dia:%d/%m}); avisei no celular.")
    if resultado.postagem is None:
        print(f"Instagram: nada a postar agora ({resultado.motivo}).")
        return SAIDA_COMPLETA
    nome = resultado.postagem.nome
    if resultado.situacao == posts.ENSAIO:
        print(f"Ensaio do {nome}: tudo montado até o Compartilhar, que não foi clicado.")
    elif resultado.situacao == posts.PUBLICADO:
        print(f"Publicado: {nome} {resultado.link}")
    elif resultado.situacao == posts.INCERTO:
        print(f"Incerto: cliquei em Compartilhar no {nome} e o Instagram não confirmou.")
    else:
        print(f"Não saiu: {nome}: {resultado.motivo}")
    for aviso in resultado.avisos:
        print(f"Atenção: {aviso}")
    for foto in resultado.fotos:
        print(f"  {foto}")
    return SAIDA_COMPLETA if resultado.situacao in (posts.ENSAIO, posts.PUBLICADO) else SAIDA_PARCIAL


def _simular_instagram(postagens, registros: dict, agora: datetime, *, pasta: str | None, agendada: bool) -> int:
    from . import posts_do_instagram as posts

    problemas = 0
    for postagem in postagens:
        situacao = posts.situacao(registros, postagem.pasta) or "-"
        try:
            conteudo = posts.conteudo_da_postagem(postagem)
            fora = posts.slides_fora_do_tamanho(conteudo)
            arquivos = ", ".join(arquivo.name for arquivo in conteudo.arquivos)
            if len(conteudo.arquivos) > 2:
                arquivos = f"{conteudo.arquivos[0].name} … {conteudo.arquivos[-1].name} ({len(conteudo.arquivos)})"
            capa = f" + {conteudo.capa.name}" if conteudo.capa else ""
            conferido = f"{arquivos}{capa}, legenda {len(conteudo.legenda)} car." + (f", FORA DO TAMANHO: {fora}" if fora else "")
            problemas += bool(fora)
        except posts.ConteudoIncompleto as erro:
            conferido = f"FALTA: {erro}"
            problemas += situacao in ("-", posts.FALHOU, posts.PERDIDO)
        dia = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")[postagem.dia.weekday()]
        print(f"{dia} {postagem.dia:%d/%m} {postagem.hora:%H:%M}  {postagem.pasta:<26} {situacao:<10} {conferido}")
    escolhida, motivo = posts.escolher(postagens, registros, agora, pasta=pasta or "", agendada=agendada)
    print()
    print(f"Em {agora:%d/%m %H:%M}: " + (f"sairia o {escolhida.nome}." if escolhida else f"nada sairia ({motivo})."))
    if escolhida:
        conteudo = posts.conteudo_da_postagem(escolhida)
        print("Legenda:")
        print(conteudo.legenda)
    return SAIDA_COMPLETA if not problemas else SAIDA_PARCIAL


def _comando_news(args: argparse.Namespace) -> int:
    """Gera o jornal de hoje, publica a página e avisa com o link.

    Quem chama na hora agendada é a tarefa `Whis-Noticias`: o toast é o único
    sinal de que a edição saiu, e o botão abre a página.
    """
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if args.agendada and not configuracao.noticias_ativa:
        _log.info(
            "Jornal desligado em [noticias].ativa de %s — a tarefa agendada não gerou nada.",
            configuracao.origem,
        )
        return SAIDA_COMPLETA
    simular = args.simular or args.fakes
    if args.sabado and not simular:
        print("--sabado só vale com --simular ou --fakes: a edição de verdade só é de sábado no sábado.")
        return SAIDA_DE_USO
    portas = montar_portas_do_jornal(configuracao, fakes=args.fakes)
    try:
        resultado = gerar_jornal(
            datetime.now().astimezone(),
            configuracao,
            editor=portas.editor,
            formacao=portas.formacao,
            publicacao=portas.publicacao,
            leitor_do_x=portas.leitor_do_x,
            marcacoes=portas.marcacoes,
            quiz=portas.quiz,
            sabado=True if args.sabado else None,
            simular=simular,
        )
    except JornalNaoNasceu as erro:
        _log.error("O jornal não nasceu: %s", erro)
        print(f"O jornal não nasceu: {erro}")
        if not simular:
            _palheta.avisar_resultado(configuracao, _palheta.resultado_do_jornal_que_nao_nasceu(erro))
        return SAIDA_NAO_NASCEU

    _relatar_jornal(resultado)
    if not simular:
        _palheta.avisar_resultado(configuracao, _palheta.resultado_do_jornal(resultado))
    return SAIDA_PARCIAL if resultado.parcial else SAIDA_COMPLETA


def _relatar_jornal(resultado: ResultadoDoJornal) -> None:
    from .pagina_do_jornal import texto_do_custo, texto_do_plano

    edicao = resultado.edicao
    if resultado.simulado:
        print(f"Simulação — nada publicado nem escrito no vault. Nota: {resultado.nota}")
        print(f"Página: {resultado.pagina}")
    else:
        print(f"Jornal de {edicao.data.isoformat()} no vault: {resultado.nota}")
        if resultado.url:
            print(f"Página: {resultado.url}")
        else:
            print(f"A página não saiu: {resultado.falha_da_publicacao}")
    for secao in edicao.secoes:
        print(f"  {secao.titulo}: {len(secao.itens)}")
    if edicao.time:
        print(f"  {edicao.time_nome or 'Time'}: {len(edicao.time)}")
    aulas = (1 if edicao.aprender else 0) + len(edicao.para_aprender)
    print(f"  Aulas: {aulas}")
    if edicao.quiz is not None:
        print(f"  Quiz da semana: {len(edicao.quiz.perguntas)} pergunta(s)")
    print(
        " · ".join(
            parte
            for parte in (
                f"{edicao.segundos:.0f} s",
                texto_do_custo(edicao.custo_usd),
                texto_do_plano(edicao.plano_antes, edicao.plano_depois),
            )
            if parte
        )
    )
    for aviso in edicao.avisos:
        print(f"Faltou: {aviso}")
    if edicao.fontes_com_falha:
        print("Não responderam: " + ", ".join(edicao.fontes_com_falha))


def _anotar_ponto(configuracao: Configuracao, texto: str) -> int:
    """Acrescenta uma linha em *Pontos* de `daily/atual.md` e sai, sem publicar nada."""
    if not texto.strip():
        _log.error("Nada a anotar: --anotar precisa de um texto.")
        return 1
    raiz = _vault.preparar(configuracao.vault)
    arquivo = _vault.anotar_ponto(raiz, texto)
    print(f"Anotado em {arquivo}")
    print(f"Está em '## {_vault.SECAO_PONTOS}' da nota viva, e fica lá até você tirar.")
    return 0


def _relatar_daily(resultado: ResultadoDaDaily) -> None:
    print(f"Daily — {resultado.data.isoformat()}")
    print()
    for slide in resultado.deck.slides:
        print(f"{slide.titulo}:")
        for item in slide.itens:
            print(f"{'  ' * (item.profundidade + 1)}{item.texto}")
        print()

    if resultado.simulado:
        print("Simulação — nada foi publicado nem gravado.")
        if resultado.vazio:
            print("A nota da daily está vazia: nenhum slide sairia.")
        return
    if resultado.vazio:
        print(f"A nota da daily está vazia — escreva em {_vault.CAMINHO_DA_NOTA_VIVA} e rode de novo.")
        return
    if resultado.publicado:
        print(f"Slides: {resultado.url}")
    else:
        print(f"Os slides não saíram: {resultado.falha_da_publicacao}")
    if resultado.copia is not None:
        print(f"Cópia: {resultado.copia}")


# -- perguntar --------------------------------------------------------------

#: A chave do fio do terminal em `conversas.json`. A DM do Slack tem a sua.
FIO_DO_TERMINAL = "terminal"


def _comando_perguntar(args: argparse.Namespace) -> int:
    """Pergunta ao vault pelo terminal — a mesma costura que a DM do Slack usa.

    O fio de 24 horas vale aqui também: duas chamadas seguidas continuam a
    mesma conversa, e `--sem-fio` pergunta sem ler nem gravar o fio.
    """
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    busca = montar_busca(configuracao, fakes=args.fakes)
    agora = datetime.now().astimezone()

    conversas = None if args.sem_fio else Conversas.de_configuracao(configuracao)
    conversa = conversas.abrir(FIO_DO_TERMINAL, agora=agora) if conversas else None
    resposta = responder(
        args.pergunta, configuracao=configuracao, busca=busca, conversa=conversa, agora=agora
    )
    if conversas is not None and conversa is not None:
        conversas.guardar(FIO_DO_TERMINAL, conversa)

    _relatar_resposta(resposta)
    return SAIDA_COMPLETA if resposta.achou else SAIDA_PARCIAL


def _relatar_resposta(resposta: Resposta) -> None:
    print(resposta.texto or "Não achei.")
    if resposta.citacoes:
        print()
        print("Fontes:")
        for citacao in resposta.citacoes:
            trecho = f" — {citacao.trecho}" if citacao.trecho else ""
            print(f"  {citacao.origem}{trecho}")
    if not resposta.achou:
        print()
        print("(não achou: nenhuma nota do vault sustenta uma resposta)")


# -- meetings ---------------------------------------------------------------


def _comando_meetings(args: argparse.Namespace) -> int:
    """Puxa da Granola as reuniões que ainda não estão em `reunioes/`.

    Quem chama na hora agendada é a tarefa `Whis-Reunioes`: ela não abre
    navegador, e o toast só sai quando chegou nota ou falhou — reunião ainda
    sem resumo fica para a puxada seguinte, sem avisar. Com `--fakes`, a
    Granola é de mentira e o vault é temporário: uma reunião inventada no vault
    de verdade ficaria lá para sempre e o `ask` a leria como real.
    """
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if args.fakes:
        import tempfile
        from dataclasses import replace

        configuracao = replace(configuracao, vault=Path(tempfile.mkdtemp(prefix="whis-meetings-")))
        print(f"Vault temporário: {configuracao.vault}")
    porta = montar_reunioes(configuracao, fakes=args.fakes, pode_logar=not args.agendada)
    try:
        resultado = puxar_reunioes(configuracao, porta)
    except ErroDaGranola as erro:
        _log.error("As reuniões da Granola não vieram: %s", erro)
        print(f"As reuniões da Granola não vieram: {erro}")
        if not args.fakes:
            _palheta.avisar_resultado(configuracao, _palheta.resultado_das_reunioes_que_falharam(erro))
        return SAIDA_NAO_NASCEU

    _relatar_reunioes(resultado)
    avisar = not args.agendada or resultado.novas or resultado.falhas
    if avisar and not args.fakes:
        _palheta.avisar_resultado(configuracao, _palheta.resultado_das_reunioes(resultado))
    if resultado.nada_veio:
        return SAIDA_NAO_NASCEU
    return SAIDA_PARCIAL if resultado.parcial else SAIDA_COMPLETA


def _relatar_reunioes(resultado: ResultadoDasReunioes) -> None:
    if not resultado.novas:
        print(f"Nada novo na Granola ({resultado.ja_no_vault} reunião(ões) dos últimos 30 dias já no vault).")
    for caminho in resultado.novas:
        print(f"  nova: {caminho}")
    for titulo in resultado.sem_resumo:
        print(f"  ainda sem resumo: {titulo}")
    for falha in resultado.falhas:
        print(f"  não veio: {falha}")


# -- hunt -------------------------------------------------------------------

CODIGOS_DA_CACADA = f"""\
códigos de saída:
  {SAIDA_COMPLETA}  a caçada terminou: o relatório em cacada/ está inteiro
  {SAIDA_PARCIAL}  a caçada parou antes do fim (o plano do Claude, o limite da corrida, ou uma etapa falhou): o relatório
     tem o que deu, e rodar de novo continua de onde parou
  {SAIDA_NAO_NASCEU}  não rodou: outra caçada está correndo, ou a configuração é inválida
  2  erro de uso (argumento inválido)
o rastro de toda execução fica em bandeja.log, ao lado do config.toml"""


def _comando_hunt(args: argparse.Namespace) -> int:
    """A caçada de vagas e freelas: horas lendo, julgando e preparando; nada é enviado.

    Quem chama pela palheta é um processo próprio, solto da bandeja: a
    palheta não fica presa horas e o toast do fim é o sinal de que acabou.
    """
    from . import corrida as _corrida
    from .cacada import LimiteDaCacada, cacar
    from .freelas_rapidos import MODO_FREELAS

    if args.sem_limite and (args.limite_minutos is not None or args.limite_plano is not None):
        print("Use --sem-limite sozinho, ou --limite-minutos/--limite-plano: os dois juntos se contradizem.", file=sys.stderr)
        return 2
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if args.simular:
        return _simular_coleta(configuracao, freelas=args.freelas)
    limite = LimiteDaCacada.escolher(
        configuracao, minutos=args.limite_minutos, plano=args.limite_plano, sem_limite=args.sem_limite
    )

    pasta = _corrida.pasta_da_cacada()
    if args.fakes:
        import tempfile
        from dataclasses import replace

        temporaria = Path(tempfile.mkdtemp(prefix="whis-hunt-"))
        configuracao = replace(configuracao, vault=temporaria / "vault")
        pasta = temporaria / "estado"
        print(f"Vault temporário: {configuracao.vault}")

    avisar = not args.fakes
    try:
        with _corrida.TravaDaCacada(pasta):
            _corrida.manter_acordado()
            if avisar:
                _palheta.avisar_resultado(
                    configuracao,
                    _palheta.Resultado(
                        "A caçada começou",
                        "Corre por horas e não envia nada. O relatório nasce em cacada/ e eu aviso quando acabar.",
                    ),
                )
            ao_pausar = (
                (lambda aviso: _palheta.avisar_resultado(configuracao, _palheta.Resultado("A caçada pausou", aviso.capitalize() + ".")))
                if avisar
                else None
            )
            try:
                resultado = cacar(
                    configuracao,
                    montar_portas_da_cacada(configuracao, fakes=args.fakes, a_fundo=args.freelas),
                    pasta_do_estado=pasta,
                    do_zero=args.do_zero,
                    modo=MODO_FREELAS if args.freelas else "",
                    ao_pausar=ao_pausar,
                    limite=limite,
                )
                if args.freelas and resultado.completa:
                    _agencias_do_mes(configuracao, fakes=args.fakes)
                if args.freelas and resultado.completa and resultado.sobrou_plano and not resultado.com_limite:
                    _relatar_cacada(resultado)
                    print("Os freelas acabaram e ainda sobra plano antes da virada: emendando a caçada de vagas.", flush=True)
                    resultado = cacar(
                        configuracao,
                        montar_portas_da_cacada(configuracao, fakes=args.fakes),
                        pasta_do_estado=pasta,
                        ao_pausar=ao_pausar,
                    )
            finally:
                _corrida.liberar_sono()
    except _corrida.CacadaEmAndamento as erro:
        andamento = _corrida.ler_andamento(pasta) or {}
        print(f"Não comecei: {erro} Etapa: {andamento.get('etapa', '?')} {andamento.get('detalhe', '')}".rstrip())
        return SAIDA_NAO_NASCEU

    _relatar_cacada(resultado)
    if avisar:
        _palheta.avisar_resultado(configuracao, _palheta.resultado_da_cacada(resultado))
    return SAIDA_COMPLETA if resultado.completa else SAIDA_PARCIAL


def _simular_coleta(configuracao: Configuracao, *, freelas: bool = False) -> int:
    """Só a coleta em Python, sem Claude e sem escrever nada além dos critérios: que fonte responde?"""
    from .criterios_da_cacada import ler_criterios
    from .fontes_da_cacada import coletar, fonte_paga, fonte_sem_conta

    from .freelas_rapidos import criterios_de_freelas, do_nicho

    dele = ler_criterios(_vault.preparar(configuracao.vault))
    criterios = dele
    nicho = configuracao.cacada_nicho_freela if freelas else ""
    if freelas:
        opcoes = {"plataformas_pagas": configuracao.cacada_plataformas_pagas}
        criterios = criterios_de_freelas(dele, configuracao.cacada_plataformas_sem_conta, nicho=nicho, **opcoes)
    sem_conta, pagas = configuracao.cacada_plataformas_sem_conta, configuracao.cacada_plataformas_pagas
    lidas_aqui = [f for f in criterios.fontes if not fonte_sem_conta(f, sem_conta) and not fonte_paga(f, pagas)]
    coleta = coletar(lidas_aqui, datetime.now().astimezone(), ao_ler=lambda nome: print(f"lendo {nome}...", flush=True))
    print()
    if nicho:
        print(f"Nicho: {nicho} — só o que cita o nicho ou e-commerce perto dele pode ser chance boa; o resto fica no máximo em tentar a sorte.")
        ficaram = {f.nome for f in criterios.fontes}
        sairam = [
            f.nome
            for f in criterios_de_freelas(dele, sem_conta, **opcoes).fontes
            if f.tipo == "freelancer" and f.nome not in ficaram
        ]
        if sairam:
            print(f"Fora do nicho, saíram do Freelancer.com: {', '.join(sairam)}")
    for fonte in criterios.fontes:
        if fonte_sem_conta(fonte, sem_conta):
            print(f"{fonte.nome}: pulada, sua conta nesse site não está ativa ([cacada].plataformas_sem_conta)")
        elif fonte_paga(fonte, pagas):
            print(f"{fonte.nome}: pulada, responder lá pede plano pago ([cacada].plataformas_pagas)")
    for nome, lista in coleta.por_fonte.items():
        no_nicho = f" ({sum(do_nicho(o, nicho) for o in lista)} do nicho)" if nicho else ""
        print(f"{nome}: {len(lista)}{no_nicho}")
        for oportunidade in lista[:3]:
            print(f"    {oportunidade.titulo[:90]} — {oportunidade.link}")
    for nome, motivo in coleta.falhas.items():
        print(f"{nome}: NÃO RESPONDEU — {motivo}")
    web = [fonte.nome for fonte in criterios.fontes if fonte.tipo == "web"]
    if web:
        print(f"Fontes do explorador (só na caçada de verdade, pelo Claude): {', '.join(web)}")
    if nicho:
        podem = [o for o in coleta.todas() if do_nicho(o, nicho)]
        print(f"\nPodem ser chance boa (do nicho): {len(podem)} de {len(coleta.todas())}")
        for oportunidade in podem[:20]:
            print(f"    {oportunidade.titulo[:90]} — {oportunidade.link}")
    print(f"Total: {len(coleta.todas())} oportunidade(s) únicas. Nada foi enviado ao Claude.")
    return SAIDA_COMPLETA if coleta.por_fonte else SAIDA_NAO_NASCEU


def _relatar_cacada(resultado) -> None:
    print(f"Relatório: {resultado.relatorio}")
    print(
        f"{resultado.chances_boas} chances boas ({resultado.pacotes} com pacote) · "
        f"{resultado.tentar_a_sorte} para tentar a sorte · {resultado.descartadas} descartadas"
    )
    if resultado.plano_antes is not None:
        print(f"Plano semanal: {resultado.plano_antes}% → {resultado.plano_depois}%")
    print(f"Equivalente a US$ {resultado.custo_usd:.2f} na tabela da API (a assinatura não cobra por token).")
    for nome, motivo in resultado.fontes_com_falha.items():
        print(f"Não respondeu: {nome} — {motivo}")
    if resultado.parada:
        print(f"Parou antes do fim: {resultado.parada}. Rode de novo para continuar.")
    elif resultado.limite_batido:
        print(f"O extra parou: {resultado.limite_batido}.")


# -- lembrete e candidaturas -------------------------------------------------


def _comando_lembrete(args: argparse.Namespace) -> int:
    """O toast da véspera: a semana do plano vira nas próximas 30 h? Só lembra, nunca solta a caçada."""
    from . import corrida as _corrida
    from .lembrete import ja_lembrado, marcar_lembrado, proxima_virada, texto_do_lembrete

    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    pasta = _corrida.pasta_da_cacada()
    conhecida = _corrida.ler_virada(pasta)
    if conhecida is None:
        from .cacada_claude import SondaDoPlano

        try:
            plano = SondaDoPlano(cli=configuracao.claude_cli or None).ler()
        except Exception as erro:  # noqa: BLE001
            _log.warning("A sonda do plano falhou: %s", erro)
            plano = None
        if plano is None or plano.vira_sete_dias is None:
            print("Ainda não sei quando a semana do plano vira (o CLI do Claude não mostrou a janela semanal).")
            return SAIDA_NAO_NASCEU
        conhecida = plano.vira_sete_dias
        _corrida.guardar_virada(pasta, conhecida)
    agora = datetime.now().astimezone()
    virada = proxima_virada(conhecida, agora)
    print(f"A próxima virada da semana do plano: {virada.astimezone():%d/%m %H:%M}.")
    texto = texto_do_lembrete(virada, agora)
    if texto is None:
        print("Longe ainda: nada a lembrar hoje.")
        return SAIDA_COMPLETA
    if args.agendada and ja_lembrado(pasta, virada):
        return SAIDA_COMPLETA
    print(texto)
    _palheta.avisar_resultado(configuracao, _palheta.Resultado("Véspera da virada do plano", texto))
    marcar_lembrado(pasta, virada)
    return SAIDA_COMPLETA


def _comando_vigia_freelas(args: argparse.Namespace) -> int:
    """A vigia de freelas: o projeto novo do nicho vira toast (e, o do Freelancer.com, mensagem no celular);
    com `[cacada].propostas_auto`, o novo da 99Freelas que ele entrega ganha proposta."""
    from . import celular, lances_do_freelancer, vigia_de_freelas
    from .vigia_de_freelas import projeto_de_teste, vigiar

    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    nicho = configuracao.cacada_nicho_freela or ("shopify" if args.fakes else "")
    if not nicho:
        print("A vigia de freelas precisa de um nicho: escreva [cacada].nicho_freela no config.toml (ex.: \"shopify\").")
        return SAIDA_NAO_NASCEU
    opcoes = {}
    if args.fakes:
        import tempfile

        estado = Path(tempfile.mkdtemp(prefix="whis-vigia-")) / "vigia-de-freelas.json"
        estado.write_text("{}", encoding="utf-8")
        lidas = (projeto_de_teste(),)
        opcoes = {"estado": estado}
        print(f"Modo de teste: um projeto falso e o estado em {estado}.")
    else:
        agora = datetime.now().astimezone()
        ultima_rodada = vigia_de_freelas.caminho_da_ultima_rodada()
        pagas = configuracao.cacada_plataformas_pagas
        janela = _janela_da_proposta(configuracao)
        if vigia_de_freelas.voltou_de_parada(ultima_rodada, agora):
            lidas = vigia_de_freelas.ler_do_nicho(nicho, paginas_da_99freelas=vigia_de_freelas.PAGINAS_NA_VOLTA, plataformas_pagas=pagas)
            print(f"A vigia ficou mais de 6 h parada: li {vigia_de_freelas.PAGINAS_NA_VOLTA} páginas da 99Freelas para ver o que saiu nesse tempo.")
        elif janela is not None:
            lidas = vigia_de_freelas.ler_do_nicho(
                nicho, paginas_da_99freelas=vigia_de_freelas.PAGINAS_DA_PROPOSTA, ate_idade_da_99freelas=janela, plataformas_pagas=pagas
            )
        else:
            lidas = vigia_de_freelas.ler_do_nicho(nicho, plataformas_pagas=pagas)
        vigia_de_freelas.marcar_rodada(ultima_rodada, agora)
    rodada = vigiar(
        nicho,
        ler=lambda _nicho: lidas,
        celular=lambda texto: celular.avisar(configuracao, texto),
        max_propostas_freelancer=configuracao.cacada_max_propostas_freelancer,
        sem_lances_no_freelancer=not args.fakes and lances_do_freelancer.sem_lances(lances_do_freelancer.caminho_do_estado()),
        **opcoes,
    )
    if rodada.guardados:
        print(f"Primeira rodada: {rodada.guardados} projeto(s) de {nicho} já abertos guardados sem aviso; a próxima avisa o que for novo.")
    elif rodada.avisados:
        for oportunidade in rodada.avisados:
            print(f"Avisado: {oportunidade.titulo} — {oportunidade.link}")
    else:
        print(f"Nada novo de {nicho} desde a última rodada.")
    saida = SAIDA_COMPLETA
    try:
        _propostas_automaticas(configuracao, lidas, nicho, fakes=args.fakes)
    except Exception as erro:  # noqa: BLE001 — os avisos desta rodada já saíram; a proposta tenta na próxima hora
        _log.exception("A proposta automática falhou nesta rodada.")
        print(f"A proposta automática falhou nesta rodada: {erro}")
        saida = SAIDA_PARCIAL
    if not args.fakes:
        try:
            _emails_das_agencias(configuracao)
        except Exception as erro:  # noqa: BLE001 — o e-mail das agências tenta de novo na próxima hora
            _log.exception("O e-mail das agências falhou nesta rodada.")
            print(f"O e-mail das agências falhou nesta rodada: {erro}")
            saida = SAIDA_PARCIAL
        try:
            _emails_das_lojas(configuracao)
        except Exception as erro:  # noqa: BLE001 — o e-mail das lojas tenta de novo na próxima hora
            _log.exception("O e-mail das lojas falhou nesta rodada.")
            print(f"O e-mail das lojas falhou nesta rodada: {erro}")
            saida = SAIDA_PARCIAL
        try:
            _sequencia_fria(configuracao)
        except Exception as erro:  # noqa: BLE001 — a sequência fria tenta de novo na próxima hora
            _log.exception("A sequência do e-mail frio falhou nesta rodada.")
            print(f"A sequência do e-mail frio falhou nesta rodada: {erro}")
            saida = SAIDA_PARCIAL
        try:
            _lances_do_freelancer(configuracao, lidas)
        except Exception as erro:  # noqa: BLE001 — os lances são olhados de novo na próxima hora
            _log.exception("A vigia dos lances do Freelancer.com falhou nesta rodada.")
            print(f"A vigia dos lances do Freelancer.com falhou nesta rodada: {erro}")
            saida = SAIDA_PARCIAL
        try:
            _vigia_do_discord(configuracao)
        except Exception as erro:  # noqa: BLE001 — o Discord é lido de novo na próxima hora
            _log.exception("A vigia do Discord falhou nesta rodada.")
            print(f"A vigia do Discord falhou nesta rodada: {erro}")
            saida = SAIDA_PARCIAL
        try:
            _vigia_do_x(configuracao)
        except Exception as erro:  # noqa: BLE001 — o X é lido de novo na próxima hora
            _log.exception("A vigia do X falhou nesta rodada.")
            print(f"A vigia do X falhou nesta rodada: {erro}")
            saida = SAIDA_PARCIAL
    return saida


def _vigia_do_discord(configuracao: Configuracao) -> None:
    """Os canais de cacada/discord.md no Chrome da caçada: o que ele sabe fazer vira rascunho no celular."""
    from . import celular, propostas_auto, vigia_do_discord
    from .corrida import cacada_correndo, pasta_da_cacada
    from .instancia import OutraInstancia, Trava
    from .navegador_logado import NavegadorLogado
    from .vigia_do_discord_claude import DiscordClaude

    if not configuracao.cacada_discord:
        return
    vault = _vault.preparar(configuracao.vault)
    canais = vigia_do_discord.ler_canais(vault)
    if not canais:
        print(f"Vigia do Discord: nenhum canal em {vigia_do_discord.CAMINHO_DOS_CANAIS}.")
        return
    if cacada_correndo(pasta_da_cacada()):
        print("A caçada está com o Chrome da caçada: o Discord fica para a próxima hora.")
        return
    trava = Trava(pasta_da_cacada() / propostas_auto.NOME_DA_TRAVA)
    try:
        trava.adquirir()
    except OutraInstancia:
        print("O Chrome da caçada está ocupado com um envio pelo celular: o Discord fica para a próxima hora.")
        return
    try:
        with NavegadorLogado().abrir() as sessao:
            rodada = vigia_do_discord.rodar(
                canais,
                sessao=sessao,
                escritor=DiscordClaude(modelo=configuracao.claude_modelo or None, cli=configuracao.claude_cli or None),
                contexto=propostas_auto.contexto_do_vault(vault, configuracao.cacada_nicho_freela),
                estado=vigia_do_discord.caminho_do_estado(),
                celular=lambda texto, **opcoes: celular.avisar(configuracao, texto, **opcoes),
                por_dia=configuracao.cacada_discord_por_dia,
            )
    finally:
        trava.liberar()
    if rodada.parou:
        print(f"Vigia do Discord parou: {rodada.parou}.")
        return
    if rodada.novos_canais:
        print(f"Vigia do Discord: primeira leitura de {', '.join(rodada.novos_canais)}; o que já estava lá ficou como visto.")
    for dados in rodada.rascunhos:
        print(f"Vigia do Discord: {dados['tipo']} para {dados['autor']} em {dados['nome_do_canal']} mandada ao celular.")
    if not rodada.rascunhos and not rodada.novos_canais:
        print("Vigia do Discord: nada novo que ele faça.")


def _vigia_do_x(configuracao: Configuracao, *, forcar: bool = False, fakes: bool = False) -> None:
    """As buscas de cacada/x/buscas.md no Chrome do jornal, a cada 3 horas: quem contrata vira resposta escrita no celular."""
    from . import celular, propostas_auto, vigia_do_x
    from .fonte_x import LeitorDoX
    from .sequencia_fria import ler_a_voz
    from .vigia_do_x_claude import EscritorFake, LeitorFake, XClaude

    if configuracao.cacada_x_por_dia <= 0:
        return
    if fakes:
        import tempfile

        raiz = Path(tempfile.mkdtemp(prefix="whis-vigia-x-"))
        buscas = (vigia_do_x.Busca("Teste", '"looking for a shopify developer"'),)
        leitor, escritor, estado = LeitorFake(), EscritorFake(), raiz / vigia_do_x.NOME_DO_ESTADO
        contexto, voz = propostas_auto.Contexto(procuro="", sobre_mim="", perfil="", portfolio=(), exemplos=()), ""
        mandar = lambda texto, botoes=(): print(f"\n[celular]\n{texto}" + "".join(f"\n[{r}] {d}" for r, d in botoes)) is None  # noqa: E731
        print(f"Modo de teste: três posts falsos e o estado em {estado}, sem Chrome, Claude nem Telegram.")
    else:
        estado = vigia_do_x.caminho_do_estado()
        if not forcar and not vigia_do_x.na_hora(estado):
            return
        vault = _vault.preparar(configuracao.vault)
        buscas = vigia_do_x.ler_buscas(vault)
        if not buscas:
            print(f"Vigia do X: nenhuma busca em {vigia_do_x.CAMINHO_DAS_BUSCAS}.")
            return
        leitor = LeitorDoX()
        escritor = XClaude(modelo=configuracao.claude_modelo or None, cli=configuracao.claude_cli or None)
        contexto = propostas_auto.contexto_do_vault(vault, configuracao.cacada_nicho_freela)
        voz = ler_a_voz(vault)
        mandar = lambda texto, botoes=(): celular.avisar(configuracao, texto, botoes=botoes, html=True)  # noqa: E731
    rodada = vigia_do_x.rodar(
        buscas,
        leitor=leitor,
        escritor=escritor,
        contexto=contexto,
        voz=voz,
        estado=estado,
        celular=mandar,
        por_dia=configuracao.cacada_x_por_dia,
        botao=configuracao.cacada_x_botao_responder,
    )
    if rodada.parou:
        print(f"Vigia do X parou: {rodada.parou}.")
        return
    for falha in rodada.falhas:
        print(f"Vigia do X: a busca {falha}.")
    print(f"Vigia do X: {rodada.lidos} post(s) lido(s), {rodada.candidatos} foram ao Claude, {len(rodada.rascunhos)} rascunho(s) no celular.")
    for dados in rodada.rascunhos:
        print(f"  @{dados['autor']} · {dados['tipo']} · {dados['link']}")


def _comando_vigia_x(args: argparse.Namespace) -> int:
    """A vigia do X fora da rodada de hora em hora: lê agora, mesmo fora das 9 às 22 ou antes das 3 horas."""
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if configuracao.cacada_x_por_dia <= 0:
        print("A vigia do X está desligada: [cacada].x_por_dia = 0.")
        return SAIDA_COMPLETA
    try:
        _vigia_do_x(configuracao, forcar=True, fakes=args.fakes)
    except Exception as erro:  # noqa: BLE001 — o comando diz o que houve em vez de despejar o rastro
        _log.exception("A vigia do X falhou.")
        print(f"A vigia do X falhou: {erro}")
        return SAIDA_PARCIAL
    return SAIDA_COMPLETA


def _janela_da_proposta(configuracao: Configuracao) -> timedelta | None:
    """Até que idade de publicado o projeto da 99Freelas ainda serve à proposta automática; sem ela, None."""
    from .propostas_auto import ENSAIO, JANELA_DO_EXCLUSIVO, LIGADO

    if configuracao.cacada_propostas_auto not in (ENSAIO, LIGADO):
        return None
    horas = timedelta(hours=configuracao.cacada_propostas_auto_horas)
    return horas if configuracao.cacada_propostas_auto_exclusivos else JANELA_DO_EXCLUSIVO + horas


def _sequencia_fria(configuracao: Configuracao) -> None:
    """A rodada da sequência do e-mail frio: respostas e devoluções, a fila na janela e o 2º e o 3º e-mail que venceram."""
    from . import celular, sequencia_fria

    feito = sequencia_fria.rodada(
        _vault.preparar(configuracao.vault),
        estado=sequencia_fria.caminho_do_estado(),
        contas=sequencia_fria.Contas.da_configuracao(configuracao),
        carteiro=sequencia_fria.smtp_do_gmail,
        leitor=sequencia_fria.imap_do_gmail,
        leitor_dos_enviados=sequencia_fria.enviados_do_gmail,
        eventos=sequencia_fria.posthog(configuracao.cacada_posthog_chave),
        celular=lambda texto, **opcoes: celular.avisar(configuracao, texto, **opcoes),
        escritor=sequencia_fria.ReenvioClaude(modelo=configuracao.claude_modelo or None, cli=configuracao.claude_cli or None),
        por_dia=configuracao.cacada_envios_frios_por_dia,
    )
    for email in feito.respostas:
        print(f"E-mail frio: {email} respondeu; a sequência parou.")
    for email in feito.voltaram:
        print(f"E-mail frio: o de {email} voltou; a sequência parou.")
    for email in feito.enviados:
        print(f"E-mail frio: saiu da fila o de {email}.")
    for email in feito.preparados:
        print(f"E-mail frio: o próximo de {email} está no celular, esperando o toque.")


def _envio_frio(configuracao: Configuracao, segmento: str = "agencia"):
    from . import sequencia_fria

    return sequencia_fria.Envio(
        sequencia_fria.caminho_do_estado(),
        sequencia_fria.Contas.da_configuracao(configuracao),
        sequencia_fria.smtp_do_gmail,
        sequencia_fria.posthog(configuracao.cacada_posthog_chave),
        configuracao.cacada_envios_frios_por_dia,
        segmento,
    )


def _lances_do_freelancer(configuracao: Configuracao, lidas) -> None:
    """Uma vez por dia, os lances que sobram no Freelancer.com, num projeto que a vigia acabou de ler; quando voltam, o celular avisa."""
    from . import celular, lances_do_freelancer
    from .corrida import cacada_correndo, pasta_da_cacada
    from .fontes_da_cacada import e_do_freelancer, tipos_pagos
    from .instancia import OutraInstancia, Trava
    from .navegador_logado import FREELANCER, NavegadorLogado
    from .propostas_auto import NOME_DA_TRAVA

    projeto = next((oportunidade.link for oportunidade in lidas if e_do_freelancer(oportunidade.link)), "")
    if not projeto or FREELANCER in tipos_pagos(configuracao.cacada_plataformas_pagas) or cacada_correndo(pasta_da_cacada()):
        return
    trava = Trava(pasta_da_cacada() / NOME_DA_TRAVA)
    try:
        trava.adquirir()
    except OutraInstancia:
        return
    try:
        lances = lances_do_freelancer.conferir(
            abrir_sessao=lambda: NavegadorLogado().abrir(),
            estado=lances_do_freelancer.caminho_do_estado(),
            celular=lambda texto, **opcoes: celular.avisar(configuracao, texto, **opcoes),
            projeto=projeto,
            pasta_do_formulario=lances_do_freelancer.pasta_do_formulario(),
        )
    finally:
        trava.liberar()
    if lances is not None:
        print(f"Lances do Freelancer.com: {lances[0]} de {lances[1]}.")


def _emails_das_agencias(configuracao: Configuracao) -> None:
    """O e-mail do dia para uma agência Shopify da lista, escrito e mandado ao celular com o botão."""
    from . import agencias_email, celular, sequencia_fria

    if configuracao.cacada_agencias_por_dia <= 0:
        return
    preparados = agencias_email.preparar(
        _vault.preparar(configuracao.vault),
        escritor=agencias_email.EmailClaude(modelo=configuracao.claude_modelo or None, cli=configuracao.claude_cli or None),
        estado=agencias_email.caminho_do_estado(),
        celular=lambda texto, **opcoes: celular.avisar(configuracao, texto, **opcoes),
        por_dia=configuracao.cacada_agencias_por_dia,
        sequencia=sequencia_fria.caminho_do_estado(),
    )
    for preparado in preparados:
        print(f"E-mail para a agência {preparado.nome} ({preparado.email}) no celular, esperando o toque: {preparado.nota}")


def _emails_das_lojas(configuracao: Configuracao) -> None:
    """Os e-mails do dia para as lojas Shopify da procura (lenta, ou com o tema de fábrica): medida ou comparada de novo, escrita e mandada ao celular com o botão."""
    from . import celular, lojas_email, lojas_shopify, sequencia_fria, tema_de_fabrica
    from .medida_no_celular import medir_para_o_email

    if configuracao.cacada_lojas_por_dia <= 0 and configuracao.cacada_lojas_redesenho_por_dia <= 0:
        return
    preparados = lojas_email.preparar(
        _vault.preparar(configuracao.vault),
        escritor=lojas_email.EmailDaLojaClaude(modelo=configuracao.claude_modelo or None, cli=configuracao.claude_cli or None),
        estado=lojas_email.caminho_do_estado(),
        celular=lambda texto, **opcoes: celular.avisar(configuracao, texto, **opcoes),
        por_dia=configuracao.cacada_lojas_por_dia,
        medir_no_celular=medir_para_o_email,
        por_dia_redesenho=configuracao.cacada_lojas_redesenho_por_dia,
        conferir_tema=lambda site: lojas_shopify.conferir_tema_do_site(site, tema_de_fabrica.caminho_do_estado()),
        sequencia=sequencia_fria.caminho_do_estado(),
    )
    for preparado in preparados:
        print(f"E-mail para a loja {preparado.nome} ({preparado.email}) no celular, esperando o toque: {preparado.nota}")


def _comando_sequencia_fria(args: argparse.Namespace) -> int:
    """Os envios feitos à mão entram na sequência fria, cada um no passo dele, com o nome e o segmento que o Whis já sabe."""
    from . import agencias_email, lojas_email, sequencia_fria

    configuracao = _configuracao(args)
    nomes: dict[str, tuple[str, str]] = {}
    for modulo, segmento in ((agencias_email, sequencia_fria.AGENCIA), (lojas_email, sequencia_fria.LOJA)):
        for email, registro in agencias_email._ler(modulo.caminho_do_estado())["emails"].items():
            nomes.setdefault(email.casefold(), (str(registro.get("nome") or email), segmento))
    vault = _vault.preparar(configuracao.vault)
    for email, nome in agencias_email.ja_escritas(vault).items():
        nomes.setdefault(email, (nome, sequencia_fria.AGENCIA))
    importados = sequencia_fria.importar_envios(vault, args.importar, estado=sequencia_fria.caminho_do_estado(), nomes=nomes)
    sem_nome = [email for email in importados if email not in nomes]
    print(f"{len(importados)} envio(s) entraram na sequência fria: {', '.join(importados) or 'nenhum novo'}.")
    if sem_nome:
        print(f"Sem nome no Whis (fica o código e o segmento agência): {', '.join(sem_nome)}.")
    return SAIDA_COMPLETA


def _propostas_automaticas(configuracao: Configuracao, lidas, nicho: str, *, fakes: bool = False) -> None:
    """A proposta automática da 99Freelas com a mesma leitura da vigia; no `--fakes`, um ensaio sem Claude nem Chrome."""
    from . import celular, propostas_auto
    from .corrida import cacada_correndo, pasta_da_cacada
    from .instancia import OutraInstancia, Trava

    modo = propostas_auto.ENSAIO if fakes else configuracao.cacada_propostas_auto
    if modo == propostas_auto.DESLIGADO:
        return
    no_celular = lambda texto, **opcoes: celular.avisar(configuracao, texto, **opcoes)  # noqa: E731
    travas = propostas_auto.Travas.da_configuracao(configuracao)
    if fakes:
        import tempfile

        temporaria = Path(tempfile.mkdtemp(prefix="whis-propostas-auto-"))
        print(f"Modo de teste: a proposta automática em ensaio, sem Claude e sem Chrome, em {temporaria}.")
        rodada = propostas_auto.rodar(
            (propostas_auto.projeto_de_teste(),),
            modo=modo,
            travas=travas,
            vault=temporaria / "vault",
            escritor=propostas_auto.EscritorDeTeste(),
            contexto=propostas_auto.Contexto(procuro="", sobre_mim="", perfil="", portfolio=(), exemplos=()),
            sessao=None,
            publica=lambda _url: propostas_auto.PAGINA_DE_TESTE,
            estado=temporaria / propostas_auto.NOME_DO_ESTADO,
            celular=no_celular,
        )
    else:
        if cacada_correndo(pasta_da_cacada()):
            print("A caçada está correndo com o Chrome da caçada: a proposta automática fica para a próxima hora.")
            return
        from .navegador_logado import NavegadorLogado
        from .propostas_auto_claude import PropostaClaude

        trava = Trava(pasta_da_cacada() / propostas_auto.NOME_DA_TRAVA)
        try:
            trava.adquirir()
        except OutraInstancia:
            print("A proposta automática fica para a próxima hora: o envio pelo celular está usando o Chrome da caçada.")
            return
        try:
            vault = _vault.preparar(configuracao.vault)
            with NavegadorLogado().abrir() as sessao:
                rodada = propostas_auto.rodar(
                    lidas,
                    modo=modo,
                    travas=travas,
                    vault=vault,
                    escritor=PropostaClaude(modelo=configuracao.claude_modelo or None, cli=configuracao.claude_cli or None),
                    contexto=propostas_auto.contexto_do_vault(vault, nicho, configuracao.cacada_propostas_auto_valor_hora),
                    sessao=sessao,
                    estado=propostas_auto.caminho_do_estado(),
                    celular=no_celular,
                    fotos=propostas_auto.pasta_das_fotos(),
                )
        finally:
            trava.liberar()
    for resultado in rodada.resultados:
        detalhe = f" — {resultado.motivo}" if resultado.motivo else ""
        print(f"Proposta automática ({modo}): {resultado.situacao} · {resultado.titulo} · {resultado.link}{detalhe}")
    if rodada.parou:
        print(f"Proposta automática parou: {rodada.parou}.")
    elif not rodada.resultados:
        print("Proposta automática: nenhum projeto novo da 99Freelas passou nas travas.")


def _comando_celular(args: argparse.Namespace) -> int:
    """Liga o bot do Telegram dele: pede o token sem mostrar, descobre o chat e grava `[celular]`."""
    import getpass
    from dataclasses import replace

    from . import celular
    from .configuracao import escrever

    configuracao = _configuracao(args)
    if args.tocar:
        import json

        _registro.acrescentar_arquivo(configuracao)
        resposta = _tocou_no_celular(configuracao, args.tocar)
        print(json.dumps({"texto": resposta.texto, "tirar_botao": resposta.tirar_botao}, ensure_ascii=False))
        return SAIDA_COMPLETA
    if args.pendentes:
        from . import propostas_auto

        if not (configuracao.celular_telegram_token and configuracao.celular_telegram_chat):
            print("Falta o bot: rode `whis celular` primeiro.")
            return SAIDA_NAO_NASCEU
        oferecidas = propostas_auto.oferecer_pendentes(
            estado=propostas_auto.caminho_do_estado(),
            vault=_vault.preparar(configuracao.vault),
            celular=lambda texto, **opcoes: celular.avisar(configuracao, texto, **opcoes),
        )
        if oferecidas:
            print(f"{oferecidas} {'proposta esperando' if oferecidas == 1 else 'propostas esperando'} o seu toque, mandadas de novo ao celular.")
        else:
            print(f"Nenhuma proposta escrita nos últimos {propostas_auto.DIAS_DOS_PENDENTES} dias está esperando o seu toque.")
        return SAIDA_COMPLETA
    if args.teste:
        if not (configuracao.celular_telegram_token and configuracao.celular_telegram_chat):
            print("Falta o bot: rode `whis celular` primeiro.")
            return SAIDA_NAO_NASCEU
        telegram = celular.Telegram(configuracao.celular_telegram_token, configuracao.celular_telegram_chat)
        if not telegram.enviar("Teste do Whis: é por aqui que chegam os avisos de freela."):
            print("O Telegram não aceitou a mensagem; veja o log do Whis.")
            return SAIDA_NAO_NASCEU
        print("Mensagem de teste mandada; confira no celular.")
        return SAIDA_COMPLETA
    token = getpass.getpass("Cole o token do bot (o @BotFather mostra; não aparece na tela) e aperte Enter: ").strip()
    telegram = celular.Telegram(token, "")
    bot = telegram.usuario_do_bot()
    if not bot:
        print("Esse token não é de um bot do Telegram (o Telegram recusou). Copie de novo do @BotFather.")
        return SAIDA_NAO_NASCEU
    print(f"Bot @{bot} encontrado. Abra t.me/{bot} no celular e mande qualquer mensagem (ex.: /start); espero até 2 min.")
    chat = celular.esperar_o_chat(telegram)
    if not chat:
        print(f"Nenhuma mensagem chegou ao @{bot}. Mande uma e rode `whis celular` de novo.")
        return SAIDA_NAO_NASCEU
    configuracao = replace(configuracao, celular_telegram_token=token, celular_telegram_chat=chat)
    escrever(configuracao, configuracao.origem)
    celular.Telegram(token, chat).enviar("Pronto: o Whis vai te avisar aqui das propostas e dos freelas novos.")
    print(f"Gravado em {configuracao.origem} ([celular]). Mandei uma mensagem de teste para o @{bot}.")
    return SAIDA_COMPLETA


#: O toque pode esperar a rodada da vigia soltar o Chrome (até 10 min) e ainda abrir o Chrome e conferir.
TEMPO_DO_TOQUE_S = 1200


def _tocar_num_processo_novo(configuracao: Configuracao, dados: str, *, rodar=None):
    """O toque que a bandeja ouve, atendido por `whis celular --tocar` num processo novo.

    A bandeja fica no ar por dias; num processo novo o toque usa o código de agora (em 18/10 a
    bandeja das 11h45 mandou uma proposta com o preço da regra velha) e o Chrome da caçada não
    fica preso ao processo da bandeja.
    """
    import json
    import subprocess

    from .celular import FALHOU, Resposta

    comando = [sys.executable, "-m", "whis", "celular", "--tocar", dados]
    if configuracao.origem:
        comando += ["--config", str(configuracao.origem)]
    rodar = rodar or subprocess.run
    try:
        feito = rodar(
            comando,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TEMPO_DO_TOQUE_S,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as erro:
        _log.error("Celular: o processo do toque não rodou (%s).", erro)
        return Resposta(FALHOU)
    linhas = [linha for linha in (feito.stdout or "").splitlines() if linha.strip()]
    try:
        dados_da_resposta = json.loads(linhas[-1]) if feito.returncode == 0 and linhas else None
    except ValueError:
        dados_da_resposta = None
    if not isinstance(dados_da_resposta, dict):
        _log.error("Celular: o toque %r falhou no processo novo (saída %s): %s", dados, feito.returncode, (feito.stderr or "")[-1500:])
        return Resposta(FALHOU)
    return Resposta(str(dados_da_resposta.get("texto", "")), tirar_botao=bool(dados_da_resposta.get("tirar_botao")))


def _tocou_no_celular(configuracao: Configuracao, dados: str):
    """O toque num botão do Telegram, que a bandeja ouve: "Enviar esta proposta", "Enviar e-mail", "Enviar reenvio",
    "Enviar no Discord" e "Publicar no X"."""
    from . import agencias_email, ideias_do_x, lojas_email, propostas_auto, sequencia_fria, vigia_do_discord
    from .api_do_x import ChavesDoX, ClienteDoX
    from .celular import Resposta
    from .corrida import cacada_correndo, pasta_da_cacada
    from .navegador_logado import NavegadorLogado

    if dados.startswith(ideias_do_x.PREFIXO_DO_TOQUE):
        chaves = ChavesDoX.da_configuracao(configuracao)
        if not chaves.completas:
            return Resposta("Não publiquei: faltam as chaves de [x] no config.toml.")
        return ideias_do_x.tocou_publicar(
            dados,
            cliente=ClienteDoX(chaves),
            estado=ideias_do_x.caminho_do_estado(),
            vault=_vault.preparar(configuracao.vault),
            artes=ideias_do_x.pasta_das_artes(),
        )
    if dados.startswith(lojas_email.PREFIXO_DO_TOQUE):
        return lojas_email.tocou_enviar_email(
            dados,
            vault=_vault.preparar(configuracao.vault),
            estado=lojas_email.caminho_do_estado(),
            enviar=_envio_frio(configuracao, sequencia_fria.LOJA),
        )
    if dados.startswith(agencias_email.PREFIXO_DO_TOQUE):
        return agencias_email.tocou_enviar_email(
            dados,
            vault=_vault.preparar(configuracao.vault),
            estado=agencias_email.caminho_do_estado(),
            enviar=_envio_frio(configuracao, sequencia_fria.AGENCIA),
        )
    if dados.startswith(sequencia_fria.PREFIXO_DO_TOQUE):
        envio = _envio_frio(configuracao)
        return sequencia_fria.tocou_enviar_reenvio(
            dados,
            vault=_vault.preparar(configuracao.vault),
            estado=envio.estado,
            contas=envio.contas,
            carteiro=envio.carteiro,
            eventos=envio.eventos,
            por_dia=envio.por_dia,
        )
    if dados.startswith(vigia_do_discord.PREFIXO_DO_TOQUE):
        return _tocou_no_discord(dados)
    if not dados.startswith(propostas_auto.PREFIXO_DO_TOQUE):
        return Resposta("Esse botão não faz mais nada no Whis.", tirar_botao=True)
    pasta = pasta_da_cacada()
    return propostas_auto.tocou_enviar(
        dados,
        abrir_sessao=lambda: NavegadorLogado().abrir(),
        vault=_vault.preparar(configuracao.vault),
        estado=propostas_auto.caminho_do_estado(),
        travas=propostas_auto.Travas.da_configuracao(configuracao),
        modo=configuracao.cacada_propostas_auto,
        trava=pasta / propostas_auto.NOME_DA_TRAVA,
        ocupada=lambda: cacada_correndo(pasta),
        fotos=propostas_auto.pasta_das_fotos(),
    )


def _tocou_no_discord(dados: str):
    """"Enviar no Discord": espera a vigia soltar o Chrome da caçada, como o toque da proposta da 99Freelas."""
    import time

    from . import propostas_auto, vigia_do_discord
    from .celular import Resposta
    from .corrida import cacada_correndo, pasta_da_cacada
    from .instancia import Trava
    from .navegador_logado import NavegadorLogado

    pasta = pasta_da_cacada()
    if cacada_correndo(pasta):
        return Resposta("Não mandei: a caçada está usando o Chrome da caçada agora. Toque de novo quando ela terminar.")
    segura = propostas_auto._esperar_a_trava(Trava(pasta / propostas_auto.NOME_DA_TRAVA), propostas_auto.ESPERA_DA_TRAVA_S, time.sleep)
    if segura is None:
        return Resposta("Não mandei: o Chrome da caçada está ocupado com a rodada da vigia. Toque de novo daqui a alguns minutos.")
    try:
        return vigia_do_discord.tocou_enviar(dados, abrir_sessao=lambda: NavegadorLogado().abrir(), estado=vigia_do_discord.caminho_do_estado())
    finally:
        segura.liberar()


def _comando_o_que_pedem(args: argparse.Namespace) -> int:
    """Junta os freelas do nicho de todas as caçadas no vault e conta o que os clientes pedem."""
    from .o_que_pedem import atualizar

    configuracao = _configuracao(args)
    nicho = (args.nicho or configuracao.cacada_nicho_freela).strip().casefold()
    if not nicho:
        print("Diga o nicho: --nicho shopify, ou [cacada].nicho_freela no config.toml.")
        return SAIDA_NAO_NASCEU
    anuncios, linhas, caminho = atualizar(_vault.preparar(configuracao.vault), nicho)
    cacadas = len({a.cacada for a in anuncios})
    print(f"{len(anuncios)} anúncios de {nicho} em {cacadas} caçadas, sem repetir.")
    for linha in linhas:
        mediana = f"mediana do teto US$ {linha.mediana:,.0f}" if linha.mediana is not None else "sem orçamento"
        print(f"  {linha.anuncios:3d}  {linha.parte:4.0%}  {linha.pedido} ({mediana})")
    print(f"Nota: {caminho}")
    return SAIDA_COMPLETA


def _comando_agencias(args: argparse.Namespace) -> int:
    """A busca do mês de agências Shopify do Brasil: só leitura na web, a lista para ele contatar à mão."""
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    return _agencias_do_mes(configuracao, fakes=args.fakes, forcar=args.forcar)


def _agencias_do_mes(configuracao: Configuracao, *, fakes: bool = False, forcar: bool = False) -> int:
    from .agencias import CAMINHO_DA_LISTA, AgenciasClaude, AgenciasFake, buscar_agencias, caminho_do_estado
    from .cacada_claude import ErroDaCacada, LimiteDoPlano

    if fakes:
        import tempfile

        temporaria = Path(tempfile.mkdtemp(prefix="whis-agencias-"))
        vault, estado, porta = temporaria / "vault", temporaria / "agencias.json", AgenciasFake()
        lista = Path(configuracao.vault) / CAMINHO_DA_LISTA
        (vault / CAMINHO_DA_LISTA).parent.mkdir(parents=True)
        (vault / CAMINHO_DA_LISTA).write_text(
            lista.read_text(encoding="utf-8") if lista.is_file() else "**1. Aloft** · Criciúma/SC · https://aloftagencia.com\n",
            encoding="utf-8",
        )
        print(f"Modo de teste: uma cópia da lista em {vault}, sem Claude.")
    else:
        vault, estado = _vault.preparar(configuracao.vault), caminho_do_estado()
        porta = AgenciasClaude(cli=configuracao.claude_cli or None)
    try:
        resultado = buscar_agencias(vault, porta, estado=estado, forcar=forcar)
    except (ErroDaCacada, LimiteDoPlano) as erro:
        print(f"A busca de agências do mês não saiu ({erro}); a próxima caçada tenta de novo.")
        return SAIDA_NAO_NASCEU
    if resultado.ja_feita:
        print(f"A busca de agências deste mês já foi feita: {resultado.caminho} (use --forcar para fazer de novo).")
        return SAIDA_COMPLETA
    plural = "agência nova" if len(resultado.novas) == 1 else "agências novas"
    print(f"Agências do mês: {len(resultado.novas)} {plural} em {resultado.caminho}; {len(resultado.repetidas)} já estavam na lista.")
    for agencia in resultado.novas:
        print(f"  {agencia.nome} · {agencia.cidade} · {agencia.site}")
    return SAIDA_COMPLETA


def _rodar_instagram(configuracao: Configuracao, vault: Path, *, fakes: bool, forcar: bool, pasta_de_teste: Path | None = None) -> bool:
    """A lista do Instagram do dia; False quando a busca falhou (tenta de novo amanhã)."""
    from . import celular, instagram
    from .sequencia_fria import ler_a_voz

    if configuracao.cacada_instagram_por_dia <= 0:
        return True
    if fakes:
        porta, estado = instagram.InstagramFake(), (pasta_de_teste or Path(vault)) / instagram.NOME_DO_ESTADO
        avisar = lambda texto: print(texto) is None  # noqa: E731
    else:
        porta, estado = instagram.InstagramClaude(cli=configuracao.claude_cli or None), instagram.caminho_do_estado()
        avisar = lambda texto: celular.avisar(configuracao, texto, html=True)  # noqa: E731
    try:
        resultado = instagram.buscar_do_dia(
            vault, porta, estado=estado, avisar=avisar, por_dia=configuracao.cacada_instagram_por_dia,
            voz=ler_a_voz(vault), forcar=forcar,
        )
    except Exception as erro:  # noqa: BLE001 — a procura segue com agências e lojas
        _log.exception("A lista do Instagram falhou.")
        print(f"A lista do Instagram não saiu ({erro}); tenta de novo amanhã.")
        return False
    if resultado.ja_feita:
        print(f"Instagram: sem lista hoje, {resultado.motivo}.")
        return True
    fora = ", ".join(f"{motivo}: {quantos}" for motivo, quantos in resultado.descartados.items()) or "nenhum"
    print(f"Instagram: {len(resultado.novos)} perfil(is) em {resultado.caminho}; fora: {fora}.")
    for perfil in resultado.novos:
        print(f"  @{perfil.arroba} · {perfil.seguidores} · {perfil.nicho}")
    return True


def _comando_instagram(args: argparse.Namespace) -> int:
    """A lista do Instagram do dia, fora da procura."""
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if args.fakes:
        import tempfile

        raiz = Path(tempfile.mkdtemp(prefix="whis-instagram-"))
        vault = raiz / "vault"
        (vault / "cacada").mkdir(parents=True)
        print(f"Modo de teste: um vault temporário em {vault}, sem Claude nem Telegram.")
        return SAIDA_COMPLETA if _rodar_instagram(configuracao, vault, fakes=True, forcar=True, pasta_de_teste=raiz) else SAIDA_PARCIAL
    vault = _vault.preparar(configuracao.vault)
    return SAIDA_COMPLETA if _rodar_instagram(configuracao, vault, fakes=False, forcar=args.forcar) else SAIDA_PARCIAL


def _rodar_ideia_do_x(configuracao: Configuracao, vault: Path, *, fakes: bool, forcar: bool, pasta_de_teste: Path | None = None) -> bool:
    """O post do X de seg, qua e sex; False quando o Claude falhou (tenta de novo no próximo dia de ideia)."""
    from . import celular, ideias_do_x, propostas_auto
    from .api_do_x import ChavesDoX
    from .sequencia_fria import ler_a_voz

    if not configuracao.cacada_x_ideias:
        return True
    botao = ChavesDoX.da_configuracao(configuracao).completas
    if fakes:
        porta, estado = ideias_do_x.IdeiaFake(), (pasta_de_teste or Path(vault)) / ideias_do_x.NOME_DO_ESTADO
        avisar = lambda texto, botoes=(): print(texto + "".join(f"\n[{r}] {d}" for r, d in botoes)) is None  # noqa: E731
        contexto = ""
    else:
        porta = ideias_do_x.IdeiaClaude(modelo=configuracao.claude_modelo or None, cli=configuracao.claude_cli or None)
        estado = ideias_do_x.caminho_do_estado()
        avisar = lambda texto, botoes=(): celular.avisar(configuracao, texto, botoes=botoes, html=True)  # noqa: E731
        contexto = propostas_auto.contexto_do_vault(vault, configuracao.cacada_nicho_freela).sobre_mim
    try:
        resultado = ideias_do_x.do_dia(
            vault, porta, estado=estado, avisar=avisar, voz=ler_a_voz(vault), contexto=contexto, forcar=forcar, botao=botao
        )
    except Exception as erro:  # noqa: BLE001 — a procura segue com agências e lojas
        _log.exception("A ideia de post do X falhou.")
        print(f"A ideia de post do X não saiu ({erro}).")
        return False
    if resultado.ja_feita:
        print(f"X: sem ideia hoje, {resultado.motivo}.")
        return True
    if resultado.problemas:
        print(f"X: a ideia \"{resultado.ideia.tema}\" ficou fora: {'; '.join(resultado.problemas)}.")
        return False
    onde = f" em {resultado.caminho}" if resultado.caminho else " (da lista de posts preparados)"
    print(f"X: post do dia \"{resultado.ideia.tema}\"{onde}, mandado ao celular.")
    return True


def _comando_ideia_x(args: argparse.Namespace) -> int:
    """O post do X do dia, fora da procura."""
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if args.fakes:
        import tempfile

        raiz = Path(tempfile.mkdtemp(prefix="whis-ideia-x-"))
        vault = raiz / "vault"
        (vault / "cacada").mkdir(parents=True)
        print(f"Modo de teste: um vault temporário em {vault}, sem Claude nem Telegram.")
        return SAIDA_COMPLETA if _rodar_ideia_do_x(configuracao, vault, fakes=True, forcar=True, pasta_de_teste=raiz) else SAIDA_PARCIAL
    vault = _vault.preparar(configuracao.vault)
    return SAIDA_COMPLETA if _rodar_ideia_do_x(configuracao, vault, fakes=False, forcar=args.forcar) else SAIDA_PARCIAL


def _comando_procurar(args: argparse.Namespace) -> int:
    """A procura do dia (tarefa Whis-Procura): agências e lojas Shopify lentas novas quando a fila de e-mails ficou curta."""
    from . import agencias, lojas_email, lojas_shopify
    from .medida_no_celular import MedidaNoCelular

    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if args.fakes:
        import tempfile

        raiz = Path(tempfile.mkdtemp(prefix="whis-procura-"))
        vault = raiz / "vault"
        (vault / "cacada").mkdir(parents=True)
        porta_das_agencias, porta_das_lojas = agencias.AgenciasFake(), lojas_shopify.LojasFake()
        estado_das_agencias, estado_das_lojas = raiz / "agencias.json", raiz / "lojas.json"
        fila_das_lojas = 0
        tema = '<script>Shopify.theme = {"name":"Dawn","schema_name":"Dawn","schema_version":"15.0.0"};</script>'
        abrir = lambda url: lojas_shopify.PaginaInicial(tema, url)  # noqa: E731
        medir = lambda url: MedidaNoCelular(9.4, 3.2, 140)  # noqa: E731
        print(f"Modo de teste: um vault temporário em {vault}, sem Claude nem Chrome.")
    else:
        vault = _vault.preparar(configuracao.vault)
        opcoes_do_claude = {"cli": configuracao.claude_cli or None}
        porta_das_agencias = agencias.AgenciasClaude(**opcoes_do_claude)
        porta_das_lojas = lojas_shopify.LojasClaude(**opcoes_do_claude)
        estado_das_agencias, estado_das_lojas = agencias.caminho_do_estado(), lojas_shopify.caminho_do_estado()
        fila_das_lojas = lojas_email.na_fila(vault, lojas_email.caminho_do_estado())
        abrir, medir = None, None
    saida = SAIDA_COMPLETA
    if not _rodar_instagram(configuracao, vault, fakes=args.fakes, forcar=args.forcar, pasta_de_teste=raiz if args.fakes else None):
        saida = SAIDA_PARCIAL
    if not _rodar_ideia_do_x(configuracao, vault, fakes=args.fakes, forcar=args.forcar, pasta_de_teste=raiz if args.fakes else None):
        saida = SAIDA_PARCIAL
    if configuracao.cacada_agencias_por_dia > 0:
        try:
            resultado = agencias.buscar_na_procura(vault, porta_das_agencias, estado=estado_das_agencias, forcar=args.forcar)
        except Exception as erro:  # noqa: BLE001 — a busca de lojas roda mesmo assim, e a de agências tenta amanhã
            _log.exception("A busca de agências da procura falhou.")
            print(f"A busca de agências não saiu ({erro}); tenta de novo amanhã.")
            saida = SAIDA_PARCIAL
        else:
            if resultado.ja_feita:
                print(f"Agências: sem busca hoje, {resultado.motivo}.")
            else:
                print(f"Agências: {len(resultado.novas)} nova(s) em {resultado.caminho}; {len(resultado.repetidas)} já estavam na lista.")
                for agencia in resultado.novas:
                    print(f"  {agencia.nome} · {agencia.cidade} · {agencia.site}")
    lojas_por_dia = configuracao.cacada_lojas_por_dia + configuracao.cacada_lojas_redesenho_por_dia
    if lojas_por_dia > 0:
        try:
            lojas = lojas_shopify.buscar_se_faltar(
                vault, porta_das_lojas, estado=estado_das_lojas, na_fila=fila_das_lojas, abrir=abrir, medir_no_celular=medir,
                minimo=lojas_shopify.MINIMO_NA_FILA * lojas_por_dia, forcar=args.forcar,
            )
        except Exception as erro:  # noqa: BLE001 — tenta de novo amanhã
            _log.exception("A busca de lojas da procura falhou.")
            print(f"A busca de lojas não saiu ({erro}); tenta de novo amanhã.")
            saida = SAIDA_PARCIAL
        else:
            if lojas.ja_feita:
                print(f"Lojas: sem busca hoje, {lojas.motivo}.")
            else:
                fora = ", ".join(f"{motivo}: {quantas}" for motivo, quantas in lojas.descartadas.items()) or "nenhuma"
                print(f"Lojas: {len(lojas.novas)} nova(s) em {lojas_shopify.CAMINHO_DA_LISTA_DE_LOJAS} de {lojas.devolvidas} achada(s); fora: {fora}.")
                for loja in lojas.novas:
                    fabrica = f" · tema de fábrica: {loja.fabrica.como_linha()}" if loja.fabrica is not None else ""
                    print(f"  {loja.nome} · {loja.site} · LCP {loja.medida.lcp_s:.1f} s · {loja.medida.mb:.1f} MB{fabrica}")
    return saida


def _comando_candidaturas(args: argparse.Namespace) -> int:
    """Regenera `cacada/candidaturas.md` agora (a caçada já faz isso no fim de cada corrida)."""
    from . import acompanhamento
    from .acoes import Registro
    from .corrida import pasta_da_cacada

    configuracao = _configuracao(args)
    mensagens: list[dict[str, str]] = []
    leitura = montar_leitura_da_caixa(configuracao)
    if leitura is not None:
        try:
            mensagens = [{"remetente": m.remetente, "assunto": m.assunto, "quando": m.quando} for m in leitura()]
        except Exception as erro:  # noqa: BLE001
            print(f"O Gmail não abriu: {erro}")
    caminho = acompanhamento.atualizar(_vault.preparar(configuracao.vault), registro=Registro(pasta_da_cacada()), mensagens=mensagens)
    print(f"Candidaturas: {caminho}")
    return SAIDA_COMPLETA


# -- contas -----------------------------------------------------------------


def _comando_contas(args: argparse.Namespace) -> int:
    """Cria as contas de `cacada/criterios.md` com o Gmail dedicado, confirma e completa os perfis.

    Feito para rodar com ele no PC: CAPTCHA, telefone e documento são dele, e o
    Chrome da caçada fica na tela.
    """
    from . import corrida as _corrida
    from .acoes import Registro
    from .cacada import CAMINHO_DO_PERFIL
    from .cofre import cofre_padrao
    from .contas import chave_no_cofre, cuidar_das_contas, escrever_nota
    from .criterios_da_cacada import ler_criterios
    from .navegador_agente import AgenteDoNavegador

    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    cofre = cofre_padrao(configuracao)
    if args.senha:
        guardada = cofre.obter(chave_no_cofre(args.senha))
        if not guardada:
            print(f"Não há conta de '{args.senha}' no cofre.")
            return SAIDA_NAO_NASCEU
        print(f"{guardada.get('site')}: {guardada.get('email')} / {guardada.get('senha')}")
        return SAIDA_COMPLETA

    raiz = _vault.preparar(configuracao.vault)
    criterios = ler_criterios(raiz)
    if not criterios.contas:
        print("Nenhum site em '## Contas' de cacada/criterios.md.")
        return SAIDA_NAO_NASCEU
    perfil = raiz / CAMINHO_DO_PERFIL
    perfil_texto = perfil.read_text(encoding="utf-8") if perfil.is_file() else criterios.sobre_mim
    pasta = _corrida.pasta_da_cacada()
    try:
        with _corrida.TravaDaCacada(pasta):
            _corrida.manter_acordado()
            try:
                resultado = cuidar_das_contas(
                    configuracao,
                    criterios,
                    agente=AgenteDoNavegador(modelo=configuracao.claude_modelo or None, cli=configuracao.claude_cli or None),
                    cofre=cofre,
                    registro=Registro(pasta),
                    perfil_texto=perfil_texto,
                    ler_caixa=montar_leitura_da_caixa(configuracao),
                    so=args.so or "",
                )
            finally:
                _corrida.liberar_sono()
    except _corrida.CacadaEmAndamento as erro:
        print(f"Não comecei: {erro} (contas e caçada usam o mesmo Chrome; espere uma acabar).")
        return SAIDA_NAO_NASCEU
    except ValueError as erro:
        print(f"Não comecei: {erro}.")
        return SAIDA_NAO_NASCEU
    nota = escrever_nota(raiz, resultado, configuracao.cacada_email, datetime.now().astimezone())
    for situacao in resultado.situacoes:
        marcas = " ".join(
            f"{nome} {'✓' if feito else '—'}"
            for nome, feito in (("conta", situacao.conta_feita), ("e-mail", situacao.confirmada), ("perfil", situacao.perfil_feito))
        )
        print(f"{situacao.conta.nome}: {marcas}{f' — falta: {situacao.pendente}' if situacao.pendente else ''}")
    for falha in resultado.falhas:
        print(f"Falha: {falha}")
    print(f"Resumo em {nota}. As senhas ficam no cofre: 'whis contas --senha <site>'.")
    _palheta.avisar_resultado(
        configuracao,
        _palheta.Resultado(
            "Contas: o Whis terminou a rodada",
            f"{len(resultado.pendentes)} site(s) precisam de você." if resultado.pendentes else "Tudo feito.",
            nota=nota,
        ),
    )
    return SAIDA_PARCIAL if resultado.pendentes or resultado.falhas else SAIDA_COMPLETA


def _comando_navegador(args: argparse.Namespace) -> int:
    """Abre um Chrome comum no perfil da caçada, para ele entrar à mão nos sites (Google incluso)."""
    import subprocess

    from .fonte_x import caminho_do_chrome
    from .navegador_agente import pasta_do_perfil
    from .navegador_logado import PAGINAS_DE_LOGIN

    chrome = caminho_do_chrome()
    if chrome is None:
        print("O Chrome não foi encontrado.")
        return SAIDA_NAO_NASCEU
    perfil = pasta_do_perfil()
    perfil.mkdir(parents=True, exist_ok=True)
    enderecos = [args.url] if args.url else list(PAGINAS_DE_LOGIN)
    if args.url:
        print(f"Abrindo o Chrome da caçada ({perfil}). Entre no site e feche a janela quando terminar.")
    else:
        print(
            f"Abrindo o Chrome da caçada ({perfil}) com Freelancer.com, 99Freelas, VintePila, freelancer.com.br e Discord. "
            "Entre em cada um e feche a janela quando terminar: a caçada usa esse login (com a janela fechada)."
        )
    subprocess.Popen(
        [
            str(chrome),
            f"--user-data-dir={perfil}",
            "--window-name=Chrome da caçada (Whis)",
            "--no-first-run",
            "--no-default-browser-check",
            *enderecos,
        ]
    )
    return SAIDA_COMPLETA


# -- linkedin ---------------------------------------------------------------


def _comando_linkedin(args: argparse.Namespace) -> int:
    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    if args.fakes:
        import tempfile
        from dataclasses import replace

        configuracao = replace(configuracao, vault=Path(tempfile.mkdtemp(prefix="whis-linkedin-")))
        print(f"Vault temporário: {configuracao.vault}")
    resultado = preparar_linkedin_de(configuracao, fakes=args.fakes)
    if resultado.analise:
        print(f"Análise do perfil: {resultado.analise}")
    for caminho in resultado.rascunhos:
        print(f"Rascunho: {caminho}")
    for falha in resultado.falhas:
        print(f"Faltou: {falha}")
    print("Nada foi publicado. Para publicar um rascunho: posts na palheta (Enter) ou 'whis linkedin-publicar <nota>'.")
    if not args.fakes:
        _palheta.avisar_resultado(configuracao, _palheta.resultado_do_linkedin(resultado))
    return SAIDA_COMPLETA if resultado.rascunhos and not resultado.falhas else (SAIDA_PARCIAL if resultado.rascunhos or resultado.analise else SAIDA_NAO_NASCEU)


def _comando_linkedin_login(args: argparse.Namespace) -> int:
    from .linkedin import RETORNO, ErroDoLinkedIn

    configuracao = _configuracao(args)
    api = montar_linkedin_api(configuracao)
    print(f"Abrindo o LinkedIn no navegador. Autorize o app; a página volta para {RETORNO}.")
    try:
        api.login()
    except ErroDoLinkedIn as erro:
        print(f"Login não concluído: {erro}")
        return SAIDA_NAO_NASCEU
    print(f"Login guardado no cofre; vale por {api.dias_restantes()} dias.")
    return SAIDA_COMPLETA


def _comando_linkedin_publicar(args: argparse.Namespace) -> int:
    """Publica um rascunho de `linkedin/posts/` — sempre perguntando antes: é o aval dele."""
    from .linkedin import ErroDoLinkedIn, ler_post, publicar_post

    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    raiz = Path(configuracao.vault)
    caminho = args.nota.replace("\\", "/")
    if Path(caminho).is_absolute():
        caminho = Path(caminho).resolve().relative_to(raiz.resolve()).as_posix()
    post = ler_post(raiz, caminho)
    if post is None:
        print(f"{caminho} não é um post do Whis em linkedin/posts/.")
        return SAIDA_NAO_NASCEU
    print(post.texto)
    print()
    resposta = input("Publicar este post no seu LinkedIn agora? [s/N] ").strip().casefold()
    if resposta not in ("s", "sim", "y", "yes"):
        print("Nada publicado.")
        return SAIDA_COMPLETA
    try:
        url = publicar_post(raiz, caminho, montar_linkedin_api(configuracao))
    except ErroDoLinkedIn as erro:
        print(f"Não publicado: {erro}")
        return SAIDA_NAO_NASCEU
    print(f"Publicado: {url}")
    return SAIDA_COMPLETA


# -- atender ----------------------------------------------------------------


def _comando_atender(args: argparse.Namespace) -> int:
    """Sobe o atendente: a DM do Slack escutando por Socket Mode até o Ctrl+C.

    `--fakes` troca só a busca pelo fake: a DM continua sendo o Slack de
    verdade, que é como se ensaia a app aprovada sem gastar a assinatura.
    """
    from . import atendente as _atendente

    configuracao = _configuracao(args)
    _registro.acrescentar_arquivo(configuracao, verboso=args.verboso)
    busca = montar_busca(configuracao, fakes=args.fakes)
    try:
        dm = montar_dm(configuracao)
    except ErroDeDM as erro:
        _log.error("%s", erro)
        return 1
    return _atendente.executar(configuracao, dm=dm, busca=busca, verboso=args.verboso)


# -- bandeja ----------------------------------------------------------------


def _comando_bandeja(args: argparse.Namespace) -> int:
    """Sobe o ícone na bandeja com a palheta hospedada nele.

    Aqui só se decide de onde vêm as portas dos comandos da palheta — reais por
    padrão, fakes com `--fakes`: `daily`, `news` e `ask` rodam com as mesmas
    fábricas dos subcomandos. `meetings` fica indisponível com `--fakes`: ele
    escreve no vault de verdade, e reunião inventada ficaria lá para sempre.
    """
    from . import bandeja as _bandeja

    configuracao = _configuracao(args)
    fakes = args.fakes
    comandos = _palheta.comandos_padrao(
        publicacao_do_deck=lambda atual: montar_publicacao_do_deck(atual),
        simular_daily=fakes,
        busca=lambda atual: montar_busca(atual, fakes=fakes),
        portas_do_jornal=lambda atual: montar_portas_do_jornal(atual, fakes=fakes),
        reunioes=None if fakes else (lambda atual: montar_reunioes(atual, pode_logar=True)),
        linkedin=lambda atual: preparar_linkedin_de(atual, fakes=fakes),
        linkedin_api=None if fakes else (lambda atual: montar_linkedin_api(atual)),
        iniciar_cacada=(
            (lambda _atual: _palheta.Resultado("Caçada indisponível", "Com --fakes, rode 'whis hunt --fakes' num terminal."))
            if fakes
            else None
        ),
        iniciar_contas=(
            (lambda _atual: _palheta.Resultado("Contas indisponíveis", "Com --fakes, as contas não são criadas."))
            if fakes
            else None
        ),
    )
    palheta = _palheta.Palheta(comandos, configuracao)

    def painel(raiz) -> None:
        from .painel import abrir_painel

        abrir_painel(raiz, palheta, destino=configuracao.origem)

    ouvinte = None
    if not fakes and configuracao.celular_telegram_token and configuracao.celular_telegram_chat:
        from .celular import Ouvinte

        ouvinte = Ouvinte(lambda: palheta.configuracao, lambda dados: _tocar_num_processo_novo(palheta.configuracao, dados))
    return _bandeja.executar(
        configuracao,
        verboso=args.verboso,
        palheta=palheta,
        atalho=analisar_atalho(configuracao.palheta_atalho),
        painel=painel,
        ouvinte=ouvinte,
    )


# -- init -------------------------------------------------------------------


def _comando_init(args: argparse.Namespace) -> int:
    """Escreve o `config.toml` inicial na máquina de destino.

    Antes disso o usuário tinha que criar o arquivo à mão, adivinhando as
    chaves — e o primeiro erro provável era a barra invertida do Windows. O
    arquivo nasce com o vault no perfil do usuário e comentários explicando o
    que preencher.
    """
    destino = Path(args.config) if args.config else caminho_padrao()
    if destino.exists() and not args.forcar:
        print(f"Já existe uma configuração em {destino}.")
        print("Abra o arquivo para editar, ou rode 'init --forcar' para sobrescrever.")
        return 1

    perfil = Path.home()
    conteudo = MODELO_DE_CONFIGURACAO.format(vault=(perfil / "whis-vault").as_posix())
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(conteudo, encoding="utf-8")

    print(f"Configuração criada em {destino}")
    print()
    print("Próximos passos:")
    print("  1. Confira o caminho do vault no arquivo.")
    print("  2. Rode 'python -m whis bandeja' e abra a palheta pelo atalho ou pelo ícone.")
    return 0


# -- windows ----------------------------------------------------------------


#: Como cada chave de `[daily].horarios` aparece para o usuário.
_DIAS_POR_EXTENSO = {
    "segunda": "segunda",
    "terca": "terça",
    "quarta": "quarta",
    "quinta": "quinta",
    "sexta": "sexta",
    "sabado": "sábado",
    "domingo": "domingo",
}


def _horarios_por_extenso(configuracao: Configuracao) -> str:
    return ", ".join(
        f"{_DIAS_POR_EXTENSO[dia]} {hora}" for dia, hora in configuracao.daily_horarios
    )


def _comando_windows(args: argparse.Namespace) -> int:
    """Amarra (ou desamarra) o Whis ao Windows: logon, daily, jornal e vigia.

    Amarrações explícitas, cada uma com o seu `--remover`. A daily precisa da configuração
    (é dela que saem os dias e horas); a tarefa recebe o caminho do arquivo
    explicitamente, porque não herda `WHIS_CONFIG` de quem a criou.
    """
    from . import automacao

    precisa_da_configuracao = bool(args.config) or (
        (args.daily or args.news or args.meetings or args.hunt or args.vigia_freelas or args.procura or args.instagram) and not args.remover
    )
    configuracao = _configuracao(args) if precisa_da_configuracao else None
    origem = (configuracao.origem if configuracao else None) or caminho_padrao()
    feitos: list[str] = []
    so_remover_legado = args.remover_legado and not (
        args.atendente or args.daily or args.news or args.meetings or args.hunt or args.vigia_freelas or args.procura or args.instagram
    )

    if args.daily and not args.remover and not configuracao.daily_horarios:
        _log.error(
            "A tabela [daily].horarios de %s está vazia: não há dia nem hora para "
            'agendar. Preencha-a (por exemplo horarios = { segunda = "13:55" }) e '
            "rode de novo.",
            origem,
        )
        return 1

    try:
        if args.remover_legado:
            legado = automacao.remover_legado()
            feitos.append(
                f"Atalho antigo removido de {legado.atalho}"
                if legado.atalho
                else f"Não havia atalho '{automacao.NOME_DO_ATALHO_LEGADO}' na Inicialização."
            )
            for nome in automacao.TAREFAS_LEGADAS:
                feitos.append(
                    f"Tarefa antiga '{nome}' removida do Agendador."
                    if nome in legado.tarefas
                    else f"Não havia tarefa '{nome}' no Agendador."
                )
        if args.remover:
            atalho = automacao.remover_inicializacao()
            feitos.append(
                f"Atalho de inicialização removido de {atalho}"
                if atalho
                else "Não havia atalho de inicialização."
            )
            if args.atendente:
                removido = automacao.remover_atendente()
                feitos.append(
                    f"Atalho do atendente removido de {removido}"
                    if removido
                    else "Não havia atalho do atendente."
                )
            if args.daily:
                removida = automacao.desagendar_daily()
                feitos.append(
                    f"Tarefa '{automacao.NOME_DA_TAREFA_DA_DAILY}' removida do Agendador."
                    if removida
                    else "Não havia tarefa da daily agendada."
                )
                removido = automacao.desagendar_vigia()
                feitos.append(
                    f"Tarefa '{automacao.NOME_DA_TAREFA_DO_VIGIA}' removida do Agendador."
                    if removido
                    else "Não havia vigia agendado."
                )
            if args.vigia_freelas:
                removida = automacao.desagendar_vigia_de_freelas()
                feitos.append(
                    f"Tarefa '{automacao.NOME_DA_TAREFA_DA_VIGIA_DE_FREELAS}' removida do Agendador."
                    if removida
                    else "Não havia vigia de freelas agendada."
                )
            if args.procura:
                removida = automacao.desagendar_procura()
                feitos.append(
                    f"Tarefa '{automacao.NOME_DA_TAREFA_DA_PROCURA}' removida do Agendador."
                    if removida
                    else "Não havia procura agendada."
                )
            if args.instagram:
                removida = automacao.desagendar_posts_do_instagram()
                feitos.append(
                    f"Tarefa '{automacao.NOME_DA_TAREFA_DO_INSTAGRAM}' removida do Agendador."
                    if removida
                    else "Não havia posts do Instagram agendados."
                )
            if args.news:
                removida = automacao.desagendar_noticias()
                feitos.append(
                    f"Tarefa '{automacao.NOME_DA_TAREFA_DO_JORNAL}' removida do Agendador."
                    if removida
                    else "Não havia tarefa do jornal agendada."
                )
                if removida and not args.daily and automacao.vigia_agendado():
                    vigiadas = automacao.agendar_vigia(configuracao or _configuracao(args))
                    feitos.append(
                        f"Vigia reagendado como '{automacao.NOME_DA_TAREFA_DO_VIGIA}': "
                        f"agora vigia {', '.join(vigiadas)}."
                    )
            if args.hunt:
                removida = automacao.desagendar_lembrete()
                feitos.append(
                    f"Tarefa '{automacao.NOME_DA_TAREFA_DO_LEMBRETE}' removida do Agendador."
                    if removida
                    else "Não havia lembrete da caçada agendado."
                )
            if args.meetings:
                removida = automacao.desagendar_reunioes()
                feitos.append(
                    f"Tarefa '{automacao.NOME_DA_TAREFA_DAS_REUNIOES}' removida do Agendador."
                    if removida
                    else "Não havia tarefa das reuniões agendada."
                )
                if removida and not args.daily and automacao.vigia_agendado():
                    vigiadas = automacao.agendar_vigia(configuracao or _configuracao(args))
                    feitos.append(
                        f"Vigia reagendado como '{automacao.NOME_DA_TAREFA_DO_VIGIA}': "
                        f"agora vigia {', '.join(vigiadas)}."
                    )
        elif not so_remover_legado:
            atalho = automacao.instalar_inicializacao()
            feitos.append(f"Atalho de inicialização criado em {atalho}")
            feitos.append(
                f"Atalho do Menu Iniciar criado em {automacao.caminho_do_atalho_do_menu()} "
                f"(AUMID {automacao.AUMID}) — é o que permite ao Whis mostrar toast."
            )
            feitos.append("A bandeja passa a subir sozinha no próximo logon.")
            if args.atendente:
                atalho_do_atendente = automacao.instalar_atendente()
                feitos.append(f"Atalho do atendente criado em {atalho_do_atendente}")
                feitos.append(
                    "O agente da DM do Slack passa a subir sozinho no próximo logon, "
                    "ao lado da bandeja."
                )
            if args.news:
                automacao.agendar_noticias(configuracao)
                feitos.append(
                    f"Jornal agendado como '{automacao.NOME_DA_TAREFA_DO_JORNAL}': todo dia "
                    f"às {configuracao.noticias_horario}. Com [noticias].ativa = false a "
                    f"tarefa continua lá, mas sai sem gerar."
                )
                vigia_ja_agendado = not args.daily and automacao.vigia_agendado()
                if vigia_ja_agendado:
                    vigiadas = automacao.agendar_vigia(configuracao)
                    feitos.append(
                        f"Vigia reagendado como '{automacao.NOME_DA_TAREFA_DO_VIGIA}': "
                        f"avisa por toast se {' ou '.join(vigiadas)} falhou ou a bandeja "
                        f"não está no ar."
                    )
                elif not args.daily:
                    feitos.append(
                        f"'{automacao.NOME_DA_TAREFA_DO_VIGIA}' não está agendado: rode "
                        f"'whis windows --daily' para ele olhar também pelo jornal."
                    )
            if args.hunt:
                automacao.agendar_lembrete(configuracao)
                feitos.append(
                    f"Lembrete da caçada agendado como '{automacao.NOME_DA_TAREFA_DO_LEMBRETE}': todo dia às "
                    f"{configuracao.cacada_hora_do_lembrete} ele olha se a semana do plano vira nas próximas 30 h "
                    f"e avisa por toast, uma vez por virada. A caçada continua só quando você chama."
                )
            if args.vigia_freelas:
                automacao.agendar_vigia_de_freelas(configuracao)
                feitos.append(
                    f"Vigia de freelas agendada como '{automacao.NOME_DA_TAREFA_DA_VIGIA_DE_FREELAS}': todo dia às "
                    f"{', '.join(configuracao.cacada_horarios_da_vigia_de_freelas)} ela olha a 99Freelas e o "
                    f"Freelancer.com e avisa por toast (e no celular, o do Freelancer.com) o projeto novo de "
                    f"{configuracao.cacada_nicho_freela or '[cacada].nicho_freela (ainda vazio: ela não roda sem ele)'}; "
                    f"proposta automática na 99Freelas: {configuracao.cacada_propostas_auto}."
                )
            if args.procura:
                from .lojas_shopify import MINIMO_NA_FILA

                automacao.agendar_procura(configuracao)
                feitos.append(
                    f"Procura agendada como '{automacao.NOME_DA_TAREFA_DA_PROCURA}': todo dia às "
                    f"{configuracao.cacada_horario_da_procura} ela busca na web agências novas a cada 2 dias e "
                    f"lojas Shopify lentas novas quando a fila de e-mails ficou com menos de {MINIMO_NA_FILA}; os "
                    f"e-mails chegam pela vigia de freelas, um por dia de cada."
                )
            if args.instagram:
                automacao.agendar_posts_do_instagram(configuracao)
                feitos.append(
                    f"Posts do Instagram agendados como '{automacao.NOME_DA_TAREFA_DO_INSTAGRAM}': todo dia às "
                    f"{automacao.HORA_DOS_POSTS_DO_INSTAGRAM} sai o item do dia do calendário do velozo-reels na "
                    f"@velozo.dev (login uma vez com 'whis instagram-login')."
                )
            if args.meetings:
                automacao.agendar_reunioes(configuracao)
                feitos.append(
                    f"Reuniões agendadas como '{automacao.NOME_DA_TAREFA_DAS_REUNIOES}': todo dia "
                    f"às {configuracao.granola_horario}, puxando da Granola o que ainda não está "
                    f"em reunioes/. Ela nunca abre o navegador: o primeiro login é pela palheta "
                    f"(meetings)."
                )
                if not args.daily and automacao.vigia_agendado():
                    vigiadas = automacao.agendar_vigia(configuracao)
                    feitos.append(
                        f"Vigia reagendado como '{automacao.NOME_DA_TAREFA_DO_VIGIA}': "
                        f"avisa por toast se {' ou '.join(vigiadas)} falhou ou a bandeja "
                        f"não está no ar."
                    )
                elif not args.daily:
                    feitos.append(
                        f"'{automacao.NOME_DA_TAREFA_DO_VIGIA}' não está agendado: rode "
                        f"'whis windows --daily' para ele olhar também pelas reuniões."
                    )
            if args.daily:
                automacao.agendar_daily(configuracao)
                feitos.append(
                    f"Daily agendada como '{automacao.NOME_DA_TAREFA_DA_DAILY}': "
                    f"{_horarios_por_extenso(configuracao)}."
                )
                vigiadas = automacao.agendar_vigia(configuracao)
                feitos.append(
                    f"Vigia agendado como '{automacao.NOME_DA_TAREFA_DO_VIGIA}': "
                    f"{automacao.ATRASO_DO_VIGIA.seconds // 60} minutos depois de cada "
                    f"daily e todo dia às {automacao.HORA_DA_RONDA_DO_VIGIA} — avisa por "
                    f"toast se {' ou '.join(vigiadas)} falhou ou a bandeja não está no ar."
                )
            if args.daily or args.news or args.meetings:
                feitos.append(
                    "As tarefas rodam mesmo que o PC estivesse desligado na hora: assim "
                    "que ele ligar, e acordando a máquina se estiver em suspensão."
                )
    except automacao.ErroDeAutomacao as erro:
        _log.error("%s", erro)
        return 1

    for linha in feitos:
        print(linha)
    return 0


# -- Bastidores da CLI ------------------------------------------------------


def _data_iso(texto: str) -> date:
    """A data do diário na linha de comando, sempre `YYYY-MM-DD`."""
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{texto!r} não é uma data no formato YYYY-MM-DD."
        ) from None


def _inteiro_nao_negativo(texto: str) -> int:
    try:
        numero = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{texto!r} não é um número inteiro.") from None
    if numero < 0:
        raise argparse.ArgumentTypeError(f"{numero} é negativo; use 0 para sem limite.")
    return numero


def _pontos_do_plano(texto: str) -> int:
    numero = _inteiro_nao_negativo(texto)
    if numero > 100:
        raise argparse.ArgumentTypeError(f"{numero} passa de 100 pontos do plano semanal.")
    return numero


def _configuracao(args: argparse.Namespace) -> Configuracao:
    configuracao = carregar(args.config)
    _log.info("Configuração lida de %s", configuracao.origem)
    return configuracao


def _acrescentar_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=None,
        help=r"caminho do config.toml (padrão: WHIS_CONFIG ou %%APPDATA%%\whis)",
    )


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whis",
        description="Agente pessoal local: diário, daily, jornal e busca no vault Obsidian.",
    )
    parser.add_argument("-v", "--verboso", action="store_true", help="logs detalhados")
    subcomandos = parser.add_subparsers(dest="comando", required=True)

    diario_cmd = subcomandos.add_parser(
        "diario",
        help="lê as fontes passivas do dia e escreve o diário em diario/",
        epilog=CODIGOS_DE_SAIDA,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    diario_cmd.add_argument(
        "data",
        nargs="?",
        type=_data_iso,
        default=None,
        help="dia do diário (YYYY-MM-DD); sem ele, o dia deduzido do relógio",
    )
    _acrescentar_config(diario_cmd)
    diario_cmd.add_argument(
        "--fakes",
        action="store_true",
        help="usa fontes e resumo fake em vez de git, Linear, Slack e Claude",
    )
    diario_cmd.add_argument(
        "--simular",
        action="store_true",
        help="mostra o que entraria no diário, sem enviar ao Claude e sem escrever",
    )
    diario_cmd.set_defaults(funcao=_comando_diario)

    daily_cmd = subcomandos.add_parser(
        "daily",
        help="publica os slides de daily/atual.md no link de sempre e grava a cópia em daily/",
        epilog=CODIGOS_DA_DAILY,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _acrescentar_config(daily_cmd)
    daily_cmd.add_argument(
        "--anotar",
        metavar="TEXTO",
        default=None,
        help="acrescenta uma linha em '## Pontos' de daily/atual.md e sai, sem publicar nada",
    )
    daily_cmd.add_argument(
        "--fakes",
        action="store_true",
        help="o mesmo que --simular: mostra os slides sem publicar nem escrever",
    )
    daily_cmd.add_argument(
        "--simular",
        action="store_true",
        help="mostra os slides que sairiam, sem publicar e sem escrever",
    )
    daily_cmd.add_argument(
        "--agendada",
        action="store_true",
        help="é como a tarefa Whis-Daily chama: sai sem gerar nada se [daily].ativa = false",
    )
    daily_cmd.set_defaults(funcao=_comando_daily)

    news_cmd = subcomandos.add_parser(
        "news",
        help="gera o jornal de hoje em noticias/ e publica a página",
        description=(
            "Lê os feeds de noticias/fontes.md, pede ao Claude da sua conta que escolha, "
            "resuma e ensine, escreve noticias/AAAA-MM-DD.md e publica a página como "
            "artifact, no mesmo endereço da edição anterior."
        ),
        epilog=CODIGOS_DE_SAIDA,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _acrescentar_config(news_cmd)
    news_cmd.add_argument(
        "--fakes",
        action="store_true",
        help="usa editor, formação e publicação fake: sem Claude, sem publicar, sem escrever no vault",
    )
    news_cmd.add_argument(
        "--simular",
        action="store_true",
        help="gera nota e página numa pasta temporária, sem publicar e sem escrever no vault",
    )
    news_cmd.add_argument(
        "--agendada",
        action="store_true",
        help="é como a tarefa Whis-Noticias chama: sai sem gerar nada se [noticias].ativa = false",
    )
    news_cmd.add_argument(
        "--sabado",
        action="store_true",
        help="faz a edição de sábado (o quiz no lugar das aulas novas) em qualquer dia; só com --simular ou --fakes",
    )
    news_cmd.set_defaults(funcao=_comando_news)

    x_login_cmd = subcomandos.add_parser(
        "x-login",
        help="abre o Chrome do Whis no login do x.com para você entrar na conta do jornal",
        description=(
            "Abre um Chrome comum com o perfil só do Whis (%LOCALAPPDATA%\\whis\\navegador-x) na tela "
            "de login do x.com. Entre na sua conta e feche a janela quando a home aparecer; o Whis "
            "confere a sessão e o jornal passa a ler a seção Dev com IA. O Whis nunca vê a senha."
        ),
    )
    x_login_cmd.set_defaults(funcao=_comando_x_login)

    instagram_login_cmd = subcomandos.add_parser(
        "instagram-login",
        help="abre o Chrome do Whis no login do Instagram para você entrar na @velozo.dev",
        description=(
            "Abre um Chrome comum com o perfil só do Whis (%LOCALAPPDATA%\\whis\\navegador-instagram) no "
            "login do Instagram. Entre na @velozo.dev e feche a janela; o Whis confere a sessão. O Whis "
            "nunca vê a senha."
        ),
    )
    instagram_login_cmd.set_defaults(funcao=_comando_instagram_login)

    instagram_postar_cmd = subcomandos.add_parser(
        "instagram-postar",
        help="publica na @velozo.dev o Reel ou o carrossel do dia do calendário do velozo-reels",
    )
    _acrescentar_config(instagram_postar_cmd)
    instagram_postar_cmd.add_argument("--agendada", action="store_true", help="chamada pela tarefa das 19:00: não tenta de novo o que falhou")
    instagram_postar_cmd.add_argument("--simular", action="store_true", help="mostra o calendário conferido e o que sairia, sem abrir o Chrome")
    instagram_postar_cmd.add_argument("--ensaio", action="store_true", help="monta o post no Instagram até o Compartilhar, sem clicar")
    instagram_postar_cmd.add_argument("--pasta", default=None, help="um item do calendário (ex.: 01-plinko) em vez do de hoje")
    instagram_postar_cmd.add_argument("--data", type=date.fromisoformat, default=None, help="com --simular: o dia AAAA-MM-DD às 19:01")
    instagram_postar_cmd.set_defaults(funcao=_comando_instagram_postar)

    perguntar_cmd = subcomandos.add_parser(
        "perguntar",
        help="pergunta ao vault pelo terminal, citando as notas (para teste e diagnóstico)",
        epilog=CODIGOS_DA_PERGUNTA,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _acrescentar_config(perguntar_cmd)
    perguntar_cmd.add_argument("pergunta", help="a pergunta, entre aspas")
    perguntar_cmd.add_argument(
        "--fakes",
        action="store_true",
        help="usa a busca fake em vez do Claude (sem rede, sem gastar a assinatura)",
    )
    perguntar_cmd.add_argument(
        "--sem-fio",
        action="store_true",
        help="pergunta sem ler nem gravar o fio de conversa do terminal",
    )
    perguntar_cmd.set_defaults(funcao=_comando_perguntar)

    atender_cmd = subcomandos.add_parser(
        "atender",
        help="fica no ar respondendo na DM do bot no Slack (Socket Mode, sem porta aberta)",
        description=(
            "Fica no ar escutando a DM do bot no Slack por Socket Mode — conexão de dentro "
            "para fora, sem porta aberta — e responde ali, citando as notas do vault. "
            "Ctrl+C encerra. Precisa de [slack].bot_token e [slack].app_token."
        ),
    )
    _acrescentar_config(atender_cmd)
    atender_cmd.add_argument(
        "--fakes",
        action="store_true",
        help="responde na DM de verdade com a busca fake (ensaia o Slack sem gastar a assinatura)",
    )
    atender_cmd.set_defaults(funcao=_comando_atender)

    meetings_cmd = subcomandos.add_parser(
        "meetings",
        help="puxa da Granola as reuniões que ainda não estão em reunioes/",
        description=(
            "Lista as reuniões dos últimos 30 dias na Granola (MCP oficial) e escreve em "
            "reunioes/ as que ainda não estão lá, com o resumo da Granola. Uma nota que já "
            "existe nunca é reescrita. Sem login guardado, abre a Granola no navegador."
        ),
        epilog=CODIGOS_DE_SAIDA,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _acrescentar_config(meetings_cmd)
    meetings_cmd.add_argument(
        "--agendada",
        action="store_true",
        help="é como a tarefa Whis-Reunioes chama: nunca abre o navegador e só avisa se chegou nota ou falhou",
    )
    meetings_cmd.add_argument(
        "--fakes",
        action="store_true",
        help="usa uma Granola de mentira e escreve num vault temporário (sem rede, sem login)",
    )
    meetings_cmd.set_defaults(funcao=_comando_meetings)

    hunt_cmd = subcomandos.add_parser(
        "hunt",
        help="caça vagas e freelas por horas e deixa tudo pronto em cacada/; nada é enviado",
        description=(
            "Lê as fontes de cacada/criterios.md (APIs de vaga, Workana, 99Freelas, a busca pública do "
            "LinkedIn, a web pelo Claude), monta o seu perfil, faz a triagem e prepara as chances boas: "
            "proposta pronta, preço, como enviar e, no freela web, uma amostra. Pausa quando a janela de "
            "5 h do plano bate e para perto do teto semanal. Fora da véspera da virada da semana do plano "
            "(mais de 24 h), para também no limite de [cacada].limite_minutos e [cacada].limite_plano. "
            "Rodar de novo retoma de onde parou."
        ),
        epilog=CODIGOS_DA_CACADA,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _acrescentar_config(hunt_cmd)
    hunt_cmd.add_argument(
        "--simular",
        action="store_true",
        help="só lê as fontes e mostra quantas oportunidades cada uma trouxe (sem Claude, sem escrever)",
    )
    hunt_cmd.add_argument(
        "--fakes", action="store_true", help="a caçada inteira com portas fake num vault temporário (sem rede)"
    )
    hunt_cmd.add_argument(
        "--do-zero", action="store_true", help="ignora a caçada inacabada e começa outra"
    )
    hunt_cmd.add_argument(
        "--freelas",
        action="store_true",
        help=(
            "modo freelas rápidos: só freela pequeno de preço fechado que paga logo e casa com você; "
            "lê só as fontes de freela e vasculha a web a fundo por vários lados"
        ),
    )
    hunt_cmd.add_argument(
        "--limite-minutos",
        type=_inteiro_nao_negativo,
        metavar="N",
        help="para depois de N minutos, perto da virada ou não (0 = sem limite de tempo); vale no lugar de [cacada].limite_minutos",
    )
    hunt_cmd.add_argument(
        "--limite-plano",
        type=_pontos_do_plano,
        metavar="N",
        help="para depois de gastar N pontos do plano semanal, perto da virada ou não (0 = sem limite de plano); vale no lugar de [cacada].limite_plano",
    )
    hunt_cmd.add_argument(
        "--sem-limite",
        action="store_true",
        help="vai até o plano acabar, mesmo longe da virada (no modo freelas, atravessa a virada e emenda a caçada de vagas)",
    )
    hunt_cmd.set_defaults(funcao=_comando_hunt)

    contas_cmd = subcomandos.add_parser(
        "contas",
        help="cria as contas de cacada/criterios.md com o Gmail dedicado, confirma e completa os perfis (com você no PC)",
    )
    _acrescentar_config(contas_cmd)
    contas_cmd.add_argument("--so", metavar="SITE", default=None, help="só o site cujo nome começa com isto")
    contas_cmd.add_argument("--senha", metavar="SITE", default=None, help="mostra o e-mail e a senha guardados de um site e sai")
    contas_cmd.set_defaults(funcao=_comando_contas)

    lembrete_cmd = subcomandos.add_parser(
        "lembrete", help="avisa por toast quando a semana do plano do Claude vira nas próximas 30 h (véspera da caçada)"
    )
    _acrescentar_config(lembrete_cmd)
    lembrete_cmd.add_argument(
        "--agendada", action="store_true", help="é como a tarefa Whis-Lembrete-Cacada chama: um toast só por virada"
    )
    lembrete_cmd.set_defaults(funcao=_comando_lembrete)

    vigia_freelas_cmd = subcomandos.add_parser(
        "vigia-freelas",
        help="olha a 99Freelas e o Freelancer.com, avisa por toast e no celular o projeto novo de [cacada].nicho_freela "
        "e, com [cacada].propostas_auto, propõe na 99Freelas",
    )
    _acrescentar_config(vigia_freelas_cmd)
    vigia_freelas_cmd.add_argument(
        "--agendada", action="store_true", help="é como a tarefa Whis-Vigia-Freelas chama"
    )
    vigia_freelas_cmd.add_argument(
        "--fakes", action="store_true",
        help="um projeto falso e um estado temporário: o toast e um ensaio da proposta automática, sem Claude nem Chrome"
    )
    vigia_freelas_cmd.set_defaults(funcao=_comando_vigia_freelas)

    frio_cmd = subcomandos.add_parser(
        "sequencia-fria",
        help="importa para a sequência do e-mail frio os envios feitos à mão (a tabela de uma nota como cacada/envios-2026-10-28.md)",
    )
    _acrescentar_config(frio_cmd)
    frio_cmd.add_argument("--importar", metavar="NOTA", required=True, help="a nota do vault com a tabela, ex.: cacada/envios-2026-10-28.md")
    frio_cmd.set_defaults(funcao=_comando_sequencia_fria)

    celular_cmd = subcomandos.add_parser(
        "celular", help="liga o bot do Telegram que te avisa no celular: pede o token, descobre o chat e grava [celular]"
    )
    _acrescentar_config(celular_cmd)
    celular_cmd.add_argument("--teste", action="store_true", help="só manda uma mensagem de teste com o que já está no config")
    celular_cmd.add_argument("--tocar", default="", help=argparse.SUPPRESS)
    celular_cmd.add_argument(
        "--pendentes",
        action="store_true",
        help="manda de novo, com o botão Enviar, as propostas da 99Freelas escritas nos últimos 2 dias que esperam o seu toque",
    )
    celular_cmd.set_defaults(funcao=_comando_celular)

    o_que_pedem_cmd = subcomandos.add_parser(
        "o-que-pedem",
        help="conta o que os clientes do nicho pedem em todas as caçadas do vault e escreve cacada/o-que-pedem.md",
    )
    _acrescentar_config(o_que_pedem_cmd)
    o_que_pedem_cmd.add_argument("--nicho", default="", help="o nicho a contar; sem ele, [cacada].nicho_freela")
    o_que_pedem_cmd.set_defaults(funcao=_comando_o_que_pedem)

    agencias_cmd = subcomandos.add_parser(
        "agencias",
        help="uma vez por mês, busca na web agências Shopify do Brasil que ainda não estão em cacada/agencias-shopify.md (só leitura)",
    )
    _acrescentar_config(agencias_cmd)
    agencias_cmd.add_argument("--forcar", action="store_true", help="busca de novo mesmo que este mês já tenha sido feito")
    agencias_cmd.add_argument("--fakes", action="store_true", help="uma agência fictícia num vault temporário, sem Claude")
    agencias_cmd.set_defaults(funcao=_comando_agencias)

    procurar_cmd = subcomandos.add_parser(
        "procurar",
        help="busca na web agências e lojas Shopify lentas do Brasil quando a fila de e-mails ficou curta (só leitura; tarefa Whis-Procura)",
    )
    _acrescentar_config(procurar_cmd)
    procurar_cmd.add_argument("--agendada", action="store_true", help="é como a tarefa Whis-Procura chama")
    procurar_cmd.add_argument("--forcar", action="store_true", help="busca agora mesmo com a fila cheia ou a busca de hoje feita")
    procurar_cmd.add_argument("--fakes", action="store_true", help="uma agência e uma loja fictícias num vault temporário, sem Claude nem Chrome")
    procurar_cmd.set_defaults(funcao=_comando_procurar)

    instagram_cmd = subcomandos.add_parser(
        "instagram",
        help="a lista do dia de perfis do Instagram de marcas pequenas, com a primeira mensagem escrita, no celular (você manda à mão)",
    )
    _acrescentar_config(instagram_cmd)
    instagram_cmd.add_argument("--forcar", action="store_true", help="faz outra lista mesmo que a de hoje já tenha saído")
    instagram_cmd.add_argument("--fakes", action="store_true", help="dois perfis fictícios num vault temporário, sem Claude nem Telegram")
    instagram_cmd.set_defaults(funcao=_comando_instagram)

    vigia_x_cmd = subcomandos.add_parser(
        "vigia-x",
        help="lê agora as buscas de cacada/x/buscas.md no Chrome do jornal e manda ao celular a resposta escrita para quem "
        "está contratando dev de Shopify no X (você manda à mão); a vigia de freelas faz isso sozinha a cada 3 horas",
    )
    _acrescentar_config(vigia_x_cmd)
    vigia_x_cmd.add_argument("--fakes", action="store_true", help="três posts falsos e um estado temporário, sem Chrome, Claude nem Telegram")
    vigia_x_cmd.set_defaults(funcao=_comando_vigia_x)

    ideia_x_cmd = subcomandos.add_parser(
        "ideia-x",
        help="o post do X do dia (seg, qua e sex), pronto, no celular: primeiro os de cacada/x/posts-*.md, depois uma ideia nova; "
        "você publica à mão",
    )
    _acrescentar_config(ideia_x_cmd)
    ideia_x_cmd.add_argument("--forcar", action="store_true", help="manda um post agora, mesmo fora de seg, qua e sex ou com o de hoje já mandado")
    ideia_x_cmd.add_argument("--fakes", action="store_true", help="uma thread fictícia num vault temporário, sem Claude nem Telegram")
    ideia_x_cmd.set_defaults(funcao=_comando_ideia_x)

    candidaturas_cmd = subcomandos.add_parser(
        "candidaturas", help="atualiza cacada/candidaturas.md: pacotes, o que o Whis enviou e as respostas no Gmail"
    )
    _acrescentar_config(candidaturas_cmd)
    candidaturas_cmd.set_defaults(funcao=_comando_candidaturas)

    navegador_cmd = subcomandos.add_parser(
        "navegador", help="abre o Chrome da caçada para você entrar à mão nos sites que a caçada lê logada"
    )
    navegador_cmd.add_argument(
        "url",
        nargs="?",
        default=None,
        help="onde abrir; sem nada, abre as abas de Freelancer.com, 99Freelas, VintePila e freelancer.com.br",
    )
    navegador_cmd.set_defaults(funcao=_comando_navegador)

    linkedin_cmd = subcomandos.add_parser(
        "linkedin",
        help="analisa o perfil do LinkedIn (pelo export de dados) e escreve rascunhos de post; nada é publicado",
    )
    _acrescentar_config(linkedin_cmd)
    linkedin_cmd.add_argument("--fakes", action="store_true", help="sem Claude, num vault temporário")
    linkedin_cmd.set_defaults(funcao=_comando_linkedin)

    linkedin_login_cmd = subcomandos.add_parser(
        "linkedin-login", help="entra no LinkedIn pela API oficial (OAuth) para o Whis poder publicar o que você aprovar"
    )
    _acrescentar_config(linkedin_login_cmd)
    linkedin_login_cmd.set_defaults(funcao=_comando_linkedin_login)

    linkedin_publicar_cmd = subcomandos.add_parser(
        "linkedin-publicar", help="publica um rascunho de linkedin/posts/ no seu perfil, depois de você confirmar"
    )
    _acrescentar_config(linkedin_publicar_cmd)
    linkedin_publicar_cmd.add_argument("nota", help="o caminho da nota, relativo ao vault (linkedin/posts/...md)")
    linkedin_publicar_cmd.set_defaults(funcao=_comando_linkedin_publicar)

    bandeja_cmd = subcomandos.add_parser(
        "bandeja",
        help="ícone na bandeja do Windows com a palheta no atalho global",
    )
    _acrescentar_config(bandeja_cmd)
    bandeja_cmd.add_argument(
        "--fakes",
        action="store_true",
        help="os comandos da palheta rodam com as portas fake (sem rede, sem gastar a assinatura)",
    )
    bandeja_cmd.set_defaults(funcao=_comando_bandeja)

    init_cmd = subcomandos.add_parser(
        "init", help="cria o config.toml inicial com os caminhos e as chaves a preencher"
    )
    _acrescentar_config(init_cmd)
    init_cmd.add_argument(
        "--forcar", action="store_true", help="sobrescreve uma configuração existente"
    )
    init_cmd.set_defaults(funcao=_comando_init)

    windows_cmd = subcomandos.add_parser(
        "windows",
        aliases=["mac"],
        help="faz a bandeja subir com o logon (e, opcionalmente, agenda a daily, o jornal e as reuniões)",
        description=(
            "Amarra o Whis ao Windows. Sozinho, cria o atalho de logon da bandeja. "
            "--daily agenda a daily nos dias e horas de [daily].horarios e o vigia que "
            "avisa por toast quando uma peça do Whis não subiu; --news agenda o jornal "
            "todo dia em [noticias].horario; --meetings agenda a puxada das reuniões da "
            "Granola todo dia em [granola].horario. As tarefas rodam mesmo que o PC "
            "estivesse desligado na hora. --remover com os mesmos flags desfaz."
        ),
    )
    _acrescentar_config(windows_cmd)
    windows_cmd.add_argument(
        "--atendente",
        action="store_true",
        help="também faz o agente da DM do Slack (atender) subir com o logon",
    )
    windows_cmd.add_argument(
        "--daily",
        action="store_true",
        help=(
            "também agenda a daily nos horários de [daily].horarios (tarefa Whis-Daily) "
            "e o vigia (Whis-Vigia)"
        ),
    )
    windows_cmd.add_argument(
        "--news",
        action="store_true",
        help=(
            "também agenda o jornal todo dia em [noticias].horario (tarefa Whis-Noticias) "
            "e reagenda o vigia, se existe, para olhar por ele"
        ),
    )
    windows_cmd.add_argument(
        "--meetings",
        action="store_true",
        help=(
            "também agenda a puxada das reuniões da Granola todo dia em [granola].horario "
            "(tarefa Whis-Reunioes) e reagenda o vigia, se existe, para olhar por ela"
        ),
    )
    windows_cmd.add_argument(
        "--hunt",
        action="store_true",
        help=(
            "também agenda o lembrete da véspera da caçada todo dia em [cacada].hora_do_lembrete "
            "(tarefa Whis-Lembrete-Cacada); a caçada em si continua só quando você chama"
        ),
    )
    windows_cmd.add_argument(
        "--vigia-freelas",
        action="store_true",
        help=(
            "também agenda a vigia de freelas nas horas de [cacada].horarios_da_vigia_de_freelas "
            "(tarefa Whis-Vigia-Freelas): o projeto novo de [cacada].nicho_freela vira toast"
        ),
    )
    windows_cmd.add_argument(
        "--procura",
        action="store_true",
        help=(
            "também agenda a procura diária em [cacada].horario_da_procura (tarefa Whis-Procura): "
            "agências e lojas Shopify lentas novas quando a fila de e-mails acaba"
        ),
    )
    windows_cmd.add_argument(
        "--instagram",
        action="store_true",
        help="também agenda os posts da @velozo.dev às 19:00 (tarefa Whis-Instagram), pelo calendário do velozo-reels",
    )
    windows_cmd.add_argument(
        "--remover", action="store_true", help="desfaz o que foi instalado"
    )
    windows_cmd.add_argument(
        "--remover-legado",
        action="store_true",
        help=(
            "apaga o atalho 'Second Brain.lnk' e as tarefas 'SecondBrain-Limpeza' e "
            "'Whis-Limpeza', que chamam o que não existe mais; sozinho, não instala nada"
        ),
    )
    windows_cmd.set_defaults(funcao=_comando_windows)
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.stdout is None:
        # Sob `pythonw.exe` (bandeja, tarefas agendadas): sem isto cada `git`,
        # PowerShell e CLI do Claude abriria um console próprio na tela.
        from . import sem_console

        sem_console.instalar()
    forcar_utf8_na_saida()
    parser = construir_parser()
    args = parser.parse_args(argv)
    configurar_logs(args.verboso)
    try:
        return int(args.funcao(args))
    except (
        ConfiguracaoInvalida,
        ErroDeBusca,
        PerguntaVazia,
        TodasAsFontesFalharam,
        NenhumaFonteConfigurada,
    ) as erro:
        _log.error("%s", erro)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
