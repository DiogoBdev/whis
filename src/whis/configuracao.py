r"""Configuração local em TOML.

Lida de `%APPDATA%\whis\config.toml`, com override pela variável de
ambiente `WHIS_CONFIG` — é assim que os testes apontam para um
diretório temporário. Nenhum segredo mora no repositório: as chaves do
Linear e do Slack vivem só neste arquivo, que está no `.gitignore`.

Toda chave nova nasce com padrão: uma configuração escrita antes das fontes
passivas continua carregando sem edição nenhuma. Chave que o Whis não lê mais
(`gravacoes`, `retencao_dias`, `[deepgram]`, `[audio]`, da gravação que saiu)
é ignorada, e some do arquivo quando o painel o reescreve.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .atalho_global import ATALHO_PADRAO, AtalhoInvalido, analisar_atalho

_log = logging.getLogger("whis.configuracao")
from .fontes import INICIO_DO_DIA_PADRAO, analisar_hora

#: Variável de ambiente que sobrepõe o caminho padrão da configuração.
VARIAVEL_DE_AMBIENTE = "WHIS_CONFIG"

#: Nome do arquivo dentro de `%APPDATA%\whis\`.
NOME_DO_ARQUIVO = "config.toml"

PLATAFORMAS_SEM_CONTA = ("peopleperhour", "workana")
PLATAFORMAS_PAGAS = ("freelancer.com.br", "freelancer.com")
HORARIOS_DA_VIGIA_DE_FREELAS = tuple(f"{hora:02d}:00" for hora in range(7, 24))
MODOS_DAS_PROPOSTAS_AUTO = ("desligado", "ensaio", "ligado")
POSTHOG_CHAVE = "phc_o3G5x8Q7jfzSe6G3fscQ5HQjKJDrH3X9fGg6EUFtZsxT"

#: As chaves aceitas em `[daily].horarios`, na ordem em que a semana anda.
#: Sem acento de propósito: é o que se digita num TOML sem pensar em escape.
DIAS_DA_SEMANA = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")

#: O `config.toml` que `whis init` escreve e que o painel regenera. Cada chave
#: carrega o comentário que explica o que preencher: é a única documentação que
#: o usuário lê na hora em que precisa. `{vault}` é o campo que `init`
#: preenche com o perfil do usuário; as chaves da tabela de horários vão com
#: chaves dobradas por causa do `format`.
MODELO_DE_CONFIGURACAO = """\
# Configuração do Whis. Use barras normais (/) nos caminhos:
# no TOML a barra invertida é escape e quebra o arquivo.

vault = "{vault}"            # a pasta do vault Obsidian: a memória durável do Whis

[claude]
modelo = ""                  # vazio = o modelo padrão da sua conta
cli = ""                     # caminho do claude.exe; vazio = acha sozinho

[diario]
inicio_do_dia = "04:00"      # o diário de um dia vai desta hora à mesma hora do dia seguinte
hora_da_tarefa = "19:00"     # quando a tarefa agendada gera o diário

[daily]
ativa = true                  # false = sem daily: a tarefa agendada não publica os slides e a palheta esconde "Generate daily"
horarios = {{ segunda = "13:55", terca = "13:55", quarta = "10:25", quinta = "13:55", sexta = "10:25" }}  # hora em que os slides da daily saem em cada dia (segunda..domingo, sem acento); dia ausente = sem daily

[noticias]
ativa = true                 # false = sem jornal: a tarefa agendada não gera e a palheta esconde "Generate news"
horario = "08:20"            # quando a tarefa agendada gera o jornal do dia

[granola]
horario = "19:00"            # quando a tarefa agendada puxa as reuniões da Granola para reunioes/

[palheta]
atalho = "ctrl+space"        # o atalho global que abre a palheta: ctrl, alt, shift, win e uma tecla (letra, dígito, space, f1..f12)

[privacidade]
excluir = []                 # trechos que nunca saem da máquina (substring, sem diferenciar maiúsculas)
pastas_privadas = []         # pastas do vault que a busca nunca abre, relativas a ele (ex.: "notas/pessoal")

[busca]
maximo_de_notas = 40         # teto de notas lidas numa pergunta, para uma pergunta não varrer o vault

[git]
raiz = ""                    # pasta onde varrer repositórios; vazio = fonte desligada
profundidade = 3             # quantos níveis de pasta a varredura desce a partir da raiz
excluir = []                 # pastas ignoradas na varredura
autores = []                 # vazio = deduz do git config user.email de cada repositório

[linear]
api_key = ""                 # Personal API Key read-only; vazio = fonte desligada

[slack]
user_token = ""              # user token xoxp da app interna; vazio = fonte desligada
conversas_excluidas = []     # canais e DMs que o coletor nunca lê
so_com_mencao = []           # canais em que só entra o que cita você (ex.: code review)
bot_token = ""               # Bot User OAuth Token xoxb da mesma app: o atendente responde na DM com ele
app_token = ""               # App-Level Token xapp com connections:write: a conexão do Socket Mode

