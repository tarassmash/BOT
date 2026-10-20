import logging
import asyncio
import random
import time
import math
import traceback
import os
import json
import html
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramForbiddenError
from aiogram import BaseMiddleware
import firebase_admin
from firebase_admin import credentials, firestore
from admin import Ctx, start_admin

# =========================================================
# DISCLAIMER & CONFIG
# =========================================================
DISCLAIMER_TEXT = (
    "⚠️ <b>Перш ніж почати</b>\n\n"
    "• Бот — лише технічна платформа для знайомств. Адміністрація не відповідає за зміст анкет, "
    "слова та дії інших людей і за зустрічі в реальному житті.\n"
    "• Будь обережним(-ою): не надсилай гроші, не діли документи й коди з SMS, перші зустрічі — у людних місцях.\n"
    "• Побачив(-ла) фейк, спам чи образи — відкрий «☰ Ще» → «🚨 Скарга», модератор перевірить.\n\n"
    "Продовжуючи, ти погоджуєшся з цим."
)

COUNTRIES = ["Іспанія", "Польща", "Німеччина", "Чехія", "Італія"]

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
    "Тут — наші люди в Іспанії, Польщі, Німеччині, Чехії та Італії.\n\n"
    "✨ <b>Що вміє бот:</b>\n"
    "• анкети поруч — за відстанню і віком\n"
    "• 🏠 <b>земляки</b> — люди з твого міста в Україні першими\n"
    "• ☕️ <b>«вільний сьогодні»</b> — для тих, хто готовий зустрітися на каву\n"
    "• 💌 <b>суперлайк</b> із коротким повідомленням\n"
    "• 🎁 запроси друга — і ви <b>обоє</b> побачите, хто вас лайкнув\n\n"
    "Анкета займе одну хвилину 👇"
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

# токен має бути лише в Railway → Variables → BOT_TOKEN.
# Старий токен лежить у публічному репозиторії: відкличте його в @BotFather (/revoke) і задайте новий у BOT_TOKEN.
LEGACY_TOKEN = "8731550935:AAF_XmQNZjBmtnhtQ-cIJ3gFvYswg-eDiZs"
TOKEN = os.getenv("BOT_TOKEN", "").strip() or LEGACY_TOKEN
if TOKEN == LEGACY_TOKEN:
    logging.warning("⚠️ BOT_TOKEN не задано — використовується старий токен з коду. Він публічний: відкличте його в @BotFather і задайте новий у Railway → Variables → BOT_TOKEN.")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# =========================================================
# SETTINGS (редагуються в адмінці, зберігаються у Firestore system/settings)
# =========================================================
MIN_AGE = 18
SETTINGS = {
    "onboarding": ONBOARDING_TEXT,
    "disclaimer": DISCLAIMER_TEXT,
    "privacy": None,
    "welcome_back": "❤️ З поверненням!",
    "countries": list(COUNTRIES),
    "premium_minutes": 10,
    "report_hide": 3,
    "admin_ids": [],
}
ENV_ADMINS = {x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
BANNED = set()

def admin_ids():
    return ENV_ADMINS | {str(x) for x in SETTINGS.get("admin_ids") or []}

def countries():
    return SETTINGS.get("countries") or COUNTRIES

def premium_minutes():
    return int(SETTINGS.get("premium_minutes") or 10)

async def load_settings():
    if not db:
        return
    doc = await firebase_get(db.collection("system").document("settings"))
    if doc and doc.exists:
        for k, v in (doc.to_dict() or {}).items():
            if k in SETTINGS and v not in (None, "", []):
                SETTINGS[k] = v

async def save_settings(new):
    SETTINGS.update(new)
    await firebase_set(db.collection("system").document("settings"), SETTINGS)

async def load_banned():
    docs = await asyncio.to_thread(lambda: list(db.collection("users").where("banned", "==", True).stream()))
    BANNED.clear()
    BANNED.update(d.id for d in docs)

def on_ban(uid, banned):
    (BANNED.add if banned else BANNED.discard)(str(uid))

async def settings_watcher():
    while True:
        await asyncio.sleep(60)
        try:
            await load_settings()
            await load_banned()
        except Exception as e:
            logging.error(f"settings reload: {e}")

# заблоковані не можуть користуватися ботом; фіксуємо, коли людина була активна (раз на 10 хв)
_last_touch = {}

class GuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # у групах бот мовчить: працює лише в особистих повідомленнях
        if isinstance(event, types.Message) and event.chat.type != "private":
            return
        user = data.get("event_from_user")
        if user:
            uid = str(user.id)
            if uid in BANNED:
                if isinstance(event, types.Message):
                    await event.answer("⛔️ Ваш акаунт заблоковано адміністрацією.")
                elif isinstance(event, types.CallbackQuery):
                    await event.answer("⛔️ Акаунт заблоковано", show_alert=True)
                return
            # кнопка меню під час редагування/вводу — скасовує введення, а не потрапляє в анкету
            if isinstance(event, types.Message) and event.text in menu_texts():
                raw = data.get("raw_state")
                if raw and raw.startswith("Registration:"):
                    return await event.answer("⚠️ Спочатку заверши реєстрацію — відповідай на останнє питання 🙂")
                if raw and data.get("state"):
                    await data["state"].clear()
                    data["raw_state"] = None
            now = time.time()
            if db and now - _last_touch.get(uid, 0) > 600:
                _last_touch[uid] = now
                asyncio.create_task(touch(uid))
        return await handler(event, data)

async def touch(uid):
    try:
        await asyncio.to_thread(db.collection("users").document(uid).update, {"last_active": firestore.SERVER_TIMESTAMP, "blocked_bot": False})
    except Exception:
        pass  # ще не зареєстрований

dp.message.outer_middleware(GuardMiddleware())
dp.callback_query.outer_middleware(GuardMiddleware())

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
    waiting_for_hometown = State()
    waiting_for_location = State()

class SuperLike(StatesGroup):
    message = State()

class SearchFilters(StatesGroup):
    choosing_min_age = State()
    choosing_max_age = State()

class EditProfile(StatesGroup):
    name = State()
    age = State()
    photo = State()
    about = State()
    location = State()
    hometown = State()

class LeoSearch(StatesGroup):
    watching_profiles = State()  # старі кнопки 👍👎 (до оновлення)

# =========================================================
# TEXTS & KEYBOARDS
# =========================================================
MEN = "Я Чоловік 👱‍♂️"
WOMEN = "Я Жінка 👩"
WANT_F = "Шукаю Дівчину 👩"
WANT_M = "Шукаю Хлопця 👱‍♂️"

B_FEED = "👥 Дивитися анкети"
B_LIKES = "❤️ Мої лайки"
B_ME = "👤 Моя анкета"
B_FILTERS = "⚙️ Фільтри"
B_INVITE = "🎁 Запросити друга"
B_MORE = "☰ Ще"
SKIP = "Пропустити ➡️"
HOMETOWNS = ["Київ", "Львів", "Харків", "Одеса", "Дніпро", "Запоріжжя", "Вінниця", "Івано-Франківськ"]
COFFEE_HOURS = 24
# старі кнопки, які ще можуть бути в людей на клавіатурі
OLD_FEED = ["1. Дивитися анкети 👥"]
OLD_FILTERS = ["🔍 Пошук з фільтрами ⚙️"]
OLD_ME = ["2. Моя анкету 📝", "2. Моя анкета 📝", "3. Редагувати анкету ✏️"]
OLD_LIKES = ["👀 Хто мене лайкнув?"]
OLD_DELETE = ["4. Видалити анкету ❌"]
OLD_PRIVACY = ["📜 Політика конфіденційності"]

def menu_texts():
    return {B_FEED, B_LIKES, B_ME, B_FILTERS, B_INVITE, B_MORE, *OLD_FEED, *OLD_FILTERS, *OLD_ME, *OLD_LIKES, *OLD_DELETE, *OLD_PRIVACY}

def esc(s):
    return html.escape(str(s or ""))

def get_main_menu():
    kb = [
        [types.KeyboardButton(text=B_FEED)],
        [types.KeyboardButton(text=B_LIKES), types.KeyboardButton(text=B_ME)],
        [types.KeyboardButton(text=B_FILTERS), types.KeyboardButton(text=B_INVITE)],
        [types.KeyboardButton(text=B_MORE)],
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, is_persistent=True, input_field_placeholder="Обери дію 👇")

def reply_kb(rows, one_time=True):
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True, one_time_keyboard=one_time)

def ikb(rows):
    return types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text=t, **({"url": d[4:]} if d.startswith("url:") else {"callback_data": d})) for t, d in r] for r in rows])

def countries_kb(extra=None):
    cs = list(countries())
    rows = [cs[i:i + 2] for i in range(0, len(cs), 2)]
    if extra:
        rows.append([extra])
    return reply_kb(rows)

def location_kb():
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="📍 Надіслати локацію", request_location=True)], [types.KeyboardButton(text=SKIP)]], resize_keyboard=True, one_time_keyboard=True)

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

def user_ref(uid):
    return db.collection("users").document(str(uid))

async def get_user(uid):
    doc = await firebase_get(user_ref(uid))
    return (doc.to_dict() or {}) if doc and doc.exists else None

async def update_user(uid, fields):
    try:
        await asyncio.to_thread(user_ref(uid).update, fields)
        return True
    except Exception as e:
        logging.error(f"update_user {uid}: {e}")
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
    for _ in range(3):
        try:
            return await bot.send_photo(chat_id, photo, caption=caption, **kwargs)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            return None
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
            if db:
                await asyncio.to_thread(db.collection("system").document("ping").set, {"time": firestore.SERVER_TIMESTAMP})
                logging.info("🔥 Firebase OK")
        except Exception as e:
            logging.error(f"Firebase dead: {e}")
        await asyncio.sleep(60)

_bot_username = None
async def bot_username():
    global _bot_username
    if not _bot_username:
        _bot_username = (await bot.get_me()).username
    return _bot_username

# =========================================================
# CARDS
# =========================================================
def dist_km(me, p):
    if me and me.get("lat") is not None and p.get("lat") is not None:
        d = calculate_distance(me.get("lat"), me.get("lon"), p.get("lat"), p.get("lon"))
        if d < 999:
            return max(1, int(round(d)))
    return None

def is_free(p):
    return int(p.get("free_until") or 0) > time.time()

def same_home(me, p):
    a, b = str((me or {}).get("hometown") or "").strip().lower(), str(p.get("hometown") or "").strip().lower()
    return bool(a and a == b)

def card_text(p, me=None, incoming=False, own=False, note=""):
    lines = []
    if note:
        lines.append(f"💌 <b>Суперлайк!</b> «{esc(note)}»\n")
    elif incoming:
        lines.append("❤️ <b>Ти сподобався(-лась) цій людині!</b>\n")
    lines.append(f"<b>{esc(p.get('name'))}, {esc(p.get('age'))}</b>")
    place = f"🌍 {esc(p.get('country'))}"
    d = dist_km(me, p) if not own else None
    if d:
        place += f" · 📍 ~{d} км від тебе"
    lines.append(place)
    if p.get("hometown"):
        lines.append(f"🏠 Родом: {esc(p['hometown'])}" + (" — <b>земляки!</b> 🤝" if not own and same_home(me, p) else ""))
    if is_free(p):
        lines.append("☕️ <b>Вільний(-а) сьогодні — можна на каву</b>")
    if p.get("about"):
        lines.append(f"\n{esc(str(p.get('about'))[:700])}")
    return "\n".join(lines)[:1024]

def feed_kb(cid, incoming):
    i = "1" if incoming else "0"
    return ikb([[("❤️ Лайк", f"f:l:{cid}:{i}"), ("👎 Далі", f"f:n:{cid}:{i}")]])

# остання показана анкета кожного користувача — для суперлайку і скарги з меню «☰ Ще»
CURRENT = {}

REPORT_REASONS = {
    "fake": "Фейк / чуже фото",
    "minor": "Неповнолітня особа",
    "spam": "Спам, реклама або шахрайство",
    "nsfw": "Еротика або образи",
    "other": "Інше порушення",
}

def report_kb(cid, i):
    rows = [[(t, f"f:rr:{cid}:{k}")] for k, t in REPORT_REASONS.items()]
    rows.append([("⬅️ Назад", f"f:b:{cid}:{i}")])
    return ikb(rows)

def visible(p):
    return p and "photo" in p and "age" in p and not p.get("banned") and not p.get("hidden") and not p.get("paused")

# =========================================================
# MATCHING ENGINE
# =========================================================
async def find_next(user_id):
    """→ (анкета, вхідний_лайк, моя_анкета) або (None, причина, моя_анкета)."""
    me = await get_user(user_id)
    if not me:
        return None, "noprofile", None
    ref = user_ref(user_id)
    likes_docs = list(await asyncio.to_thread(lambda: ref.collection("likes").limit(10).get()))
    likes_docs.sort(key=lambda d: 0 if (d.to_dict() or {}).get("msg") else 1)  # суперлайки першими
    for like in likes_docs:
        p = await get_user(like.id)
        # анкету видалено або заблоковано — прибираємо лайк, щоб він не застрягав
        if not p or p.get("banned") or "photo" not in p:
            await firebase_delete(like.reference)
            continue
        if p.get("hidden") or p.get("paused"):
            continue
        p["tg_id"] = like.id
        p["_note"] = (like.to_dict() or {}).get("msg", "")
        # щоб після відповіді ця людина не з'явилась ще раз у звичайній стрічці
        await firebase_set(ref.collection("seen").document(like.id), {"ts": firestore.SERVER_TIMESTAMP})
        return p, True, me

    filters = me.get("search_filters") or {}
    target_gender = WOMEN if "Дівчину" in me.get("search", "") else MEN
    query = db.collection("users").where("gender", "==", target_gender).where("country", "==", filters.get("country") or me.get("country"))
    docs = await asyncio.to_thread(lambda: query.limit(300).get())
    min_age, max_age = filters.get("min_age"), filters.get("max_age")
    seen_ids = {d.id for d in list(await asyncio.to_thread(lambda: ref.collection("seen").get()))}
    cand = []
    for d in docs:
        p = d.to_dict() or {}
        if d.id == str(user_id) or d.id in seen_ids or not visible(p):
            continue
        if (min_age and p["age"] < min_age) or (max_age and p["age"] > max_age):
            continue
        p["tg_id"] = d.id
        cand.append(p)
    if not cand:
        return None, "empty", me
    my_age = me.get("age", 25)
    # ☕️ вільні сьогодні та земляки — вище; далі відстань і вік
    cand.sort(key=lambda c: (0 if is_free(c) else 1, 0 if same_home(me, c) else 1, dist_km(me, c) or 99999, abs(c.get("age", 99) - my_age), random.random()))
    p = cand[0]
    await firebase_set(ref.collection("seen").document(p["tg_id"]), {"ts": firestore.SERVER_TIMESTAMP})
    return p, False, me

async def show_next(user_id, chat_id, msg=None, mid=None):
    """Показує наступну анкету. Якщо є повідомлення з попередньою — міняє його на місці (без нового повідомлення і звуку)."""
    try:
        p, incoming, me = await find_next(user_id)
    except Exception as e:
        logging.error(f"find_next: {e}")
        return await safe_send_message(chat_id, "⚠️ Не вдалося завантажити анкети. Спробуй ще раз за хвилину.", reply_markup=get_main_menu())
    if msg and msg.photo:
        mid = msg.message_id
    if p is None:
        CURRENT.pop(str(user_id), None)
        if mid:
            try:
                await bot.delete_message(chat_id, mid)
            except Exception:
                pass
        if incoming == "noprofile":
            return await safe_send_message(chat_id, "Спочатку створи анкету — натисни /start")
        f = (me or {}).get("search_filters") or {}
        hint = "Спробуй інші фільтри або зазирни пізніше — нові люди реєструються щодня." if (f.get("country") or f.get("min_age") or f.get("max_age")) else "Зазирни пізніше — нові люди реєструються щодня. А поки можна запросити друзів 🎁"
        return await safe_send_message(chat_id, f"😔 <b>Нових анкет поки немає.</b>\n\n{hint}", parse_mode="HTML", disable_notification=True,
                                       reply_markup=ikb([[("⚙️ Фільтри", "flt:open"), ("🔄 Ще раз", "f:more")]]))
    caption, kb = card_text(p, me, incoming, note=p.get("_note", "")), feed_kb(p["tg_id"], incoming)
    CURRENT[str(user_id)] = {"cid": p["tg_id"], "i": incoming, "name": p.get("name", ""), "chat": chat_id, "mid": None}
    if mid:
        try:
            await bot.edit_message_media(chat_id=chat_id, message_id=mid, media=types.InputMediaPhoto(media=p["photo"], caption=caption, parse_mode="HTML"), reply_markup=kb)
            CURRENT[str(user_id)]["mid"] = mid
            return
        except Exception as e:
            logging.info(f"edit_media fallback: {e}")
            try:
                await bot.delete_message(chat_id, mid)
            except Exception:
                pass
    sent = await safe_send_photo(chat_id, p["photo"], caption=caption, parse_mode="HTML", reply_markup=kb, disable_notification=True)
    if sent:
        CURRENT[str(user_id)]["mid"] = sent.message_id
    return sent

_like_ping = {}

async def notify_liked(cid):
    # не частіше разу на годину, щоб не дратувати сповіщеннями
    now = time.time()
    if now - _like_ping.get(cid, 0) < 3600:
        return
    _like_ping[cid] = now
    await safe_send_message(cid, "❤️ Комусь сподобалась твоя анкета! Відкрий анкети — ця людина буде першою.",
                            reply_markup=ikb([[("👀 Подивитися", "f:more")]]))

def chat_link(p, uid):
    if p.get("username"):
        return f"https://t.me/{p['username']}"
    return None

async def make_match(user_id, cid, note=""):
    me, other = await get_user(user_id), await get_user(cid)
    if not me or not other:
        return
    try:
        await asyncio.to_thread(db.collection("matches").add, {"a": str(user_id), "b": str(cid), "ts": firestore.SERVER_TIMESTAMP})
    except Exception as e:
        logging.error(f"match log: {e}")
    for to, who, who_id in ((user_id, other, cid), (cid, me, user_id)):
        link = chat_link(who, who_id)
        text = (f"🎉 <b>Взаємна симпатія!</b>\n\nТи і <b>{esc(who.get('name'))}</b> сподобались одне одному.\n" +
                ("Напиши першим(-ою) 👇" if link else f"Напиши: <a href=\"tg://user?id={who_id}\">{esc(who.get('name'))}</a>"))
        if note and to == cid:
            text += f"\n\n💌 Повідомлення: «{esc(note)}»"
        kb = ikb([[("💬 Написати", "url:" + link)]]) if link else None
        await safe_send_photo(to, who.get("photo"), caption=text, parse_mode="HTML", reply_markup=kb)
    if not me.get("username"):
        await safe_send_message(user_id, "💡 У тебе немає нікнейму в Telegram, тому тобі складніше написати. Додай його: Налаштування → Ім'я користувача.")

# =========================================================
# FEED CALLBACKS (кнопки під анкетою)
# =========================================================
@dp.callback_query(F.data.startswith("f:"))
async def feed_callback(callback: types.CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    parts = callback.data.split(":")
    act = parts[1]
    msg = callback.message
    try:
        if act == "more":
            await callback.answer()
            return await show_next(user_id, msg.chat.id)
        if act == "slx":
            await state.clear()
            await callback.answer("Скасовано")
            try:
                await msg.delete()
            except Exception:
                pass
            return
        if act == "s":
            await callback.answer("💤 Пауза")
            try:
                await msg.edit_reply_markup(reply_markup=ikb([[("▶️ Продовжити", "f:more")]]))
            except Exception:
                pass
            return
        cid = parts[2]
        if act == "r":
            await callback.answer()
            return await msg.edit_text(f"🚨 <b>Скарга — {esc(CURRENT.get(user_id, {}).get('name', 'анкету'))}</b>\n\nЩо не так?", parse_mode="HTML", reply_markup=report_kb(cid, parts[3]))
        if act == "b":
            await callback.answer()
            try:
                await msg.delete()
            except Exception:
                pass
            return
        if act == "rr":
            reason = REPORT_REASONS.get(parts[3], REPORT_REASONS["other"])
            await asyncio.to_thread(db.collection("reports").add, {
                "reporter_id": user_id, "reported_user_id": cid, "reason": reason,
                "timestamp": firestore.SERVER_TIMESTAMP, "status": "new"})
            await firebase_set(user_ref(user_id).collection("seen").document(cid), {"ts": firestore.SERVER_TIMESTAMP})
            await firebase_delete(user_ref(user_id).collection("likes").document(cid))
            asyncio.create_task(after_report(cid, user_id))
            await callback.answer("🚨 Скаргу прийнято. Модератор перевірить анкету.", show_alert=True)
            try:
                await msg.delete()  # повідомлення меню зі скаргою
            except Exception:
                pass
            cur = CURRENT.get(user_id) or {}
            return await show_next(user_id, msg.chat.id, mid=cur.get("mid") if cur.get("cid") == cid else None)
        incoming = parts[3] == "1"
        if act == "sl":
            me = await get_user(user_id) or {}
            left = int(me.get("superlike_at") or 0) + DAY_S - time.time()
            if left > 0:
                return await callback.answer(f"💌 Суперлайк — раз на добу. Наступний через {int(left // 3600)} год {int(left % 3600 // 60)} хв", show_alert=True)
            await callback.answer()
            await state.set_state(SuperLike.message)
            await state.update_data(sl_cid=cid, sl_incoming=incoming)
            prompt = (f"💌 <b>Суперлайк — {esc(CURRENT.get(user_id, {}).get('name', ''))}</b>\n\n"
                      "Напиши коротке повідомлення (до 200 символів) — його побачать разом з твоєю анкетою, і вона покажеться першою.")
            try:
                return await msg.edit_text(prompt, parse_mode="HTML", reply_markup=ikb([[("✖️ Скасувати", "f:slx")]]))
            except Exception:
                return await msg.answer(prompt, parse_mode="HTML", reply_markup=ikb([[("✖️ Скасувати", "f:slx")]]))
        if act == "l":
            await callback.answer("❤️")
            if incoming:
                await firebase_delete(user_ref(user_id).collection("likes").document(cid))
                asyncio.create_task(make_match(user_id, cid))
            else:
                # якщо ця людина вже лайкнула мене — одразу взаємність
                mine = await firebase_get(user_ref(user_id).collection("likes").document(cid))
                if mine and mine.exists:
                    await firebase_delete(mine.reference)
                    asyncio.create_task(make_match(user_id, cid))
                else:
                    await firebase_set(user_ref(cid).collection("likes").document(user_id), {"ts": firestore.SERVER_TIMESTAMP})
                    asyncio.create_task(notify_liked(cid))
            return await show_next(user_id, msg.chat.id, msg)
        if act == "n":
            await callback.answer()
            if incoming:
                await firebase_delete(user_ref(user_id).collection("likes").document(cid))
            return await show_next(user_id, msg.chat.id, msg)
        await callback.answer()
    except Exception as e:
        logging.error(f"feed_callback {callback.data}: {e}")
        try:
            await callback.answer("⚠️ Помилка, спробуй ще раз")
        except Exception:
            pass

DAY_S = 86400

@dp.message(SuperLike.message)
async def superlike_message(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not 1 <= len(text) <= 200:
        return await message.answer("❌ Напиши текстом, до 200 символів")
    data = await state.get_data()
    await state.clear()
    uid, cid = str(message.from_user.id), data.get("sl_cid")
    if not cid:
        return
    if data.get("sl_incoming"):
        # людина вже лайкнула — це одразу взаємність, повідомлення відправимо разом
        await firebase_delete(user_ref(uid).collection("likes").document(cid))
        await update_user(uid, {"superlike_at": int(time.time())})
        asyncio.create_task(make_match(uid, cid, note=text))
    else:
        mine = await firebase_get(user_ref(uid).collection("likes").document(cid))
        await update_user(uid, {"superlike_at": int(time.time())})
        if mine and mine.exists:
            await firebase_delete(mine.reference)
            asyncio.create_task(make_match(uid, cid, note=text))
        else:
            await firebase_set(user_ref(cid).collection("likes").document(uid), {"ts": firestore.SERVER_TIMESTAMP, "msg": text})
            await safe_send_message(cid, "💌 Тобі надіслали <b>суперлайк</b> з повідомленням! Відкрий анкети — ця людина буде першою.", parse_mode="HTML",
                                    reply_markup=ikb([[("👀 Подивитися", "f:more")]]))
    await message.answer("💌 Суперлайк надіслано!", reply_markup=get_main_menu())
    cur = CURRENT.get(uid) or {}
    await show_next(uid, message.chat.id, mid=cur.get("mid") if cur.get("cid") == cid else None)

# старі кнопки 👍👎💤🚨 під полем вводу — переводимо на нові
@dp.message(F.text.in_(["👍", "👎", "💤", "🚨"]))
async def legacy_feed_buttons(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("✨ Бот оновився: тепер кнопки ❤️ 👎 прямо під анкетою.", reply_markup=get_main_menu())
    await show_next(str(message.from_user.id), message.chat.id)

async def open_feed(message: types.Message, state: FSMContext, user_id: str):
    cur = await state.get_state()
    if cur and "Registration" in cur:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    await state.clear()
    me = await get_user(user_id)
    if not me:
        return await message.answer("Спочатку створи анкету — натисни /start")
    if not me.get("disclaimer_seen"):
        await message.answer(SETTINGS["disclaimer"], parse_mode="HTML")
        await update_user(user_id, {"disclaimer_seen": True})
        await asyncio.sleep(0.6)
    if me.get("paused"):
        await message.answer("⏸ Твоя анкета на паузі — інші тебе зараз не бачать.", reply_markup=ikb([[("▶️ Увімкнути анкету", "me:resume")]]))
    await show_next(user_id, message.chat.id)

@dp.message(F.text.in_([B_FEED] + OLD_FEED))
async def menu_feed(message: types.Message, state: FSMContext):
    await open_feed(message, state, str(message.from_user.id))

@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    await open_feed(message, state, str(message.from_user.id))

# =========================================================
# REGISTRATION
# =========================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    try:
        user_id = str(message.from_user.id)
        args = message.text.split()[1:]
        referrer = args[0][4:] if args and args[0].startswith("ref_") else None
        if await get_user(user_id):
            await state.clear()
            return await message.answer(SETTINGS["welcome_back"], reply_markup=get_main_menu())
        await state.clear()
        if referrer and referrer != user_id:
            await state.update_data(referrer=referrer)
        a, b = random.randint(1, 9), random.randint(1, 9)
        await state.update_data(captcha_answer=a + b)
        await message.answer(f"🤖 Перевірка, що ти не бот\n\n<b>{a} + {b} = ?</b>", parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
        await state.set_state(Registration.captcha)
    except Exception as e:
        logging.error(f"Start error: {e}")

@dp.message(Registration.captcha)
async def process_captcha(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not (message.text or "").strip().isdigit() or int(message.text) != data.get("captcha_answer"):
        a, b = random.randint(1, 9), random.randint(1, 9)
        await state.update_data(captcha_answer=a + b)
        return await message.answer(f"❌ Неправильно. Ще раз:\n\n<b>{a} + {b} = ?</b>", parse_mode="HTML")
    await message.answer(SETTINGS["onboarding"], parse_mode="HTML")
    await asyncio.sleep(1)
    name = (message.from_user.first_name or "").strip()[:30]
    await message.answer("👋 <b>Як тебе звати?</b>" + ("\n\nМожна натиснути кнопку нижче." if name else ""), parse_mode="HTML",
                         reply_markup=reply_kb([[name]]) if len(name) >= 2 else types.ReplyKeyboardRemove())
    await state.set_state(Registration.waiting_for_name)

@dp.message(Registration.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 30:
        return await message.answer("❌ Ім'я — від 2 до 30 символів")
    await state.update_data(name=name)
    await message.answer("🎂 <b>Скільки тобі років?</b>", parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Registration.waiting_for_age)

@dp.message(Registration.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    t = (message.text or "").strip()
    if not t.isdigit():
        return await message.answer("❌ Напиши вік числом, наприклад 27")
    age = int(t)
    if age < MIN_AGE or age > 70:
        return await message.answer(f"❌ Бот лише для дорослих: вік від {MIN_AGE} до 70")
    await state.update_data(age=age)
    await message.answer("🌍 <b>У якій ти країні?</b>", parse_mode="HTML", reply_markup=countries_kb())
    await state.set_state(Registration.waiting_for_country)

@dp.message(Registration.waiting_for_country)
async def process_country(message: types.Message, state: FSMContext):
    if message.text not in countries():
        return await message.answer("👇 Обери країну кнопкою", reply_markup=countries_kb())
    await state.update_data(country=message.text)
    await message.answer("👤 <b>Ти...</b>", parse_mode="HTML", reply_markup=reply_kb([[MEN, WOMEN]]))
    await state.set_state(Registration.waiting_for_gender)

@dp.message(Registration.waiting_for_gender)
async def process_gender(message: types.Message, state: FSMContext):
    if message.text not in (MEN, WOMEN):
        return await message.answer("👇 Обери кнопкою", reply_markup=reply_kb([[MEN, WOMEN]]))
    await state.update_data(gender=message.text)
    await message.answer("❤️ <b>Кого шукаєш?</b>", parse_mode="HTML", reply_markup=reply_kb([[WANT_F, WANT_M]]))
    await state.set_state(Registration.waiting_for_search)

@dp.message(Registration.waiting_for_search)
async def process_search(message: types.Message, state: FSMContext):
    if message.text not in (WANT_F, WANT_M):
        return await message.answer("👇 Обери кнопкою", reply_markup=reply_kb([[WANT_F, WANT_M]]))
    await state.update_data(search=message.text)
    await message.answer("📸 <b>Надішли своє реальне фото</b>\n\nОбличчя має бути добре видно. Анкети з чужими фото видаляються.\n📎 Скріпка → Фото.",
                         parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Registration.waiting_for_photo)

@dp.message(Registration.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("📝 <b>Напиши кілька слів про себе</b>\n\nЩо любиш, кого шукаєш, у якому ти місті. Це останній крок 🙂", parse_mode="HTML")
    await state.set_state(Registration.waiting_for_about)

@dp.message(Registration.waiting_for_photo)
async def photo_error(message: types.Message):
    await message.answer("❌ <b>Потрібне саме фото</b>\n\n📎 Скріпка → Фото.", parse_mode="HTML")

@dp.message(Registration.waiting_for_about)
async def process_about(message: types.Message, state: FSMContext):
    try:
        about = (message.text or "").strip()
        if len(about) < 3:
            return await message.answer("❌ Напиши хоча б кілька слів текстом")
        data = await state.get_data()
        user_id = str(message.from_user.id)
        referrer = data.get("referrer")
        now = int(time.time())
        profile = {
            "tg_id": user_id,
            "username": message.from_user.username or "",
            "name": data["name"],
            "age": data["age"],
            "country": data["country"],
            "gender": data["gender"],
            "search": data["search"],
            "photo": data["photo"],
            "about": about[:700],
            "registered_at": firestore.SERVER_TIMESTAMP,
            "search_filters": {"country": None, "min_age": None, "max_age": None},
            "lat": None,
            "lon": None,
            "last_active": firestore.SERVER_TIMESTAMP,
            "referred_by": referrer or "",
        }
        if referrer:
            profile["likes_view_until"] = now + premium_minutes() * 60  # бонус і новому користувачу
        await firebase_set(user_ref(user_id), profile)

        if referrer:
            ref = await get_user(referrer)
            if ref:
                until = max(now, int(ref.get("likes_view_until") or 0)) + premium_minutes() * 60
                await update_user(referrer, {"likes_view_until": until})
                await safe_send_message(referrer, f"🎉 Твій друг зареєструвався! «Хто мене лайкнув?» відкрито ще на {premium_minutes()} хв.",
                                        reply_markup=ikb([[("❤️ Подивитися", "lk:show")]]))

        await state.set_state(Registration.waiting_for_hometown)
        await message.answer("🏠 <b>Звідки ти родом в Україні?</b>\n\nЗемляків бот показує вище й позначає 🤝. Обери або напиши своє місто.",
                             parse_mode="HTML", reply_markup=hometown_kb())
    except Exception as e:
        logging.error(f"About error: {e}")

def hometown_kb():
    rows = [HOMETOWNS[i:i + 4] for i in range(0, len(HOMETOWNS), 4)] + [[SKIP]]
    return reply_kb(rows)

def clean_town(t):
    t = (t or "").strip()
    return t[:1].upper() + t[1:40] if 2 <= len(t) <= 40 and not t.startswith("/") else None

@dp.message(Registration.waiting_for_hometown)
async def process_hometown(message: types.Message, state: FSMContext):
    try:
        if message.text != SKIP:
            town = clean_town(message.text)
            if not town:
                return await message.answer("Напиши назву міста або натисни «Пропустити»", reply_markup=hometown_kb())
            await update_user(message.from_user.id, {"hometown": town})
        await state.set_state(Registration.waiting_for_location)
        await message.answer("📍 <b>Покажемо людей поруч?</b>\n\nНадішли локацію — інші бачитимуть лише приблизну відстань у км, не адресу.",
                             parse_mode="HTML", reply_markup=location_kb())
    except Exception as e:
        logging.error(f"Hometown error: {e}")

async def finish_registration(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)
    p = await get_user(user_id)
    await message.answer("🎉 <b>Анкету створено!</b> Ось як її бачать інші:", parse_mode="HTML", reply_markup=get_main_menu())
    if p:
        await safe_send_photo(message.chat.id, p["photo"], caption=card_text(p, own=True), parse_mode="HTML")
    if p and p.get("likes_view_until", 0) > time.time():
        await message.answer(f"🎁 Тебе запросив друг — «Хто мене лайкнув?» відкрито на {premium_minutes()} хв.")
    if not message.from_user.username:
        await message.answer("💡 Додай нікнейм у Telegram (Налаштування → Ім'я користувача), щоб після взаємної симпатії тобі могли написати.")
    await message.answer("Натисни «👥 Дивитися анкети», щоб почати 👇")

@dp.message(Registration.waiting_for_location, F.location)
async def process_location(message: types.Message, state: FSMContext):
    await update_user(message.from_user.id, {"lat": message.location.latitude, "lon": message.location.longitude})
    await finish_registration(message, state)

@dp.message(Registration.waiting_for_location)
async def skip_location(message: types.Message, state: FSMContext):
    if message.text and (message.text == SKIP or message.text.lower() in ["пропустити", "skip", "пізніше"]):
        return await finish_registration(message, state)
    await message.answer("📍 Надішли локацію кнопкою або натисни «Пропустити»", reply_markup=location_kb())

# =========================================================
# FILTERS
# =========================================================
AGE_PRESETS = [("18–25", 18, 25), ("25–35", 25, 35), ("35–45", 35, 45), ("45+", 45, 70)]

def filters_view(me):
    f = me.get("search_filters") or {}
    country = f.get("country") or f"{me.get('country')} (твоя)"
    age = f"{f['min_age'] or 18}–{f['max_age'] or 70}" if (f.get("min_age") or f.get("max_age")) else "будь-який"
    text = f"⚙️ <b>Фільтри пошуку</b>\n\n🌍 Країна: <b>{esc(country)}</b>\n🎂 Вік: <b>{age}</b>\n\nАнкети поруч показуються першими."
    kb = ikb([
        [("🌍 Країна", "flt:country"), ("🎂 Вік", "flt:age")],
        [("🔄 Скинути", "flt:reset"), ("✅ Шукати", "f:more")],
    ])
    return text, kb

async def save_filters(uid, **kw):
    me = await get_user(uid) or {}
    f = dict(me.get("search_filters") or {"country": None, "min_age": None, "max_age": None})
    f.update(kw)
    await update_user(uid, {"search_filters": f})
    # нові фільтри — показуємо й тих, кого вже бачили з іншими налаштуваннями? ні: «бачені» лишаються
    me["search_filters"] = f
    return me

@dp.message(F.text.in_([B_FILTERS] + OLD_FILTERS))
async def menu_filters(message: types.Message, state: FSMContext):
    await state.clear()
    me = await get_user(message.from_user.id)
    if not me:
        return await message.answer("Спочатку створи анкету — натисни /start")
    text, kb = filters_view(me)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data.startswith("flt:"))
async def filters_callback(callback: types.CallbackQuery, state: FSMContext):
    uid = str(callback.from_user.id)
    act = callback.data.split(":", 2)
    await callback.answer()
    msg = callback.message
    me = await get_user(uid)
    if not me:
        return
    if act[1] == "open":
        text, kb = filters_view(me)
        return await msg.answer(text, parse_mode="HTML", reply_markup=kb)
    if act[1] == "country":
        cs = list(countries())
        rows = [[(c, f"flt:c:{i}") for i, c in enumerate(cs)][j:j + 2] for j in range(0, len(cs), 2)]
        rows.append([(f"Моя ({me.get('country')})", "flt:c:mine")])
        return await msg.edit_text("🌍 Де шукати?", reply_markup=ikb(rows))
    if act[1] == "c":
        cs = list(countries())
        country = None if act[2] == "mine" else cs[int(act[2])] if act[2].isdigit() and int(act[2]) < len(cs) else None
        me = await save_filters(uid, country=country)
    elif act[1] == "age":
        rows = [[(t, f"flt:a:{a}-{b}") for t, a, b in AGE_PRESETS[:2]], [(t, f"flt:a:{a}-{b}") for t, a, b in AGE_PRESETS[2:]],
                [("Будь-який", "flt:a:any"), ("✍️ Свій діапазон", "flt:a:custom")]]
        return await msg.edit_text("🎂 Який вік показувати?", reply_markup=ikb(rows))
    elif act[1] == "a":
        if act[2] == "custom":
            await msg.edit_text("✍️ Напиши мінімальний вік (від 18):")
            return await state.set_state(SearchFilters.choosing_min_age)
        if act[2] == "any":
            me = await save_filters(uid, min_age=None, max_age=None)
        else:
            a, b = (int(x) for x in act[2].split("-"))
            me = await save_filters(uid, min_age=a, max_age=b)
    elif act[1] == "reset":
        me = await save_filters(uid, country=None, min_age=None, max_age=None)
    text, kb = filters_view(me)
    try:
        await msg.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await msg.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.message(SearchFilters.choosing_min_age)
async def process_min_age(message: types.Message, state: FSMContext):
    t = (message.text or "").strip()
    if not t.isdigit() or not MIN_AGE <= int(t) <= 70:
        return await message.answer(f"❌ Число від {MIN_AGE} до 70")
    await state.update_data(min_age=int(t))
    await message.answer("✍️ А максимальний?")
    await state.set_state(SearchFilters.choosing_max_age)

@dp.message(SearchFilters.choosing_max_age)
async def process_max_age(message: types.Message, state: FSMContext):
    t = (message.text or "").strip()
    data = await state.get_data()
    lo = data.get("min_age", MIN_AGE)
    if not t.isdigit() or not lo <= int(t) <= 70:
        return await message.answer(f"❌ Число від {lo} до 70")
    await state.clear()
    me = await save_filters(str(message.from_user.id), min_age=lo, max_age=int(t))
    text, kb = filters_view(me)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

# =========================================================
# MY PROFILE
# =========================================================
def me_kb(p):
    return ikb([
        [("📷 Фото", "me:photo"), ("📝 Про себе", "me:about")],
        [("👤 Ім'я", "me:name"), ("🎂 Вік", "me:age")],
        [("🌍 Країна", "me:country"), ("📍 Локація", "me:loc")],
        [("🏠 Звідки родом", "me:home"), ("☕️ Сьогодні вільний(-а) ✓" if is_free(p) else "☕️ Вільний(-а) сьогодні", "me:coffee")],
        [("▶️ Увімкнути анкету", "me:resume") if p.get("paused") else ("⏸ Пауза", "me:pause"), ("🗑 Видалити", "me:del")],
    ])

async def send_me(chat_id, uid, note=None):
    p = await get_user(uid)
    if not p:
        return await safe_send_message(chat_id, "❌ Анкети немає. Натисни /start, щоб створити.")
    cap = (note + "\n\n" if note else "") + card_text(p, own=True)
    if p.get("paused"):
        cap += "\n\n⏸ <i>Анкета на паузі — її ніхто не бачить.</i>"
    await safe_send_photo(chat_id, p["photo"], caption=cap[:1024], parse_mode="HTML", reply_markup=me_kb(p))

@dp.message(F.text.in_([B_ME] + OLD_ME))
async def menu_profile(message: types.Message, state: FSMContext):
    cur = await state.get_state()
    if cur and "Registration" in cur:
        return await message.answer("⚠️ Спочатку заверши реєстрацію!")
    await state.clear()
    await send_me(message.chat.id, str(message.from_user.id))

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message, state: FSMContext):
    await menu_profile(message, state)

EDIT_PROMPTS = {
    "photo": (EditProfile.photo, "📷 Надішли нове фото"),
    "about": (EditProfile.about, "📝 Напиши новий текст про себе"),
    "name": (EditProfile.name, "👤 Як тебе звати?"),
    "age": (EditProfile.age, "🎂 Скільки тобі років?"),
    "loc": (EditProfile.location, "📍 Надішли нову локацію кнопкою нижче"),
    "home": (EditProfile.hometown, "🏠 Звідки ти родом в Україні? Обери або напиши місто"),
}

@dp.callback_query(F.data.startswith("me:"))
async def me_callback(callback: types.CallbackQuery, state: FSMContext):
    uid = str(callback.from_user.id)
    act = callback.data.split(":")
    await callback.answer()
    if act[1] in EDIT_PROMPTS:
        st, prompt = EDIT_PROMPTS[act[1]]
        await state.set_state(st)
        kb = location_kb() if act[1] == "loc" else hometown_kb() if act[1] == "home" else ikb([[("✖️ Скасувати", "me:cancel")]])
        return await callback.message.answer(prompt, reply_markup=kb)
    if act[1] == "cancel":
        await state.clear()
        return await callback.message.answer("Скасовано", reply_markup=get_main_menu())
    if act[1] == "country":
        cs = list(countries())
        rows = [[(c, f"me:c:{i}") for i, c in enumerate(cs)][j:j + 2] for j in range(0, len(cs), 2)]
        return await callback.message.answer("🌍 У якій ти країні?", reply_markup=ikb(rows))
    if act[1] == "c":
        cs = list(countries())
        if act[2].isdigit() and int(act[2]) < len(cs):
            await update_user(uid, {"country": cs[int(act[2])]})
        try:
            await callback.message.delete()
        except Exception:
            pass
        return await send_me(callback.message.chat.id, uid, "✅ Збережено")
    if act[1] == "coffee":
        p = await get_user(uid) or {}
        on = not is_free(p)
        await update_user(uid, {"free_until": int(time.time()) + COFFEE_HOURS * 3600 if on else 0})
        try:
            await callback.message.delete()
        except Exception:
            pass
        note = ("☕️ Готово! Наступні 24 години твоя анкета показується вище з позначкою «вільний(-а) сьогодні»." if on
                else "☕️ Позначку знято.")
        return await send_me(callback.message.chat.id, uid, note)
    if act[1] in ("pause", "resume"):
        await update_user(uid, {"paused": act[1] == "pause"})
        try:
            await callback.message.delete()
        except Exception:
            pass
        note = "⏸ Анкету поставлено на паузу. Ти можеш дивитися анкети, але тебе ніхто не бачить." if act[1] == "pause" else "▶️ Анкета знову в пошуку!"
        return await send_me(callback.message.chat.id, uid, note)
    if act[1] == "del":
        return await callback.message.answer(
            "⚠️ <b>Видалити анкету назавжди?</b>\n\nФото, лайки та симпатії буде стерто. Якщо хочеш просто перепочити — краще «⏸ Пауза».",
            parse_mode="HTML", reply_markup=ikb([[("🗑 Так, видалити", "confirm_delete")], [("⏸ Краще пауза", "me:pause"), ("✖️ Ні", "cancel_delete")]]))

async def edit_done(message, state, fields):
    await state.clear()
    await update_user(message.from_user.id, fields)
    await message.answer("✅ Збережено", reply_markup=get_main_menu())
    await send_me(message.chat.id, str(message.from_user.id))

@dp.message(EditProfile.photo, F.photo)
async def edit_photo(message: types.Message, state: FSMContext):
    await edit_done(message, state, {"photo": message.photo[-1].file_id})

@dp.message(EditProfile.photo)
async def edit_photo_err(message: types.Message):
    await message.answer("❌ Потрібне фото: 📎 Скріпка → Фото")

@dp.message(EditProfile.about)
async def edit_about(message: types.Message, state: FSMContext):
    t = (message.text or "").strip()
    if len(t) < 3:
        return await message.answer("❌ Напиши хоча б кілька слів")
    await edit_done(message, state, {"about": t[:700]})

@dp.message(EditProfile.name)
async def edit_name(message: types.Message, state: FSMContext):
    t = (message.text or "").strip()
    if not 2 <= len(t) <= 30:
        return await message.answer("❌ Ім'я — від 2 до 30 символів")
    await edit_done(message, state, {"name": t})

@dp.message(EditProfile.age)
async def edit_age(message: types.Message, state: FSMContext):
    t = (message.text or "").strip()
    if not t.isdigit() or not MIN_AGE <= int(t) <= 70:
        return await message.answer(f"❌ Вік від {MIN_AGE} до 70")
    await edit_done(message, state, {"age": int(t)})

@dp.message(EditProfile.hometown)
async def edit_hometown(message: types.Message, state: FSMContext):
    if message.text == SKIP:
        await state.clear()
        return await message.answer("Не змінено", reply_markup=get_main_menu())
    town = clean_town(message.text)
    if not town:
        return await message.answer("Напиши назву міста", reply_markup=hometown_kb())
    await edit_done(message, state, {"hometown": town})

@dp.message(EditProfile.location, F.location)
async def edit_location(message: types.Message, state: FSMContext):
    await edit_done(message, state, {"lat": message.location.latitude, "lon": message.location.longitude})

@dp.message(EditProfile.location)
async def edit_location_other(message: types.Message, state: FSMContext):
    if message.text == SKIP:
        await state.clear()
        return await message.answer("Локацію не змінено", reply_markup=get_main_menu())
    await message.answer("📍 Натисни кнопку «Надіслати локацію»", reply_markup=location_kb())

# =========================================================
# DELETE
# =========================================================
@dp.message(F.text.in_(OLD_DELETE))
async def menu_delete(message: types.Message, state: FSMContext):
    await state.clear()
    if not await get_user(message.from_user.id):
        return await message.answer("❌ Анкети немає")
    await message.answer("⚠️ <b>Видалити анкету назавжди?</b>", parse_mode="HTML",
                         reply_markup=ikb([[("🗑 Так, видалити", "confirm_delete")], [("⏸ Краще пауза", "me:pause"), ("✖️ Ні", "cancel_delete")]]))

@dp.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: types.CallbackQuery):
    await callback.answer("Скасовано")
    try:
        await callback.message.delete()
    except Exception:
        pass

@dp.callback_query(F.data == "confirm_delete")
async def confirm_delete(callback: types.CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    ref = user_ref(callback.from_user.id)
    try:
        for sub in ("seen", "likes"):
            for doc in await asyncio.to_thread(lambda: ref.collection(sub).get()):
                await firebase_delete(doc.reference)
    except Exception as e:
        logging.error(f"Subcollection cleanup error: {e}")
    await firebase_delete(ref)
    await callback.message.answer("❌ Анкету повністю видалено. Захочеш повернутися — натисни /start.", reply_markup=types.ReplyKeyboardRemove())

# =========================================================
# LIKES
# =========================================================
async def show_likes(chat_id, uid):
    me = await get_user(uid)
    if not me:
        return await safe_send_message(chat_id, "Спочатку створи анкету — натисни /start")
    likes = list(await asyncio.to_thread(lambda: user_ref(uid).collection("likes").limit(20).get()))
    n = len(likes)
    if not n:
        return await safe_send_message(chat_id, "😔 Поки нових лайків немає.\n\nПорада: гарне фото з усмішкою та кілька слів про себе дають у рази більше лайків 😉",
                                       reply_markup=ikb([[("📷 Змінити фото", "me:photo"), ("📝 Про себе", "me:about")]]))
    if time.time() > int(me.get("likes_view_until") or 0):
        link = f"https://t.me/{await bot_username()}?start=ref_{uid}"
        return await safe_send_message(chat_id,
            f"❤️ <b>Тебе лайкнули: {n}{'+' if n == 20 else ''}</b>\n\n"
            "Ці люди покажуться першими, коли відкриєш анкети 👥\n\n"
            f"🔓 Щоб одразу побачити всіх списком — запроси друга: ви обоє отримаєте Преміум на {premium_minutes()} хв.",
            parse_mode="HTML", reply_markup=ikb([[("👥 Дивитися анкети", "f:more")], [("🎁 Запросити друга", "url:" + share_url(link))]]))
    await safe_send_message(chat_id, f"💎 <b>Тебе лайкнули: {n}</b>", parse_mode="HTML")
    for like in likes[:10]:
        p = await get_user(like.id)
        if not visible(p):
            continue
        await safe_send_photo(chat_id, p["photo"], caption=card_text(p, me), parse_mode="HTML", disable_notification=True,
                              reply_markup=ikb([[("❤️ Взаємно", f"lk:y:{like.id}"), ("👎", f"lk:n:{like.id}")]]))
        await asyncio.sleep(0.2)

@dp.message(F.text.in_([B_LIKES] + OLD_LIKES))
async def menu_likes(message: types.Message, state: FSMContext):
    await state.clear()
    await show_likes(message.chat.id, str(message.from_user.id))

@dp.message(Command("likes"))
async def cmd_likes(message: types.Message, state: FSMContext):
    await menu_likes(message, state)

@dp.callback_query(F.data.startswith("lk:"))
async def likes_callback(callback: types.CallbackQuery):
    uid = str(callback.from_user.id)
    act = callback.data.split(":")
    if act[1] == "show":
        await callback.answer()
        return await show_likes(callback.message.chat.id, uid)
    cid = act[2]
    await firebase_delete(user_ref(uid).collection("likes").document(cid))
    await firebase_set(user_ref(uid).collection("seen").document(cid), {"ts": firestore.SERVER_TIMESTAMP})
    if act[1] == "y":
        await callback.answer("❤️")
        asyncio.create_task(make_match(uid, cid))
        try:
            await callback.message.edit_reply_markup(reply_markup=ikb([[("✅ Взаємна симпатія", "noop")]]))
        except Exception:
            pass
    else:
        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:
            pass

@dp.callback_query(F.data == "noop")
async def noop(callback: types.CallbackQuery):
    await callback.answer()

# =========================================================
# INVITE, MORE, HELP
# =========================================================
def share_url(link):
    from urllib.parse import quote
    text = "Знайомства для українців за кордоном ❤️ Спробуй — тут багато наших поруч!"
    return f"https://t.me/share/url?url={quote(link)}&text={quote(text)}"

@dp.message(F.text.startswith("📤 Запросити друга") | (F.text == B_INVITE))
async def menu_invite_friend(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)
    link = f"https://t.me/{await bot_username()}?start=ref_{user_id}"
    me = await get_user(user_id) or {}
    invited = 0
    try:
        invited = len(await asyncio.to_thread(lambda: db.collection("users").where("referred_by", "==", user_id).limit(100).get()))
    except Exception:
        pass
    text = (
        f"🎁 <b>Запроси друга — отримайте Преміум обоє</b>\n\n"
        f"Коли друг зареєструється за твоїм посиланням, ви <b>обоє</b> отримаєте {premium_minutes()} хв «Хто мене лайкнув?».\n\n"
        f"🔗 Твоє посилання:\n<code>{link}</code>\n\n"
        f"👥 Ти вже запросив(-ла): <b>{invited}</b>"
    )
    if int(me.get("likes_view_until") or 0) > time.time():
        text += f"\n💎 Преміум активний до {time.strftime('%H:%M', time.localtime(me['likes_view_until']))}"
    await message.answer(text, parse_mode="HTML", reply_markup=ikb([[("📤 Надіслати другу", "url:" + share_url(link))]]))

def privacy_text():
    return SETTINGS.get("privacy") or (
        "📜 <b>Політика конфіденційності та правила</b>\n\n"
        "1. Бот обробляє ваші дані (ім'я, вік, країну, фото, локацію) лише для пошуку анкет.\n"
        "2. Точна локація нікому не показується — лише приблизна відстань у кілометрах.\n"
        "3. Бот лише для повнолітніх (18+). Чужі фото, спам і образи — бан.\n"
        "4. Анкету можна поставити на паузу або назавжди видалити: «👤 Моя анкета» → «🗑 Видалити»."
    )

HELP_TEXT = (
    "ℹ️ <b>Як це працює</b>\n\n"
    "👥 <b>Анкети</b> — під фото лише дві кнопки: ❤️ Лайк і 👎 Далі. Хто вже лайкнув тебе, показується першим.\n"
    "🎉 <b>Взаємна симпатія</b> — коли ви обоє тиснете ❤️, бот надсилає контакт.\n"
    "💌 <b>Суперлайк</b> і 🚨 <b>скарга</b> на анкету, яку зараз дивишся, — у меню «☰ Ще». Суперлайк — раз на добу, з коротким повідомленням.\n"
    "☕️ <b>Вільний(-а) сьогодні</b> — на 24 години анкета вище з позначкою «можна на каву».\n"
    "🏠 <b>Земляки</b> — вкажи, звідки ти родом, і бот підніме земляків та позначить 🤝.\n"
    "⚙️ <b>Фільтри</b> — країна і вік. Люди поруч — першими (якщо додати локацію).\n"
    "👤 <b>Моя анкета</b> — змінити фото, текст, вік, локацію; пауза або видалення.\n"
    "🚨 Скарги на фейки, спам чи образи перевіряє модератор.\n\n"
    "Команди: /search — анкети, /profile — моя анкета, /likes — лайки."
)

@dp.message(F.text == B_MORE)
async def menu_more(message: types.Message, state: FSMContext):
    await state.clear()
    rows = []
    cur = CURRENT.get(str(message.from_user.id))
    if cur and cur.get("cid"):
        i = "1" if cur.get("i") else "0"
        name = str(cur.get("name") or "")[:20]
        rows += [[(f"💌 Суперлайк: {name}", f"f:sl:{cur['cid']}:{i}")], [(f"🚨 Скарга: {name}", f"f:r:{cur['cid']}:{i}")]]
    await message.answer("☰ <b>Ще</b>", parse_mode="HTML", reply_markup=ikb(rows + [
        [("☕️ Вільний(-а) сьогодні", "me:coffee")],
        [("ℹ️ Як це працює", "more:help"), ("📜 Правила", "more:privacy")],
        [("⏸ Пауза анкети", "me:pause"), ("🗑 Видалити анкету", "me:del")],
    ]))

@dp.callback_query(F.data.startswith("more:"))
async def more_callback(callback: types.CallbackQuery):
    await callback.answer()
    if callback.data == "more:help":
        await callback.message.answer(HELP_TEXT, parse_mode="HTML")
    else:
        await callback.message.answer(privacy_text(), parse_mode="HTML")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(HELP_TEXT, parse_mode="HTML")

@dp.message(F.text.in_(OLD_PRIVACY))
async def menu_privacy(message: types.Message):
    await message.answer(privacy_text(), parse_mode="HTML", reply_markup=get_main_menu())

# =========================================================
# ADMIN: скарги в Telegram, /admin, /myid
# =========================================================
async def after_report(reported_id, reporter_id):
    try:
        docs = await asyncio.to_thread(lambda: list(db.collection("reports").where("reported_user_id", "==", reported_id).stream()))
        new = [d.to_dict() or {} for d in docs if (d.to_dict() or {}).get("status", "new") == "new"]
        reporters = {r.get("reporter_id") for r in new}
        fields = {"reports_count": len(new)}
        limit = int(SETTINGS.get("report_hide") or 0)
        auto = bool(limit and len(reporters) >= limit)
        if auto:
            fields["hidden"] = True
        await asyncio.to_thread(db.collection("users").document(reported_id).update, fields)
        doc = await firebase_get(db.collection("users").document(reported_id))
        p = (doc.to_dict() or {}) if doc and doc.exists else {}
        if not admin_ids() or not p:
            return
        cap = (f"🚨 <b>Скарга</b> ({len(new)} від {len(reporters)} людей)" + (" — <b>анкету приховано автоматично</b>" if auto else "") +
               f"\n\n👤 {html.escape(str(p.get('name')))}, {p.get('age')} · {html.escape(str(p.get('country')))}\n" + (f"@{p['username']}\n" if p.get("username") else "") +
               f"ID: <code>{reported_id}</code>\n\n📝 {html.escape(str(p.get('about', ''))[:500])}")
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="⛔️ Забанити", callback_data=f"adm_ban_{reported_id}"),
            types.InlineKeyboardButton(text="🙈 Сховати", callback_data=f"adm_hide_{reported_id}"),
            types.InlineKeyboardButton(text="✅ Все ок", callback_data=f"adm_ok_{reported_id}"),
        ]])
        for aid in admin_ids():
            await safe_send_photo(aid, p.get("photo"), caption=cap[:1000], parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logging.error(f"after_report: {e}")

async def mark_reports(uid, status):
    def run():
        for d in db.collection("reports").where("reported_user_id", "==", uid).stream():
            if (d.to_dict() or {}).get("status", "new") == "new":
                d.reference.update({"status": status, "handled_at": int(time.time())})
    await asyncio.to_thread(run)

@dp.callback_query(F.data.startswith("adm_"))
async def admin_callback(callback: types.CallbackQuery):
    if str(callback.from_user.id) not in admin_ids():
        return await callback.answer("Немає доступу", show_alert=True)
    _, action, uid = callback.data.split("_", 2)
    ref = db.collection("users").document(uid)
    try:
        if action == "ban":
            await asyncio.to_thread(ref.update, {"banned": True, "banned_at": int(time.time())})
            on_ban(uid, True)
            await mark_reports(uid, "banned")
            await safe_send_message(uid, "⛔️ Ваш акаунт заблоковано адміністрацією за порушення правил.")
            note = "⛔️ Забанено"
        elif action == "hide":
            await asyncio.to_thread(ref.update, {"hidden": True})
            note = "🙈 Приховано зі стрічки"
        else:
            await asyncio.to_thread(ref.update, {"hidden": False, "reports_count": 0})
            await mark_reports(uid, "dismissed")
            note = "✅ Скаргу відхилено"
        await callback.answer(note)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(note)
    except Exception as e:
        logging.error(f"admin_callback: {e}")
        await callback.answer("Помилка: анкету вже видалено?", show_alert=True)

@dp.message(Command("myid"))
async def cmd_myid(message: types.Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>", parse_mode="HTML")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if str(message.from_user.id) not in admin_ids():
        return
    docs = await asyncio.to_thread(lambda: list(db.collection("users").stream()))
    us = [d.to_dict() or {} for d in docs]
    now = time.time()
    def recent(u, k, days):
        v = u.get(k)
        return hasattr(v, "timestamp") and v.timestamp() > now - days * 86400
    new_rep = await asyncio.to_thread(lambda: sum(1 for d in db.collection("reports").stream() if (d.to_dict() or {}).get("status", "new") == "new"))
    url = os.getenv("ADMIN_URL") or (f"https://{os.getenv('RAILWAY_PUBLIC_DOMAIN')}/admin" if os.getenv("RAILWAY_PUBLIC_DOMAIN") else "")
    text = (f"🛠 <b>Адмінка</b>\n\n👥 Анкет: <b>{len(us)}</b> (👱‍♂️ {sum(1 for u in us if 'Чоловік' in str(u.get('gender')))} · 👩 {sum(1 for u in us if 'Жінка' in str(u.get('gender')))})\n"
            f"🆕 За добу: <b>{sum(1 for u in us if recent(u, 'registered_at', 1))}</b> · за тиждень: <b>{sum(1 for u in us if recent(u, 'registered_at', 7))}</b>\n"
            f"🔥 Активні за добу: <b>{sum(1 for u in us if recent(u, 'last_active', 1))}</b>\n"
            f"🚨 Нових скарг: <b>{new_rep}</b> · ⛔️ забанено: {sum(1 for u in us if u.get('banned'))}\n" + (f"\n🔗 {url}" if url else "\nВеб-адмінка: відкрийте домен сервісу Railway + /admin"))
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

# =========================================================
# POOLING & RUN
# =========================================================
async def main():
    asyncio.create_task(internet_watcher())
    asyncio.create_task(firebase_watcher())
    if db:
        await load_settings()
        await load_banned()
        asyncio.create_task(settings_watcher())
        await start_admin(Ctx(db, bot, SETTINGS, save_settings, admin_ids, on_ban))
    
    try:
        await bot.set_my_commands([
            types.BotCommand(command="search", description="👥 Дивитися анкети"),
            types.BotCommand(command="likes", description="❤️ Мої лайки"),
            types.BotCommand(command="profile", description="👤 Моя анкета"),
            types.BotCommand(command="help", description="ℹ️ Як це працює"),
            types.BotCommand(command="start", description="🔄 Головне меню"),
        ])
    except Exception as e:
        logging.error(f"set_my_commands: {e}")
    print("🚀 Бот запускається...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
