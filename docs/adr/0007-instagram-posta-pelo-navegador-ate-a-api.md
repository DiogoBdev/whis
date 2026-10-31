# Os posts da @velozo.dev saem pelo instagram.com num Chrome do Whis até a API da Meta entrar

> Registro de 2026-10-31.

Em 31/10 ele pediu que o Whis postasse os Reels e os carrosséis da @velozo.dev sempre no horário: 19h,
Reel de domingo a quinta e carrossel na sexta, um por dia. O calendário é o
`velozo-reels/pagina/calendario.json`. A tarefa `Whis-Instagram` roda `whis instagram-postar --agendada`
todo dia às 19:00 e publica o item do dia pelo instagram.com. O Chrome usa um perfil só do Whis
(`%LOCALAPPDATA%\whis\navegador-instagram`), em que ele entra uma vez com `whis instagram-login`, como no
X da ADR 0003. O primeiro precisava sair no domingo, 01/11, às 19h.

## Por quê

A rota oficial é a publicação de conteúdo da API do Instagram. Ela publica Reels e carrosséis, e o post não depende do
notebook ligado. Mas tudo o que ela pede é dele: a conta de desenvolvedor da Meta, o app, o token
com `instagram_business_content_publish` e um endereço público para cada arquivo. A Meta baixa a mídia por URL;
a subida direta (`upload_type=resumable`) é só para quem usa o Facebook Login for Business. A API também
só aceita imagem em JPEG, e os slides são PNG ([Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing)).
Nada disso ficaria pronto e provado até domingo. O navegador fica pronto no mesmo dia: o carrossel "Aqui é o
Gabriel" foi publicado à mão por esse fluxo em 31/10.

## Considered Options

- **API com o Instagram Login (sem Página do Facebook)**: a rota de destino. Fica para quando ele criar o
  app. A porta (`PortaDePublicacao`) é a mesma, então a troca é só de quem publica.
- **Agente do Claude dirigindo o navegador**: lento, gasta a assinatura e muda a cada post. O fluxo é
  fixo e cabe em Playwright puro.
- **Meta Business Suite agendando**: não está configurado (cai no login) e não lê o calendário.
- **Publicar à mão pela página de postagem**: o que ele quer parar de fazer.

## Consequências

- **Risco aceito por ele, na conta dele**: os termos do Instagram não liberam automação de conta pelo
  site. Um post por dia, sem curtir, seguir nem abrir direct, foi a medida que ele escolheu ("como a gente
  vai postar em um momento muito espaçado ... não vai aparecer que é um bot").
- **O notebook precisa estar ligado às 19h.** Com `-StartWhenAvailable`, ligar mais tarde no mesmo dia
  ainda posta o item. Item de um dia que passou não sai atrasado: vira um aviso no Telegram com o
  comando para postar à mão.
- **Uma vez só**: o estado (`cacada\instagram-posts.json`) vira `enviando` antes do clique em
  Compartilhar. Sem o "foi compartilhado" na tela, vira `incerto`, e o Whis nunca repete. Falha antes do
  clique (login vencido, arquivo que falta, tela que mudou) vira `falhou`, com um aviso só. A tarefa não
  tenta de novo; à mão, `whis instagram-postar --pasta <item>` tenta. O carrossel 03 publicado à mão está
  no código como já publicado.
- **Arquivos exatos**: o Reel sai do `-sem-voz.mp4` (decisão dele em 31/10). Os três cortes de podcast
  saem do `.mp4`, porque não têm outra versão. Sem o arquivo certo, nada sai: o Whis nunca cai no narrado.
- **Conferência antes do clique**: a conta logada tem que ser a @velozo.dev (o "Editar perfil" no
  perfil). A legenda na caixa tem que bater linha a linha com o `legenda.md`, e o rótulo de IA tem que
  estar desligado. `--ensaio` faz tudo até o Compartilhar e para; as fotos de cada passo ficam em
  `.scratch/instagram/execucoes/`.
- **Frágil como o X**: quando o Instagram muda a tela, o passo que quebrou vira o motivo do aviso, com a
  foto e o HTML do diálogo ao lado.
- **Janela de login esquecida aberta**: antes de abrir o perfil, o Whis fecha o Chrome que estiver nele.
- Fixar post não existe na web; continua no celular.
