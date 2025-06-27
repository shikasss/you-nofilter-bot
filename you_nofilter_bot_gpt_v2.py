import os
import json
import logging
import hmac
import hashlib
import base64
from datetime import datetime, timedelta
from collections import Counter

import openai
from yookassa import Configuration, Payment
from aiohttp import web
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ─── SYSTEM PROMPT ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
Ты — внимательный и эмпатичный ИИ-психолог.
Твоя задача — помочь человеку лучше понять себя, создать ощущение безопасности,
тепла и уважительного диалога.

★ Стиль
• Говори просто, по-человечески, избегая клише и сложных формулировок.
• Будь «хамелеоном»: подстраивайся под стиль собеседника, но мягко и деликатно.
• Сохраняй спокойствие и профессионализм, даже если человек взволнован.
• Иногда можешь делиться наблюдениями о других людях — не как совет, а как пример.

★ Твоя речь
• Избегай пустых фраз поддержки. Покажи, что понял, и мягко веди к следующему шагу.
• Помни о «психологической памяти» — у тебя есть лёгкий намёк на то, о чём вы говорили раньше.
  Не цитируй дословно, но строй ощущение узнавания.

Детекция «проблемных» сигналов
Если в словах пользователя встречаются «стресс», «беспокойство», «боль»,
«утомление», «трудности», «не по себе» и т. п. — это знак, что нужно переключиться
на вопросы о способах справляться, ресурсы и копинг-стратегии.

При таких сигналах:
  • Не задавай те же поверхностные вопросы.
  • Спроси: «Как обычно вы справляетесь с этим?»
  • Предложи варианты: дыхательные упражнения, хобби, физическую активность, границы, поддержку.

★ Если человек уходит в сторону
1. Сначала — короткий отклик.
2. Потом — мягкий вопрос, возвращающий к теме.
3. Если он явно хочет сменить тему — поддержи и помоги связать это с его состоянием.

★ Запрещено
• Осуждать
• Говорить «успокойтесь»
• Ставить диагнозы (можно упомянуть, что диагноз ставят только специалисты)

