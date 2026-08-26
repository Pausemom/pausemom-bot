import asyncio
import hashlib
import json
import os
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from urllib.parse import urlencode, quote_plus, parse_qs

import aiohttp
import aiosqlite
from aiohttp import web
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

ROBOKASSA_LOGIN = os.getenv('ROBOKASSA_LOGIN')
ROBOKASSA_PASSWORD1 = os.getenv('ROBOKASSA_PASSWORD1')
ROBOKASSA_PASSWORD2 = os.getenv('ROBOKASSA_PASSWORD2')
ROBOKASSA_TEST_MODE = os.getenv('ROBOKASSA_TEST_MODE', 'False').lower() == 'true'

ROBOKASSA_URL = 'https://auth.robokassa.ru/Merchant/Index.aspx'
ROBOKASSA_API_URL = 'https://auth.robokassa.ru/Merchant/WebService/Service.asmx/OpState'
RESULT_URL = os.getenv('RESULT_URL', 'https://yourdomain.com/robokassa/result')

DB_PATH = "pause_bot.db"

# Ссылки на юридические документы
POLICY_URL = "https://disk.yandex.ru/i/ModbOQOoLMBQvw"
OFFER_URL = "https://disk.yandex.ru/i/Euq939bSwdxUbg"
CONSENT_URL = "https://disk.yandex.ru/i/UhxqVf-LYJBm4w"

# Инициализация
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
router = Router()
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
app = web.Application()

