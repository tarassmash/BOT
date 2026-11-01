"""Дизайн застосунку з адмінки: кольори, шрифти, тексти онбордингу й картинки екранів (генерація через fal.ai).

Адмінка → вкладка «Дизайн». Ключ fal.ai (FAL_KEY) лише на сервері.
Застосунок читає дизайн з /app/api/design (публічно — там немає нічого секретного),
картинки — з /app/asset/{slot}?v=… (зберігаються у Firestore частинами).
"""
import asyncio
import base64
import json
import logging
import os
import re
import time
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

FAL_HOSTS = ("fal.media", "fal.ai", "fal.run", "storage.googleapis.com")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# кольори теми (ключ → підпис в адмінці, значення за замовчуванням)
THEME = {
    "night": ("Фон", "#0E1E45"),
    "glow": ("Свечение сверху", "#1B3375"),
    "deep": ("Карточки и панели", "#172B5C"),
    "deep2": ("Поля и кнопки", "#20376E"),
    "gold": ("Главный цвет (кнопки)", "#F3C443"),
    "gold_ink": ("Текст на главных кнопках", "#1A2340"),
    "red": ("Лайк и сердца", "#E2394B"),
    "linen": ("Текст", "#F5EEE2"),
    "hint": ("Второстепенный текст", "#A7B3CF"),
}
# лише шрифти з українською кирилицею
DISPLAY_FONTS = ["Unbounded", "Montserrat Alternates", "Comfortaa", "Playfair Display", "Lora", "Rubik", "Manrope", "Russo One"]
BODY_FONTS = ["Onest", "Manrope", "Montserrat", "Rubik", "Nunito", "Inter", "Open Sans", "Roboto"]

# картинки екранів: розмір для генерації й підказка за замовчуванням
SLOTS = {
    "hero": {"title": "Онбординг, экран 1 (главная картинка)", "w": 1152, "h": 960,
             "prompt": "Warm cinematic illustration of a young Ukrainian couple in love seen from behind, both wearing modern embroidered vyshyvanka shirts, she has a flower wreath with blue and yellow ribbons, holding hands, looking at an evening European city with lights at sunset, deep cobalt blue and golden palette, soft glow, no text"},
    "ob2": {"title": "Онбординг, экран 2 «Чем мы лучше»", "w": 1152, "h": 640,
            "prompt": "Two young Ukrainians laughing together in a cozy cafe abroad, warm golden evening light, candid photo, subtle blue and yellow accents, no text, no logos"},
    "ob3": {"title": "Онбординг, экран 3 «Фишки»", "w": 1152, "h": 640,
            "prompt": "Young woman and man meeting for coffee on a sunny European street terrace, friendly smiles, candid lifestyle photo, warm tones, no text, no logos"},
    "ob4": {"title": "Онбординг, экран 4 «Безопасность»", "w": 1152, "h": 640,
            "prompt": "Calm cozy evening scene, couple walking along a seaside promenade in a European city, soft golden light, sense of trust and safety, no faces close up, no text"},
    "match": {"title": "Фон экрана «Це взаємно!»", "w": 864, "h": 1536,
              "prompt": "Abstract festive background, soft bokeh lights in deep cobalt blue and warm gold, gentle hearts, vertical, no text, no people"},
    "empty": {"title": "Пустая лента (все анкеты просмотрены)", "w": 1024, "h": 768,
              "prompt": "Minimal cute illustration of a coffee cup and a small heart on a table by a window with evening city lights, cobalt blue and gold palette, no text"},
}
SLIDES = 4
# тексти онбордингу за замовчуванням (те, що бачить людина, поки в адмінці нічого не змінено)
DEFAULT_SLIDES = [
    {"title": "Наші поруч.", "lead": "Знайомства для українців, які живуть за кордоном. Українською — з людьми, які розуміють тебе з півслова."},
    {"title": "Чим ми інші", "lead": "Звичайні додатки знайомств зроблені для всіх. FlirtUA — для наших.", "items": [
        {"title": "Хто в стрічці", "them": "Місцеві й туристи", "us": "Українці, які живуть поруч"},
        {"title": "Мова", "them": "Англійська чи місцева", "us": "Українська з першого слова"},
        {"title": "Спільне", "them": "Пояснюєш, звідки ти", "us": "Земляки з твого міста — першими 🤝"},
        {"title": "Як почати", "them": "Новий додаток, пошта, пароль", "us": "Просто в Telegram, за хвилину"}]},
    {"title": "Зроблено для нашого життя тут", "lead": "Того, що нижче, немає в звичайних додатках.", "items": [
        {"icon": "🤝", "title": "Земляки", "text": "Люди з твого Львова, Харкова чи Одеси — вище в стрічці й з позначкою."},
        {"icon": "📍", "title": "Поруч з тобою", "text": "Анкети за відстанню: твоє місто, твій район. Іншим видно лише км, не адресу."},
        {"icon": "☕️", "title": "Вільний сьогодні", "text": "Позначка на 24 години — кава після роботи без тижнів переписки."},
        {"icon": "💌", "title": "Суперлайк з повідомленням", "text": "Раз на добу — напиши одразу, щоб тебе помітили першим(-ою)."}]},
    {"title": "Безпечно й по-людськи", "lead": "Знайомства, у яких хочеться залишитися.", "items": [
        {"icon": "🔞", "title": "Лише 18+", "text": "Тільки дорослі — вік вказується при реєстрації, анкети молодших не показуємо."},
        {"icon": "🚨", "title": "Скарги перевіряє модератор", "text": "Фейки, спам і образи — одна кнопка, людина перевіряє анкету."},
        {"icon": "⏸", "title": "Пауза будь-коли", "text": "Сховай анкету на час — лайки й симпатії збережуться."},
        {"icon": "🎁", "title": "Безкоштовно", "text": "Знайомитись можна без оплати. Преміум — за бажанням."}]},
]


