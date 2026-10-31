"""Веб-админка бота: статистика, анкеты, жалобы, рассылка и настройки.

Работает в том же процессе, что и бот (aiohttp). Вход по паролю ADMIN_PASSWORD.
Адрес: https://<домен Railway>/admin
"""
import asyncio
import hashlib
import hmac
import logging
import os
import time
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone

from aiohttp import web
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest

HERE = os.path.dirname(os.path.abspath(__file__))
MEN = "Я Чоловік 👱‍♂️"
WOMEN = "Я Жінка 👩"
DAY = 86400


def ts(v):
    """Firestore Timestamp / datetime / секунды → миллисекунды (или None)."""
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        try:
            return int(v.timestamp() * 1000)
        except Exception:
            return None
    if isinstance(v, (int, float)):
        return int(v * 1000) if v < 1e11 else int(v)
    return None


class Ctx:
    """Всё, что админке нужно от бота."""

    def __init__(self, db, bot, settings, save_settings, admin_ids, on_ban=None):
        self.db = db
        self.bot = bot
        self.settings = settings          # dict, общий с ботом
        self.save_settings = save_settings  # async (dict) -> None
        self.admin_ids = admin_ids        # callable -> set[str]
        self.on_ban = on_ban or (lambda uid, banned: None)


class Admin:
    def __init__(self, ctx: Ctx):
        self.c = ctx
        self.password = os.getenv("ADMIN_PASSWORD", "")
        self.token = hmac.new(self.password.encode(), b"bot-admin-v1", hashlib.sha256).hexdigest() if self.password else ""
        self._users = []
        self._users_at = 0
        self._photos = OrderedDict()
        self._login_fails = defaultdict(list)
        self.cast = {"running": False}
        self._cast_stop = False

    # ---------- доступ ----------
    def authed(self, req):
        if not self.token:
            return False
        got = req.cookies.get("bot_admin", "")
        return hmac.compare_digest(got, self.token)

    @web.middleware
    async def guard(self, req, handler):
        p = req.path
        if p.startswith("/admin/api/") or p.startswith("/admin/photo/"):
            if not self.authed(req):
                return web.json_response({"error": "Нужно войти"}, status=401)
        try:
            return await handler(req)
        except web.HTTPException:
            raise
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            logging.exception("admin error")
            return web.json_response({"error": f"Ошибка сервера: {e}"}, status=500)

    async def page(self, req):
        with open(os.path.join(HERE, "admin.html"), encoding="utf-8") as f:
            html = f.read()
        return web.Response(text=html, content_type="text/html", headers={"cache-control": "no-store", "x-robots-tag": "noindex", "x-frame-options": "DENY"})

    async def login(self, req):
        ip = (req.headers.get("x-forwarded-for", "").split(",")[0].strip() or req.remote or "?")
        now = time.time()
        fails = [t for t in self._login_fails[ip] if now - t < 900]
        self._login_fails[ip] = fails
        if len(fails) >= 10:
            return web.json_response({"error": "Слишком много попыток. Подождите 15 минут."}, status=429)
        if not self.password:
            return web.json_response({"error": "Админка выключена: задайте ADMIN_PASSWORD в Railway → Variables."}, status=403)
        body = await req.json()
        if not hmac.compare_digest(str(body.get("password", "")).encode(), self.password.encode()):
            fails.append(now)
            return web.json_response({"error": "Неверный пароль"}, status=401)
        res = web.json_response({"ok": True})
        secure = req.headers.get("x-forwarded-proto", req.scheme) == "https"
        res.set_cookie("bot_admin", self.token, max_age=30 * DAY, httponly=True, samesite="Strict", secure=secure, path="/admin")
        return res

    async def logout(self, req):
        res = web.json_response({"ok": True})
        res.del_cookie("bot_admin", path="/admin")
        return res

    # ---------- данные ----------
    async def users(self, fresh=False):
        if fresh or time.time() - self._users_at > 30:
            docs = await asyncio.to_thread(lambda: list(self.c.db.collection("users").stream()))
            out = []
            for d in docs:
                u = d.to_dict() or {}
                u["id"] = d.id
                out.append(u)
            self._users = out
            self._users_at = time.time()
        return self._users

    def drop_cache(self):
        self._users_at = 0

    def card(self, u):
        now = time.time()
        return {
            "id": u["id"],
            "name": u.get("name", ""),
            "age": u.get("age"),
            "country": u.get("country", ""),
            "gender": "m" if u.get("gender") == MEN else "f" if u.get("gender") == WOMEN else "",
            "search": u.get("search", ""),
            "about": u.get("about", ""),
            "username": u.get("username", ""),
            "photo": u.get("photo", ""),
            "registered": ts(u.get("registered_at")),
            "active": ts(u.get("last_active")),
            "location": u.get("lat") is not None,
            "banned": bool(u.get("banned")),
            "hidden": bool(u.get("hidden")),
            "blocked": bool(u.get("blocked_bot")),
            "premium": int(u.get("likes_view_until") or 0) > now,
            "premiumUntil": int(u.get("likes_view_until") or 0) * 1000,
            "referrer": u.get("referred_by", ""),
            "reports": u.get("reports_count", 0),
            "hometown": u.get("hometown", ""),
            "city": u.get("city", ""),
            "mediaCount": len(u.get("media") or []) or (1 if u.get("photo") else 0),
            "free": int(u.get("free_until") or 0) > now,
            "paused": bool(u.get("paused")),
        }

    async def count(self, query, fallback):
        try:
            r = await asyncio.to_thread(lambda: query().count().get())
            return int(r[0][0].value)
        except Exception:
            try:
                return await asyncio.to_thread(fallback)
            except Exception:
                return None

    async def stats(self, req):
        us = await self.users(fresh=req.query.get("fresh") == "1")
        now = time.time() * 1000
        regs = [ts(u.get("registered_at")) or 0 for u in us]
        act = [ts(u.get("last_active")) or 0 for u in us]
        by_country = defaultdict(lambda: {"m": 0, "f": 0})
        for u in us:
            g = "m" if u.get("gender") == MEN else "f"
            by_country[u.get("country") or "—"][g] += 1
        days = []
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        for i in range(29, -1, -1):
            a = today - i * DAY * 1000
            days.append({"day": int(a), "n": sum(1 for r in regs if a <= r < a + DAY * 1000)})
        refs = defaultdict(int)
        for u in us:
            if u.get("referred_by"):
                refs[u["referred_by"]] += 1
        names = {u["id"]: u.get("name", "") for u in us}
        db = self.c.db
        reports_new = await self.count(lambda: db.collection("reports").where("status", "==", "new"),
                                       lambda: sum(1 for d in db.collection("reports").stream() if (d.to_dict() or {}).get("status") == "new"))
        # старые жалобы (до админки) без поля status тоже считаем новыми
        try:
            legacy = await asyncio.to_thread(lambda: sum(1 for d in db.collection("reports").stream() if "status" not in (d.to_dict() or {})))
            reports_new = (reports_new or 0) + legacy
        except Exception:
            pass
        matches = await self.count(lambda: db.collection("matches"), lambda: sum(1 for _ in db.collection("matches").stream()))
        likes = await self.count(lambda: db.collection_group("likes"), lambda: sum(1 for _ in db.collection_group("likes").stream()))
        return web.json_response({
            "total": len(us),
            "men": sum(1 for u in us if u.get("gender") == MEN),
            "women": sum(1 for u in us if u.get("gender") == WOMEN),
            "new1": sum(1 for r in regs if r > now - DAY * 1000),
            "new7": sum(1 for r in regs if r > now - 7 * DAY * 1000),
            "new30": sum(1 for r in regs if r > now - 30 * DAY * 1000),
            "active1": sum(1 for a in act if a > now - DAY * 1000),
            "active7": sum(1 for a in act if a > now - 7 * DAY * 1000),
            "location": sum(1 for u in us if u.get("lat") is not None),
            "banned": sum(1 for u in us if u.get("banned")),
            "hidden": sum(1 for u in us if u.get("hidden")),
            "blocked": sum(1 for u in us if u.get("blocked_bot")),
            "premium": sum(1 for u in us if int(u.get("likes_view_until") or 0) > time.time()),
            "referred": sum(1 for u in us if u.get("referred_by")),
            "minors": sum(1 for u in us if isinstance(u.get("age"), int) and u["age"] < 18),
            "reportsNew": reports_new,
            "matches": matches,
            "likesPending": likes,
            "countries": sorted(({"country": k, **v} for k, v in by_country.items()), key=lambda x: -(x["m"] + x["f"])),
            "days": days,
            "topRef": [{"id": k, "name": names.get(k, ""), "n": n} for k, n in sorted(refs.items(), key=lambda x: -x[1])[:10]],
            "adminIds": sorted(self.c.admin_ids()),
            **(await self.pay_summary()),
        })

    async def pay_summary(self):
        try:
            rows = [r for r in await self.load_payments() if r.get("status") == "paid"]
        except Exception:
            return {"stars30": None, "starsTotal": None, "payers": None}
        now = time.time()
        return {"stars30": sum(int(r.get("stars") or 0) for r in rows if int(r.get("ts") or 0) > now - 30 * DAY),
                "starsTotal": sum(int(r.get("stars") or 0) for r in rows), "payers": len({r.get("uid") for r in rows})}

    async def list_users(self, req):
        q = req.query
        us = await self.users(fresh=q.get("fresh") == "1")
        text = q.get("q", "").strip().lower().lstrip("@")
        out = []
        for u in us:
            if q.get("country") and u.get("country") != q["country"]:
                continue
            if q.get("gender") == "m" and u.get("gender") != MEN:
                continue
            if q.get("gender") == "f" and u.get("gender") != WOMEN:
                continue
            st = q.get("status", "")
            if st == "banned" and not u.get("banned"):
                continue
            if st == "hidden" and not u.get("hidden"):
                continue
            if st == "blocked" and not u.get("blocked_bot"):
                continue
            if st == "reported" and not u.get("reports_count"):
                continue
            if st == "premium" and not int(u.get("likes_view_until") or 0) > time.time():
                continue
            if st == "paused" and not u.get("paused"):
                continue
            if st == "minor" and not (isinstance(u.get("age"), int) and u["age"] < 18):
                continue
            if text and text not in " ".join([u["id"], str(u.get("name", "")), str(u.get("username", "")), str(u.get("about", ""))]).lower():
                continue
            out.append(u)
        key = {"active": lambda u: ts(u.get("last_active")) or 0, "reports": lambda u: u.get("reports_count", 0)}.get(q.get("sort"), lambda u: ts(u.get("registered_at")) or 0)
        out.sort(key=key, reverse=True)
        page = max(1, int(q.get("page", 1) or 1))
        per = 60
        return web.json_response({"total": len(out), "page": page, "per": per, "items": [self.card(u) for u in out[(page - 1) * per: page * per]]})

    async def user_detail(self, req):
        uid = req.match_info["id"]
        db = self.c.db
        ref = db.collection("users").document(uid)
        doc = await asyncio.to_thread(ref.get)
        if not doc.exists:
            return web.json_response({"error": "Анкета не найдена"}, status=404)
        u = doc.to_dict() or {}
        u["id"] = uid

        def extra():
            likes = [d.id for d in ref.collection("likes").stream()]
            seen = sum(1 for _ in ref.collection("seen").stream())
            reps = []
            for d in db.collection("reports").where("reported_user_id", "==", uid).stream():
                r = d.to_dict() or {}
                reps.append({"id": d.id, "reporter": r.get("reporter_id"), "reason": r.get("reason", ""), "status": r.get("status", "new"), "ts": ts(r.get("timestamp"))})
            made = sum(1 for _ in db.collection("reports").where("reporter_id", "==", uid).stream())
            m = []
            for f in ("a", "b"):
                for d in db.collection("matches").where(f, "==", uid).stream():
                    r = d.to_dict() or {}
                    m.append({"with": r.get("b" if f == "a" else "a"), "ts": ts(r.get("ts"))})
            return likes, seen, reps, made, m

        likes, seen, reps, made, m = await asyncio.to_thread(extra)
        names = {x["id"]: x.get("name", "") for x in await self.users()}
        card = self.card(u)
        card.update({
            "likedBy": [{"id": i, "name": names.get(i, "")} for i in likes[:50]],
            "likedByCount": len(likes),
            "seen": seen,
            "reportsList": sorted(reps, key=lambda r: -(r["ts"] or 0)),
            "reportsMade": made,
            "matches": [{**x, "name": names.get(x["with"], "")} for x in sorted(m, key=lambda r: -(r["ts"] or 0))[:50]],
            "filters": u.get("search_filters") or {},
            "referrerName": names.get(u.get("referred_by", ""), ""),
            "payments": [{"id": r["id"], "stars": r.get("stars"), "days": r.get("days"), "status": r.get("status"), "ts": int(r.get("ts") or 0) * 1000}
                         for r in await self.load_payments(uid)],
        })
        return web.json_response(card)

    async def delete_user(self, uid):
        db = self.c.db
        ref = db.collection("users").document(uid)

        def run():
            for sub in ("seen", "likes"):
                for d in ref.collection(sub).stream():
                    d.reference.delete()
            ref.delete()
        await asyncio.to_thread(run)

    async def set_fields(self, uid, fields):
        ref = self.c.db.collection("users").document(uid)
        await asyncio.to_thread(ref.update, fields)

    async def user_action(self, req):
        uid = req.match_info["id"]
        b = await req.json()
        a = b.get("action")
        bot = self.c.bot
        if a in ("ban", "unban"):
            await self.set_fields(uid, {"banned": a == "ban", "banned_at": int(time.time()) if a == "ban" else None})
            self.c.on_ban(uid, a == "ban")
            if a == "ban" and b.get("notify", True):
                await self.try_send(uid, "⛔️ Ваш акаунт заблоковано адміністрацією за порушення правил.")
            await self.resolve_reports(uid, "banned" if a == "ban" else None)
        elif a in ("hide", "unhide"):
            await self.set_fields(uid, {"hidden": a == "hide"})
            if a == "unhide":
                await self.resolve_reports(uid, "dismissed")
        elif a == "delete":
            if b.get("notify"):
                await self.try_send(uid, "❌ Вашу анкету видалено адміністрацією. Натисніть /start, щоб створити нову за правилами.")
            await self.delete_user(uid)
            await self.resolve_reports(uid, "deleted")
        elif a == "premium":
            minutes = max(1, min(60 * 24 * 365, int(b.get("minutes", 60))))
            until = int(time.time()) + minutes * 60
            await self.set_fields(uid, {"likes_view_until": until})
            if b.get("notify", True):
                await self.try_send(uid, f"💎 Вам подаровано Преміум: «Хто мене лайкнув?» відкрито до {datetime.fromtimestamp(until).strftime('%d.%m %H:%M')}.")
        elif a == "message":
            text = str(b.get("text", "")).strip()
            if not text:
                raise ValueError("Пустое сообщение")
            ok = await self.try_send(uid, "📩 <b>Повідомлення від адміністрації</b>\n\n" + text, html=True)
            if not ok:
                return web.json_response({"error": "Не доставлено: пользователь заблокировал бота или ошибка в разметке."}, status=400)
        else:
            raise ValueError("Неизвестное действие")
        self.drop_cache()
        return web.json_response({"ok": True})

    async def try_send(self, uid, text, html=False):
        try:
            await self.c.bot.send_message(uid, text, parse_mode="HTML" if html else None)
            return True
        except TelegramForbiddenError:
            try:
                await self.set_fields(uid, {"blocked_bot": True})
            except Exception:
                pass
            return False
        except Exception as e:  # noqa: BLE001
            logging.warning(f"admin send {uid}: {e}")
            return False

    # ---------- жалобы ----------
    async def resolve_reports(self, uid, status):
        if not status:
            return
        db = self.c.db

        def run():
            for d in db.collection("reports").where("reported_user_id", "==", uid).stream():
                r = d.to_dict() or {}
                if r.get("status", "new") == "new":
                    d.reference.update({"status": status, "handled_at": int(time.time())})
        await asyncio.to_thread(run)

    async def reports(self, req):
        db = self.c.db
        status = req.query.get("status", "new")
        docs = await asyncio.to_thread(lambda: list(db.collection("reports").stream()))
        users = {u["id"]: u for u in await self.users()}
        groups = {}
        for d in docs:
            r = d.to_dict() or {}
            st = r.get("status", "new")
            if status == "new" and st != "new":
                continue
            rid = r.get("reported_user_id", "")
            g = groups.setdefault(rid, {"user": None, "items": [], "last": 0, "reporters": set()})
            t = ts(r.get("timestamp")) or 0
            g["items"].append({"id": d.id, "reporter": r.get("reporter_id"), "reporterName": users.get(r.get("reporter_id"), {}).get("name", ""), "reason": r.get("reason", ""), "status": st, "ts": t})
            g["reporters"].add(r.get("reporter_id"))
            g["last"] = max(g["last"], t)
        out = []
        for rid, g in groups.items():
            u = users.get(rid)
            out.append({"userId": rid, "user": self.card(u) if u else None, "count": len(g["items"]), "reporters": len(g["reporters"]), "last": g["last"], "items": sorted(g["items"], key=lambda x: -x["ts"])[:20]})
        out.sort(key=lambda x: (-x["reporters"], -x["last"]))
        return web.json_response(out[:200])

    async def report_action(self, req):
        uid = req.match_info["id"]
        b = await req.json()
        a = b.get("action")
        if a == "dismiss":
            await self.resolve_reports(uid, "dismissed")
            try:
                await self.set_fields(uid, {"hidden": False, "reports_count": 0})
            except Exception:
                pass
        elif a == "ban":
            await self.set_fields(uid, {"banned": True, "banned_at": int(time.time())})
            self.c.on_ban(uid, True)
            await self.try_send(uid, "⛔️ Ваш акаунт заблоковано адміністрацією за порушення правил.")
            await self.resolve_reports(uid, "banned")
        elif a == "delete":
            await self.try_send(uid, "❌ Вашу анкету видалено адміністрацією через скарги. Натисніть /start, щоб створити нову за правилами.")
            await self.delete_user(uid)
            await self.resolve_reports(uid, "deleted")
        else:
            raise ValueError("Неизвестное действие")
        self.drop_cache()
        return web.json_response({"ok": True})

    # ---------- рассылка ----------
    async def broadcast_status(self, req):
        return web.json_response(self.cast)

    async def broadcast(self, req):
        b = await req.json()
        if b.get("stop"):
            self._cast_stop = True
            return web.json_response({"ok": True})
        text = str(b.get("text", "")).strip()
        if not text:
            raise ValueError("Пустой текст")
        if len(text) > 4000:
            raise ValueError("Слишком длинный текст (до 4000 символов)")
        if self.cast.get("running"):
            raise ValueError("Рассылка уже идёт")
        if b.get("test"):
            ids = sorted(self.c.admin_ids())
            if not ids:
                raise ValueError("Нет Telegram ID админов: добавьте свой ID в «Настройки» (узнать: команда /myid в боте).")
        else:
            us = await self.users(fresh=True)
            ids = []
            for u in us:
                if u.get("banned") or u.get("blocked_bot"):
                    continue
                if b.get("country") and u.get("country") != b["country"]:
                    continue
                if b.get("gender") == "m" and u.get("gender") != MEN:
                    continue
                if b.get("gender") == "f" and u.get("gender") != WOMEN:
                    continue
                if b.get("inactive") and (ts(u.get("last_active")) or 0) > (time.time() - int(b["inactive"]) * DAY) * 1000:
                    continue
                ids.append(u["id"])
        self.cast = {"running": True, "test": bool(b.get("test")), "total": len(ids), "sent": 0, "failed": 0, "blocked": 0, "started": int(time.time() * 1000), "finished": None, "text": text[:200]}
        self._cast_stop = False
        asyncio.create_task(self._run_cast(ids, text, b.get("button_text"), b.get("button_url")))
        return web.json_response(self.cast)

    async def _run_cast(self, ids, text, btn_text=None, btn_url=None):
        from aiogram import types
        kb = None
        if btn_text and btn_url and str(btn_url).startswith(("https://", "http://", "tg://")):
            kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text=str(btn_text)[:60], url=str(btn_url))]])
        for uid in ids:
            if self._cast_stop:
                break
            for _ in range(3):
                try:
                    await self.c.bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=False)
                    self.cast["sent"] += 1
                    break
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except TelegramForbiddenError:
                    self.cast["blocked"] += 1
                    try:
                        await self.set_fields(uid, {"blocked_bot": True})
                    except Exception:
                        pass
                    break
                except TelegramBadRequest as e:
                    self.cast["failed"] += 1
                    self.cast["lastError"] = str(e)[:200]
                    break
                except Exception as e:  # noqa: BLE001
                    self.cast["lastError"] = str(e)[:200]
                    await asyncio.sleep(2)
            else:
                self.cast["failed"] += 1
            await asyncio.sleep(0.05)  # ≈20 сообщений в секунду — в пределах лимитов Telegram
        self.cast["running"] = False
        self.cast["stopped"] = self._cast_stop
        self.cast["finished"] = int(time.time() * 1000)
        self.drop_cache()

    # ---------- оплаты (зірки Telegram) ----------
    async def load_payments(self, uid=None):
        def run():
            q = self.c.db.collection("payments")
            if uid:
                q = q.where("uid", "==", uid)
            return [{**(d.to_dict() or {}), "id": d.id} for d in q.stream()]
        rows = await asyncio.to_thread(run)
        return sorted(rows, key=lambda r: -int(r.get("ts") or 0))

    async def payments(self, req):
        rows = await self.load_payments()
        now = time.time()
        paid = [r for r in rows if r.get("status") == "paid"]
        refunded = [r for r in rows if r.get("status") == "refunded"]
        stars = lambda lst: sum(int(r.get("stars") or 0) for r in lst)
        since = lambda days: [r for r in paid if int(r.get("ts") or 0) > now - days * DAY]
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        days = []
        for i in range(29, -1, -1):
            a = today - i * DAY
            days.append({"day": int(a * 1000), "n": stars(r for r in paid if a <= int(r.get("ts") or 0) < a + DAY)})
        plans = defaultdict(lambda: {"n": 0, "stars": 0})
        for r in paid:
            plans[int(r.get("days") or 0)]["n"] += 1
            plans[int(r.get("days") or 0)]["stars"] += int(r.get("stars") or 0)
        balance = None
        try:
            b = await self.c.bot.get_my_star_balance()
            balance = int(b.amount)
        except Exception as e:  # noqa: BLE001
            logging.info(f"star balance: {e}")
        return web.json_response({
            "balance": balance,
            "total": stars(paid), "count": len(paid),
            "today": stars(r for r in paid if int(r.get("ts") or 0) >= today),
            "d7": stars(since(7)), "d30": stars(since(30)), "n30": len(since(30)),
            "payers": len({r.get("uid") for r in paid}),
            "refunded": stars(refunded), "refundedCount": len(refunded),
            "days": days,
            "plans": [{"days": d, **v} for d, v in sorted(plans.items())],
            "items": [{"id": r["id"], "uid": r.get("uid"), "name": r.get("name", ""), "username": r.get("username", ""), "days": r.get("days"),
                       "stars": r.get("stars"), "status": r.get("status"), "ts": int(r.get("ts") or 0) * 1000,
                       "refundedAt": int(r.get("refunded_at") or 0) * 1000} for r in rows[:500]],
        })

    async def payment_action(self, req):
        pid = req.match_info["id"]
        b = await req.json()
        if b.get("action") != "refund":
            raise ValueError("Неизвестное действие")
        db = self.c.db
        ref = db.collection("payments").document(pid)
        doc = await asyncio.to_thread(ref.get)
        if not doc.exists:
            raise ValueError("Платёж не найден")
        p = doc.to_dict() or {}
        if p.get("status") == "refunded":
            raise ValueError("Уже возвращён")
        try:
            await self.c.bot.refund_star_payment(user_id=int(p["uid"]), telegram_payment_charge_id=pid)
        except TelegramBadRequest as e:
            raise ValueError(f"Telegram отказал: {e.message}")
        await asyncio.to_thread(ref.update, {"status": "refunded", "refunded_at": int(time.time())})
        # снимаем оплаченные дни (но не раньше «сейчас»)
        cut = int(p.get("days") or 0) * DAY
        for r, field in ((db.collection("users").document(p["uid"]), "likes_view_until"), (db.collection("premium").document(p["uid"]), "until")):
            try:
                d = await asyncio.to_thread(r.get)
                if d.exists:
                    cur = int((d.to_dict() or {}).get(field) or 0)
                    await asyncio.to_thread(r.update, {field: max(int(time.time()), cur - cut) if cur else 0})
            except Exception as e:  # noqa: BLE001
                logging.warning(f"refund premium {p['uid']}: {e}")
        self.drop_cache()
        if b.get("notify", True):
            await self.try_send(p["uid"], f"↩️ Вам повернуто {p.get('stars')} ⭐ за Преміум.")
        return web.json_response({"ok": True})

    # ---------- настройки ----------
    async def get_settings(self, req):
        return web.json_response(self.c.settings)

    async def put_settings(self, req):
        b = await req.json()
        s = dict(self.c.settings)
        if "privacy" in b or b.get("privacy_default"):
            s["privacy"] = str(b.get("privacy") or "").strip()[:4000] or None
        for k in ("onboarding", "disclaimer", "welcome_back"):
            if k in b:
                v = str(b[k]).strip()
                if not v:
                    raise ValueError("Текст не может быть пустым")
                s[k] = v[:4000]
        if "countries" in b:
            lst = [str(x).strip()[:40] for x in b["countries"] if str(x).strip()]
            if not lst:
                raise ValueError("Нужна хотя бы одна страна")
            s["countries"] = lst[:20]
        if "premium_minutes" in b:
            s["premium_minutes"] = max(1, min(60 * 24 * 30, int(b["premium_minutes"])))
        if "plans" in b:
            pl = []
            for x in b["plans"] or []:
                d, st = int(x.get("days") or 0), int(x.get("stars") or 0)
                if not (1 <= d <= 365 and 1 <= st <= 100000):
                    raise ValueError("Тариф: дней от 1 до 365, звёзд от 1 до 100000")
                pl.append({"days": d, "stars": st})
            if not pl:
                raise ValueError("Нужен хотя бы один тариф")
            if len({x["days"] for x in pl}) != len(pl):
                raise ValueError("Два тарифа с одинаковым числом дней")
            s["plans"] = sorted(pl, key=lambda x: x["days"])[:6]
        if "payments" in b:
            s["payments"] = bool(b["payments"])
        if "report_hide" in b:
            s["report_hide"] = max(0, min(50, int(b["report_hide"])))
        if "admin_ids" in b:
            s["admin_ids"] = [x for x in (str(i).strip() for i in b["admin_ids"]) if x.lstrip("-").isdigit()][:20]
        await self.c.save_settings(s)
        return web.json_response(self.c.settings)

    # ---------- фото из Telegram ----------
    async def photo(self, req):
        fid = req.match_info["fid"]
        if fid not in self._photos:
            try:
                f = await self.c.bot.get_file(fid)
                buf = await self.c.bot.download_file(f.file_path)
                self._photos[fid] = buf.getvalue() if hasattr(buf, "getvalue") else buf
            except Exception as e:  # noqa: BLE001
                logging.warning(f"photo {fid[:12]}: {e}")
                raise web.HTTPNotFound()
            while len(self._photos) > 300:
                self._photos.popitem(last=False)
        self._photos.move_to_end(fid)
        return web.Response(body=self._photos[fid], content_type="image/jpeg", headers={"cache-control": "private, max-age=86400"})

    def app(self):
        app = web.Application(middlewares=[self.guard], client_max_size=30 * 1024 * 1024)  # фото/відео із застосунку
        r = app.router
        # корінь сайту і /app/ ведуть у застосунок — щоб працювала будь-яка адреса, вставлена в @BotFather
        r.add_get("/", to_app)
        r.add_get("/app/", to_app)
        r.add_get("/health", lambda req: web.Response(text="ok"))
        r.add_get("/admin", self.page)
        r.add_post("/admin/login", self.login)
        r.add_post("/admin/logout", self.logout)
        r.add_get("/admin/api/stats", self.stats)
        r.add_get("/admin/api/users", self.list_users)
        r.add_get("/admin/api/user/{id}", self.user_detail)
        r.add_post("/admin/api/user/{id}", self.user_action)
        r.add_get("/admin/api/reports", self.reports)
        r.add_post("/admin/api/reports/{id}", self.report_action)
        r.add_get("/admin/api/broadcast", self.broadcast_status)
        r.add_post("/admin/api/broadcast", self.broadcast)
        r.add_get("/admin/api/payments", self.payments)
        r.add_post("/admin/api/payments/{id}", self.payment_action)
        r.add_get("/admin/api/settings", self.get_settings)
        r.add_post("/admin/api/settings", self.put_settings)
        r.add_get("/admin/photo/{fid}", self.photo)
        return app


async def to_app(req):
    raise web.HTTPFound("/app" + (("?" + req.query_string) if req.query_string else ""))


async def start_admin(ctx: Ctx, extra=None):
    adm = Admin(ctx)
    app = adm.app()
    if extra:
        extra(app)  # застосунок у Telegram (/app) на тому ж сервері
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info(f"🛠 Админка: порт {port}, путь /admin" + ("" if adm.password else " (ADMIN_PASSWORD не задан — вход закрыт)"))
    return adm