# ================= БАЗА ДАННЫХ =================
async def create_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
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
            last_invoice_id TEXT,
            order_counter INTEGER DEFAULT 0
        )''')
        
        # Таблица счетов
        await db.execute('''CREATE TABLE IF NOT EXISTS invoices (
            inv_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            status TEXT,
            created_at TEXT
        )''')
                
        await db.commit()
    print("База данных готова!")

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
def robokassa_sign(params: dict, password: str) -> str:
    """Вычисляет подпись для Робокассы."""
    items = []
    for key in sorted(params.keys()):
        if key == 'SignatureValue':
            continue
        if key.startswith('Shp_'):
            items.append(f"{key}={params[key]}")
        else:
            items.append(str(params[key]))
    sign_string = ':'.join(items) + ':' + password
    return hashlib.md5(sign_string.encode('cp1251')).hexdigest()

async def save_invoice(inv_id: str, user_id: int, amount: float):
    """Сохраняет счёт в БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO invoices (inv_id, user_id, amount, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (inv_id, user_id, amount, datetime.now().isoformat())
        )
        await db.commit()

async def update_invoice_status(inv_id: str, status: str):
    """Обновляет статус счёта."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE invoices SET status = ? WHERE inv_id = ?", (status, inv_id))
        await db.commit()

async def get_invoice_amount(inv_id: str):
    """Получает сумму счёта."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT amount FROM invoices WHERE inv_id = ?", (inv_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def generate_payment_link_and_save(user_id: int, amount: float = 999):
    """Генерирует ссылку и возвращает (url, inv_id)."""
    inv_id = str(int(datetime.now().timestamp()))
    description = f"Оплата Premium на 30 дней"

    # Данные чека
    receipt_data = {
        "sno": "usn_income",
        "items": [{
            "name": "Premium подписка на 30 дней",
            "quantity": 1,
            "sum": amount,
            "payment_method": "full_payment",
            "payment_object": "service",
            "tax": "vat0"
        }]
    }
    receipt_json = json.dumps(receipt_data, ensure_ascii=False, separators=(',', ':'))
    receipt_encoded_once = quote_plus(receipt_json)
    receipt_encoded_twice = quote_plus(receipt_encoded_once)

    signature_string = (
        f"{ROBOKASSA_LOGIN}:{amount:.2f}:{inv_id}:"
        f"{receipt_encoded_once}:{ROBOKASSA_PASSWORD1}:Shp_user={user_id}"
    )
    signature = hashlib.md5(signature_string.encode('cp1251')).hexdigest()

    params = {
        'MerchantLogin': ROBOKASSA_LOGIN,
        'OutSum': f"{amount:.2f}",
        'InvId': inv_id,
        'Description': description,
        'Receipt': receipt_encoded_twice,
        'SignatureValue': signature,
        'IsTest': '1' if ROBOKASSA_TEST_MODE else '0',
        'Shp_user': str(user_id),
        'Culture': 'ru',
    }
    payment_url = f"{ROBOKASSA_URL}?{urlencode(params)}"

    # Сохраняем счёт
    await save_invoice(inv_id, user_id, amount)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_invoice_id = ? WHERE user_id = ?", (inv_id, user_id))
        await db.commit()

    return payment_url, inv_id  # ВАЖНО: возвращаем кортеж

async def check_payment_async(inv_id: str) -> bool:
    """Асинхронно проверяет статус платежа."""
    try:
        inv_id_int = int(inv_id)
    except ValueError:
        inv_id_int = None

    if inv_id_int is not None:
        signature = hashlib.md5(
            f"{ROBOKASSA_LOGIN}:{inv_id_int}:{ROBOKASSA_PASSWORD2}".encode('cp1251')
        ).hexdigest()
        params = {
            'MerchantLogin': ROBOKASSA_LOGIN,
            'InvId': inv_id_int,
            'Signature': signature
        }
    else:
        signature = hashlib.md5(
            f"{ROBOKASSA_LOGIN}:{inv_id}:{ROBOKASSA_PASSWORD2}".encode('cp1251')
        ).hexdigest()
        params = {
            'MerchantLogin': ROBOKASSA_LOGIN,
            'InvoiceID': inv_id,
            'Signature': signature
        }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(ROBOKASSA_API_URL, params=params, timeout=10) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    root = ET.fromstring(text)
                    state_code = root.findtext('StateCode')
                    if state_code is None:
                        state_code = root.findtext('Code')
                    return state_code and state_code.strip() == '100'
    except Exception as e:
        logging.error(f"Ошибка проверки платежа: {e}")
    return False

async def poll_payment(user_id: int, chat_id: int, inv_id: str):
    """Фоновая проверка платежа."""
    for _ in range(30):  # 5 минут
        if await check_payment_async(inv_id):
            await add_premium(user_id, 30)
            await update_invoice_status(inv_id, 'paid')
            try:
                await bot.send_message(chat_id, "✅ Оплата получена! Premium активирован на 30 дней!")
            except:
                pass
            return
        await asyncio.sleep(10)
    
    try:
        await bot.send_message(
            chat_id,
            "⏰ Платёж пока не подтверждён. Если вы оплатили, проверьте статус позже."
        )
    except:
        pass

async def robokassa_result(request: web.Request):
    """Обработчик вебхука от Робокассы."""
    try:
        data = await request.post()
        logging.info(f"Получен вебхук: {dict(data)}")

        out_sum = data.get('OutSum')
        inv_id = data.get('InvId')
        signature = data.get('SignatureValue')

        if not all([out_sum, inv_id, signature]):
            logging.warning("Отсутствуют обязательные параметры")
            return web.Response(text='BAD', status=400)

        shp_params = {k: v for k, v in data.items() if k.startswith('Shp_')}
        params_for_sign = {'OutSum': out_sum, 'InvId': inv_id, **shp_params}
        expected_sign = robokassa_sign(params_for_sign, ROBOKASSA_PASSWORD2)

        if signature.lower() != expected_sign.lower():
            logging.warning(f"Неверная подпись для inv_id={inv_id}")
            return web.Response(text='BAD', status=400)

        expected_amount = await get_invoice_amount(inv_id)
        if expected_amount is None or float(out_sum) != expected_amount:
            logging.warning(f"Сумма не совпадает: ожидалось {expected_amount}, получено {out_sum}")
            return web.Response(text='BAD', status=400)

        await update_invoice_status(inv_id, 'paid')
        user_id = int(shp_params.get('Shp_user', 0))
        if user_id:
            await add_premium(user_id, 30)
            try:
                await bot.send_message(user_id, "✅ Оплата подтверждена! Premium активирован.")
            except:
                pass

        logging.info(f"Платёж {inv_id} подтверждён")
        return web.Response(text='OK')
    except Exception as e:
        logging.error(f"Ошибка в обработчике вебхука: {e}")
        return web.Response(text='ERROR', status=500)
        
# ---------- Обработчик вебхука ResultURL ----------
async def robokassa_result(request: web.Request):
    """Обрабатывает POST-запрос от Робокассы."""
    try:
        data = await request.post()
        logging.info(f"Получен вебхук: {dict(data)}")

        out_sum = data.get('OutSum')
        inv_id = data.get('InvId')
        signature = data.get('SignatureValue')

        if not out_sum or not inv_id or not signature:
            logging.warning("Отсутствуют обязательные параметры")
            return web.Response(text='BAD', status=400)

        # Собираем Shp_ параметры
        shp_params = {k: v for k, v in data.items() if k.startswith('Shp_')}
        params_for_sign = {
            'OutSum': out_sum,
            'InvId': inv_id,
            **shp_params
        }
        expected_sign = robokassa_sign(params_for_sign, ROBOKASSA_PASSWORD2)

        if signature.lower() != expected_sign.lower():
            logging.warning(f"Неверная подпись для inv_id={inv_id}")
            return web.Response(text='BAD', status=400)

        # Сверяем сумму
        expected_amount = await get_invoice_amount(inv_id)
        if expected_amount is None or float(out_sum) != expected_amount:
            logging.warning(f"Сумма не совпадает: ожидалось {expected_amount}, получено {out_sum}")
            return web.Response(text='BAD', status=400)

        # Обновляем статус и активируем Premium
        await update_invoice_status(inv_id, 'paid')
        user_id = int(shp_params.get('Shp_user', 0))
        if user_id:
            await add_premium(user_id, 30)
            try:
                await bot.send_message(user_id, "✅ Оплата подтверждена! Premium активирован.")
            except:
                pass

        logging.info(f"Платёж {inv_id} подтверждён")
        return web.Response(text='OK')
    except Exception as e:
        logging.error(f"Ошибка в обработчике вебхука: {e}")
        return web.Response(text='ERROR', status=500)


# ---------- Обработчик оплаты Premium ----------
@router.callback_query(F.data == "pay_premium")
async def pay_premium(callback: CallbackQuery):
    user_id = callback.from_user.id
    payment_url, inv_id = await generate_payment_link_and_save(user_id)  # Получаем оба значения

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)],
            [InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data="check_premium_payment")]
        ]
    )
    await callback.message.edit_text(
        "💎 <b>Оплата Premium</b>\n\n"
        "Нажмите кнопку ниже для оплаты.\n"
        "После оплаты Premium активируется автоматически.",
        reply_markup=keyboard
    )
    await callback.answer()

    # Запускаем фоновую проверку
    asyncio.create_task(poll_payment(user_id, callback.message.chat.id, inv_id))


# ---------- Обработчик проверки платежа ----------
@router.callback_query(F.data == "check_premium_payment")
async def check_premium_payment(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT last_invoice_id FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
    
    if not result or not result[0]:
        await callback.message.edit_text("❌ Платёж не найден.")
        await callback.answer()
        return

    inv_id = result[0]
    await callback.message.edit_text("⏳ Проверяю платёж...")
    
    if await check_payment_async(inv_id):
        await add_premium(user_id, 30)
        await update_invoice_status(inv_id, 'paid')
        await callback.message.edit_text("✅ Платёж подтверждён! Premium активирован на 30 дней!")
    else:
        await callback.message.edit_text("❌ Платёж ещё не поступил. Попробуйте позже.")
    
    await callback.answer()
# ---------- Запуск ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS invoices (
                inv_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount REAL,
                status TEXT,
                created_at TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                is_premium INTEGER DEFAULT 0,
                subscription_end TEXT
            )
        ''')
        await db.commit()

