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

# ===== ПОЛУЧАЕМ ТОКЕН ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =====
# На Bothost токен нужно добавить в разделе "Переменные окружения"
API_TOKEN = os.getenv('BOT_TOKEN')  # Не забудь добавить переменную!

# ===== ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ =====
# На Bothost база данных будет в папке с ботом
conn = sqlite3.connect('pause_bot.db', check_same_thread=False)
cursor = conn.cursor()

# Создаем таблицы
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

# ===== ГЕНЕРАЦИЯ РЕФЕРАЛЬНОЙ ССЫЛКИ =====
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
    
    # Проверяем реферальный код
    args = message.get_args()
    if args:
        cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (args,))
        referrer = cursor.fetchone()
        if referrer and referrer[0] != user_id:
            cursor.execute("UPDATE users SET referrer_id = ? WHERE user_id = ?", (referrer[0], user_id))
            cursor.execute("UPDATE users SET no_cry_days = no_cry_days + 3 WHERE user_id = ?", (referrer[0],))
            conn.commit()
            await bot.send_message(referrer[0], "👏 По твоей ссылке пришла новая мама! Ты получаешь +3 дня к статистике без криков!")
    
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date, free_phrases) VALUES (?, ?, ?, ?, 3)",
                   (user_id, username, first_name, datetime.now().strftime('%Y-%m-%d')))
    conn.commit()
    
    # Генерируем реферальный код
    code = generate_referral_code(user_id)
    
    welcome_text = (
        f"👋 Привет, {first_name}!\n\n"
        "Я — PauseBot 🤖\n"
        "Я создан, чтобы помочь тебе сохранять спокойствие с детьми.\n\n"
        "⚡ Как я работаю:\n"
        "• Чувствуешь, что закипаешь → нажми «🆘 SOS-Пауза»\n"
        "• Я дам тебе способы успокоиться за 1 минуту\n"
        "• Хочешь сказать ребенку что-то важное → используй «📝 Сказать мягко»\n\n"
        "💡 Бесплатно: 3 мягкие фразы в день.\n"
        "💎 Premium (299 ₽/мес): безлимит, аудио-сказки, голосовые упражнения.\n\n"
        f"👥 Твоя реферальная ссылка:\n"
        f"https://t.me/{ (await bot.get_me()).username }?start={code}\n"
        "Пригласи подругу — получи бонусы!\n\n"
        "Начнем?"
    )
    await message.answer(welcome_text, reply_markup=main_keyboard())

