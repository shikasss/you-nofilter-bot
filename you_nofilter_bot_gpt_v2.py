
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
from collections import Counter

SYSTEM_PROMPT = """
Ты — внимательный и эмпатичный ИИ-психолог.  
Твоя задача — помочь человеку лучше понять себя, создать ощущение безопасности, тепла и уважительного диалога.

★ Стиль  
• Говори просто, по-человечески, избегая клише и сложных формулировок.  
• Будь «хамелеоном»: подстраивайся под стиль собеседника, но мягко и деликатно.  
• Сохраняй спокойствие и профессионализм, даже если человек взволнован.  
• Иногда можешь поделиться тем, как это бывает у других людей — не как совет, а как наблюдение.

★ Твоя речь  
• Не соглашайся автоматически — лучше перефразируй, уточни, задай вопрос.  
• Избегай пустых фраз поддержки. Лучше покажи, что понял, и мягко веди к следующему шагу.  
• Помни, что у тебя есть «психологическая память» — ты уже общался с этим человеком раньше. Не ссылайся на конкретные фразы, но строй ощущение узнавания.

★ Если человек уходит в сторону  
1. Сначала — короткий отклик на его слова.  
2. Потом — мягкий вопрос, возвращающий к теме.  
3. Если он хочет сменить тему — поддержи, помоги связать это с его состоянием.

★ Запрещено  
• Осуждать  
• Говорить «успокойтесь»  
• Выставлять диагнозы прямо, но намекать можно и если пользователь спросит прямо как называется такой диагноз, оговорись, что диагнозы ставить не можешь, но в психологической практике есть такой диагноз

Обращайся к собеседнику на «вы» — нейтрально, с уважением.
"""

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

openai.api_key = OPENAI_API_KEY
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

SESSION, ASK_CONTACT = range(2)
YUMONEY_ACCOUNT = "410015497173415"
FREE_LIMIT = 10

DATA_DIR = "/mnt/data"
USED_FILE = os.path.join(DATA_DIR, "used_data.json")
ACCESS_FILE = os.path.join(DATA_DIR, "access_data.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history_data.json")

access_data = {}

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🧠 Начать сессию")],
        [KeyboardButton("❓ О боте")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

def load_used_data():
    global used_data
    if os.path.exists(USED_FILE):
        with open(USED_FILE, "r") as f:
            used_data = json.load(f)
    else:
        used_data = {}

def save_used_data(data):
    with open(USED_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_access_data():
    global access_data
    if os.path.exists(ACCESS_FILE):
        with open(ACCESS_FILE, "r") as f:
            access_data = json.load(f)
    else:
        access_data = {}

def save_access_data():
    global access_data
    with open(ACCESS_FILE, "w") as f:
        json.dump(access_data, f, indent=2)

load_used_data()

def has_access(user_id: int) -> bool:
    global access_data
    until_str = access_data.get(str(user_id))
    if not until_str:
        return False
    try:
        until = datetime.fromisoformat(until_str)
    except Exception:
        return False
    return until > datetime.now()

history_data = {}

def load_history_data():
    global history_data
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            history_data = json.load(f)
    else:
        history_data = {}

def save_history_data():
    with open(HISTORY_FILE, "w") as f:
        json.dump(history_data, f, indent=2)

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
    user_id = str(update.effective_user.id)
    context.user_data["history"] = history_data.get(user_id, [])
    return SESSION

async def handle_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    user_msg = update.message.text

    context.user_data.setdefault("history", [])

    if not has_access(int(user_id)):
        used = used_data.get(user_id, 0)

        if used >= FREE_LIMIT:
            await update.message.reply_text(
                f"Ты использовал {FREE_LIMIT} бесплатных сообщений.\n\n"
                "🔓 Хочешь, чтобы я связался с тобой и открыл доступ?",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton("Хочу"), KeyboardButton("Не надо")]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
            )
            return ASK_CONTACT

        used_data[user_id] = used + 1
        save_used_data(used_data)
        left = FREE_LIMIT - used_data[user_id]
        await update.message.reply_text(f"🧭 Осталось бесплатных сообщений: {left}")

    context.user_data["history"].append({"role": "user", "content": user_msg})
    history_data[user_id] = context.user_data["history"]
    save_history_data()

    # 🧠 Обновляем «мягкую память»
    memory = extract_memory(context.user_data["history"])
    if memory:
        context.user_data["memory"] = memory

    tone = detect_tone(user_msg)
    prev_tone = context.user_data.get("prev_tone")
    system_prompts = [{"role": "system", "content": SYSTEM_PROMPT}]

    if tone != prev_tone:
        system_prompts.append({
            "role": "system",
            "content": (
                f"У пользователя сейчас настроение: {tone}. "
                "Подстрой лексику и темп под это настроение, "
                "но сохраняй спокойствие и профессионализм."
            )
        })
        context.user_data["prev_tone"] = tone

    if context.user_data.get("memory"):
        system_prompts.append({
            "role": "system",
            "content": f"Ранее пользователь упоминал: {context.user_data['memory']}. Учитывай это, если поможет лучше понять контекст."
        })

    prompt = system_prompts + context.user_data["history"]

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

def extract_memory(history, limit=8):
    """Извлекает ключевые слова из последних сообщений пользователя."""
    keywords = []
    for msg in history[-limit:]:
        if msg["role"] == "user":
            content = msg["content"].lower()
            for word in content.split():
                w = word.strip(",.!?\"«»")
                if len(w) > 3 and w not in {"это", "просто", "очень", "такой", "какой", "когда"}:
                    keywords.append(w)
    common = [w for w, _ in Counter(keywords).most_common(3)]
    return ", ".join(common) if common else None

def detect_tone(text: str) -> str:
    """Грубо определяем настроение по ключевым словам."""
    t = text.lower()

    joy     = {"ура", "круто", "супер", "рад", "счастл"}
    sadness = {"груст", "тоск", "плохо", "тяжело", "депресс"}
    anger   = {"бесит", "злюсь", "ненавиж", "раздраж"}
    calm    = {"спокойно", "норм", "ладно", "ок"}

    if any(w in t for w in joy):      return "joy"
    if any(w in t for w in sadness):  return "sadness"
    if any(w in t for w in anger):    return "anger"
    if any(w in t for w in calm):     return "calm"
    return "neutral"
# ────────────────────────────────────────────────────────────────

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
    global access_data
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
    access_data[str(user_id)] = (datetime.now() + timedelta(days=days)).isoformat()
    save_access_data()
    access_list = context.application.bot_data.setdefault("access_list", {})
    access_list[user_id] = datetime.now() + timedelta(days=days)

    await update.message.reply_text(f"✅ Доступ выдан пользователю {user_id} на {days} дней.")

if __name__ == "__main__":
    load_access_data()
    load_history_data()
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("unlock", unlock))
    app.add_handler(MessageHandler(filters.Regex("❓ О боте"), about))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )
