# ============================================================
# John Rolagens — Bot Discord + Servidor WebSocket para OBS
# ============================================================
# Requisitos: Python 3.10+
# Instalar: pip install discord.py websockets python-dotenv
# Iniciar:  python bot.py

import asyncio
import http
import json
import os
import re
import websockets
from websockets.server import serve
import discord

# ---------------------- CONFIGURAÇÃO ----------------------

from dotenv import load_dotenv

load_dotenv()  # lê o arquivo .env na mesma pasta do bot.py, se existir

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise SystemExit(
        "[Erro fatal] Crie um arquivo .env na mesma pasta do bot.py com a linha:\n"
        "DISCORD_TOKEN=seu_token_aqui\n\n"
        "IMPORTANTE: o token antigo estava exposto no código-fonte — regenere-o em "
        "https://discord.com/developers/applications antes de continuar."
    )

ROLLEM_BOT_NAME   = "rollem"
# Em hospedagens como Railway, a porta pública vem pela variável de ambiente
# PORT (definida automaticamente por eles). No seu PC local, essa variável
# não existe, então cai no 8765 de sempre — não precisa mudar nada pra testar
# localmente, e funciona automaticamente quando for hospedar.
WS_PORT           = int(os.environ.get("PORT", 8765))
MAX_DICE_PER_PAGE = 5
BOT_NAME          = "John Rolagens"

COLOR_PALETTE = {
    "vermelho": "#FF3B3B",
    "azul":     "#3B82F6",
    "verde":    "#5CD68A",
    "amarelo":  "#F5C242",
    "roxo":     "#A855F7",
    "rosa":     "#F472B6",
    "branco":   "#F5F3EE",
    "laranja":  "#FF5A3C",
    "cinza":    "#8A8D98",
    "preto":    "#2A2D38",
}
DEFAULT_COLOR = "branco"

# Layouts visuais disponíveis pro overlay. É uma escolha de MESA (afeta todo
# mundo ao mesmo tempo), não individual como as cores — por isso vive nas
# configurações globais (settings.json), igual a "soma".
AVAILABLE_LAYOUTS = ["fragmentos"]
DEFAULT_LAYOUT = "fragmentos"

CHARACTERS_FILE = os.path.join(os.path.dirname(__file__), "characters.json")

# ---------------------- PERSISTÊNCIA ----------------------

