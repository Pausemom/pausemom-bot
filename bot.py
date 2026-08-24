import asyncio
import hashlib
import os
from datetime import datetime, timedelta, date
from urllib.parse import urlencode

import requests
import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# Загружаем секреты из файла .env
load_dotenv()

# ================= НАСТРОЙКИ =================
API_TOKEN = os.getenv('BOT_TOKEN')
ADMINS = [int(os.getenv('ADMIN_ID', 0))]

DB_PATH = "pause_bot.db"

# Ссылки на юридические документы (замените на реальные)
POLICY_URL = "https://docs.google.com/document/d/ВАША_ССЫЛКА_ПОЛИТИКА/edit"
OFFER_URL = "https://docs.google.com/document/d/ВАША_ССЫЛКА_ОФЕРТА/edit"
CONSENT_URL = "https://docs.google.com/document/d/ВАША_ССЫЛКА_СОГЛАСИЕ/edit"

# ================= РОБОКАССА =================
ROBOKASSA_LOGIN = os.getenv('ROBOKASSA_LOGIN')
ROBOKASSA_PASSWORD1 = os.getenv('ROBOKASSA_PASSWORD1')
ROBOKASSA_PASSWORD2 = os.getenv('ROBOKASSA_PASSWORD2')
ROBOKASSA_TEST_MODE = os.getenv('ROBOKASSA_TEST_MODE', 'True').lower() == 'true'

ROBOKASSA_URL = 'https://auth.robokassa.ru/Merchant/Index.aspx'
ROBOKASSA_API_URL = 'https://auth.robokassa.ru/Merchant/WebService/Service.asmx/PaymentState'

