"""Застосунок у Telegram (Mini App): стрічка анкет на весь екран, лайки, симпатії, профіль.

Працює на тому ж сервері, що й адмінка: https://<домен Railway>/app
Вхід — підписані дані Telegram (initData), тож пароль не потрібен і підробити користувача не можна.
"""
import asyncio
import hashlib
import re
import hmac
import json
import logging
import os
import time
from collections import OrderedDict
from urllib.parse import parse_qsl, quote

from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))


class MiniApp:
    def __init__(self, core):
        self.c = core  # модуль bot.py: db, bot, find_next, do_like, ...
        self.token = core.TOKEN
        self.secret = hmac.new(b"WebAppData", self.token.encode(), hashlib.sha256).digest()
        self.media_key = hashlib.sha256(("media|" + self.token).encode()).digest()
        self._files = OrderedDict()

    # ---------- авторизація ----------
    def user_id(self, req):
        raw = req.headers.get("x-init-data", "")
        if not raw:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Відкрий застосунок через Telegram"}), content_type="application/json")
        data = dict(parse_qsl(raw, keep_blank_values=True))
        got = data.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        want = hmac.new(self.secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(got, want):
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Невірний підпис Telegram"}), content_type="application/json")
        if time.time() - int(data.get("auth_date", 0) or 0) > 7 * 86400:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Сесія застаріла — відкрий застосунок знову"}), content_type="application/json")
        user = json.loads(data.get("user", "{}") or "{}")
        if not user.get("id"):
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Немає користувача"}), content_type="application/json")
        req["tg_data"] = data  # start_param тощо
        return str(user["id"]), user

    def sign(self, fid):
        return hmac.new(self.media_key, fid.encode(), hashlib.sha256).hexdigest()[:24]

    def media_token(self, uid, fid):
        """Підпис «цей файл завантажив саме цей користувач» — щоб не можна було вставити чуже фото в свою анкету."""
        return hmac.new(self.media_key, f"own|{uid}|{fid}".encode(), hashlib.sha256).hexdigest()[:24]

    def own_media(self, uid, items, current=None):
        """Перевіряє список фото/відео з застосунку: нові — з підписом, старі — ті, що вже є в анкеті."""
        cur = {m["id"]: m for m in (current or [])}
        out = []
        for m in items or []:
            fid = str(m.get("id", ""))
            if fid in cur:
                out.append(cur[fid])
            elif hmac.compare_digest(str(m.get("tok", "")), self.media_token(uid, fid)):
                out.append({k: v for k, v in {"t": "video" if m.get("t") == "video" else "photo", "id": fid, "thumb": m.get("thumb")}.items() if v})
        return out[:3]

    def media_url(self, m):
        fid = m["id"]
        return f"/app/media/{quote(fid, safe='')}?s={self.sign(fid)}"

    # ---------- представлення анкети ----------
    def card(self, p, me=None, incoming=False, note=""):
        c = self.c
        d = c.dist_km(me, p) if me else None
        return {
            "id": str(p.get("tg_id") or p.get("id")),
            "name": p.get("name", ""),
            "age": p.get("age"),
            "city": p.get("city") or "",
            "country": p.get("country", ""),
            "about": p.get("about", ""),
            "hometown": p.get("hometown", ""),
            "sameHome": bool(me and c.same_home(me, p)),
            "free": c.is_free(p),
            "dist": d,
            "incoming": bool(incoming),
            "note": note or "",
            "media": [{"t": m.get("t", "photo"), "url": self.media_url(m)} for m in c.media_list(p)],
        }

    async def page(self, req):
        with open(os.path.join(HERE, "app.html"), encoding="utf-8") as f:
            html = f.read()
        return web.Response(text=html, content_type="text/html", headers={"cache-control": "no-cache"})

    async def me(self, req):
        uid, tg_user = self.user_id(req)
        c = self.c
        p = await c.get_user(uid)
        bot_name = await c.bot_username()
        common = {"bot": bot_name, "countries": list(c.countries()), "reasons": c.REPORT_REASONS, "premiumMinutes": c.premium_minutes(),
                  "plans": [{"i": i, "days": p["days"], "stars": p["stars"], "title": c.plan_title(p["days"])} for i, p in enumerate(c.plans())] if c.payments_on() else [],
                  "minAge": c.MIN_AGE, "hometowns": c.HOMETOWNS, "rules": c.privacy_text(), "disclaimer": c.SETTINGS.get("disclaimer", ""),
                  "name": (tg_user.get("first_name") or "")[:30]}
        if not p:
            return web.json_response({"registered": False, **common})
        likes = len(list(await asyncio.to_thread(lambda: c.user_ref(uid).collection("likes").limit(99).get())))
        f = p.get("search_filters") or {}
        if not p.get("username") and tg_user.get("username"):
            await c.update_user(uid, {"username": tg_user["username"]})
        try:
            invited = len(list(await asyncio.to_thread(lambda: c.db.collection("users").where("referred_by", "==", uid).limit(200).get())))
        except Exception:
            invited = 0
        return web.json_response({
            "registered": True, **common, "invited": invited,
            "me": {**self.card({**p, "tg_id": uid}), "gender": "m" if p.get("gender") == c.MEN else "f", "search": "m" if "Хлопця" in str(p.get("search")) else "f",
                   "mediaRaw": [{"t": m.get("t", "photo"), "id": m["id"], "url": self.media_url(m)} for m in c.media_list(p)],
                   "location": p.get("lat") is not None, "paused": bool(p.get("paused")), "freeUntil": int(p.get("free_until") or 0),
                   "premiumUntil": int(p.get("likes_view_until") or 0), "username": p.get("username", "") or tg_user.get("username", "")},
            "filters": {"country": f.get("country"), "min_age": f.get("min_age"), "max_age": f.get("max_age")},
            "likes": likes,
            "superlikeWait": int(await c.superlike_wait(uid)),
            "ref": ref_link(bot_name, uid),
            "supportUnread": await self.support_unread(uid),
        })

    async def support_unread(self, uid):
        try:
            d = await self.c.firebase_get(self.c.db.collection("support").document(uid))
            return int(((d.to_dict() or {}) if d and d.exists else {}).get("unread_user") or 0)
        except Exception:
            return 0

    async def next(self, req):
        uid, _ = self.user_id(req)
        p, incoming, me = await self.c.find_next(uid)
        if p is None:
            return web.json_response({"empty": True, "reason": incoming})
        return web.json_response({"card": self.card(p, me, incoming, p.get("_note", ""))})

    async def swipe(self, req):
        uid, _ = self.user_id(req)
        b = await req.json()
        cid, incoming = str(b.get("id", "")), bool(b.get("incoming"))
        if not cid or cid == uid:
            raise web.HTTPBadRequest()
        if b.get("action") == "like":
            match = await self.c.do_like(uid, cid, incoming)
            if match:
                return web.json_response({"match": await self.match_info(cid)})
        else:
            await self.c.do_skip(uid, cid, incoming)
        return web.json_response({"ok": True})

    async def match_info(self, cid):
        p = await self.c.get_user(cid) or {}
        media = self.c.media_list(p)
        return {"id": cid, "name": p.get("name", ""), "username": p.get("username", ""),
                "photo": self.media_url(media[0]) if media else ""}

    async def superlike(self, req):
        uid, _ = self.user_id(req)
        b = await req.json()
        text = str(b.get("text", "")).strip()
        if not 1 <= len(text) <= 200:
            return web.json_response({"error": "Повідомлення — від 1 до 200 символів"}, status=400)
        wait = await self.c.superlike_wait(uid)
        if wait > 0:
            return web.json_response({"error": f"Суперлайк — раз на добу. Наступний через {int(wait // 3600)} год {int(wait % 3600 // 60)} хв"}, status=400)
        cid = str(b.get("id", ""))
        match = await self.c.do_superlike(uid, cid, bool(b.get("incoming")), text)
        return web.json_response({"match": await self.match_info(cid)} if match else {"ok": True})

    async def report(self, req):
        uid, _ = self.user_id(req)
        b = await req.json()
        await self.c.do_report(uid, str(b.get("id", "")), str(b.get("reason", "other")))
        return web.json_response({"ok": True})

    async def likes(self, req):
        uid, _ = self.user_id(req)
        c = self.c
        me = await c.get_user(uid) or {}
        docs = list(await asyncio.to_thread(lambda: c.user_ref(uid).collection("likes").limit(60).get()))
        premium = int(me.get("likes_view_until") or 0) > time.time()
        out = []
        if premium:
            for d in docs:
                p = await c.get_user(d.id)
                if c.visible(p):
                    out.append(self.card({**p, "tg_id": d.id}, me, True, (d.to_dict() or {}).get("msg", "")))
        return web.json_response({"count": len(docs), "premium": premium, "items": out})

    async def matches(self, req):
        uid, _ = self.user_id(req)
        c = self.c

        def load():
            rows = []
            for f, o in (("a", "b"), ("b", "a")):
                for d in c.db.collection("matches").where(f, "==", uid).stream():
                    r = d.to_dict() or {}
                    t = r.get("ts")
                    rows.append((r.get(o), t.timestamp() if hasattr(t, "timestamp") else 0))
            return rows
        rows = await asyncio.to_thread(load)
        seen, out = set(), []
        for other, t in sorted(rows, key=lambda x: -x[1]):
            if not other or other in seen:
                continue
            seen.add(other)
            p = await c.get_user(other)
            if not p or p.get("banned"):
                continue
            media = c.media_list(p)
            out.append({"id": other, "name": p.get("name", ""), "age": p.get("age"), "city": p.get("city") or p.get("country", ""),
                        "username": p.get("username", ""), "photo": self.media_url(media[0]) if media else "", "ts": int(t * 1000)})
            if len(out) >= 100:
                break
        return web.json_response(out)

    async def profile(self, req):
        uid, _ = self.user_id(req)
        c = self.c
        b = await req.json()
        fields, err = self.clean_profile({k: v for k, v in b.items() if k in ("name", "age", "country", "city", "hometown", "about", "gender", "search", "lat", "lon")}, partial=True)
        if err:
            return web.json_response({"error": err}, status=400)
        if "paused" in b:
            fields["paused"] = bool(b["paused"])
        if "free" in b:
            fields["free_until"] = int(time.time()) + c.COFFEE_HOURS * 3600 if b["free"] else 0
        if "filters" in b:
            f = b["filters"] or {}
            country = f.get("country") if f.get("country") in c.countries() else None
            lo = int(f["min_age"]) if str(f.get("min_age") or "").isdigit() else None
            hi = int(f["max_age"]) if str(f.get("max_age") or "").isdigit() else None
            if lo and hi and hi < lo:
                lo, hi = hi, lo
            fields["search_filters"] = {"country": country, "min_age": lo, "max_age": hi}
        if fields:
            await c.update_user(uid, fields)
        return web.json_response({"ok": True})

    async def upload(self, req):
        """Фото/відео із застосунку: надсилаємо в чат з людиною (без звуку), беремо file_id і одразу видаляємо повідомлення."""
        uid, _ = self.user_id(req)
        c = self.c
        reader = await req.multipart()
        part = await reader.next()
        if not part or part.name != "file":
            return web.json_response({"error": "Немає файлу"}, status=400)
        ctype = part.headers.get("Content-Type", "")
        data = await part.read(decode=False)
        if len(data) > 20 * 1024 * 1024:
            return web.json_response({"error": "Файл завеликий (до 20 МБ)"}, status=400)
        from aiogram.types import BufferedInputFile
        from aiogram.exceptions import TelegramForbiddenError
        try:
            if ctype.startswith("video/"):
                msg = await c.bot.send_video(uid, BufferedInputFile(data, "video.mp4"), disable_notification=True)
                v = msg.video or msg.animation
                if v and getattr(v, "duration", 0) and v.duration > 60:
                    await c.bot.delete_message(uid, msg.message_id)
                    return web.json_response({"error": "Відео до 60 секунд"}, status=400)
                m = {"t": "video", "id": v.file_id, "thumb": v.thumbnail.file_id if getattr(v, "thumbnail", None) else None}
            elif ctype.startswith("image/"):
                msg = await c.bot.send_photo(uid, BufferedInputFile(data, "photo.jpg"), disable_notification=True)
                m = {"t": "photo", "id": msg.photo[-1].file_id}
            else:
                return web.json_response({"error": "Потрібне фото або відео"}, status=400)
        except TelegramForbiddenError:
            return web.json_response({"error": "Розблокуй бота в Telegram, щоб завантажити фото"}, status=400)
        except Exception as e:  # noqa: BLE001
            logging.warning(f"app upload: {e}")
            return web.json_response({"error": "Не вдалося завантажити. Спробуй інше фото."}, status=400)
        try:
            await c.bot.delete_message(uid, msg.message_id)
        except Exception:
            pass
        m = {k: v for k, v in m.items() if v}
        return web.json_response({**m, "tok": self.media_token(uid, m["id"]), "url": self.media_url(m)})

    def clean_profile(self, b, partial=False):
        """Спільна перевірка полів анкети. Повертає (поля, помилка)."""
        c = self.c
        f = {}
        if not partial or "name" in b:
            n = str(b.get("name", "")).strip()
            if not 2 <= len(n) <= 30:
                return None, "Ім'я — від 2 до 30 символів"
            f["name"] = n
        if not partial or "age" in b:
            try:
                a = int(b.get("age"))
            except Exception:
                a = 0
            if not c.MIN_AGE <= a <= 70:
                return None, f"Бот лише для дорослих: вік від {c.MIN_AGE} до 70"
            f["age"] = a
        if not partial or "country" in b:
            if b.get("country") not in c.countries():
                return None, "Обери країну"
            f["country"] = b["country"]
        if not partial or "city" in b:
            city = c.clean_town(b.get("city"))
            if not city:
                return None, "Напиши місто"
            f["city"] = city
        if "hometown" in b:
            v = str(b.get("hometown") or "").strip()
            f["hometown"] = (c.clean_town(v) or "") if v else ""
        if not partial or "about" in b:
            t = str(b.get("about", "")).strip()
            if len(t) < 3:
                return None, "Напиши кілька слів про себе"
            f["about"] = t[:700]
        if not partial or "gender" in b:
            if b.get("gender") not in ("m", "f"):
                return None, "Вкажи стать"
            f["gender"] = c.MEN if b["gender"] == "m" else c.WOMEN
        if not partial or "search" in b:
            if b.get("search") not in ("m", "f"):
                return None, "Вкажи, кого шукаєш"
            f["search"] = c.WANT_M if b["search"] == "m" else c.WANT_F
        if b.get("lat") is not None and b.get("lon") is not None:
            try:
                lat, lon = float(b["lat"]), float(b["lon"])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    f["lat"], f["lon"] = round(lat, 4), round(lon, 4)  # ~10 м, точніше не потрібно
            except Exception:
                pass
        return f, None

    async def register(self, req):
        uid, tg_user = self.user_id(req)
        c = self.c
        if await c.get_user(uid):
            return web.json_response({"ok": True, "already": True})
        b = await req.json()
        if not b.get("agree"):
            return web.json_response({"error": "Підтверди, що тобі є 18 і ти погоджуєшся з правилами"}, status=400)
        fields, err = self.clean_profile(b)
        if err:
            return web.json_response({"error": err}, status=400)
        media = self.own_media(uid, b.get("media"))
        if not media:
            return web.json_response({"error": "Додай хоча б одне фото"}, status=400)
        pend = await c.firebase_get(c.db.collection("pending_ref").document(uid))
        referrer = ((pend.to_dict() or {}).get("ref") if pend and pend.exists else "") or ""
        sp = str(req.get("tg_data", {}).get("start_param", ""))
        if not referrer and sp.startswith("ref_"):
            referrer = sp[4:]  # відкрили застосунок за посиланням друга t.me/бот?startapp=ref_…
        if referrer == uid or (referrer and not await c.get_user(referrer)):
            referrer = ""
        firestore = c.firestore
        now = int(time.time())
        profile = {
            "tg_id": uid, "username": tg_user.get("username") or "", **fields, **c.media_fields(media),
            "registered_at": firestore.SERVER_TIMESTAMP, "last_active": firestore.SERVER_TIMESTAMP,
            "search_filters": {"country": None, "min_age": None, "max_age": None},
            "lat": fields.get("lat"), "lon": fields.get("lon"), "referred_by": referrer, "disclaimer_seen": True, "source": "app",
        }
        if referrer:
            profile["likes_view_until"] = now + c.premium_minutes() * 60
        paid = await c.paid_premium_until(uid)  # оплачений Преміум повертається в нову анкету
        if paid > int(profile.get("likes_view_until") or 0):
            profile["likes_view_until"] = paid
        await c.firebase_set(c.user_ref(uid), profile)
        if pend and pend.exists:
            await c.firebase_delete(pend.reference)
        if referrer:
            asyncio.create_task(c.reward_referrer(referrer))
        return web.json_response({"ok": True})

    async def invoice(self, req):
        """Рахунок у зірках Telegram: застосунок відкриває його через Telegram.WebApp.openInvoice — оплата не виходячи з застосунку."""
        uid, _ = self.user_id(req)
        b = await req.json()
        try:
            link = await self.c.premium_invoice_link(uid, int(b.get("plan", -1)))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            logging.warning(f"app invoice: {e}")
            return web.json_response({"error": "Не вдалося створити рахунок. Спробуй ще раз."}, status=400)
        return web.json_response({"link": link})

    async def support_get(self, req):
        uid, _ = self.user_id(req)
        c = self.c
        msgs = await c.support_messages(uid)
        try:
            await asyncio.to_thread(c.db.collection("support").document(uid).update, {"unread_user": 0})
        except Exception:
            pass  # ще не писав
        return web.json_response({"messages": msgs})

    async def support_post(self, req):
        uid, tg_user = self.user_id(req)
        b = await req.json()
        text = str(b.get("text", "")).strip()
        if not 2 <= len(text) <= self.c.SUPPORT_MAX:
            return web.json_response({"error": f"Повідомлення — від 2 до {self.c.SUPPORT_MAX} символів"}, status=400)
        last = await self.c.support_messages(uid, limit=5)
        if sum(1 for m in last if m["from"] == "user" and m["ts"] > (time.time() - 60) * 1000) >= 3:
            return web.json_response({"error": "Забагато повідомлень підряд — зачекай хвилинку"}, status=429)

        class U:  # ім'я з Telegram, якщо анкети ще немає
            first_name = tg_user.get("first_name") or ""
            username = tg_user.get("username") or ""
        await self.c.support_from_user(uid, text, "app", U)
        return web.json_response({"messages": await self.c.support_messages(uid)})

    async def premium(self, req):
        uid, _ = self.user_id(req)
        p = await self.c.get_user(uid) or {}
        return web.json_response({"premiumUntil": int(p.get("likes_view_until") or 0)})

    async def set_media(self, req):
        uid, _ = self.user_id(req)
        c = self.c
        p = await c.get_user(uid)
        if not p:
            raise web.HTTPNotFound()
        media = self.own_media(uid, (await req.json()).get("media"), c.media_list(p))
        if not media:
            return web.json_response({"error": "Потрібне хоча б одне фото"}, status=400)
        await c.update_user(uid, c.media_fields(media))
        return web.json_response({"ok": True, "media": [{"t": m["t"], "id": m["id"], "url": self.media_url(m)} for m in media]})

    async def delete(self, req):
        uid, _ = self.user_id(req)
        b = await req.json()
        if b.get("confirm") != "DELETE":
            raise web.HTTPBadRequest()
        await self.c.delete_profile(uid)
        return web.json_response({"ok": True})

    async def photo_via_bot(self, req):
        """Фото змінюються через чат з ботом: Telegram не дає застосунку зберегти файл так само надійно."""
        uid, _ = self.user_id(req)
        c = self.c
        ctx = c.dp.fsm.get_context(c.bot, int(uid), int(uid))
        await ctx.clear()
        c.PENDING_MEDIA.pop(uid, None)
        await ctx.set_state(c.EditProfile.photo)
        await c.safe_send_message(uid, "📷 Надішли до 3 нових фото або відео — вони замінять поточні.\nПотім знову відкрий застосунок ❤️",
                                  reply_markup=c.ikb([[("✖️ Скасувати", "me:cancel")]]))
        return web.json_response({"ok": True})

    async def media(self, req):
        fid = req.match_info["fid"]
        if not hmac.compare_digest(req.query.get("s", ""), self.sign(fid)):
            raise web.HTTPForbidden()
        if fid not in self._files:
            try:
                f = await self.c.bot.get_file(fid)
                buf = await self.c.bot.download_file(f.file_path)
                data = buf.getvalue() if hasattr(buf, "getvalue") else buf
                ctype = "video/mp4" if str(f.file_path).lower().endswith((".mp4", ".mov")) else "image/jpeg"
                self._files[fid] = (data, ctype)
            except Exception as e:  # noqa: BLE001
                logging.warning(f"app media {fid[:12]}: {e}")
                raise web.HTTPNotFound()
            # кеш у пам'яті ~ до 120 МБ
            while len(self._files) > 8 and sum(len(v[0]) for v in self._files.values()) > 120 * 1024 * 1024:
                self._files.popitem(last=False)
        self._files.move_to_end(fid)
        data, ctype = self._files[fid]
        return web.Response(body=data, content_type=ctype, headers={"cache-control": "private, max-age=604800", "accept-ranges": "none"})

    def setup(self, app):
        r = app.router
        r.add_get("/app", self.page)
        r.add_get("/app/api/me", self.me)
        r.add_get("/app/api/next", self.next)
        r.add_post("/app/api/swipe", self.swipe)
        r.add_post("/app/api/superlike", self.superlike)
        r.add_post("/app/api/report", self.report)
        r.add_get("/app/api/likes", self.likes)
        r.add_get("/app/api/matches", self.matches)
        r.add_post("/app/api/profile", self.profile)
        r.add_post("/app/api/photo", self.photo_via_bot)
        r.add_post("/app/api/upload", self.upload)
        r.add_post("/app/api/register", self.register)
        r.add_post("/app/api/media", self.set_media)
        r.add_post("/app/api/delete", self.delete)
        r.add_post("/app/api/invoice", self.invoice)
        r.add_get("/app/api/premium", self.premium)
        r.add_get("/app/api/support", self.support_get)
        r.add_post("/app/api/support", self.support_post)
        r.add_get("/app/media/{fid}", self.media)


def app_url():
    if os.getenv("WEBAPP_URL"):
        return os.getenv("WEBAPP_URL").rstrip("/")
    dom = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{dom}/app" if dom else ""


def main_app():
    """У @BotFather увімкнено «головний застосунок» — посилання t.me/бот?startapp відкривають його одразу."""
    return os.getenv("MAIN_APP", "").strip().lower() in ("1", "true", "yes", "on")


def ref_link(bot_name, uid):
    return f"https://t.me/{bot_name}?startapp=ref_{uid}" if main_app() else f"https://t.me/{bot_name}?start=ref_{uid}"
