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
DISCLAIMER_TEXT = (
    "⚠️ <b>ЗНЯТТЯ ВІДПОВІДАЛЬНОСТІ</b>\n\n"
    "Використовуючи цей бот, ви повністю погоджуєтесь, що вся відповідальність за спілкування та зустрічі лежить на вас.\n\n"
    "18+ контент суворо заборонено."
)

COUNTRIES = ["Іспанія", "Польща", "Німеччина", "Чехія", "Італія"]

ONBOARDING_TEXT = (
    "❤️ <b>Вітаємо в найкращому боті для українців за кордоном!</b>\n\n"
    "Прості знайомства • Реальні люди • Безпека на першому місці\n\n"
    "Готовий почати?"
)

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# =========================================================
# FIREBASE
# =========================================================
db = None
firebase_json = os.getenv("FIREBASE_JSON")
if firebase_json:
    try:
        with open("firebase_key.json", "w") as f:
            json.dump(json.loads(firebase_json), f)
        cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase підключено успішно!")
    except Exception as e:
        print(f"❌ Firebase error: {e}")

TOKEN = os.getenv("BOT_TOKEN")
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
    return types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="1. Дивитися анкети 👥")],
        [types.KeyboardButton(text="2. Моя анкета 📝")],
        [types.KeyboardButton(text="3. Редагувати анкету ✏️"), types.KeyboardButton(text="4. Видалити анкету ❌")],
        [types.KeyboardButton(text="👀 Хто мене лайкнув?"), types.KeyboardButton(text="📤 Запросити друга")]
    ], resize_keyboard=True)

def get_main_menu_btn():
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🏠 Головне меню")]], resize_keyboard=True)

