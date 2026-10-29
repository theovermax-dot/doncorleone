#!/usr/bin/env python3
"""Telegram-бот пиццерии Don Corleone (Батуми).

Меню с фото, корзина, оформление доставки или самовывоза, заказ уходит в чат ресторана
с кнопками статусов, клиент получает уведомления о каждом шаге.

Базы данных нет. Всё состояние живёт в самих сообщениях Telegram:
  * корзина зашита в кнопки (короткий код из цифр);
  * шаги оформления хранятся в скрытой ссылке внутри сообщения бота;
  * заказ хранится в сообщении в чате ресторана.
Поэтому бот спокойно работает на Vercel (вебхук) и локально (polling).
"""
import base64
import html
import json
import logging
import os
import re
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

import menu as M

BASE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------- настройки
def load_env(path):
    env = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = {**load_env(os.path.join(BASE, ".env")), **os.environ}
TOKEN = ENV.get("BOT_TOKEN", "").strip()
API = ENV.get("TG_API_BASE", "https://api.telegram.org").rstrip("/") + "/bot" + TOKEN

RESTAURANT = "Don Corleone | Pizza Batumi"
ADDRESS = "Батуми, ул. Г. Брцкинвале 63 б"
INSTAGRAM = "don_corleone2026"
PHONE = ENV.get("PHONE", "").strip()
PAYMENTS = [p.strip() for p in ENV.get("PAYMENTS", "Наличными,Картой").split(",") if p.strip()]
TZ = timezone(timedelta(hours=4))               # Тбилиси
MAX_QTY = 9
CUR = "₾"
STATE_URL = "https://t.me/doncorleone_state#"   # скрытая ссылка с состоянием оформления

log = logging.getLogger("doncorleone")

file_ids = {}   # кэш file_id фото, только на время жизни процесса


RUNTIME = {}   # заполняет api/webhook.py на каждый запрос: base_url, secret и orders (чат заказов)


def orders_chat():
    """Чат ресторана: сначала переменная ORDERS_CHAT_ID, затем значение из адреса вебхука."""
    try:
        return int(ENV.get("ORDERS_CHAT_ID", "").strip())
    except ValueError:
        return RUNTIME.get("orders")


# ------------------------------------------------------------ Telegram API
class TgError(Exception):
    def __init__(self, desc, code=None, params=None):
        super().__init__(desc)
        self.desc, self.code, self.params = desc, code, params or {}


HTTP = requests.Session()


def _prep(d):
    out = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        out[k] = json.dumps(v) if isinstance(v, (dict, list, bool)) else v
    return out


def api(method, data=None, files=None, timeout=40):
    for attempt in range(3):
        r = HTTP.post(f"{API}/{method}", data=_prep(data), files=files, timeout=timeout)
        try:
            j = r.json()
        except ValueError:
            raise TgError(f"bad response {r.status_code}")
        if j.get("ok"):
            return j["result"]
        if j.get("error_code") == 429 and attempt < 2:
            time.sleep(min(int(j.get("parameters", {}).get("retry_after", 2)), 5))
            continue
        raise TgError(j.get("description", "error"), j.get("error_code"), j.get("parameters"))


def send_message(chat, text, kb=None, plain=False):
    return api("sendMessage", {"chat_id": chat, "text": text, "parse_mode": None if plain else "HTML",
                               "disable_web_page_preview": True, "reply_markup": kb})


def edit_text(chat, mid, text, kb=None, plain=False):
    return api("editMessageText", {"chat_id": chat, "message_id": mid, "text": text,
                                   "parse_mode": None if plain else "HTML",
                                   "disable_web_page_preview": True, "reply_markup": kb})


def edit_markup(chat, mid, kb):
    return api("editMessageReplyMarkup", {"chat_id": chat, "message_id": mid, "reply_markup": kb})


def delete_message(chat, mid):
    try:
        api("deleteMessage", {"chat_id": chat, "message_id": mid})
    except TgError:
        pass


def photo_file(name):
    """Фото лежат в photos/, но если их загрузили прямо в корень репозитория, берём оттуда."""
    for folder in ("photos", ""):
        p = os.path.join(BASE, folder, name + ".jpg")
        if os.path.exists(p):
            return p
    return os.path.join(BASE, "photos", name + ".jpg")


def _remember(name, res):
    try:
        file_ids[name] = res["photo"][-1]["file_id"]
    except (KeyError, IndexError, TypeError):
        pass


def send_photo(chat, name, caption, kb=None):
    base = {"chat_id": chat, "caption": caption, "parse_mode": "HTML", "reply_markup": kb}
    if name in file_ids:
        try:
            return api("sendPhoto", {**base, "photo": file_ids[name]})
        except TgError:
            file_ids.pop(name, None)
    with open(photo_file(name), "rb") as fh:
        res = api("sendPhoto", base, files={"photo": (name + ".jpg", fh, "image/jpeg")}, timeout=60)
    _remember(name, res)
    return res


def edit_photo(chat, mid, name, caption, kb=None):
    base = {"chat_id": chat, "message_id": mid, "reply_markup": kb}
    if name in file_ids:
        try:
            media = {"type": "photo", "media": file_ids[name], "caption": caption, "parse_mode": "HTML"}
            return api("editMessageMedia", {**base, "media": media})
        except TgError as e:
            if "not modified" in e.desc:
                raise
            file_ids.pop(name, None)
    media = {"type": "photo", "media": "attach://f", "caption": caption, "parse_mode": "HTML"}
    with open(photo_file(name), "rb") as fh:
        res = api("editMessageMedia", {**base, "media": media},
                  files={"f": (name + ".jpg", fh, "image/jpeg")}, timeout=60)
    _remember(name, res)
    return res


def show(chat, msg, text, kb=None, photo=None):
    """Показать экран: правим текущее сообщение, если можно, иначе удаляем и шлём новое."""
    if msg is not None:
        is_photo = bool(msg.get("photo"))
        try:
            if photo and is_photo:
                edit_photo(chat, msg["message_id"], photo, text, kb)
                return
            if not photo and not is_photo:
                edit_text(chat, msg["message_id"], text, kb)
                return
        except TgError as e:
            if "not modified" in e.desc:
                return
            log.warning("edit failed (%s), resend", e.desc)
        delete_message(chat, msg["message_id"])
    if photo:
        send_photo(chat, photo, text, kb)
    else:
        send_message(chat, text, kb)


# ----------------------------------------------- корзина и скрытое состояние
# Корзина: строка из цифр, по одной на позицию меню (количество 0-9).
N_ITEMS = len(M.ITEMS)
IDX = {it["id"]: i for i, it in enumerate(M.ITEMS)}
EMPTY = "0" * N_ITEMS


def cart_decode(code):
    code = (code or "").ljust(N_ITEMS, "0")[:N_ITEMS]
    return [int(ch) if ch.isdigit() else 0 for ch in code]


def cart_encode(cart):
    return "".join(str(max(0, min(MAX_QTY, q))) for q in cart)


def cart_lines(cart):
    return [(M.ITEMS[i], q) for i, q in enumerate(cart) if q > 0]


def cart_total(cart):
    return sum(it["price"] * q for it, q in cart_lines(cart))


def cart_count(cart):
    return sum(cart)


def cart_label(cart):
    n = cart_count(cart)
    return "🛒 Корзина" if not n else f"🛒 {n} · {money(cart_total(cart))}"


def with_qty(cart, item_id, delta):
    c = list(cart)
    i = IDX[item_id]
    c[i] = max(0, min(MAX_QTY, c[i] + delta))
    return c


def pack(state):
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode().rstrip("=")


def unpack(s):
    s += "=" * (-len(s) % 4)
    return json.loads(zlib.decompress(base64.urlsafe_b64decode(s)).decode("utf-8"))


def hidden(state):
    """Невидимая ссылка в тексте сообщения, в ней лежит состояние оформления."""
    return f'<a href="{STATE_URL}{pack(state)}">​</a>'


