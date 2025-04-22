
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import openai
import logging
import os

# ==== ВСТАВЬ СВОИ КЛЮЧИ ====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# ==== ЛОГИ ====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ==== КОНСТАНТЫ ====
SESSION = range(1)

# ==== СТАРТ ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["history"] = []
    await update.message.reply_text(
        "Ты. Без фильтра.\n\nМесто, где можно быть настоящим.\n\nНапиши, что у тебя внутри — и мы начнём."
    )
    return SESSION

# ==== СЕССИЯ С GPT ====
async def handle_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    context.user_data["history"].append({"role": "user", "content": user_input})

    prompt = [
        {"role": "system", "content": "Ты — эмпатичный психолог. Отвечай с теплом и участием. Помоги человеку понять себя. Не давай готовых советов, а мягко направляй."}
    ] + context.user_data["history"]

    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=prompt
    )

    reply = response.choices[0].message.content
    context.user_data["history"].append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)
    return SESSION

# ==== СБРОС ====
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Сессия завершена. Напиши /start, чтобы начать заново.")
    return ConversationHandler.END

# ==== ЗАПУСК ====
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

app.add_handler(conv_handler)
print("Бот запущен.")
app.run_polling()
