import os
import openai
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ==== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

openai.api_key = OPENAI_API_KEY

# ==== ЛОГИ ====
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# ==== СТАДИИ СЕССИИ ====
SESSION = range(1)

# ==== СТАРТ ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["history"] = []
    await update.message.reply_text(
        "Ты. Без фильтра.\n\nМесто, где можно быть настоящим.\n\nНапиши, что у тебя внутри — и мы начнём."
    )
    return SESSION

# ==== ОБРАБОТКА СООБЩЕНИЙ ====
async def handle_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# ==== ОТМЕНА / СБРОС ====
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Сессия завершена. Напиши /start, чтобы начать заново.")
    return ConversationHandler.END

# ==== ГЛАВНЫЙ БЛОК ЗАПУСКА ====
async def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)

    # Настройка webhook
    await app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )

# ==== ЗАПУСК ====
if __name__ == "__main__":
    import asyncio

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)

    # Запуск webhook напрямую
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )
