import asyncio
import hashlib
import json
import os
import logging
from datetime import datetime, timedelta, date
from urllib.parse import urlencode, quote_plus

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

# Загружаем секреты
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

DB_PATH = "pause_bot.db"

# Ссылки на документы
POLICY_URL = "https://disk.yandex.ru/i/ModbOQOoLMBQvw"
OFFER_URL = "https://disk.yandex.ru/i/Euq939bSwdxUbg"
CONSENT_URL = "https://disk.yandex.ru/i/UhxqVf-LYJBm4w"

# ================= ИНИЦИАЛИЗАЦИЯ =================
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
            last_invoice_id TEXT,
            order_counter INTEGER DEFAULT 0
        )''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS invoices (
            inv_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            status TEXT,
            created_at TEXT
        )''')
        
        await db.commit()
    logging.info("База данных готова!")

async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()

async def is_premium(user_id):
    if user_id in ADMINS:
        return True
    user = await get_user(user_id)
    if user and user[4]:
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
    return result[0] == 1 if result else False

async def set_agreed_to_terms(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET agreed_to_terms = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

# ================= РОБОКАССА =================
def robokassa_sign(params: dict, password: str) -> str:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO invoices (inv_id, user_id, amount, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (inv_id, user_id, amount, datetime.now().isoformat())
        )
        await db.commit()

async def update_invoice_status(inv_id: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE invoices SET status = ? WHERE inv_id = ?", (status, inv_id))
        await db.commit()

async def get_invoice_amount(inv_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT amount FROM invoices WHERE inv_id = ?", (inv_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def generate_payment_link_and_save(user_id: int, amount: float = 999):
    inv_id = str(int(datetime.now().timestamp()))
    description = "Оплата Premium на 30 дней"

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

    await save_invoice(inv_id, user_id, amount)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_invoice_id = ? WHERE user_id = ?", (inv_id, user_id))
        await db.commit()

    return payment_url, inv_id

async def check_payment_async(inv_id: str) -> bool:
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
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(text)
                    state_code = root.findtext('StateCode')
                    if state_code is None:
                        state_code = root.findtext('Code')
                    return state_code and state_code.strip() == '100'
    except Exception as e:
        logging.error(f"Ошибка проверки платежа: {e}")
    return False

async def poll_payment(user_id: int, chat_id: int, inv_id: str):
    for _ in range(30):
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

# ================= ОБРАБОТЧИК ВЕБХУКА =================
async def robokassa_result(request: web.Request):
    try:
        data = await request.post()
        logging.info(f"Получен вебхук: {dict(data)}")

        out_sum = data.get('OutSum')
        inv_id = data.get('InvId')
        signature = data.get('SignatureValue')

        if not all([out_sum, inv_id, signature]):
            return web.Response(text='BAD', status=400)

        shp_params = {k: v for k, v in data.items() if k.startswith('Shp_')}
        params_for_sign = {'OutSum': out_sum, 'InvId': inv_id, **shp_params}
        expected_sign = robokassa_sign(params_for_sign, ROBOKASSA_PASSWORD2)

        if signature.lower() != expected_sign.lower():
            logging.warning(f"Неверная подпись для {inv_id}")
            return web.Response(text='BAD', status=400)

        expected_amount = await get_invoice_amount(inv_id)
        if expected_amount is None or float(out_sum) != expected_amount:
            logging.warning(f"Сумма не совпадает для {inv_id}")
            return web.Response(text='BAD', status=400)

        await update_invoice_status(inv_id, 'paid')
        user_id = int(shp_params.get('Shp_user', 0))
        if user_id:
            await add_premium(user_id, 30)
            try:
                await bot.send_message(user_id, "✅ Оплата подтверждена! Premium активирован.")
            except:
                pass

        return web.Response(text='OK')
    except Exception as e:
        logging.error(f"Ошибка вебхука: {e}")
        return web.Response(text='ERROR', status=500)

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

# ================= ОБРАБОТЧИКИ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or "Аноним"
    first_name = message.from_user.first_name or "Мама"

    args = message.text.split()
    if len(args) > 1:
        ref_code = args[1]
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
            referrer = await cursor.fetchone()
            if referrer and referrer[0] != user_id:
                await db.execute("UPDATE users SET referrer_id = ? WHERE user_id = ?", (referrer[0], user_id))
                await db.commit()
                try:
                    await bot.send_message(referrer[0], "👏 По твоей ссылке пришла новая мама!")
                except:
                    pass

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, date.today().strftime('%Y-%m-%d'))
        )
        await db.commit()

    await generate_referral_code(user_id)

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
        "Нажми «Здесь безопасно», чтобы продолжить ✨"
    )
    await message.answer(welcome_text, reply_markup=keyboard)

# ================= PREMIUM =================
@router.message(F.text == "💎 Premium")
async def premium_info(message: Message):
    user_id = message.from_user.id

    if user_id in ADMINS:
        if not await is_premium(user_id):
            await add_premium(user_id, 36500)
        await message.answer("👑 Premium активирован!", reply_markup=main_keyboard(user_id))
        return

    if await is_premium(user_id):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
            result = await cursor.fetchone()
        if result and result[0]:
            await message.answer(
                f"✅ <b>У тебя уже есть Premium!</b>\n\n📅 Действует до: {result[0]}",
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
        "✨ Что ты получаешь:\n"
        "✅ Техники для малышей (1-12 лет)\n"
        "✅ Модуль «Восстановление контакта»\n"
        "✅ 100 аффирмаций поддержки\n\n"
        "💳 Нажмите «Оплатить» для перехода к оплате.",
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
        "Нажмите кнопку ниже для оплаты.\n"
        "После оплаты Premium активируется автоматически.",
        reply_markup=keyboard
    )
    await callback.answer()

    asyncio.create_task(poll_payment(user_id, callback.message.chat.id, inv_id))

@router.callback_query(F.data == "check_premium_payment")
async def check_premium_payment(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT last_invoice_id FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
    
    if not result or not result[0]:
        await callback.message.edit_text("❌ Платёж не найден.")
        return

    inv_id = result[0]
    await callback.message.edit_text("⏳ Проверяю платёж...")
    
    if await check_payment_async(inv_id):
        await add_premium(user_id, 30)
        await update_invoice_status(inv_id, 'paid')
        await callback.message.edit_text("✅ Платёж подтверждён! Premium активирован на 30 дней!")
    else:
        await callback.message.edit_text("❌ Платёж ещё не поступил.")
    await callback.answer()

@router.callback_query(F.data == "payment_help")
async def payment_help(callback: CallbackQuery):
    await callback.message.edit_text(
        "❓ <b>Как оплатить Premium:</b>\n\n"
        "1️⃣ Нажмите «Оплатить»\n"
        "2️⃣ Перейдите по ссылке\n"
        "3️⃣ Оплатите удобным способом\n"
        "4️⃣ Premium активируется автоматически"
    )
    await callback.answer()

# ================= ЗАПУСК =================
async def main():
    logging.basicConfig(level=logging.INFO)
    
    # Инициализация БД
    await create_tables()
    
    # Подключаем роутер
    dp.include_router(router)
    
    # Создаём веб-приложение
    app = web.Application()
    app.router.add_post('/robokassa/result', robokassa_result)
    app.router.add_get('/', lambda r: web.Response(text='Bot is running'))
    
    # Запускаем веб-сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logging.info("Вебхук сервер запущен на порту 8080")
    
    print("Бот запущен!")
    
    # Запускаем бота
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
