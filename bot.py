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
# DISCLAIMER (Політика конфіденційності)
# =========================================================
DISCLAIMER_TEXT = (
    "⚠️ <b>ЗНЯТТЯ ВІДПОВІДАЛЬНОСТІ — ВАЖЛИВО!</b>\n\n"
    "Використовуючи кнопки <b>👍 Лайк</b>, <b>👎 Далі</b> та <b>💤 Завершити</b> "
    "для перегляду анкет, ви <b>повністю підтверджуєте та погоджуєтесь</b> з наступним:\n\n"
    "• Бот є лише технічною платформою для знайомств.\n"
    "• <b>Адміністрація бота НЕ несе жодної відповідальності</b> за:\n"
    "   — зміст анкет, фото та опис користувачів\n"
    "   — дії, слова, наміри та поведінку інших учасників\n"
    "   — будь-які зустрічі в реальному життя\n"
    "   — можливе шахрайство, образи, загрози чи інші наслідки\n\n"
    "• Уся відповідальність за перевірку інформації, безпеку та прийняті рішення "
    "лежить <b>виключно на вас</b>.\n"
    "• Ви використовуєте бот <b>на свій страх і ризик</b>.\n\n"
    "Якщо ви не згодні з цими умовами — <b>не використовуйте пошук анкет</b> "
    "та не натискайте кнопки лайк/далі.\n\n"
    "Продовжуючи — ви підтверджуєте, що ознайомлені та згодні."
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
TOKEN = "8731550935:AAHac8rH08YAI1Bi707oxV56nVLv4Gt0v20"
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
            types.KeyboardButton(text="📤 Надіслати другу (10 хв)")
        ],
        [types.KeyboardButton(text="📜 Політика конфіденційності")]
    ]
    return types.ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True
    )
# =========================================================
# SAFE FIREBASE (ASYNC)
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
# SAFE TELEGRAM
# =========================================================
async def safe_send_message(chat_id, text, **kwargs):
    for _ in range(5):
        try:
            return await bot.send_message(
                chat_id,
                text,
                **kwargs
            )
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
            return await bot.send_photo(
                chat_id,
                photo,
                caption=caption,
                **kwargs
            )
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramNetworkError:
            await asyncio.sleep(5)
        except TelegramForbiddenError:
            return None
        except Exception as e:
            logging.error(f"send_photo error: {e}")
            await asyncio.sleep(2)
    return None
# =========================================================
# INTERNET WATCHER
# =========================================================
async def internet_watcher():
    while True:
        try:
            me = await bot.get_me()
            logging.info(f"🌐 Internet OK @{me.username}")
        except Exception as e:
            logging.error(f"❌ INTERNET LOST: {e}")
        await asyncio.sleep(30)
# =========================================================
# FIREBASE WATCHER
# =========================================================
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
# GLOBAL ERRORS
# =========================================================
@dp.errors()
async def global_error_handler(event):
    logging.error(traceback.format_exc())
    try:
        if hasattr(event, "update") and event.update and event.update.message:
            await event.update.message.answer("⚠️ Тимчасова помилка. Спробуй ще раз.")
    except:
        pass
    return True
# =========================================================
# ANTI FLOOD
# =========================================================
user_last_message = {}
ANTI_FLOOD_SECONDS = 1
# =========================================================
# START
# =========================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    try:
        user_id = str(message.from_user.id)
        doc = await firebase_get(
            db.collection("users").document(user_id)
        )
        if doc and doc.exists:
            await message.answer(
                "❤️ З поверненням!",
                reply_markup=get_main_menu()
            )
            return
        a = random.randint(1, 9)
        b = random.randint(1, 9)
        await state.update_data(captcha_answer=a + b)
        await message.answer(
            f"🤖 Перевірка що ти не бот\n\n"
            f"{a} + {b} = ?"
        )
        await state.set_state(Registration.captcha)
    except Exception as e:
        logging.error(f"Start error: {e}")
# =========================================================
# CAPTCHA
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
            return await message.answer(
                f"❌ Неправильно\n\n"
                f"{a} + {b} = ?"
            )
        await message.answer("✅ Перевірку пройдено!")
        await asyncio.sleep(1)
        await message.answer("👋 Як тебе звати?")
        await state.set_state(Registration.waiting_for_name)
    except Exception as e:
        logging.error(f"Captcha error: {e}")
# =========================================================
# NAME
# =========================================================
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
# =========================================================
# AGE
# =========================================================
@dp.message(Registration.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        if not message.text.isdigit():
            return await message.answer("❌ Введи число")
        age = int(message.text)
        if age < 16 or age > 70:
            return await message.answer("❌ Вік 16-70")
        await state.update_data(age=age)
        kb = [
            [types.KeyboardButton(text="Іспанія")],
            [types.KeyboardButton(text="Польща")],
            [types.KeyboardButton(text="Німеччина")],
            [types.KeyboardButton(text="Чехія")],
            [types.KeyboardButton(text="Італія")]
        ]
        await message.answer(
            "🌍 Де ти зараз?",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=kb,
                resize_keyboard=True,
                one_time_keyboard=True
            )
        )
        await state.set_state(Registration.waiting_for_country)
    except Exception as e:
        logging.error(f"Age error: {e}")
# =========================================================
# COUNTRY
# =========================================================
@dp.message(Registration.waiting_for_country)
async def process_country(message: types.Message, state: FSMContext):
    try:
        await state.update_data(country=message.text)
        kb = [[
            types.KeyboardButton(text="Я Чоловік 👱‍♂️"),
            types.KeyboardButton(text="Я Жінка 👩")
        ]]
        await message.answer(
            "👤 Вкажи стать",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=kb,
                resize_keyboard=True,
                one_time_keyboard=True
            )
        )
        await state.set_state(Registration.waiting_for_gender)
    except Exception as e:
        logging.error(f"Country error: {e}")
# =========================================================
# GENDER
# =========================================================
@dp.message(Registration.waiting_for_gender)
async def process_gender(message: types.Message, state: FSMContext):
    try:
        await state.update_data(gender=message.text)
        kb = [[
            types.KeyboardButton(text="Шукаю Дівчину 👩"),
            types.KeyboardButton(text="Шукаю Хлопця 👱‍♂️")
        ]]
        await message.answer(
            "❤️ Кого шукаєш?",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=kb,
                resize_keyboard=True,
                one_time_keyboard=True
            )
        )
        await state.set_state(Registration.waiting_for_search)
    except Exception as e:
        logging.error(f"Gender error: {e}")
# =========================================================
# SEARCH
# =========================================================
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
# =========================================================
# PHOTO
# =========================================================
@dp.message(Registration.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    try:
        await state.update_data(photo=message.photo[-1].file_id)
        await message.answer(
            "📝 <b>Напиши трохи про себе</b>\n\n"
            "Це останній крок реєстрації. Після цього ти зможеш дивитися анкети."
        )
        await state.set_state(Registration.waiting_for_about)
    except Exception as e:
        logging.error(f"Photo error: {e}")
@dp.message(Registration.waiting_for_photo)
async def photo_error(message: types.Message):
    await message.answer(
        "❌ <b>Фото обов’язкове!</b>\n\n"
        "📎 Натисни на <b>скріпку 📎</b> внизу → обери фото з галереї.\n\n"
        "Без реального фото ти не зможеш завершити реєстрацію "
        "і користуватися ботом."
    )
# =========================================================
# ABOUT
# =========================================================
@dp.message(Registration.waiting_for_about)
async def process_about(message: types.Message, state: FSMContext):
    try:
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
            "registered_at": firestore.SERVER_TIMESTAMP
        }
        ok = await firebase_set(
            db.collection("users").document(user_id),
            profile
        )
        if not ok:
            return await message.answer("❌ Firebase error")
        await state.clear()
        await message.answer(
            "🎉 Анкету створено!",
            reply_markup=get_main_menu()
        )
    except Exception as e:
        logging.error(f"About error: {e}")