def load_characters() -> dict:
    try:
        with open(CHARACTERS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = {}
        for user_id, value in raw.items():
            if isinstance(value, str):
                result[user_id] = {"name": value, "nameColor": DEFAULT_COLOR, "diceColor": DEFAULT_COLOR}
            else:
                result[user_id] = {
                    "name":      value.get("name", ""),
                    "nameColor": value.get("nameColor", DEFAULT_COLOR),
                    "diceColor": value.get("diceColor", DEFAULT_COLOR),
                }
        return result
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_characters(characters: dict) -> bool:
    try:
        with open(CHARACTERS_FILE, "w", encoding="utf-8") as f:
            json.dump(characters, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        print(f"[Erro] Não foi possível salvar characters.json: {e}")
        return False

active_characters: dict = load_characters()

def get_or_create(user_id: str, fallback_name: str) -> dict:
    if user_id not in active_characters:
        active_characters[user_id] = {
            "name": fallback_name,
            "nameColor": DEFAULT_COLOR,
            "diceColor": DEFAULT_COLOR,
        }
    return active_characters[user_id]

# ---------------------- CONFIGURAÇÕES GERAIS (soma Σ liga/desliga) ----------------------

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_settings(settings: dict) -> bool:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        print(f"[Erro] Não foi possível salvar settings.json: {e}")
        return False

_settings: dict = load_settings()
sum_enabled: bool = _settings.get("sum_enabled", True)  # soma (Σ) ligada por padrão
current_layout: str = _settings.get("layout", DEFAULT_LAYOUT)  # layout do overlay — vale pra todo mundo
if current_layout not in AVAILABLE_LAYOUTS:
    current_layout = DEFAULT_LAYOUT
current_channel_id = _settings.get("channel_id")  # None = escuta em qualquer canal com acesso

# ---------------------- WEBSOCKET ----------------------

connected_clients: set = set()

async def ws_handler(websocket):
    connected_clients.add(websocket)
    print("[WebSocket] Overlay conectado.")
    try:
        await websocket.wait_closed()
    finally:
        connected_clients.discard(websocket)

async def broadcast(data: dict):
    if not connected_clients:
        return
    payload = json.dumps(data, ensure_ascii=False)
    await asyncio.gather(
        *[ws.send(payload) for ws in connected_clients if ws.open],
        return_exceptions=True,
    )

# ---------------------- PARSING DO ROLLEM ----------------------
#
# Formatos suportados:
#   61 ⟵ [11, 10, 8, 4] 10d20            → múltiplos dados do mesmo tipo, numa linha só
#   ` 17 ` 🎲 [13] 1d20 + 5              → formato novo do Rollem com crases
#   8 ⟵ [6] 1d8 + [2] 1d6               → dados de tipos diferentes somados
#   ` 13 `⟵ [13] 1d20                   → prefixo # (ex: 5#d20): uma linha por dado
#   ` 19 `⟵ [19] 1d20
#   ` 10 `⟵ [10] 1d20  (etc, uma por linha)
#
# Regra de soma: a soma real (Σ) é calculada e exibida (badge ao lado do
# nome do personagem) quando há mais de um dado na rolagem — seja vários
# dados do mesmo tipo numa linha (5d20), tipos diferentes somados
# (1d8 + 1d6), ou várias linhas separadas (5#d20). EXCEÇÕES: soma
# desativada via `!soma off`, ou rolagem feita só de d20 (checks de d20
# não são somados entre si por padrão nesse sistema).

# Primeira fatia: tem total antes do separador
FIRST_SLICE = re.compile(
    r"`?\s*(-?\d+)\s*`?\s*[^[\d\n]{0,8}\[([^\]]+)\]\s*([0-9]*d[0-9]+)",
    re.IGNORECASE,
)
# Fatias adicionais de soma: + [2] 1d6  ou  - [1] 1d4
EXTRA_SLICE = re.compile(
    r"[+\-]\s*\[([^\]]+)\]\s*([0-9]*d[0-9]+)",
    re.IGNORECASE,
)

def clean_dice(raw: str) -> list[int]:
    cleaned = re.sub(r'\*+', '', raw)
    result = []
    for d in cleaned.split(","):
        d = d.strip()
        if re.fullmatch(r'-?\d+', d):
            result.append(int(d))
    return result

def parse_single_expression(text: str):
    """Faz o parsing de UMA linha/expressão do Rollem, por exemplo:
       '69 ⟵ [19, 17, 15, 11, 7] 5d20'  ou  '8 ⟵ [6] 1d8 + [2] 1d6'  ou
       ' 13 ⟵ [13] 1d20'.
       Retorna (rolls, total_real_da_linha) ou None se a linha não bater com o formato."""
    bonus_at_end = re.search(r'\]\s*[0-9]*d[0-9]+\s*([+\-])\s*(\d+)\s*$', text)
    end_bonus = 0
    if bonus_at_end:
        sign = bonus_at_end.group(1)
        val  = int(bonus_at_end.group(2))
        end_bonus = -val if sign == "-" else val

    first = FIRST_SLICE.search(text)
    if not first:
        return None

    dice_main  = clean_dice(first.group(2))
    expr_main  = first.group(3)
    sides_m    = re.search(r"d(\d+)", expr_main, re.IGNORECASE)
    sides_main = int(sides_m.group(1)) if sides_m else None
    extras     = list(EXTRA_SLICE.finditer(text, first.end()))

    rolls = []
    if len(dice_main) > 1:
        # Múltiplos dados do mesmo tipo numa linha só (ex: 10d20)
        count = len(dice_main)
        for j, val in enumerate(dice_main):
            is_last_die = (j == count - 1 and not extras)
            bonus = end_bonus if is_last_die else 0
            rolls.append({
                "total":  val + bonus,
                "dice":   [val],
                "sides":  sides_main,
                "bonus":  bonus,
            })
    else:
        val   = dice_main[0] if dice_main else int(first.group(1))
        bonus = end_bonus if not extras else 0
        rolls.append({
            "total":  val + bonus,
            "dice":   [val],
            "sides":  sides_main,
            "bonus":  bonus,
        })

    # Fatias extras: + [2] 1d6
    for i, m in enumerate(extras):
        dice_extra  = clean_dice(m.group(1))
        expr_extra  = m.group(2)
        sides_m2    = re.search(r"d(\d+)", expr_extra, re.IGNORECASE)
        sides_extra = int(sides_m2.group(1)) if sides_m2 else None
        is_last     = (i == len(extras) - 1)
        val   = dice_extra[0] if dice_extra else 0
        bonus = end_bonus if is_last else 0
        rolls.append({
            "total":  val + bonus,
            "dice":   [val],
            "sides":  sides_extra,
            "bonus":  bonus,
        })

    # Total real da expressão inteira: vem sempre do próprio Rollem
    # (mais confiável do que somar manualmente, cobre vantagem/kh/kl etc.)
    real_total = int(first.group(1))
    return rolls, real_total


def parse_rollem(content: str) -> list[dict]:
    lines = [ln for ln in content.split("\n") if ln.strip()]

    line_results = []
    for ln in lines:
        parsed = parse_single_expression(ln)
        if parsed:
            line_results.append(parsed)

    if not line_results:
        return []

    if len(line_results) == 1:
        # Uma única expressão na mensagem (ex: 5d20, 1d8 + 1d6, d20 + 5...)
        rolls, real_total = line_results[0]
    else:
        # Várias linhas independentes = uso do prefixo # (ex: 5#d20), onde o
        # Rollem manda um resultado por linha em vez de agrupar tudo numa só mensagem.
        rolls = []
        real_total = 0
        for line_rolls, line_total in line_results:
            rolls.extend(line_rolls)
            real_total += line_total

    # Soma real (Σ) no ÚLTIMO dado da rolagem, quando há mais de um dado
    # envolvido — vale pra 5d20, 1d8 + 1d6, 5#d20, 3d12+2d6+2, etc.
    # Exceções: soma desativada via !soma off, ou rolagem feita só de d20
    # (checks de d20 normalmente não são somados entre si nesse sistema).
    for r in rolls:
        r["sum_total"] = None
    all_d20 = all(r["sides"] == 20 for r in rolls)
    if len(rolls) > 1 and sum_enabled and not all_d20:
        rolls[-1]["sum_total"] = real_total

    return rolls


def group_into_pages(rolls: list[dict]) -> list[list[dict]]:
    return [rolls[i:i + MAX_DICE_PER_PAGE] for i in range(0, len(rolls), MAX_DICE_PER_PAGE)]

# ---------------------- MENSAGENS DO DISCORD ----------------------

def build_help_text() -> str:
    colors = " ".join(f"`{c}`" for c in COLOR_PALETTE.keys())
    layouts = ", ".join(f"`{l}`" for l in AVAILABLE_LAYOUTS)
    soma_status = "ativada ✅" if sum_enabled else "desativada 🚫"
    canal_atual = f"<#{current_channel_id}>" if current_channel_id else "qualquer canal com acesso _(nenhum fixado ainda)_"
    return (
        "🎲 **John Rolagens — Comandos**\n"
        "──────────────────────────────\n\n"
        "**👤 Seu personagem**\n"
        "`!set name <nome>` — nome exibido no overlay\n"
        "`!set namecolor <cor>` — cor da barra ao lado do nome\n"
        "`!set dicecolor <cor>` — cor do contorno do dado\n\n"
        "**🎨 Visual da mesa**  _(vale pra todo mundo, não é individual)_\n"
        "`!set layout <nome>` — troca o visual do overlay\n"
        f"Disponíveis: {layouts}  ·  Atual: `{current_layout}`\n\n"
        "**📍 Canal ativo**  _(vale pra todo mundo, não é individual)_\n"
        "Por padrão o bot lê e responde em qualquer canal onde ele tenha acesso "
        "de leitura/escrita (geralmente os canais abertos pro @everyone do servidor).\n"
        "`!set channel #canal` — fixa o bot pra escutar e responder só nesse canal\n"
        f"Atual: {canal_atual}\n\n"
        "**Σ Soma total**\n"
        "`!soma on` / `!soma off` — liga/desliga a soma no overlay\n"
        f"Status atual: {soma_status}\n"
        "_(a soma nunca aparece em rolagens feitas só de d20, mesmo ativada)_\n\n"
        "**ℹ️ Informações**\n"
        "`!list` — personagens configurados\n"
        "`!help` — esta mensagem\n\n"
        "**🎨 Cores disponíveis**\n"
        f"{colors}\n\n"
        "**🎲 Dados suportados:** d4, d6, d8, d10, d12, d20 e customizados\n"
        "**Exemplos de rolagem:** `d20` · `3#d20` · `10d20` · `1d8 + 1d6` · `2d6 - 3`"
    )

# ---------------------- BOT DISCORD ----------------------

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
_channel_ref = None  # referência ao canal para mensagens de sistema

@bot.event
async def on_ready():
    global _channel_ref
    print(f"[Discord] Bot conectado como {bot.user}")

    if current_channel_id is None:
        print("[Discord] Nenhum canal fixado ainda — escutando em todos os canais com acesso de leitura/escrita.")
        print("[Discord] Use `!set channel #canal` em qualquer canal pra fixar um específico.")
        return

    print(f"[Discord] Escutando o canal ID {current_channel_id}")
    channel = bot.get_channel(current_channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(current_channel_id)
        except discord.NotFound:
            print(f"[Erro] Canal com ID {current_channel_id} não encontrado.")
            return
        except discord.Forbidden:
            print(f"[Erro] Sem acesso ao canal ID {current_channel_id}.")
            return

    _channel_ref = channel
    print(f"[Discord] Acesso confirmado ao canal #{channel.name}. Tudo pronto!")

    # Alerta de inicialização no canal
    try:
        await channel.send(f"🟢 **{BOT_NAME} está online!** Overlay pronto para receber rolagens.")
    except Exception:
        pass

@bot.event
async def on_disconnect():
    # Tenta avisar no canal que o bot caiu (nem sempre funciona se a queda for brusca)
    if _channel_ref:
        try:
            await _channel_ref.send(f"🔴 **{BOT_NAME}** perdeu a conexão. Tentando reconectar...")
        except Exception:
            pass

@bot.event
async def on_resumed():
    # Dispara quando a sessão com o Discord é retomada com sucesso após uma
    # queda (o gateway "resumiu" sem precisar reidentificar do zero)
    print("[Discord] Conexão retomada com sucesso.")
    if _channel_ref:
        try:
            await _channel_ref.send(f"🟢 **{BOT_NAME}** reconectado com sucesso!")
        except Exception:
            pass

@bot.event
async def on_message(message: discord.Message):
    global sum_enabled, current_layout, current_channel_id, _channel_ref

    if current_channel_id is not None and message.channel.id != current_channel_id:
        return
    if message.author.bot and message.author.name.lower() != ROLLEM_BOT_NAME:
        return

    content = message.content.strip()
    user_id = str(message.author.id)
    available_colors = ", ".join(COLOR_PALETTE.keys())

    # -------- !help --------
    if not message.author.bot and content.lower() == "!help":
        await message.reply(build_help_text())
        return

    # -------- !list --------
    if not message.author.bot and content.lower() == "!list":
        soma_status = "ativada ✅" if sum_enabled else "desativada 🚫"
        canal_atual = f"<#{current_channel_id}>" if current_channel_id else "qualquer canal (nenhum fixado)"
        rodape = f"\n──────────────────────────────\n🎨 Layout da mesa: `{current_layout}`  ·  Σ Soma: {soma_status}  ·  📍 Canal: {canal_atual}"

        if not active_characters:
            await message.reply(
                "📋 **Nenhum personagem configurado ainda.**\n"
                "Use `!set name <nome>` pra começar." + rodape
            )
            return

        lines = ["📋 **Personagens configurados**", "──────────────────────────────"]
        for uid, char in active_characters.items():
            member = message.guild.get_member(int(uid)) if message.guild else None
            discord_name = member.display_name if member else f"ID {uid}"
            lines.append(
                f"**{char['name']}**  _(de {discord_name})_ — barra `{char['nameColor']}` · dado `{char['diceColor']}`"
            )
        await message.reply("\n".join(lines) + rodape)
        return

    # -------- !soma (liga/desliga o Σ) --------
    if not message.author.bot and content.lower().startswith("!soma"):
        arg = content[5:].strip().lower()
        if arg in ("on", "ativar", "ligar", "ligado"):
            sum_enabled = True
            _settings["sum_enabled"] = True
            saved = save_settings(_settings)
            msg = "✅ Soma (Σ) **ativada**."
            if not saved:
                msg += " (não consegui salvar a preferência em disco, vai voltar ao padrão se reiniciar)"
            await message.reply(msg)
        elif arg in ("off", "desativar", "desligar", "desligado"):
            sum_enabled = False
            _settings["sum_enabled"] = False
            saved = save_settings(_settings)
            msg = "🚫 Soma (Σ) **desativada**."
            if not saved:
                msg += " (não consegui salvar a preferência em disco, vai voltar ao padrão se reiniciar)"
            await message.reply(msg)
        else:
            estado = "ativada ✅" if sum_enabled else "desativada 🚫"
            await message.reply(
                f"⚠️ Uso correto: `!soma on` ou `!soma off`.\nAtualmente a soma (Σ) está **{estado}**."
            )
        return

    # -------- !set --------
    if not message.author.bot and content.startswith("!set"):
        args = content[4:].strip().split()
        subcommand = args[0].lower() if args else ""
        rest = args[1:]

        if subcommand == "name":
            name = " ".join(rest).strip()
            if not name:
                await message.reply("⚠️ Uso correto: `!set name NomeDoPersonagem`. Exemplo: `!set name Yoona`")
                return
            if len(name) > 30:
                await message.reply("⚠️ Nome muito longo (máximo 30 caracteres).")
                return
            char = get_or_create(user_id, name)
            old_name = char.get("name", "")
            char["name"] = name
            saved = save_characters(active_characters)
            change = f"**{old_name}** → **{name}**" if old_name and old_name != name else f"**{name}**"
            if saved:
                await message.reply(f"✅ Personagem definido: {change}")
            else:
                await message.reply(f"⚠️ Personagem definido como {change} para esta sessão, mas não consegui salvar em disco.")

        elif subcommand == "namecolor":
            color_key = rest[0].lower() if rest else ""
            if not color_key:
                await message.reply(f"⚠️ Uso correto: `!set namecolor cor`.\nCores: {available_colors}")
                return
            if color_key not in COLOR_PALETTE:
                await message.reply(f"⚠️ Cor `{color_key}` não reconhecida.\nCores disponíveis: {available_colors}")
                return
            char = get_or_create(user_id, message.author.display_name)
            old_color = char.get("nameColor", DEFAULT_COLOR)
            char["nameColor"] = color_key
            saved = save_characters(active_characters)
            change = f"`{old_color}` → `{color_key}`" if old_color != color_key else f"`{color_key}`"
            if saved:
                await message.reply(f"✅ Cor da barra do nome: {change}")
            else:
                await message.reply(f"⚠️ Cor definida como {change} para esta sessão, mas não consegui salvar.")

        elif subcommand == "dicecolor":
            color_key = rest[0].lower() if rest else ""
            if not color_key:
                await message.reply(f"⚠️ Uso correto: `!set dicecolor cor`.\nCores: {available_colors}")
                return
            if color_key not in COLOR_PALETTE:
                await message.reply(f"⚠️ Cor `{color_key}` não reconhecida.\nCores disponíveis: {available_colors}")
                return
            char = get_or_create(user_id, message.author.display_name)
            old_color = char.get("diceColor", DEFAULT_COLOR)
            char["diceColor"] = color_key
            saved = save_characters(active_characters)
            change = f"`{old_color}` → `{color_key}`" if old_color != color_key else f"`{color_key}`"
            if saved:
                await message.reply(f"✅ Cor do contorno do dado: {change}")
            else:
                await message.reply(f"⚠️ Cor definida como {change} para esta sessão, mas não consegui salvar.")

        elif subcommand == "layout":
            layout_key = rest[0].lower() if rest else ""
            available_layouts = ", ".join(AVAILABLE_LAYOUTS)
            if not layout_key:
                await message.reply(
                    f"⚠️ Uso correto: `!set layout nome`.\n"
                    f"Layouts disponíveis: {available_layouts}\n"
                    f"Layout atual: `{current_layout}`"
                )
                return
            if layout_key not in AVAILABLE_LAYOUTS:
                await message.reply(f"⚠️ Layout `{layout_key}` não existe.\nLayouts disponíveis: {available_layouts}")
                return
            old_layout = current_layout
            current_layout = layout_key
            _settings["layout"] = layout_key
            saved = save_settings(_settings)
            change = f"`{old_layout}` → `{layout_key}`" if old_layout != layout_key else f"`{layout_key}`"
            msg = f"🎨 Layout do overlay alterado para todo mundo: {change}"
            if not saved:
                msg += "\n⚠️ Não consegui salvar em disco — vai voltar ao padrão se o bot reiniciar."
            await message.reply(msg)

        elif subcommand == "channel":
            canal_atual = f"<#{current_channel_id}>" if current_channel_id else "qualquer canal (nenhum fixado ainda)"
            if not rest:
                await message.reply(
                    "⚠️ Uso correto: `!set channel #nome-do-canal` (mencione o canal com #).\n"
                    "Ou `!set channel limpar` pra voltar a escutar em qualquer canal.\n"
                    f"Canal atual: {canal_atual}"
                )
                return

            if rest[0].lower() in ("limpar", "reset", "todos", "nenhum"):
                current_channel_id = None
                _channel_ref = None
                _settings["channel_id"] = None
                save_settings(_settings)
                await message.reply("📍 Canal desfixado — o bot volta a escutar e responder em qualquer canal com acesso.")
                return

            # Extrai o ID de uma menção de canal (<#123...>) ou de um ID puro
            raw = rest[0]
            match = re.search(r"\d{15,20}", raw)
            if not match:
                await message.reply("⚠️ Não reconheci esse canal. Mencione ele digitando `#` e escolhendo na lista do Discord.")
                return
            new_channel_id = int(match.group())

            try:
                new_channel = bot.get_channel(new_channel_id) or await bot.fetch_channel(new_channel_id)
            except (discord.NotFound, discord.Forbidden):
                await message.reply("⚠️ Não consegui acessar esse canal — confira se o bot tem permissão nele.")
                return

            old_channel_id = current_channel_id
            old_channel_label = f"<#{old_channel_id}>" if old_channel_id else "qualquer canal"
            current_channel_id = new_channel_id
            _channel_ref = new_channel
            _settings["channel_id"] = new_channel_id
            saved = save_settings(_settings)

            if saved:
                await message.reply(f"🔀 Canal ativo alterado para todo mundo: {old_channel_label} → <#{new_channel_id}>")
            else:
                await message.reply(f"⚠️ Canal alterado para <#{new_channel_id}> nesta sessão, mas não consegui salvar em disco.")
            try:
                await new_channel.send(f"🔀 **{BOT_NAME}** agora escuta e responde aqui.")
            except Exception:
                pass

        else:
            await message.reply(
                f"⚠️ Subcomando `{subcommand or '(vazio)'}` não reconhecido.\n"
                "Use `!help` para ver todos os comandos disponíveis."
            )
        return

    # Typo comum de !set
    if not message.author.bot and re.match(r"^!se?t?\b", content, re.IGNORECASE) and len(content) < 10:
        await message.reply("🤔 Comando não reconhecido. Você quis dizer `!set name NomeDoPersonagem`? Use `!help` para ver todos os comandos.")
        return

    # -------- Mensagens do Rollem --------
    if message.author.name.lower() == ROLLEM_BOT_NAME:
        mentioned = message.mentions[0] if message.mentions else None
        if not mentioned:
            print(f"[Aviso] Mensagem do Rollem sem menção identificada. Ignorada: {content[:80]}")
            return

        uid = str(mentioned.id)
        char = active_characters.get(uid)
        char_name  = char["name"]      if char else mentioned.display_name
        name_color = COLOR_PALETTE.get(char["nameColor"] if char else DEFAULT_COLOR, COLOR_PALETTE[DEFAULT_COLOR])
        dice_color = COLOR_PALETTE.get(char["diceColor"] if char else DEFAULT_COLOR, COLOR_PALETTE[DEFAULT_COLOR])

        rolls = parse_rollem(content)
        if not rolls:
            warn = f"[Aviso] Rolagem não reconhecida pelo parser: {content[:100]}"
            print(warn)
            # Avisa no canal que algo não foi reconhecido
            try:
                await message.channel.send(
                    f"⚠️ **{BOT_NAME}:** não consegui ler essa rolagem para o overlay. "
                    f"Formato não reconhecido: `{content[:80]}`"
                )
            except Exception:
                pass
            return

        pages = group_into_pages(rolls)

        await broadcast({
            "type":      "roll",
            "character": char_name,
            "nameColor": name_color,
            "diceColor": dice_color,
            "layout":    current_layout,
            "pages": [
                {
                    "dice": [
                        {
                            "sides":      r["sides"],
                            "values":     r["dice"],
                            "bonus":      r["bonus"],
                            "total":      r["total"],
                            "sum_total":  r.get("sum_total"),
                        }
                        for r in page
                    ]
                }
                for page in pages
            ],
            "timestamp": message.created_at.timestamp(),
        })

        print(f"[Rolagem] {char_name} -> {len(rolls)} dado(s) em {len(pages)} bloco(s)")

# ---------------------- ARQUIVOS SERVIDOS POR URL ----------------------
#
# Serve overlay.html (e editor.html) pela MESMA porta do WebSocket — é assim
# que dá pra usar o overlay por URL em vez de arquivo local, e é o que
# funciona em hospedagens que só expõem uma porta pública (Railway, etc).
# A raiz "/" fica livre de propósito: é o caminho que o WebSocket usa pro
# handshake (ws://.../ ou wss://.../), então NUNCA servimos arquivo nela.

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVABLE_FILES = {
    "/overlay.html": ("text/html; charset=utf-8", "overlay.html"),
    "/editor.html":  ("text/html; charset=utf-8", "editor.html"),
}

async def process_request(path, request_headers):
    clean_path = path.split("?")[0]
    entry = SERVABLE_FILES.get(clean_path)
    if entry is None:
        return None  # não é um arquivo conhecido — segue pro handshake normal do WebSocket

    content_type, filename = entry
    try:
        with open(os.path.join(BOT_DIR, filename), "rb") as f:
            body = f.read()
        return http.HTTPStatus.OK, [("Content-Type", content_type)], body
    except FileNotFoundError:
        return http.HTTPStatus.NOT_FOUND, [], b"Arquivo nao encontrado no servidor.\n"

# ---------------------- INICIALIZAÇÃO ----------------------

async def main():
    print(f"[Servidor] Iniciando na porta {WS_PORT}...")
    try:
        # "0.0.0.0" = aceita conexão de qualquer endereço. É obrigatório pra
        # hospedagem em nuvem (Railway etc.) e continua funcionando normal
        # no seu PC local também.
        async with serve(ws_handler, "0.0.0.0", WS_PORT, process_request=process_request):
            print(f"[WebSocket] Overlay conecta em ws://localhost:{WS_PORT} (ou wss://SEU_DOMINIO em produção)")
            print(f"[HTTP] Overlay acessível por URL em http://localhost:{WS_PORT}/overlay.html")
            await bot.start(DISCORD_TOKEN)
    except OSError as e:
        print(f"[Erro fatal] Porta {WS_PORT} já em uso. O bot já está rodando em outro terminal?")
        print(f"Detalhes: {e}")
    except discord.LoginFailure:
        print("[Erro fatal] Token inválido. Confira em https://discord.com/developers/applications")
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Desligamento manual (Ctrl+C no terminal) — avisa no canal antes de fechar
        print("[Discord] Desligando por pedido manual (Ctrl+C)...")
    finally:
        if _channel_ref:
            try:
                await _channel_ref.send(f"⚪ **{BOT_NAME}** foi desligado.")
            except Exception:
                pass
        if not bot.is_closed():
            await bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