Обращайся на «вы» — нейтрально и с уважением.
"""

# ─── КОНФИГУРАЦИЯ ─────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
YKASSA_SHOP_ID    = os.getenv("YKASSA_SHOP_ID")
YKASSA_SECRET_KEY = os.getenv("YKASSA_SECRET_KEY")
BASE_URL          = os.getenv("WEBHOOK_URL")

openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO)

# YooKassa
Configuration.account_id = YKASSA_SHOP_ID
Configuration.secret_key  = YKASSA_SECRET_KEY
YK_SECRET = YKASSA_SECRET_KEY.encode()

# ─── ХРАНИЛИЩЕ ────────────────────────────────────────────────────────────────
DATA_DIR     = "/mnt/data"
USED_FILE    = os.path.join(DATA_DIR, "used.json")
ACCESS_FILE  = os.path.join(DATA_DIR, "access.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
os.makedirs(DATA_DIR, exist_ok=True)

def load_json(path):
    if os.path.exists(path):
        return json.load(open(path, "r", encoding="utf-8"))
    return {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

used_data    = load_json(USED_FILE)
access_data  = load_json(ACCESS_FILE)
history_data = load_json(HISTORY_FILE)
orders       = {}  # {order_id: user_id}

# ─── КОНСТАНТЫ и клавиатура ──────────────────────────────────────────────────
SESSION    = 0
FREE_LIMIT = 10

main_kb = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🧠 Начать сессию")],
        [KeyboardButton("❓ О боте"), KeyboardButton("💳 Купить доступ")],
    ],
    resize_keyboard=True,
)

# ─── УТИЛИТЫ ─────────────────────────────────────────────────────────────────
def has_access(uid: str) -> bool:
    until = access_data.get(uid)
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now()
    except:
        return False

def detect_tone(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("ура","рад","счаст")):     return "joy"
    if any(w in t for w in ("груст","тоск","плохо")):  return "sadness"
    if any(w in t for w in ("злюсь","бесит","раздраж")): return "anger"
    if any(w in t for w in ("спокойно","норм","ладно")): return "calm"
    return "neutral"

def extract_memory(history, limit=8):
    cnt = Counter()
    for msg in history[-limit:]:
        if msg["role"] == "user":
            for w in msg["content"].lower().split():
                w = w.strip(",.!?\"«»")
                if len(w) > 3 and w not in {"это","просто","очень"}:
                    cnt[w] += 1
    return ", ".join(w for w,_ in cnt.most_common(3))

# ─── ОБРАБОТЧИКИ ТЕЛЕГРАМА ─────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ctx.user_data["history"] = history_data.get(uid, [])
    await update.message.reply_photo(
        "https://i.imgur.com/AH7eK7Z.png",
        caption="Ты. Без фильтра.\n\nМесто, где можно быть настоящим."
    )
    await update.message.reply_text("Что у вас на душе?", reply_markup=main_kb)
    return SESSION

async def about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Бот-психолог: слушает, задаёт вопросы и поддерживает."
    )

async def buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        uid  = update.callback_query.from_user.id
        send = update.callback_query.edit_message_text
    else:
        uid  = update.effective_user.id
        send = update.message.reply_text

    order_id = f"order_{uid}_{int(datetime.now().timestamp())}"
    payment = Payment.create({
        "amount":      {"value":"5.00","currency":"RUB"},
        "confirmation":{"type":"redirect","return_url": BASE_URL},
        "capture":     True,
        "description": f"Доступ YouNoFilter (пользователь {uid})",
        "metadata":    {"user_id":str(uid),"order_id":order_id}
    })
    orders[order_id] = str(uid)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Оплатить 5₽/мес", url=payment.confirmation.confirmation_url)
    ]])
    await send(
        "🔒 Лимит бесплатных сообщений исчерпан.\n💳 Стоимость: 5 ₽/мес. Нажмите кнопку для оплаты.",
        reply_markup=kb
    )

async def handle_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = str(update.effective_user.id)
    text = update.message.text

    # проверяем лимит
    if not has_access(uid):
        used = used_data.get(uid, 0)
        if used >= FREE_LIMIT:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Купить доступ", callback_data="BUY")]])
            await update.message.reply_text("🔒 Лимит исчерпан.", reply_markup=kb)
            return SESSION
        used_data[uid] = used + 1
        save_json(USED_FILE, used_data)
        await update.message.reply_text(
            f"🧭 Осталось бесплатных сообщений: {FREE_LIMIT - used_data[uid]}",
            reply_markup=main_kb
        )

    # история
    history = ctx.user_data.setdefault("history", [])
    history.append({"role":"user","content":text})
    history_data[uid] = history
    save_json(HISTORY_FILE, history_data)

    # память
    mem = extract_memory(history)
    if mem:
        ctx.user_data["memory"] = mem

    # собираем prompt
    system = [{"role":"system","content":SYSTEM_PROMPT}]
    tone   = detect_tone(text)
    prev   = ctx.user_data.get("prev_tone")
    if tone != prev:
        system.append({"role":"system","content":f"Настроение пользователя: {tone}"})
        ctx.user_data["prev_tone"] = tone
    if "memory" in ctx.user_data:
        system.append({"role":"system","content":f"Небольшое напоминание: {ctx.user_data['memory']}"})
    prompt = system + history

    resp = openai.chat.completions.create(model="gpt-4o-mini", messages=prompt)
    answer = resp.choices[0].message.content

    history.append({"role":"assistant","content":answer})
    save_json(HISTORY_FILE, history_data)

    await update.message.reply_text(answer)
    return SESSION

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Сессия завершена.", reply_markup=main_kb)
    return ConversationHandler.END

# ─── Webhook для Telegram ─────────────────────────────────────────────────────
async def telegram_webhook(request: web.Request):
    data   = await request.json()
    upd    = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(upd)
    return web.Response(text="OK")

# ─── Webhook для YooKassa ──────────────────────────────────────────────────────
async def ykassa_webhook(request: web.Request):
    body      = await request.read()
    sig       = request.headers.get("Content-SHA256","")
    expected  = base64.b64encode(hmac.new(YK_SECRET, body, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(sig, expected):
        return web.Response(status=400, text="invalid signature")

    event = json.loads(body.decode())
    if event.get("event") == "payment.succeeded":
        md       = event["object"]["metadata"]
        order_id = md.get("order_id")
        uid      = orders.get(order_id)
        if uid:
            access_data[uid] = (datetime.now() + timedelta(days=30)).isoformat()
            save_json(ACCESS_FILE, access_data)
            await telegram_app.bot.send_message(int(uid), "✅ Оплата принята, доступ продлён!")

    return web.Response(text="OK")

# ─── Запуск ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    telegram_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("🧠 Начать сессию"), start),
        ],
        states={SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    telegram_app.add_handler(conv)
    telegram_app.add_handler(CommandHandler("menu", start))
    telegram_app.add_handler(CommandHandler("buy", buy))
    telegram_app.add_handler(MessageHandler(filters.Regex("💳 Купить доступ"), buy))
    telegram_app.add_handler(CallbackQueryHandler(buy, pattern="^BUY$"))
    telegram_app.add_handler(MessageHandler(filters.Regex("❓ О боте"), about))

    # перед первым запуском вручную:
    # curl -F "url=https://<ваш-домен>/telegram" \
    #      https://api.telegram.org/bot<TELEGRAM_TOKEN>/setWebhook

    web_app = web.Application()
    web_app.router.add_post("/telegram", telegram_webhook)
    web_app.router.add_post("/ykassa-webhook", ykassa_webhook)

    logging.info("Сервер слушает порт %s …", os.getenv("PORT", "10000"))
    web.run_app(web_app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
