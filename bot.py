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
    "✅ Показуємо анкети за відстанню + віком\n"
    "✅ Преміум через запрошення друга\n\n"
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
    except Exception as e:
        print(f"❌ Помилка firebase_key.json: {e}")

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
        [types.KeyboardButton(text="❌ Так, є оголене тіло / 18+ (відхилити)")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)

# =========================================================
# SAFE FIREBASE & SEND
# =========================================================
async def firebase_get(ref):
    for _ in range(5):
        try:
            return await asyncio.to_thread(ref.get)
        except Exception:
            await asyncio.sleep(2)
    return None

async def firebase_set(ref, data):
    for _ in range(5):
        try:
            await asyncio.to_thread(ref.set, data)
            return True
        except Exception:
            await asyncio.sleep(2)
    return False

async def firebase_delete(ref):
    for _ in range(5):
        try:
            await asyncio.to_thread(ref.delete)
            return True
        except Exception:
            await asyncio.sleep(2)
    return False

async def safe_send_message(chat_id, text, **kwargs):
    for _ in range(5):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception:
            await asyncio.sleep(2)
    return None

async def safe_send_photo(chat_id, photo, caption=None, **kwargs):
    for _ in range(5):
        try:
            return await bot.send_photo(chat_id, photo, caption=caption, **kwargs)
        except Exception:
            await asyncio.sleep(2)
    return None

# =========================================================
# REGISTRATION
# =========================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        if await is_user_banned(user_id):
            return await message.answer("🚫 Твоя анкета заблокована.")
        await message.answer("❤️ З поверненням!", reply_markup=get_main_menu())
        return

    a = random.randint(1, 9)
    b = random.randint(1, 9)
    await state.update_data(captcha_answer=a + b)
    await message.answer(f"🤖 Перевірка що ти не бот\n\n{a} + {b} = ?", reply_markup=get_main_menu_button_only())
    await state.set_state(Registration.captcha)

# ... (всі обробники реєстрації залишаються без змін до process_about)

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
                await safe_send_message(referrer, "🎉 Твій друг завершив реєстрацію! Ти отримав доступ до «Хто мене лайкнув?» на 10 хвилин!")

        await state.clear()
        await message.answer(
            "🎉 Анкету створено!\n\n"
            "📍 Хочеш додати локацію? Надішли локацію або напиши «Пропустити»",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.set_state(Registration.waiting_for_location)
    except Exception as e:
        logging.error(f"About error: {e}")

# (Інші обробники реєстрації — captcha, name, age, country, gender, photo — залишаються без змін)

# =========================================================
# MAIN MENU
# =========================================================
@dp.message(F.text == "🏠 Головне меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        await state.clear()
    await message.answer("🏠 Головне меню", reply_markup=get_main_menu())

@dp.message(F.text == "1. Дивитися анкети 👥")
async def menu_search(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    
    user_id = str(message.from_user.id)
    if await is_user_banned(user_id):
        return await message.answer("🚫 Твоя анкета заблокована.")

    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        data = doc.to_dict() or {}
        if not data.get("disclaimer_seen"):
            await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
            await firebase_set(db.collection("users").document(user_id), {**data, "disclaimer_seen": True})
            await asyncio.sleep(0.8)

    await message.answer("🔍 Шукаю анкети...")
    await send_next_candidate(message, user_id)

# =========================================================
# ALGORITHMIC MATCHING (без фільтрів)
# =========================================================
async def send_next_candidate(message: types.Message, user_id: str):
    try:
        if await is_user_banned(user_id):
            return await message.answer("🚫 Твоя анкета заблокована.")

        my_doc = await firebase_get(db.collection("users").document(user_id))
        if not my_doc or not my_doc.exists:
            return await message.answer("❌ Спочатку створи анкету через /start")

        my = my_doc.to_dict()
        my_age = my.get("age", 25)
        my_lat = my.get("lat")
        my_lon = my.get("lon")

        target_gender = "Я Жінка 👩" if "Дівчину" in my.get("search", "") else "Я Чоловік 👱‍♂️"

        query = db.collection("users").where("gender", "==", target_gender)
        query = query.where("country", "==", my.get("country"))

        docs = await asyncio.to_thread(lambda: query.limit(300).get())

        candidates = [doc.to_dict() for doc in docs if doc.to_dict() and doc.id != user_id and "photo" in doc.to_dict()]

        if not candidates:
            return await message.answer("😔 Анкет у твоїй країні поки немає.")

        seen_docs = await asyncio.to_thread(lambda: db.collection("users").document(user_id).collection("seen").get())
        seen_ids = {doc.id for doc in seen_docs}

        unseen = [c for c in candidates if c.get("tg_id") not in seen_ids]
        if not unseen:
            return await message.answer("😔 Більше немає нових анкет.")

        def sort_key(c):
            dist = calculate_distance(my_lat, my_lon, c.get("lat"), c.get("lon"))
            age_diff = abs(c.get("age", 99) - my_age)
            return (dist, age_diff, random.random())

        unseen.sort(key=sort_key)
        candidate = unseen[0]

        await firebase_set(db.collection("users").document(user_id).collection("seen").document(candidate["tg_id"]), {"ts": firestore.SERVER_TIMESTAMP})

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
            [
                types.InlineKeyboardButton(text="🚫 Поскаржитися", callback_data=f"report_{candidate['tg_id']}"),
            ],
            [
                types.InlineKeyboardButton(text="💤 Завершити", callback_data="stop_search")
            ]
        ])

        await safe_send_photo(message.chat.id, candidate["photo"], caption=text, reply_markup=kb)

    except Exception as e:
        logging.error(f"send_next_candidate error: {e}")
        await message.answer("⚠️ Помилка при пошуку анкет.")

# =========================================================
# Інші хендлери (профіль, лайки, редагування, видалення, репорти) — залишаються
# (Вони не змінювались, тому для економії місця я їх не дублюю тут)

# Повний код з усіма хендлерами (like, report, profile, delete тощо) я можу надіслати, якщо потрібно.

# =========================================================
# RUN
# =========================================================
async def main():
    if db is not None:
        asyncio.create_task(internet_watcher())
        asyncio.create_task(firebase_watcher())
        print("🚀 Бот запущений!")
        await dp.start_polling(bot)
    else:
        logging.critical("Firebase не підключено!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот зупинено.")
