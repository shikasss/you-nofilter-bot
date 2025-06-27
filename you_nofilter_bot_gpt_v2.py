import os
import json
import logging
from datetime import datetime, timedelta
from collections import Counter

import openai
from yookassa import Configuration, Payment
import hmac, hashlib, base64
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

# ─── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
Ты — внимательный и эмпатичный ИИ-психолог.  
Твоя задача — помочь человеку лучше понять себя, создать ощущение безопасности, тепла и уважительного диалога.

★ Стиль  
• Говори просто, по-человечески, избегая клише и сложных формулировок.  
• Будь «хамелеоном»: подстраивайся под стиль собеседника, но мягко и деликатно.  
• Сохраняй спокойствие и профессионализм, даже если человек взволнован.  
• Иногда можешь поделиться тем, как это бывает у других людей — не как совет, а как наблюдение.

★ Твоя речь  
• Избегай пустых фраз поддержки. Лучше покажи, что понял, и мягко веди к следующему шагу.  
• Помни, что у тебя есть «психологическая память» — ты уже общался с этим человеком раньше.  
  Не ссылайся на конкретные фразы, но строй ощущение узнавания.

Детекция «проблемных» сигналов  
Если в словах пользователя есть забота о своём самочувствии, тревога, упоминание «стресса»,  
«беспокойства», «боль», «утомление», «трудности» и т.п. — это **сигнал**, что речь идёт  
о проблеме, а не о банальных желаниях.

Изменение тактики при проблемах  
Когда обнаружен такой сигнал:  
  • **Не** задавай повторно одни и те же поверхностные вопросы.  
  • Предложи **копинг-стратегии**: «Как вы обычно справляетесь с этим?»,  
    «Какие ресурсы помогают вам чувствовать себя лучше?»  
  • Подскажи варианты: дыхательные упражнения, хобби, физическая активность, поддержка.

★ Если человек уходит в сторону  
1. Сначала — короткий отклик на его слова.  
2. Потом — мягкий вопрос, возвращающий к теме.  
3. Если он хочет сменить тему — поддержи и помоги связать это с его состоянием.

★ Запрещено  
• Осуждать  
• Говорить «успокойтесь»  
• Выставлять диагнозы прямо, но при запросе упомянуть, что диагноз ставят специалисты.