def merged_slides(slides):
    out = []
    for i, base in enumerate(DEFAULT_SLIDES):
        cur = (slides or [{}] * SLIDES)[i] if i < len(slides or []) else {}
        out.append({"title": cur.get("title") or base["title"], "lead": cur.get("lead") or base["lead"],
                    **({"items": cur.get("items") or base["items"]} if "items" in base else {})})
    return out
CHUNK = 700_000  # символів base64 на документ Firestore (ліміт документа ~1 МБ)


def default_design():
    return {"theme": {k: v[1] for k, v in THEME.items()}, "fonts": {"display": "Unbounded", "body": "Onest"},
            "slides": [{} for _ in range(SLIDES)], "assets": {}}


def clean_design(d):
    """Перевіряє все, що прийшло з адмінки чи від нейромережі."""
    out = default_design()
    d = d or {}
    for k, v in (d.get("theme") or {}).items():
        if k in THEME and isinstance(v, str) and HEX.match(v.strip()):
            out["theme"][k] = v.strip().upper()
    f = d.get("fonts") or {}
    if f.get("display") in DISPLAY_FONTS:
        out["fonts"]["display"] = f["display"]
    if f.get("body") in BODY_FONTS:
        out["fonts"]["body"] = f["body"]
    for i, sl in enumerate((d.get("slides") or [])[:SLIDES]):
        sl = sl or {}
        o = {}
        for key, lim in (("title", 80), ("lead", 300)):
            v = str(sl.get(key) or "").strip()
            if v:
                o[key] = v[:lim]
        items = []
        for it in (sl.get("items") or [])[:5]:
            if not isinstance(it, dict):
                continue
            g = lambda k, n: str(it.get(k) or "").strip()[:n]
            if i == 1:  # «Чим ми інші»: тема | у звичайних | у нас
                if g("title", 40) and g("us", 90):
                    items.append({"title": g("title", 40), "them": g("them", 90), "us": g("us", 90)})
            elif g("title", 60):
                items.append({"icon": g("icon", 4), "title": g("title", 60), "text": g("text", 160)})
        if items:
            o["items"] = items
        out["slides"][i] = o
    for k, v in (d.get("assets") or {}).items():
        if k in SLOTS and isinstance(v, (int, float)) and v > 0:
            out["assets"][k] = int(v)
    for k, v in (d.get("prompts") or {}).items():
        if k in SLOTS and isinstance(v, str) and v.strip():
            out.setdefault("prompts", {})[k] = v.strip()[:1200]
    return out