def read_state(msg):
    for e in (msg or {}).get("entities") or []:
        url = e.get("url") or ""
        if e.get("type") == "text_link" and url.startswith(STATE_URL):
            try:
                return unpack(url[len(STATE_URL):])
            except Exception:  # noqa: BLE001
                return None
    return None


# ------------------------------------------------------------- клавиатуры
def btn(text, data=None, url=None):
    b = {"text": text}
    if url:
        b["url"] = url
    else:
        b["callback_data"] = data
    return b


def ik(rows):
    return {"inline_keyboard": rows}


def cb(kind, arg, cart):
    return f"{kind}:{arg}:{cart_encode(cart)}"


def money(n):
    return f"{n} {CUR}"


def esc(s):
    return html.escape(str(s), quote=False)


# -------------------------------------------------------------------- экраны
WELCOME = (
    f"<b>{RESTAURANT}</b>\n"
    "Пицца на томатном или сливочном соусе. Доставка от 20 минут.\n\n"
    "К каждой пицце два фирменных соуса. Выбирайте в меню, оформление занимает минуту.\n\n"
    "<i>Вкус, которому невозможно отказать.</i>"
)


def screen_welcome(chat):
    zero = cart_decode(EMPTY)
    send_photo(chat, "logo", WELCOME, ik([
        [btn("🍕 Открыть меню", cb("menu", "", zero))],
        [btn("🛵 Доставка и оплата", cb("info", "delivery", zero)), btn("📍 Контакты", cb("info", "contacts", zero))]]))


def screen_categories(chat, msg, cart):
    rows = [[btn(f"{c['emoji']} {c['title']}", cb("cat", c["id"], cart))] for c in M.CATEGORIES]
    rows.append([btn(cart_label(cart), cb("cart", "", cart))])
    show(chat, msg, "<b>Меню</b>\nВыберите раздел.", ik(rows))


def screen_category(chat, msg, cat_id, cart):
    cat = next((c for c in M.CATEGORIES if c["id"] == cat_id), None)
    if not cat:
        return screen_categories(chat, msg, cart)
    rows = []
    for it in M.items_in(cat_id):
        q = cart[IDX[it["id"]]]
        mark = f"🛒{q} " if q else ""
        rows.append([btn(f"{mark}{it['name']} · {money(it['price'])}", cb("item", it["id"], cart))])
    rows.append([btn("← Разделы", cb("menu", "", cart)), btn(cart_label(cart), cb("cart", "", cart))])
    text = f"<b>{cat['emoji']} {cat['title']}</b>\nНажмите на пиццу, чтобы увидеть фото и состав."
    show(chat, msg, text, ik(rows))


def screen_item(chat, msg, item_id, cart):
    it = M.ITEMS_BY_ID.get(item_id)
    if not it:
        return screen_categories(chat, msg, cart)
    q = cart[IDX[item_id]]
    tag = f"  ·  {esc(it['tag'])}" if it.get("tag") else ""
    text = f"<b>{esc(it['name'])}</b>{tag}\n<i>{esc(it['desc'])}</i>\n\n<b>{money(it['price'])}</b>"
    if q:
        first = [btn("➖", cb("sub", item_id, cart)), btn(f"{q} шт · {money(q * it['price'])}", "noop"),
                 btn("➕", cb("add", item_id, cart))]
    else:
        first = [btn(f"➕ В корзину · {money(it['price'])}", cb("add", item_id, cart))]
    rows = [first, [btn("← Назад", cb("cat", it["cat"], cart)), btn(cart_label(cart), cb("cart", "", cart))]]
    show(chat, msg, text, ik(rows), photo=it["photo"])


def cart_text(cart):
    out = ["<b>Корзина</b>", ""]
    for n, (it, q) in enumerate(cart_lines(cart), 1):
        out.append(f"{n}. {esc(it['name'])} × {q} = {money(it['price'] * q)}")
    out += ["", f"Итого: <b>{money(cart_total(cart))}</b>",
            "Стоимость доставки ресторан назовёт при подтверждении."]
    return "\n".join(out)


def screen_cart(chat, msg, cart):
    lines = cart_lines(cart)
    if not lines:
        show(chat, msg, "<b>Корзина пуста</b>\nДобавьте что-нибудь из меню.",
             ik([[btn("🍕 Открыть меню", cb("menu", "", cart))]]))
        return
    rows = []
    for it, q in lines:
        rows.append([btn("➖", cb("c-", it["id"], cart)), btn(f"{it['name']} × {q}", cb("item", it["id"], cart)),
                     btn("➕", cb("c+", it["id"], cart))])
    rows.append([btn("✅ Оформить заказ", cb("checkout", "", cart))])
    rows.append([btn("🍕 Добавить ещё", cb("menu", "", cart)), btn("🗑 Очистить", cb("clear", "", cart))])
    show(chat, msg, cart_text(cart), ik(rows))


def screen_info(chat, msg, which, cart):
    back = [btn("← Меню", cb("menu", "", cart))]
    if which == "contacts":
        maps = "https://www.google.com/maps/search/?api=1&query=" + quote(ADDRESS)
        lines = [f"<b>{RESTAURANT}</b>", esc(ADDRESS)]
        if PHONE:
            lines.append(esc(PHONE))
        lines.append(f"Instagram: @{INSTAGRAM}")
        rows = [[btn("📍 Открыть на карте", url=maps)], [btn("Instagram", url=f"https://instagram.com/{INSTAGRAM}")], back]
        show(chat, msg, "\n".join(lines), ik(rows))
    else:
        pays = ", ".join(p.lower() for p in PAYMENTS)
        text = (f"<b>Доставка</b>\nВозим по Батуми, от 20 минут. Время и стоимость зависят от адреса, "
                f"ресторан уточнит их, когда подтвердит заказ.\n\n<b>Оплата при получении:</b> {esc(pays)}.\n\n"
                f"<b>Самовывоз:</b> {esc(ADDRESS)}.")
        show(chat, msg, text, ik([back]))


# -------------------------------------------------------------- оформление
FORCE = {"force_reply": True, "selective": False}


def start_checkout(chat, msg, cart):
    if not cart_lines(cart):
        return screen_cart(chat, msg, cart)
    rows = [[btn("🛵 Доставка", cb("ot", "delivery", cart)), btn("🏃 Самовывоз", cb("ot", "pickup", cart))],
            [btn("← Корзина", cb("cart", "", cart))]]
    show(chat, msg, cart_text(cart) + "\n\n<b>Как получите заказ?</b>", ik(rows))


STEP_TITLE = {"address": "Адрес", "phone": "Телефон", "pay": "Оплата", "comment": "Комментарий"}
PROMPT = {
    "address": ("📍 <b>Куда привезти?</b>\nУлица, дом, подъезд, этаж.", "Улица, дом, подъезд, этаж"),
    "phone": ("📞 <b>Номер для связи</b>\nНапример, +995 555 12 34 56.", "+995 ..."),
    "comment": ("💬 <b>Комментарий к заказу</b>\nНапример, «без лука» или «позвоните у двери».", "Ваш комментарий"),
}


def steps_for(st):
    return ["address", "phone", "pay", "comment"] if st["t"] == "delivery" else ["phone", "pay", "comment"]


def card_text(st):
    """Карточка оформления: одно сообщение, которое обновляется на каждом шаге."""
    steps = steps_for(st)
    cart = cart_decode(st["c"])
    s = st["s"]
    if s == "sent":
        out = [f"✅ <b>Заказ №{st['n']} отправлен в ресторан</b>", "Как только его подтвердят, я напишу сюда."]
    else:
        out = ["<b>Оформление заказа</b>"]
        out.append(f"Шаг {steps.index(s) + 1} из {len(steps)} · {STEP_TITLE[s]}" if s in steps
                   else "Остался один клик · проверьте заказ")
    out.append("")
    for it, q in cart_lines(cart):
        out.append(f"• {esc(it['name'])} × {q}")
    out += ["", f"Итого: <b>{money(cart_total(cart))}</b> <i>(без доставки)</i>", ""]
    out.append("🛵 Доставка" if st["t"] == "delivery" else f"🏃 Самовывоз · {esc(ADDRESS)}")
    if st.get("a"):
        out.append(f"📍 {esc(st['a'])}")
    if st.get("p"):
        out.append(f"📞 {esc(st['p'])}")
    if st.get("y"):
        out.append(f"💳 {esc(st['y'])}")
    if st.get("m"):
        out.append(f"💬 {esc(st['m'])}")
    if s == "pay":
        out += ["", "<b>Как будете платить при получении?</b>"]
    elif s in PROMPT:
        out += ["", "<i>Ответьте на сообщение ниже ⬇️</i>"]
    return "\n".join(out)


def card_kb(st):
    cancel = [btn("✖️ Отменить оформление", "x")]
    s = st["s"]
    if s == "pay":
        return ik([[btn(p, f"pay:{n}") for n, p in enumerate(PAYMENTS)], cancel])
    if s == "comment":
        return ik([[btn("Без комментария", "nc")], cancel])
    if s == "confirm":
        return ik([[btn("✅ Отправить заказ", "ok")], cancel])
    if s == "sent":
        return ik([[btn("🍕 Меню", cb("menu", "", cart_decode(EMPTY)))]])
    return ik([cancel])


def goto(chat, st, step, note=None):
    """Перейти на шаг: убрать прежний вопрос, задать новый и обновить карточку."""
    st["s"] = step
    old = st.pop("k", None)
    if old:
        delete_message(chat, old)
    if step in PROMPT:
        text, hint = PROMPT[step]
        if note:
            text = f"⚠️ {note}\n\n{text}"
        m = send_message(chat, text + hidden(st), {**FORCE, "input_field_placeholder": hint})
        st["k"] = m["message_id"]
    try:
        edit_text(chat, st["w"], card_text(st) + hidden(st), card_kb(st))
    except TgError as e:
        if "not modified" not in e.desc:
            raise



def set_orders_chat(chat):
    """Сделать этот чат получателем заказов. Работает один раз: чат запоминается в адресе вебхука."""
    if orders_chat() is not None:
        return send_message(chat, "Чат для заказов уже настроен.")
    base = RUNTIME.get("base_url")
    if not base:   # локальный запуск без вебхука
        return send_message(chat, f"ID этого чата: <code>{chat}</code>\nВпишите его в ORDERS_CHAT_ID в файле .env.")
    try:
        api("setWebhook", {"url": f"{base}?orders={chat}", "secret_token": RUNTIME.get("secret"),
                           "max_connections": 1, "allowed_updates": ["message", "callback_query"]})
    except TgError as e:
        log.error("setWebhook: %s", e)
        return send_message(chat, "Не получилось сохранить чат. Попробуйте ещё раз.")
    RUNTIME["orders"] = chat
    send_message(chat, "✅ Готово, заказы будут приходить в этот чат.")


def move_orders_chat(new):
    """Группа стала супергруппой и у неё сменился id: переписываем адрес вебхука на новый."""
    if ENV.get("ORDERS_CHAT_ID", "").strip():
        log.error("чат заказов сменил id на %s, обновите ORDERS_CHAT_ID", new)
        return
    RUNTIME["orders"] = new
    base = RUNTIME.get("base_url")
    if base:
        api("setWebhook", {"url": f"{base}?orders={new}", "secret_token": RUNTIME.get("secret"),
                           "max_connections": 1, "allowed_updates": ["message", "callback_query"]})
    log.warning("чат заказов переехал: %s", new)


def handle_reply(msg, st):
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    step = st.get("s")
    st["k"] = msg["reply_to_message"]["message_id"]      # вопрос, на который ответили
    delete_message(chat, msg["message_id"])               # ответ клиента убираем, чтобы чат был чистым
    if not text:
        return goto(chat, st, step, "Нужен текст, а не файл.")
    if step == "address":
        if len(text) < 5:
            return goto(chat, st, step, "Напишите адрес подробнее.")
        st["a"] = text[:200]
        return goto(chat, st, "phone")
    if step == "phone":
        if not 9 <= len(re.sub(r"\D", "", text)) <= 15:
            return goto(chat, st, step, "Не похоже на номер. Нужен код страны, например +995.")
        st["p"] = text[:30]
        return goto(chat, st, "pay")
    if step == "comment":
        st["m"] = "" if text in ("-", "—", "нет", "Нет") else text[:200]
        return goto(chat, st, "confirm")