# Инициализация
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
router = Router()
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================= БАЗА ДАННЫХ =================
async def create_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            reg_date DATE,
            subscription_end DATE,
            total_sos INTEGER DEFAULT 0,
            no_cry_days INTEGER DEFAULT 0,
            last_cry_date DATE,
            referral_code TEXT,
            referrer_id INTEGER,
            last_affirmation DATE,
            agreed_to_terms BOOLEAN DEFAULT 0,
            last_invoice_id TEXT
        )''')
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()

async def is_premium(user_id):
    if user_id in ADMINS:
        return True
    user = await get_user(user_id)
    if user and user[4]:  # subscription_end
        try:
            end_date = datetime.strptime(user[4], '%Y-%m-%d').date()
            if end_date >= date.today():
                return True
        except:
            pass
    return False

async def add_premium(user_id, days=30):
    end_date = (date.today() + timedelta(days=days)).strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET subscription_end = ? WHERE user_id = ?", (end_date, user_id))
        await db.commit()

async def generate_referral_code(user_id):
    code = hashlib.md5(str(user_id).encode()).hexdigest()[:8]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET referral_code = ? WHERE user_id = ?", (code, user_id))
        await db.commit()
    return code

async def has_agreed_to_terms(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT agreed_to_terms FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
    if result:
        return result[0] == 1
    return False

async def set_agreed_to_terms(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET agreed_to_terms = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

# ================= ФУНКЦИИ РОБОКАССЫ =================
async def generate_payment_link_and_save(user_id: int, amount: float = 999) -> str:
    """
    Генерирует ссылку на оплату и сохраняет InvoiceID в базу.
    """
    inv_id = f"pm_{user_id}_{int(datetime.now().timestamp())}"

    signature = hashlib.md5(
        f"{ROBOKASSA_LOGIN}:{amount:.2f}:{inv_id}:{ROBOKASSA_PASSWORD1}:Shp_user={user_id}".encode()
    ).hexdigest()

    params = {
        'MerchantLogin': ROBOKASSA_LOGIN,
        'OutSum': f"{amount:.2f}",
        'InvoiceID': inv_id,
        'Description': 'Premium подписка на 30 дней',
        'SignatureValue': signature,
        'IsTest': '1' if ROBOKASSA_TEST_MODE else '0',
        'Shp_user': str(user_id),
        'Culture': 'ru'
    }

    payment_url = f"{ROBOKASSA_URL}?{urlencode(params)}"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_invoice_id = ? WHERE user_id = ?", (inv_id, user_id))
        await db.commit()

    return payment_url

def check_payment(inv_id: str) -> bool:
    """
    Проверяет статус платежа по InvoiceID через API Робокассы.
    Возвращает True, если оплата прошла успешно (StateCode=100).
    """
    if not ROBOKASSA_LOGIN or not ROBOKASSA_PASSWORD2:
        return False

    signature = hashlib.md5(
        f"{ROBOKASSA_LOGIN}:{inv_id}:{ROBOKASSA_PASSWORD2}".encode()
    ).hexdigest()

    params = {
        'MerchantLogin': ROBOKASSA_LOGIN,
        'InvoiceID': inv_id,
        'Signature': signature
    }

    try:
        response = requests.get(ROBOKASSA_API_URL, params=params, timeout=10)
        if response.status_code == 200:
            if 'StateCode="100"' in response.text:
                return True
    except Exception as e:
        print(f"Ошибка проверки платежа: {e}")
    return False

# ================= КЛАВИАТУРЫ =================
def main_keyboard(user_id):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆘 SOS-Пауза"), KeyboardButton(text="💎 Premium")],
            [KeyboardButton(text="👥 Пригласить подругу"), KeyboardButton(text="💝 Нужные слова для мамы")],
            [KeyboardButton(text="🧸 Техники для малышей"), KeyboardButton(text="🤝 Восстановить контакт")],
            [KeyboardButton(text="📚 Общие рекомендации по возрасту"), KeyboardButton(text="📞 Помощь")],
            [KeyboardButton(text="🌅 Аффирмация дня")]
        ],
        resize_keyboard=True,
        row_width=2
    )

def sos_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌬️ Дыхание 4-7-8", callback_data="sos_breath"),
             InlineKeyboardButton(text="🧘 Осознанное дыхание", callback_data="sos_mindful")],
            [InlineKeyboardButton(text="👀 5-4-3-2-1", callback_data="sos_54321"),
             InlineKeyboardButton(text="🤗 Обнять себя", callback_data="sos_hug")],
            [InlineKeyboardButton(text="💧 Умыться водой", callback_data="sos_wash"),
             InlineKeyboardButton(text="🦶 Стойка на ногах", callback_data="sos_standing")],
            [InlineKeyboardButton(text="🧠 Сканирование тела", callback_data="sos_bodyscan"),
             InlineKeyboardButton(text="☀️ Луч света", callback_data="sos_light")],
            [InlineKeyboardButton(text="🌊 Волна дыхания", callback_data="sos_wave"),
             InlineKeyboardButton(text="💭 Наблюдатель", callback_data="sos_observer")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_main_from_sos")]
        ]
    )

# ================= СОСТОЯНИЯ =================
class Form(StatesGroup):
    restore_step1 = State()
    restore_step2 = State()
    restore_step3 = State()

# ================= ДАННЫЕ =================
DISCLAIMER = (
    "📋 <b>О боте PauseMomBot</b>\n\n"
    "PauseMomBot — это информационный помощник для родителей. "
    "Все техники и рекомендации носят ознакомительный и общеразвивающий характер.\n\n"
    "⚠️ <b>Важно:</b>\n"
    "• Бот не является медицинским или психотерапевтическим инструментом\n"
    "• Бот не ставит диагнозы и не назначает лечение\n"
    "• Бот не заменяет профессиональную помощь психолога или врача\n\n"
    "📱 Поддержка: @PauseMomSupport_bot"
)

def get_kids_techniques(age_group):
    techniques = {
        "1_3": {
            "title": "👶 Техники для детей 1-3 года",
            "tips": [
                "🤗 <b>Техника «Обними-меня»</b>\n\nКогда ребёнок расстроен — протяни ему руки.",
                "🔄 <b>Техника «Переключение»</b>\n\nОтвлеки внимание ребёнка на что-то интересное.",
                "🧸 <b>Техника «Игрушка-мирилка»</b>\n\nВозьми его любимую игрушку и помиритесь через неё.",
                "📖 <b>Техника «Сказка про меня»</b>\n\nРасскажи историю о зверюшке, который помирился с мамой."
            ]
        },
        "4_6": {
            "title": "🧒 Техники для детей 4-6 лет",
            "tips": [
                "🤗 <b>Техника «Обними-меня»</b>\n\nСядь на уровень ребёнка и протяни руки.",
                "🎨 <b>Техника «Рисуем обиду»</b>\n\nНарисуйте злость и порвите рисунок.",
                "🎭 <b>Техника «Игра в чувства»</b>\n\nНазывай чувства, ребёнок показывает их лицом.",
                "🌊 <b>Техника «Дыхание вместе»</b>\n\nПодышите вместе как волны океана.",
                "🧩 <b>Техника «Пять минут вместе»</b>\n\nПроведи 5 минут только с ребёнком."
            ]
        },
        "7_9": {
            "title": "👦 Техники для детей 7-9 лет",
            "tips": [
                "🤗 <b>Техника «Обними-меня»</b>\n\nСпроси: «Обняться или поговорить?»",
                "🎨 <b>Техника «Рисуем обиду»</b>\n\nНарисуй свои чувства.",
                "🎭 <b>Техника «Игра в чувства»</b>\n\nПоиграйте в угадывание эмоций.",
                "🧩 <b>Техника «Стоп-фраза»</b>\n\nПридумайте слово, которое останавливает ссору.",
                "🔄 <b>Техника «Круг благодарности»</b>\n\nНапишите друг другу, за что вы благодарны."
            ]
        },
        "10_12": {
            "title": "👦 Техники для детей 10-12 лет",
            "tips": [
                "💬 <b>Техника «Я-сообщение +»</b>\n\n«Я чувствую... Потому что... Я знаю, что ты... Давай...»\n\nПример: «Я чувствую усталость, потому что у меня был тяжёлый день. Я знаю, что ты тоже устал. Давай вместе почитаем книгу и обнимемся?»",
                "✨ <b>Техника «Светящиеся руки»</b>\n\n1️⃣ Потри ладони.\n2️⃣ Представь тёплый золотистый свет.\n3️⃣ Положи руку на плечо ребёнка.\n4️⃣ Скажи: «Я рядом. Я тебя люблю».",
                "☁️ <b>Техника «Облако мира»</b>\n\n1️⃣ Закрой глаза.\n2️⃣ Представь пушистое облако.\n3️⃣ Положи туда обиду и гнев.\n4️⃣ Смотри, как облако уплывает."
            ]
        }
    }
    return techniques.get(age_group, techniques["4_6"])

def get_daily_affirmation():
    affirmations = [
        "Я — хорошая мама, и я делаю всё, что в моих силах.",
        "Сегодня я выбираю спокойствие и любовь.",
        "Мой голос — это инструмент любви, а не крика.",
        "Я умею слушать и слышать своего ребёнка.",
        "С каждым днём я становлюсь спокойнее и мудрее.",
        "Я заслуживаю отдыха и заботы о себе.",
        "Мои чувства важны, и я имею право их выражать.",
        "Я учусь прощать себя за ошибки.",
        "Сегодня я дарю себе и своим близким тепло.",
        "Я выбираю радость, даже когда трудно.",
        "Моя интуиция ведёт меня к правильным решениям.",
        "Я достаточно хорошая мама для своего ребёнка.",
        "Каждый день я становлюсь сильнее.",
        "Я отпускаю чувство вины и принимаю себя.",
        "Мой ребёнок чувствует мою любовь, даже когда я устаю.",
        "Я умею просить о помощи, когда мне нужно.",
        "Сегодня я буду доброй к себе.",
        "Моя семья — моя опора, и я их опора.",
        "Я выбираю слова, которые несут свет.",
        "Мой внутренний голос говорит мне о любви.",
        "Я заслуживаю счастья и покоя.",
        "Каждый день я учусь чему-то новому.",
        "Я — источник любви и нежности.",
        "Я умею замечать хорошее в каждом дне.",
        "Моя усталость имеет значение, я могу отдохнуть.",
        "Я выбираю быть счастливой прямо сейчас.",
        "Мой ребёнок видит мою любовь в каждом взгляде.",
        "Я справлюсь с любыми трудностями.",
        "Сегодня я буду говорить с собой с любовью.",
        "Я ценю себя за всё, что я делаю.",
        "Мир начинается с меня, и я выбираю мир.",
        "Я — волшебница, которая создаёт уют и тепло.",
        "Мои объятия лечат и успокаивают.",
        "Я умею находить радость в мелочах.",
        "Сегодня я буду слушать своё сердце.",
        "Я достойна любви и уважения.",
        "Каждый мой день наполнен смыслом.",
        "Я умею прощать и отпускать обиды.",
        "Мой смех — это лекарство для души.",
        "Я выбираю быть терпеливой и понимающей.",
        "Я горжусь тем, какая я есть.",
        "Мои руки способны на нежность и заботу.",
        "Я создаю пространство любви в своём доме.",
        "Сегодня я буду самой лучшей поддержкой для себя.",
        "Я доверяю своей материнской интуиции.",
        "Мой ребёнок учится у меня спокойствию.",
        "Я имею право на личное пространство и время.",
        "Я наполняю свою жизнь светом и добром.",
        "Каждый день я дарю себе немного любви.",
        "Я умею радоваться успехам своего ребёнка.",
        "Моя семья — это моя сила и моя радость.",
        "Я выбираю здоровые отношения с собой.",
        "Я учусь говорить «нет» без чувства вины.",
        "Сегодня я буду бережно относиться к себе.",
        "Мои чувства — это мои учителя.",
        "Я принимаю себя со всеми моими несовершенствами.",
        "Я открыта к новому опыту и знаниям.",
        "Каждый день я становлюсь мудрее.",
        "Я выбираю быть в гармонии с собой.",
        "Мой дом — место любви и взаимопонимания.",
        "Я — мама, которая умеет любить безусловно.",
        "Сегодня я выбираю спокойствие в каждой ситуации.",
        "Я доверяю процессу воспитания.",
        "Мои слова создают атмосферу любви.",
        "Я умею слушать не только ушами, но и сердцем.",
        "Каждый день я становлюсь более осознанной.",
        "Я заслуживаю быть счастливой.",
        "Я создаю для своей семьи пространство счастья.",
        "Мой внутренний свет освещает путь моим детям.",
        "Я умею находить время для себя.",
        "Сегодня я выбираю доброту и мягкость.",
        "Я доверяю своей способности воспитывать.",
        "Мой ребёнок чувствует себя в безопасности рядом со мной.",
        "Я отпускаю контроль и доверяю жизни.",
        "Я умею получать удовольствие от материнства.",
        "Сегодня я буду заботиться о себе с любовью.",
        "Я — источник вдохновения для своей семьи.",
        "Мои ошибки делают меня сильнее.",
        "Я выбираю любовь вместо страха.",
        "Каждый день я дарю себе немного радости.",
        "Я умею видеть красоту в каждом мгновении.",
        "Моя семья — это самое ценное в моей жизни.",
        "Я заслуживаю поддержки и понимания.",
        "Сегодня я буду мягкой к себе и к другим.",
        "Я учусь принимать решения с любовью.",
        "Мой внутренний мир наполнен светом.",
        "Я создаю гармонию в своей жизни.",
        "Каждый день я становлюсь более уверенной.",
        "Я выбираю быть здоровой и счастливой.",
        "Мои дети — мои лучшие учителя.",
        "Я благодарна за каждый день с моей семьёй.",
        "Я — мама, которая умеет просить о помощи.",
        "Сегодня я выбираю быть в моменте здесь и сейчас.",
        "Я доверяю своей интуиции и своему сердцу.",
        "Мои слова наполнены любовью и теплом.",
        "Я умею радоваться даже в сложных ситуациях.",
        "Каждый день я становлюсь более гибкой и терпимой.",
        "Я заслуживаю уважения и признания.",
        "Я создаю в своей семье атмосферу доверия.",
        "Мой дом — это место мира и понимания.",
        "Я умею находить время для своих увлечений.",
        "Сегодня я буду слушать свои желания."
    ]
    day_of_year = datetime.now().timetuple().tm_yday
    if datetime.now().year % 4 == 0 and day_of_year > 60:
        day_of_year -= 1
    return affirmations[day_of_year % 100]

def get_support_message(category):
    # (полный словарь texts для каждой категории, как в предыдущем коде)
    # Я не повторяю его здесь из-за экономии места, но в итоговом файле он должен быть.
    # Вставьте сюда содержимое из предыдущего ответа (сообщения с 7 текстами на категорию).
    pass

def get_age_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👶 1-3 года", callback_data="age_1_3"),
             InlineKeyboardButton(text="🧒 4-6 лет", callback_data="age_4_6")],
            [InlineKeyboardButton(text="👦 7-10 лет", callback_data="age_7_10"),
             InlineKeyboardButton(text="👧 11-14 лет", callback_data="age_11_14")],
            [InlineKeyboardButton(text="🧑 15-18 лет", callback_data="age_15_18"),
             InlineKeyboardButton(text="👩 18+ лет", callback_data="age_18_plus")],
            [InlineKeyboardButton(text="📚 Общие рекомендации", callback_data="age_general")]
        ]
    )

def get_advice_by_age(age_group):
    # (аналогично, полный словарь советов из предыдущего кода)
    # Вставьте сюда содержимое из предыдущего ответа.
    pass

# ================= ОБРАБОТЧИКИ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or "Аноним"
    first_name = message.from_user.first_name or "Мама"

    # Обработка реферальной ссылки
    args = message.text.split()
    if len(args) > 1:
        ref_code = args[1]
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
            referrer = await cursor.fetchone()
            if referrer and referrer[0] != user_id:
                await db.execute("UPDATE users SET referrer_id = ? WHERE user_id = ?", (referrer[0], user_id))
                await db.execute("UPDATE users SET no_cry_days = no_cry_days + 3 WHERE user_id = ?", (referrer[0],))
                await db.commit()
                try:
                    await bot.send_message(referrer[0], "👏 По твоей ссылке пришла новая мама!")
                except:
                    pass

    # Регистрируем пользователя
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, date.today().strftime('%Y-%m-%d'))
        )
        await db.commit()

    # Генерируем реферальный код
    await generate_referral_code(user_id)

    # Проверяем согласие
    if await has_agreed_to_terms(user_id):
        await message.answer(
            "🌸 <b>Главное меню:</b>\n\nВыберите, что вам нужно:",
            reply_markup=main_keyboard(user_id)
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛡️ Здесь безопасно", callback_data="show_terms")]
        ]
    )

    welcome_text = (
        f"👋 Привет, {first_name}!\n\n"
        "Я — <b>PauseMomBot</b> 🤖\n\n"
        "Я — твой помощник в сложные моменты воспитания.\n\n"
        "Я помогаю мамам:\n"
        "🌸 Сохранять спокойствие, когда закипаешь\n"
        "🌸 Заботиться о себе и своих чувствах\n"
        "🌸 Находить нужные слова для себя и детей\n\n"
        "Здесь безопасно и конфиденциально.\n"
        "Нажми «Здесь безопасно», чтобы продолжить ✨"
    )
    await message.answer(welcome_text, reply_markup=keyboard)

# ================= ЮРИДИЧЕСКИЙ БЛОК =================
@router.callback_query(F.data == "show_terms")
async def show_terms(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Принять и продолжить", callback_data="accept_terms")]
        ]
    )

    text = (
        "📋 <b>Пару формальностей и начнём.</b>\n\n"
        "Мы ценим доверие и уважаем закон. Поэтому, всё официально.\n\n"
        "📄 <b>Ознакомьтесь с документами:</b>\n\n"
        f"• <a href='{POLICY_URL}'>Политика обработки персональных данных</a>\n"
        f"• <a href='{OFFER_URL}'>Публичная оферта</a>\n"
        f"• <a href='{CONSENT_URL}'>Соглашение на обработку персональных данных</a>\n\n"
        "Нажимая «Принять и продолжить», Вы соглашаетесь с указанными документами."
    )
    await callback.message.edit_text(text, reply_markup=keyboard)

@router.callback_query(F.data == "accept_terms")
async def accept_terms(callback: CallbackQuery):
    user_id = callback.from_user.id
    await set_agreed_to_terms(user_id)

    await callback.message.edit_text(
        "✅ <b>Спасибо!</b>\n\nТеперь вы можете пользоваться ботом.\n\n🌸 Начните с главного меню:"
    )
    await callback.message.answer(
        "🌸 <b>Главное меню:</b>\n\nВыберите, что вам нужно:",
        reply_markup=main_keyboard(user_id)
    )

# ================= SOS-ПАУЗА =================
@router.message(F.text == "🆘 SOS-Пауза")
async def sos(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET total_sos = total_sos + 1 WHERE user_id = ?", (user_id,))
        await db.commit()
    await message.answer(
        "⏳ <b>Стоп! Ты сделала главное — остановилась.</b>\n\n"
        "Выбери упражнение осознанности, которое поможет успокоиться:",
        reply_markup=sos_keyboard()
    )

# ================= ОБРАБОТЧИКИ ИНЛАЙН-КНОПОК SOS =================
@router.callback_query(F.data == "sos_breath")
async def sos_breath_callback(callback: CallbackQuery):
    await callback.message.answer(
        "🌬️ <b>Дыхание 4-7-8</b>\n\n"
        "1️⃣ Вдохни носом — <b>4</b> секунды\n"
        "2️⃣ Задержи дыхание — <b>7</b> секунд\n"
        "3️⃣ Выдохни ртом — <b>8</b> секунд\n\n"
        "🔄 Повтори <b>5 раз</b>.\n\n"
        "✨ Это снижает уровень кортизола.",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_mindful")
async def sos_mindful_callback(callback: CallbackQuery):
    await callback.message.answer(
        "🧘 <b>Осознанное дыхание</b>\n\n"
        "1️⃣ Сядь удобно, закрой глаза.\n"
        "2️⃣ Сделай 3 глубоких вдоха и выдоха.\n"
        "3️⃣ Теперь просто <b>наблюдай</b> за своим дыханием.\n"
        "4️⃣ Продолжай 1 минуту.\n\n"
        "✨ Это возвращает тебя в «здесь и сейчас».",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_54321")
async def sos_54321_callback(callback: CallbackQuery):
    await callback.message.answer(
        "👀 <b>Упражнение «5-4-3-2-1»</b>\n\n"
        "Оглянись вокруг и найди:\n"
        "5️⃣ <b>вещей</b>, которые ты видишь\n"
        "4️⃣ <b>звука</b>, которые ты слышишь\n"
        "3️⃣ <b>ощущения</b> на коже\n"
        "2️⃣ <b>запаха</b>, которые ты чувствуешь\n"
        "1️⃣ <b>вкуса</b> во рту\n\n"
        "✨ Это возвращает мозг в реальность.",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_hug")
async def sos_hug_callback(callback: CallbackQuery):
    await callback.message.answer(
        "🤗 <b>Техника «Бабочка»</b>\n\n"
        "1️⃣ Скрести руки на груди\n"
        "2️⃣ Похлопай себя по плечам попеременно\n"
        "3️⃣ Продолжай <b>1 минуту</b>\n\n"
        "✨ Это успокаивает нервную систему.",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_wash")
async def sos_wash_callback(callback: CallbackQuery):
    await callback.message.answer(
        "🚰 <b>Умывание холодной водой</b>\n\n"
        "1️⃣ Встань и подойди к раковине\n"
        "2️⃣ Умой лицо холодной водой <b>3 раза</b>\n"
        "3️⃣ Почувствуй, как вода смывает гнев\n\n"
        "✨ Это запускает «нырятельный рефлекс».",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_standing")
async def sos_standing_callback(callback: CallbackQuery):
    await callback.message.answer(
        "🦶 <b>Стойка на ногах</b>\n\n"
        "1️⃣ Встань ровно, ноги на ширине плеч\n"
        "2️⃣ Почувствуй, как ноги касаются пола\n"
        "3️⃣ Начинай медленно переносить вес:\n"
        "   • На пятки — <b>3 секунды</b>\n"
        "   • На носки — <b>3 секунды</b>\n"
        "   • На внешний край стоп — <b>3 секунды</b>\n"
        "4️⃣ Повтори <b>5 раз</b>\n\n"
        "✨ Это возвращает тебя в тело.",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_bodyscan")
async def sos_bodyscan_callback(callback: CallbackQuery):
    await callback.message.answer(
        "🧠 <b>Сканирование тела</b>\n\n"
        "Закрой глаза и почувствуй:\n"
        "👣 <b>Стопы</b>\n"
        "🦵 <b>Ноги</b>\n"
        "🤲 <b>Руки</b>\n"
        "🫀 <b>Грудь</b>\n"
        "👤 <b>Лицо</b>\n\n"
        "✨ Это снимает напряжение.",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_light")
async def sos_light_callback(callback: CallbackQuery):
    await callback.message.answer(
        "☀️ <b>Луч света</b>\n\n"
        "1️⃣ Закрой глаза.\n"
        "2️⃣ Представь <b>тёплый золотистый свет</b> над головой.\n"
        "3️⃣ Этот свет медленно опускается:\n"
        "   • на лицо — смывает напряжение\n"
        "   • на плечи — снимает тяжесть\n"
        "   • на грудь — наполняет спокойствием\n"
        "4️⃣ Продолжай <b>1 минуту</b>\n\n"
        "✨ Свет растворяет гнев.",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_wave")
async def sos_wave_callback(callback: CallbackQuery):
    await callback.message.answer(
        "🌊 <b>Волна дыхания</b>\n\n"
        "Представь, что твоё дыхание — это волны океана:\n\n"
        "🌊 <b>Вдох</b> — волна накатывает\n"
        "🌊 <b>Выдох</b> — волна уходит\n\n"
        "🔄 Повтори <b>5 раз</b>\n\n"
        "✨ Волны смывают гнев и тревогу.",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sos_observer")
async def sos_observer_callback(callback: CallbackQuery):
    await callback.message.answer(
        "💭 <b>Наблюдатель</b>\n\n"
        "1️⃣ Закрой глаза.\n"
        "2️⃣ Представь, что ты смотришь на себя со стороны.\n"
        "3️⃣ Ты видишь свою злость как <b>облако</b>.\n"
        "4️⃣ Наблюдай за этим облаком:\n"
        "   • Оно пришло\n"
        "   • Оно здесь\n"
        "   • Оно уходит\n\n"
        "5️⃣ Ты не злость — ты просто <b>наблюдаешь</b> её.\n\n"
        "✨ Ты отделяешь себя от эмоций.",
        reply_markup=sos_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "back_to_main_from_sos")
async def back_to_main_from_sos_callback(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "🌸 <b>Главное меню:</b>\n\nВыберите, что вам нужно:",
        reply_markup=main_keyboard(callback.from_user.id)
    )
    await callback.answer()

# ================= КНОПКА "ПРИГЛАСИТЬ ПОДРУГУ" =================
@router.message(F.text == "👥 Пригласить подругу")
async def referral(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
    if result and result[0]:
        code = result[0]
    else:
        code = await generate_referral_code(user_id)

    bot_info = await bot.get_me()
    bot_username = bot_info.username

    await message.answer(
        f"👥 <b>Твоя реферальная ссылка:</b>\n"
        f"<code>https://t.me/{bot_username}?start={code}</code>\n\n"
        "🌸 Поделись с подругой — поддержка важна для каждой мамы.",
        reply_markup=main_keyboard(user_id)
    )

# ================= КНОПКА "ВОССТАНОВЛЕНИЕ КОНТАКТА" =================
@router.message(F.text == "🤝 Восстановить контакт")
async def restore_contact(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if await is_premium(user_id):
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Начать восстановление", callback_data="start_restore")],
                [InlineKeyboardButton(text="🌸 Ресурсные техники для мамы", callback_data="resource_techniques")],
                [InlineKeyboardButton(text="🧸 Техники для малышей", callback_data="kids_restore")],
                [InlineKeyboardButton(text="🧑 Техники для подростков и детей постарше", callback_data="teen_restore")],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_main")]
            ]
        )
        await message.answer(
            "🤝 <b>Восстановление контакта</b>\n\n"
            "Выбери категорию:\n\n"
            "🔄 <b>Начать восстановление</b> — пошаговый план (3 шага)\n"
            "🌸 <b>Ресурсные техники для мамы</b> — 5 техник для восстановления ресурса\n"
            "🧸 <b>Техники для малышей</b> — для детей 1-12 лет\n"
            "🧑 <b>Техники для подростков и детей постарше</b> — 6 техник для взрослых детей",
            reply_markup=keyboard
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Оформить Premium", callback_data="back_to_premium")]
        ]
    )
    await message.answer(
        "🔒 <b>Этот раздел доступен только по подписке Premium.</b>\n\n"
        "Оформите Premium (999 ₽/мес), чтобы получить доступ к эксклюзивному модулю «Восстановление контакта».",
        reply_markup=keyboard
    )

# ================= НАЧАЛО ВОССТАНОВЛЕНИЯ (3 ШАГА) =================
@router.callback_query(F.data == "start_restore")
async def start_restore_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Прочитала, дай следующий шаг")],
            [KeyboardButton(text="🔙 Главное меню")]
        ],
        resize_keyboard=True,
        row_width=1
    )

    await callback.message.answer(
        "🛑 <b>Шаг 1 из 3: Стоп-сигнал</b>\n\n"
        "Ты уже осознала, что наговорила лишнего.\n\n"
        "🔹 <b>Что делать:</b>\n"
        "Ребёнок поставил границу. Самое важное сейчас — <b>не преследовать его</b>.\n\n"
        "🚫 Не стучись в дверь.\n"
        "🚫 Не кричи вдогонку.\n"
        "🚫 Не требуй ответа.\n\n"
        "✅ Просто отойди и дай ему время на остывание.\n"
        "Скажи себе: «Я уважаю его право на паузу».\n\n"
        "Когда будешь готова — нажми кнопку ниже.",
        reply_markup=keyboard
    )
    await state.set_state(Form.restore_step1)

@router.message(Form.restore_step1, F.text == "✅ Прочитала, дай следующий шаг")
async def restore_step2(message: Message, state: FSMContext):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Прочитала, дай следующий шаг")],
            [KeyboardButton(text="🔙 Главное меню")]
        ],
        resize_keyboard=True,
        row_width=1
    )

    await message.answer(
        "✍️ <b>Шаг 2 из 3: Я-сообщение</b>\n\n"
        "Теперь, когда ты успокоилась, попробуй сказать ему <b>тихо</b> и <b>без оправданий</b>.\n\n"
        "📝 <b>Выбери фразу:</b>\n\n"
        "1️⃣ «Я знаю, что я наговорила лишнего. Мне очень жаль.»\n\n"
        "2️⃣ «Я не справилась со своими эмоциями. Это неправильно.»\n\n"
        "3️⃣ «Ты очень дорог мне, даже когда я ошибаюсь.»\n\n"
        "💡 Скажи это один раз — и отойди.\n\n"
        "Когда будешь готова — нажми кнопку ниже.",
        reply_markup=keyboard
    )
    await state.set_state(Form.restore_step2)

@router.message(Form.restore_step2, F.text == "✅ Прочитала, дай следующий шаг")
async def restore_step3(message: Message, state: FSMContext):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Прочитала, дай следующий шаг")],
            [KeyboardButton(text="🔙 Главное меню")]
        ],
        resize_keyboard=True,
        row_width=1
    )

    await message.answer(
        "🍲 <b>Шаг 3 из 3: Действие без слов</b>\n\n"
        "Он не хочет говорить? Хорошо. Не заставляй.\n\n"
        "Покажи свою любовь через действие:\n\n"
        "• 🍳 Приготовь его любимую еду\n"
        "• ✉️ Положи записку под дверь\n"
        "• 😊 Просто улыбнись\n\n"
        "Твоя задача — не давить, а показать, что ты рядом.\n\n"
        "Когда будешь готова — нажми кнопку ниже.",
        reply_markup=keyboard
    )
    await state.set_state(Form.restore_step3)

@router.message(Form.restore_step3, F.text == "✅ Прочитала, дай следующий шаг")
async def restore_step4(message: Message, state: FSMContext):
    await message.answer(
        "💎 <b>Ты сделала большой шаг!</b>\n\n"
        "Ты прошла все 3 шага восстановления контакта.\n\n"
        "Ты научилась:\n"
        "✅ Делать паузу, когда закипаешь\n"
        "✅ Говорить мягко и без оправданий\n"
        "✅ Показывать любовь через действие\n\n"
        "Ты не просто мама, которая ошибается. "
        "Ты мама, которая умеет исправлять свои ошибки. ❤️\n\n"
        "🌸 Ты — самая лучшая мама для своего ребёнка!",
        reply_markup=main_keyboard(message.from_user.id)
    )
    await state.clear()

# ================= РЕСУРСНЫЕ ТЕХНИКИ ДЛЯ МАМЫ =================
@router.callback_query(F.data == "resource_techniques")
async def resource_techniques(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌬️ Квадратное дыхание", callback_data="res_breath")],
            [InlineKeyboardButton(text="✍️ Список благодарности", callback_data="res_gratitude")],
            [InlineKeyboardButton(text="🫂 Разрешение на отдых", callback_data="res_rest")],
            [InlineKeyboardButton(text="🌅 Луч света", callback_data="res_light")],
            [InlineKeyboardButton(text="🎵 Музыкальная пауза", callback_data="res_music")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_restore")]
        ]
    )

    await callback.message.edit_text(
        "🌸 <b>Ресурсные техники для мамы</b>\n\n"
        "Эти техники помогут тебе восстановить силы, успокоиться и наполниться энергией.\n\n"
        "Выбери технику:",
        reply_markup=keyboard
    )

@router.callback_query(F.data.startswith("res_"))
async def show_resource_technique(callback: CallbackQuery):
    technique = callback.data.replace("res_", "")

    techniques_texts = {
        "breath": (
            "🌬️ <b>Техника «Квадратное дыхание»</b>\n\n"
            "Это дыхание помогает вернуть контроль над эмоциями за 1 минуту.\n\n"
            "1️⃣ Вдох — 4 секунды\n"
            "2️⃣ Задержка — 4 секунды\n"
            "3️⃣ Выдох — 4 секунды\n"
            "4️⃣ Задержка — 4 секунды\n\n"
            "🔄 Повтори 5 раз.\n\n"
            "💡 Представь, что ты рисуешь дыханием квадрат. Это переключает мозг с эмоций на логику."
        ),
        "gratitude": (
            "✍️ <b>Техника «Список благодарности»</b>\n\n"
            "Ты так много даёшь другим. А теперь дай себе пару минут.\n\n"
            "1️⃣ Возьми лист бумаги или открой заметки в телефоне.\n"
            "2️⃣ Напиши 3 вещи, за которые ты благодарна себе сегодня.\n"
            "   • «Я благодарна себе за то, что...»\n"
            "   • «Я благодарна себе за то, что...»\n"
            "   • «Я благодарна себе за то, что...»\n"
            "3️⃣ Прочитай это вслух себе.\n\n"
            "📌 <b>Примеры:</b>\n"
            "• «Я благодарна себе за то, что нашла время выпить чай»\n"
            "• «Я благодарна себе за то, что не сорвалась на ребёнка»\n"
            "• «Я благодарна себе за то, что я — хорошая мама»\n\n"
            "💡 Благодарность к себе — это топливо для души."
        ),
        "rest": (
            "🫂 <b>Техника «Разрешение на отдых»</b>\n\n"
            "Ты имеешь право на отдых. Без чувства вины. Без оправданий.\n\n"
            "1️⃣ Сядь удобно и закрой глаза.\n"
            "2️⃣ Скажи себе вслух:\n"
            "   «Я разрешаю себе отдохнуть.\n"
            "   Я разрешаю себе быть уставшей.\n"
            "   Я разрешаю себе ничего не делать.\n"
            "   Я заслуживаю отдыха».\n\n"
            "3️⃣ Позволь себе 5 минут тишины. Просто посиди, не делай ничего.\n\n"
            "💡 Отдых — это не роскошь. Это необходимость. Ты заслуживаешь его."
        ),
        "light": (
            "🌅 <b>Техника «Луч света»</b>\n\n"
            "Закрой глаза и представь, что сверху на тебя льётся тёплый золотистый свет.\n\n"
            "1️⃣ Свет мягко касается твоей головы — ты чувствуешь тепло.\n"
            "2️⃣ Он опускается на плечи — снимает тяжесть и напряжение.\n"
            "3️⃣ Он доходит до груди — наполняет тебя спокойствием.\n"
            "4️⃣ Он разливается по всему телу — ты чувствуешь лёгкость и силу.\n\n"
            "💡 Побудь в этом свете 2-3 минуты. Когда откроешь глаза — ты почувствуешь себя обновлённой."
        ),
        "music": (
            "🎵 <b>Техника «Музыкальная пауза»</b>\n\n"
            "Музыка лечит. Музыка успокаивает. Музыка возвращает к себе.\n\n"
            "1️⃣ Включи свою любимую песню.\n"
            "2️⃣ Закрой глаза и слушай только музыку.\n"
            "3️⃣ Не думай ни о чём. Просто чувствуй.\n"
            "4️⃣ Если мысли приходят — отпускай их и возвращайся к музыке.\n\n"
            "💡 3 минуты музыки могут дать больше отдыха, чем час в социальных сетях."
        )
    }

    text = techniques_texts.get(technique, "Техника не найдена.")

    await callback.message.edit_text(
        f"{text}\n\n"
        f"🔙 Нажми «Назад», чтобы вернуться к списку техник.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="resource_techniques")]
            ]
        )
    )

# ================= ТЕХНИКИ ДЛЯ МАЛЫШЕЙ (В ВОССТАНОВЛЕНИИ) =================
@router.callback_query(F.data == "kids_restore")
async def kids_restore_techniques(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👶 1-3 года", callback_data="kids_restore_1_3"),
             InlineKeyboardButton(text="🧒 4-6 лет", callback_data="kids_restore_4_6")],
            [InlineKeyboardButton(text="👦 7-9 лет", callback_data="kids_restore_7_9"),
             InlineKeyboardButton(text="👦 10-12 лет", callback_data="kids_restore_10_12")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_restore")]
        ]
    )

    await callback.message.edit_text(
        "🧸 <b>Техники для малышей</b>\n\nВыбери возраст ребёнка:",
        reply_markup=keyboard
    )

@router.callback_query(F.data.startswith("kids_restore_"))
async def show_kids_restore_technique(callback: CallbackQuery):
    age_group = callback.data.replace("kids_restore_", "")
    techniques_data = get_kids_techniques(age_group)

    tips_text = "\n\n".join(techniques_data["tips"])

    await callback.message.edit_text(
        f"{techniques_data['title']}\n\n{tips_text}\n\n"
        f"🔙 Нажми «Назад», чтобы выбрать другой возраст.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="kids_restore")]
            ]
        )
    )

# ================= ТЕХНИКИ ДЛЯ ПОДРОСТКОВ =================
@router.callback_query(F.data == "teen_restore")
async def teen_restore_techniques(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👁️ Наблюдатель", callback_data="teen_observer")],
            [InlineKeyboardButton(text="🎯 Стоп-кадр", callback_data="teen_stop_frame")],
            [InlineKeyboardButton(text="🗣️ Честность без оправданий", callback_data="teen_honest")],
            [InlineKeyboardButton(text="🎯 Совет по запросу", callback_data="teen_advice")],
            [InlineKeyboardButton(text="📱 Музыка-мостик", callback_data="teen_music")],
            [InlineKeyboardButton(text="🧩 Спроси, а не учи", callback_data="teen_ask")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_restore")]
        ]
    )

    await callback.message.edit_text(
        "🧑 <b>Техники для подростков и детей постарше</b>\n\n"
        "Эти техники помогут восстановить контакт с подростками и взрослыми детьми.\n\n"
        "Выбери технику:",
        reply_markup=keyboard
    )

@router.callback_query(F.data.startswith("teen_"))
async def show_teen_technique(callback: CallbackQuery):
    technique = callback.data.replace("teen_", "")

    techniques_texts = {
        "observer": (
            "👁️ <b>Техника «Наблюдатель»</b>\n\n"
            "1️⃣ Закрой глаза и представь, что ты смотришь на себя со стороны.\n"
            "2️⃣ Ты видишь маму и ребёнка после ссоры.\n"
            "3️⃣ Что ты видишь? Какие эмоции? Что происходит?\n"
            "4️⃣ Теперь представь, что ты — мудрый друг, который даёт совет этой маме.\n"
            "5️⃣ Что бы ты ей сказала?\n\n"
            "💡 Взгляд со стороны помогает увидеть решение."
        ),
        "stop_frame": (
            "🎯 <b>Техника «Стоп-кадр»</b>\n\n"
            "Ты уже осознала, что сорвалась. Теперь давай разберём ситуацию.\n\n"
            "1️⃣ Закрой глаза и прокрути ситуацию как фильм.\n"
            "2️⃣ Найди момент, когда ты ещё могла сказать мягко.\n"
            "3️⃣ Назови этот момент — твой «стоп-кадр».\n"
            "4️⃣ Придумай фразу, которую нужно было сказать вместо крика.\n"
            "5️⃣ Запомни эту фразу. Она — твой новый инструмент.\n\n"
            "💡 Это упражнение превращает ошибку в опыт."
        ),
        "honest": (
            "🗣️ <b>Техника «Честность без оправданий»</b>\n\n"
            "Подростки и взрослые дети ненавидят оправдания.\n"
            "«Я устала», «Я не хотела», «Ты сам меня довёл» — это только злит.\n\n"
            "🔹 <b>Что сказать:</b>\n"
            "Вместо оправданий скажи коротко и честно:\n\n"
            "«Я была неправа. Прости.»\n\n"
            "Или:\n"
            "«Я сорвалась на тебе. Это было неправильно.»\n\n"
            "💡 Без оправданий. Без объяснений. Просто признание ошибки.\n"
            "Это вызывает уважение, а не раздражение."
        ),
        "advice": (
            "🎯 <b>Техника «Совет только по запросу»</b>\n\n"
            "Взрослые дети не любят непрошенные советы.\n\n"
            "🔹 <b>Что делать:</b>\n"
            "❌ Не говори: «Я бы на твоём месте...»\n"
            "✅ Спроси: «Хочешь, я поделюсь своим мнением?»\n"
            "✅ Если он говорит «нет» — прими это.\n\n"
            "💡 Непрошенный совет — это вторжение.\n"
            "Запрошенный совет — это помощь."
        ),
        "music": (
            "📱 <b>Техника «Музыка-мостик»</b>\n\n"
            "Музыка объединяет лучше слов.\n\n"
            "🔹 <b>Что делать:</b>\n"
            "1️⃣ Спроси: «Что ты сейчас слушаешь? Можешь скинуть плейлист?»\n"
            "2️⃣ Если он скидывает — это большой шаг. Значит, он готов делиться своим миром.\n"
            "3️⃣ Послушай, что он скинул.\n"
            "4️⃣ Скажи: «Классная песня. Спасибо, что поделился».\n\n"
            "💡 Интерес к его музыке — интерес к его миру."
        ),
        "ask": (
            "🧩 <b>Техника «Спроси, а не учи»</b>\n\n"
            "Подростки ненавидят, когда их учат жизни.\n"
            "Им нужен диалог, а не лекция.\n\n"
            "🔹 <b>Что спросить:</b>\n"
            "«Что ты думаешь об этом?»\n"
            "«Как ты видишь эту ситуацию?»\n"
            "«Что для тебя сейчас важно?»\n"
            "«Что я могу сделать, чтобы тебе было легче?»\n\n"
            "💡 Когда ты спрашиваешь — ты показываешь уважение.\n"
            "Когда ты учишь — ты показываешь превосходство."
        )
    }

    text = techniques_texts.get(technique, "Техника не найдена.")

    await callback.message.edit_text(
        f"{text}\n\n"
        f"🔙 Нажми «Назад», чтобы вернуться к списку техник.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="teen_restore")]
            ]
        )
    )

# ================= КНОПКА "НУЖНЫЕ СЛОВА ДЛЯ МАМЫ" =================
@router.message(F.text == "💝 Нужные слова для мамы")
async def support_menu(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👋 Первое знакомство", callback_data="support_welcome"),
             InlineKeyboardButton(text="🌸 После SOS-паузы", callback_data="support_after_sos")],
            [InlineKeyboardButton(text="🫂 После срыва", callback_data="support_after_cry"),
             InlineKeyboardButton(text="🌙 Вечерняя поддержка", callback_data="support_evening")],
            [InlineKeyboardButton(text="🌅 Утренняя поддержка", callback_data="support_morning"),
             InlineKeyboardButton(text="👧 Для мам подростков", callback_data="support_teen")],
            [InlineKeyboardButton(text="👩 Для мам взрослых детей", callback_data="support_adult"),
             InlineKeyboardButton(text="🍼 Для мам в декрете", callback_data="support_baby")],
            [InlineKeyboardButton(text="😴 Кто не выспался", callback_data="support_tired"),
             InlineKeyboardButton(text="💔 Кто чувствует себя недостаточно хорошей", callback_data="support_not_enough")],
            [InlineKeyboardButton(text="🫂 Кто устал от вины", callback_data="support_guilt"),
             InlineKeyboardButton(text="🫂 Кто чувствует себя одинокой", callback_data="support_alone")],
            [InlineKeyboardButton(text="💫 Кто чувствует себя разбитой или потерянной", callback_data="support_lost"),
             InlineKeyboardButton(text="💝 Забота о себе", callback_data="support_self_care")]
        ]
    )
    await message.answer(
        "💝 <b>Нужные слова для мамы</b>\n\nВыбери категорию, которая откликается тебе сейчас:",
        reply_markup=keyboard
    )

@router.callback_query(F.data.startswith('support_'))
async def show_support_message(callback: CallbackQuery):
    category = callback.data.replace('support_', '')
    message_data = get_support_message(category)
    texts = message_data["texts"]

    user_id = callback.from_user.id

    if await is_premium(user_id):
        day_index = datetime.now().weekday()
        chosen_text = texts[day_index % len(texts)]
    else:
        chosen_text = texts[0]

    await callback.message.edit_text(
        f"💝 <b>{message_data['title']}</b>\n\n{chosen_text}\n\n"
        "🔙 Нажми «Назад», чтобы выбрать другую категорию.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к категориям", callback_data="back_to_support")]
            ]
        )
    )
    await callback.answer()

@router.callback_query(F.data == "back_to_support")
async def back_to_support(callback: CallbackQuery):
    await support_menu(callback.message)
    await callback.answer()

# ================= КНОПКА "ТЕХНИКИ ДЛЯ МАЛЫШЕЙ" (PREMIUM) =================
@router.message(F.text == "🧸 Техники для малышей")
async def kids_techniques_menu(message: Message):
    user_id = message.from_user.id

    if await is_premium(user_id):
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👶 1-3 года", callback_data="kids_1_3"),
                 InlineKeyboardButton(text="🧒 4-6 лет", callback_data="kids_4_6")],
                [InlineKeyboardButton(text="👦 7-9 лет", callback_data="kids_7_9"),
                 InlineKeyboardButton(text="👦 10-12 лет", callback_data="kids_10_12")],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_main")]
            ]
        )
        await message.answer(
            "🧸 <b>Техники для малышей</b>\n\nВыбери возраст:",
            reply_markup=keyboard
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Оформить Premium", callback_data="back_to_premium")]
        ]
    )
    await message.answer(
        "🔒 <b>Техники для малышей доступны только Premium-пользователям.</b>\n\n"
        "Оформите Premium (999 ₽/мес) и получите доступ к техникам для детей от 1 до 12 лет.",
        reply_markup=keyboard
    )

@router.callback_query(F.data.startswith('kids_'))
async def process_kids_technique(callback: CallbackQuery):
    if callback.data == "kids_back_to_main":
        await callback.message.delete()
        await callback.message.answer(
            "Главное меню:",
            reply_markup=main_keyboard(callback.from_user.id)
        )
        return

    age_group = callback.data.replace("kids_", "")
    techniques_data = get_kids_techniques(age_group)

    tips_text = "\n\n".join(techniques_data["tips"])

    await callback.message.edit_text(
        f"{techniques_data['title']}\n\n{tips_text}\n\n"
        f"🔙 Нажми «Назад», чтобы выбрать другой возраст.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к возрастам", callback_data="back_to_kids")]
            ]
        )
    )

@router.callback_query(F.data == "back_to_kids")
async def back_to_kids(callback: CallbackQuery):
    await kids_techniques_menu(callback.message)

# ================= КНОПКА "АФФИРМАЦИЯ ДНЯ" =================
@router.message(F.text == "🌅 Аффирмация дня")
async def daily_affirmation(message: Message):
    user_id = message.from_user.id

    if not await is_premium(user_id):
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💎 Оформить Premium", callback_data="back_to_premium")]
            ]
        )
        await message.answer(
            "🔒 <b>Аффирмации доступны только Premium-пользователям.</b>\n\n"
            "Оформите Premium (999 ₽/мес) и получайте 100 поддерживающих фраз.",
            reply_markup=keyboard
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT last_affirmation FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()

    today = date.today().strftime('%Y-%m-%d')

    if result and result[0] == today:
        affirmation = get_daily_affirmation()
        await message.answer(
            f"🌅 <b>Твоя аффирмация на сегодня:</b>\n\n"
            f"«{affirmation}»\n\n"
            f"💫 Ты уже получила её сегодня. Повтори несколько раз с любовью к себе.",
            reply_markup=main_keyboard(user_id)
        )
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET last_affirmation = ? WHERE user_id = ?", (today, user_id))
            await db.commit()

        affirmation = get_daily_affirmation()
        day_of_year = datetime.now().timetuple().tm_yday

        await message.answer(
            f"🌅 <b>Аффирмация дня #{day_of_year % 100 + 1}</b>\n\n"
            f"«{affirmation}»\n\n"
            f"💫 Повтори эту аффирмацию 3 раза сегодня.\n"
            f"✨ Она наполнит тебя силой и уверенностью.\n\n"
            f"💝 Ты — самая лучшая мама для своего ребёнка!",
            reply_markup=main_keyboard(user_id)
        )

# ================= КНОПКА "ОБЩИЕ РЕКОМЕНДАЦИИ ПО ВОЗРАСТУ" =================
@router.message(F.text == "📚 Общие рекомендации по возрасту")
async def age_recommendations(message: Message):
    await message.answer(
        "📚 <b>Общие рекомендации по возрасту</b>\n\n"
        "Выбери возраст своего ребёнка:",
        reply_markup=get_age_keyboard()
    )

@router.callback_query(F.data.startswith('age_'))
async def process_age_choice(callback: CallbackQuery):
    age_group = callback.data
    advice_data = get_advice_by_age(age_group)

    tips_text = "\n\n".join(advice_data["tips"])
    restore_text = "\n\n".join([f"• {t}" for t in advice_data.get("restore_techniques", [])])

    message_text = (
        f"📚 <b>{advice_data['title']}</b>\n\n"
        f"📋 <b>Рекомендации:</b>\n\n"
        f"{tips_text}\n\n"
    )

    if restore_text:
        message_text += (
            f"🔄 <b>Техники восстановления контакта:</b>\n\n"
            f"{restore_text}\n\n"
        )

    message_text += (
        f"💡 Помни: каждый ребёнок уникален. Эти рекомендации — ориентир.\n\n"
        f"🔙 Нажми «Назад», чтобы выбрать другой возраст."
    )

    await callback.message.edit_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к возрастам", callback_data="back_to_ages")]
            ]
        )
    )

@router.callback_query(F.data == "back_to_ages")
async def back_to_ages(callback: CallbackQuery):
    await callback.message.edit_text(
        "📚 <b>Общие рекомендации по возрасту</b>\n\n"
        "Выбери возраст своего ребёнка:",
        reply_markup=get_age_keyboard()
    )

# ================= НАЗАД К ВОССТАНОВЛЕНИЮ КОНТАКТА =================
@router.callback_query(F.data == "back_to_restore")
async def back_to_restore(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()

    class FakeMessage:
        def __init__(self, user_id):
            self.from_user = type('User', (), {'id': user_id})()
        async def answer(self, text, reply_markup=None):
            await callback.message.answer(text, reply_markup=reply_markup)

    fake_msg = FakeMessage(callback.from_user.id)
    await restore_contact(fake_msg, state)

@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "🌸 <b>Главное меню:</b>\n\nВыберите, что вам нужно:",
        reply_markup=main_keyboard(callback.from_user.id)
    )

# ================= PREMIUM И ОПЛАТА =================
@router.message(F.text == "💎 Premium")
async def premium_info(message: Message):
    user_id = message.from_user.id

    if user_id in ADMINS:
        await message.answer(
            "👑 <b>Вы — создатель бота!</b>\n\n"
            "Вам доступны все функции Premium без оплаты.\n\n"
            "✨ <b>Доступно:</b>\n"
            "✅ Техники для малышей (1-12 лет)\n"
            "✅ Модуль «Восстановление контакта»\n"
            "✅ 100 аффирмаций поддержки",
            reply_markup=main_keyboard(user_id)
        )
        if not await is_premium(user_id):
            await add_premium(user_id, 36500)
        return

    if await is_premium(user_id):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
            result = await cursor.fetchone()
        if result and result[0]:
            await message.answer(
                f"✅ <b>У тебя уже есть Premium!</b>\n\n"
                f"📅 Действует до: {result[0]}\n\n"
                "Пользуйся всеми функциями без ограничений.",
                reply_markup=main_keyboard(user_id)
            )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", callback_data="pay_premium")],
            [InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data="check_premium_payment")],
            [InlineKeyboardButton(text="❓ Как оплатить?", callback_data="payment_help")]
        ]
    )

    await message.answer(
        "💎 <b>Premium — 999 ₽/мес</b>\n\n"
        "✨ <b>Что ты получаешь:</b>\n"
        "✅ Техники для малышей (1-12 лет)\n"
        "✅ Модуль «Восстановление контакта»\n"
        "✅ 100 аффирмаций поддержки\n\n"
        "💳 Нажмите «Оплатить», чтобы перейти к оплате через Робокассу.",
        reply_markup=keyboard
    )

@router.callback_query(F.data == "pay_premium")
async def pay_premium(callback: CallbackQuery):
    user_id = callback.from_user.id
    payment_url = await generate_payment_link_and_save(user_id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)],
            [InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data="check_premium_payment")]
        ]
    )

    await callback.message.edit_text(
        "💎 <b>Оплата Premium</b>\n\n"
        "Нажмите кнопку ниже, чтобы перейти на защищённую страницу оплаты Робокассы.\n"
        "После оплаты вернитесь сюда и нажмите «Я оплатил(а)».",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data == "check_premium_payment")
async def check_premium_payment(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT last_invoice_id FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
    if not result or not result[0]:
        await callback.message.edit_text("❌ Платёж не найден. Пожалуйста, нажмите «Оплатить» ещё раз.")
        return

    inv_id = result[0]
    if check_payment(inv_id):
        await add_premium(user_id, 30)
        await callback.message.edit_text("✅ Платёж подтверждён! Premium активирован на 30 дней!")
    else:
        await callback.message.edit_text("❌ Платёж ещё не поступил. Попробуйте позже или обратитесь в поддержку.")
    await callback.answer()

@router.callback_query(F.data == "payment_help")
async def payment_help(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к Premium", callback_data="back_to_premium")]
        ]
    )

    await callback.message.edit_text(
        "❓ <b>Как оплатить Premium:</b>\n\n"
        "1️⃣ Нажмите «Оплатить» в разделе Premium.\n"
        "2️⃣ Перейдите по ссылке на защищённую страницу Робокассы.\n"
        "3️⃣ Оплатите удобным способом (карта, СБП и др.).\n"
        "4️⃣ Вернитесь в бот и нажмите «Я оплатил(а)».\n"
        "5️⃣ Бот проверит платёж и автоматически активирует Premium.\n\n"
        "💡 Если возникли проблемы, напишите в поддержку: @PauseMomSupport_bot",
        reply_markup=keyboard
    )

@router.callback_query(F.data == "back_to_premium")
async def back_to_premium(callback: CallbackQuery):
    await callback.message.delete()

    class FakeMessage:
        def __init__(self, user_id):
            self.from_user = type('User', (), {'id': user_id})()
        async def answer(self, text, reply_markup=None):
            await callback.message.answer(text, reply_markup=reply_markup)

    fake_msg = FakeMessage(callback.from_user.id)
    await premium_info(fake_msg)

# ================= ПОМОЩЬ =================
@router.message(F.text == "📞 Помощь")
async def help_menu(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", url="https://t.me/PauseMomSupport_bot")]
        ]
    )

    await message.answer(
        "📞 <b>Помощь</b>\n\n"
        "❓ <b>Частые вопросы:</b>\n"
        "• Как оплатить Premium? → Нажми «💎 Premium»\n"
        "• Проблемы с оплатой? → Напиши поддержке\n"
        "• Хочешь предложить идею? → Мы открыты!\n\n"
        "🕐 Мы отвечаем в течение 24 часов.\n"
        "💝 Спасибо, что ты с нами!",
        reply_markup=keyboard
    )

# ================= АДМИН-ПАНЕЛЬ =================
@router.message(Command("admin"))
async def admin_command(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("⛔ <b>Доступ запрещён.</b>")
        return

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👑 Активировать Premium (навсегда)"), KeyboardButton(text="👑 Активировать Premium (1 месяц)")],
            [KeyboardButton(text="📊 Пользователи (статистика)"), KeyboardButton(text="🔙 Главное меню")]
        ],
        resize_keyboard=True,
        row_width=2
    )
    await message.answer(
        "👑 <b>Админ-панель</b>\n\nДобро пожаловать, создатель! 👋\n\nВыберите действие:",
        reply_markup=keyboard
    )

@router.message(F.text == "👑 Активировать Premium (навсегда)")
async def admin_premium_forever(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("⛔ Доступ запрещён.")
        return

    await add_premium(user_id, 36500)
    await message.answer(
        "✅ <b>Premium активирован НАВСЕГДА!</b> 🎉\n\n"
        "Срок действия — 100 лет (до 2126 года).\n\n"
        "🌸 Приятного использования!",
        reply_markup=main_keyboard(user_id)
    )

@router.message(F.text == "👑 Активировать Premium (1 месяц)")
async def admin_premium_1month(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("⛔ Доступ запрещён.")
        return

    await add_premium(user_id, 30)
    await message.answer(
        "✅ <b>Premium активирован на 1 месяц!</b> 🎉",
        reply_markup=main_keyboard(user_id)
    )

@router.message(F.text == "📊 Пользователи (статистика)")
async def admin_stats(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("⛔ Доступ запрещён.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE subscription_end IS NOT NULL AND subscription_end > date('now')")
        premium_users = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT SUM(total_sos) FROM users")
        total_sos = (await cursor.fetchone())[0] or 0

    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 <b>Всего пользователей:</b> {total_users}\n"
        f"💎 <b>Premium-пользователей:</b> {premium_users}\n"
        f"🆘 <b>Всего SOS-пауз:</b> {total_sos}\n\n"
        f"📈 Бот растёт! 🌸",
        reply_markup=main_keyboard(user_id)
    )

# ================= ВОЗВРАТ В ГЛАВНОЕ МЕНЮ (ОБЫЧНАЯ КНОПКА) =================
@router.message(F.text == "🔙 Главное меню")
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🌸 <b>Главное меню:</b>\n\nВыберите, что вам нужно:",
        reply_markup=main_keyboard(message.from_user.id)
    )

# ================= ЗАПУСК =================
async def on_startup():
    await create_tables()
    print("База данных готова!")
    print("Бот запущен!")

async def main():
    dp.include_router(router)
    await on_startup()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
