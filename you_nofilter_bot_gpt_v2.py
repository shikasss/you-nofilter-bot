
import os
import json
import openai
import logging
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

openai.api_key = OPENAI_API_KEY
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

SESSION, ASK_CONTACT = range(2)
YUMONEY_ACCOUNT = "410015497173415"
FREE_LIMIT = 10
USED_FILE = "used_messages.json"

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        KeyboardButton("🧠 Начать сессию"),
        KeyboardButton("❓ О боте")
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

def load_used_data():
    if os.path.exists(USED_FILE):
        with open(USED_FILE, "r") as f:
            return json.load(f)
    return {}

def save_used_data(data):
    with open(USED_FILE, "w") as f:
        json.dump(data, f)

used_data = load_used_data()

def has_access(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    access = context.application.bot_data.get("access_list", {})
    until = access.get(user_id)
    return until and until > datetime.now()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_photo(
        photo="https://i.imgur.com/AH7eK7Z.png",
        caption="Ты. Без фильтра.\n\nМесто, где можно быть настоящим."
    )
    context.user_data["history"] = []
    await update.message.reply_text(
        "Хорошо. Напиши, что у тебя внутри — и мы начнём.",
        reply_markup=main_keyboard
    )
    return SESSION

async def handle_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    context.user_data.setdefault("history", [])

    if not has_access(context, int(user_id)):
        count = used_data.get(user_id, 0)
        if count >= FREE_LIMIT:
            await update.message.reply_text(
                f"Ты использовал {FREE_LIMIT} бесплатных сообщений.\n\n"
                f"🔓 Хочешь, чтобы я связался с тобой и открыл доступ?",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton("Хочу"), KeyboardButton("Не надо")]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
            )
            return ASK_CONTACT
        else:
            used_data[user_id] = count + 1
            save_used_data(used_data)
            left = FREE_LIMIT - used_data[user_id]
            await update.message.reply_text(f"🧭 Осталось бесплатных сообщений: {left}")

    user_input = update.message.text
    context.user_data["history"].append({"role": "user", "content": user_input})

    prompt = [
        {"role": "system", "content": "Ты — эмпатичный психолог. Отвечай с теплом и участием. Помоги человеку понять себя. Не давай готовых советов, а мягко направляй."}
    ] + context.user_data["history"]

    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=prompt
    )

    reply = response.choices[0].message.content
    context.user_data["history"].append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)
    return SESSION

async def ask_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "без username"
    choice = update.message.text.strip().lower()

    if choice == "хочу":
        text = f"📨 Пользователь @{username} (ID: {user_id}) хочет, чтобы с ним связались."
        await context.bot.send_message(chat_id=ADMIN_ID, text=text)
        await update.message.reply_text("Спасибо, я передал твой запрос. Я свяжусь с тобой позже 🤝", reply_markup=main_keyboard)
    else:
        await update.message.reply_text("Хорошо, доступ останется ограничен. Если передумаешь — нажми /menu.", reply_markup=main_keyboard)

    return SESSION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Сессия завершена. Нажми «🧠 Начать сессию», чтобы начать заново.")
    return ConversationHandler.END

# async def buy(...) [ОТКЛЮЧЕНО]
    pass  # отключено

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Этот бот — твой психологический помощник. Он помогает разобраться в себе, задать важные вопросы и посмотреть на себя по-новому. Все разговоры конфиденциальны. Ты. Без фильтра."
    )

async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только администратор может выполнять эту команду.")
        return

    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text("Использование: /unlock <user_id> [дней]")
        return

    try:
        user_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
    except ValueError:
        await update.message.reply_text("ID и дни должны быть числами.")
        return

    access_list = context.application.bot_data.setdefault("access_list", {})
    access_list[user_id] = datetime.now() + timedelta(days=days)

    await update.message.reply_text(f"✅ Доступ выдан пользователю {user_id} на {days} дней.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("🧠 Начать сессию"), start)
        ],
        states={
            SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)],
            ASK_CONTACT: [MessageHandler(filters.TEXT, ask_contact_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("unlock", unlock))
        app.add_handler(MessageHandler(filters.Regex("❓ О боте"), about))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )
