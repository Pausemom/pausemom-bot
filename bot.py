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

API_TOKEN = os.getenv('BOT_TOKEN')

conn = sqlite3.connect('pause_bot.db', check_same_thread=False)
cursor = conn.cursor()

# ===== СОЗДАНИЕ ТАБЛИЦ =====
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
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
    agreed_to_terms BOOLEAN DEFAULT 0
)''')
conn.commit()

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ===== ID АДМИНА =====
ADMINS = [1076773869]  # Ваш Telegram ID

# ===== СОСТОЯНИЯ =====
class Form(StatesGroup):
    restore_step1 = State()
    restore_step2 = State()
    restore_step3 = State()
    waiting_for_terms = State()

# ===== ПРОВЕРКА PREMIUM (С УЧЁТОМ АДМИНА) =====
def is_premium(user_id):
    # Админы всегда имеют Premium
    if user_id in ADMINS:
        return True
    try:
        cursor.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if result and result[0]:
            end_date = datetime.strptime(result[0], '%Y-%m-%d')
            if end_date >= datetime.now().date():
                return True
        return False
    except:
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

def has_agreed_to_terms(user_id):
    cursor.execute("SELECT agreed_to_terms FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result:
        return result[0] == 1
    return False

def set_agreed_to_terms(user_id):
    cursor.execute("UPDATE users SET agreed_to_terms = 1 WHERE user_id = ?", (user_id,))
    conn.commit()

# ===== ГЛАВНАЯ КЛАВИАТУРА =====
def main_keyboard(user_id):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    keyboard.add(
        KeyboardButton("🆘 SOS-Пауза"),
        KeyboardButton("💎 Premium")
    )
    keyboard.add(
        KeyboardButton("👥 Пригласить подругу"),
        KeyboardButton("💝 Нужные слова для мамы")
    )
    keyboard.add(
        KeyboardButton("🧸 Техники для малышей"),
        KeyboardButton("🤝 Восстановить контакт")
    )
    keyboard.add(
        KeyboardButton("📚 Общие рекомендации по возрасту"),
        KeyboardButton("📞 Помощь")
    )
    
    # Аффирмация дня — видна всем, но доступ только Premium/админам
    keyboard.add(
        KeyboardButton("🌅 Аффирмация дня")
    )
    
    return keyboard

# ===== КЛАВИАТУРА SOS =====
def sos_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    keyboard.add(
        KeyboardButton("🔙 Главное меню")
    )
    keyboard.add(
        KeyboardButton("🌬️ Дыхание 4-7-8"),
        KeyboardButton("🧘 Осознанное дыхание")
    )
    keyboard.add(
        KeyboardButton("👀 5-4-3-2-1"),
        KeyboardButton("🤗 Обнять себя")
    )
    keyboard.add(
        KeyboardButton("💧 Умыться водой"),
        KeyboardButton("🦶 Стойка на ногах")
    )
    keyboard.add(
        KeyboardButton("🧠 Сканирование тела"),
        KeyboardButton("☀️ Луч света")
    )
    keyboard.add(
        KeyboardButton("🌊 Волна дыхания"),
        KeyboardButton("💭 Наблюдатель")
    )
    
    return keyboard

# ===== ДИСКЛЕЙМЕР =====
DISCLAIMER = (
    "📋 **О боте PauseMomBot**\n\n"
    "PauseMomBot — это информационный помощник для родителей. "
    "Все техники и рекомендации носят ознакомительный и общеразвивающий характер.\n\n"
    "⚠️ **Важно:**\n"
    "• Бот не является медицинским или психотерапевтическим инструментом\n"
    "• Бот не ставит диагнозы и не назначает лечение\n"
    "• Бот не заменяет профессиональную помощь психолога или врача\n\n"
    "📱 Поддержка: @PauseMomSupport_bot"
)

# ===== ТЕХНИКИ ДЛЯ МАЛЫШЕЙ =====
def get_kids_techniques(age_group):
    techniques = {
        "1_3": {
            "title": "👶 Техники для детей 1-3 года",
            "tips": [
                "🤗 **Техника «Обними-меня»**\n\nКогда ребёнок расстроен — протяни ему руки.",
                "🔄 **Техника «Переключение»**\n\nОтвлеки внимание ребёнка на что-то интересное.",
                "🧸 **Техника «Игрушка-мирилка»**\n\nВозьми его любимую игрушку и помиритесь через неё.",
                "📖 **Техника «Сказка про меня»**\n\nРасскажи историю о зверюшке, который помирился с мамой."
            ]
        },
        "4_6": {
            "title": "🧒 Техники для детей 4-6 лет",
            "tips": [
                "🤗 **Техника «Обними-меня»**\n\nСядь на уровень ребёнка и протяни руки.",
                "🎨 **Техника «Рисуем обиду»**\n\nНарисуйте злость и порвите рисунок.",
                "🎭 **Техника «Игра в чувства»**\n\nНазывай чувства, ребёнок показывает их лицом.",
                "🌊 **Техника «Дыхание вместе»**\n\nПодышите вместе как волны океана.",
                "🧩 **Техника «Пять минут вместе»**\n\nПроведи 5 минут только с ребёнком."
            ]
        },
        "7_9": {
            "title": "👦 Техники для детей 7-9 лет",
            "tips": [
                "🤗 **Техника «Обними-меня»**\n\nСпроси: «Обняться или поговорить?»",
                "🎨 **Техника «Рисуем обиду»**\n\nНарисуй свои чувства.",
                "🎭 **Техника «Игра в чувства»**\n\nПоиграйте в угадывание эмоций.",
                "🧩 **Техника «Стоп-фраза»**\n\nПридумайте слово, которое останавливает ссору.",
                "🔄 **Техника «Круг благодарности»**\n\nНапишите друг другу, за что вы благодарны."
            ]
        },
        "10_12": {
            "title": "👦 Техники для детей 10-12 лет",
            "tips": [
                "💬 **Техника «Я-сообщение +»**\n\n«Я чувствую... (свои чувства) Потому что... (причина) Я знаю, что ты... (чувства ребёнка) Давай... (предложение)»\n\nПример: «Я чувствую усталость, потому что у меня был тяжёлый день. Я знаю, что ты тоже устал. Давай вместе почитаем книгу и обнимемся?»",
                "✨ **Техника «Светящиеся руки»**\n\n1️⃣ Потри ладони друг о друга, чтобы они стали тёплыми.\n2️⃣ Представь, что от них исходит тёплый золотистый свет.\n3️⃣ Подойди к ребёнку и мягко положи руку на его плечо.\n4️⃣ Скажи: «Я рядом. Я тебя люблю». Постой так 10 секунд.\n\n💡 Тепло рук передаёт любовь без слов.",
                "☁️ **Техника «Облако мира»**\n\n1️⃣ Закрой глаза.\n2️⃣ Представь большое пушистое облако.\n3️⃣ Мысленно положи в это облако свою обиду, гнев, раздражение.\n4️⃣ Посмотри, как облако медленно уплывает, унося все негативные чувства.\n5️⃣ Открой глаза и скажи: «Я отпускаю всё плохое. Я начинаю заново»."
            ]
        }
    }
    return techniques.get(age_group, techniques["4_6"])

# ===== 100 АФФИРМАЦИЙ =====
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

# ===== ПОДДЕРЖИВАЮЩИЕ СООБЩЕНИЯ ДЛЯ МАМЫ =====
def get_support_message(category):
    messages = {
        "welcome": {
            "title": "👋 Первое знакомство",
            "text": "Я знаю, как это бывает. Ты устала, дети капризничают, и иногда слова срываются с губ раньше, чем ты успеваешь подумать. А потом — чувство вины и стыда.\n\nТы не одна. И ты не плохая мама. Ты просто устала, и тебе нужна поддержка.\n\nТы делаешь всё, что в твоих силах. Ты — любящая мама."
        },
        "after_sos": {
            "title": "🌸 После SOS-паузы",
            "text": "Ты справилась! Ты сделала самое сложное — остановилась. Не дала гневу взять верх. Позволила себе дышать.\n\nПомни: ты не злишься на ребёнка. Ты устала. Ты перегружена. Ты человек.\n\nТы — хорошая мама. Просто у тебя был тяжёлый день."
        },
        "after_cry": {
            "title": "🫂 После срыва",
            "text": "Я знаю, что ты сейчас чувствуешь. Ком в горле, тяжесть в груди, слёзы на глазах. Тебе кажется, что ты всё испортила.\n\nЭто неправда. Ты не ужасная мама. Ты — мама, которая устала. Которая перегружена. Которая тоже имеет право на ошибки.\n\nТы сорвалась — это случилось. Но это не конец света. Ты осознала это — это уже большой шаг. Ты хочешь всё исправить — это говорит о твоей любви."
        },
        "evening": {
            "title": "🌙 Вечерняя поддержка",
            "text": "Сегодня был непростой день. Ты устала. Ты сделала всё, что могла. Ты была там для своих детей — даже когда это было трудно.\n\nТы заслуживаешь тишины и покоя. Прямо сейчас, перед сном, скажи себе: «Я хорошая мама. Я делаю всё, что в моих силах. Я заслуживаю любви и отдыха»."
        },
        "morning": {
            "title": "🌅 Утренняя поддержка",
            "text": "Сегодня — новый день. Чистый лист. Вчера было сложно? Забудь. Сегодня ты начинаешь с нуля.\n\nТы достаточно сильная. Достаточно любящая. Достаточно хорошая.\n\nНачни день с улыбки и скажи себе: «Сегодня я буду бережной к себе и к своим детям»."
        },
        "teen": {
            "title": "👧 Для мам подростков",
            "text": "Я знаю, как это бывает. Ты помнишь его маленьким — и вдруг он вырос. Он молчит, когда ты хочешь говорить. Он закрывается, когда ты хочешь быть рядом.\n\nЭто нормально. Это не про тебя. Это про него — про его взросление.\n\nТы не теряешь контакт. Ты учишься новому способу быть рядом. Ты справишься. Ты уже справляешься."
        },
        "adult": {
            "title": "👩 Для мам взрослых детей",
            "text": "Ты вырастила его. Ты дала ему всё, что могла. А теперь он живёт своей жизнью. И это правильно.\n\nНо иногда трудно отпустить. Трудно смотреть, как он ошибается. Трудно не вмешиваться.\n\nПомни: он взрослый человек. Он имеет право на свой путь. Твоя любовь не должна быть контролем. Ты уже дала ему всё, что было нужно. Теперь твоя задача — верить в него."
        },
        "baby": {
            "title": "🍼 Для мам в декрете",
            "text": "Ты — супергерой. Ты не спишь ночами, кормишь, укачиваешь, успокаиваешь. Ты даёшь всё, что у тебя есть.\n\nТы даёшь своему малышу самое главное — любовь и безопасность. Ты его мир. Ты его дом.\n\nТы можешь уставать. Ты можешь плакать. Ты можешь просить о помощи. Это нормально. Ты не одна."
        },
        "tired": {
            "title": "😴 Для тех, кто не выспался",
            "text": "Сегодня ты снова не выспалась. Ты встала рано, день только начался, а ты уже устала.\n\nПрямо сейчас остановись на 5 минут. Выпей воды. Посмотри в окно. Скажи себе: «Я справлюсь. Я делаю всё, что могу».\n\nТы не обязана быть энергичной 24/7. Ты человек, а не робот. Ты — замечательная мама. Даже если ты устала. Особенно когда ты устала."
        },
        "not_enough": {
            "title": "💔 Для тех, кто думает, что недостаточно хороша",
            "text": "Ты когда-нибудь ловила себя на мысли: «Я недостаточно хорошая мама»? Я знаю, что ловила. И я знаю, что это неправда.\n\nТы волнуешься — значит, тебе не всё равно. Ты переживаешь — значит, ты любишь. Ты хочешь быть лучше — значит, ты уже хорошая.\n\nНедостаточно хорошая мама не ищет поддержки. Она не волнуется о чувствах ребёнка. Она не хочет меняться. Ты — всё это делаешь."
        },
        "guilt": {
            "title": "🫂 Для тех, кто устал от вины",
            "text": "Чувство вины — это тяжёлый груз. «Я не так посмотрела», «Я не то сказала», «Я слишком много кричала».\n\nПозволь мне сказать тебе: ты не должна быть идеальной. Ты не должна быть спокойной 24/7. Ты не должна всё успевать.\n\nТы имеешь право на ошибки. Ты имеешь право на усталость. Ты имеешь право на свои чувства. Ты — хорошая мама. Ты заслуживаешь прощения — особенно от самой себя."
        },
        "alone": {
            "title": "🫂 Для тех, кто чувствует себя одинокой",
            "text": "Я знаю, что иногда ты чувствуешь себя одинокой. Даже когда рядом дети. Даже когда вокруг люди.\n\nТы думаешь: «Я одна не справлюсь», «Меня никто не понимает», «Я устала быть сильной».\n\nТы не одна. Ты часть большого сообщества мам, которые тоже устают, тоже срываются, тоже хотят тишины и покоя. Ты сильная. Ты справляешься. Ты достойна любви."
        },
        "self_care": {
            "title": "💝 Забота о себе",
            "text": "Ты так много даёшь своим детям. Но кто даёт тебе? Прямо сейчас напомни себе: ты заслуживаешь заботы. Ты заслуживаешь любви. Ты заслуживаешь отдыха.\n\nПозволь себе сделать что-то для себя сегодня. Просто 15 минут. Чтобы выдохнуть. Чтобы почувствовать себя собой.\n\nТы — не только мама. Ты — женщина. Ты — личность. Ты имеешь право на свою жизнь."
        }
    }
    return messages.get(category, messages["welcome"])

# ===== ОБЩИЕ РЕКОМЕНДАЦИИ ПО ВОЗРАСТУ =====
def get_age_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("👶 1-3 года", callback_data="age_1_3"),
        InlineKeyboardButton("🧒 4-6 лет", callback_data="age_4_6")
    )
    keyboard.add(
        InlineKeyboardButton("👦 7-10 лет", callback_data="age_7_10"),
        InlineKeyboardButton("👧 11-14 лет", callback_data="age_11_14")
    )
    keyboard.add(
        InlineKeyboardButton("🧑 15-18 лет", callback_data="age_15_18"),
        InlineKeyboardButton("👩 18+ лет", callback_data="age_18_plus")
    )
    keyboard.add(
        InlineKeyboardButton("📚 Общие рекомендации", callback_data="age_general")
    )
    return keyboard

def get_advice_by_age(age_group):
    advice = {
        "age_1_3": {
            "title": "👶 Дети 1-3 года",
            "tips": [
                "🧸 **Кризис 3 лет** — это нормально.",
                "🫂 **Обнимайте чаще**.",
                "🔄 **Переключайте внимание**.",
                "😌 **Сохраняйте спокойствие**.",
                "🗣️ **Говорите простыми фразами**.",
                "⏰ **Режим дня** — основа спокойствия.",
                "🎮 **Игра — главный способ обучения**."
            ],
            "restore_techniques": [
                "🤗 **Обними-меня**",
                "🔄 **Переключение**",
                "🧸 **Игрушка-мирилка**"
            ]
        },
        "age_4_6": {
            "title": "🧒 Дети 4-6 лет",
            "tips": [
                "🎨 **Развивайте фантазию**.",
                "🤝 **Учите договариваться**.",
                "📖 **Читайте вместе**.",
                "⏱️ **Давайте выбор**.",
                "👂 **Слушайте внимательно**.",
                "🏆 **Хвалите за старания**.",
                "😤 **Истерики — способ выразить эмоции**."
            ],
            "restore_techniques": [
                "🤗 **Обними-меня**",
                "🎨 **Рисуем обиду**",
                "🌊 **Дыхание вместе**"
            ]
        },
        "age_7_10": {
            "title": "👦 Дети 7-10 лет",
            "tips": [
                "📚 **Школа — новый стресс**.",
                "🤗 **Поддерживайте дружбу**.",
                "⏰ **Учите планировать время**.",
                "🗣️ **Обсуждайте чувства**.",
                "🎮 **Игры и спорт** помогают.",
                "📱 **Устанавливайте правила**.",
                "💪 **Хвалите за самостоятельность**."
            ],
            "restore_techniques": [
                "🤗 **Обними-меня**",
                "🎨 **Рисуем обиду**",
                "🧩 **Стоп-фраза**"
            ]
        },
        "age_11_14": {
            "title": "👧 Дети 11-14 лет",
            "tips": [
                "🔥 **Подростковый кризис** — норма.",
                "🤝 **Будьте другом**.",
                "🗣️ **Не критикуйте внешность**.",
                "📱 **Интернет-безопасность**.",
                "👂 **Слушайте без осуждения**.",
                "💪 **Давайте свободу**.",
                "💕 **Говорите о любви**."
            ],
            "restore_techniques": [
                "🛑 **Стоп-сигнал**",
                "✍️ **Я-сообщение**",
                "🍲 **Действие без слов**"
            ]
        },
        "age_15_18": {
            "title": "🧑 Дети 15-18 лет",
            "tips": [
                "🎯 **Поддерживайте выбор профессии**.",
                "🤝 **Отношения на равных**.",
                "🗣️ **Обсуждайте будущее**.",
                "💕 **Говорите о чувствах**.",
                "📱 **Доверяйте, но проверяйте**.",
                "💪 **Поддерживайте самостоятельность**.",
                "❤️ **Будьте рядом**."
            ],
            "restore_techniques": [
                "🛑 **Стоп-сигнал**",
                "✍️ **Я-сообщение**",
                "🍲 **Действие без слов**"
            ]
        },
        "age_18_plus": {
            "title": "👩 Взрослые дети (18+ лет)",
            "tips": [
                "🤝 **Отношения на равных**.",
                "🗣️ **Советуйте, но не навязывайте**.",
                "🫂 **Будьте опорой**.",
                "💕 **Принимайте выборы**.",
                "📱 **Не вмешивайтесь**.",
                "💰 **Финансовая поддержка** должна уменьшаться.",
                "👂 **Слушайте без осуждения**.",
                "💝 **Продолжайте проявлять любовь**.",
                "🔄 **Пересмотрите роль «родитель»**.",
                "🙏 **Прощайте ошибки**.",
                "💬 **Учитесь диалогу**.",
                "🌟 **Радуйтесь успехам**."
            ],
            "restore_techniques": [
                "🛑 **Стоп-сигнал**",
                "✍️ **Я-сообщение**",
                "🍲 **Действие без слов**"
            ]
        },
        "age_general": {
            "title": "📚 Общие рекомендации для всех возрастов",
            "tips": [
                "❤️ **Безусловная любовь**.",
                "👂 **Слушайте активно**.",
                "😌 **Контролируйте свои эмоции**.",
                "🔄 **Устанавливайте чёткие границы**.",
                "🤗 **Обнимайте каждый день**.",
                "📖 **Читайте и обсуждайте**.",
                "🙏 **Будьте примером**."
            ],
            "restore_techniques": [
                "🛑 **Стоп-сигнал**",
                "✍️ **Я-сообщение**",
                "🍲 **Действие без слов**"
            ]
        }
    }
    return advice.get(age_group, advice["age_general"])

# ===== ЮРИДИЧЕСКИЙ БЛОК =====
POLICY_URL = "https://docs.google.com/document/d/ВАША_ССЫЛКА_ПОЛИТИКА/edit"
OFFER_URL = "https://docs.google.com/document/d/ВАША_ССЫЛКА_ОФЕРТА/edit"
CONSENT_URL = "https://docs.google.com/document/d/ВАША_ССЫЛКА_СОГЛАСИЕ/edit"

# ===== КОМАНДА /START =====
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

    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date) VALUES (?, ?, ?, ?)",
                   (user_id, username, first_name, datetime.now().strftime('%Y-%m-%d')))
    conn.commit()

    code = generate_referral_code(user_id)
    bot_username = (await bot.get_me()).username

    if has_agreed_to_terms(user_id):
        await show_main_menu(message)
        return

    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("🛡️ Здесь безопасно", callback_data="show_terms")
    )

    welcome_text = (
        f"👋 Привет, {first_name}!\n\n"
        "Я — **PauseMomBot** 🤖\n\n"
        "Я — твой помощник в сложные моменты воспитания.\n\n"
        "Я помогаю мамам:\n"
        "🌸 Сохранять спокойствие, когда закипаешь\n"
        "🌸 Заботиться о себе и своих чувствах\n"
        "🌸 Находить нужные слова для себя и детей\n\n"
        "Здесь безопасно и конфиденциально.\n"
        "Нажми «Здесь безопасно», чтобы продолжить ✨"
    )
    await message.answer(welcome_text, reply_markup=keyboard, parse_mode="Markdown")

# ===== ЮРИДИЧЕСКИЙ БЛОК =====
@dp.callback_query_handler(lambda c: c.data == "show_terms")
async def show_terms(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("✅ Принять и продолжить", callback_data="accept_terms")
    )

    await callback_query.message.edit_text(
        "📋 **Пару формальностей и начнём.**\n\n"
        "Мы ценим доверие и уважаем закон. Поэтому, всё официально.\n\n"
        "📄 **Ознакомьтесь с документами:**\n\n"
        f"• <a href='{POLICY_URL}'>Политика обработки персональных данных</a>\n"
        f"• <a href='{OFFER_URL}'>Публичная оферта</a>\n"
        f"• <a href='{CONSENT_URL}'>Соглашение на обработку персональных данных</a>\n\n"
        "Нажимая «Принять и продолжить», Вы соглашаетесь с указанными документами.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "accept_terms")
async def accept_terms(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    set_agreed_to_terms(user_id)

    await callback_query.message.edit_text(
        "✅ **Спасибо!**\n\n"
        "Теперь вы можете пользоваться ботом.\n\n"
        "🌸 Начните с главного меню:"
    )
    
    await callback_query.message.answer(
        "🌸 **Главное меню:**\n\n"
        "Выберите, что вам нужно:",
        reply_markup=main_keyboard(user_id)
    )
    await callback_query.answer()

# ===== ГЛАВНОЕ МЕНЮ =====
async def show_main_menu(message: types.Message):
    user_id = message.from_user.id
    await message.answer(
        "🌸 **Главное меню:**\n\n"
        "Выберите, что вам нужно:",
        reply_markup=main_keyboard(user_id)
    )

# ===== SOS-ПАУЗА =====
@dp.message_handler(lambda message: message.text == "🆘 SOS-Пауза")
async def sos(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("UPDATE users SET total_sos = total_sos + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    await message.answer(
        "⏳ **Стоп! Ты сделала главное — остановилась.**\n\n"
        "Выбери упражнение осознанности, которое поможет успокоиться:",
        reply_markup=sos_keyboard()
    )

# ===== ВСЕ 10 УПРАЖНЕНИЙ SOS =====
@dp.message_handler(lambda message: message.text == "🌬️ Дыхание 4-7-8")
async def breathe(message: types.Message):
    await message.answer(
        "🌬️ **Дыхание 4-7-8**\n\n"
        "1️⃣ Вдохни носом — **4** секунды\n"
        "2️⃣ Задержи дыхание — **7** секунд\n"
        "3️⃣ Выдохни ртом — **8** секунд\n\n"
        "🔄 Повтори **5 раз**.\n\n"
        "✨ Это снижает уровень кортизола.",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "🧘 Осознанное дыхание")
async def mindful_breath(message: types.Message):
    await message.answer(
        "🧘 **Осознанное дыхание**\n\n"
        "1️⃣ Сядь удобно, закрой глаза.\n"
        "2️⃣ Сделай 3 глубоких вдоха и выдоха.\n"
        "3️⃣ Теперь просто **наблюдай** за своим дыханием.\n"
        "4️⃣ Продолжай 1 минуту.\n\n"
        "✨ Это возвращает тебя в «здесь и сейчас».",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "👀 5-4-3-2-1")
async def grounding(message: types.Message):
    await message.answer(
        "👀 **Упражнение «5-4-3-2-1»**\n\n"
        "Оглянись вокруг и найди:\n"
        "5️⃣ **вещей**, которые ты видишь\n"
        "4️⃣ **звука**, которые ты слышишь\n"
        "3️⃣ **ощущения** на коже\n"
        "2️⃣ **запаха**, которые ты чувствуешь\n"
        "1️⃣ **вкуса** во рту\n\n"
        "✨ Это возвращает мозг в реальность.",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "🤗 Обнять себя")
async def self_hug(message: types.Message):
    await message.answer(
        "🤗 **Техника «Бабочка»**\n\n"
        "1️⃣ Скрести руки на груди\n"
        "2️⃣ Похлопай себя по плечам попеременно\n"
        "3️⃣ Продолжай **1 минуту**\n\n"
        "✨ Это успокаивает нервную систему.",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "💧 Умыться водой")
async def wash(message: types.Message):
    await message.answer(
        "🚰 **Умывание холодной водой**\n\n"
        "1️⃣ Встань и подойди к раковине\n"
        "2️⃣ Умой лицо холодной водой **3 раза**\n"
        "3️⃣ Почувствуй, как вода смывает гнев\n\n"
        "✨ Это запускает «нырятельный рефлекс».",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "🦶 Стойка на ногах")
async def standing(message: types.Message):
    await message.answer(
        "🦶 **Стойка на ногах**\n\n"
        "1️⃣ Встань ровно, ноги на ширине плеч\n"
        "2️⃣ Почувствуй, как ноги касаются пола\n"
        "3️⃣ Начинай медленно переносить вес:\n"
        "   • На пятки — **3 секунды**\n"
        "   • На носки — **3 секунды**\n"
        "   • На внешний край стоп — **3 секунды**\n"
        "4️⃣ Повтори **5 раз**\n\n"
        "✨ Это возвращает тебя в тело.",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "🧠 Сканирование тела")
async def body_scan(message: types.Message):
    await message.answer(
        "🧠 **Сканирование тела**\n\n"
        "Закрой глаза и почувствуй:\n"
        "👣 **Стопы**\n"
        "🦵 **Ноги**\n"
        "🤲 **Руки**\n"
        "🫀 **Грудь**\n"
        "👤 **Лицо**\n\n"
        "✨ Это снимает напряжение.",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "☀️ Луч света")
async def light_beam(message: types.Message):
    await message.answer(
        "☀️ **Луч света**\n\n"
        "1️⃣ Закрой глаза.\n"
        "2️⃣ Представь **тёплый золотистый свет** над головой.\n"
        "3️⃣ Этот свет медленно опускается:\n"
        "   • на лицо — смывает напряжение\n"
        "   • на плечи — снимает тяжесть\n"
        "   • на грудь — наполняет спокойствием\n"
        "4️⃣ Продолжай **1 минуту**\n\n"
        "✨ Свет растворяет гнев.",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "🌊 Волна дыхания")
async def wave_breath(message: types.Message):
    await message.answer(
        "🌊 **Волна дыхания**\n\n"
        "Представь, что твоё дыхание — это волны океана:\n\n"
        "🌊 **Вдох** — волна накатывает\n"
        "🌊 **Выдох** — волна уходит\n\n"
        "🔄 Повтори **5 раз**\n\n"
        "✨ Волны смывают гнев и тревогу.",
        reply_markup=sos_keyboard()
    )

@dp.message_handler(lambda message: message.text == "💭 Наблюдатель")
async def observer(message: types.Message):
    await message.answer(
        "💭 **Наблюдатель**\n\n"
        "1️⃣ Закрой глаза.\n"
        "2️⃣ Представь, что ты смотришь на себя со стороны.\n"
        "3️⃣ Ты видишь свою злость как **облако**.\n"
        "4️⃣ Наблюдай за этим облаком:\n"
        "   • Оно пришло\n"
        "   • Оно здесь\n"
        "   • Оно уходит\n\n"
        "5️⃣ Ты не злость — ты просто **наблюдаешь** её.\n\n"
        "✨ Ты отделяешь себя от эмоций.",
        reply_markup=sos_keyboard()
    )

# ===== КНОПКА "ПРИГЛАСИТЬ ПОДРУГУ" =====
@dp.message_handler(lambda message: message.text == "👥 Пригласить подругу")
async def referral(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result:
        code = result[0]
    else:
        code = generate_referral_code(user_id)
    bot_username = (await bot.get_me()).username

    await message.answer(
        f"👥 **Твоя реферальная ссылка:**\n"
        f"`https://t.me/{bot_username}?start={code}`\n\n"
        "🌸 Поделись с подругой — поддержка важна для каждой мамы.",
        reply_markup=main_keyboard(user_id)
    )

# ===== КНОПКА "ВОССТАНОВЛЕНИЕ КОНТАКТА" =====
@dp.message_handler(lambda message: message.text == "🤝 Восстановить контакт")
async def restore_contact(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in ADMINS or is_premium(user_id):
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🔄 Начать восстановление", callback_data="start_restore"),
            InlineKeyboardButton("🌸 Ресурсные техники для мамы", callback_data="resource_techniques"),
            InlineKeyboardButton("🧸 Техники для малышей", callback_data="kids_restore"),
            InlineKeyboardButton("🧑 Техники для подростков и детей постарше", callback_data="teen_restore"),
            InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")
        )
        
        await message.answer(
            "🤝 **Восстановление контакта**\n\n"
            "Выбери категорию:\n\n"
            "🔄 **Начать восстановление** — пошаговый план (3 шага)\n"
            "🌸 **Ресурсные техники для мамы** — 5 техник для восстановления ресурса\n"
            "🧸 **Техники для малышей** — для детей 1-12 лет\n"
            "🧑 **Техники для подростков и детей постарше** — 6 техник для взрослых детей",
            reply_markup=keyboard
        )
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("💎 Оформить Premium", callback_data="back_to_premium")
    )
    await message.answer(
        "🔒 **Этот раздел доступен только по подписке Premium.**\n\n"
        "Оформите Premium (999 ₽/мес), чтобы получить доступ к эксклюзивному модулю «Восстановление контакта».",
        reply_markup=keyboard
    )

# ===== НАЧАЛО ВОССТАНОВЛЕНИЯ =====
@dp.callback_query_handler(lambda c: c.data == "start_restore")
async def start_restore_callback(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.delete()
    
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    keyboard.add(KeyboardButton("✅ Прочитала, дай следующий шаг"))
    keyboard.add(KeyboardButton("🔙 Главное меню"))
    
    await callback_query.message.answer(
        "🛑 **Шаг 1 из 3: Стоп-сигнал**\n\n"
        "Ты уже осознала, что наговорила лишнего.\n\n"
        "🔹 **Что делать:**\n"
        "Ребёнок поставил границу. Самое важное сейчас — **не преследовать его**.\n\n"
        "🚫 Не стучись в дверь.\n"
        "🚫 Не кричи вдогонку.\n"
        "🚫 Не требуй ответа.\n\n"
        "✅ Просто отойди и дай ему время на остывание.\n"
        "Скажи себе: «Я уважаю его право на паузу».\n\n"
        "Когда будешь готова — нажми кнопку ниже.",
        reply_markup=keyboard
    )
    await state.set_state("restore_step1")
    await callback_query.answer()

@dp.message_handler(state="restore_step1", text="✅ Прочитала, дай следующий шаг")
async def restore_step2(message: types.Message, state: FSMContext):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    keyboard.add(KeyboardButton("✅ Прочитала, дай следующий шаг"))
    keyboard.add(KeyboardButton("🔙 Главное меню"))

    await message.answer(
        "✍️ **Шаг 2 из 3: Я-сообщение**\n\n"
        "Теперь, когда ты успокоилась, попробуй сказать ему **тихо** и **без оправданий**.\n\n"
        "📝 **Выбери фразу:**\n\n"
        "1️⃣ «Я знаю, что я наговорила лишнего. Мне очень жаль.»\n\n"
        "2️⃣ «Я не справилась со своими эмоциями. Это неправильно.»\n\n"
        "3️⃣ «Ты очень дорог мне, даже когда я ошибаюсь.»\n\n"
        "💡 Скажи это один раз — и отойди.\n\n"
        "Когда будешь готова — нажми кнопку ниже.",
        reply_markup=keyboard
    )
    await state.set_state("restore_step2")

@dp.message_handler(state="restore_step2", text="✅ Прочитала, дай следующий шаг")
async def restore_step3(message: types.Message, state: FSMContext):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    keyboard.add(KeyboardButton("✅ Прочитала, дай следующий шаг"))
    keyboard.add(KeyboardButton("🔙 Главное меню"))

    await message.answer(
        "🍲 **Шаг 3 из 3: Действие без слов**\n\n"
        "Он не хочет говорить? Хорошо. Не заставляй.\n\n"
        "Покажи свою любовь через действие:\n\n"
        "• 🍳 Приготовь его любимую еду\n"
        "• ✉️ Положи записку под дверь\n"
        "• 😊 Просто улыбнись\n\n"
        "Твоя задача — не давить, а показать, что ты рядом.\n\n"
        "Когда будешь готова — нажми кнопку ниже.",
        reply_markup=keyboard
    )
    await state.set_state("restore_step3")

@dp.message_handler(state="restore_step3", text="✅ Прочитала, дай следующий шаг")
async def restore_step4(message: types.Message, state: FSMContext):
    await message.answer(
        "💎 **Ты сделала большой шаг!**\n\n"
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
    await state.finish()

# ===== РЕСУРСНЫЕ ТЕХНИКИ ДЛЯ МАМЫ =====
@dp.callback_query_handler(lambda c: c.data == "resource_techniques")
async def resource_techniques(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("🌬️ Квадратное дыхание", callback_data="res_breath"),
        InlineKeyboardButton("✍️ Список благодарности", callback_data="res_gratitude"),
        InlineKeyboardButton("🫂 Разрешение на отдых", callback_data="res_rest"),
        InlineKeyboardButton("🌅 Луч света", callback_data="res_light"),
        InlineKeyboardButton("🎵 Музыкальная пауза", callback_data="res_music"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_restore")
    )
    
    await callback_query.message.edit_text(
        "🌸 **Ресурсные техники для мамы**\n\n"
        "Эти техники помогут тебе восстановить силы, успокоиться и наполниться энергией.\n\n"
        "Выбери технику:",
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("res_"))
async def show_resource_technique(callback_query: types.CallbackQuery):
    technique = callback_query.data.replace("res_", "")
    
    techniques_texts = {
        "breath": (
            "🌬️ **Техника «Квадратное дыхание»**\n\n"
            "Это дыхание помогает вернуть контроль над эмоциями за 1 минуту.\n\n"
            "1️⃣ Вдох — 4 секунды\n"
            "2️⃣ Задержка — 4 секунды\n"
            "3️⃣ Выдох — 4 секунды\n"
            "4️⃣ Задержка — 4 секунды\n\n"
            "🔄 Повтори 5 раз.\n\n"
            "💡 Представь, что ты рисуешь дыханием квадрат. Это переключает мозг с эмоций на логику."
        ),
        "gratitude": (
            "✍️ **Техника «Список благодарности»**\n\n"
            "Ты так много даёшь другим. А теперь дай себе пару минут.\n\n"
            "1️⃣ Возьми лист бумаги или открой заметки в телефоне.\n"
            "2️⃣ Напиши 3 вещи, за которые ты благодарна себе сегодня.\n"
            "   • «Я благодарна себе за то, что...»\n"
            "   • «Я благодарна себе за то, что...»\n"
            "   • «Я благодарна себе за то, что...»\n"
            "3️⃣ Прочитай это вслух себе.\n\n"
            "📌 **Примеры:**\n"
            "• «Я благодарна себе за то, что нашла время выпить чай»\n"
            "• «Я благодарна себе за то, что не сорвалась на ребёнка»\n"
            "• «Я благодарна себе за то, что я — хорошая мама»\n\n"
            "💡 Благодарность к себе — это топливо для души."
        ),
        "rest": (
            "🫂 **Техника «Разрешение на отдых»**\n\n"
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
            "🌅 **Техника «Луч света»**\n\n"
            "Закрой глаза и представь, что сверху на тебя льётся тёплый золотистый свет.\n\n"
            "1️⃣ Свет мягко касается твоей головы — ты чувствуешь тепло.\n"
            "2️⃣ Он опускается на плечи — снимает тяжесть и напряжение.\n"
            "3️⃣ Он доходит до груди — наполняет тебя спокойствием.\n"
            "4️⃣ Он разливается по всему телу — ты чувствуешь лёгкость и силу.\n\n"
            "💡 Побудь в этом свете 2-3 минуты. Когда откроешь глаза — ты почувствуешь себя обновлённой."
        ),
        "music": (
            "🎵 **Техника «Музыкальная пауза»**\n\n"
            "Музыка лечит. Музыка успокаивает. Музыка возвращает к себе.\n\n"
            "1️⃣ Включи свою любимую песню.\n"
            "2️⃣ Закрой глаза и слушай только музыку.\n"
            "3️⃣ Не думай ни о чём. Просто чувствуй.\n"
            "4️⃣ Если мысли приходят — отпускай их и возвращайся к музыке.\n\n"
            "💡 3 минуты музыки могут дать больше отдыха, чем час в социальных сетях."
        )
    }
    
    text = techniques_texts.get(technique, "Техника не найдена.")
    
    await callback_query.message.edit_text(
        f"{text}\n\n"
        f"🔙 Нажми «Назад», чтобы вернуться к списку техник.",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Назад", callback_data="resource_techniques")
        )
    )
    await callback_query.answer()

# ===== ТЕХНИКИ ДЛЯ МАЛЫШЕЙ (В ВОССТАНОВЛЕНИИ КОНТАКТА) =====
@dp.callback_query_handler(lambda c: c.data == "kids_restore")
async def kids_restore_techniques(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("👶 1-3 года", callback_data="kids_restore_1_3"),
        InlineKeyboardButton("🧒 4-6 лет", callback_data="kids_restore_4_6"),
        InlineKeyboardButton("👦 7-9 лет", callback_data="kids_restore_7_9"),
        InlineKeyboardButton("👦 10-12 лет", callback_data="kids_restore_10_12"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_restore")
    )
    
    await callback_query.message.edit_text(
        "🧸 **Техники для малышей**\n\n"
        "Выбери возраст ребёнка:",
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("kids_restore_"))
async def show_kids_restore_technique(callback_query: types.CallbackQuery):
    age_group = callback_query.data.replace("kids_restore_", "")
    techniques_data = get_kids_techniques(age_group)
    
    tips_text = "\n\n".join(techniques_data["tips"])
    
    await callback_query.message.edit_text(
        f"{techniques_data['title']}\n\n"
        f"{tips_text}\n\n"
        f"🔙 Нажми «Назад», чтобы выбрать другой возраст.",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Назад", callback_data="kids_restore")
        )
    )
    await callback_query.answer()

# ===== ТЕХНИКИ ДЛЯ ПОДРОСТКОВ И ДЕТЕЙ ПОСТАРШЕ =====
@dp.callback_query_handler(lambda c: c.data == "teen_restore")
async def teen_restore_techniques(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("👁️ Наблюдатель", callback_data="teen_observer"),
        InlineKeyboardButton("🎯 Стоп-кадр", callback_data="teen_stop_frame"),
        InlineKeyboardButton("🗣️ Честность без оправданий", callback_data="teen_honest"),
        InlineKeyboardButton("🎯 Совет по запросу", callback_data="teen_advice"),
        InlineKeyboardButton("📱 Музыка-мостик", callback_data="teen_music"),
        InlineKeyboardButton("🧩 Спроси, а не учи", callback_data="teen_ask"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_restore")
    )
    
    await callback_query.message.edit_text(
        "🧑 **Техники для подростков и детей постарше**\n\n"
        "Эти техники помогут восстановить контакт с подростками и взрослыми детьми.\n\n"
        "Выбери технику:",
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("teen_"))
async def show_teen_technique(callback_query: types.CallbackQuery):
    technique = callback_query.data.replace("teen_", "")
    
    techniques_texts = {
        "observer": (
            "👁️ **Техника «Наблюдатель»**\n\n"
            "1️⃣ Закрой глаза и представь, что ты смотришь на себя со стороны.\n"
            "2️⃣ Ты видишь маму и ребёнка после ссоры.\n"
            "3️⃣ Что ты видишь? Какие эмоции? Что происходит?\n"
            "4️⃣ Теперь представь, что ты — мудрый друг, который даёт совет этой маме.\n"
            "5️⃣ Что бы ты ей сказала?\n\n"
            "💡 Взгляд со стороны помогает увидеть решение."
        ),
        "stop_frame": (
            "🎯 **Техника «Стоп-кадр»**\n\n"
            "Ты уже осознала, что сорвалась. Теперь давай разберём ситуацию.\n\n"
            "1️⃣ Закрой глаза и прокрути ситуацию как фильм.\n"
            "2️⃣ Найди момент, когда ты ещё могла сказать мягко.\n"
            "3️⃣ Назови этот момент — твой «стоп-кадр».\n"
            "4️⃣ Придумай фразу, которую нужно было сказать вместо крика.\n"
            "5️⃣ Запомни эту фразу. Она — твой новый инструмент.\n\n"
            "💡 Это упражнение превращает ошибку в опыт."
        ),
        "honest": (
            "🗣️ **Техника «Честность без оправданий»**\n\n"
            "Подростки и взрослые дети ненавидят оправдания.\n"
            "«Я устала», «Я не хотела», «Ты сам меня довёл» — это только злит.\n\n"
            "🔹 **Что сказать:**\n"
            "Вместо оправданий скажи коротко и честно:\n\n"
            "«Я была неправа. Прости.»\n\n"
            "Или:\n"
            "«Я сорвалась на тебе. Это было неправильно.»\n\n"
            "💡 Без оправданий. Без объяснений. Просто признание ошибки.\n"
            "Это вызывает уважение, а не раздражение."
        ),
        "advice": (
            "🎯 **Техника «Совет только по запросу»**\n\n"
            "Взрослые дети не любят непрошенные советы.\n\n"
            "🔹 **Что делать:**\n"
            "❌ Не говори: «Я бы на твоём месте...»\n"
            "✅ Спроси: «Хочешь, я поделюсь своим мнением?»\n"
            "✅ Если он говорит «нет» — прими это.\n\n"
            "💡 Непрошенный совет — это вторжение.\n"
            "Запрошенный совет — это помощь."
        ),
        "music": (
            "📱 **Техника «Музыка-мостик»**\n\n"
            "Музыка объединяет лучше слов.\n\n"
            "🔹 **Что делать:**\n"
            "1️⃣ Спроси: «Что ты сейчас слушаешь? Можешь скинуть плейлист?»\n"
            "2️⃣ Если он скидывает — это большой шаг. Значит, он готов делиться своим миром.\n"
            "3️⃣ Послушай, что он скинул.\n"
            "4️⃣ Скажи: «Классная песня. Спасибо, что поделился».\n\n"
            "💡 Интерес к его музыке — интерес к его миру."
        ),
        "ask": (
            "🧩 **Техника «Спроси, а не учи»**\n\n"
            "Подростки ненавидят, когда их учат жизни.\n"
            "Им нужен диалог, а не лекция.\n\n"
            "🔹 **Что спросить:**\n"
            "«Что ты думаешь об этом?»\n"
            "«Как ты видишь эту ситуацию?»\n"
            "«Что для тебя сейчас важно?»\n"
            "«Что я могу сделать, чтобы тебе было легче?»\n\n"
            "💡 Когда ты спрашиваешь — ты показываешь уважение.\n"
            "Когда ты учишь — ты показываешь превосходство."
        )
    }
    
    text = techniques_texts.get(technique, "Техника не найдена.")
    
    await callback_query.message.edit_text(
        f"{text}\n\n"
        f"🔙 Нажми «Назад», чтобы вернуться к списку техник.",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Назад", callback_data="teen_restore")
        )
    )
    await callback_query.answer()

# ===== КНОПКА "НУЖНЫЕ СЛОВА ДЛЯ МАМЫ" =====
@dp.message_handler(lambda message: message.text == "💝 Нужные слова для мамы")
async def support_menu(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("👋 Первое знакомство", callback_data="support_welcome"),
        InlineKeyboardButton("🌸 После SOS-паузы", callback_data="support_after_sos"),
        InlineKeyboardButton("🫂 После срыва", callback_data="support_after_cry"),
        InlineKeyboardButton("🌙 Вечерняя поддержка", callback_data="support_evening"),
        InlineKeyboardButton("🌅 Утренняя поддержка", callback_data="support_morning"),
        InlineKeyboardButton("👧 Для мам подростков", callback_data="support_teen"),
        InlineKeyboardButton("👩 Для мам взрослых детей", callback_data="support_adult"),
        InlineKeyboardButton("🍼 Для мам в декрете", callback_data="support_baby"),
        InlineKeyboardButton("😴 Кто не выспался", callback_data="support_tired"),
        InlineKeyboardButton("💔 Кто чувствует себя недостаточно хорошей", callback_data="support_not_enough"),
        InlineKeyboardButton("🫂 Кто устал от вины", callback_data="support_guilt"),
        InlineKeyboardButton("🫂 Кто чувствует себя одинокой", callback_data="support_alone"),
        InlineKeyboardButton("💝 Забота о себе", callback_data="support_self_care")
    )
    
    await message.answer(
        "💝 **Нужные слова для мамы**\n\n"
        "Выбери категорию, которая откликается тебе сейчас:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('support_'))
async def show_support_message(callback_query: types.CallbackQuery):
    category = callback_query.data.replace('support_', '')
    message_data = get_support_message(category)
    
    await callback_query.message.edit_text(
        f"💝 **{message_data['title']}**\n\n{message_data['text']}\n\n"
        "🔙 Нажми «Назад», чтобы выбрать другую категорию.",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Назад к категориям", callback_data="back_to_support")
        )
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "back_to_support")
async def back_to_support(callback_query: types.CallbackQuery):
    await support_menu(callback_query.message)
    await callback_query.answer()

# ===== КНОПКА "ТЕХНИКИ ДЛЯ МАЛЫШЕЙ" (PREMIUM) =====
@dp.message_handler(lambda message: message.text == "🧸 Техники для малышей")
async def kids_techniques_menu(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in ADMINS or is_premium(user_id):
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("👶 1-3 года", callback_data="kids_1_3"),
            InlineKeyboardButton("🧒 4-6 лет", callback_data="kids_4_6"),
            InlineKeyboardButton("👦 7-9 лет", callback_data="kids_7_9"),
            InlineKeyboardButton("👦 10-12 лет", callback_data="kids_10_12"),
            InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")
        )
        
        await message.answer(
            "🧸 **Техники для малышей**\n\n"
            "Выбери возраст:",
            reply_markup=keyboard
        )
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("💎 Оформить Premium", callback_data="back_to_premium")
    )
    await message.answer(
        "🔒 **Техники для малышей доступны только Premium-пользователям.**\n\n"
        "Оформите Premium (999 ₽/мес) и получите доступ к техникам для детей от 1 до 12 лет.",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('kids_'))
async def process_kids_technique(callback_query: types.CallbackQuery):
    if callback_query.data == "back_to_main":
        await callback_query.message.delete()
        await callback_query.message.answer(
            "Главное меню:",
            reply_markup=main_keyboard(callback_query.from_user.id)
        )
        await callback_query.answer()
        return
    
    age_group = callback_query.data.replace("kids_", "")
    techniques_data = get_kids_techniques(age_group)
    
    tips_text = "\n\n".join(techniques_data["tips"])
    
    await callback_query.message.edit_text(
        f"{techniques_data['title']}\n\n"
        f"{tips_text}\n\n"
        f"🔙 Нажми «Назад», чтобы выбрать другой возраст.",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Назад к возрастам", callback_data="back_to_kids")
        )
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "back_to_kids")
async def back_to_kids(callback_query: types.CallbackQuery):
    await kids_techniques_menu(callback_query.message)
    await callback_query.answer()

# ===== КНОПКА "АФФИРМАЦИЯ ДНЯ" (ДОСТУП АДМИНАМ) =====
@dp.message_handler(lambda message: message.text == "🌅 Аффирмация дня")
async def daily_affirmation(message: types.Message):
    user_id = message.from_user.id
    
    # Проверяем Premium (админы всегда имеют доступ)
    if user_id not in ADMINS and not is_premium(user_id):
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(
            InlineKeyboardButton("💎 Оформить Premium", callback_data="back_to_premium")
        )
        await message.answer(
            "🔒 **Аффирмации доступны только Premium-пользователям.**\n\n"
            "Оформите Premium (999 ₽/мес) и получайте 100 поддерживающих фраз.",
            reply_markup=keyboard
        )
        return
    
    cursor.execute("SELECT last_affirmation FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    today = datetime.now().strftime('%Y-%m-%d')
    
    if result and result[0] == today:
        affirmation = get_daily_affirmation()
        await message.answer(
            f"🌅 **Твоя аффирмация на сегодня:**\n\n"
            f"«{affirmation}»\n\n"
            f"💫 Ты уже получила её сегодня. Повтори несколько раз с любовью к себе.",
            reply_markup=main_keyboard(user_id)
        )
    else:
        cursor.execute("UPDATE users SET last_affirmation = ? WHERE user_id = ?", (today, user_id))
        conn.commit()
        
        affirmation = get_daily_affirmation()
        day_of_year = datetime.now().timetuple().tm_yday
        
        await message.answer(
            f"🌅 **Аффирмация дня #{day_of_year % 100 + 1}**\n\n"
            f"«{affirmation}»\n\n"
            f"💫 Повтори эту аффирмацию 3 раза сегодня.\n"
            f"✨ Она наполнит тебя силой и уверенностью.\n\n"
            f"💝 Ты — самая лучшая мама для своего ребёнка!",
            reply_markup=main_keyboard(user_id)
        )

# ===== КНОПКА "ОБЩИЕ РЕКОМЕНДАЦИИ ПО ВОЗРАСТУ" =====
@dp.message_handler(lambda message: message.text == "📚 Общие рекомендации по возрасту")
async def age_recommendations(message: types.Message):
    await message.answer(
        "📚 **Общие рекомендации по возрасту**\n\n"
        "Выбери возраст своего ребёнка:",
        reply_markup=get_age_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith('age_'))
async def process_age_choice(callback_query: types.CallbackQuery):
    age_group = callback_query.data
    advice_data = get_advice_by_age(age_group)
    
    tips_text = "\n\n".join(advice_data["tips"])
    restore_text = "\n\n".join([f"• {t}" for t in advice_data.get("restore_techniques", [])])
    
    message_text = (
        f"📚 **{advice_data['title']}**\n\n"
        f"📋 **Рекомендации:**\n\n"
        f"{tips_text}\n\n"
    )
    
    if restore_text:
        message_text += (
            f"🔄 **Техники восстановления контакта:**\n\n"
            f"{restore_text}\n\n"
        )
    
    message_text += (
        f"💡 Помни: каждый ребёнок уникален. Эти рекомендации — ориентир.\n\n"
        f"🔙 Нажми «Назад», чтобы выбрать другой возраст."
    )
    
    await callback_query.message.edit_text(
        message_text,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Назад к возрастам", callback_data="back_to_ages")
        )
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "back_to_ages")
async def back_to_ages(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "📚 **Общие рекомендации по возрасту**\n\n"
        "Выбери возраст своего ребёнка:",
        reply_markup=get_age_keyboard()
    )
    await callback_query.answer()

# ===== НАЗАД К ВОССТАНОВЛЕНИЮ КОНТАКТА =====
@dp.callback_query_handler(lambda c: c.data == "back_to_restore")
async def back_to_restore(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    
    class FakeMessage:
        def __init__(self, user_id):
            self.from_user = types.User(id=user_id, is_bot=False, first_name="User")
        async def answer(self, text, reply_markup=None, parse_mode=None):
            await callback_query.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    
    fake_msg = FakeMessage(callback_query.from_user.id)
    await restore_contact(fake_msg)
    await callback_query.answer()

# ===== НАЗАД В ГЛАВНОЕ МЕНЮ =====
@dp.callback_query_handler(lambda c: c.data == "back_to_main")
async def back_to_main(callback_query: types.CallbackQuery):
    await callback_query.message.delete()
    await callback_query.message.answer(
        "🌸 **Главное меню:**\n\n"
        "Выберите, что вам нужно:",
        reply_markup=main_keyboard(callback_query.from_user.id)
    )
    await callback_query.answer()

# ===== PREMIUM =====
@dp.message_handler(lambda message: message.text == "💎 Premium")
async def premium_info(message: types.Message):
    user_id = message.from_user.id
    
    # Админ — доступ без оплаты
    if user_id in ADMINS:
        await message.answer(
            "👑 **Вы — создатель бота!**\n\n"
            "Вам доступны все функции Premium без оплаты.\n\n"
            "✨ **Доступно:**\n"
            "✅ Техники для малышей (1-12 лет)\n"
            "✅ Модуль «Восстановление контакта»\n"
            "✅ 100 аффирмаций поддержки",
            reply_markup=main_keyboard(user_id)
        )
        if not is_premium(user_id):
            add_premium(user_id, 36500)
        return

    # Проверка Premium
    if is_premium(user_id):
        cursor.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if result:
            end_date = result[0]
            await message.answer(
                f"✅ **У тебя уже есть Premium!**\n\n"
                f"📅 Действует до: {end_date}\n\n"
                "Пользуйся всеми функциями без ограничений.",
                reply_markup=main_keyboard(user_id)
            )
        return

    # Обычное предложение Premium
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("✅ Я оплатил(а)", callback_data="confirm_payment"),
        InlineKeyboardButton("❓ Как оплатить?", callback_data="payment_help")
    )
    
    await message.answer(
        "💎 **Premium — 999 ₽/мес**\n\n"
        "✨ **Что ты получаешь:**\n"
        "✅ Техники для малышей (1-12 лет)\n"
        "✅ Модуль «Восстановление контакта»\n"
        "✅ 100 аффирмаций поддержки\n\n"
        "📲 **Как оплатить:**\n"
        "1️⃣ Напишите @PauseMomSupport_bot\n"
        "2️⃣ Сообщите, что хотите оплатить Premium\n"
        "3️⃣ После оплаты нажмите «✅ Я оплатил(а)»\n\n"
        "🔹 Premium активируется на 30 дней",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "payment_help")
async def payment_help(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("🔙 Назад к Premium", callback_data="back_to_premium")
    )
    
    await callback_query.message.edit_text(
        "❓ **Как оплатить Premium:**\n\n"
        "1️⃣ Напишите @PauseMomSupport_bot\n"
        "2️⃣ Сообщите: «Хочу оплатить Premium»\n"
        "3️⃣ Вам придёт ссылка на оплату (999 ₽)\n"
        "4️⃣ Оплатите удобным способом\n"
        "5️⃣ Вернитесь в бот и нажмите «✅ Я оплатил(а)»\n"
        "6️⃣ Premium активируется на 30 дней\n\n"
        "💡 Если вы уже оплатили, просто нажмите «Я оплатил(а)»",
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "back_to_premium")
async def back_to_premium(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    class FakeMessage:
        def __init__(self, user_id):
            self.from_user = types.User(id=user_id, is_bot=False, first_name="User")
        async def answer(self, text, reply_markup=None, parse_mode=None):
            await callback_query.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    
    fake_msg = FakeMessage(user_id)
    await callback_query.message.delete()
    await premium_info(fake_msg)
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "confirm_payment")
async def confirm_payment(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if user_id in ADMINS:
        add_premium(user_id, 36500)
        await callback_query.message.edit_text(
            "👑 **Premium активирован на 100 лет!** 🎉",
            reply_markup=main_keyboard(user_id)
        )
        await callback_query.answer()
        return
    
    if is_premium(user_id):
        await callback_query.message.edit_text(
            "✅ **У тебя уже есть Premium!**",
            reply_markup=main_keyboard(user_id)
        )
        await callback_query.answer()
        return
    
    add_premium(user_id, 30)
    await callback_query.message.edit_text(
        "✅ **Premium активирован на 30 дней!** 🎉\n\n"
        "🌸 Приятного использования!",
        reply_markup=main_keyboard(user_id)
    )
    await callback_query.answer()

# ===== ПОМОЩЬ =====
@dp.message_handler(lambda message: message.text == "📞 Помощь")
async def help_menu(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("💬 Написать в поддержку", url="https://t.me/PauseMomSupport_bot")
    )
    
    await message.answer(
        "📞 **Помощь**\n\n"
        "❓ **Частые вопросы:**\n"
        "• Как оплатить Premium? → Нажми «💎 Premium»\n"
        "• Проблемы с оплатой? → Напиши поддержке\n"
        "• Хочешь предложить идею? → Мы открыты!\n\n"
        "🕐 Мы отвечаем в течение 24 часов.\n"
        "💝 Спасибо, что ты с нами!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ===== НАЗАД =====
@dp.message_handler(lambda message: message.text == "🔙 Главное меню")
async def back_to_menu(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_keyboard(message.from_user.id))

# ===== АДМИН-ПАНЕЛЬ =====
@dp.message_handler(commands=['admin'])
async def admin_command(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("⛔ **Доступ запрещён.**")
        return
    
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("👑 Активировать Premium (навсегда)"),
        KeyboardButton("👑 Активировать Premium (1 месяц)")
    )
    keyboard.add(
        KeyboardButton("📊 Пользователи (статистика)"),
        KeyboardButton("🔙 Главное меню")
    )
    
    await message.answer(
        "👑 **Админ-панель**\n\n"
        "Добро пожаловать, создатель! 👋\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )

@dp.message_handler(lambda message: message.text == "👑 Активировать Premium (навсегда)")
async def admin_premium_forever(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("⛔ Доступ запрещён.")
        return
    
    add_premium(user_id, 36500)
    await message.answer(
        "✅ **Premium активирован НАВСЕГДА!** 🎉\n\n"
        "Срок действия — 100 лет (до 2126 года).\n\n"
        "🌸 Приятного использования!",
        reply_markup=main_keyboard(user_id)
    )

@dp.message_handler(lambda message: message.text == "👑 Активировать Premium (1 месяц)")
async def admin_premium_1month(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("⛔ Доступ запрещён.")
        return
    
    add_premium(user_id, 30)
    await message.answer(
        "✅ **Premium активирован на 1 месяц!** 🎉",
        reply_markup=main_keyboard(user_id)
    )

@dp.message_handler(lambda message: message.text == "📊 Пользователи (статистика)")
async def admin_stats(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("⛔ Доступ запрещён.")
        return
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end IS NOT NULL AND subscription_end > date('now')")
    premium_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(total_sos) FROM users")
    total_sos = cursor.fetchone()[0] or 0
    
    await message.answer(
        f"📊 **Статистика бота**\n\n"
        f"👥 **Всего пользователей:** {total_users}\n"
        f"💎 **Premium-пользователей:** {premium_users}\n"
        f"🆘 **Всего SOS-пауз:** {total_sos}\n\n"
        f"📈 Бот растёт! 🌸",
        reply_markup=main_keyboard(user_id)
    )

if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
