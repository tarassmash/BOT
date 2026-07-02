import logging
import asyncio
import random
import time
import math
import traceback
import os
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramForbiddenError
import firebase_admin
from firebase_admin import credentials, firestore

# =========================================================
# DISCLAIMER & CONFIG
# =========================================================
DISCLAIMER_TEXT = (
    "⚠️ <b>ЗНЯТТЯ ВІДПОВІДАЛЬНОСТІ — ВАЖЛИВО!</b>\n\n"
    "Використовуючи кнопки <b>👍 Лайк</b>, <b>👎 Далі</b> та <b>💤 Завершити</b> "
    "для перегляду анкет, ви <b>повністю підтверджуєте та погоджуєтесь</b> з наступним:\n\n"
    "• Бот є лише технічною платформою для знайомств.\n"
    "• <b>Адміністрація бота НЕ несе жодної відповідальності</b> за:\n"
    " — зміст анкет, фото та опис користувачів\n"
    " — дії, слова, наміри та поведінку інших учасників\n"
    " — будь-які зустрічі в реальному житті\n"
    " — можливе шахрайство, образи, загрози тощо\n\n"
    "• Уся відповідальність лежить <b>виключно на вас</b>.\n"
    "• Ви використовуєте бот <b>на свій страх і ризик</b>.\n\n"
    "⚠️ <b>СТРОГО ЗАБОРОНЕНО:</b> оголене тіло, 18+ контент, порнографія, сексуальні фото.\n"
    "Порушення = негайний бан без попередження.\n\n"
    "Продовжуючи — ви підтверджуєте згоду."
)

COUNTRIES = ["Іспанія", "Польща", "Німеччина", "Чехія", "Італія"]

REPORT_REASONS = {
    "fake": "🕵️ Фейк / Спам / Бот",
    "explicit": "🔞 18+ / Оголе́не тіло",
    "harassment": "😡 Образи / Домагання / Токсичність",
    "scam": "💰 Шахрайство / Розвод",
    "other": "❓ Інше порушення"
}

BAN_THRESHOLD = 5

