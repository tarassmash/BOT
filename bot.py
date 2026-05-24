import os
import asyncio
import logging
import random

from aiogram import Bot, Dispatcher, types,
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import firebase_admin
from firebase_admin import credentials, firestore

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# =========================
# FIREBASE INIT
# =========================

try:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("🔥 Firebase connected")
except Exception as e:
    print("❌ Firebase error:", e)
    db = None


# =========================
# STATES
# =========================

class Registration(StatesGroup):
    waiting_for_name = State()
    waiting_for_country = State()
    waiting_for_gender = State()
    waiting_for_search = State()
    waiting_for_about = State()


# =========================
# HELPERS
# =========================

def run_db(func):
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, func)


def safe_dict(doc):
    if not doc:
        return None
    data = doc.to_dict()
    if data is None:
        data = {}
    data["id"] = doc.id
    return data


def main_menu():
    kb = [
        [
            types.KeyboardButton(text="1. Дивитися анкети 👥"),
            types.KeyboardButton(text="4. Оцінили мене ❤️")
        ],
        [
            types.KeyboardButton(text="2. Моя анкета 📝"),
            types.KeyboardButton(text="3. Видалити анкету ❌")
        ]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# =========================
# START
# =========================

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    uid = str(message.from_user.id)

    try:
        doc = await run_db(lambda: db.collection("users").document(uid).get())

        if doc.exists:
            await message.answer("Меню 👇", reply_markup=main_menu())
        else:
            await message.answer("Як тебе звати?")
            await state.set_state(Registration.waiting_for_name)

    except Exception as e:
        logging.error(e)
        await message.answer("Помилка старту")


# =========================
# REGISTRATION FLOW
# =========================

@dp.message(Registration.waiting_for_name)
async def reg_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)

    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Іспанія"), types.KeyboardButton(text="Польща")],
            [types.KeyboardButton(text="Німеччина"), types.KeyboardButton(text="Чехія")]
        ],
        resize_keyboard=True
    )

    await message.answer("Країна?", reply_markup=kb)
    await state.set_state(Registration.waiting_for_country)


@dp.message(Registration.waiting_for_country)
async def reg_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text)

    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Я Чоловік 👱‍♂️"), types.KeyboardButton(text="Я Жінка 👩")]
        ],
        resize_keyboard=True
    )

    await message.answer("Стать?", reply_markup=kb)
    await state.set_state(Registration.waiting_for_gender)


@dp.message(Registration.waiting_for_gender)
async def reg_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)

    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Шукаю Дівчину 👩"), types.KeyboardButton(text="Шукаю Хлопця 👱‍♂️")]
        ],
        resize_keyboard=True
    )

    await message.answer("Кого шукаєш?", reply_markup=kb)
    await state.set_state(Registration.waiting_for_search)


@dp.message(Registration.waiting_for_search)
async def reg_search(message: types.Message, state: FSMContext):
    await state.update_data(search=message.text)
    await message.answer("Напиши про себе:")
    await state.set_state(Registration.waiting_for_about)


@dp.message(Registration.waiting_for_about)
async def reg_about(message: types.Message, state: FSMContext):
    uid = str(message.from_user.id)
    data = await state.get_data()

    profile = {
        "tg_id": uid,
        "username": message.from_user.username or "",
        "name": data["name"],
        "country": data["country"],
        "gender": data["gender"],
        "search": data["search"],
        "about": message.text
    }

    await run_db(lambda: db.collection("users").document(uid).set(profile))

    await state.clear()
    await message.answer("Готово!", reply_markup=main_menu())


# =========================
# FIND CANDIDATES
# =========================

