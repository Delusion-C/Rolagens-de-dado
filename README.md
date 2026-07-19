# John Rolagens — Dice Overlay Bot

Bot de Discord que lê rolagens de dado e manda pra um overlay animado no OBS.

## Estrutura

- `bot.py` — o bot em si (Discord + servidor WebSocket)
- `overlay.html` — o visual que o OBS carrega (fica **local**, no seu PC, sempre)
- `editor.html` — estúdio pra criar novos visuais (uso manual, não é lido pelo bot)
- `.env` — token e ID do canal (**nunca sobe pro GitHub**, veja abaixo)

## 1. Subir esse código pro GitHub

Dentro dessa pasta, no terminal:

```
git init
git add .
git commit -m "Primeira versão do bot"
```

Crie um repositório vazio em https://github.com/new (pode ser privado), depois:

```
git remote add origin https://github.com/SEU_USUARIO/NOME_DO_REPO.git
git branch -M main
git push -u origin main
```

O `.gitignore` já está configurado pra **não** subir seu `.env` nem os arquivos
`characters.json`/`settings.json` (dados salvos localmente) — isso é
propositalmente para não expor seu token e não sujar o repositório com dados
que são recriados sozinhos quando o bot roda.

## 2. Hospedar o bot.py em algum lugar (fora do seu PC)

Escolha uma hospedagem que **não hiberna** (o bot precisa ficar conectado o
tempo todo, tanto no Discord quanto no WebSocket do overlay). Duas opções
razoáveis pra começar — teste a primeira, e se não servir, vá pra segunda:

**Opção A — Discloud** (brasileira, tem plano grátis pra bots)
- Conecta com seu GitHub, escolhe o repositório, configura as variáveis de
  ambiente (`DISCORD_TOKEN`, `CHANNEL_ID`) no painel deles, e sobe.

**Opção B — Railway** (pago, ~US$5/mês, mas é o mais "aperta o botão e funciona")
- Conecta com seu GitHub, escolhe o repositório, e no painel dele vá em
  **Variables** e adicione `DISCORD_TOKEN` com o valor real (não precisa do
  arquivo `.env` na nuvem — cada hospedagem tem seu próprio jeito de guardar
  isso com segurança).

Qualquer uma delas vai te dar, depois do deploy, um **endereço público**
parecido com `algumacoisa.up.railway.app` ou similar. Guarde esse endereço.

## Sobre o canal do Discord

Não precisa configurar nenhum canal antes de rodar. Por padrão, o bot lê e
responde em **qualquer canal onde ele tenha permissão de leitura/escrita**
(geralmente os canais abertos pro @everyone do servidor). Se quiser restringir
o bot a um canal só, use `!set channel #nome-do-canal` diretamente no Discord
— e `!set channel limpar` pra voltar ao comportamento padrão.

## Publicando pra outras pessoas usarem

Esse repositório é genérico — qualquer pessoa pode clonar, criar seu próprio
bot no [Discord Developer Portal](https://discord.com/developers/applications),
colocar o token dela no `.env` (ou nas variáveis de ambiente da hospedagem
escolhida) e ter sua própria instância rodando no servidor dela, sem precisar
mexer em nenhuma linha de código.

## 3. Apontar o overlay pra URL do bot na nuvem

Depois do deploy, o próprio bot já serve o `overlay.html` por URL — não
precisa mais usar "Local file" no OBS. Só dois ajustes, feitos uma única vez:

**a) Dentro do `overlay.html`**, troque a linha:

```js
const WS_URL = 'ws://localhost:8765';
```

pelo endereço público que a hospedagem te deu, começando com `wss://`
(o "s" no final é importante):

```js
const WS_URL = 'wss://algumacoisa.up.railway.app';
```

Esse arquivo editado precisa estar na mesma pasta que o `bot.py` na
hospedagem (ou seja: suba essa alteração pro GitHub também, e a hospedagem
vai reimplantar sozinha com o arquivo atualizado).

**b) No OBS**, na fonte de Navegador, mude de "Local file" para "URL" e cole:

```
https://algumacoisa.up.railway.app/overlay.html
```

(reparar: aqui é `https://`, não `wss://` — é só na hora de configurar o
`WS_URL` dentro do arquivo que se usa `wss://`)

Prontinho — não precisa mais ter uma cópia do `overlay.html` separada no seu
PC; o OBS carrega direto da nuvem, e qualquer atualização futura no arquivo
(reflow de layout, novo design, etc.) só precisa ser subida pro GitHub uma
vez, sem precisar reconfigurar o OBS de novo.