# =========================================================
# SEND NEXT PROFILE
# =========================================================
async def send_next_candidate(message: types.Message, user_id: str):
    try:
        my_doc = await firebase_get(
            db.collection("users").document(user_id)
        )
        if not my_doc or not my_doc.exists:
            return await message.answer("❌ Спочатку створи анкету через /start")
        my = my_doc.to_dict()
        if "Дівчину" in my.get("search", ""):
            target_gender = "Я Жінка 👩"
        else:
            target_gender = "Я Чоловік 👱‍♂️"
        docs = await asyncio.to_thread(
            lambda: db.collection("users")
            .where("gender", "==", target_gender)
            .where("country", "==", my["country"])
            .limit(100)
            .get()
        )
        candidates = []
        for doc in docs:
            try:
                data = doc.to_dict()
                if data and doc.id != user_id and "photo" in data:
                    candidates.append(data)
            except:
                pass
        if not candidates:
            return await message.answer("😔 Анкет немає")
        seen_docs = await asyncio.to_thread(
            lambda: db.collection("users")
            .document(user_id)
            .collection("seen")
            .get()
        )
        seen_ids = {doc.id for doc in seen_docs}
        available = [c for c in candidates if c["tg_id"] not in seen_ids]
        if not available:
            available = candidates
        candidate = random.choice(available)
        await firebase_set(
            db.collection("users")
            .document(user_id)
            .collection("seen")
            .document(candidate["tg_id"]),
            {"ts": firestore.SERVER_TIMESTAMP}
        )
        text = (
            f"👤 {candidate['name']}, {candidate['age']}\n"
            f"🌍 {candidate['country']}\n\n"
            f"📝 {candidate['about']}"
        )
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="👍 Лайк",
                        callback_data=f"like_{candidate['tg_id']}"
                    ),
                    types.InlineKeyboardButton(
                        text="👎 Далі",
                        callback_data="dislike"
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="💤 Завершити",
                        callback_data="stop_search"
                    )
                ]
            ]
        )
        await safe_send_photo(
            message.chat.id,
            candidate["photo"],
            caption=text,
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"CRITICAL send_next_candidate error:\n{traceback.format_exc()}")
        await message.answer("⚠️ Помилка при пошуку анкет. Спробуй пізніше.")
# =========================================================
# MENU SEARCH
# =========================================================
@dp.message(F.text == "1. Дивитися анкети 👥")
async def menu_search(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return await message.answer(
            "⚠️ Спочатку заверши реєстрацію!\n"
            "Надішли своє фото та текст про себе, щоб почати користуватися ботом."
        )

    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))

    if doc and doc.exists:
        data = doc.to_dict() or {}
        if not data.get("disclaimer_seen"):
            await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
            await firebase_set(
                db.collection("users").document(user_id),
                {**data, "disclaimer_seen": True}
            )
            await asyncio.sleep(0.8)

    await message.answer("🔍 Шукаю анкети...")
    await send_next_candidate(message, user_id)
