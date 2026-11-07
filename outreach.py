"""Поиск украинских каналов и чатов по городам для рекламы FlirtUA (админка → «📣 Блогеры»).

Источники:
  • TGStat API (channels/search) — токен TGSTAT_TOKEN в Railway или в админке; нужен тариф Stat API S+.
  • Свой Telegram-аккаунт (Telethon): api_id/api_hash с my.telegram.org, вход по коду прямо в админке.

Отправка — полуавтомат: админка готовит личное сообщение и персональную ссылку, админ жмёт «Написать»
(открывается чат в Telegram с готовым текстом) и отправляет сам. Массовые сообщения незнакомым людям
Telegram считает спамом и замораживает аккаунты, поэтому автоматической рассылки здесь нет.

Персональная ссылка https://t.me/<бот>?startapp=ad_<id> — приложение записывает источник в анкету
(поле ad_source), и в админке видно, сколько людей пришло от каждого блогера.
"""
import asyncio
import logging
import os
import re
import time

import aiohttp
from aiohttp import web

DAY = 86400
STATUSES = {"new": "Новый", "sent": "Написали", "replied": "Ответил", "deal": "Договорились", "posted": "Вышла реклама", "rejected": "Отказ", "skip": "Пропустить"}

DEFAULT_CITIES = """Іспанія: Іспанія/Испания/Spain; Мадрид/Madrid; Барселона/Barcelona; Валенсія/Валенсия/Valencia; Аліканте/Аликанте/Alicante; Малага/Malaga; Торрев'єха/Торревьеха/Torrevieja; Бенідорм/Бенидорм/Benidorm; Мурсія/Мурсия/Murcia; Севілья/Севилья/Sevilla
Польща: Польща/Польша/Poland; Варшава/Warszawa; Краків/Краков/Krakow; Вроцлав/Wroclaw; Гданськ/Гданьск/Gdansk; Познань/Poznan; Лодзь/Lodz; Катовіце/Катовице/Katowice; Люблін/Люблин/Lublin
Німеччина: Німеччина/Германия/Germany; Берлін/Берлин/Berlin; Мюнхен/Munchen; Гамбург/Hamburg; Франкфурт/Frankfurt; Кельн/Koln; Дюссельдорф/Dusseldorf; Штутгарт/Stuttgart; Лейпциг/Leipzig
Чехія: Чехія/Чехия/Czech; Прага/Praha; Брно/Brno; Острава/Ostrava; Пльзень/Plzen
Італія: Італія/Италия/Italy; Рим/Roma; Мілан/Милан/Milano; Неаполь/Napoli; Болонья/Bologna; Турин/Torino"""

DEFAULT_QUERIES = "{city}\nукраїнці {city}\nукраинцы {city}"  # «{city}» окремо — потім відбираємо українські за назвою/описом

DEFAULT_TEMPLATE = """Вітаю! 👋 Мене звати {my_name}, я з команди FlirtUA — знайомства для українців за кордоном прямо в Telegram (@{bot}).

Бачу, що «{title}» — велика українська спільнота ({city}, {members} підписників). Хочемо запропонувати платне розміщення: пост про FlirtUA для вашої аудиторії.

Від нас — готовий текст і картинки, персональне посилання для ваших підписників: {link}

Підкажіть, будь ласка, вартість і найближчі вільні дати? 🙏"""

DEFAULTS = {"cities": DEFAULT_CITIES, "queries": DEFAULT_QUERIES, "template": DEFAULT_TEMPLATE, "min_members": 500,
            "auto": True, "every_hours": 24, "max_queries": 40, "my_name": "", "tgstat_token": "", "notify": True}

UKR = ("україн", "украин", "ukrain", "🇺🇦", "наші ", "наши ", "земляк", "укр ")
AD_WORDS = re.compile(r"(реклам|співпрац|сотруднич|по питан|по вопрос|admin|адмін|админ|\bads?\b|зв['’]яз|связ|менеджер|contact|пиш[іи]ть|пишите|власник|владел)", re.I)
HANDLE = re.compile(r"(?:@|(?:https?://)?t(?:elegram)?\.me/)([A-Za-z][A-Za-z0-9_]{3,31})\b")


def slug(username, fallback=""):
    s = re.sub(r"[^A-Za-z0-9_]", "", str(username or "")).lower()[:40]
    return s or re.sub(r"[^A-Za-z0-9_]", "", str(fallback))[:40]