Обращайся к собеседнику на «вы» — нейтрально, с уважением.
"""

# ─── КОНФИГУРАЦИЯ ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL       = os.getenv("WEBHOOK_URL")       # URL для Telegram webhook
BASE_URL          = os.getenv("BASE_URL")          # e.g. https://your-app.onrender.com
YKASSA_SHOP_ID    = os.getenv("YKASSA_SHOP_ID")
YKASSA_SECRET_KEY = os.getenv("YKASSA_SECRET_KEY")

openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO)

# ─── Настройка ЮKassa ─────────────────────────────────────────────────────────
Configuration.account_id = YKASSA_SHOP_ID
Configuration.secret_key  = YKASSA_SECRET_KEY

# Секрет для верификации вебхука
YK_SECRET = YKASSA_SECRET_KEY.encode()

def verify_ykassa_signature(body: bytes, signature: str) -> bool:
    expected = base64.b64encode(
        hmac.new(YK_SECRET, body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)

# ─── ПУТИ К ФАЙЛАМ ─────────────────────────────────────────────────────────────
DATA_DIR     = "/mnt/data"
USED_FILE    = os.path.join(DATA_DIR, "used_data.json")
ACCESS_FILE  = os.path.join(DATA_DIR, "access_data.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history_data.json")

os.makedirs(DATA_DIR, exist_ok=True)

def load_json(path):
    if os.path.exists(path):
        return json.load(open(path, "r"))
    return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

used_data    = load_json(USED_FILE)
access_data  = load_json(ACCESS_FILE)
history_data = load_json(HISTORY_FILE)

# В памяти: order_id → user_id
orders = {}

# ─── КОНСТАНТЫ ─────────────────────────────────────────────────────────────────
SESSION    = 0
FREE_LIMIT = 10

main_keyboard = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🧠 Начать сессию")],
        [KeyboardButton("❓ О боте"), KeyboardButton("💳 Купить доступ")]
    ],
    resize_keyboard=True,
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

def extract_memory(history, limit=8):
    keywords = []
    for msg in history[-limit:]:
        if msg["role"] == "user":
            for w in msg["content"].lower().split():
                w = w.strip(",.!?\"«»")
                if len(w) > 3 and w not in {"это","просто","очень","такой","какой","когда"}:
                    keywords.append(w)
    common = [w for w,_ in Counter(keywords).most_common(3)]
    return ", ".join(common) if common else None

def detect_tone(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("ура","круто","рад","счаст")):    return "joy"
    if any(w in t for w in ("груст","тоск","плохо","депресс")): return "sadness"
    if any(w in t for w in ("злюсь","бесит","ненавиж","раздраж")): return "anger"
    if any(w in t for w in ("спокойно","норм","ладно","ок")):    return "calm"
    return "neutral"

# ─── ОБРАБОТЧИКИ БОТА ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    context.user_data["history"] = history_data.get(user_id, [])
    await update.message.reply_photo(
        photo="https://i.imgur.com/AH7eK7Z.png",
        caption="Ты. Без фильтра.\n\nМесто, где можно быть настоящим."
    )
    await update.message.reply_text("Напишите, что у вас на душе.", reply_markup=main_keyboard)
    return SESSION

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Этот бот — ваш ИИ-психолог. Помогает разобраться в себе, задаёт вопросы и предлагает идеи для поддержки."
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # различаем команду и callback
    if update.callback_query:
        await update.callback_query.answer()
        user_id = update.callback_query.from_user.id
        send = update.callback_query.edit_message_text
    else:
        user_id = update.effective_user.id
        send = update.message.reply_text

    order_id = f"access_{user_id}_{int(datetime.now().timestamp())}"
    payment = Payment.create({
        "amount":      {"value": "5.00", "currency": "RUB"},
        "confirmation":{"type":"redirect","return_url": BASE_URL},
        "capture":     True,
        "description": f"Месячный доступ YouNoFilter (пользователь {user_id})",
        "metadata":    {"user_id": str(user_id), "order_id": order_id}
    })
    orders[order_id] = str(user_id)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Оплатить 5 ₽ в месяц", url=payment.confirmation.confirmation_url)
    ]])
    text = (
        "🔒 Бесплатные сессии закончились.\n\n"
        "💳 Стоимость доступа: 5 ₽ в месяц.\n\n"
        "Нажмите кнопку ниже, чтобы оплатить и получить полный доступ."
    )
    await send(text, reply_markup=kb)

async def handle_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    msg     = update.message.text

    # проверка доступа и лимита
    if not has_access(user_id):
        used = used_data.get(user_id, 0)
        if used >= FREE_LIMIT:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Купить доступ", callback_data="BUY_ACCESS")
            ]])
            await update.message.reply_text(
                "🔒 Вы исчерпали бесплатные сообщения.\n\n"
                "💳 Стоимость доступа: 5 ₽ в месяц.\n\n"
                "Нажмите кнопку, чтобы получить ссылку на оплату.",
                reply_markup=kb
            )
            return SESSION
        used_data[user_id] = used + 1
        save_json(USED_FILE, used_data)
        left = FREE_LIMIT - used_data[user_id]
        await update.message.reply_text(f"🧭 Осталось бесплатных сообщений: {left}")

    # обновление истории
    h = context.user_data.setdefault("history", [])
    h.append({"role":"user","content": msg})
    history_data[user_id] = h
    save_json(HISTORY_FILE, history_data)

    # мягкая память
    memory = extract_memory(h)
    if memory:
        context.user_data["memory"] = memory

    # формируем prompt
    tone       = detect_tone(msg)
    sys_prompts = [{"role":"system","content":SYSTEM_PROMPT}]
    prev_tone   = context.user_data.get("prev_tone")
    if tone != prev_tone:
        sys_prompts.append({
            "role":"system",
            "content": f"Настроение пользователя: {tone}. Подстройтесь под него."
        })
        context.user_data["prev_tone"] = tone
    if "memory" in context.user_data:
        sys_prompts.append({
            "role":"system",
            "content": f"Ранее упоминалось: {context.user_data['memory']}."
        })

    prompt = sys_prompts + h
    resp   = openai.chat.completions.create(model="gpt-4o-mini", messages=prompt)
    answer = resp.choices[0].message.content

    # сохраняем ответ
    h.append({"role":"assistant","content": answer})
    save_json(HISTORY_FILE, history_data)

    await update.message.reply_text(answer)
    return SESSION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Сессия завершена.", reply_markup=main_keyboard)
    return ConversationHandler.END

# ─── WEBHOOK ЮKassa ────────────────────────────────────────────────────────────
async def ykassa_webhook(request: web.Request):
    body_bytes = await request.read()
    sig        = request.headers.get("Content-Sha256", "")
    if not verify_ykassa_signature(body_bytes, sig):
        return web.Response(status=400, text="invalid signature")
    event = json.loads(body_bytes.decode())
    if event.get("event") == "payment.succeeded":
        md       = event["object"]["metadata"]
        order_id = md.get("order_id")
        user_id  = orders.get(order_id)
        if user_id:
            access_data[user_id] = (datetime.now() + timedelta(days=30)).isoformat()
            save_json(ACCESS_FILE, access_data)
            await app.bot.send_message(
                chat_id=int(user_id),
                text="✅ Оплата получена! Доступ продлён на 30 дней."
            )
    return web.Response(text="ok")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("🧠 Начать сессию"), start),
        ],
        states={ SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session)] },
        fallbacks=[ CommandHandler("cancel", cancel) ],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(MessageHandler(filters.Regex("💳 Купить доступ"), buy))
    app.add_handler(CallbackQueryHandler(buy, pattern="^BUY_ACCESS$"))
    app.add_handler(MessageHandler(filters.Regex("❓ О боте"), about))

    # регистрируем endpoint для ЮKassa
    app._web_app.router.add_post("/ykassa-webhook", ykassa_webhook)

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )
