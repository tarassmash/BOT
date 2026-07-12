import logging
import asyncio
import random
import string
import time
import traceback
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramForbiddenError
)
import firebase_admin
from firebase_admin import credentials, firestore
# =========================================================
# DISCLAIMER
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
    "Продовжуючи — ви підтверджуєте згоду."
)
# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
# =========================================================
# FIREBASE
# =========================================================
try:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase підключено!")
except Exception as e:
    print(f"❌ Firebase error: {e}")
    db = None
# =========================================================
# BOT
# =========================================================
TOKEN = "8731550935:AAF_XmQNZjBmtnhtQ-cIJ3gFvYswg-eDiZs"
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
    waiting_for_about = State()
# =========================================================
# MENU
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
# =========================================================
# SAFE FIREBASE
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
# SAFE SEND
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
            await asyncio.to_thread(
                db.collection("system").document("ping").set,
                {"time": firestore.SERVER_TIMESTAMP}
            )
            logging.info("🔥 Firebase OK")
        except Exception as e:
            logging.error(f"Firebase dead: {e}")
        await asyncio.sleep(60)
# =========================================================
# START
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
        if doc and doc.exists:
            await message.answer("❤️ З поверненням!", reply_markup=get_main_menu())
            return
        if referrer:
            await state.update_data(referrer=referrer)
        a = random.randint(1, 9)
        b = random.randint(1, 9)
        await state.update_data(captcha_answer=a + b)
        await message.answer(f"🤖 Перевірка що ти не бот\n\n{a} + {b} = ?")
        await state.set_state(Registration.captcha)
    except Exception as e:
        logging.error(f"Start error: {e}")
# =========================================================
# REGISTRATION
# =========================================================
@dp.message(Registration.captcha)
async def process_captcha(message: types.Message, state: FSMContext):
    try:
        if not message.text.isdigit():
            return await message.answer("❌ Введи число")
        data = await state.get_data()
        if int(message.text) != data["captcha_answer"]:
            a = random.randint(1, 9)
            b = random.randint(1, 9)
            await state.update_data(captcha_answer=a + b)
            return await message.answer(f"❌ Неправильно\n\n{a} + {b} = ?")
        await message.answer("✅ Перевірку пройдено!")
        await asyncio.sleep(1)
        await message.answer("👋 Як тебе звати?")
        await state.set_state(Registration.waiting_for_name)
    except Exception as e:
        logging.error(f"Captcha error: {e}")
@dp.message(Registration.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    try:
        name = message.text.strip()
        if len(name) < 2:
            return await message.answer("❌ Коротке ім'я")
        await state.update_data(name=name)
        await message.answer("🎂 Скільки тобі років?")
        await state.set_state(Registration.waiting_for_age)
    except Exception as e:
        logging.error(f"Name error: {e}")
@dp.message(Registration.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        if not message.text.isdigit():
            return await message.answer("❌ Введи число")
        age = int(message.text)
        if age < 16 or age > 70:
            return await message.answer("❌ Вік 16-70")
        await state.update_data(age=age)
        kb = [[types.KeyboardButton(text=c)] for c in ["Іспанія","Польща","Німеччина","Чехія","Італія"]]
        await message.answer(
            "🌍 Де ти зараз?",
            reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)
        )
        await state.set_state(Registration.waiting_for_country)
    except Exception as e:
        logging.error(f"Age error: {e}")
@dp.message(Registration.waiting_for_country)
async def process_country(message: types.Message, state: FSMContext):
    try:
        await state.update_data(country=message.text)
        kb = [[types.KeyboardButton(text="Я Чоловік 👱‍♂️"), types.KeyboardButton(text="Я Жінка 👩")]]
        await message.answer(
            "👤 Вкажи стать",
            reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)
        )
        await state.set_state(Registration.waiting_for_gender)
    except Exception as e:
        logging.error(f"Country error: {e}")