# =========================================================
# MY PROFILE
# =========================================================
@dp.message(F.text == "2. Моя анкета 📝")
async def menu_profile(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return await message.answer(
            "⚠️ Спочатку заверши реєстрацію!\n"
            "Надішли своє фото та текст про себе."
        )
    try:
        user_id = str(message.from_user.id)
        doc = await firebase_get(
            db.collection("users").document(user_id)
        )
        if not doc or not doc.exists:
            return await message.answer("❌ Анкета не знайдена")
        p = doc.to_dict()
        text = (
            f"👤 {p['name']}, {p['age']}\n"
            f"🌍 {p['country']}\n\n"
            f"📝 {p['about']}"
        )
        await safe_send_photo(
            message.chat.id,
            p["photo"],
            caption=text
        )
    except Exception as e:
        logging.error(f"My profile error: {e}")
# =========================================================
# EDIT
# =========================================================
@dp.message(F.text == "3. Редагувати анкету ✏️")
async def menu_edit(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return await message.answer(
            "⚠️ Спочатку заверши реєстрацію!\n"
            "Надішли своє фото та текст про себе."
        )
    await message.answer(
        "✏️ Видали стару анкету\n"
        "і створи нову через /start"
    )
# =========================================================
# DELETE
# =========================================================
@dp.message(F.text == "4. Видалити анкету ❌")
async def menu_delete(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return await message.answer(
            "⚠️ Спочатку заверши реєстрацію!\n"
            "Надішли своє фото та текст про себе."
        )
    try:
        user_id = str(message.from_user.id)
        ref = db.collection("users").document(user_id)
        doc = await firebase_get(ref)
        if not doc or not doc.exists:
            return await message.answer("❌ Анкети немає")
        ok = await firebase_delete(ref)
        if not ok:
            return await message.answer("❌ Firebase error")
        await message.answer(
            "✅ Анкету видалено",
            reply_markup=types.ReplyKeyboardRemove()
        )
    except Exception as e:
        logging.error(f"Delete error: {e}")


# =========================================================
# ПОЛІТИКА КОНФІДЕНЦІЙНОСТІ (кнопка в меню)
# =========================================================
@dp.message(F.text == "📜 Політика конфіденційності")
async def show_privacy_policy(message: types.Message):
    await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")


# =========================================================
# ХТО МЕНЕ ЛАЙКНУВ + НАДІСЛАТИ ДРУГУ
# =========================================================
@dp.message(F.text == "👀 Хто мене лайкнув?")
async def show_who_liked_me(message: types.Message):
    user_id = str(message.from_user.id)
    doc = await firebase_get(db.collection("users").document(user_id))
    
    if not doc or not doc.exists:
        return await message.answer("❌ Спочатку створи анкету через /start")

    data = doc.to_dict() or {}
    unlock_until = data.get("likes_view_until", 0)

    if time.time() > unlock_until:
        return await message.answer(
            "🔒 Функція перегляду лайків заблокована.\n\n"
            "Натисни «📤 Надіслати другу (10 хв)» і надішли код другу, "
            "щоб розблокувати на 10 хвилин."
        )

    likes_docs = await asyncio.to_thread(
        lambda: db.collection("users")
        .document(user_id)
        .collection("likes")
        .get()
    )

    if not likes_docs:
        return await message.answer("😔 Поки що тебе ніхто не лайкнув.")

    text = "❤️ Тебе лайкнули:\n\n"
    for like_doc in likes_docs:
        try:
            liker_id = like_doc.id
            liker_doc = await firebase_get(db.collection("users").document(liker_id))
            if liker_doc and liker_doc.exists:
                liker = liker_doc.to_dict()
                text += f"👤 {liker.get('name', 'Без імені')}, {liker.get('age', '?')} — @{liker.get('username', '')}\n"
        except:
            pass

    await message.answer(text)


@dp.message(F.text == "📤 Надіслати другу (10 хв)")
async def send_to_friend_for_likes(message: types.Message):
    user_id = str(message.from_user.id)
    
    code = "UNLOCK_" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    await firebase_set(
        db.collection("unlock_codes").document(code),
        {
            "owner_id": user_id,
            "created_at": firestore.SERVER_TIMESTAMP
        }
    )
    
    await message.answer(
        f"✅ Код згенеровано!\n\n"
        f"<b>{code}</b>\n\n"
        f"📤 Надішли цей код своєму другу.\n"
        f"Коли він надішле код боту — ти отримаєш доступ до перегляду лайків на <b>10 хвилин</b>.\n\n"
        f"Після активації натисни «👀 Хто мене лайкнув?»",
        parse_mode="HTML"
    )


@dp.message(F.text.startswith("UNLOCK_"))
async def activate_unlock_code(message: types.Message):
    code = message.text.strip().upper()
    
    code_doc = await firebase_get(db.collection("unlock_codes").document(code))
    
    if not code_doc or not code_doc.exists:
        return await message.answer("❌ Невірний або прострочений код.")
    
    data = code_doc.to_dict()
    owner_id = data.get("owner_id")
    
    if not owner_id:
        return await message.answer("❌ Помилка активації.")
    
    unlock_time = int(time.time()) + 600  # 10 хвилин
    
    owner_doc = await firebase_get(db.collection("users").document(owner_id))
    if owner_doc and owner_doc.exists:
        owner_data = owner_doc.to_dict() or {}
        await firebase_set(
            db.collection("users").document(owner_id),
            {**owner_data, "likes_view_until": unlock_time}
        )
    
    await firebase_delete(db.collection("unlock_codes").document(code))
    
    await message.answer("✅ Код активовано! Тепер власник коду може 10 хвилин бачити хто його лайкнув.")
    
    try:
        await safe_send_message(
            owner_id,
            "🎉 Твій друг активував код!\n"
            "Тепер протягом 10 хвилин ти можеш натиснути «👀 Хто мене лайкнув?» і побачити лайки."
        )
    except:
        pass


# =========================================================
# LIKE
# =========================================================
@dp.callback_query(F.data.startswith("like_"))
async def handle_like(callback: types.CallbackQuery):
    try:
        my_id = str(callback.from_user.id)
        target_id = callback.data.split("_")[1]
        await firebase_set(
            db.collection("users")
            .document(my_id)
            .collection("likes")
            .document(target_id),
            {"ts": firestore.SERVER_TIMESTAMP}
        )
        reverse_like = await firebase_get(
            db.collection("users")
            .document(target_id)
            .collection("likes")
            .document(my_id)
        )
        if reverse_like and reverse_like.exists:
            me_doc = await firebase_get(db.collection("users").document(my_id))
            them_doc = await firebase_get(db.collection("users").document(target_id))
            me = me_doc.to_dict() if me_doc else {}
            them = them_doc.to_dict() if them_doc else {}
            await safe_send_message(
                my_id,
                f"🎉 МЕТЧ!\nПиши @{them.get('username', '')}"
            )
            await safe_send_message(
                target_id,
                f"🎉 МЕТЧ!\nПиши @{me.get('username', '')}"
            )
        try:
            await callback.message.delete()
        except:
            pass
        await send_next_candidate(callback.message, my_id)
    except Exception as e:
        logging.error(f"Like error: {e}")
# =========================================================
# DISLIKE
# =========================================================
@dp.callback_query(F.data == "dislike")
async def handle_dislike(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    await send_next_candidate(
        callback.message,
        str(callback.from_user.id)
    )
# =========================================================
# STOP SEARCH
# =========================================================
@dp.callback_query(F.data == "stop_search")
async def handle_stop(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer(
        "🛑 Пошук зупинено",
        reply_markup=get_main_menu()
    )
# =========================================================
# UNKNOWN
# =========================================================
@dp.message()
async def unknown_message(message: types.Message):
    user_id = message.from_user.id
    now = asyncio.get_event_loop().time()
    last = user_last_message.get(user_id, 0)
    if now - last < ANTI_FLOOD_SECONDS:
        return
    user_last_message[user_id] = now
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
            await dp.start_polling(
                bot,
                skip_updates=True
            )
        except KeyboardInterrupt:
            print("⛔ Bot stopped")
            break
        except TelegramNetworkError as e:
            logging.error(f"Internet lost: {e}")
            print("♻️ Reconnect after 10 sec")
            await asyncio.sleep(10)
        except Exception as e:
            logging.error(f"MAIN CRASH:\n{traceback.format_exc()}")
            print("♻️ Restart after 15 sec")
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