async def next_candidate(message: types.Message, uid: str):
    try:
        my_doc = await run_db(lambda: db.collection("users").document(uid).get())
        if not my_doc.exists:
            return await message.answer("Створи профіль")

        my = my_doc.to_dict()

        target_gender = "Я Жінка 👩" if "Дівчину" in my.get("search", "") else "Я Чоловік 👱‍♂️"

        seen_docs = await run_db(
            lambda: db.collection("users").document(uid).collection("seen").get()
        )
        seen = {d.id for d in seen_docs}
        seen.add(uid)

        all_docs = await run_db(
            lambda: db.collection("users")
            .where("gender", "==", target_gender)
            .where("country", "==", my.get("country"))
            .stream()
        )

        candidates = []
        for d in all_docs:
            if d.id not in seen:
                data = d.to_dict()
                data["id"] = d.id
                candidates.append(data)

        if not candidates:
            return await message.answer("Нема анкет")

        c = random.choice(candidates)

        text = f"✨ {c.get('name')}\n{c.get('about')}"

        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="👍", callback_data=f"like_{c['id']}"),
            types.InlineKeyboardButton(text="👎", callback_data=f"dislike_{c['id']}"),
            types.InlineKeyboardButton(text="💤", callback_data="stop")
        ]])

        await message.answer(text, reply_markup=kb)

    except Exception as e:
        logging.error(e)


# =========================
# LIKERS
# =========================

async def next_liker(message: types.Message, uid: str):
    liked = await run_db(
        lambda: db.collection("users").document(uid).collection("liked_by").get()
    )
    seen = await run_db(
        lambda: db.collection("users").document(uid).collection("seen").get()
    )

    liked_ids = {d.id for d in liked}
    seen_ids = {d.id for d in seen}

    candidates = list(liked_ids - seen_ids)

    if not candidates:
        return await message.answer("Нема лайків")

    liker_id = candidates[0]

    doc = await run_db(lambda: db.collection("users").document(liker_id).get())
    if not doc.exists:
        return

    c = safe_dict(doc)

    text = f"🔥 ТЕБЕ ЛАЙКНУЛИ\n{c.get('name')}\n{c.get('about')}"

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="👍", callback_data=f"like_{c['id']}"),
        types.InlineKeyboardButton(text="👎", callback_data=f"dislike_{c['id']}"),
        types.InlineKeyboardButton(text="💤", callback_data="stop")
    ]])

    await message.answer(text, reply_markup=kb)


# =========================
# MENU
# =========================

@dp.message(F.text == "1. Дивитися анкети 👥")
async def m1(message: types.Message):
    await next_candidate(message, str(message.from_user.id))


@dp.message(F.text == "2. Моя анкета 📝")
async def m2(message: types.Message):
    uid = str(message.from_user.id)

    doc = await run_db(lambda: db.collection("users").document(uid).get())
    if not doc.exists:
        return await message.answer("Нема профілю")

    p = doc.to_dict()

    await message.answer(
        f"{p['name']}\n{p['country']}\n{p['about']}",
        reply_markup=main_menu()
    )


@dp.message(F.text == "3. Видалити анкету ❌")
async def m3(message: types.Message):
    uid = str(message.from_user.id)
    await run_db(lambda: db.collection("users").document(uid).delete())
    await message.answer("Видалено")


@dp.message(F.text == "4. Оцінили мене ❤️")
async def m4(message: types.Message):
    await next_liker(message, str(message.from_user.id))


# =========================
# CALLBACKS
# =========================

@dp.callback_query(F.data.startswith("like_"))
async def like(cq: types.CallbackQuery):
    uid = str(cq.from_user.id)
    target = cq.data.split("_")[1]

    await run_db(lambda: db.collection("users").document(uid).collection("likes").document(target).set({}))
    await run_db(lambda: db.collection("users").document(target).collection("liked_by").document(uid).set({}))
    await run_db(lambda: db.collection("users").document(uid).collection("seen").document(target).set({}))

    await cq.answer()
    await cq.message.delete()
    await next_candidate(cq.message, uid)


@dp.callback_query(F.data.startswith("dislike_"))
async def dislike(cq: types.CallbackQuery):
    uid = str(cq.from_user.id)
    target = cq.data.split("_")[1]

    await run_db(lambda: db.collection("users").document(uid).collection("seen").document(target).set({}))

    await cq.answer()
    await cq.message.delete()
    await next_candidate(cq.message, uid)


@dp.callback_query(F.data == "stop")
async def stop(cq: types.CallbackQuery):
    await cq.answer()
    await cq.message.delete()
    await cq.message.answer("Стоп", reply_markup=main_menu())


# =========================
# RUN
# =========================

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())