# -------------------------------------------------------------------- заказы
STATUS_TITLE = {"new": "🆕 Новый", "accepted": "✅ Принят, готовим", "onway": "🛵 Курьер выехал",
                "ready": "🍕 Готов к выдаче", "done": "✔️ Выдан", "cancelled": "❌ Отменён"}

CUSTOMER_NOTE = {
    "accepted": "Заказ №{id} принят, уже готовим 🍕",
    "onway": "Заказ №{id}: курьер выехал 🛵",
    "ready": "Заказ №{id} готов. Ждём вас: " + ADDRESS,
    "done": "Заказ №{id} выдан. Приятного аппетита! Дон Корлеоне доволен.",
    "cancelled": "Заказ №{id} отменён рестораном. Если это неожиданно, напишите нам в Instagram @" + INSTAGRAM + ".",
}


def render_order(o, number):
    """Текст заказа для чата ресторана. Обычный текст без разметки: его же читаем при смене статуса."""
    out = [f"🧾 Заказ №{number} · {o['created']}", ""]
    out.append("🛵 Доставка" if o["type"] == "delivery" else "🏃 Самовывоз")
    out.append("Клиент: " + o["name"] + (f" (@{o['username']})" if o.get("username") else ""))
    out.append(f"Телефон: {o['phone']}")
    if o["type"] == "delivery":
        out.append(f"Адрес: {o['address']}")
    out.append(f"Оплата: {o['pay']}")
    out.append("")
    for it in o["items"]:
        out.append(f"• {it['name']} × {it['qty']} = {money(it['price'] * it['qty'])}")
    out += ["", f"Итого: {money(o['total'])} (без доставки)"]
    if o.get("comment"):
        out.append(f"Комментарий: {o['comment']}")
    out += ["", "Статус: " + STATUS_TITLE["new"]]
    return "\n".join(out)


def status_kb(status, cust_chat, otype):
    t = "d" if otype == "delivery" else "p"

    def b(label, act):
        return btn(label, f"st:{act}:{cust_chat}:{t}")
    if status == "new":
        return ik([[b("✅ Принять", "accepted"), b("❌ Отклонить", "cancelled")]])
    if status == "accepted":
        nxt = b("🛵 Курьер выехал", "onway") if t == "d" else b("🍕 Готов к выдаче", "ready")
        return ik([[nxt], [b("❌ Отменить", "cancelled")]])
    if status in ("onway", "ready"):
        return ik([[b("✔️ Выдан", "done")]])
    return {"inline_keyboard": []}


def place_order(chat, user, st):
    cart = cart_decode(st["c"])
    lines = cart_lines(cart)
    zero = cart_decode(EMPTY)
    if not lines:
        send_message(chat, "Корзина пуста, заказ не отправлен.", ik([[btn("🍕 Меню", cb("menu", "", zero))]]))
        return None
    target = orders_chat()
    if not target:
        send_message(chat, "Ресторан пока не подключён к боту. Нажмите «Отправить заказ» ещё раз чуть позже.")
        return None
    o = {"created": datetime.now(TZ).strftime("%d.%m %H:%M"),
         "name": " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Клиент",
         "username": user.get("username"), "type": st["t"], "address": st.get("a"), "phone": st["p"],
         "pay": st["y"], "comment": st.get("m"), "total": cart_total(cart),
         "items": [{"name": it["name"], "qty": q, "price": it["price"]} for it, q in lines]}
    try:
        try:
            m = send_message(target, render_order(o, "…"), plain=True)
        except TgError as e:
            new = e.params.get("migrate_to_chat_id")
            if not new:
                raise
            move_orders_chat(new)          # группа стала супергруппой
            target = new
            m = send_message(target, render_order(o, "…"), plain=True)
        n = m["message_id"]          # номер заказа = id сообщения в чате ресторана
        edit_text(target, n, render_order(o, n), status_kb("new", chat, o["type"]), plain=True)
    except TgError as e:
        log.error("не смог отправить заказ в чат %s: %s", target, e)
        send_message(chat, "Не получилось отправить заказ в ресторан. Нажмите «Отправить заказ» ещё раз через минуту.")
        return None
    return n