[cacada]
email = ""                   # o Gmail só da caçada (nunca o do trabalho): o que o Whis põe nos pacotes e, numa entrega futura, usa para criar contas
teto_semanal = 100           # a caçada usa o plano semanal do Claude até aqui (%) e para; fora da véspera da virada, para em 70
maximo_de_pacotes = 20       # quantas chances boas ganham resumo (ou pacote completo) por caçada, das melhores para as piores
pacote_completo = false      # true = cada chance boa ganha o pacote completo (proposta, respostas de formulário, como enviar, amostra) e cada tentar a sorte a mensagem pronta; false = só um resumo curto da vaga
cloudflare_token = ""        # API token do Cloudflare (Pages: Edit) para pôr as amostras no ar; vazio = amostra só no vault
cloudflare_conta = ""        # o Account ID do Cloudflare, ao lado do token
cloudflare_projeto = ""      # nome do projeto do Pages (vira <nome>.pages.dev); vazio = um nome neutro sorteado uma vez
enviar_sozinho = false       # true = na caçada, o Whis se candidata sozinho às vagas de tentar a sorte em formulário grátis e sem limite (Gupy, ATS); nunca freela, nunca LinkedIn
maximo_de_envios = 10        # quantas candidaturas sozinho por caçada, no máximo
senha_de_app = ""            # senha de app do Google do Gmail dedicado: o Whis lê a caixa (só leitura) para confirmar contas e listar respostas
remetente = ""               # quem assina os e-mails às agências e lojas (ex.: voce@seudominio.dev); vazio = o próprio Gmail
email_antigo = ""            # o Gmail de onde saíram os primeiros e-mails frios antes da caixa nova (ex.: voce@gmail.com): o 2º e o 3º e-mail dessas conversas saem dele, na mesma conversa, e a caixa dele é lida atrás das respostas
senha_de_app_antiga = ""     # senha de app do Google desse Gmail antigo (myaccount.google.com/apppasswords)
envios_frios_por_dia = 10    # no máximo tantos e-mails frios por dia saem de [cacada].email (a caixa nova), contando os reenvios; suba devagar até 30 depois de umas semanas sem devolução
posthog_chave = "phc_o3G5x8Q7jfzSe6G3fscQ5HQjKJDrH3X9fGg6EUFtZsxT"  # a chave pública do projeto velozo.dev no PostHog: o Whis registra lá email_enviado, email_respondido e email_voltou; vazio = não registra
resend_chave = ""            # chave da Resend com acesso de envio (Resend > API Keys), do domínio do remetente: os e-mails às agências e lojas saem por ela, assinados pelo domínio, e a cópia vai para os Enviados do Gmail; vazio = saem pelo Gmail, que mostra "por gmail.com"
nome = ""                    # seu nome completo, como vai nos cadastros e candidaturas
telefone = ""                # telefone com DDD, para os formulários que exigem
cidade = ""                  # cidade e estado, como vai nos formulários (ex.: Matão - SP)
cpf = ""                     # CPF, só para os formulários que exigem (Gupy); fica fora do vault
nascimento = ""              # data de nascimento (DD/MM/AAAA), só para os formulários que exigem
hora_do_lembrete = "20:00"   # quando a tarefa do lembrete olha se a semana do plano vira nas próximas 30 h (whis windows --hunt)
usuario_freelancer = ""      # seu usuário no Freelancer.com (freelancer.com/u/<usuário>): a caçada só traz projeto com alguma skill do seu perfil, porque a conta grátis não propõe nos outros; vazio = sem esse filtro
limite_minutos = 90          # fora da véspera (a semana do plano vira em mais de 24 h), a caçada para depois de tantos minutos; perto da virada, vai até o plano acabar; 0 = sem limite de tempo
limite_plano = 15            # fora da véspera, a caçada para depois de gastar tantos pontos do plano semanal; 0 = sem limite de plano
encaixe_minimo_tentar = 40   # tentar a sorte com encaixe abaixo disto (0 a 100) sai do relatório e das mensagens prontas
plataformas_sem_conta = ["peopleperhour", "workana"]  # sites em que sua conta não está ativa (em análise ou reprovada): a caçada não lê a fonte, não procura lá e tira o que vier deles; quando a conta valer, apague o nome daqui (ex.: [])
plataformas_pagas = ["freelancer.com.br", "freelancer.com"]  # sites em que responder a um projeto pede plano pago que você não assinou: a caçada, a vigia e o modo freelas tiram o que vier deles e não procuram lá; quando assinar, apague o nome daqui
nicho_freela = ""            # o nicho do freela (ex.: shopify): no hunt --freelas, chance boa só do nicho ou de e-commerce muito perto, o resto no máximo tentar a sorte, e as buscas do Freelancer.com fora dele saem; a proposta automática da 99Freelas só sai no projeto que cita o nicho ou na loja virtual sem outra plataforma; vazio = qualquer freela
piso_freela = 0              # o menor preço de freela que você aceita, em R$: a caçada nunca sugere abaixo disso (o resumo avisa quando o Claude sugeriu menos); 0 = sem piso
horarios_da_vigia_de_freelas = ["07:00", "08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00", "23:00"]  # quando a tarefa Whis-Vigia-Freelas (whis windows --vigia-freelas) olha a 99Freelas e o Freelancer.com, avisa por toast e no celular o projeto novo do nicho e faz as propostas automáticas
propostas_auto = "desligado"  # proposta automática na 99Freelas, a cada rodada da vigia: "ensaio" escreve, avisa no celular e não envia; "ligado" envia pelo Chrome da caçada (logado pelo whis navegador); "desligado" não escreve
propostas_auto_horas = 24    # só propõe em projeto que abriu para você há menos de tantas horas (sem assinatura, o projeto abre 24 h depois de publicado)
propostas_auto_max_propostas = 40  # só propõe sozinha em projeto com menos de tantas propostas (as fontes chamam 40 a 60 de sala lotada; quando o projeto abre para quem não assina, já tem de 40 a 140, e o que passa disto chega no celular para aprovar)
propostas_auto_por_dia = 3   # no máximo tantas propostas automáticas por dia (o ensaio conta)
propostas_auto_reserva = 3   # não propõe se a proposta deixaria menos de tantas conexões da 99Freelas
propostas_auto_exclusivos = false  # true = você assina a 99Freelas e pode propor no projeto exclusivo de assinante
discord = false              # true = a cada rodada da vigia, o Whis lê os canais de cacada/discord.md no Chrome da caçada (logado pelo whis navegador) e manda ao celular o rascunho de proposta ou de resposta; só posta no seu toque
discord_por_dia = 5          # no máximo tantos rascunhos do Discord por dia
x_por_dia = 5                # a cada 3 horas, das 9 às 22, a vigia de freelas lê as buscas de cacada/x/buscas.md no Chrome do jornal (whis x-login) e manda ao celular a resposta escrita para quem está contratando dev de Shopify no X, no máximo tantas por dia, para você mandar da conta da velozo.dev; 0 = desligada
x_botao_responder = true     # a resposta da vigia do X chega com o botão Responder no X, que abre o X com o texto pronto na conta ativa do app (deixe na velozo.dev); você ainda aperta Post; false = só o texto para copiar
x_ideias = true              # seg, qua e sex, na procura do dia, chega ao celular o post do X pronto, em inglês: primeiro os de cacada/x/posts-*.md sem a linha "Publicado:", depois uma ideia nova do Claude, gravada em cacada/x/ideias.md; com as chaves de [x], vem com o botão Publicar no X, senão você publica à mão; false = nenhuma
agencias_por_dia = 1         # quantos e-mails de apresentação para agências Shopify da sua lista chegam por dia no celular, escritos, com o botão Enviar e-mail (sai do seu Gmail, de [cacada].email com a senha de app); 0 = nenhum
lojas_por_dia = 1            # quantos e-mails para lojas Shopify lentas de cacada/lojas-shopify.md chegam por dia no celular, com o número medido e o botão Enviar e-mail; 0 = nenhum
lojas_redesenho_por_dia = 1  # quantos e-mails oferecendo redesenho chegam por dia no celular, para as lojas da mesma lista com o tema de fábrica (comparado com a demonstração oficial do tema), com o botão Enviar e-mail; 0 = nenhum
instagram_por_dia = 10       # quantos perfis do Instagram de marcas pequenas (500 a 5 mil seguidores, vendendo pelo direct ou sem loja) chegam por dia no celular, com a primeira mensagem escrita, para você mandar à mão do seu perfil; 0 = nenhum
horario_da_procura = "12:30"  # quando a tarefa Whis-Procura busca agências e lojas novas, se a fila de e-mails ficou curta (whis windows --procura)
propostas_auto_valor_hora = 40  # o valor da sua hora na proposta automática, em R$: o preço é as horas que o Claude estima vezes isto, dentro da faixa de orçamento do cliente e nunca abaixo da oferta mínima (fica um pouco abaixo do que se cobra na 99Freelas, porque você ainda não tem avaliação)
max_propostas_freelancer = 30  # a vigia de freelas e a caçada deixam de fora o projeto do Freelancer.com com tantas propostas ou mais (lá um projeto de Shopify passa de 50 em uma hora, e a conta grátis tem 6 lances por mês); 0 = sem limite
propostas_auto_para_aprovar = 5  # por dia, até tantas propostas que não saem sozinhas (cota cheia, propostas demais, reserva) chegam escritas no celular com o botão Enviar; 0 = nenhuma

[celular]
telegram_token = ""          # o token do seu bot do Telegram (o @BotFather dá); grave com whis celular, que pede o token sem mostrar na tela
telegram_chat = ""           # o chat em que o bot te avisa; whis celular descobre sozinho quando você manda uma mensagem ao bot

[x]
api_key = ""                 # API Key do app da velozo.dev no console.x.com (Keys and tokens); com as quatro preenchidas, o post do dia chega com o botão Publicar no X, pago por post (US$ 0,015, ou 0,20 com link)
api_key_secret = ""          # API Key Secret do mesmo app
access_token = ""            # Access Token da conta da velozo.dev, gerado depois de pôr o app em Read and Write
access_token_secret = ""     # Access Token Secret do mesmo token

[linkedin]
client_id = ""               # Client ID do seu app no LinkedIn Developers (produtos Share on LinkedIn e Sign In with LinkedIn using OpenID Connect)
client_secret = ""           # Client Secret do mesmo app; o login fica cifrado no cofre do Whis
exportacao = ""              # o .zip ou a pasta de "Baixar seus dados" do LinkedIn; vazio = sem análise do perfil
posts_por_vez = 3            # quantos rascunhos de post o comando linkedin escreve em linkedin/posts/

[obsidian]
vault = "245833c11ad43e6e"   # o ID do vault (Copy vault ID no seletor de vaults) ou o nome da pasta; é o que o botão "Abrir no Obsidian" usa
"""

if sys.platform == "darwin":
    # No Mac o atalho padrão é outro (Ctrl+Space troca o idioma do teclado) e o
    # vault do Obsidian é achado pelo nome da pasta.
    MODELO_DE_CONFIGURACAO = MODELO_DE_CONFIGURACAO.replace(
        'atalho = "ctrl+space"        #', f'atalho = "{ATALHO_PADRAO}"         #'
    ).replace('vault = "245833c11ad43e6e"   #', 'vault = ""                   #')


class ConfiguracaoInvalida(RuntimeError):
    """A configuração não existe, não é TOML válido ou não tem os caminhos."""


class Tipo(Enum):
    """Como uma chave se apresenta no painel e como é escrita no TOML."""

    TEXTO = "texto"
    SEGREDO = "segredo"
    CAMINHO = "caminho"
    INTEIRO = "inteiro"
    HORA = "hora"
    LISTA = "lista"
    HORARIOS = "horarios"
    BOOLEANO = "booleano"


@dataclass(frozen=True)
class Chave:
    """Uma chave do `config.toml`: onde mora no arquivo e em `Configuracao`."""

    secao: str
    nome: str
    atributo: str
    tipo: Tipo

    def caminho(self) -> str:
        return f"[{self.secao}].{self.nome}" if self.secao else self.nome


#: Toda chave do modelo, na ordem do arquivo. É o contrato entre o modelo, o
#: escritor e o painel: uma chave nova entra aqui e no modelo, e o teste
#: confere que os dois batem.
CHAVES: tuple[Chave, ...] = (
    Chave("", "vault", "vault", Tipo.CAMINHO),
    Chave("claude", "modelo", "claude_modelo", Tipo.TEXTO),
    Chave("claude", "cli", "claude_cli", Tipo.TEXTO),
    Chave("diario", "inicio_do_dia", "diario_inicio_do_dia", Tipo.HORA),
    Chave("diario", "hora_da_tarefa", "diario_hora_da_tarefa", Tipo.HORA),
    Chave("daily", "ativa", "daily_ativa", Tipo.BOOLEANO),
    Chave("daily", "horarios", "daily_horarios", Tipo.HORARIOS),
    Chave("noticias", "ativa", "noticias_ativa", Tipo.BOOLEANO),
    Chave("noticias", "horario", "noticias_horario", Tipo.HORA),
    Chave("granola", "horario", "granola_horario", Tipo.HORA),
    Chave("palheta", "atalho", "palheta_atalho", Tipo.TEXTO),
    Chave("privacidade", "excluir", "privacidade_excluir", Tipo.LISTA),
    Chave("privacidade", "pastas_privadas", "privacidade_pastas_privadas", Tipo.LISTA),
    Chave("busca", "maximo_de_notas", "busca_maximo_de_notas", Tipo.INTEIRO),
    Chave("git", "raiz", "git_raiz", Tipo.CAMINHO),
    Chave("git", "profundidade", "git_profundidade", Tipo.INTEIRO),
    Chave("git", "excluir", "git_excluir", Tipo.LISTA),
    Chave("git", "autores", "git_autores", Tipo.LISTA),
    Chave("linear", "api_key", "linear_api_key", Tipo.SEGREDO),
    Chave("slack", "user_token", "slack_user_token", Tipo.SEGREDO),
    Chave("slack", "conversas_excluidas", "slack_conversas_excluidas", Tipo.LISTA),
    Chave("slack", "so_com_mencao", "slack_so_com_mencao", Tipo.LISTA),
    Chave("slack", "bot_token", "slack_bot_token", Tipo.SEGREDO),
    Chave("slack", "app_token", "slack_app_token", Tipo.SEGREDO),
    Chave("cacada", "email", "cacada_email", Tipo.TEXTO),
    Chave("cacada", "teto_semanal", "cacada_teto_semanal", Tipo.INTEIRO),
    Chave("cacada", "maximo_de_pacotes", "cacada_maximo_de_pacotes", Tipo.INTEIRO),
    Chave("cacada", "pacote_completo", "cacada_pacote_completo", Tipo.BOOLEANO),
    Chave("cacada", "cloudflare_token", "cacada_cloudflare_token", Tipo.SEGREDO),
    Chave("cacada", "cloudflare_conta", "cacada_cloudflare_conta", Tipo.TEXTO),
    Chave("cacada", "cloudflare_projeto", "cacada_cloudflare_projeto", Tipo.TEXTO),
    Chave("cacada", "enviar_sozinho", "cacada_enviar_sozinho", Tipo.BOOLEANO),
    Chave("cacada", "maximo_de_envios", "cacada_maximo_de_envios", Tipo.INTEIRO),
    Chave("cacada", "senha_de_app", "cacada_senha_de_app", Tipo.SEGREDO),
    Chave("cacada", "remetente", "cacada_remetente", Tipo.TEXTO),
    Chave("cacada", "resend_chave", "cacada_resend_chave", Tipo.SEGREDO),
    Chave("cacada", "email_antigo", "cacada_email_antigo", Tipo.TEXTO),
    Chave("cacada", "senha_de_app_antiga", "cacada_senha_de_app_antiga", Tipo.SEGREDO),
    Chave("cacada", "envios_frios_por_dia", "cacada_envios_frios_por_dia", Tipo.INTEIRO),
    Chave("cacada", "posthog_chave", "cacada_posthog_chave", Tipo.TEXTO),
    Chave("cacada", "nome", "cacada_nome", Tipo.TEXTO),
    Chave("cacada", "telefone", "cacada_telefone", Tipo.TEXTO),
    Chave("cacada", "cidade", "cacada_cidade", Tipo.TEXTO),
    Chave("cacada", "cpf", "cacada_cpf", Tipo.SEGREDO),
    Chave("cacada", "nascimento", "cacada_nascimento", Tipo.TEXTO),
    Chave("cacada", "hora_do_lembrete", "cacada_hora_do_lembrete", Tipo.HORA),
    Chave("cacada", "usuario_freelancer", "cacada_usuario_freelancer", Tipo.TEXTO),
    Chave("cacada", "limite_minutos", "cacada_limite_minutos", Tipo.INTEIRO),
    Chave("cacada", "limite_plano", "cacada_limite_plano", Tipo.INTEIRO),
    Chave("cacada", "encaixe_minimo_tentar", "cacada_encaixe_minimo_tentar", Tipo.INTEIRO),
    Chave("cacada", "plataformas_sem_conta", "cacada_plataformas_sem_conta", Tipo.LISTA),
    Chave("cacada", "plataformas_pagas", "cacada_plataformas_pagas", Tipo.LISTA),
    Chave("cacada", "nicho_freela", "cacada_nicho_freela", Tipo.TEXTO),
    Chave("cacada", "piso_freela", "cacada_piso_freela", Tipo.INTEIRO),
    Chave("cacada", "horarios_da_vigia_de_freelas", "cacada_horarios_da_vigia_de_freelas", Tipo.LISTA),
    Chave("cacada", "propostas_auto", "cacada_propostas_auto", Tipo.TEXTO),
    Chave("cacada", "propostas_auto_horas", "cacada_propostas_auto_horas", Tipo.INTEIRO),
    Chave("cacada", "propostas_auto_max_propostas", "cacada_propostas_auto_max_propostas", Tipo.INTEIRO),
    Chave("cacada", "propostas_auto_por_dia", "cacada_propostas_auto_por_dia", Tipo.INTEIRO),
    Chave("cacada", "propostas_auto_reserva", "cacada_propostas_auto_reserva", Tipo.INTEIRO),
    Chave("cacada", "propostas_auto_exclusivos", "cacada_propostas_auto_exclusivos", Tipo.BOOLEANO),
    Chave("cacada", "discord", "cacada_discord", Tipo.BOOLEANO),
    Chave("cacada", "discord_por_dia", "cacada_discord_por_dia", Tipo.INTEIRO),
    Chave("cacada", "x_por_dia", "cacada_x_por_dia", Tipo.INTEIRO),
    Chave("cacada", "x_botao_responder", "cacada_x_botao_responder", Tipo.BOOLEANO),
    Chave("cacada", "x_ideias", "cacada_x_ideias", Tipo.BOOLEANO),
    Chave("cacada", "propostas_auto_para_aprovar", "cacada_propostas_auto_para_aprovar", Tipo.INTEIRO),
    Chave("cacada", "propostas_auto_valor_hora", "cacada_propostas_auto_valor_hora", Tipo.INTEIRO),
    Chave("cacada", "agencias_por_dia", "cacada_agencias_por_dia", Tipo.INTEIRO),
    Chave("cacada", "lojas_por_dia", "cacada_lojas_por_dia", Tipo.INTEIRO),
    Chave("cacada", "lojas_redesenho_por_dia", "cacada_lojas_redesenho_por_dia", Tipo.INTEIRO),
    Chave("cacada", "instagram_por_dia", "cacada_instagram_por_dia", Tipo.INTEIRO),
    Chave("cacada", "horario_da_procura", "cacada_horario_da_procura", Tipo.HORA),
    Chave("cacada", "max_propostas_freelancer", "cacada_max_propostas_freelancer", Tipo.INTEIRO),
    Chave("celular", "telegram_token", "celular_telegram_token", Tipo.SEGREDO),
    Chave("celular", "telegram_chat", "celular_telegram_chat", Tipo.TEXTO),
    Chave("x", "api_key", "x_api_key", Tipo.SEGREDO),
    Chave("x", "api_key_secret", "x_api_key_secret", Tipo.SEGREDO),
    Chave("x", "access_token", "x_access_token", Tipo.SEGREDO),
    Chave("x", "access_token_secret", "x_access_token_secret", Tipo.SEGREDO),
    Chave("linkedin", "client_id", "linkedin_client_id", Tipo.TEXTO),
    Chave("linkedin", "client_secret", "linkedin_client_secret", Tipo.SEGREDO),
    Chave("linkedin", "exportacao", "linkedin_exportacao", Tipo.CAMINHO),
    Chave("linkedin", "posts_por_vez", "linkedin_posts_por_vez", Tipo.INTEIRO),
    Chave("obsidian", "vault", "obsidian_vault", Tipo.TEXTO),
)


@dataclass(frozen=True)
class Configuracao:
    """Os caminhos e opções da máquina do usuário.

    `vault` é obrigatório; o resto tem padrão. Nenhum valor
    é derivado de hardware desta máquina — quem preenche é o usuário.

    `claude_modelo` vazio significa "o modelo padrão da conta do usuário": o
    Whis roda contra a assinatura dele, e o CLI já escolhe um modelo que
    aquela conta pode usar. Preencher é para quem quer fixar um id.

    `claude_cli` vazio significa "achar o `claude.exe` sozinho"; preencher é
    para quando a tarefa agendada não enxerga o CLI pelo `PATH`.

    As chaves das fontes passivas seguem a mesma regra: **vazia é fonte
    desligada**. É o que permite o diário nascer só com git e Linear enquanto o
    Slack espera aprovação do admin.

    `daily_horarios` é a hora em que a daily deve nascer em cada dia da semana,
    como pares `(dia, "HH:MM")` na ordem segunda→domingo. Dia ausente é dia sem
    daily; tabela vazia é nada agendado — a tarefa do Windows nasce daqui.

    `daily_ativa` falso desliga a daily: a tarefa `Whis-Daily` continua no
    Agendador, mas sai sem publicar nada, e a palheta esconde `daily`. Desligar
    pelo painel vale na hora, sem mexer no Windows. `whis daily` no terminal
    não olha isto — é o jeito de gerar uma com ela desligada.

    `noticias_ativa` e `noticias_horario` são o mesmo par para o jornal: a
    tarefa `Whis-Noticias` roda todo dia em `noticias_horario` e sai sem gerar
    quando `noticias_ativa` é falso. Fontes, nomes e time não moram aqui, e sim
    em `noticias/fontes.md` no vault.

    `granola_horario` é quando a tarefa `Whis-Reunioes` puxa as reuniões da
    Granola; o login dela não mora aqui, e sim em `granola.json`, ao lado deste
    arquivo, cifrado para o usuário do Windows.

    `obsidian_vault` é como o Obsidian chama o vault na URI `obsidian://` — o
    ID (o que *Copy vault ID* copia) ou o nome da pasta. Vazio usa o nome da
    pasta de `vault`, que é o que o Obsidian usa quando o vault foi aberto
    pela pasta.

    `cacada_*` é a caçada de vagas e freelas: o e-mail dedicado (nunca o do
    trabalho), o teto do plano semanal em que ela para, quantos pacotes ela
    prepara, se o pacote é o completo ou só o resumo curto (o padrão), e as chaves opcionais do Cloudflare Pages para pôr as amostras
    no ar. Os critérios e as fontes não moram aqui, e sim em
    `cacada/criterios.md` no vault.

    `palheta_atalho` é a combinação de teclas que abre a palheta, já validada
    (`atalho_global.analisar_atalho` a entende); o padrão é `ctrl+space`.
    """

    vault: Path
    claude_modelo: str = ""
    claude_cli: str = ""
    diario_inicio_do_dia: str = INICIO_DO_DIA_PADRAO
    diario_hora_da_tarefa: str = "19:00"
    daily_ativa: bool = True
    daily_horarios: tuple[tuple[str, str], ...] = ()
    noticias_ativa: bool = True
    noticias_horario: str = "08:20"
    granola_horario: str = "19:00"
    privacidade_excluir: tuple[str, ...] = ()
    privacidade_pastas_privadas: tuple[str, ...] = ()
    busca_maximo_de_notas: int = 40
    git_raiz: Path | None = None
    git_profundidade: int = 3
    git_excluir: tuple[str, ...] = ()
    git_autores: tuple[str, ...] = ()
    linear_api_key: str = ""
    slack_user_token: str = ""
    slack_conversas_excluidas: tuple[str, ...] = ()
    slack_so_com_mencao: tuple[str, ...] = ()
    slack_bot_token: str = ""
    slack_app_token: str = ""
    obsidian_vault: str = ""
    palheta_atalho: str = ATALHO_PADRAO
    cacada_email: str = ""
    cacada_teto_semanal: int = 100
    cacada_maximo_de_pacotes: int = 20
    cacada_pacote_completo: bool = False
    cacada_cloudflare_token: str = ""
    cacada_cloudflare_conta: str = ""
    cacada_cloudflare_projeto: str = ""
    cacada_enviar_sozinho: bool = False
    cacada_maximo_de_envios: int = 10
    cacada_senha_de_app: str = ""
    cacada_remetente: str = ""
    cacada_resend_chave: str = ""
    cacada_email_antigo: str = ""
    cacada_senha_de_app_antiga: str = ""
    cacada_envios_frios_por_dia: int = 10
    cacada_posthog_chave: str = POSTHOG_CHAVE
    cacada_nome: str = ""
    cacada_telefone: str = ""
    cacada_cidade: str = ""
    cacada_cpf: str = ""
    cacada_nascimento: str = ""
    cacada_hora_do_lembrete: str = "20:00"
    cacada_usuario_freelancer: str = ""
    cacada_limite_minutos: int = 90
    cacada_limite_plano: int = 15
    cacada_encaixe_minimo_tentar: int = 40
    cacada_plataformas_sem_conta: tuple[str, ...] = PLATAFORMAS_SEM_CONTA
    cacada_plataformas_pagas: tuple[str, ...] = PLATAFORMAS_PAGAS
    cacada_nicho_freela: str = ""
    cacada_piso_freela: int = 0
    cacada_horarios_da_vigia_de_freelas: tuple[str, ...] = HORARIOS_DA_VIGIA_DE_FREELAS
    cacada_propostas_auto: str = "desligado"
    cacada_propostas_auto_horas: int = 24
    cacada_propostas_auto_max_propostas: int = 40
    cacada_propostas_auto_por_dia: int = 3
    cacada_propostas_auto_reserva: int = 3
    cacada_propostas_auto_exclusivos: bool = False
    cacada_discord: bool = False
    cacada_discord_por_dia: int = 5
    cacada_x_por_dia: int = 5
    cacada_x_botao_responder: bool = True
    cacada_x_ideias: bool = True
    cacada_propostas_auto_para_aprovar: int = 5
    cacada_propostas_auto_valor_hora: int = 40
    cacada_agencias_por_dia: int = 1
    cacada_lojas_por_dia: int = 1
    cacada_lojas_redesenho_por_dia: int = 1
    cacada_instagram_por_dia: int = 10
    cacada_horario_da_procura: str = "12:30"
    cacada_max_propostas_freelancer: int = 30
    celular_telegram_token: str = ""
    celular_telegram_chat: str = ""
    x_api_key: str = ""
    x_api_key_secret: str = ""
    x_access_token: str = ""
    x_access_token_secret: str = ""
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_exportacao: Path | None = None
    linkedin_posts_por_vez: int = 3
    origem: Path | None = None


def caminho_padrao() -> Path:
    """Onde a configuração mora quando `WHIS_CONFIG` não está setada."""
    from .plataforma import pasta_de_config

    return pasta_de_config() / "whis" / NOME_DO_ARQUIVO


def caminho_da_configuracao(caminho: Path | str | None = None) -> Path:
    """Resolve o caminho efetivo: argumento > variável de ambiente > padrão."""
    if caminho is not None:
        return Path(caminho)
    do_ambiente = os.environ.get(VARIAVEL_DE_AMBIENTE)
    if do_ambiente:
        return Path(do_ambiente)
    return caminho_padrao()


def carregar(caminho: Path | str | None = None) -> Configuracao:
    """Lê a configuração do disco e valida o mínimo.

    Levanta `ConfiguracaoInvalida` com uma mensagem que diz qual arquivo o
    usuário precisa criar ou corrigir.
    """
    arquivo = caminho_da_configuracao(caminho)
    if not arquivo.is_file():
        raise ConfiguracaoInvalida(
            f"Configuração não encontrada em {arquivo}. "
            f"Crie o arquivo com a chave 'vault', ou aponte "
            f"{VARIAVEL_DE_AMBIENTE} para outro caminho."
        )
    return carregar_texto(arquivo.read_text(encoding="utf-8"), origem=arquivo)


def carregar_texto(texto: str, *, origem: Path) -> Configuracao:
    """Interpreta e valida o TOML já lido; `origem` é o arquivo que as mensagens citam.

    É o que o painel usa para validar o que o usuário digitou **antes** de
    escrever, com as mesmas mensagens que o arquivo daria.
    """
    arquivo = Path(origem)
    try:
        dados = tomllib.loads(texto)
    except tomllib.TOMLDecodeError as erro:
        raise ConfiguracaoInvalida(
            f"Configuração inválida em {arquivo}: {erro}{_dica_de_barra_invertida(texto)}"
        ) from erro

    if not dados.get("vault"):
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo} sem a chave obrigatória: vault."
        )

    claude = dados.get("claude") or {}
    diario = dados.get("diario") or {}
    daily = dados.get("daily") or {}
    noticias = dados.get("noticias") or {}
    granola = dados.get("granola") or {}
    privacidade = dados.get("privacidade") or {}
    busca = dados.get("busca") or {}
    git = dados.get("git") or {}
    linear = dados.get("linear") or {}
    slack = dados.get("slack") or {}
    obsidian = dados.get("obsidian") or {}
    palheta = dados.get("palheta") or {}
    cacada = dados.get("cacada") or {}
    linkedin = dados.get("linkedin") or {}
    celular = dados.get("celular") or {}
    x = dados.get("x") or {}
    exportacao = str(linkedin.get("exportacao", "")).strip()
    raiz_do_git = str(git.get("raiz", "")).strip()
    return Configuracao(
        vault=Path(dados["vault"]),
        claude_modelo=str(claude.get("modelo", "")),
        claude_cli=str(claude.get("cli", "")).strip(),
        diario_inicio_do_dia=_hora(
            diario.get("inicio_do_dia", INICIO_DO_DIA_PADRAO),
            "[diario].inicio_do_dia",
            arquivo,
        ),
        diario_hora_da_tarefa=_hora(
            diario.get("hora_da_tarefa", "19:00"), "[diario].hora_da_tarefa", arquivo
        ),
        daily_ativa=_booleano(daily.get("ativa", True), "[daily].ativa", arquivo),
        daily_horarios=_horarios_da_daily(daily.get("horarios"), arquivo),
        noticias_ativa=_booleano(noticias.get("ativa", True), "[noticias].ativa", arquivo),
        noticias_horario=_hora(
            noticias.get("horario", "08:20"), "[noticias].horario", arquivo
        ),
        granola_horario=_hora(granola.get("horario", "19:00"), "[granola].horario", arquivo),
        privacidade_excluir=_textos(privacidade.get("excluir")),
        privacidade_pastas_privadas=_pastas_privadas(
            privacidade.get("pastas_privadas"), arquivo
        ),
        busca_maximo_de_notas=_maximo_de_notas(busca.get("maximo_de_notas", 40), arquivo),
        git_raiz=Path(raiz_do_git) if raiz_do_git else None,
        git_profundidade=_profundidade(git.get("profundidade", 3), arquivo),
        git_excluir=_textos(git.get("excluir")),
        git_autores=_textos(git.get("autores")),
        linear_api_key=str(linear.get("api_key", "")),
        slack_user_token=str(slack.get("user_token", "")),
        slack_conversas_excluidas=_textos(slack.get("conversas_excluidas")),
        slack_so_com_mencao=_textos(slack.get("so_com_mencao")),
        slack_bot_token=_token_do_slack(slack.get("bot_token", ""), "bot_token", "xoxb-", arquivo),
        slack_app_token=_token_do_slack(slack.get("app_token", ""), "app_token", "xapp-", arquivo),
        obsidian_vault=str(obsidian.get("vault", "")).strip(),
        palheta_atalho=_atalho(palheta.get("atalho", ATALHO_PADRAO), arquivo),
        cacada_email=str(cacada.get("email", "")).strip(),
        cacada_teto_semanal=_inteiro_entre(cacada.get("teto_semanal", 100), "[cacada].teto_semanal", 1, 100, arquivo),
        cacada_maximo_de_pacotes=_inteiro_entre(
            cacada.get("maximo_de_pacotes", 20), "[cacada].maximo_de_pacotes", 0, 200, arquivo
        ),
        cacada_cloudflare_token=str(cacada.get("cloudflare_token", "")).strip(),
        cacada_cloudflare_conta=str(cacada.get("cloudflare_conta", "")).strip(),
        cacada_cloudflare_projeto=_projeto_do_pages(cacada.get("cloudflare_projeto", ""), arquivo),
        cacada_pacote_completo=_booleano(cacada.get("pacote_completo", False), "[cacada].pacote_completo", arquivo),
        cacada_enviar_sozinho=_booleano(cacada.get("enviar_sozinho", False), "[cacada].enviar_sozinho", arquivo),
        cacada_maximo_de_envios=_inteiro_entre(cacada.get("maximo_de_envios", 10), "[cacada].maximo_de_envios", 0, 100, arquivo),
        cacada_senha_de_app=str(cacada.get("senha_de_app", "")).replace(" ", "").strip(),
        cacada_remetente=str(cacada.get("remetente", "")).strip(),
        cacada_resend_chave=str(cacada.get("resend_chave", "")).strip(),
        cacada_email_antigo=str(cacada.get("email_antigo", "")).strip(),
        cacada_senha_de_app_antiga=str(cacada.get("senha_de_app_antiga", "")).replace(" ", "").strip(),
        cacada_envios_frios_por_dia=_inteiro_entre(
            cacada.get("envios_frios_por_dia", 10), "[cacada].envios_frios_por_dia", 0, 50, arquivo
        ),
        cacada_posthog_chave=str(cacada.get("posthog_chave", POSTHOG_CHAVE)).strip(),
        cacada_nome=str(cacada.get("nome", "")).strip(),
        cacada_telefone=str(cacada.get("telefone", "")).strip(),
        cacada_cidade=str(cacada.get("cidade", "")).strip(),
        cacada_cpf=str(cacada.get("cpf", "")).strip(),
        cacada_nascimento=str(cacada.get("nascimento", "")).strip(),
        cacada_hora_do_lembrete=_hora(cacada.get("hora_do_lembrete", "20:00"), "[cacada].hora_do_lembrete", arquivo),
        cacada_usuario_freelancer=str(cacada.get("usuario_freelancer", "")).strip().strip("/").rsplit("/", 1)[-1],
        cacada_limite_minutos=_inteiro_entre(cacada.get("limite_minutos", 90), "[cacada].limite_minutos", 0, 1440, arquivo),
        cacada_limite_plano=_inteiro_entre(cacada.get("limite_plano", 15), "[cacada].limite_plano", 0, 100, arquivo),
        cacada_encaixe_minimo_tentar=_inteiro_entre(
            cacada.get("encaixe_minimo_tentar", 40), "[cacada].encaixe_minimo_tentar", 0, 100, arquivo
        ),
        cacada_plataformas_sem_conta=tuple(
            p.strip().casefold() for p in _textos(cacada.get("plataformas_sem_conta", PLATAFORMAS_SEM_CONTA)) if p.strip()
        ),
        cacada_plataformas_pagas=tuple(
            p.strip().casefold() for p in _textos(cacada.get("plataformas_pagas", PLATAFORMAS_PAGAS)) if p.strip()
        ),
        cacada_nicho_freela=str(cacada.get("nicho_freela", "")).strip().casefold(),
        cacada_piso_freela=_inteiro_entre(cacada.get("piso_freela", 0), "[cacada].piso_freela", 0, 100_000, arquivo),
        cacada_horarios_da_vigia_de_freelas=tuple(
            _hora(hora, "[cacada].horarios_da_vigia_de_freelas", arquivo)
            for hora in _textos(cacada.get("horarios_da_vigia_de_freelas", HORARIOS_DA_VIGIA_DE_FREELAS))
            if str(hora).strip()
        ),
        cacada_propostas_auto=_modo_das_propostas_auto(cacada.get("propostas_auto", "desligado"), arquivo),
        cacada_propostas_auto_horas=_inteiro_entre(
            cacada.get("propostas_auto_horas", 24), "[cacada].propostas_auto_horas", 1, 720, arquivo
        ),
        cacada_propostas_auto_max_propostas=_inteiro_entre(
            cacada.get("propostas_auto_max_propostas", 40), "[cacada].propostas_auto_max_propostas", 1, 1000, arquivo
        ),
        cacada_propostas_auto_por_dia=_inteiro_entre(
            cacada.get("propostas_auto_por_dia", 3), "[cacada].propostas_auto_por_dia", 0, 50, arquivo
        ),
        cacada_propostas_auto_reserva=_inteiro_entre(
            cacada.get("propostas_auto_reserva", 3), "[cacada].propostas_auto_reserva", 0, 1000, arquivo
        ),
        cacada_propostas_auto_exclusivos=_booleano(
            cacada.get("propostas_auto_exclusivos", False), "[cacada].propostas_auto_exclusivos", arquivo
        ),
        cacada_discord=_booleano(cacada.get("discord", False), "[cacada].discord", arquivo),
        cacada_discord_por_dia=_inteiro_entre(cacada.get("discord_por_dia", 5), "[cacada].discord_por_dia", 0, 50, arquivo),
        cacada_x_por_dia=_inteiro_entre(cacada.get("x_por_dia", 5), "[cacada].x_por_dia", 0, 20, arquivo),
        cacada_x_botao_responder=_booleano(cacada.get("x_botao_responder", True), "[cacada].x_botao_responder", arquivo),
        cacada_x_ideias=_booleano(cacada.get("x_ideias", True), "[cacada].x_ideias", arquivo),
        cacada_propostas_auto_para_aprovar=_inteiro_entre(
            cacada.get("propostas_auto_para_aprovar", 5), "[cacada].propostas_auto_para_aprovar", 0, 50, arquivo
        ),
        cacada_propostas_auto_valor_hora=_inteiro_entre(
            cacada.get("propostas_auto_valor_hora", 40), "[cacada].propostas_auto_valor_hora", 1, 1000, arquivo
        ),
        cacada_agencias_por_dia=_inteiro_entre(cacada.get("agencias_por_dia", 1), "[cacada].agencias_por_dia", 0, 10, arquivo),
        cacada_lojas_por_dia=_inteiro_entre(cacada.get("lojas_por_dia", 1), "[cacada].lojas_por_dia", 0, 10, arquivo),
        cacada_lojas_redesenho_por_dia=_inteiro_entre(
            cacada.get("lojas_redesenho_por_dia", 1), "[cacada].lojas_redesenho_por_dia", 0, 10, arquivo
        ),
        cacada_instagram_por_dia=_inteiro_entre(cacada.get("instagram_por_dia", 10), "[cacada].instagram_por_dia", 0, 20, arquivo),
        cacada_horario_da_procura=_hora(cacada.get("horario_da_procura", "12:30"), "[cacada].horario_da_procura", arquivo),
        cacada_max_propostas_freelancer=_inteiro_entre(
            cacada.get("max_propostas_freelancer", 30), "[cacada].max_propostas_freelancer", 0, 1000, arquivo
        ),
        celular_telegram_token=str(celular.get("telegram_token", "")).strip(),
        celular_telegram_chat=str(celular.get("telegram_chat", "")).strip(),
        x_api_key=str(x.get("api_key", "")).strip(),
        x_api_key_secret=str(x.get("api_key_secret", "")).strip(),
        x_access_token=str(x.get("access_token", "")).strip(),
        x_access_token_secret=str(x.get("access_token_secret", "")).strip(),
        linkedin_client_id=str(linkedin.get("client_id", "")).strip(),
        linkedin_client_secret=str(linkedin.get("client_secret", "")).strip(),
        linkedin_exportacao=Path(exportacao) if exportacao else None,
        linkedin_posts_por_vez=_inteiro_entre(linkedin.get("posts_por_vez", 3), "[linkedin].posts_por_vez", 1, 10, arquivo),
        origem=arquivo,
    )


def _atalho(valor: object, arquivo: Path) -> str:
    """Valida `[palheta].atalho` antes de a bandeja tentar registrá-lo.

    Um atalho ilegível só se revelaria com a bandeja no ar e sem console; aqui
    ele vira erro com o formato aceito.
    """
    texto = str(valor).strip() or ATALHO_PADRAO
    try:
        return analisar_atalho(texto).texto
    except AtalhoInvalido as erro:
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[palheta].atalho' precisa ser modificadores e "
            f'uma tecla separados por "+" (por exemplo "ctrl+alt+space"), e veio {valor!r}: '
            f"{erro}"
        ) from None


def _hora(valor: object, chave: str, arquivo: Path) -> str:
    """Valida `HH:MM` em 24 horas — a janela do dia e a tarefa dependem disso."""
    texto = str(valor)
    try:
        analisar_hora(texto)
    except ValueError:
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '{chave}' precisa ser uma hora no formato "
            f"HH:MM em 24 horas (por exemplo \"04:00\"), e veio {valor!r}."
        ) from None
    return texto.strip()


def _modo_das_propostas_auto(valor: object, arquivo: Path) -> str:
    texto = str(valor).strip().casefold()
    if texto not in MODOS_DAS_PROPOSTAS_AUTO:
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[cacada].propostas_auto' precisa ser "
            f'{", ".join(repr(modo) for modo in MODOS_DAS_PROPOSTAS_AUTO)}, e veio {valor!r}.'
        )
    return texto


def _booleano(valor: object, chave: str, arquivo: Path) -> bool:
    if not isinstance(valor, bool):
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '{chave}' precisa ser true ou false, sem aspas, "
            f"e veio {valor!r}."
        )
    return valor


def _horarios_da_daily(valor: object, arquivo: Path) -> tuple[tuple[str, str], ...]:
    """A tabela `[daily].horarios` como pares ordenados pela semana.

    Uma chave desconhecida é erro, e não dia ignorado: `terça` com acento ou
    `seg` abreviado deixariam a daily sem nascer naquele dia em silêncio — o
    modo de falha que a tabela existe para evitar.
    """
    if valor is None:
        return ()
    if not isinstance(valor, dict):
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[daily].horarios' precisa ser uma tabela de "
            f'dia da semana para hora (por exemplo {{ segunda = "13:55" }}), e veio {valor!r}.'
        )
    desconhecidas = [chave for chave in valor if chave not in DIAS_DA_SEMANA]
    if desconhecidas:
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[daily].horarios' tem dia(s) que não existem: "
            f"{', '.join(repr(chave) for chave in desconhecidas)}. "
            f"Os aceitos são {', '.join(DIAS_DA_SEMANA)}."
        )
    return tuple(
        (dia, _hora(valor[dia], f"[daily].horarios.{dia}", arquivo))
        for dia in DIAS_DA_SEMANA
        if dia in valor
    )


def _textos(valor: object) -> tuple[str, ...]:
    """Uma lista do TOML como tupla — `Configuracao` é frozen."""
    if valor is None:
        return ()
    if isinstance(valor, str):
        return (valor,)
    return tuple(str(item) for item in valor)


def _pastas_privadas(valor: object, arquivo: Path) -> tuple[str, ...]:
    """Valida e normaliza as pastas que a busca nunca abre.

    Cada uma precisa apontar para **dentro** do vault: caminho absoluto ou com
    `..` não protege pasta nenhuma, e uma pasta privada que não protege nada é
    pior do que nenhuma — o usuário acharia que está coberto. Barras invertidas
    e a barra final são aceitas, porque é assim que o Explorer mostra o caminho.
    """
    pastas: list[str] = []
    for item in _textos(valor):
        texto = item.strip().replace("\\", "/")
        partes = [parte for parte in texto.split("/") if parte and parte != "."]
        if not partes or _absoluto(texto) or ".." in partes:
            raise ConfiguracaoInvalida(
                f"Configuração em {arquivo}: cada item de '[privacidade].pastas_privadas' "
                f"precisa ser uma pasta dentro do vault, relativa a ele (por exemplo "
                f'"notas/pessoal"), e veio {item!r}.'
            )
        pastas.append("/".join(partes))
    return tuple(pastas)


_NOMES_DOS_TOKENS = {
    "xoxb-": "o Bot User OAuth Token",
    "xapp-": "o App-Level Token",
    "xoxp-": "o User OAuth Token, que vai em [slack].user_token",
}


def _token_do_slack(valor: object, chave: str, prefixo: str, arquivo: Path) -> str:
    """Um token do atendente, conferido pelo prefixo.

    A página da app mostra três tokens parecidos, e colar o errado só se
    revelaria na primeira mensagem, com o atendente já no ar sem console.
    O prefixo diz qual é qual, e a mensagem diz onde cada um vai.
    """
    token = str(valor or "").strip()
    if not token or token.startswith(prefixo):
        return token
    veio = next(
        (nome for inicio, nome in _NOMES_DOS_TOKENS.items() if token.startswith(inicio)),
        "um token de outro tipo",
    )
    raise ConfiguracaoInvalida(
        f"Configuração em {arquivo}: '[slack].{chave}' precisa ser "
        f"{_NOMES_DOS_TOKENS[prefixo]} (começa com {prefixo}), e veio {veio}."
    )


def _absoluto(texto: str) -> bool:
    """`/fora` também é absoluto: no Windows `Path` só reconhece com a letra do drive."""
    return Path(texto).is_absolute() or texto.startswith("/")


def _inteiro_entre(valor: object, chave: str, minimo: int, maximo: int, arquivo: Path) -> int:
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '{chave}' precisa ser um número inteiro, e veio {valor!r}."
        ) from None
    if not minimo <= numero <= maximo:
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '{chave}' precisa estar entre {minimo} e {maximo}, e veio {numero}."
        )
    return numero


def _projeto_do_pages(valor: object, arquivo: Path) -> str:
    """O nome do projeto do Cloudflare Pages vira subdomínio: só minúsculas, dígitos e hífen."""
    nome = str(valor or "").strip()
    if nome and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,56}[a-z0-9]", nome):
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[cacada].cloudflare_projeto' vira o endereço <nome>.pages.dev e "
            f"precisa ter só letras minúsculas, dígitos e hífen (por exemplo \"conceitos-web\"), e veio {nome!r}."
        )
    return nome


def _maximo_de_notas(valor: object, arquivo: Path) -> int:
    try:
        maximo = int(valor)
    except (TypeError, ValueError):
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[busca].maximo_de_notas' precisa ser um número "
            f"inteiro de notas, e veio {valor!r}."
        ) from None
    if maximo < 1:
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[busca].maximo_de_notas' precisa ser pelo menos 1 "
            f"(veio {maximo}) — abaixo disso o agente não conseguiria ler nota nenhuma."
        )
    return maximo


def _profundidade(valor: object, arquivo: Path) -> int:
    try:
        niveis = int(valor)
    except (TypeError, ValueError):
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[git].profundidade' precisa ser um número "
            f"inteiro de níveis de pasta, e veio {valor!r}."
        ) from None
    if niveis < 1:
        raise ConfiguracaoInvalida(
            f"Configuração em {arquivo}: '[git].profundidade' precisa ser pelo menos 1 "
            f"(veio {niveis}) — abaixo disso a varredura não acharia repositório nenhum."
        )
    return niveis


# -- escrever ---------------------------------------------------------------

#: Uma linha `chave = valor  # comentário` do modelo.
_LINHA_DE_CHAVE = re.compile(r"^(?P<chave>\w+) = (?P<valor>.*?)(?P<comentario>\s+#.*)?$")
_LINHA_DE_SECAO = re.compile(r"^\[(?P<secao>\w+)\]\s*$")


def valores_de(configuracao: Configuracao) -> dict[Chave, object]:
    """O valor de cada chave como `escrever` o escreve: caminhos em texto com `/`."""
    valores: dict[Chave, object] = {}
    for chave in CHAVES:
        valor = getattr(configuracao, chave.atributo)
        if chave.tipo is Tipo.CAMINHO:
            valor = Path(valor).as_posix() if valor else ""
        valores[chave] = valor
    return valores


def gerar_toml(valores: Mapping[Chave, object]) -> str:
    """O `config.toml` inteiro, regenerado do modelo com os valores dados.

    O modelo é a fonte dos comentários: cada linha `chave = valor  # ajuda`
    ganha o valor novo e mantém a ajuda, alinhada como estava. Preço aceito e
    registrado na spec: um comentário escrito à mão no arquivo se perde — o
    backup guarda.
    """
    modelo = MODELO_DE_CONFIGURACAO.format(vault="")
    por_caminho = {(chave.secao, chave.nome): chave for chave in CHAVES}
    linhas: list[str] = []
    secao = ""
    for linha in modelo.splitlines():
        cabecalho = _LINHA_DE_SECAO.match(linha)
        if cabecalho is not None:
            secao = cabecalho["secao"]
            linhas.append(linha)
            continue
        campo = _LINHA_DE_CHAVE.match(linha)
        chave = por_caminho.get((secao, campo["chave"])) if campo else None
        if campo is None or chave is None or chave not in valores:
            linhas.append(linha)
            continue
        prefixo = f"{campo['chave']} = {_toml(valores[chave], chave.tipo)}"
        comentario = campo["comentario"] or ""
        if comentario:
            coluna = len(campo["chave"]) + 3 + len(campo["valor"]) + len(comentario) - len(comentario.lstrip())
            comentario = " " * max(2, coluna - len(prefixo)) + comentario.lstrip()
        linhas.append(prefixo + comentario)
    return "\n".join(linhas) + "\n"


def escrever(configuracao: Configuracao, destino: Path, *, agora: datetime | None = None) -> Path:
    """Grava a configuração em `destino`, com backup do que estava lá.

    Escreve primeiro num `.tmp` ao lado e o relê com `carregar`: se o que
    saiu não carrega, nada é trocado e o erro sobe. Só então o arquivo atual
    vira `config.toml.bak-YYYYMMDD-HHMMSS` e o novo toma o lugar. UTF-8 sem
    BOM e `\\n`, como `init` escreve. Nenhum valor vai ao log: oito deles são
    segredos.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".tmp")
    with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(gerar_toml(valores_de(configuracao)))
    try:
        carregar(temporario)
    except ConfiguracaoInvalida:
        temporario.unlink(missing_ok=True)
        raise

    if destino.exists():
        momento = agora if agora is not None else datetime.now()
        backup = destino.with_name(f"{destino.name}.bak-{momento:%Y%m%d-%H%M%S}")
        shutil.copy2(destino, backup)
        _log.info("Backup da configuração em %s", backup)
    os.replace(temporario, destino)
    _log.info("Configuração escrita em %s", destino)
    return destino