def parse_cities(text):
    """«Країна: місто/варіант; місто2» → [{country, city, variants}]."""
    out = []
    for line in str(text or "").splitlines():
        if ":" not in line:
            continue
        country, rest = line.split(":", 1)
        for part in rest.split(";"):
            vs = [v.strip() for v in part.split("/") if v.strip()]
            if vs:
                out.append({"country": country.strip(), "city": vs[0], "variants": vs})
    return out


def find_contact(about, own=""):
    """Контакт «по рекламе» из описания канала: @ник рядом со словами реклама/сотрудничество, иначе первый чужой @ник."""
    about = str(about or "")
    own = str(own or "").lower()
    skip = {own, "joinchat", "share", "addlist", "addstickers", "proxy", "iv", "s", "c"}
    best, first = "", ""
    for m in HANDLE.finditer(about):
        h = m.group(1)
        if h.lower() in skip or h.lower().endswith("bot") and not AD_WORDS.search(about[max(0, m.start() - 60):m.start()]):
            continue
        if not first:
            first = h
        if AD_WORDS.search(about[max(0, m.start() - 70):m.start() + 5]):
            best = h
            break
    return best or first


def is_ukr(*texts):
    """Канал про наших: є «українці/🇺🇦/наші…» або текст українською (літери і ї є ґ)."""
    t = " ".join(str(x or "") for x in texts).lower() + " "
    return any(w in t for w in UKR) or bool(re.search(r"[іїєґ]", t))