def change_status(cq):
    _, act, cust, t = cq["data"].split(":")
    msg = cq["message"]
    chat, mid = msg["chat"]["id"], msg["message_id"]
    if chat != orders_chat() or act not in STATUS_TITLE:
        return api("answerCallbackQuery", {"callback_query_id": cq["id"]})
    text = msg.get("text") or ""
    i = text.rfind("Статус:")
    if i < 0:
        return api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Не нашёл статус заказа"})
    new = text[:i] + "Статус: " + STATUS_TITLE[act]
    try:
        edit_text(chat, mid, new, status_kb(act, cust, "delivery" if t == "d" else "pickup"), plain=True)
    except TgError as e:
        if "not modified" not in e.desc:
            raise
        return api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Статус уже стоит"})
    note = CUSTOMER_NOTE.get(act)
    if note:
        try:
            send_message(int(cust), note.format(id=mid))
        except TgError as e:
            log.warning("клиент недоступен: %s", e)
    api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": STATUS_TITLE[act]})


# ------------------------------------------------------------------- роутинг
def on_callback(cq):
    data = cq.get("data", "")
    msg = cq.get("message")
    answer = {"callback_query_id": cq["id"]}
    if not msg:
        return api("answerCallbackQuery", answer)
    chat = msg["chat"]["id"]
    zero = cart_decode(EMPTY)

    if data.startswith("st:"):
        return change_status(cq)

    if data == "noop":
        pass
    elif data.startswith("pay:"):
        st = read_state(msg)
        arg = data[4:]
        if st and st.get("s") == "pay" and arg.isdigit() and int(arg) < len(PAYMENTS):
            st["y"] = PAYMENTS[int(arg)]
            st["w"] = msg["message_id"]
            goto(chat, st, "comment")
    elif data == "nc":
        st = read_state(msg)
        if st and st.get("s") == "comment":
            st["m"] = ""
            st["w"] = msg["message_id"]
            goto(chat, st, "confirm")
    elif data == "ok":
        st = read_state(msg)
        if st and st.get("s") == "confirm":
            try:
                edit_markup(chat, msg["message_id"], {"inline_keyboard": []})   # защита от двойного нажатия
            except TgError:
                return api("answerCallbackQuery", {**answer, "text": "Этот заказ уже отправлен"})
            n = place_order(chat, cq["from"], st)
            if n:
                st.update(s="sent", n=n)
                edit_text(chat, msg["message_id"], card_text(st) + hidden(st), card_kb(st))
            else:
                edit_markup(chat, msg["message_id"], card_kb(st))   # вернуть кнопку для повтора
        else:
            answer["text"] = "Этот заказ уже отправлен"
    elif data in ("x", "no"):
        st = read_state(msg)
        if st and st.get("k"):
            delete_message(chat, st["k"])       # убираем висящий вопрос
        screen_cart(chat, msg, cart_decode(st["c"]) if st else zero)
    else:
        parts = data.split(":")
        if len(parts) != 3:
            return api("answerCallbackQuery", answer)
        kind, arg, code = parts
        cart = cart_decode(code)
        if kind == "menu":
            screen_categories(chat, msg, cart)
        elif kind == "cat":
            screen_category(chat, msg, arg, cart)
        elif kind == "item":
            screen_item(chat, msg, arg, cart)
        elif kind in ("add", "sub") and arg in IDX:
            new = with_qty(cart, arg, 1 if kind == "add" else -1)
            if kind == "add" and new == cart:
                answer["text"] = f"Максимум {MAX_QTY} шт."
            screen_item(chat, msg, arg, new)
        elif kind == "cart":
            screen_cart(chat, msg, cart)
        elif kind in ("c+", "c-") and arg in IDX:
            screen_cart(chat, msg, with_qty(cart, arg, 1 if kind == "c+" else -1))
        elif kind == "clear":
            screen_cart(chat, msg, cart_decode(EMPTY))
        elif kind == "checkout":
            start_checkout(chat, msg, cart)
        elif kind == "info":
            screen_info(chat, msg, arg, cart)
        elif kind == "ot" and arg in ("delivery", "pickup"):
            st = {"c": code, "t": arg, "w": msg["message_id"]}
            goto(chat, st, "address" if arg == "delivery" else "phone")
    api("answerCallbackQuery", answer)