@dp.message(Registration.waiting_for_gender)
async def process_gender(message: types.Message, state: FSMContext):
    try:
        await state.update_data(gender=message.text)
        kb = [[types.KeyboardButton(text="Шукаю Дівчину 👩"), types.KeyboardButton(text="Шукаю Хлопця 👱‍♂️")]]
        await message.answer(
            "❤️ Кого шукаєш?",
            reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)
        )
        await state.set_state(Registration.waiting_for_search)
    except Exception as e:
        logging.error(f"Gender error: {e}")
@dp.message(Registration.waiting_for_search)
async def process_search(message: types.Message, state: FSMContext):
    try:
        await state.update_data(search=message.text)
        await message.answer(
            "📸 <b>Надішли своє реальне фото</b>\n\n"
            "📎 <b>Як завантажити фото:</b>\n"
            "1. Натисни на <b>скріпку 📎</b> внизу екрану\n"
            "2. Обери фото з галереї або зроби нове\n"
            "3. Надішли його боту\n\n"
            "❗️ Це <b>обов’язковий</b> крок! Без фото ти не зможеш користуватися ботом.",
            parse_mode="HTML"
        )
        await state.set_state(Registration.waiting_for_photo)
    except Exception as e:
        logging.error(f"Search error: {e}")
@dp.message(Registration.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    try:
        await state.update_data(photo=message.photo[-1].file_id)
        await message.answer("📝 <b>Напиши трохи про себе</b>\n\nЦе останній крок реєстрації.")
        await state.set_state(Registration.waiting_for_about)
    except Exception as e:
        logging.error(f"Photo error: {e}")
@dp.message(Registration.waiting_for_photo)
async def photo_error(message: types.Message):
    await message.answer(
        "❌ <b>Фото обов’язкове!</b>\n\n"
        "📎 Натисни на <b>скріпку 📎</b> внизу → обери фото."
    )
@dp.message(Registration.waiting_for_about)
async def process_about(message: types.Message, state: FSMContext):
    try:
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
            "registered_at": firestore.SERVER_TIMESTAMP
        }
        await firebase_set(db.collection("users").document(user_id), profile)
        # Активація преміуму для того, хто запросив
        if referrer:
            unlock_time = int(time.time()) + 600 # 10 хвилин
            ref_doc = await firebase_get(db.collection("users").document(referrer))
            if ref_doc and ref_doc.exists:
                ref_data = ref_doc.to_dict() or {}
                await firebase_set(
                    db.collection("users").document(referrer),
                    {**ref_data, "likes_view_until": unlock_time}
                )
                await safe_send_message(
                    referrer,
                    "🎉 Твій друг завершив реєстрацію!\nТи отримав доступ до «Хто мене лайкнув?» на 10 хвилин!"
                )
        await state.clear()
        await message.answer("🎉 Анкету створено!", reply_markup=get_main_menu())
    except Exception as e:
        logging.error(f"About error: {e}")
# =========================================================
# SEND NEXT CANDIDATE
# =========================================================
async def send_next_candidate(message: types.Message, user_id: str):
    try:
        my_doc = await firebase_get(db.collection("users").document(user_id))
        if not my_doc or not my_doc.exists:
            return await message.answer("❌ Спочатку створи анкету через /start")
        my = my_doc.to_dict()
        target_gender = "Я Жінка 👩" if "Дівчину" in my.get("search", "") else "Я Чоловік 👱‍♂️"
        docs = await asyncio.to_thread(
            lambda: db.collection("users")
            .where("gender", "==", target_gender)
            .where("country", "==", my["country"])
            .limit(100)
            .get()
        )
        candidates = []
        for doc in docs:
            data = doc.to_dict()
            if data and doc.id != user_id and "photo" in data:
                candidates.append(data)
        if not candidates:
            return await message.answer("😔 Анкет немає")
        seen_docs = await asyncio.to_thread(
            lambda: db.collection("users").document(user_id).collection("seen").get()
        )
        seen_ids = {doc.id for doc in seen_docs}
        available = [c for c in candidates if c["tg_id"] not in seen_ids]
        if not available:
            available = candidates
        candidate = random.choice(available)
        await firebase_set(
            db.collection("users").document(user_id).collection("seen").document(candidate["tg_id"]),
            {"ts": firestore.SERVER_TIMESTAMP}
        )
        text = (
            f"👤 {candidate['name']}, {candidate['age']}\n"
            f"🌍 {candidate['country']}\n\n"
            f"📝 {candidate['about']}"
        )
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="👍 Лайк", callback_data=f"like_{candidate['tg_id']}"),
                types.InlineKeyboardButton(text="👎 Далі", callback_data="dislike")
            ],
            [types.InlineKeyboardButton(text="💤 Завершити", callback_data="stop_search")]
        ])
        await safe_send_photo(message.chat.id, candidate["photo"], caption=text, reply_markup=kb)
    except Exception as e:
        logging.error(f"CRITICAL send_next_candidate error:\n{traceback.format_exc()}")
        await message.answer("⚠️ Помилка при пошуку анкет. Спробуй пізніше.")
# =========================================================
# MENU HANDLERS
# =========================================================
@dp.message(F.text == "1. Дивитися анкети 👥")
async def menu_search(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
   
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if doc and doc.exists:
        data = doc.to_dict() or {}
        if not data.get("disclaimer_seen"):
            await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
            await firebase_set(db.collection("users").document(user_id), {**data, "disclaimer_seen": True})
            await asyncio.sleep(0.8)
    await message.answer("🔍 Шукаю анкети...")
    await send_next_candidate(message, user_id)
@dp.message(F.text == "2. Моя анкета 📝")
async def menu_profile(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    try:
        user_id = str(message.from_user.id)
        doc = await firebase_get(db.collection("users").document(user_id))
        if not doc or not doc.exists:
            return await message.answer("❌ Анкета не знайдена")
        p = doc.to_dict()
        text = f"👤 {p['name']}, {p['age']}\n🌍 {p['country']}\n\n📝 {p['about']}"
        await safe_send_photo(message.chat.id, p["photo"], caption=text)
    except Exception as e:
        logging.error(f"My profile error: {e}")
@dp.message(F.text == "3. Редагувати анкету ✏️")
async def menu_edit(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    await message.answer("✏️ Видали стару анкету і створи нову через /start")
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
        await message.answer(
            "⚠️ <b>УВАГА! ВИДАЛЕННЯ АНКЕТИ</b>\n\n"
            "Ви дійсно хочете <b>назавжди видалити</b> свою анкету?\n\n"
            "• Всі ваші дані, фото та опис будуть видалені\n"
            "• Інформація про лайки, перегляди та матчі зникне\n"
            "• Цю дію <b>НЕМОЖЛИВО скасувати</b>\n\n"
            "Якщо ви впевнені — натисніть кнопку нижче.",
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Delete error: {e}")
@dp.message(F.text == "👀 Хто мене лайкнув?")
async def show_who_liked_me(message: types.Message):
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    if not doc or not doc.exists:
        return await message.answer("❌ Спочатку створи анкету через /start")
    data = doc.to_dict() or {}
    if time.time() > data.get("likes_view_until", 0):
        return await message.answer(
            "🔒 Доступ до перегляду лайків закритий.\n\n"
            "Натисни «📤 Запросити друга (Преміум 10 хв)», щоб отримати 10 хвилин."
        )
    likes_docs = await asyncio.to_thread(
        lambda: db.collection("users").document(user_id).collection("likes").get()
    )
    if not likes_docs:
        return await message.answer("😔 Поки що тебе ніхто не лайкнув.")
    text = "❤️ Тебе лайкнули:\n\n"
    for like_doc in likes_docs:
        try:
            liker_id = like_doc.id
            liker_doc = await firebase_get(db.collection("users").document(liker_id))
            if liker_doc and liker_doc.exists:
                l = liker_doc.to_dict()
                text += f"👤 {l.get('name')}, {l.get('age')} — @{l.get('username','')}\n"
        except:
            pass
    await message.answer(text)
@dp.message(F.text == "📤 Запросити друга (Преміум 10 хв)")
async def invite_friend(message: types.Message):
    bot_info = await bot.get_me()
    user_id = str(message.from_user.id)
    link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    await message.answer(
        f"📤 <b>Поділись цим посиланням з другом</b>\n\n"
        f"<code>{link}</code>\n\n"
        "Як тільки друг зареєструється і завершить анкету — ти отримаєш 10 хвилин доступу до «Хто мене лайкнув?»",
        parse_mode="HTML"
    )
@dp.message(F.text == "📜 Політика конфіденційності")
async def show_privacy_policy(message: types.Message):
    await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
# =========================================================
# CALLBACKS
# =========================================================
@dp.callback_query(F.data.startswith("like_"))
async def handle_like(callback: types.CallbackQuery):
    try:
        my_id = str(callback.from_user.id)
        target_id = callback.data.split("_")[1]
        await firebase_set(
            db.collection("users").document(my_id).collection("likes").document(target_id),
            {"ts": firestore.SERVER_TIMESTAMP}
        )
        reverse = await firebase_get(
            db.collection("users").document(target_id).collection("likes").document(my_id)
        )
        if reverse and reverse.exists:
            me_doc = await firebase_get(db.collection("users").document(my_id))
            them_doc = await firebase_get(db.collection("users").document(target_id))
            me = me_doc.to_dict() if me_doc else {}
            them = them_doc.to_dict() if them_doc else {}
            await safe_send_message(my_id, f"🎉 МЕТЧ!\nПиши @{them.get('username', '')}")
            await safe_send_message(target_id, f"🎉 МЕТЧ!\nПиши @{me.get('username', '')}")
       
        try:
            await callback.message.delete()
        except:
            pass
        await send_next_candidate(callback.message, my_id)
    except Exception as e:
        logging.error(f"Like error: {e}")
@dp.callback_query(F.data == "dislike")
async def handle_dislike(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    await send_next_candidate(callback.message, str(callback.from_user.id))
@dp.callback_query(F.data == "stop_search")
async def handle_stop(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer("🛑 Пошук зупинено", reply_markup=get_main_menu())

# =========================================================
# DELETE CONFIRMATION (додано попередження)
# =========================================================
@dp.callback_query(F.data == "confirm_delete")
async def confirm_delete_account(callback: types.CallbackQuery):
    try:
        user_id = str(callback.from_user.id)
        ref = db.collection("users").document(user_id)
        await firebase_delete(ref)
        
        try:
            await callback.message.delete()
        except:
            pass
        
        await safe_send_message(
            callback.from_user.id,
            "✅ Анкету видалено назавжди.\n\n"
            "Дякуємо, що користувалися нашим ботом! ❤️",
            reply_markup=types.ReplyKeyboardRemove()
        )
    except Exception as e:
        logging.error(f"Confirm delete error: {e}")
        await callback.answer("❌ Помилка при видаленні акаунту", show_alert=True)

@dp.callback_query(F.data == "cancel_delete")
async def cancel_delete_account(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
        await callback.message.answer("❌ Видалення акаунту скасовано", reply_markup=get_main_menu())
    except Exception as e:
        logging.error(f"Cancel delete error: {e}")

# =========================================================
# UNKNOWN
# =========================================================
@dp.message()
async def unknown_message(message: types.Message):
    user_id = message.from_user.id
    now = asyncio.get_event_loop().time()
    last = getattr(unknown_message, 'last_message', {}).get(user_id, 0)
    if now - last < 1:
        return
    unknown_message.last_message = getattr(unknown_message, 'last_message', {})
    unknown_message.last_message[user_id] = now
    await message.answer("❓ Використовуй меню або /start")
# =========================================================
# MAIN
# =========================================================
async def main():
    while True:
        try:
            print("🛠 Очищення webhook")
            await bot.delete_webhook(drop_pending_updates=True)
            print("🚀 Бот запущено!")
            asyncio.create_task(internet_watcher())
            asyncio.create_task(firebase_watcher())
            await dp.start_polling(bot, skip_updates=True)
        except KeyboardInterrupt:
            print("⛔ Bot stopped")
            break
        except Exception as e:
            logging.error(f"MAIN CRASH: {e}")
            await asyncio.sleep(15)
        finally:
            try:
                await bot.session.close()
            except:
                pass
# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    asyncio.run(main())