def _toml(valor: object, tipo: Tipo) -> str:
    if tipo is Tipo.HORARIOS:
        return _tabela_de_horarios(valor)
    if tipo is Tipo.BOOLEANO and isinstance(valor, bool):
        return "true" if valor else "false"
    if tipo is Tipo.LISTA:
        itens = [valor] if isinstance(valor, str) else list(valor or ())
        return "[" + ", ".join(_texto_toml(str(item)) for item in itens) + "]"
    if tipo is Tipo.INTEIRO and isinstance(valor, int) and not isinstance(valor, bool):
        return str(valor)
    if tipo is Tipo.CAMINHO and valor:
        return _texto_toml(Path(str(valor)).as_posix())
    return _texto_toml("" if valor is None else str(valor))


def _tabela_de_horarios(valor: object) -> str:
    pares: Sequence[tuple[str, str]]
    if isinstance(valor, Mapping):
        pares = [(str(dia), str(hora)) for dia, hora in valor.items()]
    else:
        pares = [(str(dia), str(hora)) for dia, hora in (valor or ())]
    if not pares:
        return "{}"
    return "{ " + ", ".join(f"{dia} = {_texto_toml(hora)}" for dia, hora in pares) + " }"


def _texto_toml(texto: str) -> str:
    """Uma string básica do TOML: aspas e barra invertida escapadas, controle em `\\uXXXX`."""
    partes: list[str] = []
    for caractere in texto:
        if caractere == "\\":
            partes.append("\\\\")
        elif caractere == '"':
            partes.append('\\"')
        elif caractere == "\n":
            partes.append("\\n")
        elif caractere == "\t":
            partes.append("\\t")
        elif caractere == "\r":
            partes.append("\\r")
        elif ord(caractere) < 0x20 or caractere == "\x7f":
            partes.append(f"\\u{ord(caractere):04X}")
        else:
            partes.append(caractere)
    return '"' + "".join(partes) + '"'


def _dica_de_barra_invertida(texto: str) -> str:
    r"""A explicação que falta quando o TOML reclama de um caminho do Windows.

    O erro nativo é `Invalid hex value` — porque `"C:\Users\..."` faz o TOML
    ler `\U` como escape Unicode. É o primeiro tropeço de quem copia o caminho
    do Explorer, e a mensagem crua não ensina nada. Só aparece quando o arquivo
    realmente tem uma barra invertida dentro de aspas.
    """
    suspeitas = [
        linha.strip()
        for linha in texto.splitlines()
        if "\\" in linha and '"' in linha and not linha.lstrip().startswith("#")
    ]
    if not suspeitas:
        return ""
    exemplo = suspeitas[0]
    return (
        "\n\nParece um caminho do Windows com barra invertida: "
        f"{exemplo}\n"
        "No TOML a barra invertida é escape. Use barras normais — "
        'vault = "C:/Users/voce/whis-vault" — ou dobre as barras.'
    )
