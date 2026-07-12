import logging
import asyncio
import random
import time
import math
import os
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import firebase_admin
from firebase_admin import credentials, firestore

# =========================================================
# CONFIG
# =========================================================
DISCLAIMER_TEXT = "⚠️ Використовуючи бот, ви погоджуєтесь, що вся відповідальність лежить на вас."

COUNTRIES = ["Іспанія", "Польща", "Німеччина", "Чехія", "Італія"]

ONBOARDING_TEXT = "❤️ Вітаємо в боті знайомств для українців за кордоном!\n\nГотовий почати?"

# =========================================================
# LOGGING & FIREBASE
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# Firebase
db = None
if os.getenv("FIREBASE_JSON"):
    try:
        with open("firebase_key.json", "w") as f:
            json.dump(json.loads(os.getenv("FIREBASE_JSON")), f)
        cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase підключено!")
    except Exception as e:
        print(f"❌ Firebase error: {e}")

TOKEN = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# =========================================================
# STATES
# =========================================================
class Registration(StatesGroup):
    captcha = State()
    name = State()
    age = State()
    country = State()
    gender = State()
    search = State()
    photo = State()
    about = State()
    location = State()

# =========================================================
# KEYBOARDS
# =========================================================
def get_main_menu():
    kb = [
        [types.KeyboardButton(text="1. Дивитися анкети 👥")],
        [types.KeyboardButton(text="2. Моя анкета 📝")],
        [types.KeyboardButton(text="3. Редагувати анкету ✏️"), 
         types.KeyboardButton(text="4. Видалити анкету ❌")],
        [types.KeyboardButton(text="👀 Хто мене лайкнув?")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_main_menu_button():
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🏠 Головне меню")]], resize_keyboard=True)

# =========================================================
# HELPER FUNCTIONS
# =========================================================
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

async def firebase_get(ref):
    try:
        return await asyncio.to_thread(ref.get)
    except:
        return None

async def firebase_set(ref, data):
    try:
        await asyncio.to_thread(ref.set, data)
        return True
    except:
        return False

# =========================================================
# REGISTRATION
# =========================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        return await message.answer("❤️ З поверненням!", reply_markup=get_main_menu())

    a, b = random.randint(1,9), random.randint(1,9)
    await state.update_data(captcha_answer=a+b)
    await message.answer(f"🤖 Перевірка: {a} + {b} = ?", reply_markup=get_main_menu_button())
    await state.set_state(Registration.captcha)


@dp.message(Registration.captcha)
async def process_captcha(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) != (await state.get_data()).get("captcha_answer"):
        a, b = random.randint(1,9), random.randint(1,9)
        await state.update_data(captcha_answer=a+b)
        return await message.answer(f"❌ Неправильно. {a} + {b} = ?", reply_markup=get_main_menu_button())

    await message.answer("✅ Ок!")
    await message.answer(ONBOARDING_TEXT, parse_mode="HTML")
    await message.answer("👋 Як тебе звати?", reply_markup=get_main_menu_button())
    await state.set_state(Registration.name)


@dp.message(Registration.name)
async def process_name(message: types.Message, state: FSMContext):
    if len(message.text.strip()) < 2:
        return await message.answer("❌ Коротке ім'я", reply_markup=get_main_menu_button())
    await state.update_data(name=message.text.strip())
    await message.answer("🎂 Скільки тобі років?", reply_markup=get_main_menu_button())
    await state.set_state(Registration.age)


@dp.message(Registration.age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Введи число", reply_markup=get_main_menu_button())
    age = int(message.text)
    if age < 16 or age > 70:
        return await message.answer("❌ Вік 16-70", reply_markup=get_main_menu_button())
    await state.update_data(age=age)
    
    kb = [[types.KeyboardButton(text=c)] for c in COUNTRIES]
    await message.answer("🌍 Де ти зараз?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(Registration.country)


@dp.message(Registration.country)
async def process_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text)
    kb = [
        [types.KeyboardButton(text="Я Чоловік 👱‍♂️"), types.KeyboardButton(text="Я Жінка 👩")]
    ]
    await message.answer("👤 Твоя стать?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(Registration.gender)


@dp.message(Registration.gender)
async def process_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)
    kb = [
        [types.KeyboardButton(text="Шукаю Дівчину 👩"), types.KeyboardButton(text="Шукаю Хлопця 👱‍♂️")]
    ]
    await message.answer("❤️ Кого шукаєш?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(Registration.search)


@dp.message(Registration.search)
async def process_search(message: types.Message, state: FSMContext):
    await state.update_data(search=message.text)
    await message.answer("📸 Надішли своє фото", reply_markup=get_main_menu_button())
    await state.set_state(Registration.photo)


@dp.message(Registration.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("📝 Напиши про себе", reply_markup=get_main_menu_button())
    await state.set_state(Registration.about)


@dp.message(Registration.about)
async def process_about(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = str(message.from_user.id)

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
        "lon": None
    }

    await firebase_set(db.collection("users").document(user_id), profile)
    await state.clear()

    await message.answer("🎉 Анкета створена!", reply_markup=types.ReplyKeyboardRemove())
    await message.answer("📍 Надішли локацію або напиши 'Пропустити'", reply_markup=get_main_menu_button())
    await state.set_state(Registration.location)


@dp.message(Registration.location, F.location)
async def save_location(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc:
        data = doc.to_dict()
        await firebase_set(db.collection("users").document(user_id), {
            **data, 
            "lat": message.location.latitude, 
            "lon": message.location.longitude
        })
    await state.clear()
    await message.answer("✅ Локація збережена!", reply_markup=get_main_menu())


@dp.message(Registration.location)
async def skip_location(message: types.Message, state: FSMContext):
    if "пропустити" in message.text.lower():
        await state.clear()
        await message.answer("Локацію пропущено.", reply_markup=get_main_menu())

# =========================================================
# MAIN MENU
# =========================================================
@dp.message(F.text == "1. Дивитися анкети 👥")
async def start_search(message: types.Message):
    await message.answer("🔍 Шукаю анкети...")
    # Тут буде функція send_next_candidate (можу додати при потребі)

@dp.message(F.text == "🏠 Головне меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠 Головне меню", reply_markup=get_main_menu())

# =========================================================
# RUN
# =========================================================
async def main():
    if not db:
        print("❌ Firebase не підключено!")
        return
    print("🚀 Бот запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