def calculate_distance(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return 99999
    R = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

ONBOARDING_TEXT = (
    "❤️ <b>Вітаємо в боті знайомств для українців за кордоном!</b>\n\n"
    "На ринку є багато додатків для знайомств, але <b>саме для українців у Європі</b> "
    "практично немає зручних і безпечних рішень.\n\n"
    "✅ <b>Наші головні переваги:</b>\n"
    "• Спеціально для українців у Іспанії, Польщі, Німеччині, Чехії та Італії\n"
    "• Показуємо анкети за відстанню + віком\n"
    "• Преміум-функція «Хто мене лайкнув?» відкривається <b>дуже просто</b>\n\n"
    "💎 <b>Як отримати Преміум:</b>\n"
    "Просто поділись посиланням на бота з другом. "
    "Як тільки він зареєструється — ти автоматично отримаєш доступ до перегляду лайків на 10 хвилин.\n\n"
    "Готовий почати?"
)

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# =========================================================
# DYNAMIC FIREBASE KEY CREATION (FOR RAILWAY)
# =========================================================
firebase_json_raw = os.getenv("FIREBASE_JSON")
if firebase_json_raw:
    try:
        with open("firebase_key.json", "w") as f:
            json.dump(json.loads(firebase_json_raw), f)
        print("✅ Файл firebase_key.json успішно створено з перемінних оточення!")
    except Exception as e:
        print(f"❌ Помилка запису firebase_key.json: {e}")

# =========================================================
# FIREBASE INITIALIZATION
# =========================================================
try:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase підключено!")
except Exception as e:
    print(f"❌ Firebase error: {e}")
    db = None

TOKEN = os.getenv("BOT_TOKEN", "8731550935:AAF_XmQNZjBmtnhtQ-cIJ3gFvYswg-eDiZs")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# =========================================================
# STATES
# =========================================================
class Registration(StatesGroup):
    captcha = State()
    waiting_for_name = State()
    waiting_for_age = State()
    waiting_for_country = State()
    waiting_for_gender = State()
    waiting_for_search = State()
    waiting_for_photo = State()
    waiting_for_photo_confirm = State()
    waiting_for_about = State()
    waiting_for_location = State()

class SearchFilters(StatesGroup):
    choosing_country = State()
    choosing_min_age = State()
    choosing_max_age = State()

# =========================================================
# KEYBOARDS
# =========================================================
def get_main_menu():
    kb = [
        [types.KeyboardButton(text="1. Дивитися анкети 👥")],
        [types.KeyboardButton(text="🔍 Пошук з фільтрами ⚙️")],
        [types.KeyboardButton(text="2. Моя анкета 📝")],
        [
            types.KeyboardButton(text="3. Редагувати анкету ✏️"),
            types.KeyboardButton(text="4. Видалити анкету ❌")
        ],
        [
            types.KeyboardButton(text="👀 Хто мене лайкнув?"),
            types.KeyboardButton(text="📤 Запросити друга (Преміум 10 хв)")
        ],
        [types.KeyboardButton(text="📜 Політика конфіденційності")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_main_menu_button_only():
    """Кнопка повернення в головне меню (завжди доступна в реєстрації)"""
    kb = [[types.KeyboardButton(text="🏠 Головне меню")]]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_filters_inline_keyboard(current_filters: dict = None):
    if current_filters is None:
        current_filters = {}
    country = current_filters.get("country") or "Будь-яка"
    min_age = current_filters.get("min_age") or "—"
    max_age = current_filters.get("max_age") or "—"
  
    text = (
        f"⚙️ <b>Поточні фільтри пошуку:</b>\n\n"
        f"🌍 Країна: <b>{country}</b>\n"
        f"🎂 Вік: <b>{min_age} — {max_age}</b>\n\n"
        "Обери дію:"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🌍 Змінити країну", callback_data="filter_change_country")],
        [types.InlineKeyboardButton(text="🎂 Змінити вік", callback_data="filter_change_age")],
        [
            types.InlineKeyboardButton(text="✅ Почати пошук", callback_data="filter_start_search"),
            types.InlineKeyboardButton(text="🔄 Скинути фільтри", callback_data="filter_reset")
        ],
        [types.InlineKeyboardButton(text="❌ Закрити", callback_data="filter_close")]
    ])
    return text, kb

def get_photo_confirm_keyboard():
    kb = [
        [types.KeyboardButton(text="✅ Ні, фото нормальне (без 18+)")],
        [types.KeyboardButton(text="❌ Так, є оголене тіло / 18+ (відхилити)")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)

# =========================================================
# SAFE FIREBASE OPERATIONS
# =========================================================
async def firebase_get(ref):
    for _ in range(5):
        try:
            return await asyncio.to_thread(ref.get)
        except Exception as e:
            logging.error(f"Firebase GET error: {e}")
            await asyncio.sleep(2)
    return None

async def firebase_set(ref, data):
    for _ in range(5):
        try:
            await asyncio.to_thread(ref.set, data)
            return True
        except Exception as e:
            logging.error(f"Firebase SET error: {e}")
            await asyncio.sleep(2)
    return False

async def firebase_delete(ref):
    for _ in range(5):
        try:
            await asyncio.to_thread(ref.delete)
            return True
        except Exception as e:
            logging.error(f"Firebase DELETE error: {e}")
            await asyncio.sleep(2)
    return False

# =========================================================
# SAFE SEND WRAPPERS
# =========================================================
async def safe_send_message(chat_id, text, **kwargs):
    for _ in range(5):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramNetworkError:
            await asyncio.sleep(5)
        except TelegramForbiddenError:
            return None
        except Exception as e:
            logging.error(f"send_message error: {e}")
            await asyncio.sleep(2)
    return None

async def safe_send_photo(chat_id, photo, caption=None, **kwargs):
    for _ in range(5):
        try:
            return await bot.send_photo(chat_id, photo, caption=caption, **kwargs)
        except Exception as e:
            logging.error(f"send_photo error: {e}")
            await asyncio.sleep(2)
    return None

async def safe_edit_media(message: types.Message, media, reply_markup=None):
    """Безпечне редагування медіа (для плавної стрічки)"""
    for _ in range(3):
        try:
            return await message.edit_media(media=media, reply_markup=reply_markup)
        except Exception as e:
            logging.warning(f"edit_media error (attempt {_+1}): {e}")
            await asyncio.sleep(1)
    return None

# =========================================================
# WATCHERS
# =========================================================
async def internet_watcher():
    while True:
        try:
            me = await bot.get_me()
            logging.info(f"🌐 Internet OK @{me.username}")
        except Exception as e:
            logging.error(f"❌ INTERNET LOST: {e}")
        await asyncio.sleep(30)

async def firebase_watcher():
    while True:
        try:
            if db:
                await asyncio.to_thread(db.collection("system").document("ping").set, {"time": firestore.SERVER_TIMESTAMP})
                logging.info("🔥 Firebase OK")
        except Exception as e:
            logging.error(f"Firebase dead: {e}")
        await asyncio.sleep(60)

# =========================================================
# REGISTRATION HANDLERS (з покращеною стійкістю)
# =========================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    try:
        user_id = str(message.from_user.id)
        args = message.text.split()[1:] if len(message.text.split()) > 1 else []
        referrer = None
        if args and args[0].startswith("ref_"):
            referrer = args[0][4:]

        doc = await firebase_get(db.collection("users").document(user_id))
        
        # Якщо користувач вже має повну анкету — головне меню
        if doc and doc.exists:
            if await is_user_banned(user_id):
                return await message.answer("🚫 Твоя анкета заблокована. Доступ до бота закрито.")
            await message.answer("❤️ З поверненням!", reply_markup=get_main_menu())
            return

        # Новий користувач — починаємо реєстрацію з капчі (захист від ботів)
        if referrer:
            await state.update_data(referrer=referrer)

        # Покращена капча (більші числа = важче для ботів)
        a = random.randint(10, 99)
        b = random.randint(5, 50)
        await state.update_data(captcha_answer=a + b)
        
        captcha_text = (
            "🤖 <b>Перевірка, що ти не бот</b>\n\n"
            f"<b>{a} + {b} = ?</b>\n\n"
            "Введи правильну відповідь, щоб продовжити реєстрацію.\n"
            "Це простий захист від автоматичних реєстрацій."
        )
        await message.answer(captcha_text, parse_mode="HTML", reply_markup=get_main_menu_button_only())
        await state.set_state(Registration.captcha)
        
    except Exception as e:
        logging.error(f"Start error: {e}")
        await message.answer("⚠️ Помилка. Натисни /start ще раз.", reply_markup=get_main_menu())

@dp.message(Registration.captcha)
async def process_captcha(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())

        if not message.text.isdigit():
            return await message.answer("❌ Введи число", reply_markup=get_main_menu_button_only())

        data = await state.get_data()
        if int(message.text) != data.get("captcha_answer", 0):
            # Нова капча при помилці
            a = random.randint(10, 99)
            b = random.randint(5, 50)
            await state.update_data(captcha_answer=a + b)
            return await message.answer(
                f"❌ Неправильно\n\n<b>{a} + {b} = ?</b>\n\nСпробуй ще раз.",
                parse_mode="HTML",
                reply_markup=get_main_menu_button_only()
            )
      
        await message.answer("✅ Перевірку пройдено! Дякуємо.")
        await asyncio.sleep(0.8)
      
        await message.answer(ONBOARDING_TEXT, parse_mode="HTML")
        await asyncio.sleep(1.2)
      
        await message.answer("👋 Як тебе звати?", reply_markup=get_main_menu_button_only())
        await state.set_state(Registration.waiting_for_name)
        
    except Exception as e:
        logging.error(f"Captcha error: {e}")

@dp.message(Registration.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
        name = message.text.strip()
        if len(name) < 2:
            return await message.answer("❌ Ім'я занадто коротке. Введи хоча б 2 символи.", reply_markup=get_main_menu_button_only())
        await state.update_data(name=name)
        await message.answer("🎂 Скільки тобі років?", reply_markup=get_main_menu_button_only())
        await state.set_state(Registration.waiting_for_age)
    except Exception as e:
        logging.error(f"Name error: {e}")

@dp.message(Registration.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
        if not message.text.isdigit():
            return await message.answer("❌ Введи число (вік)", reply_markup=get_main_menu_button_only())
        age = int(message.text)
        if age < 16 or age > 70:
            return await message.answer("❌ Вік має бути від 16 до 70 років.", reply_markup=get_main_menu_button_only())
        await state.update_data(age=age)
        kb = [[types.KeyboardButton(text=c)] for c in COUNTRIES] + [[types.KeyboardButton(text="🏠 Головне меню")]]
        await message.answer("🌍 Де ти зараз живеш?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True))
        await state.set_state(Registration.waiting_for_country)
    except Exception as e:
        logging.error(f"Age error: {e}")

@dp.message(Registration.waiting_for_country)
async def process_country(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
        await state.update_data(country=message.text)
        kb = [
            [types.KeyboardButton(text="Я Чоловік 👱‍♂️"), types.KeyboardButton(text="Я Жінка 👩")],
            [types.KeyboardButton(text="🏠 Головне меню")]
        ]
        await message.answer("👤 Вкажи свою стать", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True))
        await state.set_state(Registration.waiting_for_gender)
    except Exception as e:
        logging.error(f"Country error: {e}")

@dp.message(Registration.waiting_for_gender)
async def process_gender(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
        await state.update_data(gender=message.text)
        kb = [
            [types.KeyboardButton(text="Шукаю Дівчину 👩"), types.KeyboardButton(text="Шукаю Хлопця 👱‍♂️")],
            [types.KeyboardButton(text="🏠 Головне меню")]
        ]
        await message.answer("❤️ Кого ти шукаєш?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True))
        await state.set_state(Registration.waiting_for_search)
    except Exception as e:
        logging.error(f"Gender error: {e}")

@dp.message(Registration.waiting_for_search)
async def process_search(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
        await state.update_data(search=message.text)
        photo_text = (
            "📸 <b>Надішли своє реальне фото</b>\n\n"
            "⚠️ <b>СТРОГО ЗАБОРОНЕНО 18+ КОНТЕНТ:</b>\n"
            "• Оголе́не тіло, інтимні зони, сексуальні пози\n"
            "• Порнографія, еротика, напівоголені фото\n"
            "• Будь-який контент, що порушує правила Telegram\n\n"
            "✅ Дозволено: звичайні селфі, фото в одязі, природні фото.\n\n"
            "📎 Натисни на скріпку 📎 → обери фото.\n\n"
            "Після надсилання ми попросимо підтвердити, що фото без 18+."
        )
        await message.answer(photo_text, parse_mode="HTML", reply_markup=get_main_menu_button_only())
        await state.set_state(Registration.waiting_for_photo)
    except Exception as e:
        logging.error(f"Search error: {e}")

@dp.message(Registration.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    try:
        await state.update_data(photo=message.photo[-1].file_id)
        confirm_text = (
            "✅ <b>Фото отримано!</b>\n\n"
            "⚠️ <b>Підтвердь, будь ласка:</b>\n\n"
            "Чи містить це фото:\n"
            "• Оголе́не тіло / інтимні зони?\n"
            "• Сексуальний або 18+ контент?\n"
            "• Будь-які елементи еротики / порнографії?\n\n"
            "Якщо так — натисни «Відхилити» і надішли інше фото.\n"
            "Якщо ні — натисни «Фото нормальне»."
        )
        await message.answer(confirm_text, parse_mode="HTML", reply_markup=get_photo_confirm_keyboard())
        await state.set_state(Registration.waiting_for_photo_confirm)
    except Exception as e:
        logging.error(f"Photo error: {e}")

@dp.message(Registration.waiting_for_photo)
async def photo_error(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    await message.answer(
        "❌ <b>Фото обов’язкове!</b>\n\n"
        "📎 Натисни на скріпку 📎 → обери фото (не текст).",
        reply_markup=get_main_menu_button_only()
    )

@dp.message(Registration.waiting_for_photo_confirm)
async def process_photo_confirm(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
        
        if "Відхилити" in message.text or "Так, є оголене" in message.text:
            await message.answer(
                "❌ Зрозуміло. Надішли, будь ласка, інше фото без 18+ контенту.\n\n"
                "📎 Натисни на скріпку 📎 → обери нове фото.",
                reply_markup=get_main_menu_button_only()
            )
            await state.set_state(Registration.waiting_for_photo)
            return
        
        if "Ні, фото нормальне" in message.text or "Фото нормальне" in message.text:
            await message.answer("✅ Дякуємо за підтвердження! Фото прийнято.")
            await asyncio.sleep(0.7)
            await message.answer("📝 <b>Напиши трохи про себе</b>\n\nЦе останній крок реєстрації.", reply_markup=get_main_menu_button_only())
            await state.set_state(Registration.waiting_for_about)
            return
        
        await message.answer("Будь ласка, обери один з варіантів нижче:", reply_markup=get_photo_confirm_keyboard())
    except Exception as e:
        logging.error(f"Photo confirm error: {e}")

@dp.message(Registration.waiting_for_about)
async def process_about(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
        
        data = await state.get_data()
        user_id = str(message.from_user.id)
        referrer = data.get("referrer")
        
        profile = {
            "tg_id": user_id,
            "username": message.from_user.username or "",
            "name": data["name"],
            "age": data["age"],
            "country": data["country"],
            "gender": data["gender"],
            "search": data["search"],
            "photo": data["photo"],
            "about": message.text,
            "registered_at": firestore.SERVER_TIMESTAMP,
            "search_filters": {"country": None, "min_age": None, "max_age": None},
            "lat": None,
            "lon": None,
            "disclaimer_seen": False,
            "report_count": 0,
            "banned": False,
            "banned_at": None,
            "ban_reason": None
        }
        
        await firebase_set(db.collection("users").document(user_id), profile)
        
        if referrer:
            unlock_time = int(time.time()) + 600
            ref_doc = await firebase_get(db.collection("users").document(referrer))
            if ref_doc and ref_doc.exists:
                ref_data = ref_doc.to_dict() or {}
                await firebase_set(db.collection("users").document(referrer), {**ref_data, "likes_view_until": unlock_time})
                await safe_send_message(referrer, "🎉 Твій друг завершив реєстрацію! Ти отримав доступ до «Хто мене лайкнув?» на 10 хвилин!")
        
        await state.clear()
        await message.answer(
            "🎉 <b>Анкету створено успішно!</b>\n\n"
            "📍 Хочеш додати локацію, щоб бачити людей поруч?\n"
            "Надішли локацію (скріпка → Локація) або напиши «Пропустити»",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.set_state(Registration.waiting_for_location)
    except Exception as e:
        logging.error(f"About error: {e}")

@dp.message(Registration.waiting_for_location, F.location)
async def process_location(message: types.Message, state: FSMContext):
    try:
        user_id = str(message.from_user.id)
        lat = message.location.latitude
        lon = message.location.longitude
        doc = await firebase_get(db.collection("users").document(user_id))
        if doc and doc.exists:
            data = doc.to_dict() or {}
            await firebase_set(db.collection("users").document(user_id), {**data, "lat": lat, "lon": lon})
        await state.clear()
        await message.answer("✅ Локацію збережено! Тепер ти будеш бачити людей за відстанню.", reply_markup=get_main_menu())
    except Exception as e:
        logging.error(f"Location error: {e}")

@dp.message(Registration.waiting_for_location)
async def skip_location(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    if message.text and message.text.lower() in ["пропустити", "skip", "пізніше"]:
        await state.clear()
        await message.answer("Локацію пропущено. Ти завжди можеш додати її пізніше через меню.", reply_markup=get_main_menu())
    else:
        await message.answer("📍 Надішли локацію або напиши «Пропустити» / «🏠 Головне меню»", reply_markup=get_main_menu_button_only())

# =========================================================
# GLOBAL MAIN MENU HANDLER
# =========================================================
@dp.message(F.text == "🏠 Головне меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
    await message.answer("🏠 Головне меню", reply_markup=get_main_menu())

# =========================================================
# BAN SYSTEM HELPERS
# =========================================================
async def is_user_banned(user_id: str) -> bool:
    try:
        doc = await firebase_get(db.collection("users").document(user_id))
        if doc and doc.exists:
            data = doc.to_dict() or {}
            return data.get("banned", False)
    except Exception as e:
        logging.error(f"Ban check error: {e}")
    return False

async def ban_user(user_id: str, reason: str = "Багато скарг від користувачів"):
    try:
        doc = await firebase_get(db.collection("users").document(user_id))
        if doc and doc.exists:
            data = doc.to_dict() or {}
            await firebase_set(db.collection("users").document(user_id), {
                **data,
                "banned": True,
                "banned_at": firestore.SERVER_TIMESTAMP,
                "ban_reason": reason
            })
            await safe_send_message(user_id,
                "🚫 <b>Твою анкету заблоковано!</b>\n\n"
                f"Причина: {reason}\n\n"
                "Якщо вважаєш, що це помилка — напиши в підтримку.",
                parse_mode="HTML"
            )
            logging.info(f"User {user_id} banned. Reason: {reason}")
    except Exception as e:
        logging.error(f"Ban user error: {e}")

async def increment_report_count(reported_id: str, reporter_id: str, reason_text: str):
    try:
        doc = await firebase_get(db.collection("users").document(reported_id))
        if not doc or not doc.exists:
            return
       
        data = doc.to_dict() or {}
        current_count = data.get("report_count", 0) + 1
       
        report_data = {
            "reporter_id": reporter_id,
            "reported_id": reported_id,
            "reason": reason_text,
            "timestamp": firestore.SERVER_TIMESTAMP
        }
        await firebase_set(db.collection("reports").document(), report_data)
       
        await firebase_set(db.collection("users").document(reported_id), {
            **data,
            "report_count": current_count
        })
       
        if current_count >= BAN_THRESHOLD and not data.get("banned", False):
            await ban_user(reported_id, f"Автоматичний бан: {current_count} скарг")
           
    except Exception as e:
        logging.error(f"Increment report error: {e}")

# =========================================================
# ALGORITHMIC MATCHING ENGINE (плавна стрічка)
# =========================================================
async def send_next_candidate(chat_id: int, user_id: str, filters: dict = None, edit_message: types.Message = None):
    """
    Показує наступну анкету.
    Якщо edit_message передано — оновлює фото/текст/клавіатуру на місці (плавно, без зникнення).
    Інакше — надсилає нове повідомлення.
    """
    try:
        if await is_user_banned(user_id):
            text = "🚫 Твоя анкета заблокована. Доступ до пошуку закрито."
            if edit_message:
                try:
                    await edit_message.edit_text(text, reply_markup=get_main_menu())
                except:
                    await safe_send_message(chat_id, text, reply_markup=get_main_menu())
            else:
                await safe_send_message(chat_id, text, reply_markup=get_main_menu())
            return

        my_doc = await firebase_get(db.collection("users").document(user_id))
        if not my_doc or not my_doc.exists:
            text = "❌ Спочатку створи анкету через /start"
            if edit_message:
                try:
                    await edit_message.edit_text(text)
                except:
                    await safe_send_message(chat_id, text)
            else:
                await safe_send_message(chat_id, text)
            return

        my = my_doc.to_dict()
        my_age = my.get("age", 25)
        my_lat = my.get("lat")
        my_lon = my.get("lon")

        if filters is None:
            filters = my.get("search_filters", {"country": None, "min_age": None, "max_age": None})

        target_gender = "Я Жінка 👩" if "Дівчину" in my.get("search", "") else "Я Чоловік 👱‍♂️"
        query = db.collection("users").where("gender", "==", target_gender)

        country_filter = filters.get("country")
        if country_filter:
            query = query.where("country", "==", country_filter)
        else:
            query = query.where("country", "==", my.get("country"))

        docs = await asyncio.to_thread(lambda: query.limit(300).get())
        candidates = []
        for doc in docs:
            data = doc.to_dict()
            if data and doc.id != user_id and "photo" in data and "age" in data:
                age = data["age"]
                min_age = filters.get("min_age")
                max_age = filters.get("max_age")
                if min_age and age < min_age: continue
                if max_age and age > max_age: continue
                candidates.append(data)

        if not candidates:
            text = "😔 Анкет за твоїми фільтрами не знайдено."
            if edit_message:
                try:
                    await edit_message.edit_text(text, reply_markup=get_main_menu())
                except:
                    await safe_send_message(chat_id, text, reply_markup=get_main_menu())
            else:
                await safe_send_message(chat_id, text, reply_markup=get_main_menu())
            return

        seen_docs = await asyncio.to_thread(lambda: db.collection("users").document(user_id).collection("seen").get())
        seen_ids = {doc.id for doc in seen_docs}
        unseen = [c for c in candidates if c.get("tg_id") not in seen_ids]

        if not unseen:
            text = "😔 Більше немає нових анкет за твоїми фільтрами."
            if edit_message:
                try:
                    await edit_message.edit_text(text, reply_markup=get_main_menu())
                except:
                    await safe_send_message(chat_id, text, reply_markup=get_main_menu())
            else:
                await safe_send_message(chat_id, text, reply_markup=get_main_menu())
            return

        def sort_key(c):
            dist = calculate_distance(my_lat, my_lon, c.get("lat"), c.get("lon"))
            age_diff = abs(c.get("age", 99) - my_age)
            return (dist, age_diff, random.random())

        unseen.sort(key=sort_key)
        candidate = unseen[0]

        await firebase_set(
            db.collection("users").document(user_id).collection("seen").document(candidate["tg_id"]),
            {"ts": firestore.SERVER_TIMESTAMP}
        )

        dist_text = ""
        if my_lat and my_lon and candidate.get("lat") and candidate.get("lon"):
            dist = calculate_distance(my_lat, my_lon, candidate["lat"], candidate["lon"])
            if dist < 999:
                dist_text = f"📍 ~{int(dist)} км\n"

        text = f"👤 <b>{candidate['name']}</b>, {candidate['age']}\n🌍 {candidate['country']}\n{dist_text}\n📝 {candidate['about']}"

        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="👍 Лайк", callback_data=f"like_{candidate['tg_id']}"),
                types.InlineKeyboardButton(text="👎 Далі", callback_data="dislike")
            ],
            [
                types.InlineKeyboardButton(text="🚫 Поскаржитися", callback_data=f"report_{candidate['tg_id']}"),
                types.InlineKeyboardButton(text="⚙️ Змінити фільтри", callback_data="change_filters")
            ],
            [
                types.InlineKeyboardButton(text="💤 Завершити", callback_data="stop_search")
            ]
        ])

        if edit_message:
            # Плавне оновлення на місці (без зникнення/появи)
            try:
                media = types.InputMediaPhoto(
                    media=candidate["photo"],
                    caption=text,
                    parse_mode="HTML"
                )
                result = await safe_edit_media(edit_message, media, kb)
                if result is None:
                    # Fallback: видаляємо старе і надсилаємо нове
                    try:
                        await edit_message.delete()
                    except:
                        pass
                    await safe_send_photo(chat_id, candidate["photo"], caption=text, reply_markup=kb)
            except Exception as e:
                logging.warning(f"edit_media failed, sending new message: {e}")
                try:
                    await edit_message.delete()
                except:
                    pass
                await safe_send_photo(chat_id, candidate["photo"], caption=text, reply_markup=kb)
        else:
            # Перший показ — нове повідомлення
            await safe_send_photo(chat_id, candidate["photo"], caption=text, reply_markup=kb)

    except Exception as e:
        logging.error(f"send_next_candidate error:\n{traceback.format_exc()}")
        error_text = "⚠️ Помилка при пошуку анкет. Спробуй ще раз."
        if edit_message:
            try:
                await edit_message.edit_text(error_text, reply_markup=get_main_menu())
            except:
                await safe_send_message(chat_id, error_text, reply_markup=get_main_menu())
        else:
            await safe_send_message(chat_id, error_text, reply_markup=get_main_menu())

# =========================================================
# MAIN MENU HANDLERS
# =========================================================
@dp.message(F.text == "1. Дивитися анкети 👥")
async def menu_search(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    user_id = str(message.from_user.id)
    if await is_user_banned(user_id):
        return await message.answer("🚫 Твоя анкета заблокована. Доступ до пошуку закрито.")
    
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        data = doc.to_dict() or {}
        if not data.get("disclaimer_seen"):
            await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
            await firebase_set(db.collection("users").document(user_id), {**data, "disclaimer_seen": True})
            await asyncio.sleep(0.8)
    
    await message.answer("🔍 Шукаю анкети (відсортовані за відстанню + віком)...")
    await send_next_candidate(message.chat.id, user_id)

@dp.message(F.text == "🔍 Пошук з фільтрами ⚙️")
async def menu_search_with_filters(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    user_id = str(message.from_user.id)
    if await is_user_banned(user_id):
        return await message.answer("🚫 Твоя анкета заблокована. Доступ до пошуку закрито.")
    doc = await firebase_get(db.collection("users").document(user_id))
    if not doc or not doc.exists:
        return await message.answer("❌ Спочатку створи анкету через /start")
    data = doc.to_dict() or {}
    current_filters = data.get("search_filters", {"country": None, "min_age": None, "max_age": None})
    text, kb = get_filters_inline_keyboard(current_filters)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

# =========================================================
# CALLBACK INLINE QUERIES (покращена стрічка)
# =========================================================
@dp.callback_query(F.data == "filter_change_country")
async def filter_change_country(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.delete()
    kb = [[types.KeyboardButton(text=c)] for c in COUNTRIES] + [[types.KeyboardButton(text="Будь-яка країна"), types.KeyboardButton(text="🏠 Головне меню")]]
    await callback.message.answer("🌍 Обери країну (або «Будь-яка країна»):", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True))
    await state.set_state(SearchFilters.choosing_country)

@dp.message(SearchFilters.choosing_country)
async def process_filter_country(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    user_id = str(message.from_user.id)
    country = message.text if message.text != "Будь-яка країна" else None
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        data = doc.to_dict() or {}
        filters = data.get("search_filters", {})
        filters["country"] = country
        await firebase_set(db.collection("users").document(user_id), {**data, "search_filters": filters})
    await state.clear()
    text, kb = get_filters_inline_keyboard({"country": country or "Будь-яка", "min_age": "—", "max_age": "—"})
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "filter_change_age")
async def filter_change_age(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer("🎂 Введи мінімальний вік:", reply_markup=get_main_menu_button_only())
    await state.set_state(SearchFilters.choosing_min_age)

@dp.message(SearchFilters.choosing_min_age)
async def process_min_age(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    if not message.text.isdigit():
        return await message.answer("❌ Введи число", reply_markup=get_main_menu_button_only())
    await state.update_data(min_age=int(message.text))
    await message.answer("🎂 Введи максимальний вік:", reply_markup=get_main_menu_button_only())
    await state.set_state(SearchFilters.choosing_max_age)

@dp.message(SearchFilters.choosing_max_age)
async def process_max_age(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    if not message.text.isdigit():
        return await message.answer("❌ Введи число", reply_markup=get_main_menu_button_only())
    data = await state.get_data()
    min_age = data.get("min_age", 16)
    max_age = int(message.text)
    if max_age < min_age:
        return await message.answer("❌ Макс. вік не може бути меншим за мін.", reply_markup=get_main_menu_button_only())
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        user_data = doc.to_dict() or {}
        filters = user_data.get("search_filters", {})
        filters["min_age"] = min_age
        filters["max_age"] = max_age
        await firebase_set(db.collection("users").document(user_id), {**user_data, "search_filters": filters})
    await state.clear()
    text, kb = get_filters_inline_keyboard({"country": "—", "min_age": min_age, "max_age": max_age})
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "filter_start_search")
async def filter_start_search(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    user_id = str(callback.from_user.id)
    await callback.message.answer("🔍 Починаю пошук за фільтрами...")
    await send_next_candidate(callback.message.chat.id, user_id)

@dp.callback_query(F.data == "filter_reset")
async def filter_reset(callback: types.CallbackQuery):
    await callback.answer()
    user_id = str(callback.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        data = doc.to_dict() or {}
        await firebase_set(db.collection("users").document(user_id), {**data, "search_filters": {"country": None, "min_age": None, "max_age": None}})
    await callback.message.delete()
    text, kb = get_filters_inline_keyboard({"country": "Будь-яка", "min_age": "—", "max_age": "—"})
    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "filter_close")
async def filter_close(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()

@dp.callback_query(F.data == "change_filters")
async def change_filters_during_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.delete()
    user_id = str(callback.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    current_filters = (doc.to_dict() or {}).get("search_filters", {}) if doc else {}
    text, kb = get_filters_inline_keyboard(current_filters)
    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "dislike")
async def handle_dislike(callback: types.CallbackQuery):
    await callback.answer()
    user_id = str(callback.from_user.id)
    # Плавне оновлення на місці (без delete + send)
    await send_next_candidate(callback.message.chat.id, user_id, edit_message=callback.message)

@dp.callback_query(F.data == "stop_search")
async def handle_stop_search(callback: types.CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer("💤 Пошук завершено. Ти завжди можеш повернутися через меню.", reply_markup=get_main_menu())

# =========================================================
# OTHER SYSTEM HANDLERS
# =========================================================
@dp.message(F.text.in_({"2. Моя анкета 📝", "2. Моя анкету 📝"}))
async def menu_profile(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    try:
        user_id = str(message.from_user.id)
        if await is_user_banned(user_id):
            return await message.answer("🚫 Твоя анкета заблокована.")
        doc = await firebase_get(db.collection("users").document(user_id))
        if not doc or not doc.exists:
            return await message.answer("❌ Анкета не знайдена")
        p = doc.to_dict()
        text = f"👤 {p['name']}, {p['age']}\n🌍 {p['country']}\n\n📝 {p['about']}"
        await safe_send_photo(message.chat.id, p["photo"], caption=text)
        await message.answer("🏠 Для повернення в меню натисни кнопку нижче або використовуй /menu", reply_markup=get_main_menu())
    except Exception as e:
        logging.error(f"My profile error: {e}")

@dp.message(F.text == "3. Редагувати анкету ✏️")
async def menu_edit(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    await message.answer(
        "✏️ Щоб відредагувати анкету:\n\n"
        "1. Видали стару анкету кнопкою «4. Видалити анкету ❌»\n"
        "2. Зареєструйся заново через /start\n\n"
        "⚠️ На жаль, редагування «на льоту» поки що не підтримується.",
        reply_markup=get_main_menu()
    )

@dp.message(F.text == "4. Видалити анкету ❌")
async def menu_delete(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    try:
        user_id = str(message.from_user.id)
        doc = await firebase_get(db.collection("users").document(user_id))
        if not doc or not doc.exists:
            return await message.answer("❌ Анкети немає")
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="✅ Так, видалити назавжди", callback_data="confirm_delete"),
                types.InlineKeyboardButton(text="❌ Ні, скасувати", callback_data="cancel_delete")
            ]
        ])
        await message.answer("⚠️ <b>УВАГА! ВИДАЛЕННЯ АНКЕТИ</b>\n\nВи дійсно хочете назавжди видалити свою анкету?", parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logging.error(f"Delete error: {e}")

@dp.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: types.CallbackQuery):
    await callback.answer("Скасовано")
    await callback.message.delete()
    await callback.message.answer("Дія скасована.", reply_markup=get_main_menu())

@dp.callback_query(F.data == "confirm_delete")
async def confirm_delete(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    user_id = str(callback.from_user.id)
    ref = db.collection("users").document(user_id)
  
    try:
        seen_docs = await asyncio.to_thread(lambda: ref.collection("seen").get())
        for doc in seen_docs:
            await firebase_delete(doc.reference)
        likes_docs = await asyncio.to_thread(lambda: ref.collection("likes").get())
        for doc in likes_docs:
            await firebase_delete(doc.reference)
    except Exception as e:
        logging.error(f"Subcollection cleanup error: {e}")
      
    await firebase_delete(ref)
    await callback.message.answer("❌ Твоя анкета повністю видалена з бази даних. Натисни /start, щоб зареєструватися знову.")

@dp.message(F.text == "👀 Хто мене лайкнув?")
async def show_who_liked_me(message: types.Message):
    user_id = str(message.from_user.id)
    if await is_user_banned(user_id):
        return await message.answer("🚫 Твоя анкета заблокована. Доступ до функції закрито.")
    doc = await firebase_get(db.collection("users").document(user_id))
    if not doc or not doc.exists:
        return await message.answer("❌ Спочатку створи анкету через /start")
    data = doc.to_dict() or {}
    if time.time() > data.get("likes_view_until", 0):
        return await message.answer("🔒 Доступ закритий. Запроси друга за допомогою кнопки нижче, щоб відкрити доступ на 10 хвилин!")
    likes_docs = await asyncio.to_thread(lambda: db.collection("users").document(user_id).collection("likes").get())
    if not likes_docs:
        return await message.answer("😔 Поки що тебе ніхто не лайкнув.")
    text = "❤️ Тебе лайкнули:\n\n"
    for like_doc in likes_docs:
        liker_id = like_doc.id
        liker_doc = await firebase_get(db.collection("users").document(liker_id))
        if liker_doc and liker_doc.exists:
            l = liker_doc.to_dict()
            text += f"👤 {l.get('name')}, {l.get('age')} — @{l.get('username','')}\n"
    await message.answer(text)
    await message.answer("🏠 Повернутися в головне меню:", reply_markup=get_main_menu())

@dp.message(F.text == "📤 Запросити друга (Преміум 10 хв)")
async def invite_friend(message: types.Message):
    bot_info = await bot.get_me()
    user_id = str(message.from_user.id)
    link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    await message.answer(f"📤 Поділись посиланням:\n\n<code>{link}</code>\n\nПісля того як друг зареєструється — ти отримаєш Преміум на 10 хвилин!", parse_mode="HTML")
    await message.answer("🏠 Повернутися в головне меню:", reply_markup=get_main_menu())

@dp.message(F.text == "📜 Політика конфіденційності")
async def show_privacy_policy(message: types.Message):
    await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
    await message.answer("🏠 Повернутися в головне меню:", reply_markup=get_main_menu())

# =========================================================
# LIKE ENGINE & MATCH DETECTION (з плавним переходом)
# =========================================================
@dp.callback_query(F.data.startswith("like_"))
async def handle_like(callback: types.CallbackQuery):
    try:
        await callback.answer()
        my_id = str(callback.from_user.id)
        target_id = callback.data.split("_")[1]
      
        await firebase_set(db.collection("users").document(my_id).collection("likes").document(target_id), {"ts": firestore.SERVER_TIMESTAMP})
        reverse = await firebase_get(db.collection("users").document(target_id).collection("likes").document(my_id))
      
        if reverse and reverse.exists:
            me_doc = await firebase_get(db.collection("users").document(my_id))
            them_doc = await firebase_get(db.collection("users").document(target_id))
            me = me_doc.to_dict() if me_doc else {}
            them = them_doc.to_dict() if them_doc else {}
          
            await safe_send_message(my_id, f"🎉 <b>МЕТЧ!</b> Пиши @{them.get('username', '')}", parse_mode="HTML")
            await safe_send_message(target_id, f"🎉 <b>МЕТЧ!</b> Пиши @{me.get('username', '')}", parse_mode="HTML")
        
        # Плавне оновлення на місці
        await send_next_candidate(callback.message.chat.id, my_id, edit_message=callback.message)
    except Exception as e:
        logging.error(f"Error in handle_like: {e}")

# =========================================================
# REPORT / BAN SYSTEM (з плавним переходом)
# =========================================================
@dp.callback_query(F.data.startswith("report_"))
async def handle_report_start(callback: types.CallbackQuery):
    try:
        await callback.answer()
        reported_id = callback.data.split("_")[1]
       
        buttons = []
        for code, text in REPORT_REASONS.items():
            buttons.append([types.InlineKeyboardButton(
                text=text,
                callback_data=f"reason_{reported_id}_{code}"
            )])
       
        buttons.append([types.InlineKeyboardButton(text="❌ Скасувати", callback_data="report_cancel")])
       
        kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
       
        await callback.message.edit_caption(
            caption="🚫 <b>Обери причину скарги:</b>\n\n"
                    "Твоя скарга допоможе зробити бот безпечнішим.\n"
                    "Зловживання скаргами також карається.",
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Report start error: {e}")

@dp.callback_query(F.data.startswith("reason_"))
async def handle_report_reason(callback: types.CallbackQuery):
    try:
        await callback.answer()
        parts = callback.data.split("_")
        reported_id = parts[1]
        reason_code = parts[2]
       
        reporter_id = str(callback.from_user.id)
        reason_text = REPORT_REASONS.get(reason_code, "Інше порушення")
       
        if reporter_id == reported_id:
            await callback.message.edit_caption("❌ Не можна скаржитися на самого себе.")
            await asyncio.sleep(1.5)
            await callback.message.delete()
            return await send_next_candidate(callback.message.chat.id, reporter_id)
       
        await increment_report_count(reported_id, reporter_id, reason_text)
       
        await callback.message.edit_caption(
            caption="✅ <b>Дякуємо!</b> Скарга надіслана.\n\n"
                    "Адміністрація розгляне її найближчим часом.\n"
                    "Продовжуємо пошук...",
            parse_mode="HTML"
        )
        await asyncio.sleep(1.0)
        
        # Плавне оновлення на місці
        await send_next_candidate(callback.message.chat.id, reporter_id, edit_message=callback.message)
       
    except Exception as e:
        logging.error(f"Report reason error: {e}")

@dp.callback_query(F.data == "report_cancel")
async def handle_report_cancel(callback: types.CallbackQuery):
    await callback.answer("Скасовано")
    user_id = str(callback.from_user.id)
    # Плавне оновлення на місці
    await send_next_candidate(callback.message.chat.id, user_id, edit_message=callback.message)

# =========================================================
# ASYNC MAIN RUNNER
# =========================================================
async def main():
    if db is not None:
        asyncio.create_task(internet_watcher())
        asyncio.create_task(firebase_watcher())
        print("🚀 Бот запущено в режимі Long Polling (покращена версія)!")
        print("✅ Плавна стрічка анкет (edit_media)")
        print("✅ Покращена капча (захист від ботів)")
        print("✅ Стійка реєстрація + кнопка «🏠 Головне меню» на кожному етапі")
        await dp.start_polling(bot)
    else:
        logging.critical("Критична помилка: Firebase не підключено! Запуск неможливий.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот зупинено.")
