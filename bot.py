import logging
import asyncio
import random
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

# ==================== CONFIG ====================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# Firebase
firebase_json_raw = os.getenv("FIREBASE_JSON")
if firebase_json_raw:
    with open("firebase_key.json", "w") as f:
        json.dump(json.loads(firebase_json_raw), f)

try:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase подключен")
except Exception as e:
    print(f"❌ Firebase error: {e}")
    db = None

TOKEN = os.getenv("BOT_TOKEN", "8731550935:AAF_XmQNZjBmtnhtQ-cIJ3gFvYswg-eDiZs")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ==================== STATES ====================
class Reg(StatesGroup):
    captcha = State()
    name = State()
    age = State()
    gender = State()
    looking_for = State()
    photo = State()
    about = State()

# ==================== KEYBOARDS ====================
def main_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="👀 Смотреть анкеты")],
            [types.KeyboardButton(text="👤 Моя анкета")]
        ],
        resize_keyboard=True
    )

def gender_kb():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="👨 Я парень")],
            [types.KeyboardButton(text="👩 Я девушка")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def looking_for_kb():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="👩 Ищу девушку")],
            [types.KeyboardButton(text="👨 Ищу парня")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def like_dislike_kb(target_id: str):
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="❤️ Лайк", callback_data=f"like_{target_id}"),
            types.InlineKeyboardButton(text="👎 Далее", callback_data="next")
        ]
    ])

# ==================== HELPERS ====================
async def get_user(user_id: str):
    doc = await asyncio.to_thread(db.collection("users").document(user_id).get)
    return doc.to_dict() if doc.exists else None

async def save_user(user_id: str, data: dict):
    await asyncio.to_thread(db.collection("users").document(user_id).set, data, merge=True)

async def has_liked(me: str, them: str) -> bool:
    doc = await asyncio.to_thread(
        db.collection("users").document(me).collection("likes").document(them).get
    )
    return doc.exists

async def mark_seen(me: str, them: str):
    await asyncio.to_thread(
        db.collection("users").document(me).collection("seen").document(them).set({"ts": firestore.SERVER_TIMESTAMP})
    )

async def get_unseen_profiles(my_id: str, my_gender: str, looking_for: str):
    """Простой поиск: все подходящие по полу, кроме себя и уже просмотренных"""
    target_gender = "👩 Я девушка" if "девушку" in looking_for else "👨 Я парень"
    
    docs = await asyncio.to_thread(
        db.collection("users")
        .where("gender", "==", target_gender)
        .limit(200)
        .get
    )
    
    seen = await asyncio.to_thread(
        db.collection("users").document(my_id).collection("seen").get
    )
    seen_ids = {d.id for d in seen}
    
    result = []
    for d in docs:
        if d.id != my_id and d.id not in seen_ids:
            data = d.to_dict()
            if data and "photo" in data:
                result.append(data)
    
    random.shuffle(result)   # простое перемешивание
    return result

# ==================== REGISTRATION ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    user = await get_user(user_id)
    
    if user:
        await message.answer("С возвращением! ❤️", reply_markup=main_menu())
        return
    
    # Простая капча
    a, b = random.randint(5, 30), random.randint(5, 30)
    await state.update_data(captcha=a + b)
    await message.answer(
        f"🤖 Проверка (защита от ботов)\n\n"
        f"<b>{a} + {b} = ?</b>\n\n"
        "Введи ответ:",
        parse_mode="HTML",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(Reg.captcha)

@dp.message(Reg.captcha)
async def process_captcha(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if message.text.isdigit() and int(message.text) == data.get("captcha"):
        await message.answer("✅ Хорошо! Как тебя зовут?")
        await state.set_state(Reg.name)
    else:
        a, b = random.randint(5, 30), random.randint(5, 30)
        await state.update_data(captcha=a + b)
        await message.answer(f"❌ Неправильно. Попробуй ещё:\n\n<b>{a} + {b} = ?</b>")

@dp.message(Reg.name)
async def process_name(message: types.Message, state: FSMContext):
    if len(message.text.strip()) < 2:
        return await message.answer("Имя слишком короткое. Введи ещё раз.")
    await state.update_data(name=message.text.strip())
    await message.answer("Сколько тебе лет?")
    await state.set_state(Reg.age)

@dp.message(Reg.age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введи возраст цифрами.")
    age = int(message.text)
    if age < 16 or age > 65:
        return await message.answer("Возраст от 16 до 65 лет.")
    await state.update_data(age=age)
    await message.answer("Ты парень или девушка?", reply_markup=gender_kb())
    await state.set_state(Reg.gender)

@dp.message(Reg.gender)
async def process_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)
    await message.answer("Кого ты ищешь?", reply_markup=looking_for_kb())
    await state.set_state(Reg.looking_for)

@dp.message(Reg.looking_for)
async def process_looking_for(message: types.Message, state: FSMContext):
    await state.update_data(looking_for=message.text)
    await message.answer(
        "📸 Отправь своё фото.\n\n"
        "⚠️ Запрещено: обнажёнка, 18+ контент.\n"
        "Просто обычное фото в одежде."
    )
    await state.set_state(Reg.photo)

@dp.message(Reg.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("Расскажи немного о себе (2-4 предложения):")
    await state.set_state(Reg.about)

@dp.message(Reg.about)
async def process_about(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = str(message.from_user.id)
    
    profile = {
        "tg_id": user_id,
        "username": message.from_user.username or "",
        "name": data["name"],
        "age": data["age"],
        "gender": data["gender"],
        "looking_for": data["looking_for"],
        "photo": data["photo"],
        "about": message.text,
        "created_at": firestore.SERVER_TIMESTAMP
    }
    
    await save_user(user_id, profile)
    await state.clear()
    
    await message.answer(
        "🎉 Анкета создана!\n\n"
        "Теперь ты можешь смотреть других людей.",
        reply_markup=main_menu()
    )

# ==================== MAIN MENU ====================
@dp.message(F.text == "👀 Смотреть анкеты")
async def show_profiles(message: types.Message):
    user_id = str(message.from_user.id)
    me = await get_user(user_id)
    if not me:
        return await message.answer("Сначала создай анкету через /start")
    
    profiles = await get_unseen_profiles(user_id, me["gender"], me["looking_for"])
    
    if not profiles:
        return await message.answer("Пока нет новых анкет. Загляни позже!")
    
    candidate = profiles[0]
    await mark_seen(user_id, candidate["tg_id"])
    
    text = f"👤 {candidate['name']}, {candidate['age']}\n\n{candidate['about']}"
    
    await message.answer_photo(
        photo=candidate["photo"],
        caption=text,
        reply_markup=like_dislike_kb(candidate["tg_id"])
    )

@dp.message(F.text == "👤 Моя анкета")
async def my_profile(message: types.Message):
    user_id = str(message.from_user.id)
    me = await get_user(user_id)
    if not me:
        return await message.answer("У тебя ещё нет анкеты. Нажми /start")
    
    text = f"👤 {me['name']}, {me['age']}\n{me['gender']}\nИщу: {me['looking_for']}\n\n{me['about']}"
    await message.answer_photo(photo=me["photo"], caption=text)

# ==================== LIKE / NEXT ====================
@dp.callback_query(F.data.startswith("like_"))
async def handle_like(callback: types.CallbackQuery):
    await callback.answer()
    
    my_id = str(callback.from_user.id)
    target_id = callback.data.split("_")[1]
    
    # Сохраняем лайк
    await asyncio.to_thread(
        db.collection("users").document(my_id).collection("likes").document(target_id).set(
            {"ts": firestore.SERVER_TIMESTAMP}
        )
    )
    
    # Проверяем взаимный лайк
    if await has_liked(target_id, my_id):
        me = await get_user(my_id)
        them = await get_user(target_id)
        if me and them:
            await bot.send_message(my_id, f"🎉 Взаимный лайк! Пиши @{them.get('username', 'пользователю')}")
            await bot.send_message(target_id, f"🎉 Взаимный лайк! Пиши @{me.get('username', 'пользователю')}")
    
    # Показываем следующую анкету
    await show_next_profile(callback)

@dp.callback_query(F.data == "next")
async def handle_next(callback: types.CallbackQuery):
    await callback.answer()
    await show_next_profile(callback)

async def show_next_profile(callback: types.CallbackQuery):
    my_id = str(callback.from_user.id)
    me = await get_user(my_id)
    if not me:
        await callback.message.delete()
        return
    
    profiles = await get_unseen_profiles(my_id, me["gender"], me["looking_for"])
    
    if not profiles:
        await callback.message.edit_caption("Анкеты закончились. Загляни позже!")
        return
    
    candidate = profiles[0]
    await mark_seen(my_id, candidate["tg_id"])
    
    text = f"👤 {candidate['name']}, {candidate['age']}\n\n{candidate['about']}"
    
    try:
        await callback.message.edit_media(
            media=types.InputMediaPhoto(media=candidate["photo"], caption=text),
            reply_markup=like_dislike_kb(candidate["tg_id"])
        )
    except:
        # Если не получилось отредактировать — удаляем и отправляем новое
        await callback.message.delete()
        await callback.message.answer_photo(
            photo=candidate["photo"],
            caption=text,
            reply_markup=like_dislike_kb(candidate["tg_id"])
        )

# ==================== RUN ====================
async def main():
    if db:
        print("🚀 Простой бот запущен")
        await dp.start_polling(bot)
    else:
        print("❌ Нет Firebase — бот не запустится")

if __name__ == "__main__":
    asyncio.run(main())
