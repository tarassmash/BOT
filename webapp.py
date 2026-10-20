"""Застосунок у Telegram (Mini App): стрічка анкет на весь екран, лайки, симпатії, профіль.

Працює на тому ж сервері, що й адмінка: https://<домен Railway>/app
Вхід — підписані дані Telegram (initData), тож пароль не потрібен і підробити користувача не можна.
"""
import asyncio
import hashlib
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
        return str(user["id"]), user

    def sign(self, fid):
        return hmac.new(self.media_key, fid.encode(), hashlib.sha256).hexdigest()[:24]

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
        if not p:
            return web.json_response({"registered": False, "bot": bot_name})
        likes = len(list(await asyncio.to_thread(lambda: c.user_ref(uid).collection("likes").limit(99).get())))
        f = p.get("search_filters") or {}
        if not p.get("username") and tg_user.get("username"):
            await c.update_user(uid, {"username": tg_user["username"]})
        return web.json_response({
            "registered": True, "bot": bot_name,
            "me": {**self.card({**p, "tg_id": uid}), "paused": bool(p.get("paused")), "freeUntil": int(p.get("free_until") or 0),
                   "premiumUntil": int(p.get("likes_view_until") or 0), "username": p.get("username", "") or tg_user.get("username", "")},
            "filters": {"country": f.get("country"), "min_age": f.get("min_age"), "max_age": f.get("max_age")},
            "countries": list(c.countries()),
            "likes": likes,
            "premiumMinutes": c.premium_minutes(),
            "superlikeWait": int(await c.superlike_wait(uid)),
            "reasons": c.REPORT_REASONS,
            "ref": f"https://t.me/{bot_name}?start=ref_{uid}",
        })

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
        fields = {}
        if "name" in b:
            n = str(b["name"]).strip()
            if not 2 <= len(n) <= 30:
                return web.json_response({"error": "Ім'я — від 2 до 30 символів"}, status=400)
            fields["name"] = n
        if "age" in b:
            try:
                a = int(b["age"])
            except Exception:
                a = 0
            if not c.MIN_AGE <= a <= 70:
                return web.json_response({"error": f"Вік від {c.MIN_AGE} до 70"}, status=400)
            fields["age"] = a
        for k in ("city", "hometown"):
            if k in b:
                v = str(b[k] or "").strip()
                fields[k] = (c.clean_town(v) or "") if v else ""
        if "about" in b:
            t = str(b["about"]).strip()
            if len(t) < 3:
                return web.json_response({"error": "Напиши хоча б кілька слів про себе"}, status=400)
            fields["about"] = t[:700]
        if "country" in b and b["country"] in c.countries():
            fields["country"] = b["country"]
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
        r.add_get("/app/media/{fid}", self.media)


def app_url():
    if os.getenv("WEBAPP_URL"):
        return os.getenv("WEBAPP_URL").rstrip("/")
    dom = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{dom}/app" if dom else ""
