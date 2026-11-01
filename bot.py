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
from google.api_core.exceptions import AlreadyExists
from admin import Ctx, start_admin
from webapp import MiniApp, app_url, ref_link, main_app
import sys

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
    "plans": [{"days": 7, "stars": 250}, {"days": 30, "stars": 750}, {"days": 90, "stars": 1750}],  # Преміум за зірки: ≈5 € / 15 € / 35 €
    "star_eur": 0.02,  # скільки євро приблизно платить людина за 1 зірку (250 ⭐ ≈ 5 € у магазинах застосунків)
    "payments": True,
    "report_hide": 3,
    "admin_ids": [],
    "nudges": True,
    "app_only": True,  # усе — у застосунку: у чаті лише кнопка «Відкрити», сповіщення, оплата й підтримка
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
    if SETTINGS.get("plans") == OLD_DEFAULT_PLANS:
        SETTINGS["plans"] = list(DEFAULT_PLANS)

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
        if isinstance(event, types.Message) and event.successful_payment:
            return await handler(event, data)  # гроші вже списано — платіж зараховуємо завжди
        if user:
            uid = str(user.id)
            if uid in BANNED:
                if isinstance(event, types.Message):
                    await event.answer("⛔️ Ваш акаунт заблоковано адміністрацією.")
                elif isinstance(event, types.CallbackQuery):
                    await event.answer("⛔️ Акаунт заблоковано", show_alert=True)
                return
            # режим «лише застосунок»: усе, що не сповіщення/оплата/підтримка, ведемо в застосунок
            if app_only() and uid not in admin_ids():
                raw = str(data.get("raw_state") or "")
                if isinstance(event, types.Message) and not raw.startswith(("Support:", "EditProfile:")):
                    cmd = (event.text or "").split()[0].split("@")[0].lower() if (event.text or "").startswith("/") else ""
                    if cmd not in APP_ONLY_COMMANDS:
                        if raw and data.get("state"):
                            await data["state"].clear()
                        txt = event.text or ""
                        tab = {"/likes": "likes", "/profile": "profile", "/search": "feed"}.get(cmd) or (
                            "likes" if txt in [B_LIKES] + OLD_LIKES else "profile" if txt in [B_ME] + OLD_ME else "feed")
                        return await send_app_only(event.chat.id, tab)
                if isinstance(event, types.CallbackQuery) and not (event.data or "").startswith(APP_ONLY_CALLBACKS) and event.data != "premium":
                    await event.answer()
                    return await send_app_only(event.from_user.id)
            # кнопка меню під час редагування/вводу — скасовує введення, а не потрапляє в анкету
            # будь-яка команда скасовує незавершене звернення в підтримку
            if isinstance(event, types.Message) and (event.text or "").startswith("/") and str(data.get("raw_state") or "").startswith("Support:"):
                await data["state"].clear()
                data["raw_state"] = None
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
# ОПЛАТА ПРЕМІУМУ ЗІРКАМИ TELEGRAM (XTR)
# Обробники стоять тут, вище за кроки реєстрації: платіж, що прийшов посеред анкети,
# не має потрапити у відповідь на питання.
# =========================================================
DEFAULT_PLANS = [{"days": 7, "stars": 250}, {"days": 30, "stars": 750}, {"days": 90, "stars": 1750}]
OLD_DEFAULT_PLANS = [{"days": 7, "stars": 75}, {"days": 30, "stars": 250}, {"days": 90, "stars": 600}]  # перші ціни (≈1,5 € за тиждень) — замінюємо на нові, якщо їх не міняли в адмінці

def plans():
    """Тарифи з адмінки: [{"days": 7, "stars": 75}, ...]"""
    out = []
    for p in SETTINGS.get("plans") or DEFAULT_PLANS:
        try:
            d, s = int(p["days"]), int(p["stars"])
            if 1 <= d <= 365 and 1 <= s <= 100000:
                out.append({"days": d, "stars": s})
        except Exception:
            continue
    return out or list(DEFAULT_PLANS)

def payments_on():
    return SETTINGS.get("payments", True) is not False

def star_eur():
    try:
        return max(0.001, float(SETTINGS.get("star_eur") or 0.02))
    except Exception:
        return 0.02

def eur(stars):
    """Приблизна ціна в євро: 250 ⭐ → «5 €», 75 ⭐ → «1,5 €»."""
    v = stars * star_eur()
    t = f"{round(v):d}" if v >= 10 or abs(v - round(v)) < 0.05 else f"{v:.1f}".replace(".", ",")
    return f"{t} €"

def plan_name(days):
    return {7: "Тиждень", 30: "Місяць", 90: "3 місяці", 180: "Пів року", 365: "Рік"}.get(days, f"{days} дн.")

def plan_title(days):
    return {7: "тиждень", 30: "місяць", 90: "3 місяці", 180: "пів року", 365: "рік"}.get(days, f"{days} дн.")

def fmt_until(ts):
    return time.strftime("%d.%m.%Y %H:%M" if ts - time.time() > 86400 else "%H:%M", time.localtime(ts))

def premium_until(p):
    return int((p or {}).get("likes_view_until") or 0)

def invoice_args(uid, plan):
    d, s = plan["days"], plan["stars"]
    return dict(
        title=f"Преміум на {plan_title(d)} — ≈{eur(s)}",
        description=f"«Хто мене лайкнув?» на {d} дн.: бачиш усіх, кому сподобався(-лась), і відповідаєш взаємністю. {s} ⭐ ≈ {eur(s)}.",
        payload=f"prem:{d}:{s}:{uid}",
        currency="XTR",  # зірки Telegram: оплата всередині Telegram, provider_token не потрібен
        prices=[types.LabeledPrice(label=f"Преміум {d} дн.", amount=s)],
    )

async def premium_invoice_link(uid, i):
    ps = plans()
    if not payments_on() or not 0 <= i < len(ps):
        raise ValueError("Тариф недоступний")
    return await bot.create_invoice_link(**invoice_args(uid, ps[i]))

def parse_payload(payload):
    try:
        kind, d, s, uid = str(payload).split(":")
        return (int(d), int(s), uid) if kind == "prem" else None
    except Exception:
        return None

async def add_premium(uid, seconds):
    """Додає час Преміуму до поточного (і в анкету, і в окремий запис, що переживає видалення анкети)."""
    uid = str(uid)
    p = await get_user(uid)
    keep = await firebase_get(db.collection("premium").document(uid))
    cur = max(premium_until(p), int(((keep.to_dict() or {}) if keep and keep.exists else {}).get("until") or 0))
    until = max(int(time.time()), cur) + int(seconds)
    await firebase_set(db.collection("premium").document(uid), {"until": until})
    if p:
        await update_user(uid, {"likes_view_until": until})
    return until