# ===== SOS-ПАУЗА =====
@dp.message_handler(lambda message: message.text == "🆘 SOS-Пауза")
async def sos(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("UPDATE users SET total_sos = total_sos + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    
    await message.answer(
        "⏳ Стоп! Ты сделала главное — остановилась.\n\n"
        "Теперь выбери, что поможет тебе успокоиться:",
        reply_markup=sos_keyboard()
    )

# ===== ДЫХАНИЕ 4-7-8 =====
@dp.message_handler(lambda message: message.text == "🌬️ Дыхание 4-7-8")
async def breathe(message: types.Message):
    await message.answer(
        "🌬️ Дыхание 4-7-8 (техника доктора Вейля)\n\n"
        "1️⃣ Вдохни носом — 4 секунды\n"
        "2️⃣ Задержи дыхание — 7 секунд\n"
        "3️⃣ Выдохни ртом — 8 секунд\n\n"
        "🔄 Повтори 5 раз.\n\n"
        "Это снижает уровень кортизола (гормона стресса)."
    )
    
    # Таймер выполнения
    for i in range(5, 0, -1):
        await asyncio.sleep(20)
        await message.answer(f"✅ {6-i} из 5 подходов")
    
    await message.answer(
        "✅ Ты справилась!\n\n"
        "Теперь твой мозг получил сигнал расслабления.\n"
        "Ты — спокойная и любящая мама.",
        reply_markup=main_keyboard()
    )

# ===== УМЫТЬСЯ ВОДОЙ =====
@dp.message_handler(lambda message: message.text == "💧 Умыться водой")
async def wash(message: types.Message):
    await message.answer(
        "🚰 Встань и подойди к раковине.\n\n"
        "❄️ Умой лицо холодной водой 3 раза.\n"
        "Это запускает «нырятельный рефлекс» — пульс замедляется.\n\n"
        "🔄 Сделай это прямо сейчас. Я подожду 30 секунд.",
        reply_markup=main_keyboard()
    )
    
    for i in range(30, 0, -5):
        await asyncio.sleep(5)
        await message.answer(f"⏳ {i} сек...")
    
    await message.answer(
        "💧 Отлично!\n\n"
        "Теперь посмотри в зеркало и скажи:\n"
        "«Я справлюсь. Я хорошая мама.»",
        reply_markup=main_keyboard()
    )

# ===== ОБНЯТЬ СЕБЯ =====
@dp.message_handler(lambda message: message.text == "🤗 Обнять себя")
async def self_hug(message: types.Message):
    await message.answer(
        "🤗 Техника «Бабочка»\n\n"
        "1. Скрести руки на груди\n"
        "2. Положи ладони на плечи\n"
        "3. Похлопай себя по плечам попеременно (левая-правая)\n"
        "4. Продолжай 1 минуту\n\n"
        "Это успокаивает нервную систему и снижает тревогу.",
        reply_markup=main_keyboard()
    )
    
    await asyncio.sleep(60)
    await message.answer(
        "🦋 Ты молодец!\n\n"
        "Это упражнение используется в EMDR-терапии.\n"
        "Ты только что помогла своей нервной системе восстановиться.",
        reply_markup=main_keyboard()
    )
# ===== БЫСТРАЯ МЕДИТАЦИЯ =====
@dp.message_handler(lambda message: message.text == "🧘 Быстрая медитация")
async def quick_meditation(message: types.Message):
    await message.answer(
        "🧘 Медитация «Здесь и сейчас»\n\n"
        "Сядь удобно, закрой глаза.\n"
        "Сделай 3 глубоких вдоха.\n"
        "Теперь ответь себе:\n"
        "• Что я слышу? (3 звука)\n"
        "• Что я чувствую? (3 ощущения)\n"
        "• Что я вижу с закрытыми глазами? (3 образа)\n\n"
        "Это вернет тебя в настоящий момент.",
        reply_markup=main_keyboard()
    )
    
    await asyncio.sleep(60)
    await message.answer(
        "🧘 Ты вернулась в «здесь и сейчас».\n\n"
        "Теперь ты готова действовать осознанно, а не на эмоциях.",
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
                "🔒 Лимит бесплатных фраз исчерпан.\n\n"
                "У тебя было 3 фразы на сегодня.\n"
                "Оформи Premium (299 ₽/мес) — и пользуйся без ограничений.",
                reply_markup=main_keyboard()
            )
            return
        cursor.execute("UPDATE users SET free_phrases = free_phrases - 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    
    await message.answer(
        "📝 Напиши одним предложением,\n"
        "что ты хотела крикнуть ребенку.\n\n"
        "Я переформулирую это в мягкую и добрую фразу.",
        reply_markup=main_keyboard()
    )
    await Form.waiting_for_voice.set()

@dp.message_handler(state=Form.waiting_for_voice)
async def process_phrase(message: types.Message, state: FSMContext):
    user_text = message.text
    
    # База мягких замен
    replacements = {
        "убери": "давай уберем вместе",
        "надоел": "я устала, мне нужна твоя помощь",
        "не делай": "давай попробуем по-другому",
        "прекрати": "остановись, пожалуйста",
        "идиот": "ты мой любимый ребенок",
        "дурак": "ты очень умный, просто ошибаешься",
        "уйди": "давай немного отдохнем друг от друга",
        "бесит": "меня это расстраивает",
        "тупой": "ты способный, просто не понял",
        "заткнись": "давай помолчим немного",
        "сколько можно": "давай договоримся",
        "опять": "давай попробуем снова вместе"
    }
    
    soft_text = user_text
    for harsh, kind in replacements.items():
        if harsh in soft_text.lower():
            soft_text = soft_text.lower().replace(harsh, kind)
    
    if soft_text == user_text:
        soft_text = f"«{user_text}». Давай найдем решение вместе."
    
    await message.answer(
        f"💡 Попробуй сказать так:\n\n"
        f"«{soft_text.capitalize()}»\n\n"
        f"✅ Это звучит мягко, и ребенок услышит тебя, а не испугается.\n\n"
        f"📋 Скопируй фразу и скажи ребенку.",
        reply_markup=main_keyboard()
    )
    await state.finish()

# ===== АУДИО-СКАЗКА =====
@dp.message_handler(lambda message: message.text == "🎧 Аудио-сказка")
async def audio_fairy_tale(message: types.Message):
    user_id = message.from_user.id
    
    if not is_premium(user_id):
        await message.answer(
            "🔒 Аудио-сказки доступны только по Premium.\n\n"
            "🎁 Что ты получишь:\n"
            "✅ 10 расслабляющих аудио-сказок\n"
            "✅ Голосовые дыхательные упражнения\n"
            "✅ Безлимитные мягкие фразы\n"
            "✅ Статистику эмоциональных триггеров\n\n"
            "💰 299 ₽/мес — нажми «💎 Premium»",
            reply_markup=main_keyboard()
        )
        return
    
    # Ссылки на Яндекс.Диск (замени на свои)
    tales = [
        ("🌊 Сказка о морской звезде", "https://disk.yandex.ru/d/tale1"),
        ("🌲 Лесная медитация", "https://disk.yandex.ru/d/tale2"),
        ("☀️ Утренний свет", "https://disk.yandex.ru/d/tale3"),
        ("🌟 Ночная колыбельная", "https://disk.yandex.ru/d/tale4"),
        ("🌸 Весеннее пробуждение", "https://disk.yandex.ru/d/tale5")
    ]
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for name, url in tales:
        keyboard.add(InlineKeyboardButton(name, url=url))
    
    await message.answer(
        "🎧 Выбери аудио-сказку для релаксации:",
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
        is_prem = is_premium(user_id)
        
        status = "💎 Premium" if is_prem else "🆓 Бесплатный"
        prem_end = ""
        if is_prem:
            cursor.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
            end = cursor.fetchone()[0]
            prem_end = f"\n📅 Действует до: {end}"
        
        await message.answer(
            f"📊 Твоя статистика:\n\n"
            f"Статус: {status}{prem_end}\n"
            f"🆘 SOS-пауз: {sos_count}\n"
            f"😌 Дней без криков: {no_cry}\n"
            f"📝 Осталось мягких фраз: {free}\n\n"
            f"{'🏆 Ты супер! Продолжай!' if no_cry > 3 else '💪 Каждый день без крика — это маленькая победа!'}",
            reply_markup=main_keyboard()
        )

# ===== PREMIUM =====
@dp.message_handler(lambda message: message.text == "💎 Premium")
async def premium_info(message: types.Message):
    user_id = message.from_user.id
    
    if is_premium(user_id):
        cursor.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
        end_date = cursor.fetchone()[0]
        await message.answer(
            f"✅ У тебя уже есть Premium!\n\n"
            f"📅 Действует до: {end_date}\n\n"
            "Пользуйся всеми функциями без ограничений.",
            reply_markup=main_keyboard()
        )
        return
    
    await message.answer(
        "💎 Premium-подписка — 299 ₽/мес\n\n"
        "✨ Что ты получаешь:\n"
        "✅ Безлимитные мягкие фразы\n"
        "✅ 5+ аудио-сказок для релаксации\n"
        "✅ Голосовые инструкции для дыхания\n"
        "✅ Статистика эмоциональных триггеров\n\n"
        "📲 Как оплатить:\n"
        "1. Перейди по ссылке: https://pay.cloudtips.ru/p/12345\n"
        "2. Оплати 299 ₽ (СБП, карта, Qiwi)\n"
        "3. Напиши мне код из 6 цифр (придет на почту)\n\n"
        "💳 Или поддержать донатом: ❤️ Поддержать проект",
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
        "👥 Пригласи подругу и получи бонус!\n\n"
        "🔗 Твоя реферальная ссылка:\n"
        f"https://t.me/{bot_username}?start={code}\n\n"
        "🎁 Что получишь:\n"
        "• Подруга переходит по ссылке\n"
        "• Вы обе получаете +3 дня без криков\n"
        "• И ты получаешь +1 бесплатную мягкую фразу\n\n"
        "📋 Нажми на ссылку выше, чтобы скопировать."
    )

# ===== ПОДДЕРЖАТЬ ПРОЕКТ =====
@dp.message_handler(lambda message: message.text == "❤️ Поддержать проект")
async def donate(message: types.Message):
    await message.answer(
"❤️ Спасибо, что хочешь поддержать!\n\n"
        "Проект существует только благодаря вам.\n\n"
        "💳 Отправить любую сумму:\n"
        "https://pay.cloudtips.ru/p/12345\n\n"
        "💰 Способы: СБП, карты Visa/Mastercard/Mir, Qiwi\n\n"
        "Спасибо от всего сердца! 🙏",
        reply_markup=main_keyboard()
    )

# ===== ПОМОЩЬ =====
@dp.message_handler(lambda message: message.text == "📞 Помощь")
async def help_menu(message: types.Message):
    await message.answer(
        "📞 Помощь и поддержка\n\n"
        "❓ Частые вопросы:\n"
        "• Как оплатить Premium? → Нажми «💎 Premium»\n"
        "• Не приходит код? → Проверь почту или напиши нам\n"
        "• Хочу предложить идею → Пиши!\n\n"
        "📱 Связь с нами:\n"
        "• Поддержка: @PauseMomSupport\n"
        "• Почта: pausebot@yandex.ru\n\n"
        "⏰ Ответ в течение 24 часов",
        reply_markup=main_keyboard()
    )

# ===== НАЗАД =====
@dp.message_handler(lambda message: message.text == "🔙 Главное меню")
async def back_to_menu(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_keyboard())

# ===== ОБРАБОТЧИК КОДА ОПЛАТЫ =====
@dp.message_handler(content_types=['text'])
async def handle_payment_code(message: types.Message):
    user_id = message.from_user.id
    
    # Если пользователь прислал код из 6 цифр
    if message.text.isdigit() and len(message.text) == 6:
        # В реальности нужно проверять код в базе платежей
        if message.text == "123456":  # Замени на свой код!
            add_premium(user_id, 30)
            await message.answer(
                "✅ Поздравляю! Premium активирован на 30 дней!\n\n"
                "🎉 Тебе доступно всё:\n"
                "✅ Безлимитные мягкие фразы\n"
                "✅ 10 аудио-сказок\n"
                "✅ Голосовые упражнения\n\n"
                "🌸 Приятного использования! Ты заслужила это.",
                reply_markup=main_keyboard()
            )
        else:
            await message.answer(
                "❌ Неверный код. Проверь почту и попробуй еще раз.\n\n"
                "Если оплатила, но код не пришел — напиши @PauseMomSupport",
                reply_markup=main_keyboard()
            )
          "Added bot code"

# ===== ЗАПУСК =====
if name == 'main':
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