async def main():
    await init_db()

    # Вебхук сервер
    app.router.add_post('/robokassa/result', robokassa_result)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()

    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
    
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
    messages = {
        "welcome": {
            "title": "👋 Первое знакомство",
            "texts": [
                (
                    "Я знаю, как это бывает. Ты устала, дети капризничают, и иногда слова срываются с губ "
                    "раньше, чем ты успеваешь подумать. А потом — чувство вины и стыда.\n\n"
                    "Ты не одна. И ты не плохая мама. Ты просто устала, и тебе нужна поддержка.\n\n"
                    "Ты делаешь всё, что в твоих силах. Ты — любящая мама."
                ),
                "Ты не одна. Миллионы мам чувствуют то же самое. Ты не плохая мама — ты просто устала.",
                "Тебе нужна поддержка, а не осуждение. Ты делаешь всё, что в твоих силах.",
                "Ты — любящая мама, даже когда сомневаешься в себе. Ошибки не делают тебя плохой.",
                "Сегодня ты уже сделала много. Позволь себе выдохнуть.",
                "Помни: забота о себе — это не эгоизм, а необходимость.",
                "Ты справляешься. Даже если кажется, что нет, ты всё равно движешься вперёд."
            ]
        },
        "after_sos": {
            "title": "🌸 После SOS-паузы",
            "texts": [
                (
                    "Ты справилась! Ты сделала самое сложное — остановилась. Не дала гневу взять верх. "
                    "Позволила себе дышать.\n\n"
                    "Помни: ты не злишься на ребёнка. Ты устала. Ты перегружена. Ты человек.\n\n"
                    "Ты — хорошая мама. Просто у тебя был тяжёлый день."
                ),
                "Ты сделала важный шаг — позаботилась о себе. Даже одна минута тишины имеет значение. Ты справляешься.",
                "Пауза не решает всё, но она даёт тебе выбор: не реагировать на автомате. Ты уже сильнее, чем за минуту до этого.",
                "Ты остановилась, чтобы не сорваться. Это не слабость, это мудрость. Дыши, ты в безопасности.",
                "Эта пауза — твой вклад в мир в семье. Ты выбрала спокойствие, и это дорогого стоит.",
                "Ты заслуживаешь отдыха и тишины. Разреши себе побыть в этом спокойствии подольше.",
                "Каждый раз, когда ты останавливаешься вместо крика, ты становишься сильнее. Гордись собой."
            ]
        },
        "after_cry": {
            "title": "🫂 После срыва",
            "texts": [
                (
                    "Я знаю, что ты сейчас чувствуешь. Ком в горле, тяжесть в груди, слёзы на глазах. "
                    "Тебе кажется, что ты всё испортила.\n\n"
                    "Это неправда. Ты не ужасная мама. Ты — мама, которая устала. Которая перегружена. "
                    "Которая тоже имеет право на ошибки.\n\n"
                    "Ты сорвалась — это случилось. Но это не конец света. Ты осознала это — это уже большой шаг. "
                    "Ты хочешь всё исправить — это говорит о твоей любви."
                ),
                "Срыв не делает тебя плохой мамой. Он делает тебя живой. Ты можешь извиниться, и это уже шаг к восстановлению.",
                "Виноват не ты, а твоя усталость. Ты не обязана быть идеальной. Ребёнку нужна твоя любовь, а не безупречность.",
                "Ты сорвалась, потому что тебе было очень тяжело. Прости себя. Завтра будет новый день, и ты сможешь начать с объятий.",
                "Позволь себе поплакать, если нужно. Слёзы смывают боль и освобождают место для нежности.",
                "Ты не одна такая. Каждая мама хоть раз срывалась. Это не клеймо, это опыт.",
                "После срыва важно не застрять в вине, а сделать шаг навстречу. Ты уже думаешь об этом — значит, ты на верном пути."
            ]
        },
        "evening": {
            "title": "🌙 Вечерняя поддержка",
            "texts": [
                (
                    "Сегодня был непростой день. Ты устала. Ты сделала всё, что могла. Ты была там для своих детей — "
                    "даже когда это было трудно.\n\n"
                    "Ты заслуживаешь тишины и покоя. Прямо сейчас, перед сном, скажи себе: «Я хорошая мама. "
                    "Я делаю всё, что в моих силах. Я заслуживаю любви и отдыха»."
                ),
                "Этот день закончился. Всё, что ты сделала — достаточно. Остальное может подождать до завтра. Отдыхай.",
                "Ты прожила ещё один день, полный забот. Теперь разреши себе просто лечь и отпустить всё. Ты заслужила покой.",
                "Ночь — твоё время. Пусть мысли утихнут, а тело расслабится. Ты сделала много, теперь просто будь.",
                "Не вини себя за то, что не успела. Ты не робот. Завтра будет новый день, а сейчас — только отдых.",
                "Поблагодари себя за этот день. За каждое объятие, за каждый приготовленный ужин, за каждое проявленное терпение.",
                "Сон — это твой способ восстановиться. Дай себе разрешение уснуть без тревог. Ты это заслужила."
            ]
        },
        "morning": {
            "title": "🌅 Утренняя поддержка",
            "texts": [
                (
                    "Сегодня — новый день. Чистый лист. Вчера было сложно? Забудь. Сегодня ты начинаешь с нуля.\n\n"
                    "Ты достаточно сильная. Достаточно любящая. Достаточно хорошая.\n\n"
                    "Начни день с улыбки и скажи себе: «Сегодня я буду бережной к себе и к своим детям»."
                ),
                "Новый день — это новая попытка. Не нужно с самого утра быть идеальной. Достаточно просто начать.",
                "Ты проснулась, и это уже маленькая победа. Даже если сил мало, помни: ты справишься, шаг за шагом.",
                "Утро не обязано быть продуктивным. Разреши себе медленно войти в день. Ты имеешь право на спокойное начало.",
                "Пусть утро будет добрым к тебе. Выпей тёплый напиток, посмотри в окно, почувствуй, как просыпается мир.",
                "Ты не обязана с утра быть в ресурсе. Просто сделай первый маленький шаг, и день начнёт складываться.",
                "Помни: каждое утро — это шанс начать заново. И ты уже здесь. Ты уже победила."
            ]
        },
        "teen": {
            "title": "👧 Для мам подростков",
            "texts": [
                (
                    "Я знаю, как это бывает. Ты помнишь его маленьким — и вдруг он вырос. Он молчит, когда ты хочешь говорить. "
                    "Он закрывается, когда ты хочешь быть рядом.\n\n"
                    "Это нормально. Это не про тебя. Это про него — про его взросление.\n\n"
                    "Ты не теряешь контакт. Ты учишься новому способу быть рядом. Ты справишься. Ты уже справляешься."
                ),
                "Подросток отталкивает, но это не значит, что ты не нужна. Ему важно знать, что ты рядом, даже если он молчит.",
                "Ты не можешь контролировать его эмоции, но можешь оставаться его опорой. Твоё спокойное присутствие — уже поддержка.",
                "Этот возраст — буря для вас обоих. Ты не враг, ты гавань. Просто будь рядом, и однажды он это оценит.",
                "Не принимай его грубость на свой счёт. Он борется с собой, а не с тобой.",
                "Твоя любовь сейчас — это уважение его границ и вера в него. Это сложно, но это и есть взрослая любовь.",
                "Когда-нибудь он вернётся к тебе с благодарностью. А пока просто будь тем, кто не отвернулся."
            ]
        },
        "adult": {
            "title": "👩 Для мам взрослых детей",
            "texts": [
                (
                    "Ты вырастила его. Ты дала ему всё, что могла. А теперь он живёт своей жизнью. И это правильно.\n\n"
                    "Но иногда трудно отпустить. Трудно смотреть, как он ошибается. Трудно не вмешиваться.\n\n"
                    "Помни: он взрослый человек. Он имеет право на свой путь. Твоя любовь не должна быть контролем. "
                    "Ты уже дала ему всё, что было нужно. Теперь твоя задача — верить в него."
                ),
                "Ты вырастила человека, и это навсегда твоя заслуга. Теперь пришло время доверять его пути, даже если он не такой, как ты мечтала.",
                "Быть мамой взрослого ребёнка — значит любить на расстоянии. Ты дала ему корни и крылья, теперь позволь ему лететь.",
                "Ты сделала всё, что могла, а в чём-то ошибалась — это по-человечески. Отпусти чувство ответственности за его выборы. Ты по-прежнему важна, но иначе.",
                "Твоя роль изменилась, но не стала меньше. Ты — тихая гавань, куда он может вернуться, когда захочет.",
                "Не обесценивай свой вклад. Даже если он не звонит каждый день, он знает, что ты есть. Это многое значит.",
                "Ты имеешь право на свою жизнь. Отпусти его — и освободи место для собственной радости."
            ]
        },
        "baby": {
            "title": "🍼 Для мам в декрете",
            "texts": [
                (
                    "Ты — супергерой. Ты не спишь ночами, кормишь, укачиваешь, успокаиваешь. Ты даёшь всё, что у тебя есть.\n\n"
                    "Ты даёшь своему малышу самое главное — любовь и безопасность. Ты его мир. Ты его дом.\n\n"
                    "Ты можешь уставать. Ты можешь плакать. Ты можешь просить о помощи. Это нормально. Ты не одна."
                ),
                "Декрет — это не перерыв в жизни, это другая жизнь, часто невидимая и изматывающая. Твоя усталость оправдана, ты не обязана всё успевать.",
                "Ты целый день заботишься о малыше, и это огромный труд, даже если кажется, что ты ничего не сделала. Ты делаешь самое важное.",
                "Однообразные будни с ребёнком могут сводить с ума, но ты не одна. Твоя работа бесценна, даже если её не видно.",
                "Позволь себе не быть идеальной мамой. Малышу нужна не идеальность, а твоё присутствие и тепло.",
                "Каждый день ты учишься чему-то новому вместе с ребёнком. Это ценно, даже если усталость затмевает радость.",
                "Помни, что забота о себе — это забота о малыше. Отдохнувшая мама — лучший подарок для ребёнка."
            ]
        },
        "tired": {
            "title": "😴 Кто не выспался",
            "texts": [
                (
                    "Сегодня ты снова не выспалась. Ты встала рано, день только начался, а ты уже устала.\n\n"
                    "Прямо сейчас остановись на 5 минут. Выпей воды. Посмотри в окно. Скажи себе: «Я справлюсь. Я делаю всё, что могу».\n\n"
                    "Ты не обязана быть энергичной 24/7. Ты человек, а не робот. Ты — замечательная мама. Даже если ты устала. Особенно когда ты устала."
                ),
                "Недосып — это пытка. Ты имеешь право чувствовать разбитость и злость. Просто делай минимум, остальное неважно.",
                "Сегодня ты можешь быть медленной, забывчивой, раздражительной. Это не твой характер, это усталость. Будь к себе мягче.",
                "Сон — базовая потребность, а не роскошь. Если сегодня нет возможности выспаться, просто разреши себе не геройствовать.",
                "Позволь себе попросить о помощи или отложить дела. Ты не обязана тащить всё на себе, особенно когда нет сил.",
                "Усталость — это сигнал тела, а не слабость. Прислушайся к нему. Даже 10 минут отдыха могут немного восстановить.",
                "Ты удивительная уже тем, что продолжаешь заботиться о других, несмотря на усталость. Но не забывай и о себе."
            ]
        },
        "lost": {
            "title": "💫 Кто чувствует себя разбитой или потерянной",
            "texts": [
                "Это нормально — чувствовать себя не в своей тарелке. Ты не сломалась, ты просто устала быть сильной.",
                "Твои чувства имеют значение. Даже если внутри путаница, ты достойна передышки и заботы.",
                "Не обязательно знать, что делать дальше. Иногда достаточно просто побыть в этом состоянии, не осуждая себя.",
                "Ты не потеряна навсегда. Это просто момент, когда нужно остановиться и прислушаться к себе.",
                "Растерянность — это не тупик, а перекрёсток. Позволь себе не выбирать прямо сейчас.",
                "Ты имеешь право не быть в порядке. Это не значит, что ты сломалась. Просто дай себе время.",
                "Помни: даже в самые тёмные времена ты остаёшься собой. И это уже опора."
            ]
        },
        "not_enough": {
            "title": "💔 Для тех, кто чувствует себя недостаточно хорошей мамой",
            "texts": [
                (
                    "Ты когда-нибудь ловила себя на мысли: «Я недостаточно хорошая мама»? Я знаю, что ловила. И я знаю, что это неправда.\n\n"
                    "Ты волнуешься — значит, тебе не всё равно. Ты переживаешь — значит, ты любишь. Ты хочешь быть лучше — значит, ты уже хорошая.\n\n"
                    "Недостаточно хорошая мама не ищет поддержки. Она не волнуется о чувствах ребёнка. Она не хочет меняться. Ты — всё это делаешь."
                ),
                "Ты не обязана быть идеальной, чтобы быть любимой. Твои старания уже видны твоему ребёнку, даже если он не говорит.",
                "Мысль «я плохая мать» приходит к тем, кто очень хочет быть хорошей. Ты больше, чем твои ошибки.",
                "Твой ребёнок не сравнивает тебя с идеалом. Для него ты — весь мир. Ты уже достаточно.",
                "Сравнивать себя с другими — путь к разочарованию. У тебя своя история, свой ритм, своя любовь.",
                "Ты имеешь право на плохие дни. Это не отменяет того, что ты хорошая мама.",
                "Поверь в себя так, как верит в тебя твой ребёнок. Он не знает никакой другой мамы — и он любит именно тебя."
            ]
        },
        "guilt": {
            "title": "🫂 Кто устал от вины",
            "texts": [
                (
                    "Чувство вины — это тяжёлый груз. «Я не так посмотрела», «Я не то сказала», «Я слишком много кричала».\n\n"
                    "Позволь мне сказать тебе: ты не должна быть идеальной. Ты не должна быть спокойной 24/7. Ты не должна всё успевать.\n\n"
                    "Ты имеешь право на ошибки. Ты имеешь право на усталость. Ты имеешь право на свои чувства. Ты — хорошая мама. "
                    "Ты заслуживаешь прощения — особенно от самой себя."
                ),
                "Вина выматывает сильнее, чем сам поступок. Ты не обязана нести её вечно. Прости себя и сделай маленький шаг к исправлению.",
                "Чувство вины — признак того, что тебе не всё равно. Но оно не должно становиться твоим постоянным спутником. Ты заслуживаешь мира.",
                "Ты не можешь изменить прошлое, но можешь быть добрее к себе сейчас. Вина не помогает, а любовь к себе — помогает.",
                "Вина не делает тебя лучше, она только крадёт энергию. Направь её на заботу о себе и близких.",
                "Позволь себе быть несовершенной. Это не значит, что ты плохая. Это значит, что ты человек.",
                "Прощение себя — это не оправдание ошибок, а освобождение от их власти. Ты достойна этого освобождения."
            ]
        },
        "alone": {
            "title": "🫂 Кто чувствует себя одинокой",
            "texts": [
                (
                    "Я знаю, что иногда ты чувствуешь себя одинокой. Даже когда рядом дети. Даже когда вокруг люди.\n\n"
                    "Ты думаешь: «Я одна не справлюсь», «Меня никто не понимает», «Я устала быть сильной».\n\n"
                    "Ты не одна. Ты часть большого сообщества мам, которые тоже устают, тоже срываются, тоже хотят тишины и покоя. "
                    "Ты сильная. Ты справляешься. Ты достойна любви."
                ),
                "Одиночество в материнстве — частое чувство, хотя о нём молчат. Ты не одна, даже если кажется иначе. Просто рядом нет тех, кто понимает.",
                "Ты можешь быть окружена людьми и всё равно чувствовать пустоту. Это не странно, это по-человечески. Позволь себе искать поддержку, даже виртуальную.",
                "Твоя ценность не зависит от того, сколько людей рядом. Ты важна сама по себе, и этот бот всегда здесь для тебя.",
                "Одиночество — это не отсутствие людей, а отсутствие понимания. Но ты можешь найти его, если позволишь себе открыться.",
                "Ты не обязана справляться в одиночку. Попросить о помощи — не слабость, а мудрость.",
                "Даже если сейчас ты чувствуешь себя одинокой, знай: твоя забота о детях — это вклад в мир, который важен."
            ]
        },
        "self_care": {
            "title": "💝 Забота о себе",
            "texts": [
                (
                    "Ты так много даёшь своим детям. Но кто даёт тебе? Прямо сейчас напомни себе: ты заслуживаешь заботы. "
                    "Ты заслуживаешь любви. Ты заслуживаешь отдыха.\n\n"
                    "Позволь себе сделать что-то для себя сегодня. Просто 15 минут. Чтобы выдохнуть. Чтобы почувствовать себя собой.\n\n"
                    "Ты — не только мама. Ты — женщина. Ты — личность. Ты имеешь право на свою жизнь."
                ),
                "Ты так много даёшь своим детям. Но кто даёт тебе?",
                "Ты заслуживаешь заботы и любви. Ты заслуживаешь отдыха.",
                "Позволь себе сделать что-то для себя сегодня. Хотя бы 15 минут тишины.",
                "Забота о себе — это не роскошь, а необходимость. Ты не можешь наливать из пустой чашки.",
                "Найди маленькую радость для себя: чашку чая в тишине, книгу, прогулку. Ты это заслужила.",
                "Ты важна не только как мама, но и как человек. Удели время себе — это не эгоизм, а мудрость."
            ]
        }
    }
    return messages.get(category, messages["welcome"])

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
    advice = {
        "age_1_3": {
            "title": "👶 Дети 1-3 года",
            "tips": [
                "🧸 <b>Кризис 3 лет</b> — это нормально.",
                "🫂 <b>Обнимайте чаще</b>.",
                "🔄 <b>Переключайте внимание</b>.",
                "😌 <b>Сохраняйте спокойствие</b>.",
                "🗣️ <b>Говорите простыми фразами</b>.",
                "⏰ <b>Режим дня</b> — основа спокойствия.",
                "🎮 <b>Игра</b> — главный способ обучения."
            ],
            "restore_techniques": [
                "🤗 <b>Обними-меня</b>",
                "🔄 <b>Переключение</b>",
                "🧸 <b>Игрушка-мирилка</b>"
            ]
        },
        "age_4_6": {
            "title": "🧒 Дети 4-6 лет",
            "tips": [
                "🎨 <b>Развивайте фантазию</b>.",
                "🤝 <b>Учите договариваться</b>.",
                "📖 <b>Читайте вместе</b>.",
                "⏱️ <b>Давайте выбор</b>.",
                "👂 <b>Слушайте внимательно</b>.",
                "🏆 <b>Хвалите за старания</b>.",
                "😤 <b>Истерики</b> — способ выразить эмоции."
            ],
            "restore_techniques": [
                "🤗 <b>Обними-меня</b>",
                "🎨 <b>Рисуем обиду</b>",
                "🌊 <b>Дыхание вместе</b>"
            ]
        },
        "age_7_10": {
            "title": "👦 Дети 7-10 лет",
            "tips": [
                "📚 <b>Школа</b> — новый стресс.",
                "🤗 <b>Поддерживайте дружбу</b>.",
                "⏰ <b>Учите планировать время</b>.",
                "🗣️ <b>Обсуждайте чувства</b>.",
                "🎮 <b>Игры и спорт</b> помогают.",
                "📱 <b>Устанавливайте правила</b>.",
                "💪 <b>Хвалите за самостоятельность</b>."
            ],
            "restore_techniques": [
                "🤗 <b>Обними-меня</b>",
                "🎨 <b>Рисуем обиду</b>",
                "🧩 <b>Стоп-фраза</b>"
            ]
        },
        "age_11_14": {
            "title": "👧 Дети 11-14 лет",
            "tips": [
                "🔥 <b>Подростковый кризис</b> — норма.",
                "🤝 <b>Будьте другом</b>.",
                "🗣️ <b>Не критикуйте внешность</b>.",
                "📱 <b>Интернет-безопасность</b>.",
                "👂 <b>Слушайте без осуждения</b>.",
                "💪 <b>Давайте свободу</b>.",
                "💕 <b>Говорите о любви</b>."
            ],
            "restore_techniques": [
                "🛑 <b>Стоп-сигнал</b>",
                "✍️ <b>Я-сообщение</b>",
                "🍲 <b>Действие без слов</b>"
            ]
        },
        "age_15_18": {
            "title": "🧑 Дети 15-18 лет",
            "tips": [
                "🎯 <b>Поддерживайте выбор профессии</b>.",
                "🤝 <b>Отношения на равных</b>.",
                "🗣️ <b>Обсуждайте будущее</b>.",
                "💕 <b>Говорите о чувствах</b>.",
                "📱 <b>Доверяйте, но проверяйте</b>.",
                "💪 <b>Поддерживайте самостоятельность</b>.",
                "❤️ <b>Будьте рядом</b>."
            ],
            "restore_techniques": [
                "🛑 <b>Стоп-сигнал</b>",
                "✍️ <b>Я-сообщение</b>",
                "🍲 <b>Действие без слов</b>"
            ]
        },
        "age_18_plus": {
            "title": "👩 Взрослые дети (18+ лет)",
            "tips": [
                "🤝 <b>Отношения на равных</b>.",
                "🗣️ <b>Советуйте, но не навязывайте</b>.",
                "🫂 <b>Будьте опорой</b>.",
                "💕 <b>Принимайте выборы</b>.",
                "📱 <b>Не вмешивайтесь</b>.",
                "💰 <b>Финансовая поддержка</b> должна уменьшаться.",
                "👂 <b>Слушайте без осуждения</b>.",
                "💝 <b>Продолжайте проявлять любовь</b>.",
                "🔄 <b>Пересмотрите роль «родитель»</b>.",
                "🙏 <b>Прощайте ошибки</b>.",
                "💬 <b>Учитесь диалогу</b>.",
                "🌟 <b>Радуйтесь успехам</b>."
            ],
            "restore_techniques": [
                "🛑 <b>Стоп-сигнал</b>",
                "✍️ <b>Я-сообщение</b>",
                "🍲 <b>Действие без слов</b>"
            ]
        },
        "age_general": {
            "title": "📚 Общие рекомендации для всех возрастов",
            "tips": [
                "❤️ <b>Безусловная любовь</b>.",
                "👂 <b>Слушайте активно</b>.",
                "😌 <b>Контролируйте свои эмоции</b>.",
                "🔄 <b>Устанавливайте чёткие границы</b>.",
                "🤗 <b>Обнимайте каждый день</b>.",
                "📖 <b>Читайте и обсуждайте</b>.",
                "🙏 <b>Будьте примером</b>."
            ],
            "restore_techniques": [
                "🛑 <b>Стоп-сигнал</b>",
                "✍️ <b>Я-сообщение</b>",
                "🍲 <b>Действие без слов</b>"
            ]
        }
    }
    return advice.get(age_group, advice["age_general"])

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
    if await has_agreed_to_terms(user_id) and user_id not in ADMINS:
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
        "Здесь безопасно и конфиденциально.\n\n"
        "⚠️ <b>Обращаем внимание</b>\n"
        "PauseMomBot — это информационный помощник для мамы. "
        "Все техники и рекомендации носят ознакомительный и общеразвивающий характер.\n\n"
        "Бот не является медицинским или психотерапевтическим инструментом, "
        "не ставит диагнозы и не назначает лечение, "
        "не заменяет профессиональную помощь психолога или врача.\n\n"
    
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
    # Проверяем, не является ли callback "back_to_support"
    if callback.data == "back_to_support":
        await back_to_support(callback)
        return
    
    category = callback.data.replace('support_', '')
    message_data = get_support_message(category)
    
    if not message_data:
        await callback.answer("Категория не найдена")
        return
    
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
    
    await callback.message.edit_text(
        "💝 <b>Нужные слова для мамы</b>\n\nВыбери категорию, которая откликается тебе сейчас:",
        reply_markup=keyboard
    )
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
            "✅ 100 аффирмаций поддержки\n"
            "✅ Поддерживающие фразы на каждый день недели (7 фраз в каждой категории)",
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
            [InlineKeyboardButton(text="❓ Как оплатить?", callback_data="payment_help")]
        ]
    )

    await message.answer(
        "💎 <b>Premium — 999 ₽/мес</b>\n\n"
        "✨ <b>Что ты получаешь:</b>\n"
        "✅ Техники для малышей (1-12 лет)\n"
        "✅ Модуль «Восстановление контакта»\n"
        "✅ 100 аффирмаций поддержки\n"
        "✅ Поддерживающие фразы на каждый день недели (7 фраз в каждой категории)\n\n"
        "💳 Нажмите «Оплатить», чтобы перейти к оплате через Робокассу.",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "pay_premium")
