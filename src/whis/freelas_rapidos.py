"""O modo freelas rápidos da caçada (`whis hunt --freelas`): dinheiro rápido, só freela.

A corrida é a mesma da caçada; muda o que ela lê e como julga. Das fontes de
*Fontes*, ficam só as de freela (Workana, 99Freelas, Freelancer.com), e
entram as de `FONTES_DE_FREELA_RAPIDO`: mais buscas no Freelancer.com e a web
vasculhada por vários lados (plataformas, Shopify, bounties pagos, posts de
quem está contratando). O pedido do modo vai na frente de *O que eu procuro*,
então a triagem e o resumo leem as regras do modo antes das dele.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from .criterios_da_cacada import Criterios, FonteDaCacada
from .fontes_da_cacada import fonte_paga
from .navegador_logado import casa_com_palavras
from .oportunidades import CHANCE_BOA, TENTAR_A_SORTE, Oportunidade, Veredito

MODO_FREELAS = "freelas"

#: O que faz um freela ser do nicho: o nome dele, no título ou na descrição; nicho
#: fora da tabela casa só pelo próprio nome.
PALAVRAS_DO_NICHO = {
    "shopify": ("shopify", "liquid"),
    #: Manutenção de sites e sistemas, e sistema robusto (ERP, CRM, sistema interno, back-end).
    "sistemas": (
        "manutenção", "manutencao", "maintenance", "maintain", "suporte de site", "suporte do sistema",
        "erp", "crm", "sistema web", "sistema de gestão", "sistema interno", "sistema sob medida", "web app",
        "saas", "back-end", "backend", "laravel", "asp.net", ".net", "c#", "legacy", "legado",
        "painel administrativo", "admin panel", "dashboard",
    ),
}

#: O e-commerce muito perto do nicho, só no título: na descrição, "e-commerce" aparece
#: de passagem em vaga de marketing e site de imobiliária (simulação de 17/10).
PERTO_DO_NICHO = {
    "shopify": (
        "e-commerce", "ecommerce", "loja virtual", "loja online", "online store", "woocommerce", "nuvemshop",
        "vtex", "bigcommerce", "magento",
    ),
    "sistemas": (
        "sistema", "system", "api", "integração", "integracao", "integration", "node.js", "nodejs", "python",
        "php", "postgresql", "sql server", "automação", "automation", "plataforma", "platform",
    ),
}


#: A loja virtual que já escolheu outra plataforma não pede o nicho.
OUTRAS_PLATAFORMAS = {
    "shopify": (
        "woocommerce", "wordpress", "nuvemshop", "vtex", "bigcommerce", "magento", "wix", "loja integrada", "tray",
    ),
}


def palavras_do_nicho(nicho: str) -> tuple[str, ...]:
    nicho = nicho.strip().casefold()
    return PALAVRAS_DO_NICHO.get(nicho, (nicho,)) if nicho else ()


def do_nicho(oportunidade: Oportunidade, nicho: str) -> bool:
    """Sem nicho, tudo é; com ele, o nicho no título ou na descrição, ou o e-commerce perto dele no título."""
    if not nicho.strip():
        return True
    if casa_com_palavras(oportunidade, palavras_do_nicho(nicho)):
        return True
    so_o_titulo = replace(oportunidade, descricao="")
    return casa_com_palavras(so_o_titulo, PERTO_DO_NICHO.get(nicho.strip().casefold(), ()))


def pede_o_nicho(oportunidade: Oportunidade, nicho: str) -> bool:
    """Onde a proposta do nicho cabe: o nicho citado, ou a loja virtual do `do_nicho` sem outra plataforma escolhida."""
    if casa_com_palavras(oportunidade, palavras_do_nicho(nicho)):
        return True
    outras = OUTRAS_PLATAFORMAS.get(nicho.strip().casefold(), ())
    return do_nicho(oportunidade, nicho) and not casa_com_palavras(oportunidade, outras)


def rebaixar_fora_do_nicho(
    vereditos: Sequence[Veredito], oportunidades: Mapping[str, Oportunidade], nicho: str
) -> tuple[Veredito, ...]:
    """A chance boa que não é do nicho vira tentar a sorte: o pedido sozinho não segurou (14/10, 8 de 9)."""
    saida = []
    for veredito in vereditos:
        oportunidade = oportunidades.get(veredito.id)
        if veredito.classe == CHANCE_BOA and oportunidade is not None and not do_nicho(oportunidade, nicho):
            veredito = replace(veredito, classe=TENTAR_A_SORTE, motivo=f"fora do nicho ({nicho}): {veredito.motivo}")
        saida.append(veredito)
    return tuple(saida)

TIPOS_DE_FREELA = frozenset({"workana", "99freelas", "freelancer"})

_INICIO_DO_PEDIDO = """\
NESTA CAÇADA (modo freelas rápidos), estas regras valem antes das outras:
- Só freela. Vaga de emprego, CLT, PJ fixo ou contrato contínuo é "descartada", por melhor que seja.
- O que ele quer é dinheiro rápido: freela pequeno de preço fechado (ou por marco) que ele entrega com \
IA em algumas horas ou num fim de semana, até umas 20 horas de trabalho, e que paga na entrega pela \
plataforma (escrow, marco) ou por bounty."""

_CHANCE_SEM_NICHO = """\
- "chance_boa" é o freela rápido que casa forte com o que ele já fez: ajuste ou seção de tema Shopify, \
app ou checkout Shopify, landing page, site pequeno, correção de bug em React/Next.js/Node, integração \
de API ou webhook, automação ou script em Python ou Node, scraping, painel simples, integração com LLM. \
Quanto mais novo o anúncio (até 3 dias) e menos propostas, maior o encaixe.
- "tentar_a_sorte" é o freela rápido de encaixe médio, ou o bom que já tem muita proposta."""

#: O trabalho do nicho que casa com o que ele já fez, para o pedido dizer o que é chance boa.
TRABALHO_DO_NICHO = {
    "sistemas": (
        "manutenção e evolução de site ou sistema que já existe (bug, atualização, performance, hospedagem), ERP, "
        "CRM, painel e sistema interno, back-end e API em Node.js, Python, C#/.NET ou PHP/Laravel com PostgreSQL, "
        "SQL Server ou MySQL, integração entre sistemas e automação, e IA aplicada (agentes, LLM) dentro deles"
    ),
    "shopify": (
        "ajuste ou seção de tema, Liquid, app ou checkout, montar ou configurar a loja, velocidade, rastreio "
        "(Pixel, GA4, Merchant Center) ou integração na loja"
    ),
}

_FIM_DO_PEDIDO = """\
- Bounty de issue (GitHub, Algora, Opire) é disputado em minutos: ganha quem posta a proposta logo que \
a issue abre. Bounty com proposta já postada, PR aberto ou mais de uns 5 comentários nunca é \
"chance_boa": no máximo "tentar_a_sorte". Bounty com responsável designado, PR já aceito ou issue \
fechada é "descartada". Sem "detalhes" que mostrem a concorrência, bounty também fica em \
"tentar_a_sorte", e o motivo diz que não deu para ver quantos já propuseram.
- Projeto da 99Freelas com "Projeto exclusivo para assinantes" nos detalhes só aceita proposta dele na hora que os detalhes dizem: julgue igual aos outros, e o resumo começa dizendo quando abre para ele.
- Projeto do Freelancer.com com "escolha a skill" nos detalhes: o resumo diz qual skill ele marca na proposta e avisa que, se a página disser "aberto apenas a freelancers selecionados", ele pula (pede saldo de US$ 20, que ele não deposita). A conta grátis dele tem só 6 propostas por mês no Freelancer.com: lá, "chance_boa" só com encaixe muito forte.
- Freela grande (semanas de trabalho), sem orçamento nenhum e sem escopo, ou com prazo curto demais \
para quem trabalha de dia é "descartada". Freela por hora avulso continua "descartada".
- No resumo do freela, diga quantas horas ele leva com IA e o preço que faz sentido pedir."""


#: O trabalho muito perto do nicho que também pode ser chance boa.
_VIZINHO = "de e-commerce muito perto dele (loja virtual em outra plataforma, com o mesmo tipo de trabalho)"
VIZINHO_DO_NICHO = {"sistemas": "de sistema web, API ou integração muito perto dele (o mesmo tipo de trabalho)"}

#: O que o nicho muda nas regras do modo (que pedem freela pequeno e descartam contrato contínuo).
REGRA_DO_NICHO = {
    "sistemas": (
        "- Neste nicho valem duas exceções às regras acima: (1) manutenção mensal de site ou sistema (valor fixo "
        "por mês, poucas horas por semana) não é descartada por ser contínua; (2) sistema grande (ERP, CRM, "
        "sistema interno) pode passar das 20 horas quando é pago por marco, com escopo e orçamento claros e prazo "
        "que caiba em quem trabalha de dia. Os dois entram no máximo como \"chance_boa\" com o encaixe que merecem; "
        "sem escopo, sem orçamento ou com prazo curto continuam \"descartada\".\n"
    ),
}


def _chance_do_nicho(nicho: str) -> str:
    nome = nicho.strip().title()
    trabalho = TRABALHO_DO_NICHO.get(nicho.strip().casefold())
    do_nicho = f"de {nome} ({trabalho})" if trabalho else f"de {nome}"
    return REGRA_DO_NICHO.get(nicho.strip().casefold(), "") + (
        f'- "chance_boa" é só o freela rápido {do_nicho}, ou {VIZINHO_DO_NICHO.get(nicho.strip().casefold(), _VIZINHO)}, '
        "que casa forte com o que ele já fez. Quanto mais novo o anúncio "
        "(até 3 dias) e menos propostas, maior o encaixe.\n"
        f'- "tentar_a_sorte" é o freela rápido do nicho de encaixe médio ou com muita proposta, e o de fora do nicho '
        f'que casa com ele: fora do nicho nunca é "chance_boa", por melhor que seja o encaixe (é em {nome} que ele '
        "tem prova de trabalho).\n"
        f"- O perfil desta caçada é o de freela de {nome}: o resumo e a stack abrem pelo que ele já fez em {nome} "
        "(só o que tem evidência), e o full stack vem depois. No resumo de cada freela, o porquê casa começa pelo "
        f"trabalho dele em {nome}."
    )


def pedido_de_freelas(nicho: str = "") -> str:
    """As regras do modo; com `nicho`, chance boa é só dele ou de e-commerce muito perto."""
    chance = _chance_do_nicho(nicho) if nicho.strip() else _CHANCE_SEM_NICHO
    return f"{_INICIO_DO_PEDIDO}\n{chance}\n{_FIM_DO_PEDIDO}"


PEDIDO_DE_FREELAS_RAPIDOS = pedido_de_freelas()

_A_FUNDO = (
    "Vasculhe a fundo: abra várias listagens e páginas, confira se cada projeto ainda está aberto e "
    "recebendo propostas, e traga o máximo de freelas reais que achar."
)

PEDIDO_DA_RODADA_EXTRA = (
    "RODADA EXTRA: a primeira passada já trouxe o que está em JÁ VISTAS. Agora traga freelas DIFERENTES: "
    "abra páginas, filtros, categorias e sites que você ainda não abriu, e não repita nenhum link."
)

PEOPLEPERHOUR = "peopleperhour"
WORKANA = "workana"
NOVENTA_E_NOVE = "99freelas"
VINTEPILA = "vintepila"
FREELANCER_BR = "freelancer.com.br"


_RECENTES = (
    "de landing page, site pequeno, Shopify, WordPress, React/Next.js, correção de bug, integração de API ou automação"
)
_FORA_DAS_GRANDES = "de desenvolvimento web, sistema pequeno, automação ou loja virtual"
_INTERNACIONAL = (
    "Small Shopify fixes, landing pages, React/Next.js or Node bug fixes, API integrations, scripts and automations"
)
_POSTS = "de site, loja, automação, integração ou correção de bug"

#: Bounty de open-source nunca é do nicho e custa até 1 h por rodada: com nicho, a busca sai.
NOME_DOS_BOUNTIES = "Web · bounties pagos"

#: O que as buscas web genéricas pedem quando há nicho (recentes, fora das grandes, internacional, posts).
BUSCAS_DO_NICHO = {
    "sistemas": (
        "de manutenção de site ou sistema web que já existe, ERP, CRM, painel administrativo, back-end ou API "
        "(Node.js, Python, C#/.NET, PHP/Laravel) ou integração entre sistemas",
        "de manutenção de sites e sistemas, ERP, CRM ou sistema interno sob medida",
        "Website and web app maintenance (monthly retainers too), ERP/CRM and custom business systems, back-end and "
        "API work (Node.js, Python, C#/.NET, PHP/Laravel), system integrations; clients anywhere in the world, "
        "paid in USD",
        "de manutenção de site ou sistema, ERP, CRM ou sistema sob medida",
    ),
    "shopify": (
        "de Shopify (ajuste ou seção de tema, Liquid, montar ou configurar a loja, app, checkout, velocidade) ou de "
        "loja virtual em outra plataforma",
        "de loja Shopify ou de loja virtual em outra plataforma",
        "Shopify work only: theme and section fixes, Liquid, store setup, Shopify apps and integrations, checkout, speed",
        "de loja Shopify (tema, seção, app, checkout, montar a loja)",
    ),
}


def _buscas_do_nicho(nicho: str) -> tuple[str, str, str, str]:
    nome = nicho.strip().title()
    return BUSCAS_DO_NICHO.get(nicho.strip().casefold(), (f"de {nome}", f"de {nome}", f"{nome} work only", f"de {nome}"))


def _web_posts(o_que: str = _POSTS) -> FonteDaCacada:
    return FonteDaCacada(
        "Web · posts de quem contrata no Brasil",
        "web",
        "posts públicos dos últimos 3 dias de brasileiros procurando freelancer ou dev para um trabalho pago: "
        "LinkedIn (\"procuro freelancer\", \"preciso de um dev\", \"freela de site\", \"freela Shopify\"), X/Twitter "
        "(\"freela dev\", \"procuro programador\"), grupos e comunidades públicas (Discord, Telegram, Facebook, "
        f"Reddit r/brdev e r/programacao), {o_que}. Traga o "
        "post com o link dele e como falar com quem postou. " + _A_FUNDO,
    )


def _web_recentes(com_workana: bool, com_99freelas: bool = True, o_que: str = _RECENTES) -> FonteDaCacada | None:
    if com_workana and com_99freelas:
        nome, onde = "Web · Workana e 99Freelas recentes", "na Workana e na 99Freelas"
    elif com_99freelas:
        nome, onde = "Web · 99Freelas recentes", "na 99Freelas (nada da Workana: a conta dele lá não está ativa)"
    elif com_workana:
        nome, onde = "Web · Workana recentes", "na Workana (nada da 99Freelas: ela é lida logada à parte)"
    else:
        return None
    return FonteDaCacada(
        nome,
        "web",
        f"projetos de preço fechado {onde} publicados nas últimas 72 horas, com poucas "
        f"propostas, {o_que}, que dê para entregar em até um fim de semana. " + _A_FUNDO,
    )


def _web_fora_das_grandes(
    com_workana: bool, sem_vintepila: bool = False, sem_freelancer_br: bool = False, o_que: str = _FORA_DAS_GRANDES
) -> FonteDaCacada:
    fora = "fora da Workana e da 99Freelas" if com_workana else (
        "fora da 99Freelas e sem nada da Workana (a conta dele lá não está ativa)"
    )
    nada = [
        "da VintePila (ela é lida logada à parte)" if sem_vintepila else "",
        "do freelancer.com.br (responder lá pede plano pago)" if sem_freelancer_br else "",
    ]
    nada_de = f"Não traga nada {' nem '.join(n for n in nada if n)}. " if any(nada) else ""
    return FonteDaCacada(
        "Web · freelas no Brasil fora das plataformas grandes",
        "web",
        f"pedidos de freela {o_que} publicados nos "
        f"últimos 3 dias no Brasil {fora} (GetNinjas, Freelas.com.br, Trampos, "
        f"comunidades e posts públicos), com orçamento ou preço fechado. {nada_de}" + _A_FUNDO,
    )


def _web_internacional(com_peopleperhour: bool, o_que: str = _INTERNACIONAL) -> FonteDaCacada:
    depois = "Guru and PeoplePerHour" if com_peopleperhour else "Guru"
    pular = "Freelancer.com and Upwork (paid bids)"
    if not com_peopleperhour:
        pular += ", PeoplePerHour (his account there is still under review)"
    return FonteDaCacada(
        "Web · Contra e clientes dos EUA e da Europa",
        "web",
        "fixed-price freelance web development gigs posted in the last 3 days by clients in the US, Canada, "
        f"Europe or Latin America that accept workers from Brazil: Contra projects first (no fees), then {depois}; "
        f"skip {pular} and any project budgeted in Indian rupees. {o_que}, doable in under 20 hours. " + _A_FUNDO,
    )


FONTES_DE_FREELA_RAPIDO = (
    FonteDaCacada("Freelancer.com · Shopify", "freelancer", "shopify"),
    FonteDaCacada("Freelancer.com · Next.js", "freelancer", "nextjs"),
    FonteDaCacada("Freelancer.com · Node.js", "freelancer", "nodejs"),
    FonteDaCacada("Freelancer.com · Python", "freelancer", "python"),
    FonteDaCacada("Freelancer.com · landing page", "freelancer", "landing page"),
    FonteDaCacada("Freelancer.com · bug fix", "freelancer", "bug fix"),
    FonteDaCacada("Freelancer.com · API", "freelancer", "api integration"),
    FonteDaCacada("Freelancer.com · automação", "freelancer", "automation"),
    _web_recentes(com_workana=True),
    _web_internacional(com_peopleperhour=True),
    FonteDaCacada(
        "Web · Shopify",
        "web",
        "lojistas e agências contratando freela rápido de Shopify nos últimos 5 dias: ajuste de tema Liquid, "
        "seção nova, bug no tema ou no carrinho, app ou checkout extension, migração pequena, velocidade da "
        "loja; em fóruns, Reddit (r/shopify, r/ShopifyeCommerce), Upwork, Shopify Community e grupos. " + _A_FUNDO,
    ),
    FonteDaCacada(
        NOME_DOS_BOUNTIES,
        "web",
        "bounties pagos e abertos em projetos open-source de TypeScript, JavaScript, React, Node ou Python, "
        "publicados nos últimos 14 dias, de US$ 50 para cima, no Algora, Opire, IssueHunt, BountySource ou "
        "issues do GitHub com label de bounty, que dê para resolver em poucas horas. Abra a issue de cada "
        "bounty e diga em detalhes quantas propostas e comentários ela já tem, se alguém foi designado e se "
        "já há PR; prefira as que ainda não têm proposta nenhuma. Nunca traga bounty de repositório da "
        "organização SUSU-LABS no GitHub: os repositórios dela escondem malware no eslint.config.js. " + _A_FUNDO,
    ),
    _web_posts(),
    FonteDaCacada(
        "Web · Shopify no Brasil",
        "web",
        "lojistas e agências brasileiras de Shopify procurando dev freela nos últimos 7 dias: posts no "
        "LinkedIn e no Instagram, comunidades de Shopify no Brasil, agências parceiras da Shopify que "
        "terceirizam tema, seção, app ou migração, e lojas que anunciam vaga freela. " + _A_FUNDO,
    ),
    _web_fora_das_grandes(com_workana=True),
)


def _buscas_do_freelancer_no_nicho(fontes: Sequence[FonteDaCacada | None], nicho: str) -> list[FonteDaCacada | None]:
    nicho = nicho.strip().casefold()
    ficam = [f for f in fontes if f is None or f.tipo != "freelancer" or nicho in f.argumento.casefold()]
    if not any(f is not None and f.tipo == "freelancer" for f in ficam):
        ficam.insert(0, FonteDaCacada(f"Freelancer.com · {nicho.title()}", "freelancer", nicho))
    return ficam


def criterios_de_freelas(
    criterios: Criterios,
    plataformas_sem_conta: Sequence[str] = (),
    *,
    cobertos: Sequence[str] = (),
    plataformas_pagas: Sequence[str] = (),
    nicho: str = "",
) -> Criterios:
    """Os critérios dele com as fontes de freela, as do modo e o pedido do modo na frente.

    Plataforma em `plataformas_sem_conta` some do texto das buscas web; hoje
    só o PeoplePerHour e a Workana são citados nelas. `cobertos` são os sites
    que o Chrome da caçada leu logado nesta corrida (99Freelas, VintePila): o
    explorador deixa de procurar neles, e volta quando o login cai. Site em
    `plataformas_pagas` (freelancer.com.br) também sai das buscas, e a fonte de
    uma plataforma paga com leitor próprio (o Freelancer.com) sai inteira. Com `nicho`,
    das buscas do modo no Freelancer.com só fica a do nicho; as dele em
    `criterios.md` ficam todas.
    """
    dele = [fonte for fonte in criterios.fontes if fonte.tipo in TIPOS_DE_FREELA]
    nomes = {fonte.nome.casefold() for fonte in dele}
    sem_conta = {p.casefold() for p in plataformas_sem_conta}
    logados = {c.casefold() for c in cobertos}
    pagas = {p.casefold() for p in plataformas_pagas}
    recentes, fora, internacional, posts = (
        _buscas_do_nicho(nicho) if nicho.strip() else (_RECENTES, _FORA_DAS_GRANDES, _INTERNACIONAL, _POSTS)
    )
    trocas = {
        FONTES_DE_FREELA_RAPIDO.index(original): nova
        for original, nova in (
            (_web_recentes(True), _web_recentes(WORKANA not in sem_conta, NOVENTA_E_NOVE not in logados, recentes)),
            (_web_internacional(True), _web_internacional(PEOPLEPERHOUR not in sem_conta, internacional)),
            (
                _web_fora_das_grandes(True),
                _web_fora_das_grandes(WORKANA not in sem_conta, VINTEPILA in logados, FREELANCER_BR in pagas, fora),
            ),
            (_web_posts(), _web_posts(posts)),
        )
    }
    do_modo = [trocas.get(i, fonte) for i, fonte in enumerate(FONTES_DE_FREELA_RAPIDO)]
    if nicho.strip():
        do_modo = [f for f in _buscas_do_freelancer_no_nicho(do_modo, nicho) if f is None or f.nome != NOME_DOS_BOUNTIES]
    fontes = [
        fonte
        for fonte in dele + [fonte for fonte in do_modo if fonte is not None and fonte.nome.casefold() not in nomes]
        if not fonte_paga(fonte, plataformas_pagas)
    ]
    procuro = f"{pedido_de_freelas(nicho)}\n\n{criterios.procuro}".rstrip()
    return replace(criterios, fontes=tuple(fontes), procuro=procuro)
