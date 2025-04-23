import os
import openai
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
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

# ==== КЛАВИАТУРА ====
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🧠 Начать сессию")],
        [KeyboardButton("💳 Купить доступ"), KeyboardButton("❓ О боте")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

# ==== ОБРАБОТЧИК СТАРТА ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я — психологический бот.\nВыбери, с чего начнём:",
        reply_markup=main_keyboard
    )

# ==== СТАРТ СЕССИИ ====
async def begin_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["history"] = []
    await update.message.reply_text("Хорошо. Напиши, что у тебя внутри — и мы начнём.")
    return SESSION

# ==== СЕССИЯ GPT ====
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

# ==== СБРОС ====
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Сессия завершена. Нажми «🧠 Начать сессию», чтобы начать заново.")
    return ConversationHandler.END

# ==== ОПЛАТА ====
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 Платёжная система скоро будет подключена.\nПока что ты можешь использовать все функции бесплатно."
    )

# ==== О БОТЕ ====
async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Этот бот — твой психологический помощник.\nОн поможет разобраться в себе, задать важные вопросы и посмотреть на себя по-новому.\n\nВсе разговоры конфиденциальны. Ты. Без фильтра."
    )

# ==== ЗАПУСК ====
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("🧠 Начать сессию"), begin_session)],
        states={SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("💳 Купить доступ"), buy))
    app.add_handler(MessageHandler(filters.Regex("❓ О боте"), about))
    app.add_handler(CommandHandler("menu", start))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )
