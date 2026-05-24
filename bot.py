import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

import firebase_admin
from firebase_admin import credentials, firestore

# ======================
# ENV
# ======================
TOKEN = os.getenv("BOT_TOKEN")

# Firebase init
cred = credentials.Certificate("serviceAccount.json")
firebase_admin.initialize_app(cred)

db = firestore.client()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ======================
# START COMMAND
# ======================
@dp.message(Command("start"))
async def start(message: types.Message):
    user = message.from_user

    # save to firebase
    db.collection("users").document(str(user.id)).set({
        "username": user.username,
        "first_name": user.first_name
    })

    await message.answer("Бот работает 🚀 Тебя добавили в Firebase!")

# ======================
# ECHO
# ======================
@dp.message()
async def echo(message: types.Message):
    await message.answer(f"Ты написал: {message.text}")

# ======================
# MAIN
# ======================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
