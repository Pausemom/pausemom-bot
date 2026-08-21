import logging
import random
import sqlite3
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
import asyncio

# ===== ПОЛУЧАЕМ ТОКЕН =====
API_TOKEN = os.getenv('BOT_TOKEN')

# ===== ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ =====
conn = sqlite3.connect('pause_bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    reg_date DATE,
    subscription_end DATE,
    total_sos INTEGER DEFAULT 0,
    no_cry_days INTEGER DEFAULT 0,
    last_cry_date DATE,
    free_phrases INTEGER DEFAULT 3,
    referral_code TEXT,
    referrer_id INTEGER
)''')
conn.commit()

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ===== СОСТОЯНИЯ =====
class Form(StatesGroup):
    waiting_for_voice = State()

# ===== КЛАВИАТУРЫ =====
def main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("🆘 SOS-Пауза"),
        KeyboardButton("📊 Моя статистика")
    )
    keyboard.add(
        KeyboardButton("📝 Сказать мягко"),
        KeyboardButton("🎧 Аудио-сказка")
    )
    keyboard.add(
        KeyboardButton("💎 Premium"),
        KeyboardButton("👥 Пригласить подругу")
    )
    keyboard.add(
        KeyboardButton("❤️ Поддержать проект"),
        KeyboardButton("📞 Помощь")
    )
    return keyboard

def sos_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    keyboard.add(
        KeyboardButton("🌬️ Дыхание 4-7-8"),
        KeyboardButton("💧 Умыться водой"),
        KeyboardButton("🤗 Обнять себя")
    )
    keyboard.add(
        KeyboardButton("🧘 Быстрая медитация"),
        KeyboardButton("🔙 Главное меню")
    )
    return keyboard

# ===== ПРОВЕРКА ПРЕМИУМ =====
def is_premium(user_id):
    cursor.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result and result[0]:
        end_date = datetime.strptime(result[0], '%Y-%m-%d')
        if end_date >= datetime.now().date():
            return True
    return False

def add_premium(user_id, days=30):
    end_date = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
    cursor.execute("UPDATE users SET subscription_end = ? WHERE user_id = ?", (end_date, user_id))
    conn.commit()

def generate_referral_code(user_id):
    import hashlib
    code = hashlib.md5(str(user_id).encode()).hexdigest()[:8]
    cursor.execute("UPDATE users SET referral_code = ? WHERE user_id = ?", (code, user_id))
    conn.commit()
    return code

# ===== СТАРТ =====
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Аноним"
    first_name = message.from_user.first_name or "Мама"

    args = message.get_args()
    if args:
        cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (args,))
        referrer = cursor.fetchone()
        if referrer and referrer[0] != user_id:
            cursor.execute("UPDATE users SET referrer_id = ? WHERE user_id = ?", (referrer[0], user_id))
            cursor.execute("UPDATE users SET no_cry_days = no_cry_days + 3 WHERE user_id = ?", (referrer[0],))
            conn.commit()
            await bot.send_message(referrer[0], "👏 По твоей ссылке пришла новая мама!")

    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date, free_phrases) VALUES (?, ?, ?, ?, 3)",
                   (user_id, username, first_name, datetime.now().strftime('%Y-%m-%d')))
    conn.commit()

    code = generate_referral_code(user_id)
    bot_username = (await bot.get_me()).username

    welcome_text = (
        f"👋 Привет, {first_name}!\n\n"
        "Я — **PauseBot** 🤖\n"
        "Я помогаю мамам сохранять спокойствие.\n\n"
        "⚡ **Как я работаю:**\n"
        "• Нажми «🆘 SOS-Пауза» когда закипаешь\n"
        "• Я дам упражнения, чтобы успокоиться\n"
        "• Используй «📝 Сказать мягко» для важных разговоров\n\n"
        f"👥 Твоя ссылка для подруг:\n"
        f"`https://t.me/{bot_username}?start={code}`\n\n"
        "💡 3 мягкие фразы в день бесплатно\n"
        "💎 Premium (299 ₽/мес) — безлимит и аудио-сказки"
    )
    await message.answer(welcome_text, reply_markup=main_keyboard())

# ===== SOS =====
@dp.message_handler(lambda message: message.text == "🆘 SOS-Пауза")
async def sos(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("UPDATE users SET total_sos = total_sos + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    await message.answer(
        "⏳ **Стоп! Ты сделала главное — остановилась.**\n\n"
        "Выбери, что поможет успокоиться:",
        reply_markup=sos_keyboard()
    )

# ===== ДЫХАНИЕ =====
@dp.message_handler(lambda message: message.text == "🌬️ Дыхание 4-7-8")
async def breathe(message: types.Message):
    await message.answer(
        "🌬️ **Дыхание 4-7-8**\n\n"
        "1️⃣ Вдохни носом — 4 секунды\n"
        "2️⃣ Задержи дыхание — 7 секунд\n"
        "3️⃣ Выдохни ртом — 8 секунд\n\n"
        "🔄 Повтори 5 раз"
    )
    await asyncio.sleep(5)
    await message.answer(
        "✅ Ты справилась!\n\n"
        "Ты — спокойная и любящая мама.",
        reply_markup=main_keyboard()
    )

# ===== УМЫТЬСЯ =====
@dp.message_handler(lambda message: message.text == "💧 Умыться водой")
async def wash(message: types.Message):
    await message.answer(
        "🚰 **Встань и подойди к раковине.**\n\n"
        "❄️ Умой лицо холодной водой 3 раза.\n"
        "Это запускает рефлекс успокоения.",
        reply_markup=main_keyboard()
    )
    await asyncio.sleep(5)
    await message.answer(
        "💧 Отлично!\n\n"
        "Теперь скажи: «Я хорошая мама»",
        reply_markup=main_keyboard()
    )

# ===== ОБНЯТЬ СЕБЯ =====
@dp.message_handler(lambda message: message.text == "🤗 Обнять себя")
async def self_hug(message: types.Message):
    await message.answer(
        "🤗 **Техника «Бабочка»**\n\n"
        "1. Скрести руки на груди\n"
        "2. Похлопай себя по плечам\n"
        "3. Продолжай 1 минуту",
        reply_markup=main_keyboard()
    )
    await asyncio.sleep(5)
    await message.answer(
        "🦋 Ты молодец! Нервная система успокоилась.",
        reply_markup=main_keyboard()
    )

# ===== МЕДИТАЦИЯ =====
@dp.message_handler(lambda message: message.text == "🧘 Быстрая медитация")
async def quick_meditation(message: types.Message):
    await message.answer(
        "🧘 **Медитация «Здесь и сейчас»**\n\n"
        "Сядь удобно, закрой глаза.\n"
        "Сделай 3 глубоких вдоха.\n"
        "Ответь себе:\n"
        "• Что я слышу? (3 звука)\n"
        "• Что я чувствую? (3 ощущения)",
        reply_markup=main_keyboard()
    )
    await asyncio.sleep(5)
    await message.answer(
        "🧘 Ты вернулась в «здесь и сейчас».\n"
        "Теперь ты готова действовать осознанно.",
        reply_markup=main_keyboard()
    )

# ===== СКАЗАТЬ МЯГКО =====
@dp.message_handler(lambda message: message.text == "📝 Сказать мягко")
async def soft_phrase(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    is_prem = is_premium(user_id)

    if not is_prem:
        cursor.execute("SELECT free_phrases FROM users WHERE user_id = ?", (user_id,))
        free = cursor.fetchone()[0]
        if free <= 0:
            await message.answer(
                "🔒 **Лимит исчерпан.**\n"
                "Оформи Premium (299 ₽/мес)",
                reply_markup=main_keyboard()
            )
            return
        cursor.execute("UPDATE users SET free_phrases = free_phrases - 1 WHERE user_id = ?", (user_id,))
        conn.commit()

    await message.answer(
        "📝 **Напиши, что хотела крикнуть ребенку.**\n"
        "Я переформулирую в мягкую фразу.",
        reply_markup=main_keyboard()
    )
    await Form.waiting_for_voice.set()

@dp.message_handler(state=Form.waiting_for_voice)
async def process_phrase(message: types.Message, state: FSMContext):
    user_text = message.text

    replacements = {
        "убери": "давай уберем вместе",
        "надоел": "я устала, мне нужна помощь",
        "не делай": "давай попробуем по-другому",
        "прекрати": "остановись, пожалуйста",
        "идиот": "ты мой любимый ребенок",
        "бесит": "меня это расстраивает"
    }

    soft_text = user_text
    for harsh, kind in replacements.items():
        if harsh in soft_text.lower():
            soft_text = soft_text.lower().replace(harsh, kind)

    if soft_text == user_text:
        soft_text = f"«{user_text}». Давай найдем решение вместе."

    await message.answer(
        f"💡 **Попробуй сказать так:**\n\n"
        f"«{soft_text.capitalize()}»\n\n"
        f"✅ Ребенок услышит тебя, а не испугается.",
        reply_markup=main_keyboard()
    )
    await state.finish()

# ===== АУДИО-СКАЗКА =====
@dp.message_handler(lambda message: message.text == "🎧 Аудио-сказка")
async def audio_fairy_tale(message: types.Message):
    user_id = message.from_user.id

    if not is_premium(user_id):
        await message.answer(
            "🔒 **Аудио-сказки доступны только по Premium.**\n\n"
            "💰 299 ₽/мес — нажми «💎 Premium»",
            reply_markup=main_keyboard()
        )
        return

    tales = [
        ("🌊 Сказка о морской звезде", "https://disk.yandex.ru/d/tale1"),
        ("🌲 Лесная медитация", "https://disk.yandex.ru/d/tale2")
    ]

    keyboard = InlineKeyboardMarkup(row_width=1)
    for name, url in tales:
        keyboard.add(InlineKeyboardButton(name, url=url))

    await message.answer(
        "🎧 **Выбери аудио-сказку:**",
        reply_markup=keyboard
    )

# ===== СТАТИСТИКА =====
@dp.message_handler(lambda message: message.text == "📊 Моя статистика")
async def stats(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT total_sos, no_cry_days, free_phrases FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result:
        sos_count, no_cry, free = result
        status = "💎 Premium" if is_premium(user_id) else "🆓 Бесплатный"

        await message.answer(
            f"📊 **Твоя статистика:**\n\n"
            f"Статус: {status}\n"
            f"🆘 SOS-пауз: {sos_count}\n"
            f"😌 Дней без криков: {no_cry}\n"
            f"📝 Осталось фраз: {free}",
            reply_markup=main_keyboard()
        )

# ===== PREMIUM =====
@dp.message_handler(lambda message: message.text == "💎 Premium")
async def premium_info(message: types.Message):
    user_id = message.from_user.id

    if is_premium(user_id):
        await message.answer(
            "✅ **У тебя уже есть Premium!**",
            reply_markup=main_keyboard()
        )
        return

    await message.answer(
        "💎 **Premium — 299 ₽/мес**\n\n"
        "✨ Что ты получаешь:\n"
        "✅ Безлимитные мягкие фразы\n"
        "✅ 5+ аудио-сказок\n"
        "✅ Голосовые упражнения\n\n"
        "📲 Оплати: https://pay.cloudtips.ru/p/12345\n"
        "Напиши код из 6 цифр",
        reply_markup=main_keyboard()
    )

# ===== ПРИГЛАСИТЬ ПОДРУГУ =====
@dp.message_handler(lambda message: message.text == "👥 Пригласить подругу")
async def referral(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
    code = cursor.fetchone()[0]
    bot_username = (await bot.get_me()).username

    await message.answer(
        f"👥 **Твоя реферальная ссылка:**\n"
        f"`https://t.me/{bot_username}?start={code}`\n\n"
        "🎁 Приведи подругу — получи +3 дня без криков!",
        reply_markup=main_keyboard()
    )

# ===== ПОДДЕРЖАТЬ =====
@dp.message_handler(lambda message: message.text == "❤️ Поддержать проект")
async def donate(message: types.Message):
    await message.answer(
        "❤️ **Спасибо за поддержку!**\n\n"
        "💳 https://pay.cloudtips.ru/p/12345",
        reply_markup=main_keyboard()
    )

# ===== ПОМОЩЬ =====
@dp.message_handler(lambda message: message.text == "📞 Помощь")
async def help_menu(message: types.Message):
    await message.answer(
        "📞 **Помощь**\n\n"
        "❓ Частые вопросы:\n"
        "• Как оплатить Premium? → Нажми «💎 Premium»\n"
        "• Не приходит код? → Напиши @PauseMomSupport\n\n"
        "📱 Поддержка: @PauseMomSupport",
        reply_markup=main_keyboard()
    )

# ===== НАЗАД =====
@dp.message_handler(lambda message: message.text == "🔙 Главное меню")
async def back_to_menu(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_keyboard())

# ===== ОБРАБОТЧИК КОДА ОПЛАТЫ =====
@dp.message_handler(content_types=['text'])
async def handle_payment_code(message: types.Message):
    if message.text.isdigit() and len(message.text) == 6:
        if message.text == "123456":
            add_premium(message.from_user.id, 30)
            await message.answer(
                "✅ **Premium активирован на 30 дней!**\n\n"
                "🌸 Приятного использования!",
                reply_markup=main_keyboard()
            )
        else:
            await message.answer(
                "❌ **Неверный код.** Попробуй еще раз.",
                reply_markup=main_keyboard()
            )

# ===== ЗАПУСК =====
if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