async def paid_premium_until(uid):
    """Оплачений Преміум, який треба повернути в нову анкету після видалення старої."""
    try:
        d = await firebase_get(db.collection("premium").document(str(uid)))
        u = int(((d.to_dict() or {}) if d and d.exists else {}).get("until") or 0)
        return u if u > time.time() else 0
    except Exception:
        return 0

@dp.pre_checkout_query()
async def pre_checkout(query: types.PreCheckoutQuery):
    info = parse_payload(query.invoice_payload)
    uid = str(query.from_user.id)
    ok = bool(info) and payments_on() and query.currency == "XTR" and info[2] == uid and uid not in BANNED \
        and {"days": info[0], "stars": info[1]} in plans() and query.total_amount == info[1]
    if ok:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Ціна або тариф змінилися. Відкрий «Преміум» ще раз і обери тариф.")

@dp.message(F.successful_payment)
async def on_successful_payment(message: types.Message, state: FSMContext):
    sp = message.successful_payment
    uid = str(message.from_user.id)
    info = parse_payload(sp.invoice_payload)
    if not info:
        logging.error(f"Невідомий платіж {sp.invoice_payload}")
        return
    days = info[0]
    me = await get_user(uid) or {}
    record = {"uid": uid, "name": me.get("name") or message.from_user.first_name or "", "username": message.from_user.username or "",
              "days": days, "stars": sp.total_amount, "currency": sp.currency, "status": "paid", "ts": int(time.time())}
    # один платіж — одне нарахування, навіть якщо Telegram надішле оновлення двічі
    try:
        await asyncio.to_thread(db.collection("payments").document(sp.telegram_payment_charge_id).create, record)
    except AlreadyExists:
        logging.warning(f"Повторне оновлення платежу {sp.telegram_payment_charge_id}")
        return
    until = await add_premium(uid, days * 86400)
    logging.info(f"💰 {sp.total_amount}⭐ від {uid}: Преміум {days} дн. до {fmt_until(until)}")
    await message.answer(f"💎 <b>Дякуємо! Преміум активовано до {fmt_until(until)}.</b>\n\nТепер видно всіх, кому ти сподобався(-лась) ❤️",
                         parse_mode="HTML", reply_markup=ikb([[open_btn("❤️ Хто мене лайкнув", "likes", "lk:show")]]))
    for a in admin_ids():
        await safe_send_message(a, f"💰 Оплата {sp.total_amount}⭐ · {esc(record['name'])} (<code>{uid}</code>) · {days} дн.", parse_mode="HTML")

def premium_text(p):
    u = premium_until(p)
    head = f"💎 <b>Преміум активний до {fmt_until(u)}</b>\nМожна продовжити — дні додадуться.\n\n" if u > time.time() else "💎 <b>Преміум</b>\n\n"
    return (head + "• бачиш усіх, хто тебе лайкнув, — з фото й анкетою\n"
            "• відповідаєш взаємністю одним дотиком\n\n"
            + "".join(f"💶 <b>{plan_name(x['days'])} — ≈{eur(x['stars'])}</b> ({x['stars']} ⭐)\n" for x in plans()) + "\n"
            "Оплата зірками Telegram ⭐ прямо тут, без картки й сторонніх сайтів. "
            "Ціна в євро — приблизна: зірки купуються в Telegram, ціна трохи залежить від країни й телефону.\n"
            f"🎁 Або запроси друга — ви обоє отримаєте {premium_minutes()} хв безкоштовно.")

async def plans_kb(uid):
    rows = [[(f"{plan_name(p['days'])} — ≈{eur(p['stars'])} · {p['stars']} ⭐", f"buy:{i}")] for i, p in enumerate(plans())] if payments_on() else []
    link = ref_link(await bot_username(), uid)
    return ikb(rows + [[(B_INVITE, "url:" + share_url(link))]])

async def send_premium(chat_id, uid):
    await safe_send_message(chat_id, premium_text(await get_user(uid)), parse_mode="HTML", reply_markup=await plans_kb(uid))

@dp.message(Command("premium"))
async def cmd_premium(message: types.Message, state: FSMContext):
    if app_only():
        return await send_app_only(message.chat.id, "profile", "💎 Преміум — у застосунку: «Профіль» → «Преміум» 👇")
    await send_premium(message.chat.id, str(message.from_user.id))

@dp.callback_query(F.data == "premium")
async def cb_premium(callback: types.CallbackQuery):
    await callback.answer()
    if app_only():
        return await send_app_only(callback.from_user.id, "profile", "💎 Преміум — у застосунку: «Профіль» → «Преміум» 👇")
    await send_premium(callback.message.chat.id, str(callback.from_user.id))

@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: types.CallbackQuery):
    i = int(callback.data.split(":")[1]) if callback.data.split(":")[1].isdigit() else -1
    ps = plans()
    if not payments_on() or not 0 <= i < len(ps):
        return await callback.answer("Тариф недоступний", show_alert=True)
    await callback.answer()
    await bot.send_invoice(callback.message.chat.id, **invoice_args(str(callback.from_user.id), ps[i]))

@dp.message(Command("terms"))
async def cmd_terms(message: types.Message):
    lst = "\n".join(f"• {plan_name(p['days'])} ({p['days']} дн.) — {p['stars']} ⭐ ≈ {eur(p['stars'])}" for p in plans())
    lst += "\n\nЦіна в євро приблизна: оплата йде зірками Telegram, а їхня ціна трохи залежить від країни й способу купівлі."
    await message.answer(
        "📄 <b>Умови Преміуму</b>\n\n" + lst + "\n\n"
        "• Оплата разова, зірками Telegram, без автосписань. Дні додаються до поточного строку.\n"
        "• Преміум відкриває список тих, хто тебе лайкнув. Оплачений строк зберігається, навіть якщо видалити анкету й створити нову.\n"
        "• Якщо Преміум не активувався або сталася помилка — /paysupport, ми повернемо зірки.\n"
        "• За порушення правил (фейки, спам, образи) анкету блокують без повернення коштів.\n\n"
        "Правила користування: /help", parse_mode="HTML")

# =========================================================
# ПІДТРИМКА: з застосунку, з чату (/support, /paysupport) і відповіді з адмінки або з Telegram
# =========================================================
class Support(StatesGroup):
    message = State()   # користувач пише в підтримку
    reply = State()     # адмін відповідає з Telegram

SUPPORT_MAX = 2000

async def support_add(uid, text, frm="user", source="chat", tg_user=None):
    """Зберігає повідомлення в support/{uid}/msgs і оновлює звернення. frm: user | admin."""
    uid = str(uid)
    text = str(text or "").strip()[:SUPPORT_MAX]
    if not text:
        raise ValueError("Порожнє повідомлення")
    now = time.time()
    ref = db.collection("support").document(uid)
    await asyncio.to_thread(ref.collection("msgs").document(f"{int(now * 1000):013d}").set,
                            {"from": frm, "text": text, "ts": now, "source": source})
    doc = await firebase_get(ref)
    cur = (doc.to_dict() or {}) if doc and doc.exists else {}
    upd = {"uid": uid, "last_ts": now, "last_text": text[:200], "last_from": frm}
    if frm == "user":
        p = await get_user(uid) or {}
        upd.update({"status": "open", "unread_admin": int(cur.get("unread_admin") or 0) + 1,
                    "name": p.get("name") or (tg_user.first_name if tg_user else "") or cur.get("name", ""),
                    "username": p.get("username") or (tg_user.username if tg_user and tg_user.username else "") or cur.get("username", "")})
        if not cur.get("created"):
            upd["created"] = now
    else:
        upd.update({"unread_admin": 0, "unread_user": int(cur.get("unread_user") or 0) + 1})
    await asyncio.to_thread(ref.set, upd, merge=True)
    return upd

async def support_from_user(uid, text, source, tg_user=None):
    t = await support_add(uid, text, "user", source, tg_user)
    src = {"app": "застосунок", "chat": "чат"}.get(source, source)
    for a in admin_ids():
        await safe_send_message(a, f"💬 <b>Підтримка</b> · {esc(t.get('name') or uid)}"
                                   f"{' @' + esc(t['username']) if t.get('username') else ''} (<code>{uid}</code>, {src})\n\n{esc(text[:1500])}",
                                parse_mode="HTML", reply_markup=ikb([[("✍️ Відповісти", f"sup:r:{uid}")]]))

async def support_reply(uid, text, admin_name="", source="admin"):
    """Відповідь адміна: зберігається в переписці й надсилається людині від імені бота."""
    await support_add(uid, text, "admin", source)
    return await safe_send_message(uid, f"💬 <b>Відповідь підтримки</b>\n\n{esc(text)}", parse_mode="HTML",
                                   reply_markup=ikb([[open_btn("💬 Відкрити чат підтримки", "support", "sup:open")]]))

async def support_messages(uid, limit=100):
    ref = db.collection("support").document(str(uid))
    docs = await asyncio.to_thread(lambda: list(ref.collection("msgs").get()))
    rows = sorted(((d.to_dict() or {}) for d in docs), key=lambda r: r.get("ts", 0))[-limit:]
    return [{"from": r.get("from"), "text": r.get("text", ""), "ts": int(float(r.get("ts") or 0) * 1000)} for r in rows]

async def ask_support(message: types.Message, state: FSMContext, about_pay=False):
    await state.set_state(Support.message)
    await message.answer(
        ("💬 <b>Підтримка з оплати</b>\n\nОпиши проблему одним повідомленням: коли оплачував(-ла), скільки зірок і що пішло не так.\n"
         "Якщо Преміум не активувався — ми нарахуємо дні або повернемо зірки." if about_pay else
         "💬 <b>Підтримка</b>\n\nНапиши одним повідомленням, що сталося або що підказати. Адміністратор відповість тут, у чаті з ботом."),
        parse_mode="HTML", reply_markup=ikb([[("✖️ Скасувати", "sup:cancel")]]))

@dp.message(Command("paysupport"))
async def cmd_paysupport(message: types.Message, state: FSMContext):
    await ask_support(message, state, about_pay=True)

@dp.message(Command("support"))
async def cmd_support(message: types.Message, state: FSMContext):
    await ask_support(message, state)

@dp.message(Support.message, F.text, ~F.text.startswith("/"))
async def support_user_text(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        await support_from_user(message.from_user.id, message.text, "chat", message.from_user)
    except ValueError:
        return
    await message.answer("✅ Повідомлення надіслано в підтримку. Відповідь прийде сюди, у чат з ботом.")

@dp.message(Support.message)
async def support_user_other(message: types.Message, state: FSMContext):
    await message.answer("Напиши, будь ласка, текстом 🙂 (фото поки не приймаємо)")

@dp.callback_query(F.data.startswith("sup:"))
async def support_callback(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    if parts[1] == "cancel":
        await state.clear()
        await callback.answer("Скасовано")
        try:
            await callback.message.delete()
        except Exception:
            pass
        return
    if parts[1] == "open":
        await callback.answer()
        return await ask_support(callback.message, state)
    if parts[1] == "r" and str(callback.from_user.id) in admin_ids():
        await callback.answer()
        await state.set_state(Support.reply)
        await state.update_data(reply_to=parts[2])
        return await callback.message.answer(f"✍️ Напиши відповідь для <code>{parts[2]}</code> одним повідомленням (або /cancel):",
                                             parse_mode="HTML")
    await callback.answer()

@dp.message(Support.reply, F.text, ~F.text.startswith("/"))
async def support_admin_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    if str(message.from_user.id) not in admin_ids():
        return await message.answer("Скасовано.")
    ok = await support_reply(data.get("reply_to"), message.text, source="telegram")
    await message.answer("✅ Відповідь надіслано." if ok else "⚠️ Збережено в адмінці, але людина заблокувала бота — не доставлено.")

# =========================================================
# STATES
# =========================================================
class Registration(StatesGroup):
    captcha = State()
    waiting_for_name = State()
    waiting_for_age = State()
    waiting_for_country = State()
    waiting_for_city = State()
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
    city = State()

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
DONE_MEDIA = "✅ Це все, зберегти"
MAX_MEDIA = 3
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

def app_button():
    url = app_url()
    return types.KeyboardButton(text="📱 Відкрити застосунок", web_app=types.WebAppInfo(url=url)) if url.startswith("https://") else None

def app_on():
    return app_url().startswith("https://")

def app_only():
    return app_on() and SETTINGS.get("app_only", True) is not False

# що лишається в чаті в режимі «лише застосунок»
APP_ONLY_COMMANDS = {"/start", "/support", "/paysupport", "/terms", "/help", "/myid", "/admin", "/premium", "/cancel"}
APP_ONLY_CALLBACKS = ("sup:", "buy:", "adm_", "x:del", "noop")
APP_ONLY_TEXT = "❤️ Анкети, лайки, симпатії й Преміум — у застосунку FlirtUA 👇"

async def send_app_only(chat_id, tab="feed", text=APP_ONLY_TEXT):
    await safe_send_message(chat_id, text, reply_markup=ikb([[open_btn("❤️ Відкрити FlirtUA", tab)]]))

def get_main_menu():
    if app_on():
        # усе — у застосунку. Кнопку під полем вводу прибираємо: застосунок з такої кнопки Telegram відкриває
        # без даних користувача (порожній екран). Відкривається кнопкою «❤️ Знайомства» зліва від поля вводу.
        return types.ReplyKeyboardRemove()
    kb = [
        [types.KeyboardButton(text=B_FEED)],
        [types.KeyboardButton(text=B_LIKES), types.KeyboardButton(text=B_ME)],
        [types.KeyboardButton(text=B_FILTERS), types.KeyboardButton(text=B_INVITE)],
        [types.KeyboardButton(text=B_MORE)],
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, is_persistent=True, input_field_placeholder="Обери дію 👇")

def reply_kb(rows, one_time=True):
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True, one_time_keyboard=one_time)

def _btn(t, d):
    if d.startswith("url:"):
        return types.InlineKeyboardButton(text=t, url=d[4:])
    if d.startswith("app:"):
        return types.InlineKeyboardButton(text=t, web_app=types.WebAppInfo(url=d[4:]))
    return types.InlineKeyboardButton(text=t, callback_data=d)

def ikb(rows):
    return types.InlineKeyboardMarkup(inline_keyboard=[[_btn(t, d) for t, d in r] for r in rows])

def open_btn(text, tab="feed", fallback="f:more"):
    """Кнопка, що відкриває застосунок на потрібній вкладці (або стрічку в чаті, якщо застосунок не ввімкнено)."""
    url = app_url()
    return (text, f"app:{url}#{tab}") if url.startswith("https://") else (text, fallback)

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

def media_list(p):
    m = p.get("media") or []
    return m if m else ([{"t": "photo", "id": p["photo"]}] if p.get("photo") else [])

# текст над фото, кнопки одразу під ним — фото займає максимум екрана
def input_media(m, caption):
    cls = types.InputMediaVideo if m.get("t") == "video" else types.InputMediaPhoto
    return cls(media=m["id"], caption=caption, parse_mode="HTML", show_caption_above_media=True)

async def send_media(chat_id, m, caption, kb=None, silent=True):
    for _ in range(3):
        try:
            fn = bot.send_video if m.get("t") == "video" else bot.send_photo
            return await fn(chat_id, m["id"], caption=caption, parse_mode="HTML", reply_markup=kb,
                            disable_notification=silent, show_caption_above_media=True)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            return None
        except Exception as e:
            logging.error(f"send_media: {e}")
            await asyncio.sleep(1)
    return None

def media_of_message(message):
    if message.photo:
        return {"t": "photo", "id": message.photo[-1].file_id}
    if message.video:
        thumb = message.video.thumbnail.file_id if message.video.thumbnail else None
        return {"t": "video", "id": message.video.file_id, "thumb": thumb}
    return None

def media_fields(media):
    """Поля анкети: media — до 3 фото/відео; photo — перше фото (або обкладинка відео) для адмінки й повідомлень про симпатію."""
    first = next((m["id"] for m in media if m["t"] == "photo"), None) or next((m.get("thumb") for m in media if m.get("thumb")), None) or media[0]["id"]
    return {"media": media[:MAX_MEDIA], "photo": first}

def card_text(p, me=None, incoming=False, own=False, note=""):
    lines = []
    if note:
        lines.append(f"💌 <b>Суперлайк:</b> «{esc(note)}»")
    elif incoming:
        lines.append("❤️ <b>Ти сподобався(-лась) цій людині!</b>")
    # компактно, як у Дайвінчику: «Оля, 24, Аліканте – текст», щоб фото було великим
    where = esc(p.get("city") or p.get("country"))
    tags = []
    d = dist_km(me, p) if not own else None
    if d:
        tags.append(f"📍 {d} км")
    if p.get("hometown"):
        tags.append(f"🏠 {esc(p['hometown'])}" + (" 🤝" if not own and same_home(me, p) else ""))
    if is_free(p):
        tags.append("☕️ вільний(-а) сьогодні")
    about = str(p.get("about") or "").strip()
    limit = 700 if own else 260
    if len(about) > limit:
        about = about[:limit].rsplit(" ", 1)[0] + "…"
    head = f"<b>{esc(p.get('name'))}, {esc(p.get('age'))}</b>, {where}"
    lines.append(head + (f" – {esc(about)}" if about else ""))
    if tags:
        lines.append(" · ".join(tags))
    return "\n".join(lines)[:1024]

def feed_kb(cid, incoming, n=1, idx=0):
    i = "1" if incoming else "0"
    rows = [[("❤️", f"f:l:{cid}:{i}"), ("👎", f"f:n:{cid}:{i}")]]
    if n > 1:
        rows.insert(0, [(f"📷 {idx + 1}/{n} ▸", f"f:p:{cid}:{i}:{(idx + 1) % n}")])
    return ikb(rows)

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

# повноекранні ефекти Telegram (лише в особистих чатах); якщо ефект недоступний — шлемо без нього
EFFECT_PARTY = "5046509860389126442"   # 🎉
EFFECT_HEART = "5159385139981059251"   # ❤️

async def send_with_effect(kind, chat_id, effect, **kw):
    # ефекти вимкнено (без анімацій) — звичайне повідомлення
    if kind == "photo":
        return await safe_send_photo(chat_id, kw.pop("photo"), **kw)
    return await safe_send_message(chat_id, kw.pop("text"), **kw)

STAMPS = {
    "like": ("❤️ ❤️ ❤️\n\n<b>ЛАЙК!</b> {name} дізнається, що сподобався(-лась) тобі 😉", "❤️ Надіслано"),
    "match": ("🎉 🎉 🎉\n\n<b>ВЗАЄМНО!</b> Ти і {name} сподобались одне одному", "🎉 Це взаємність!"),
    "next": ("👋 <b>Далі…</b>", "⏳"),
}

async def stamp(chat_id, mid, kind, name):
    """Коротка «анімація» на тій самій картці: штамп поверх фото, поки вантажиться наступна."""
    text, btn = STAMPS[kind]
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=mid, caption=text.format(name=esc(name)), parse_mode="HTML",
                                       show_caption_above_media=True, reply_markup=ikb([[(btn, "noop")]]))
    except Exception:
        pass

async def show_next(user_id, chat_id, msg=None, mid=None, anim=None, anim_name=""):
    """Показує наступну анкету. Якщо є повідомлення з попередньою — міняє його на місці (без нового повідомлення і звуку)."""
    if msg and msg.photo:
        mid = msg.message_id
    try:
        if anim and mid:
            # штамп і пошук наступної анкети йдуть одночасно; штамп видно щонайменше ~0.7 с
            t0 = time.monotonic()
            await stamp(chat_id, mid, anim, anim_name)
            p, incoming, me = await find_next(user_id)
            wait = (0.9 if anim != "next" else 0.45) - (time.monotonic() - t0)
            if wait > 0:
                await asyncio.sleep(wait)
        else:
            p, incoming, me = await find_next(user_id)
    except Exception as e:
        logging.error(f"find_next: {e}")
        return await safe_send_message(chat_id, "⚠️ Не вдалося завантажити анкети. Спробуй ще раз за хвилину.", reply_markup=get_main_menu())
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
    media = media_list(p)
    # у стрічці — лише фото і дві кнопки, без тексту (анкету можна прочитати в «☰ Ще» → «ℹ️ Про …»)
    caption, kb = None, feed_kb(p["tg_id"], incoming, len(media))
    CURRENT[str(user_id)] = {"cid": p["tg_id"], "i": incoming, "name": p.get("name", ""), "chat": chat_id, "mid": None, "note": p.get("_note", "")}
    if mid:
        try:
            await bot.edit_message_media(chat_id=chat_id, message_id=mid, media=input_media(media[0], caption), reply_markup=kb)
            CURRENT[str(user_id)]["mid"] = mid
            return
        except Exception as e:
            logging.info(f"edit_media fallback: {e}")
            try:
                await bot.delete_message(chat_id, mid)
            except Exception:
                pass
    sent = await send_media(chat_id, media[0], caption, kb)
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
    try:
        n = len(list(await asyncio.to_thread(lambda: user_ref(cid).collection("likes").limit(50).get())))
    except Exception:
        n = 1
    await safe_send_message(cid, f"❤️ Ти сподобався(-лась) <b>{people(n)}</b>! Показати?", parse_mode="HTML",
                            reply_markup=ikb([[open_btn("👀 Показати"), ("💤 Не зараз", "x:del")]]))

def people(n):
    n = max(1, int(n))
    word = "людині" if n % 10 == 1 and n % 100 != 11 else "людям"
    return f"{n}{'+' if n >= 50 else ''} {word}"

@dp.callback_query(F.data == "x:del")
async def x_delete(callback: types.CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass

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
        text = (f"🎉 <b>Є взаємна симпатія!</b>\n\nТи і <b>{esc(who.get('name'))}</b> сподобались одне одному. Сподіваюсь, добре проведете час 😉\n\n" +
                (f"Починай спілкуватися 👉 @{esc(who['username'])}" if link else f"Починай спілкуватися 👉 <a href=\"tg://user?id={who_id}\">{esc(who.get('name'))}</a>"))
        if note and to == cid:
            text += f"\n\n💌 Повідомлення: «{esc(note)}»"
        kb = ikb([[("💬 Написати", "url:" + link)]]) if link else None
        await send_with_effect("photo", to, EFFECT_PARTY, photo=who.get("photo"), caption=text, parse_mode="HTML", reply_markup=kb)
    if not me.get("username"):
        await safe_send_message(user_id, "💡 У тебе немає нікнейму в Telegram, тому тобі складніше написати. Додай його: Налаштування → Ім'я користувача.")

# =========================================================
# FEED CALLBACKS (кнопки під анкетою)
# =========================================================
# =========================================================
# СПІЛЬНІ ДІЇ (бот і застосунок у Telegram)
# =========================================================
DAY_S = 86400

async def mark_seen(uid, cid):
    await firebase_set(user_ref(uid).collection("seen").document(str(cid)), {"ts": firestore.SERVER_TIMESTAMP})

async def do_like(user_id, cid, incoming):
    """Лайк. Повертає True, якщо вийшла взаємна симпатія."""
    user_id, cid = str(user_id), str(cid)
    match = bool(incoming)
    if incoming:
        await firebase_delete(user_ref(user_id).collection("likes").document(cid))
    else:
        # якщо ця людина вже лайкнула мене — одразу взаємність
        mine = await firebase_get(user_ref(user_id).collection("likes").document(cid))
        if mine and mine.exists:
            await firebase_delete(mine.reference)
            match = True
        else:
            await firebase_set(user_ref(cid).collection("likes").document(user_id), {"ts": firestore.SERVER_TIMESTAMP})
            asyncio.create_task(notify_liked(cid))
    await mark_seen(user_id, cid)
    if match:
        await make_match(user_id, cid)
    return match

async def do_skip(user_id, cid, incoming):
    if incoming:
        await firebase_delete(user_ref(user_id).collection("likes").document(str(cid)))
    await mark_seen(user_id, cid)

async def superlike_wait(uid):
    me = await get_user(uid) or {}
    return max(0, int(me.get("superlike_at") or 0) + DAY_S - time.time())

async def do_superlike(uid, cid, incoming, text):
    """Суперлайк з повідомленням (раз на добу). Повертає True, якщо вийшла взаємність."""
    uid, cid = str(uid), str(cid)
    await update_user(uid, {"superlike_at": int(time.time())})
    await mark_seen(uid, cid)
    mine = None if incoming else await firebase_get(user_ref(uid).collection("likes").document(cid))
    if incoming or (mine and mine.exists):
        await firebase_delete(user_ref(uid).collection("likes").document(cid))
        await make_match(uid, cid, note=text)
        return True
    await firebase_set(user_ref(cid).collection("likes").document(uid), {"ts": firestore.SERVER_TIMESTAMP, "msg": text})
    await safe_send_message(cid, f"💌 Тобі надіслали <b>суперлайк</b>: «{esc(text)}»\n\nВідкрий анкети — ця людина буде першою.", parse_mode="HTML",
                            reply_markup=ikb([[open_btn("👀 Подивитися")]]))
    return False

async def do_report(uid, cid, key):
    uid, cid = str(uid), str(cid)
    reason = REPORT_REASONS.get(key, REPORT_REASONS["other"])
    await asyncio.to_thread(db.collection("reports").add, {
        "reporter_id": uid, "reported_user_id": cid, "reason": reason,
        "timestamp": firestore.SERVER_TIMESTAMP, "status": "new"})
    await mark_seen(uid, cid)
    await firebase_delete(user_ref(uid).collection("likes").document(cid))
    asyncio.create_task(after_report(cid, uid))

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
        if act == "i":
            await callback.answer()
            p, me = await get_user(parts[2]), await get_user(user_id)
            if not p:
                return
            cur = CURRENT.get(user_id) or {}
            note = cur.get("note", "") if cur.get("cid") == parts[2] else ""
            try:
                return await msg.edit_text(card_text(p, me, parts[3] == "1", note=note), parse_mode="HTML",
                                           reply_markup=ikb([[("✖️ Закрити", "x:del")]]))
            except Exception:
                return
        if act == "p":
            await callback.answer()
            p, me = await get_user(parts[2]), await get_user(user_id)
            if not p:
                return
            media, idx = media_list(p), int(parts[4])
            idx = idx if idx < len(media) else 0
            cur = CURRENT.get(user_id) or {}
            note = cur.get("note", "") if cur.get("cid") == parts[2] else ""
            return await msg.edit_media(input_media(media[idx], None),
                                        reply_markup=feed_kb(parts[2], parts[3] == "1", len(media), idx))
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
            await do_report(user_id, cid, parts[3])
            await callback.answer("🚨 Скаргу прийнято. Модератор перевірить анкету.", show_alert=True)
            try:
                await msg.delete()  # повідомлення меню зі скаргою
            except Exception:
                pass
            cur = CURRENT.get(user_id) or {}
            return await show_next(user_id, msg.chat.id, mid=cur.get("mid") if cur.get("cid") == cid else None)
        incoming = parts[3] == "1"
        if act == "sl":
            left = await superlike_wait(user_id)
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
        name = (CURRENT.get(user_id) or {}).get("name", "") if (CURRENT.get(user_id) or {}).get("cid") == cid else ""
        if act == "l":
            await callback.answer("❤️")
            await do_like(user_id, cid, incoming)
            return await show_next(user_id, msg.chat.id, msg)
        if act == "n":
            await callback.answer()
            await do_skip(user_id, cid, incoming)
            return await show_next(user_id, msg.chat.id, msg)
        await callback.answer()
    except Exception as e:
        logging.error(f"feed_callback {callback.data}: {e}")
        try:
            await callback.answer("⚠️ Помилка, спробуй ще раз")
        except Exception:
            pass


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
    await do_superlike(uid, cid, data.get("sl_incoming"), text)
    await send_with_effect("text", message.chat.id, EFFECT_HEART, text="💌 <b>Суперлайк надіслано!</b>", parse_mode="HTML", reply_markup=get_main_menu())
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
            if app_only():
                await message.answer(SETTINGS["welcome_back"], reply_markup=types.ReplyKeyboardRemove())
                return await message.answer("Анкети, лайки й симпатії — у застосунку 👇", reply_markup=ikb([[open_btn("❤️ Відкрити FlirtUA", "feed")]]))
            if app_on():
                await message.answer(SETTINGS["welcome_back"], reply_markup=get_main_menu())  # прибирає стару клавіатуру
                return await message.answer("Анкети, лайки й симпатії — у застосунку 👇\nАбо кнопка «❤️ Знайомства» зліва від поля вводу.",
                                            reply_markup=ikb([[open_btn("❤️ Відкрити знайомства", "feed")]]))
            return await message.answer(SETTINGS["welcome_back"], reply_markup=get_main_menu())
        await state.clear()
        if referrer and referrer != user_id:
            await state.update_data(referrer=referrer)
            # якщо людина створить анкету в застосунку — бонус за запрошення теж зарахується
            await firebase_set(db.collection("pending_ref").document(user_id), {"ref": referrer, "ts": int(time.time())})
        if app_only():
            await message.answer("👋", reply_markup=types.ReplyKeyboardRemove())
            await message.answer(SETTINGS["onboarding"], parse_mode="HTML", reply_markup=ikb([[open_btn("❤️ Створити анкету", "feed")]]))
            return
        if app_on():
            await message.answer("👋 Привіт!", reply_markup=get_main_menu())
            await message.answer(SETTINGS["onboarding"], parse_mode="HTML", reply_markup=ikb([
                [open_btn("❤️ Створити анкету", "feed")],
                [("💬 Заповнити в чаті", "reg:chat")]]))
            return
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
    await message.answer("🏙 <b>З якого ти міста?</b>\n\nНапиши місто, де живеш зараз, наприклад: Аліканте", parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Registration.waiting_for_city)

@dp.message(Registration.waiting_for_city)
async def process_city(message: types.Message, state: FSMContext):
    city = clean_town(message.text)
    if not city:
        return await message.answer("❌ Напиши назву міста текстом")
    await state.update_data(city=city)
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
    PENDING_MEDIA.pop(str(message.from_user.id), None)
    await message.answer(MEDIA_PROMPT, parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Registration.waiting_for_photo)

MEDIA_PROMPT = ("📸 <b>Надішли до 3 фото або відео</b> (до 60 с)\n\n"
                "Можна одразу кількома. Вертикальні фото виглядають найкраще. Обличчя має бути добре видно — анкети з чужими фото видаляються.\n"
                "📎 Скріпка → Фото або Відео.")
PENDING_MEDIA = {}   # uid -> фото/відео, які людина надсилає зараз (альбом приходить кількома повідомленнями)
_media_ack = {}

async def collect_media(message, state, on_done):
    uid = str(message.from_user.id)
    m = media_of_message(message)
    if m and m["t"] == "video" and (message.video.duration or 0) > 60:
        return await message.answer("❌ Відео до 60 секунд")
    lst = PENDING_MEDIA.setdefault(uid, [])
    if m and len(lst) < MAX_MEDIA:
        lst.append(m)
    if len(lst) >= MAX_MEDIA:
        if _media_ack.get(uid) != "done":
            _media_ack[uid] = "done"
            await on_done(message, state, PENDING_MEDIA.pop(uid))
            _media_ack.pop(uid, None)
        return
    if _media_ack.get(uid):  # відповідаємо один раз на весь альбом
        return
    _media_ack[uid] = "wait"
    await asyncio.sleep(1.2)
    if _media_ack.get(uid) != "wait":
        return
    _media_ack.pop(uid, None)
    n = len(PENDING_MEDIA.get(uid, []))
    if n and n < MAX_MEDIA:
        await message.answer(f"✅ Додано {n} з {MAX_MEDIA}. Надішли ще або натисни «{DONE_MEDIA}»", reply_markup=reply_kb([[DONE_MEDIA]]))

async def media_done_button(message, state, on_done):
    uid = str(message.from_user.id)
    if message.text == DONE_MEDIA and PENDING_MEDIA.get(uid):
        return await on_done(message, state, PENDING_MEDIA.pop(uid))
    await message.answer(MEDIA_PROMPT, parse_mode="HTML")

async def reg_media_done(message, state, media):
    await state.update_data(media=media)
    await message.answer("📝 <b>Розкажи про себе</b>\n\nХто ти, кого хочеш знайти, чим пропонуєш зайнятися. Це допоможе краще підібрати тобі пару 🙂",
                         parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Registration.waiting_for_about)

@dp.message(Registration.waiting_for_photo, F.photo | F.video)
async def process_photo(message: types.Message, state: FSMContext):
    await collect_media(message, state, reg_media_done)

@dp.message(Registration.waiting_for_photo)
async def photo_error(message: types.Message, state: FSMContext):
    await media_done_button(message, state, reg_media_done)

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
        core = {
            "username": message.from_user.username or "",
            "name": data["name"],
            "age": data["age"],
            "country": data["country"],
            "city": data.get("city", ""),
            "gender": data["gender"],
            "search": data["search"],
            "about": about[:700],
            **media_fields(data["media"]),
        }
        if data.get("redo"):
            # «заповнити заново»: лайки, Преміум і історія зберігаються
            await update_user(user_id, core)
            await state.set_state(Registration.waiting_for_hometown)
            return await message.answer("🏠 <b>Звідки ти родом в Україні?</b>", parse_mode="HTML", reply_markup=hometown_kb())
        profile = {
            "tg_id": user_id,
            **core,
            "registered_at": firestore.SERVER_TIMESTAMP,
            "search_filters": {"country": None, "min_age": None, "max_age": None},
            "lat": None,
            "lon": None,
            "last_active": firestore.SERVER_TIMESTAMP,
            "referred_by": referrer or "",
        }
        if referrer:
            profile["likes_view_until"] = now + premium_minutes() * 60  # бонус і новому користувачу
        paid = await paid_premium_until(user_id)  # оплачений Преміум повертається в нову анкету
        if paid > int(profile.get("likes_view_until") or 0):
            profile["likes_view_until"] = paid
        await firebase_set(user_ref(user_id), profile)

        if referrer:
            await reward_referrer(referrer)

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

async def reward_referrer(referrer):
    ref = await get_user(referrer)
    if not ref:
        return
    until = max(int(time.time()), int(ref.get("likes_view_until") or 0)) + premium_minutes() * 60
    await update_user(referrer, {"likes_view_until": until})
    await safe_send_message(referrer, f"🎉 Твій друг зареєструвався! «Хто мене лайкнув?» відкрито ще на {premium_minutes()} хв.",
                            reply_markup=ikb([[open_btn("❤️ Подивитися", "likes", "lk:show")]]))

async def finish_registration(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)
    p = await get_user(user_id)
    await message.answer("Так виглядає твоя анкета:", reply_markup=get_main_menu())
    if p:
        await send_media(message.chat.id, media_list(p)[0], card_text(p, own=True), silent=False)
        await message.answer("Все вірно?", reply_markup=ikb([[("✅ Так, все вірно", "reg:ok"), ("✏️ Змінити анкету", "reg:edit")]]))
    if p and p.get("referred_by") and p.get("likes_view_until", 0) > time.time():
        await message.answer(f"🎁 Тебе запросив друг — «Хто мене лайкнув?» відкрито на {premium_minutes()} хв.")
    if not message.from_user.username:
        await message.answer("💡 Додай нікнейм у Telegram (Налаштування → Ім'я користувача), щоб після взаємної симпатії тобі могли написати.")

@dp.callback_query(F.data == "reg:chat")
async def reg_in_chat(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if await get_user(callback.from_user.id):
        return await callback.message.answer(SETTINGS["welcome_back"], reply_markup=get_main_menu())
    a, b = random.randint(1, 9), random.randint(1, 9)
    await state.update_data(captcha_answer=a + b)
    await state.set_state(Registration.captcha)
    await callback.message.answer(f"🤖 Перевірка, що ти не бот\n\n<b>{a} + {b} = ?</b>", parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())

@dp.callback_query(F.data.startswith("reg:"))
async def reg_confirm(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    uid = str(callback.from_user.id)
    if callback.data == "reg:edit":
        return await send_me(callback.message.chat.id, uid)
    await callback.message.answer("🔥 Чудово! Починаємо 👇")
    await show_next(uid, callback.message.chat.id)

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
        [("🏙 Місто", "me:city"), ("🔄 Заповнити заново", "me:redo")],
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
    media = media_list(p)
    if len(media) > 1:
        cap += f"\n📷 У анкеті {len(media)} фото/відео"
    await send_media(chat_id, media[0], cap[:1024], me_kb(p), silent=False)

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
    "photo": (EditProfile.photo, "📷 Надішли до 3 нових фото або відео — вони замінять поточні"),
    "city": (EditProfile.city, "🏙 З якого ти міста?"),
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
    if act[1] == "redo":
        await state.clear()
        await state.update_data(redo=True)
        await state.set_state(Registration.waiting_for_name)
        name = (callback.from_user.first_name or "").strip()[:30]
        return await callback.message.answer("🔄 Заповнимо анкету заново (лайки й симпатії збережуться).\n\n👋 <b>Як тебе звати?</b>", parse_mode="HTML",
                                             reply_markup=reply_kb([[name]]) if len(name) >= 2 else types.ReplyKeyboardRemove())
    if act[1] in EDIT_PROMPTS:
        if act[1] == "photo":
            PENDING_MEDIA.pop(uid, None)
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

async def edit_media_done(message, state, media):
    await edit_done(message, state, media_fields(media))

@dp.message(EditProfile.photo, F.photo | F.video)
async def edit_photo(message: types.Message, state: FSMContext):
    await collect_media(message, state, edit_media_done)

@dp.message(EditProfile.photo)
async def edit_photo_err(message: types.Message, state: FSMContext):
    await media_done_button(message, state, edit_media_done)

@dp.message(EditProfile.city)
async def edit_city(message: types.Message, state: FSMContext):
    city = clean_town(message.text)
    if not city:
        return await message.answer("❌ Напиши назву міста")
    await edit_done(message, state, {"city": city})

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
    await delete_profile(callback.from_user.id)
    await callback.message.answer("❌ Анкету повністю видалено. Захочеш повернутися — натисни /start.", reply_markup=types.ReplyKeyboardRemove())

async def delete_profile(uid):
    ref = user_ref(uid)
    try:
        for sub in ("seen", "likes"):
            for doc in list(await asyncio.to_thread(lambda: ref.collection(sub).get())):
                await firebase_delete(doc.reference)
    except Exception as e:
        logging.error(f"Subcollection cleanup error: {e}")
    await firebase_delete(ref)

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
        link = ref_link(await bot_username(), uid)
        return await safe_send_message(chat_id,
            f"❤️ <b>Тебе лайкнули: {n}{'+' if n == 20 else ''}</b>\n\n"
            "Ці люди покажуться першими, коли відкриєш анкети 👥\n\n"
            "🔓 Щоб одразу побачити всіх списком — оформи 💎 Преміум за зірки Telegram"
            f" або запроси друга: ви обоє отримаєте Преміум на {premium_minutes()} хв.",
            parse_mode="HTML", reply_markup=ikb(([[(f"💎 Преміум — від ≈{eur(min(p['stars'] for p in plans()))} на тиждень" if any(p['days'] == 7 for p in plans()) else f"💎 Преміум — від ≈{eur(min(p['stars'] for p in plans()))}", "premium")]] if payments_on() else [])
                                                + [[("👥 Дивитися анкети", "f:more")], [("🎁 Запросити друга", "url:" + share_url(link))]]))
    await safe_send_message(chat_id, f"💎 <b>Тебе лайкнули: {n}</b>", parse_mode="HTML")
    for like in likes[:10]:
        p = await get_user(like.id)
        if not visible(p):
            continue
        await send_media(chat_id, media_list(p)[0], card_text(p, me, note=(like.to_dict() or {}).get("msg", "")),
                         ikb([[("❤️ Взаємно", f"lk:y:{like.id}"), ("👎", f"lk:n:{like.id}")]]))
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
    link = ref_link(await bot_username(), user_id)
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
        text += f"\n💎 Преміум активний до {fmt_until(me['likes_view_until'])}"
    rows = [[("📤 Надіслати другу", "url:" + share_url(link))]] + ([[("💎 Купити Преміум за ⭐", "premium")]] if payments_on() else [])
    await message.answer(text, parse_mode="HTML", reply_markup=ikb(rows))

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
        rows += [[(f"ℹ️ Про {name}", f"f:i:{cur['cid']}:{i}")], [(f"💌 Суперлайк: {name}", f"f:sl:{cur['cid']}:{i}")], [(f"🚨 Скарга: {name}", f"f:r:{cur['cid']}:{i}")]]
    await message.answer("☰ <b>Ще</b>", parse_mode="HTML", reply_markup=ikb(rows + [
        [("☕️ Вільний(-а) сьогодні", "me:coffee")],
        [("💎 Преміум", "premium"), ("💬 Підтримка", "sup:open")],
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
# НАГАДУВАННЯ (як у Дайвінчику): «ти сподобався N людям», «з'явились нові анкети»
# =========================================================
def _ts(v):
    try:
        return v.timestamp() if hasattr(v, "timestamp") else float(v or 0)
    except Exception:
        return 0.0

async def nudge_once(limit=300):
    now = time.time()
    docs = await asyncio.to_thread(lambda: list(db.collection("users").stream()))
    sent = 0
    for d in docs:
        if sent >= limit:
            break
        u = d.to_dict() or {}
        if u.get("banned") or u.get("blocked_bot") or u.get("paused") or d.id in BANNED:
            continue
        idle = now - (_ts(u.get("last_active")) or now)
        last = float(u.get("last_nudge") or 0)
        if idle < 86400 or now - last < 2 * 86400:
            continue
        try:
            n = len(list(await asyncio.to_thread(lambda: user_ref(d.id).collection("likes").limit(50).get())))
        except Exception:
            n = 0
        if n:
            text, kb = f"❤️ Поки тебе не було, ти сподобався(-лась) <b>{people(n)}</b>! Подивимось?", ikb([[open_btn("👀 Показати")]])
        elif idle >= 4 * 86400 and now - last >= 7 * 86400:
            text, kb = (f"👋 Давно не бачились! У {esc(u.get('city') or u.get('country') or 'твоєму місті')} з'явились нові анкети. Подивимось?",
                        ikb([[open_btn("👥 Дивитися анкети")]]))
        else:
            continue
        try:
            await bot.send_message(d.id, text, parse_mode="HTML", reply_markup=kb)
            await update_user(d.id, {"last_nudge": int(now)})
            sent += 1
        except TelegramForbiddenError:
            await update_user(d.id, {"blocked_bot": True})
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            logging.info(f"nudge {d.id}: {e}")
        await asyncio.sleep(0.06)
    return sent

async def nudge_loop():
    await asyncio.sleep(300)
    while True:
        try:
            # нагадуємо вдень, а не вночі (за часом Іспанії/Польщі/Німеччини)
            hour = time.gmtime().tm_hour
            if SETTINGS.get("nudges", True) and 8 <= hour <= 18:
                n = await nudge_once()
                if n:
                    logging.info(f"🔔 Нагадувань надіслано: {n}")
        except Exception as e:
            logging.error(f"nudge_loop: {e}")
        await asyncio.sleep(3600)

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
        asyncio.create_task(nudge_loop())
        mini = MiniApp(sys.modules[__name__])
        await start_admin(Ctx(db, bot, SETTINGS, save_settings, admin_ids, on_ban), extra=mini.setup)
        if app_on():
            try:
                await bot.set_chat_menu_button(menu_button=types.MenuButtonWebApp(text="❤️ Знайомства", web_app=types.WebAppInfo(url=app_url())))
                # те, що людина бачить у боті ще до /start, і в профілі бота
                await bot.set_my_description("❤️ Знайомства для українців в Іспанії, Польщі, Німеччині, Чехії та Італії.\n\n"
                                             "Анкети людей поруч на весь екран, земляки, «вільний сьогодні» на каву. Натисни «Відкрити» або «Почати» 👇")
                await bot.set_my_short_description("❤️ Знайомства для українців за кордоном. Відкрий застосунок 👇")
                logging.info(f"📱 Застосунок: {app_url()}" + (" (головний — посилання ?startapp відкривають його одразу)" if main_app() else ""))
            except Exception as e:
                logging.error(f"menu button: {e}")
        else:
            logging.warning("📱 Застосунок вимкнено: згенеруйте домен у Railway (Settings → Networking) або задайте WEBAPP_URL")
    
    try:
        if app_only():
            await bot.set_my_commands([
                types.BotCommand(command="start", description="❤️ Відкрити знайомства"),
                types.BotCommand(command="support", description="💬 Підтримка"),
                types.BotCommand(command="terms", description="📄 Умови оплати"),
            ])
        else:
            await bot.set_my_commands([
                types.BotCommand(command="search", description="👥 Дивитися анкети"),
                types.BotCommand(command="likes", description="❤️ Мої лайки"),
                types.BotCommand(command="profile", description="👤 Моя анкета"),
                types.BotCommand(command="premium", description="💎 Преміум"),
                types.BotCommand(command="help", description="ℹ️ Як це працює"),
                types.BotCommand(command="terms", description="📄 Умови оплати"),
                types.BotCommand(command="support", description="💬 Написати в підтримку"),
                types.BotCommand(command="paysupport", description="💳 Підтримка з оплати"),
                types.BotCommand(command="start", description="🔄 Головне меню"),
            ])
    except Exception as e:
        logging.error(f"set_my_commands: {e}")
    print("🚀 Бот запускається...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