class DesignTool:
    def __init__(self, admin):
        self.a = admin            # admin.Admin: authed(), c.db
        self._design = None
        self._at = 0
        self._assets = {}         # slot → (ts, bytes, mime)
        self._session = None

    @property
    def db(self):
        return self.a.c.db

    @property
    def key(self):
        return os.getenv("FAL_KEY", "").strip()

    async def session(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300))
        return self._session

    # ---------- збереження ----------
    async def load(self, fresh=False):
        if self._design is not None and not fresh and time.time() - self._at < 30:
            return self._design
        try:
            doc = await asyncio.to_thread(self.db.collection("system").document("design").get)
            self._design = clean_design(doc.to_dict() if doc.exists else None)
        except Exception as e:  # noqa: BLE001
            logging.warning(f"design load: {e}")
            self._design = self._design or default_design()
        self._at = time.time()
        return self._design

    async def save(self, d):
        d = clean_design(d)
        await asyncio.to_thread(self.db.collection("system").document("design").set, d)
        self._design, self._at = d, time.time()
        return d

    async def put_asset(self, slot, data, mime):
        b64 = base64.b64encode(data).decode()
        parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
        ref = self.db.collection("design_assets").document(slot)
        ts = int(time.time())

        def run():
            for i, p in enumerate(parts):
                ref.collection("parts").document(str(i)).set({"d": p})
            ref.set({"n": len(parts), "mime": mime, "ts": ts})
        await asyncio.to_thread(run)
        self._assets[slot] = (ts, data, mime)
        return ts

    async def get_asset(self, slot):
        if slot in self._assets:
            return self._assets[slot]
        ref = self.db.collection("design_assets").document(slot)

        def run():
            head = ref.get()
            if not head.exists:
                return None
            h = head.to_dict() or {}
            b64 = "".join((ref.collection("parts").document(str(i)).get().to_dict() or {}).get("d", "") for i in range(int(h.get("n") or 0)))
            return int(h.get("ts") or 0), base64.b64decode(b64), h.get("mime") or "image/jpeg"
        got = await asyncio.to_thread(run)
        if got:
            self._assets[slot] = got
        return got

    # ---------- fal.ai ----------
    async def fal(self, app_id, payload):
        if not self.key:
            raise ValueError("Нет FAL_KEY: добавьте ключ fal.ai в Railway → Variables сервиса бота")
        s = await self.session()
        async with s.post(f"https://fal.run/{app_id}", headers={"authorization": f"Key {self.key}", "content-type": "application/json"}, json=payload) as r:
            txt = await r.text()
            if r.status != 200:
                try:
                    det = json.loads(txt).get("detail")
                    det = det if isinstance(det, str) else json.dumps(det, ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    det = txt[:300]
                raise ValueError(f"fal.ai: {det}")
            return json.loads(txt)

    async def gen_image(self, slot, prompt, n=1):
        sz = SLOTS[slot]
        d = await self.fal("openai/gpt-image-2", {
            "prompt": prompt.strip() + ". High quality, no text, no letters, no logos, no watermarks.",
            "image_size": {"width": sz["w"], "height": sz["h"]}, "quality": "medium",
            "num_images": max(1, min(3, int(n))), "output_format": "jpeg"})
        urls = [i.get("url") for i in d.get("images") or [] if i.get("url")]
        if not urls:
            raise ValueError("Модель не вернула картинку")
        return urls

    async def llm_json(self, prompt):
        last = None
        for model in ("anthropic/claude-sonnet-4.5", "google/gemini-2.5-flash", None):
            try:
                d = await self.fal("fal-ai/any-llm", {"prompt": prompt, "system_prompt": "You are a senior brand and UI designer. Answer with valid JSON only.", **({"model": model} if model else {})})
                m = re.search(r"\{[\s\S]*\}", str(d.get("output") or ""))
                if not m:
                    raise ValueError("нейросеть вернула не JSON")
                return json.loads(m.group(0))
            except ValueError as e:
                last = e
                if "FAL_KEY" in str(e):
                    break
        raise last

    # ---------- API адмінки ----------
    def need_auth(self, req):
        if not self.a.authed(req):
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Нужно войти"}), content_type="application/json")

    async def api_get(self, req):
        self.need_auth(req)
        d = await self.load(fresh=True)
        return web.json_response({
            "design": d, "falKey": bool(self.key),
            "theme": {k: {"label": v[0], "default": v[1]} for k, v in THEME.items()},
            "fonts": {"display": DISPLAY_FONTS, "body": BODY_FONTS},
            "slots": {k: {"title": v["title"], "w": v["w"], "h": v["h"], "prompt": v["prompt"]} for k, v in SLOTS.items()},
            "defaultSlides": DEFAULT_SLIDES,
        })

    async def api_save(self, req):
        self.need_auth(req)
        b = await req.json()
        cur = await self.load(fresh=True)
        b["assets"] = cur.get("assets", {})  # картинки міняються окремо
        return web.json_response({"design": await self.save(b)})

    async def api_generate(self, req):
        self.need_auth(req)
        b = await req.json()
        slot = b.get("slot")
        if slot not in SLOTS:
            raise ValueError("Неизвестный экран")
        prompt = str(b.get("prompt") or SLOTS[slot]["prompt"]).strip()
        if len(prompt) < 5:
            raise ValueError("Опишите картинку")
        return web.json_response({"urls": await self.gen_image(slot, prompt, b.get("n", 1))})

    async def api_use(self, req):
        """Поставити картинку в застосунок: завантажуємо з fal і зберігаємо в себе (посилання fal тимчасові)."""
        self.need_auth(req)
        b = await req.json()
        slot, url = b.get("slot"), str(b.get("url") or "")
        if slot not in SLOTS:
            raise ValueError("Неизвестный экран")
        d = await self.load(fresh=True)
        if b.get("reset"):
            d["assets"].pop(slot, None)
            self._assets.pop(slot, None)
            return web.json_response({"design": await self.save(d)})
        u = urlparse(url)
        if u.scheme != "https" or not any(u.hostname == h or (u.hostname or "").endswith("." + h) for h in FAL_HOSTS):
            raise ValueError("Недопустимый адрес картинки")
        s = await self.session()
        async with s.get(url) as r:
            if r.status != 200:
                raise ValueError(f"Картинка не скачалась ({r.status})")
            data, mime = await r.read(), (r.content_type or "image/jpeg")
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("Картинка больше 4 МБ")
        if not mime.startswith("image/"):
            raise ValueError("Это не картинка")
        d["assets"][slot] = await self.put_asset(slot, data, mime)
        if b.get("prompt"):
            d.setdefault("prompts", {})[slot] = str(b["prompt"])[:1200]
        return web.json_response({"design": await self.save(d)})

    async def api_ai(self, req):
        """«Придумати дизайн цілком»: стиль словами → кольори, шрифти, тексти онбордингу й описи картинок."""
        self.need_auth(req)
        b = await req.json()
        wish = str(b.get("prompt") or "").strip()[:800]
        if len(wish) < 3:
            raise ValueError("Опишите стиль")
        cur = await self.load(fresh=True)
        prompt = f"""Design a visual theme and onboarding for FlirtUA — a dating Telegram mini app for Ukrainians living abroad (Spain, Poland, Germany, Czech Republic, Italy).
Key features: profiles of Ukrainians nearby by distance; "Земляки" — people from your hometown in Ukraine shown first; "Вільний сьогодні" — 24h badge for a coffee today; super-like with a message; works right inside Telegram; Ukrainian language; 18+; reports checked by a moderator; free, Premium optional.
OWNER'S STYLE WISH: {wish}
Current theme for reference: {json.dumps(cur["theme"])}

Rules:
- Dark or light theme is fine, but text must be readable: "linen" (text) must strongly contrast with "night" (background) and "deep" (cards); "gold_ink" must contrast with "gold".
- "gold" is the main action color, "red" is the like/heart color.
- Fonts: display from {DISPLAY_FONTS}, body from {BODY_FONTS}.
- 4 onboarding slides in natural modern Ukrainian (no surzhyk). Slide 1: hero title (max 4 words) + lead. Slide 2: "why we are different from ordinary dating apps": title + lead + 4 comparison items {{"title": topic, "them": how it is in ordinary apps, "us": how it is in FlirtUA}}. Slide 3: unique features, 4 items. Slide 4: safety and free, 4 items. Feature items: icon = one emoji, title max 5 words, text max 110 chars. No fake numbers, no guarantees of love, no names of other apps.
- Image prompts in English for each slot, matching the style; people look Ukrainian/Eastern European adults; no text in images.

JSON only:
{{"theme":{{"night":"#","glow":"#","deep":"#","deep2":"#","gold":"#","gold_ink":"#","red":"#","linen":"#","hint":"#"}},
"fonts":{{"display":"","body":""}},
"slides":[{{"title":"","lead":""}},{{"title":"","lead":"","items":[{{"title":"","them":"","us":""}}]}},{{"title":"","lead":"","items":[{{"icon":"","title":"","text":""}}]}},{{"title":"","lead":"","items":[{{"icon":"","title":"","text":""}}]}}],
"prompts":{{"hero":"","ob2":"","ob3":"","ob4":"","match":"","empty":""}}}}"""
        j = await self.llm_json(prompt)
        j["assets"] = cur.get("assets", {})
        return web.json_response({"design": clean_design(j)})

    # ---------- публічно для застосунку ----------
    async def app_design(self, req):
        d = await self.load()
        return web.json_response({"theme": d["theme"], "fonts": d["fonts"], "assets": d["assets"], "slides": merged_slides(d["slides"])}, headers={"cache-control": "no-cache"})

    async def app_asset(self, req):
        slot = req.match_info["slot"]
        if slot not in SLOTS:
            raise web.HTTPNotFound()
        got = await self.get_asset(slot)
        if not got:
            raise web.HTTPNotFound()
        ts, data, mime = got
        return web.Response(body=data, content_type=mime, headers={"cache-control": "public, max-age=31536000, immutable" if req.query.get("v") else "no-cache"})

    def setup(self, r):
        r.add_get("/admin/api/design", self.api_get)
        r.add_post("/admin/api/design", self.api_save)
        r.add_post("/admin/api/design/generate", self.api_generate)
        r.add_post("/admin/api/design/use", self.api_use)
        r.add_post("/admin/api/design/ai", self.api_ai)
        r.add_get("/app/api/design", self.app_design)
        r.add_get("/app/asset/{slot}", self.app_asset)
