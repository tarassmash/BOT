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
    "• <b>Адміністрація бота НЕ несе жодної відповідальності</b> за зміст анкет, зустрічі тощо.\n"
    "• Уся відповідальність лежить виключно на вас.\n\n"
    "⚠️ СТРОГО ЗАБОРОНЕНО 18+ контент. Порушення = бан.\n\n"
    "Продовжуючи — ви підтверджуєте згоду."
)

COUNTRIES = ["Іспанія", "Польща", "Німеччина", "Чехія", "Італія"]

REPORT_REASONS = {
    "fake": "🕵️ Фейк / Спам / Бот",
    "explicit": "🔞 18+ / Оголе́не тіло",
    "harassment": "😡 Образи / Домагання",
    "scam": "💰 Шахрайство",
    "other": "❓ Інше"
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
    "Готовий почати?"
)

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# =========================================================
# FIREBASE
# =========================================================
firebase_json_raw = os.getenv("FIREBASE_JSON")
if firebase_json_raw:
    try:
        with open("firebase_key.json", "w") as f:
            json.dump(json.loads(firebase_json_raw), f)
        print("✅ firebase_key.json створено!")
    except Exception as e:
        print(f"❌ Помилка firebase_key: {e}")

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

# =========================================================
# KEYBOARDS
# =========================================================
def get_main_menu():
    kb = [
        [types.KeyboardButton(text="1. Дивитися анкети 👥")],
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
    kb = [[types.KeyboardButton(text="🏠 Головне меню")]]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_photo_confirm_keyboard():
    kb = [
        [types.KeyboardButton(text="✅ Ні, фото нормальне (без 18+)")],
        [types.KeyboardButton(text="❌ Так, є оголене тіло / 18+")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)

# =========================================================
# SAFE OPERATIONS
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

async def safe_send_message(chat_id, text, **kwargs):
    for _ in range(5):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
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

# =========================================================
# WATCHERS
# =========================================================
async def internet_watcher():
    while True:
        try:
            me = await bot.get_me()
            logging.info(f"🌐 Internet OK @{me.username}")
        except Exception:
            pass
        await asyncio.sleep(30)

async def firebase_watcher():
    while True:
        try:
            if db:
                await asyncio.to_thread(db.collection("system").document("ping").set, {"time": firestore.SERVER_TIMESTAMP})
        except Exception:
            pass
        await asyncio.sleep(60)

# =========================================================
# BAN SYSTEM
# =========================================================
async def is_user_banned(user_id: str) -> bool:
    try:
        doc = await firebase_get(db.collection("users").document(user_id))
        if doc and doc.exists:
            return doc.to_dict().get("banned", False)
    except Exception:
        pass
    return False

async def ban_user(user_id: str, reason: str = "Багато скарг"):
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
            await safe_send_message(user_id, f"🚫 <b>Твою анкету заблоковано!</b>\nПричина: {reason}", parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ban error: {e}")

async def increment_report_count(reported_id: str, reporter_id: str, reason_text: str):
    try:
        doc = await firebase_get(db.collection("users").document(reported_id))
        if not doc or not doc.exists:
            return
        data = doc.to_dict() or {}
        current = data.get("report_count", 0) + 1

        await firebase_set(db.collection("reports").document(), {
            "reporter_id": reporter_id,
            "reported_id": reported_id,
            "reason": reason_text,
            "timestamp": firestore.SERVER_TIMESTAMP
        })

        await firebase_set(db.collection("users").document(reported_id), {**data, "report_count": current})

        if current >= BAN_THRESHOLD and not data.get("banned", False):
            await ban_user(reported_id, f"Автобан: {current} скарг")
    except Exception as e:
        logging.error(f"Report error: {e}")

# =========================================================
# REGISTRATION HANDLERS
# =========================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    try:
        user_id = str(message.from_user.id)
        args = message.text.split()[1:] if len(message.text.split()) > 1 else []
        referrer = args[0][4:] if args and args[0].startswith("ref_") else None

        doc = await firebase_get(db.collection("users").document(user_id))
        if doc and doc.exists:
            if await is_user_banned(user_id):
                return await message.answer("🚫 Твоя анкета заблокована.")
            await message.answer("❤️ З поверненням!", reply_markup=get_main_menu())
            return

        if referrer:
            await state.update_data(referrer=referrer)

        a = random.randint(1, 9)
        b = random.randint(1, 9)
        await state.update_data(captcha_answer=a + b)
        await message.answer(f"🤖 Перевірка\n\n{a} + {b} = ?", reply_markup=get_main_menu_button_only())
        await state.set_state(Registration.captcha)
    except Exception as e:
        logging.error(f"Start error: {e}")

@dp.message(Registration.captcha)
async def process_captcha(message: types.Message, state: FSMContext):
    try:
        if message.text == "🏠 Головне меню":
            await state.clear()
            return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())

        if not message.text.isdigit():
            return await message.answer("❌ Введи число", reply_markup=get_main_menu_button_only())

        data = await state.get_data()
        if int(message.text) != data["captcha_answer"]:
            a, b = random.randint(1,9), random.randint(1,9)
            await state.update_data(captcha_answer=a+b)
            return await message.answer(f"❌ Неправильно\n\n{a} + {b} = ?", reply_markup=get_main_menu_button_only())

        await message.answer("✅ Перевірку пройдено!")
        await asyncio.sleep(1)
        await message.answer(ONBOARDING_TEXT, parse_mode="HTML")
        await asyncio.sleep(1)
        await message.answer("👋 Як тебе звати?", reply_markup=get_main_menu_button_only())
        await state.set_state(Registration.waiting_for_name)
    except Exception as e:
        logging.error(f"Captcha error: {e}")

# ... (process_name, process_age, process_country, process_gender, process_search, process_photo, process_photo_confirm — залишаються без змін)

@dp.message(Registration.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    name = message.text.strip()
    if len(name) < 2:
        return await message.answer("❌ Коротке ім'я", reply_markup=get_main_menu_button_only())
    await state.update_data(name=name)
    await message.answer("🎂 Скільки тобі років?", reply_markup=get_main_menu_button_only())
    await state.set_state(Registration.waiting_for_age)

@dp.message(Registration.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    if not message.text.isdigit():
        return await message.answer("❌ Введи число", reply_markup=get_main_menu_button_only())
    age = int(message.text)
    if age < 16 or age > 70:
        return await message.answer("❌ Вік 16-70", reply_markup=get_main_menu_button_only())
    await state.update_data(age=age)
    kb = [[types.KeyboardButton(text=c)] for c in COUNTRIES] + [[types.KeyboardButton(text="🏠 Головне меню")]]
    await message.answer("🌍 Де ти зараз?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True))
    await state.set_state(Registration.waiting_for_country)

@dp.message(Registration.waiting_for_country)
async def process_country(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    await state.update_data(country=message.text)
    kb = [
        [types.KeyboardButton(text="Я Чоловік 👱‍♂️"), types.KeyboardButton(text="Я Жінка 👩")],
        [types.KeyboardButton(text="🏠 Головне меню")]
    ]
    await message.answer("👤 Вкажи стать", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True))
    await state.set_state(Registration.waiting_for_gender)

@dp.message(Registration.waiting_for_gender)
async def process_gender(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    await state.update_data(gender=message.text)
    kb = [
        [types.KeyboardButton(text="Шукаю Дівчину 👩"), types.KeyboardButton(text="Шукаю Хлопця 👱‍♂️")],
        [types.KeyboardButton(text="🏠 Головне меню")]
    ]
    await message.answer("❤️ Кого шукаєш?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True))
    await state.set_state(Registration.waiting_for_search)

@dp.message(Registration.waiting_for_search)
async def process_search(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    await state.update_data(search=message.text)
    await message.answer(
        "📸 Надішли своє реальне фото (без 18+!)",
        reply_markup=get_main_menu_button_only()
    )
    await state.set_state(Registration.waiting_for_photo)

@dp.message(Registration.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer(
        "⚠️ Підтвердь: на фото немає оголеного тіла чи 18+ контенту?",
        reply_markup=get_photo_confirm_keyboard()
    )
    await state.set_state(Registration.waiting_for_photo_confirm)

@dp.message(Registration.waiting_for_photo)
async def photo_error(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    await message.answer("❌ Надішли фото!", reply_markup=get_main_menu_button_only())

@dp.message(Registration.waiting_for_photo_confirm)
async def process_photo_confirm(message: types.Message, state: FSMContext):
    if message.text == "🏠 Головне меню":
        await state.clear()
        return await message.answer("🏠 Головне меню", reply_markup=get_main_menu())
    if "Відхилити" in message.text or "Так, є оголене" in message.text:
        await message.answer("❌ Надішли інше фото без 18+", reply_markup=get_main_menu_button_only())
        await state.set_state(Registration.waiting_for_photo)
        return
    await message.answer("✅ Фото прийнято!")
    await asyncio.sleep(0.8)
    await message.answer("📝 Напиши трохи про себе", reply_markup=get_main_menu_button_only())
    await state.set_state(Registration.waiting_for_about)

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
                await safe_send_message(referrer, "🎉 Твій друг зареєструвався! Ти отримав Преміум на 10 хвилин!")

        await state.clear()
        await message.answer(
            "🎉 Анкета створена!\n\nНадішли локацію або напиши «Пропустити»",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.set_state(Registration.waiting_for_location)
    except Exception as e:
        logging.error(f"About error: {e}")

@dp.message(Registration.waiting_for_location, F.location)
async def process_location(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lat = message.location.latitude
    lon = message.location.longitude
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        data = doc.to_dict() or {}
        await firebase_set(db.collection("users").document(user_id), {**data, "lat": lat, "lon": lon})
    await state.clear()
    await message.answer("✅ Локацію збережено!", reply_markup=get_main_menu())

@dp.message(Registration.waiting_for_location)
async def skip_location(message: types.Message, state: FSMContext):
    if message.text and message.text.lower() in ["пропустити", "skip", "пізніше"]:
        await state.clear()
        await message.answer("Локацію пропущено.", reply_markup=get_main_menu())

# =========================================================
# MAIN MENU & SEARCH
# =========================================================
@dp.message(F.text == "🏠 Головне меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    if await state.get_state():
        await state.clear()
    await message.answer("🏠 Головне меню", reply_markup=get_main_menu())

@dp.message(F.text == "1. Дивитися анкети 👥")
async def menu_search(message: types.Message, state: FSMContext):
    if await state.get_state():
        return await message.answer("⚠️ Заверши реєстрацію спочатку!")
    user_id = str(message.from_user.id)
    if await is_user_banned(user_id):
        return await message.answer("🚫 Ти заблокований.")
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists and not doc.to_dict().get("disclaimer_seen"):
        await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
        await firebase_set(db.collection("users").document(user_id), {**doc.to_dict(), "disclaimer_seen": True})
    await message.answer("🔍 Шукаю анкети...")
    await send_next_candidate(message, user_id)

# =========================================================
# MATCHING ENGINE
# =========================================================
async def send_next_candidate(message: types.Message, user_id: str):
    try:
        if await is_user_banned(user_id):
            return await message.answer("🚫 Ти заблокований.")

        my_doc = await firebase_get(db.collection("users").document(user_id))
        if not my_doc or not my_doc.exists:
            return await message.answer("❌ Анкета не знайдена. Напиши /start")

        my = my_doc.to_dict()
        my_lat = my.get("lat")
        my_lon = my.get("lon")

        target_gender = "Я Жінка 👩" if "Дівчину" in my.get("search", "") else "Я Чоловік 👱‍♂️"

        query = (db.collection("users")
                 .where("gender", "==", target_gender)
                 .where("country", "==", my.get("country")))

        docs = await asyncio.to_thread(lambda: query.limit(300).get())
        candidates = [d.to_dict() for d in docs if d.id != user_id and "photo" in d.to_dict()]

        if not candidates:
            return await message.answer("😔 Анкет у твоїй країні поки немає.")

        seen = await asyncio.to_thread(lambda: db.collection("users").document(user_id).collection("seen").get())
        seen_ids = {d.id for d in seen}

        unseen = [c for c in candidates if c.get("tg_id") not in seen_ids]
        if not unseen:
            return await message.answer("😔 Більше немає анкет.")

        def sort_key(c):
            dist = calculate_distance(my_lat, my_lon, c.get("lat"), c.get("lon"))
            return (dist, random.random())

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

        text = f"👤 {candidate['name']}, {candidate['age']}\n🌍 {candidate['country']}\n{dist_text}\n📝 {candidate['about']}"

        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="👍 Лайк", callback_data=f"like_{candidate['tg_id']}"),
                types.InlineKeyboardButton(text="👎 Далі", callback_data="dislike")
            ],
            [types.InlineKeyboardButton(text="🚫 Поскаржитися", callback_data=f"report_{candidate['tg_id']}")],
            [types.InlineKeyboardButton(text="💤 Завершити", callback_data="stop_search")]
        ])

        await safe_send_photo(message.chat.id, candidate["photo"], caption=text, reply_markup=kb)
    except Exception as e:
        logging.error(f"send_next_candidate error: {traceback.format_exc()}")
        await message.answer("⚠️ Помилка пошуку.")

# =========================================================
# CALLBACKS
# =========================================================
@dp.callback_query(F.data == "dislike")
async def handle_dislike(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    await send_next_candidate(callback.message, str(callback.from_user.id))

@dp.callback_query(F.data == "stop_search")
async def handle_stop_search(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer("💤 Пошук завершено.", reply_markup=get_main_menu())

@dp.callback_query(F.data.startswith("like_"))
async def handle_like(callback: types.CallbackQuery):
    try:
        await callback.answer()
        await callback.message.delete()
        my_id = str(callback.from_user.id)
        target_id = callback.data.split("_")[1]

        await firebase_set(db.collection("users").document(my_id).collection("likes").document(target_id), {"ts": firestore.SERVER_TIMESTAMP})

        reverse = await firebase_get(db.collection("users").document(target_id).collection("likes").document(my_id))
        if reverse and reverse.exists:
            me = (await firebase_get(db.collection("users").document(my_id))).to_dict()
            them = (await firebase_get(db.collection("users").document(target_id))).to_dict()
            await safe_send_message(my_id, f"🎉 <b>МЕТЧ!</b> Пиши @{them.get('username','')}", parse_mode="HTML")
            await safe_send_message(target_id, f"🎉 <b>МЕТЧ!</b> Пиши @{me.get('username','')}", parse_mode="HTML")

        await send_next_candidate(callback.message, my_id)
    except Exception as e:
        logging.error(f"Like error: {e}")

@dp.callback_query(F.data.startswith("report_"))
async def handle_report(callback: types.CallbackQuery):
    await callback.answer()
    reported_id = callback.data.split("_")[1]
    reporter_id = str(callback.from_user.id)
    if reporter_id == reported_id:
        await callback.message.edit_caption("❌ Не можна скаржитися на себе.")
        await asyncio.sleep(1.5)
        await callback.message.delete()
        await send_next_candidate(callback.message, reporter_id)
        return

    reason_text = "Скарга"
    await increment_report_count(reported_id, reporter_id, reason_text)
    await callback.message.edit_caption("✅ Скарга надіслана.")
    await asyncio.sleep(1)
    await callback.message.delete()
    await send_next_candidate(callback.message, reporter_id)

# =========================================================
# OTHER HANDLERS
# =========================================================
@dp.message(F.text.in_({"2. Моя анкета 📝", "2. Моя анкету 📝"}))
async def menu_profile(message: types.Message):
    user_id = str(message.from_user.id)
    if await is_user_banned(user_id):
        return await message.answer("🚫 Ти заблокований.")
    doc = await firebase_get(db.collection("users").document(user_id))
    if not doc or not doc.exists:
        return await message.answer("❌ Анкета не знайдена")
    p = doc.to_dict()
    text = f"👤 {p['name']}, {p['age']}\n🌍 {p['country']}\n\n📝 {p['about']}"
    await safe_send_photo(message.chat.id, p["photo"], caption=text)
    await message.answer("🏠 Головне меню:", reply_markup=get_main_menu())

@dp.message(F.text == "3. Редагувати анкету ✏️")
async def menu_edit(message: types.Message):
    await message.answer("Щоб змінити анкету — видали стару та зареєструйся заново через /start", reply_markup=get_main_menu())

@dp.message(F.text == "4. Видалити анкету ❌")
async def menu_delete(message: types.Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Так, видалити", callback_data="confirm_delete"),
         types.InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_delete")]
    ])
    await message.answer("⚠️ Видалити анкету назавжди?", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "confirm_delete")
async def confirm_delete(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    ref = db.collection("users").document(user_id)
    await firebase_delete(ref)
    await callback.message.answer("✅ Анкета видалена. Напиши /start для нової реєстрації.")
    await callback.message.delete()

@dp.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: types.CallbackQuery):
    await callback.answer("Скасовано")
    await callback.message.delete()

@dp.message(F.text == "👀 Хто мене лайкнув?")
async def show_who_liked_me(message: types.Message):
    user_id = str(message.from_user.id)
    if await is_user_banned(user_id):
        return await message.answer("🚫 Ти заблокований.")
    doc = await firebase_get(db.collection("users").document(user_id))
    if not doc or not doc.exists:
        return await message.answer("❌ Анкета не знайдена")
    data = doc.to_dict()
    if time.time() > data.get("likes_view_until", 0):
        return await message.answer("🔒 Доступ закритий. Запроси друга для Преміум.")
    # ... (логіка показу лайків)
    await message.answer("Поки що ніхто не лайкнув.", reply_markup=get_main_menu())

@dp.message(F.text == "📤 Запросити друга (Преміум 10 хв)")
async def invite_friend(message: types.Message):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    await message.answer(f"Поділись посиланням:\n\n{link}", parse_mode="HTML", reply_markup=get_main_menu())

@dp.message(F.text == "📜 Політика конфіденційності")
async def show_privacy(message: types.Message):
    await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
    await message.answer("🏠 Головне меню", reply_markup=get_main_menu())

# =========================================================
# RUN
# =========================================================
async def main():
    if db:
        asyncio.create_task(internet_watcher())
        asyncio.create_task(firebase_watcher())
        print("🚀 Бот запущено!")
        await dp.start_polling(bot)
    else:
        logging.critical("Firebase не підключено!")

if __name__ == "__main__":
    asyncio.run(main())
