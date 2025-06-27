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
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, ConversationHandler, filters
)

# ─── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
Ты — внимательный и эмпатичный ИИ-психолог. …
(здесь ваш полный системный промт)
Обращайся к собеседнику на «вы» — нейтрально, с уважением.
"""

# ─── КОНФИГУРАЦИЯ ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
BASE_URL          = os.getenv("BASE_URL")           # https://your-app.onrender.com
YKASSA_SHOP_ID    = os.getenv("YKASSA_SHOP_ID")
YKASSA_SECRET_KEY = os.getenv("YKASSA_SECRET_KEY")

openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO)

# YooKassa
Configuration.account_id = YKASSA_SHOP_ID
Configuration.secret_key  = YKASSA_SECRET_KEY

# Хранение данных
DATA_DIR     = "/mnt/data"
USED_FILE    = os.path.join(DATA_DIR, "used_data.json")
ACCESS_FILE  = os.path.join(DATA_DIR, "access_data.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history_data.json")
os.makedirs(DATA_DIR, exist_ok=True)

def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

used_data    = load_json(USED_FILE)
access_data  = load_json(ACCESS_FILE)
history_data = load_json(HISTORY_FILE)

# Временная память для сопоставления заказов
orders = {}

# Сессия
SESSION    = 0
FREE_LIMIT = 10

main_keyboard = ReplyKeyboardMarkup(
    [[KeyboardButton("🧠 Начать сессию")],
     [KeyboardButton("❓ О боте"), KeyboardButton("💳 Купить доступ")]],
    resize_keyboard=True
)

# ─── УТИЛИТЫ ───────────────────────────────────────────────────────────────────
def has_access(user_id: str) -> bool:
    until = access_data.get(user_id)
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now()
    except:
        return False

def detect_tone(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("ура","рад","счаст")):      return "joy"
    if any(w in t for w in ("груст","тоск","плохо")):   return "sadness"
    if any(w in t for w in ("злюсь","бесит","раздраж")): return "anger"
    if any(w in t for w in ("спокойно","норм","ладно")): return "calm"
    return "neutral"

def extract_memory(history, limit=8):
    cnt = Counter()
    for msg in history[-limit:]:
        if msg["role"]=="user":
            for w in msg["content"].lower().split():
                w = w.strip(",.!?\"«»")
                if len(w)>3 and w not in {"это","просто","очень","такой","какой","когда"}:
                    cnt[w]+=1
    return ", ".join(w for w,_ in cnt.most_common(3))

# ─── ОБРАБОТЧИКИ ТЕЛЕГРАМА ────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ctx.user_data["history"] = history_data.get(uid, [])
    await update.message.reply_photo(
        "https://i.imgur.com/AH7eK7Z.png",
        caption="Ты. Без фильтра.\n\nМесто, где можно быть настоящим."
    )
    await update.message.reply_text("Напишите, что у вас на душе.", reply_markup=main_keyboard)
    return SESSION

async def about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Этот бот — ваш ИИ-психолог. Помогает разобраться в себе и поддерживает."
    )

async def buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        uid = update.callback_query.from_user.id
        send = update.callback_query.edit_message_text
    else:
        uid = update.effective_user.id
        send = update.message.reply_text

    order_id = f"access_{uid}_{int(datetime.now().timestamp())}"
    payment = Payment.create({
        "amount":      {"value":"5.00","currency":"RUB"},
        "confirmation":{"type":"redirect","return_url":BASE_URL},
        "capture":     True,
        "description": f"Доступ YouNoFilter, пользователь {uid}",
        "metadata":    {"user_id":str(uid),"order_id":order_id}
    })
    orders[order_id] = str(uid)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Оплатить 5 ₽/мес", url=payment.confirmation.confirmation_url)
    ]])
    await send(
        "🔒 Бесплатные сессии закончились.\n\n💳 5 ₽ в месяц.\nНажмите кнопку ниже для оплаты.",
        reply_markup=kb
    )

async def handle_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    text = update.message.text

    # лимит
    if not has_access(uid):
        used = used_data.get(uid,0)
        if used>=FREE_LIMIT:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Купить доступ", callback_data="BUY")]])
            await update.message.reply_text(
                "🔒 Лимит бесплатных сообщений исчерпан.", reply_markup=kb
            )
            return SESSION
        used_data[uid]=used+1
        save_json(USED_FILE, used_data)
        await update.message.reply_text(f"Осталось {FREE_LIMIT-used_data[uid]} бесплатных сообщений.")

    # история
    h = ctx.user_data.setdefault("history",[])
    h.append({"role":"user","content":text})
    history_data[uid]=h
    save_json(HISTORY_FILE, history_data)

    # память
    mem = extract_memory(h)
    if mem:
        ctx.user_data["memory"]=mem

    # собираем prompt
    prompts = [{"role":"system","content":SYSTEM_PROMPT}]
    tone = detect_tone(text)
    prev = ctx.user_data.get("prev_tone")
    if tone!=prev:
        prompts.append({"role":"system","content":f"Настроение: {tone}"})
        ctx.user_data["prev_tone"]=tone
    if "memory" in ctx.user_data:
        prompts.append({"role":"system","content":f"Напоминание: {ctx.user_data['memory']}"})
    prompts += h

    resp = openai.chat.completions.create(model="gpt-4o-mini", messages=prompts)
    answer = resp.choices[0].message.content

    h.append({"role":"assistant","content":answer})
    save_json(HISTORY_FILE, history_data)

    await update.message.reply_text(answer)
    return SESSION

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Сессия завершена.", reply_markup=main_keyboard)
    return ConversationHandler.END

# ─── HTTP-РОУТЫ ───────────────────────────────────────────────────────────────
# Telegram webhook
async def telegram_webhook(request: web.Request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# YooKassa webhook
async def ykassa_webhook(request: web.Request):
    body = await request.read()
    sig  = request.headers.get("Content-SHA256", "")
    expected = base64.b64encode(
        hmac.new(YKASSA_SECRET_KEY.encode(), body, hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(sig, expected):
        return web.Response(status=400, text="invalid signature")
    event = json.loads(body.decode())
    if event.get("event")=="payment.succeeded":
        md       = event["object"]["metadata"]
        order_id = md.get("order_id")
        uid      = orders.get(order_id)
        if uid:
            access_data[uid] = (datetime.now()+timedelta(days=30)).isoformat()
            save_json(ACCESS_FILE, access_data)
            await app.bot.send_message(int(uid), "✅ Оплата получена, доступ продлён на 30 дней.")
    return web.Response(text="OK")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    # создаём Telegram-приложение
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("🧠 Начать сессию"), start),
        ],
        states={SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(MessageHandler(filters.Regex("💳 Купить доступ"), buy))
    app.add_handler(CallbackQueryHandler(buy, pattern="^BUY$"))
    app.add_handler(MessageHandler(filters.Regex("❓ О боте"), about))

    # запускаем aiohttp-сервер
    web_app = web.Application()
    web_app.router.add_post("/telegram", telegram_webhook)
    web_app.router.add_post("/ykassa-webhook", ykassa_webhook)

    logging.info("Сервер запущен на порту %s", os.getenv("PORT", "10000"))
    web.run_app(web_app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