class Outreach:
    def __init__(self, admin):
        self.a = admin
        self._cfg = None
        self._session = None
        self._tg = None          # TelegramClient (вход выполнен)
        self._login = None       # {"client", "phone", "hash"} — ждём код
        self.state = {"running": False, "log": [], "last": None}

    # ---------- хранилище ----------
    @property
    def db(self):
        return self.a.c.db

    def col(self):
        return self.db.collection("outreach")

    async def cfg(self, fresh=False):
        if self._cfg is None or fresh:
            d = await asyncio.to_thread(self.db.collection("system").document("outreach").get)
            self._cfg = {**DEFAULTS, **((d.to_dict() or {}) if d.exists else {})}
        return self._cfg

    async def save_cfg(self, upd):
        cfg = {**(await self.cfg()), **upd}
        await asyncio.to_thread(self.db.collection("system").document("outreach").set, cfg)
        self._cfg = cfg
        return cfg

    def tgstat_token(self, cfg):
        return os.getenv("TGSTAT_TOKEN", "").strip() or str(cfg.get("tgstat_token") or "").strip()

    async def secret(self):
        d = await asyncio.to_thread(self.db.collection("system").document("outreach_secret").get)
        return (d.to_dict() or {}) if d.exists else {}

    def api_keys(self, sec):
        api_id = os.getenv("TG_API_ID", "").strip() or str(sec.get("api_id") or "")
        api_hash = os.getenv("TG_API_HASH", "").strip() or str(sec.get("api_hash") or "")
        return (int(api_id) if api_id.isdigit() else 0), api_hash

    async def bot_name(self):
        try:
            return (await self.a.c.bot.get_me()).username
        except Exception:
            return "FlirtUa_bot"

    def log(self, msg):
        logging.info(f"📣 outreach: {msg}")
        self.state["log"] = (self.state["log"] + [f"{time.strftime('%H:%M:%S')} {msg}"])[-40:]

    async def http(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))
        return self._session

    async def upsert(self, item):
        """Новый канал — со статусом «Новый»; уже известный — обновляем цифры, статус и заметки не трогаем."""
        key = item["id"]
        ref = self.col().document(key)
        d = await asyncio.to_thread(ref.get)
        now = int(time.time())
        if d.exists:
            old = d.to_dict() or {}
            upd = {k: v for k, v in item.items() if k not in ("id", "status", "contact") and v not in (None, "")}
            if item.get("contact") and not old.get("contact_manual"):
                upd["contact"] = item["contact"]
            srcs = set(old.get("sources") or []) | {item["source"]}
            upd.update({"updated_at": now, "sources": sorted(srcs)})
            await asyncio.to_thread(ref.update, upd)
            return False
        await asyncio.to_thread(ref.set, {**{k: v for k, v in item.items() if k != "id"}, "status": "new", "found_at": now, "updated_at": now,
                                         "sources": [item["source"]], "note": "", "opens": 0})
        return True

    # ---------- источники ----------
    async def search_tgstat(self, token, q):
        s = await self.http()
        out = []
        for peer in ("channel", "chat"):
            params = {"token": token, "q": q, "peer_type": peer, "search_by_description": 1, "limit": 100}
            async with s.get("https://api.tgstat.ru/channels/search", params=params) as r:
                data = await r.json(content_type=None)
            if data.get("status") != "ok":
                raise RuntimeError(f"TGStat: {data.get('error') or data}")
            for it in (data.get("response") or {}).get("items") or []:
                uname = it.get("username") or ""
                out.append({"username": uname.lstrip("@"), "title": it.get("title", ""), "about": it.get("about", ""),
                            "members": int(it.get("participants_count") or 0), "kind": "chat" if peer == "chat" else "channel",
                            "tgstat_id": it.get("id"), "link": it.get("link") or (f"t.me/{uname.lstrip('@')}" if uname else "")})
        return out

    async def tg_client(self):
        if self._tg and self._tg.is_connected():
            return self._tg
        sec = await self.secret()
        api_id, api_hash = self.api_keys(sec)
        if not (api_id and api_hash and sec.get("session")):
            return None
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        c = TelegramClient(StringSession(sec["session"]), api_id, api_hash)
        await c.connect()
        if not await c.is_user_authorized():
            await c.disconnect()
            return None
        self._tg = c
        return c

    async def search_tg(self, client, q, budget):
        from telethon import functions, errors
        from telethon.tl.types import Channel
        try:
            res = await client(functions.contacts.SearchRequest(q=q, limit=50))
        except errors.FloodWaitError as e:
            raise RuntimeError(f"Telegram просит подождать {e.seconds} с")
        out = []
        for ch in res.chats:
            if not isinstance(ch, Channel) or not ch.username or budget["full"] <= 0:
                continue
            about, members = "", int(ch.participants_count or 0)
            try:
                budget["full"] -= 1
                full = await client(functions.channels.GetFullChannelRequest(ch))
                about = full.full_chat.about or ""
                members = int(full.full_chat.participants_count or members)
                await asyncio.sleep(1.2)
            except errors.FloodWaitError as e:
                if e.seconds > 60:
                    raise RuntimeError(f"Telegram просит подождать {e.seconds} с")
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:  # noqa: BLE001
                logging.warning(f"outreach full {ch.username}: {e}")
            out.append({"username": ch.username, "title": ch.title or "", "about": about, "members": members,
                        "kind": "chat" if ch.megagroup else "channel", "link": f"t.me/{ch.username}"})
        return out

    # ---------- поиск ----------
    def plan(self, cfg):
        cities = parse_cities(cfg.get("cities"))
        qs = [x.strip() for x in str(cfg.get("queries") or "").splitlines() if "{city}" in x]
        jobs, seen = [], set()
        for c in cities:
            for v in c["variants"][:2]:  # українська й російська назви; латиниця — лише для впізнавання
                # Telegram шукає за початком слова: «Валенсі» знаходить і «Валенсія», і «Валенсії»
                stem = v[:-1] if len(v) >= 6 and v[-1].lower() in "аяіїюеоуиыь" else v
                for t in qs:
                    q = t.replace("{city}", v).strip()
                    if len(q) >= 3 and q.lower() not in seen:
                        seen.add(q.lower())
                        jobs.append({**c, "q": q, "q_tg": t.replace("{city}", stem).strip()})
        return jobs

    def city_of(self, cities, text, fallback):
        """Если в названии/описании прямо назван город — берём его, иначе город из запроса."""
        t = str(text or "").lower()
        for c in cities:
            if c["city"] != c["country"] and any(len(v) >= 4 and v.lower() in t for v in c["variants"]):
                return c
        return fallback

    async def run(self, manual=False):
        if self.state["running"]:
            return
        self.state.update(running=True, log=[], started=int(time.time()))
        found = new = 0
        try:
            cfg = await self.cfg(fresh=True)
            jobs = self.plan(cfg)
            if not jobs:
                return self.log("Нет городов или запросов — проверьте настройки")
            token = self.tgstat_token(cfg)
            client = None
            try:
                client = await self.tg_client()
            except Exception as e:  # noqa: BLE001
                self.log(f"Telegram-аккаунт: {e}")
            if not token and not client:
                return self.log("Нет источника: добавьте токен TGStat или войдите в Telegram-аккаунт")
            cur = int(cfg.get("cursor") or 0) % len(jobs)
            n = max(1, min(len(jobs), int(cfg.get("max_queries") or 40)))
            batch = [jobs[(cur + i) % len(jobs)] for i in range(n)]
            cities = parse_cities(cfg.get("cities"))
            min_m = int(cfg.get("min_members") or 0)
            budget = {"full": 120}
            self.log(f"Запросов: {n} из {len(jobs)} · источники: {', '.join(x for x, ok in (('TGStat', token), ('Telegram', client)) if ok)}")
            for job in batch:
                for src, fn in (("tgstat", (lambda q: self.search_tgstat(token, q)) if token else None),
                                ("telegram", (lambda q: self.search_tg(client, job.get("q_tg") or q, budget)) if client else None)):
                    if not fn:
                        continue
                    try:
                        items = await fn(job["q"])
                    except Exception as e:  # noqa: BLE001
                        self.log(f"{src}: «{job['q']}» — {e}")
                        if src == "tgstat" and str(e).startswith("TGStat"):
                            token = ""  # токен/тариф не подходит — не тратим остальные запросы
                        if src == "telegram" and "подождать" in str(e):
                            client = None
                        continue
                    k = 0
                    for it in items:
                        if it["members"] < min_m or not (it["username"] or it.get("tgstat_id")):
                            continue
                        # лише українські: за назвою й описом самого каналу (не за словами запиту)
                        if not is_ukr(it["title"], it["about"]):
                            continue
                        c = self.city_of(cities, f'{it["title"]} {it["about"]}', job)
                        item = {"id": slug(it["username"], f"tgs{it.get('tgstat_id', '')}"), "username": it["username"], "title": it["title"][:200],
                                "about": it["about"][:1500], "members": it["members"], "kind": it["kind"], "link": it["link"],
                                "country": c["country"], "city": c["city"], "source": src, "contact": find_contact(it["about"], it["username"]),
                                "query": job["q"]}
                        if not item["id"]:
                            continue
                        found += 1
                        k += 1
                        if await self.upsert(item):
                            new += 1
                    self.log(f"{src}: «{job['q']}» → {k}")
                    await asyncio.sleep(1.0 if src == "tgstat" else 2.5)
                if not token and not client:
                    self.log("Источники недоступны — остановлено")
                    break
            await self.save_cfg({"cursor": (cur + n) % len(jobs), "last_run": int(time.time()), "last_new": new})
            self.log(f"Готово: найдено {found}, новых {new}")
            if new and cfg.get("notify") and not manual:
                for aid in self.a.c.admin_ids():
                    try:
                        await self.a.c.bot.send_message(aid, f"📣 Нашлось {new} новых украинских каналов/чатов для рекламы FlirtUA.\nАдминка → «📣 Блогеры».")
                    except Exception:
                        pass
        except Exception as e:  # noqa: BLE001
            logging.exception("outreach run")
            self.log(f"Ошибка: {e}")
        finally:
            self.state.update(running=False, last={"ts": int(time.time()), "found": found, "new": new})

    async def loop(self):
        await asyncio.sleep(120)
        while True:
            try:
                cfg = await self.cfg(fresh=True)
                every = max(1, int(cfg.get("every_hours") or 24)) * 3600
                if cfg.get("auto") and time.time() - int(cfg.get("last_run") or 0) >= every:
                    await self.run()
            except Exception as e:  # noqa: BLE001
                logging.error(f"outreach loop: {e}")
            await asyncio.sleep(900)

    # ---------- сообщение ----------
    def message(self, cfg, it, bot):
        link = f"https://t.me/{bot}?startapp=ad_{it['id']}"
        kind = "чат" if it.get("kind") == "chat" else "канал"
        vals = {"title": it.get("title", ""), "city": it.get("city") or it.get("country", ""), "country": it.get("country", ""),
                "members": f"{int(it.get('members') or 0):,}".replace(",", " "), "bot": bot, "link": link, "kind": kind,
                "my_name": cfg.get("my_name") or "", "username": it.get("username", "")}
        text = str(cfg.get("template") or DEFAULT_TEMPLATE)
        if not vals["my_name"]:  # ім'я не задано — «Мене звати {my_name}, я з команди» → «Я з команди»
            text = re.sub(r"Мене звати\s*\{my_name\}\s*,\s*я", "Я", text)
        for k, v in vals.items():
            text = text.replace("{" + k + "}", str(v))
        return text, link

    # ---------- API админки ----------
    async def api_list(self, req):
        cfg = await self.cfg()
        bot = await self.bot_name()
        docs = await asyncio.to_thread(lambda: list(self.col().stream()))
        regs = {}
        try:
            for u in await self.a.users():
                if u.get("ad_source"):
                    regs[u["ad_source"]] = regs.get(u["ad_source"], 0) + 1
        except Exception:
            pass
        items = []
        for d in docs:
            it = {"id": d.id, **(d.to_dict() or {})}
            it["text"], it["ref"] = self.message(cfg, it, bot)
            it["regs"] = regs.get(d.id, 0)
            items.append(it)
        items.sort(key=lambda x: -int(x.get("members") or 0))
        sec = await self.secret()
        api_id, api_hash = self.api_keys(sec)
        tg_ok = bool(sec.get("session") and api_id and api_hash)
        return web.json_response({
            "items": items, "statuses": STATUSES, "state": self.state, "bot": bot,
            "cfg": {k: v for k, v in cfg.items() if k not in ("tgstat_token",)},
            "tgstat": bool(self.tgstat_token(cfg)), "tgstatEnv": bool(os.getenv("TGSTAT_TOKEN", "").strip()),
            "tg": {"ok": tg_ok, "me": sec.get("me", ""), "waitingCode": bool(self._login), "keysEnv": bool(os.getenv("TG_API_ID") and os.getenv("TG_API_HASH")),
                   "apiId": str(sec.get("api_id") or "")},
        })

    async def api_item(self, req):
        key = req.match_info["id"]
        b = await req.json()
        ref = self.col().document(key)
        if b.get("delete"):
            await asyncio.to_thread(ref.delete)
            return web.json_response({"ok": True})
        upd = {}
        if "status" in b:
            if b["status"] not in STATUSES:
                raise ValueError("Неизвестный статус")
            upd["status"] = b["status"]
            if b["status"] == "sent":
                upd["sent_at"] = int(time.time())
        if "note" in b:
            upd["note"] = str(b["note"])[:1000]
        if "contact" in b:
            upd["contact"] = re.sub(r"^(@|https?://t\.me/|t\.me/)", "", str(b["contact"]).strip())[:40]
            upd["contact_manual"] = True
        if upd:
            upd["updated_at"] = int(time.time())
            await asyncio.to_thread(ref.update, upd)
        return web.json_response({"ok": True})

    async def api_add(self, req):
        """Добавить канал вручную: @username или ссылка t.me/…"""
        b = await req.json()
        m = re.search(r"([A-Za-z][A-Za-z0-9_]{3,31})\s*$", str(b.get("username", "")).strip().rstrip("/"))
        if not m:
            raise ValueError("Укажите @username или ссылку t.me/…")
        u = m.group(1)
        item = {"id": slug(u), "username": u, "title": str(b.get("title") or u)[:200], "about": "", "members": int(b.get("members") or 0),
                "kind": "channel", "link": f"t.me/{u}", "country": str(b.get("country") or ""), "city": str(b.get("city") or ""),
                "source": "manual", "contact": re.sub(r"^@", "", str(b.get("contact") or "").strip())[:40]}
        client = None
        try:
            client = await self.tg_client()
        except Exception:
            pass
        if client:  # подтягиваем название, описание и подписчиков
            try:
                from telethon import functions
                full = await client(functions.channels.GetFullChannelRequest(u))
                ch = full.chats[0]
                item.update(title=ch.title or u, about=full.full_chat.about or "", members=int(full.full_chat.participants_count or 0),
                            kind="chat" if getattr(ch, "megagroup", False) else "channel")
                item["contact"] = item["contact"] or find_contact(item["about"], u)
            except Exception as e:  # noqa: BLE001
                logging.warning(f"outreach add {u}: {e}")
        await self.upsert(item)
        return web.json_response({"ok": True})

    async def api_settings(self, req):
        b = await req.json()
        upd = {}
        for k in ("cities", "queries", "template", "my_name"):
            if k in b:
                upd[k] = str(b[k])[:20000]
        if "template" in upd and not upd["template"].strip():
            upd["template"] = DEFAULT_TEMPLATE
        if "cities" in upd and not parse_cities(upd["cities"]):
            raise ValueError("Города: по строке на страну, «Країна: Місто1; Місто2/варіант»")
        for k, lo, hi in (("min_members", 0, 10_000_000), ("every_hours", 1, 24 * 30), ("max_queries", 1, 500)):
            if k in b:
                upd[k] = max(lo, min(hi, int(b[k] or 0)))
        for k in ("auto", "notify"):
            if k in b:
                upd[k] = bool(b[k])
        if b.get("tgstat_token") is not None and str(b["tgstat_token"]).strip() != "•••":
            upd["tgstat_token"] = str(b["tgstat_token"]).strip()[:200]
        await self.save_cfg(upd)
        return web.json_response({"ok": True})

    async def api_run(self, req):
        if self.state["running"]:
            return web.json_response({"ok": True, "already": True})
        asyncio.create_task(self.run(manual=True))
        await asyncio.sleep(0.2)
        return web.json_response({"ok": True})

    async def api_state(self, req):
        return web.json_response(self.state)

    # ---------- вход в Telegram-аккаунт ----------
    async def api_tg(self, req):
        b = await req.json()
        act = b.get("action")
        from telethon import TelegramClient, errors
        from telethon.sessions import StringSession
        if act == "logout":
            if self._tg:
                try:
                    await self._tg.log_out()
                except Exception:
                    pass
            self._tg = None
            await asyncio.to_thread(self.db.collection("system").document("outreach_secret").set, {})
            return web.json_response({"ok": True})
        if act == "start":
            sec = await self.secret()
            api_id = str(b.get("api_id") or "").strip() or str(sec.get("api_id") or "")
            api_hash = str(b.get("api_hash") or "").strip() or str(sec.get("api_hash") or "")
            env_id, env_hash = os.getenv("TG_API_ID", "").strip(), os.getenv("TG_API_HASH", "").strip()
            api_id, api_hash = env_id or api_id, env_hash or api_hash
            phone = re.sub(r"[^\d+]", "", str(b.get("phone") or ""))
            if not (api_id.isdigit() and api_hash and len(phone) >= 8):
                raise ValueError("Нужны api_id, api_hash (my.telegram.org → API development tools) и номер телефона")
            if self._login:
                try:
                    await self._login["client"].disconnect()
                except Exception:
                    pass
            c = TelegramClient(StringSession(), int(api_id), api_hash)
            await c.connect()
            try:
                sent = await c.send_code_request(phone)
            except errors.PhoneNumberInvalidError:
                raise ValueError("Неверный номер телефона")
            except errors.ApiIdInvalidError:
                raise ValueError("Неверные api_id / api_hash")
            self._login = {"client": c, "phone": phone, "hash": sent.phone_code_hash, "api_id": int(api_id), "api_hash": api_hash}
            return web.json_response({"ok": True, "codeSent": True})
        if act == "code":
            L = self._login
            if not L:
                raise ValueError("Сначала запросите код")
            c = L["client"]
            try:
                if b.get("password"):
                    await c.sign_in(password=str(b["password"]))
                else:
                    await c.sign_in(L["phone"], str(b.get("code") or "").strip(), phone_code_hash=L["hash"])
            except errors.SessionPasswordNeededError:
                return web.json_response({"ok": False, "needPassword": True})
            except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
                raise ValueError("Код неверный или устарел — запросите новый")
            except errors.PasswordHashInvalidError:
                raise ValueError("Неверный пароль двухэтапной проверки")
            me = await c.get_me()
            await asyncio.to_thread(self.db.collection("system").document("outreach_secret").set,
                                    {"api_id": L["api_id"], "api_hash": L["api_hash"], "session": c.session.save(),
                                     "me": f"{me.first_name or ''} @{me.username or ''} {me.phone and '+' + me.phone[:4] + '…' or ''}".strip()})
            self._tg, self._login = c, None
            return web.json_response({"ok": True})
        raise ValueError("Неизвестное действие")

    def setup(self, r):
        r.add_get("/admin/api/outreach", self.api_list)
        r.add_post("/admin/api/outreach/item/{id}", self.api_item)
        r.add_post("/admin/api/outreach/add", self.api_add)
        r.add_post("/admin/api/outreach/settings", self.api_settings)
        r.add_post("/admin/api/outreach/run", self.api_run)
        r.add_get("/admin/api/outreach/state", self.api_state)
        r.add_post("/admin/api/outreach/tg", self.api_tg)