def on_message(msg):
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    zero = cart_decode(EMPTY)

    if msg["chat"].get("type", "private") != "private":
        # в группах бот только принимает заказы: реагирует на /here и на переезд группы в супергруппу
        if msg.get("migrate_to_chat_id") and chat == orders_chat():
            return move_orders_chat(msg["migrate_to_chat_id"])
        if msg.get("migrate_from_chat_id") and msg["migrate_from_chat_id"] == orders_chat():
            return move_orders_chat(chat)
        if text.split("@")[0].split()[0:1] == ["/here"]:
            return set_orders_chat(chat)
        return

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start":
            return screen_welcome(chat)
        if cmd == "/menu":
            return screen_categories(chat, None, zero)
        if cmd == "/help":
            return screen_info(chat, None, "delivery", zero)
        if cmd == "/here":
            return set_orders_chat(chat)
        if cmd == "/cancel":
            return send_message(chat, "Оформление отменено.", ik([[btn("🍕 Меню", cb("menu", "", zero))]]))

    reply = msg.get("reply_to_message") or {}
    st = read_state(reply) if (reply.get("from") or {}).get("is_bot") else None
    if st and st.get("s") in ("address", "phone", "comment"):
        return handle_reply(msg, st)
    send_message(chat, "Нажмите /start, чтобы открыть меню.")


def handle(update):
    """Обработать одно обновление Telegram (для вебхука на Vercel и для polling)."""
    try:
        if "callback_query" in update:
            on_callback(update["callback_query"])
        elif "message" in update:
            on_message(update["message"])
    except TgError as e:
        log.error("Telegram: %s", e)
        cq = update.get("callback_query")
        if cq:
            try:
                api("answerCallbackQuery", {"callback_query_id": cq["id"]})
            except TgError:
                pass
    except Exception:
        log.exception("ошибка в обработчике")


def setup_bot():
    try:
        api("setMyCommands", {"commands": [
            {"command": "start", "description": "Начать"},
            {"command": "menu", "description": "Меню"},
            {"command": "help", "description": "Доставка и оплата"}]})
        api("setMyDescription", {"description": "Заказ пиццы Don Corleone в Батуми. Меню с фото, доставка от 20 минут."})
        api("setMyShortDescription", {"short_description": "Пицца Don Corleone, Батуми. Заказать в пару касаний."})
    except TgError as e:
        log.warning("настройка бота пропущена: %s", e)


def main():
    """Локальный запуск через polling (на Vercel используется api/webhook.py)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not TOKEN:
        sys.exit("Нет BOT_TOKEN. Создайте файл .env рядом с bot.py (см. .env.example).")
    me = api("getMe")
    log.info("Бот @%s запущен", me.get("username"))
    if orders_chat() is None:
        log.warning("ORDERS_CHAT_ID не задан. Отправьте боту /here, он покажет ID чата для заказов.")
    setup_bot()
    offset = 0
    while True:
        try:
            updates = api("getUpdates", {"offset": offset, "timeout": 30,
                                         "allowed_updates": ["message", "callback_query"]}, timeout=45)
        except (requests.RequestException, TgError) as e:
            log.warning("getUpdates: %s", e)
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            handle(u)


if __name__ == "__main__":
    main()
