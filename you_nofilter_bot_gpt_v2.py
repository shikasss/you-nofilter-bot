
import os
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

SESSION = range(1)
YUMONEY_ACCOUNT = "410015497173415"
FREE_LIMIT = 3

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🧠 Начать сессию")],
        [KeyboardButton("💳 Купить доступ"), KeyboardButton("❓ О боте")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_photo(
        photo="https://chat.openai.com/mnt/data/ii-psychologist-cover.png",
        caption="Ты. Без фильтра.\nМесто, где можно быть настоящим."
    )
    await update.message.reply_text("Выбери, с чего начнём:", reply_markup=main_keyboard)

async def begin_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["history"] = []
    context.user_data["used"] = 0
    await update.message.reply_text("Хорошо. Напиши, что у тебя внутри — и мы начнём.")
    return SESSION

def has_access(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    access = context.application.bot_data.get("access_list", {})
    until = access.get(user_id)
    return until and until > datetime.now()

async def handle_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.setdefault("used", 0)

    if not has_access(context, user_id):
        if context.user_data["used"] >= FREE_LIMIT:
            await update.message.reply_text(
                f"Ты достиг лимита бесплатных сообщений.\n"
                f"💳 Чтобы продолжить, отправь *$5 (доступ на 1 месяц)* на ЮMoney: `{YUMONEY_ACCOUNT}`\n"
                f"Затем пришли скрин оплаты — и тебе будет выдан доступ.",
                parse_mode="Markdown"
            )
            await update.message.reply_text("💳 После оплаты нажми /menu", reply_markup=main_keyboard)
            return SESSION
        else:
            context.user_data["used"] += 1

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

async def handle_post_limit_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if has_access(context, user_id):
        return

    if context.user_data.get("used", 0) < FREE_LIMIT:
        return

    try:
        caption = f"💳 Запрос на доступ от @{update.effective_user.username or 'без username'} (ID: {user_id})"
        await update.message.forward(chat_id=ADMIN_ID)
        await context.bot.send_message(chat_id=ADMIN_ID, text=caption)
    except Exception as e:
        logging.error(f"Ошибка при пересылке: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Сессия завершена. Нажми «🧠 Начать сессию», чтобы начать заново.")
    return ConversationHandler.END

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
     await update.message.reply_text(
                f"Ты достиг лимита бесплатных сообщений.\n"
                f"💳 Чтобы продолжить, отправь *$5 (доступ на 1 месяц)* на ЮMoney: `{YUMONEY_ACCOUNT}`\n"
                f"Затем пришли скрин оплаты — и тебе будет выдан доступ.",
                parse_mode="Markdown"
            )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Этот бот — твой психологический помощник.\nОн помогает разобраться в себе, задать важные вопросы и посмотреть на себя по-новому.\nВсе разговоры конфиденциальны. Ты. Без фильтра."
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
        entry_points=[MessageHandler(filters.Regex("🧠 Начать сессию"), begin_session)],
        states={SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("unlock", unlock))
    app.add_handler(MessageHandler(filters.Regex("💳 Купить доступ"), buy))
    app.add_handler(MessageHandler(filters.Regex("❓ О боте"), about))
    app.add_handler(MessageHandler(filters.ALL, handle_post_limit_media))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )
