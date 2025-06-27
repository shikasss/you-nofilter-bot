import os
import json
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from yookassa import Configuration, Payment, WebhookHandler

# ─── Настройки ────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
YKASSA_SHOP_ID   = os.getenv("YKASSA_SHOP_ID")
YKASSA_SECRET    = os.getenv("YKASSA_SECRET_KEY")
DATA_DIR         = "/mnt/data"
USED_FILE        = os.path.join(DATA_DIR, "used_data.json")
ACCESS_FILE      = os.path.join(DATA_DIR, "access_data.json")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# ─── ЮKassa ───────────────────────────────────────
Configuration.account_id = YKASSA_SHOP_ID
Configuration.secret_key  = YKASSA_SECRET

# ─── Простое хранение лимитов и доступов ──────────
def load_json(path):
    if os.path.exists(path):
        return json.load(open(path))
    return {}

def save_json(path, data):
    json.dump(data, open(path, "w"), indent=2)

used_data   = load_json(USED_FILE)
access_data = load_json(ACCESS_FILE)
orders      = {}  # in-memory map order_id → user_id

# ─── Создаём FastAPI и Telegram Application ───────
app = FastAPI()
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

# ─── Обработчики бота ─────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши что-нибудь, и я отвечу.")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    order_id = f"access_{user_id}_{int(datetime.now().timestamp())}"
    payment = Payment.create({
        "amount": {
            "value": "5.00",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": os.getenv("WEBHOOK_URL")  # например https://your-app.onrender.com/
        },
        "capture": True,
        "description": f"Доступ к YouNoFilter (пользователь {user_id})",
        "metadata": {"user_id": str(user_id), "order_id": order_id}
    })
    orders[order_id] = user_id
    await update.message.reply_text(
        f"Пожалуйста, оплатите по ссылке:\n{payment.confirmation.confirmation_url}"
    )

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("buy", buy))

# ─── Вебхук для Telegram ──────────────────────────
@app.post("/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

# ─── Вебхук для ЮKassa ────────────────────────────
@app.post("/ykassa-webhook")
async def ykassa_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("Content-Sha256", "")
    try:
        WebhookHandler.check_authenticity(body, signature)
    except Exception:
        raise HTTPException(400, "Invalid signature")

    event = await request.json()
    if event["event"] == "payment.succeeded":
        md = event["object"]["metadata"]
        order_id = md.get("order_id")
        user_id = orders.get(order_id)
        if user_id:
            access_data[str(user_id)] = (datetime.now() + timedelta(days=30)).isoformat()
            save_json(ACCESS_FILE, access_data)
            # уведомляем пользователя
            await bot_app.bot.send_message(
                chat_id=user_id,
                text="✅ Спасибо, оплата получена! Доступ продлён на 30 дней."
            )
    return {"ok": True}

# ─── Точка входа для Uvicorn ─────────────────────
# При запуске Render выполнит: `uvicorn main:app --host 0.0.0.0 --port $PORT`
