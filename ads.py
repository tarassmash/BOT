"""Генератор рекламных видео для TikTok / Reels / Shorts на fal.ai — страница /admin/ads в админке.

Ключ fal.ai (FAL_KEY) живёт только на сервере: браузер ходит в fal через прокси /admin/fal,
доступный лишь после входа в админку, и только к нужным моделям.
"""
import json
import logging
import os
import time
from collections import Counter
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))

# модели, которые может вызывать страница (owner/app — первые две части пути)
ALLOWED_APPS = {
    "fal-ai/any-llm",
    "openai/gpt-image-2",
    "fal-ai/kling-video",
    "fal-ai/elevenlabs",
    "CassetteAI/music-generator",
}
FAL_HOSTS = ("fal.run",)                  # fal.run, queue.fal.run, *.fal.run
MEDIA_HOSTS = ("fal.media", "fal.ai", "fal.run", "storage.googleapis.com")
PASS_HEADERS = ("content-type", "accept", "x-fal-queue-priority", "x-fal-runner-hint", "x-fal-request-timeout", "x-fal-store-io")


def host_ok(host, allowed):
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in allowed)


class AdsTool:
    def __init__(self, admin):
        self.a = admin                      # admin.Admin: authed(), users(), c.bot, c.settings
        self._session = None
        self.spent = Counter()              # приблизний облік викликів за час роботи сервера

    @property
    def key(self):
        return os.getenv("FAL_KEY", "").strip()

    async def session(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600))
        return self._session

    # ---------- сторінка ----------
    async def page(self, req):
        if not self.a.authed(req):
            raise web.HTTPFound("/admin")
        with open(os.path.join(HERE, "ads.html"), encoding="utf-8") as f:
            html = f.read()
        return web.Response(text=html, content_type="text/html", headers={"cache-control": "no-store", "x-robots-tag": "noindex"})

    async def info(self, req):
        """Що знає бот для реклами: ім'я бота, країни, реальна кількість анкет (щоб не вигадувати цифри)."""
        us = await self.a.users()
        by_country = Counter(u.get("country") or "" for u in us if not u.get("banned"))
        try:
            me = await self.a.c.bot.get_me()
            bot_name = me.username
        except Exception:  # noqa: BLE001
            bot_name = ""
        countries = list(self.a.c.settings.get("countries") or [])
        return web.json_response({
            "falKey": bool(self.key),
            "bot": bot_name,
            "countries": countries,
            "profiles": {c: by_country.get(c, 0) for c in countries},
            "total": sum(by_country.values()),
            "spent": dict(self.spent),
        })

    # ---------- проксі fal.ai (протокол @fal-ai/client: заголовок x-fal-target-url) ----------
    async def fal_proxy(self, req):
        if not self.a.authed(req):
            return web.json_response({"error": "Нужно войти в админку"}, status=401)
        if not self.key:
            return web.json_response({"error": "Нет FAL_KEY: добавьте ключ fal.ai в Railway → Variables"}, status=500)
        target = req.headers.get("x-fal-target-url", "")
        u = urlparse(target)
        if u.scheme != "https" or not host_ok(u.hostname, FAL_HOSTS):
            return web.json_response({"error": "Недопустимый адрес"}, status=400)
        parts = u.path.strip("/").split("/")
        app_id = "/".join(parts[:2])
        if app_id not in ALLOWED_APPS:
            return web.json_response({"error": f"Модель {app_id} не разрешена"}, status=403)
        headers = {"authorization": f"Key {self.key}"}
        for h in PASS_HEADERS:
            if h in req.headers:
                headers[h] = req.headers[h]
        body = await req.read() if req.method in ("POST", "PUT") else None
        if req.method == "POST" and not parts[-1] in ("status", "cancel") and "requests" not in parts:
            self.spent[app_id] += 1
        s = await self.session()
        try:
            async with s.request(req.method, target, headers=headers, data=body) as r:
                data = await r.read()
                return web.Response(body=data, status=r.status, content_type=(r.content_type or "application/json"))
        except Exception as e:  # noqa: BLE001
            logging.warning(f"fal proxy {app_id}: {e}")
            return web.json_response({"error": f"fal.ai не ответил: {e}"}, status=502)

    async def media(self, req):
        """Файли fal (картинки, звук, відео) через наш сервер — щоб браузер міг зібрати з них ролик без проблем CORS."""
        url = req.query.get("url", "")
        u = urlparse(url)
        if u.scheme != "https" or not host_ok(u.hostname, MEDIA_HOSTS):
            return web.json_response({"error": "Недопустимый адрес"}, status=400)
        s = await self.session()
        try:
            async with s.get(url) as r:
                if r.status != 200:
                    return web.json_response({"error": f"файл не скачался ({r.status})"}, status=502)
                data = await r.read()
                if len(data) > 80 * 1024 * 1024:
                    return web.json_response({"error": "файл слишком большой"}, status=413)
                return web.Response(body=data, content_type=r.content_type or "application/octet-stream",
                                    headers={"cache-control": "private, max-age=86400"})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": str(e)}, status=502)

    def setup(self, r):
        r.add_get("/admin/ads", self.page)
        r.add_get("/admin/api/ads/info", self.info)
        r.add_get("/admin/api/ads/media", self.media)
        r.add_route("*", "/admin/fal", self.fal_proxy)