# =========================================================
# HELPERS
# =========================================================
def calculate_distance(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return 99999
    R = 6371.0
    a = math.sin(math.radians(lat2 - lat1) / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(math.radians(lon2 - lon1) / 2)**2
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
# REGISTRATION (ПОВНА)
# =========================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        return await message.answer("❤️ З поверненням!", reply_markup=get_main_menu())

    a, b = random.randint(1,9), random.randint(1,9)
    await state.update_data(captcha_answer=a + b)
    await message.answer(f"🤖 Перевірка: {a} + {b} = ?", reply_markup=get_main_menu_btn())
    await state.set_state(Registration.captcha)

# === Всі кроки реєстрації ===
@dp.message(Registration.captcha)
async def reg_captcha(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not message.text.isdigit() or int(message.text) != data.get("captcha_answer"):
        a, b = random.randint(1,9), random.randint(1,9)
        await state.update_data(captcha_answer=a+b)
        return await message.answer(f"❌ Неправильно. {a} + {b} = ?", reply_markup=get_main_menu_btn())

    await message.answer("✅ Перевірено!")
    await message.answer(ONBOARDING_TEXT, parse_mode="HTML")
    await message.answer("👋 Як тебе звати?", reply_markup=get_main_menu_btn())
    await state.set_state(Registration.name)

@dp.message(Registration.name)
async def reg_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        return await message.answer("❌ Занадто коротке ім'я", reply_markup=get_main_menu_btn())
    await state.update_data(name=name)
    await message.answer("🎂 Скільки тобі років?", reply_markup=get_main_menu_btn())
    await state.set_state(Registration.age)

@dp.message(Registration.age)
async def reg_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Введи число", reply_markup=get_main_menu_btn())
    age = int(message.text)
    if age < 16 or age > 70:
        return await message.answer("❌ Вік від 16 до 70", reply_markup=get_main_menu_btn())
    await state.update_data(age=age)
    kb = [[types.KeyboardButton(text=c)] for c in COUNTRIES]
    await message.answer("🌍 В якій країні ти зараз?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(Registration.country)

@dp.message(Registration.country)
async def reg_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text)
    kb = [[types.KeyboardButton(text="Я Чоловік 👱‍♂️"), types.KeyboardButton(text="Я Жінка 👩")]]
    await message.answer("👤 Твоя стать?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(Registration.gender)

@dp.message(Registration.gender)
async def reg_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)
    kb = [[types.KeyboardButton(text="Шукаю Дівчину 👩"), types.KeyboardButton(text="Шукаю Хлопця 👱‍♂️")]]
    await message.answer("❤️ Кого шукаєш?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(Registration.search)

@dp.message(Registration.search)
async def reg_search(message: types.Message, state: FSMContext):
    await state.update_data(search=message.text)
    await message.answer("📸 Надішли своє реальне фото", reply_markup=get_main_menu_btn())
    await state.set_state(Registration.photo)

@dp.message(Registration.photo, F.photo)
async def reg_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("📝 Напиши коротко про себе", reply_markup=get_main_menu_btn())
    await state.set_state(Registration.about)

@dp.message(Registration.about)
async def reg_about(message: types.Message, state: FSMContext):
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
        "lon": None,
        "disclaimer_seen": True
    }

    await firebase_set(db.collection("users").document(user_id), profile)
    await state.clear()

    await message.answer("🎉 Анкета успішно створена!", reply_markup=types.ReplyKeyboardRemove())
    await message.answer("📍 Надішли локацію або напиши «Пропустити»", reply_markup=get_main_menu_btn())
    await state.set_state(Registration.location)

@dp.message(Registration.location, F.location)
async def reg_location(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc:
        data = doc.to_dict()
        await firebase_set(db.collection("users").document(user_id), {
            **data, "lat": message.location.latitude, "lon": message.location.longitude
        })
    await state.clear()
    await message.answer("✅ Локація збережена!", reply_markup=get_main_menu())

@dp.message(Registration.location)
async def reg_skip_location(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Локацію пропущено.", reply_markup=get_main_menu())

# =========================================================
# ПОШУК АНКЕТ
# =========================================================
async def send_next_candidate(message: types.Message, user_id: str):
    my_doc = await firebase_get(db.collection("users").document(user_id))
    if not my_doc:
        return await message.answer("❌ Анкета не знайдена")

    my = my_doc.to_dict()
    target_gender = "Я Жінка 👩" if "Дівчину" in my.get("search", "") else "Я Чоловік 👱‍♂️"

    query = db.collection("users").where("gender", "==", target_gender).where("country", "==", my.get("country"))
    docs = await asyncio.to_thread(lambda: query.limit(150).get())

    candidates = [d.to_dict() for d in docs if d.id != user_id and "photo" in d.to_dict()]

    if not candidates:
        return await message.answer("😔 Анкет у твоїй країні поки немає.")

    def sort_key(c):
        dist = calculate_distance(my.get("lat"), my.get("lon"), c.get("lat"), c.get("lon"))
        return (dist, random.random())

    candidates.sort(key=sort_key)
    candidate = candidates[0]

    await firebase_set(db.collection("users").document(user_id).collection("seen").document(candidate["tg_id"]), {"ts": firestore.SERVER_TIMESTAMP})

    dist_text = ""
    if my.get("lat") and candidate.get("lat"):
        dist = calculate_distance(my.get("lat"), my.get("lon"), candidate.get("lat"), candidate.get("lon"))
        if dist < 999:
            dist_text = f"📍 ~{int(dist)} км\n"

    text = f"👤 {candidate['name']}, {candidate['age']}\n🌍 {candidate['country']}\n{dist_text}📝 {candidate['about']}"

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="👍 Лайк", callback_data=f"like_{candidate['tg_id']}"),
         types.InlineKeyboardButton(text="👎 Далі", callback_data="dislike")],
        [types.InlineKeyboardButton(text="💤 Завершити", callback_data="stop_search")]
    ])

    await bot.send_photo(message.chat.id, candidate["photo"], caption=text, reply_markup=kb)

@dp.message(F.text == "1. Дивитися анкети 👥")
async def start_search(message: types.Message):
    await message.answer("🔍 Шукаю анкети...")
    await send_next_candidate(message, str(message.from_user.id))

@dp.callback_query(F.data == "dislike")
async def dislike(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    await send_next_candidate(callback.message, str(callback.from_user.id))

@dp.callback_query(F.data.startswith("like_"))
async def like(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    my_id = str(callback.from_user.id)
    target_id = callback.data.split("_")[1]

    await firebase_set(db.collection("users").document(my_id).collection("likes").document(target_id), {"ts": firestore.SERVER_TIMESTAMP})

    # Перевірка метчу
    if await firebase_get(db.collection("users").document(target_id).collection("likes").document(my_id)):
        await bot.send_message(my_id, "🎉 <b>МЕТЧ!</b> 🎉", parse_mode="HTML")
        await bot.send_message(target_id, "🎉 <b>МЕТЧ!</b> 🎉", parse_mode="HTML")

    await send_next_candidate(callback.message, my_id)

@dp.callback_query(F.data == "stop_search")
async def stop(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer("💤 Пошук завершено", reply_markup=get_main_menu())

# =========================================================
# RUN
# =========================================================
async def main():
    if not db:
        print("❌ Firebase не підключено!")
        return
    print("🚀 Бот запущено в топовому режимі!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