async def pay_premium(callback: CallbackQuery):
    user_id = callback.from_user.id
    payment_url, inv_id = await generate_payment_link_and_save(user_id)

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

    # Запускаем фоновую проверку
    asyncio.create_task(poll_payment(user_id, callback.message.chat.id, inv_id))


@router.callback_query(F.data == "check_premium_payment")
async def check_premium_payment(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT last_invoice_id FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
    
    if not result or not result[0]:
        await callback.message.edit_text("❌ Платёж не найден.")
        await callback.answer()
        return

    inv_id = result[0]
    await callback.message.edit_text("⏳ Проверяю платёж...")
    
    if await check_payment_async(inv_id):
        await add_premium(user_id, 30)
        await update_invoice_status(inv_id, 'paid')
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
    await callback.answer()


@router.callback_query(F.data == "back_to_premium")
async def back_to_premium(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if await is_premium(user_id):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
            result = await cursor.fetchone()
        if result and result[0]:
            await callback.message.edit_text(
                f"✅ <b>У тебя уже есть Premium!</b>\n\n"
                f"📅 Действует до: {result[0]}\n\n"
                "Пользуйся всеми функциями без ограничений."
            )
            await callback.answer()
            return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", callback_data="pay_premium")],
            [InlineKeyboardButton(text="❓ Как оплатить?", callback_data="payment_help")]
        ]
    )
    
    await callback.message.edit_text(
        "💎 <b>Premium — 999 ₽/мес</b>\n\n"
        "✨ <b>Что ты получаешь:</b>\n"
        "✅ Техники для малышей (1-12 лет)\n"
        "✅ Модуль «Восстановление контакта»\n"
        "✅ 100 аффирмаций поддержки\n"
        "✅ Поддерживающие фразы на каждый день недели (7 фраз в каждой категории)\n\n"
        "💳 Нажмите «Оплатить», чтобы перейти к оплате через Робокассу.",
        reply_markup=keyboard
    )
    await callback.answer()

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
async def main():
    logging.basicConfig(level=logging.INFO)
    
    # Инициализация БД
    await create_tables()
    
    # Добавляем поле order_counter, если его ещё нет
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'order_counter' not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN order_counter INTEGER DEFAULT 0")
            await db.commit()
            print("Поле order_counter добавлено")
    
    print("База данных готова!")
    
    # Подключаем роутер
    dp.include_router(router)
    
    # Запускаем бота БЕЗ веб-сервера (рекомендуется для Bothost)
    print("Бот запущен!")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Ошибка polling: {e}")
    finally:
        await bot.session.close()
        logging.info("Бот остановлен")


if __name__ == '__main__':
    # Для Bothost: НЕ используйте asyncio.run()
    # Вместо этого используйте:
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
