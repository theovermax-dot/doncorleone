"""Точка входа для Vercel: Telegram шлёт сюда обновления (вебхук).

Нужна одна переменная окружения BOT_TOKEN. Секрет вебхука бот выводит из токена сам.
GET /api/webhook?setup=1 регистрирует вебхук у Telegram (безопасно запускать повторно).
Чат для заказов запоминается в адресе вебхука после команды /here.
"""
import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402


def secret():
    own = os.environ.get("WEBHOOK_SECRET", "").strip()      # необязательно: можно задать свой
    if own:
        return own
    if not bot.TOKEN:
        return ""
    return hashlib.sha256(("doncorleone|" + bot.TOKEN).encode()).hexdigest()[:48]


def is_chat_id(value):
    return bool(value) and value.lstrip("-").isdigit()


class handler(BaseHTTPRequestHandler):
    def _send(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _base_url(self):
        host = (os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or self.headers.get("x-forwarded-host")
                or self.headers.get("host"))
        return f"https://{host}/api/webhook"

    def do_POST(self):
        sec = secret()
        got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not sec or not hmac.compare_digest(got, sec):
            return self._send(403, "forbidden")
        try:
            size = int(self.headers.get("Content-Length") or 0)
            update = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self._send(400, "bad json")
        orders = parse_qs(urlparse(self.path).query).get("orders", [""])[0]
        bot.RUNTIME.update({"orders": int(orders) if is_chat_id(orders) else None,
                            "base_url": self._base_url(), "secret": sec})
        bot.handle(update)          # ошибки внутри логируются, Telegram всегда получает 200
        self._send(200, "ok")

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if "setup" not in q:
            return self._send(200, "Don Corleone bot is running\nBOT_TOKEN: " + ("задан" if bot.TOKEN else "НЕ ЗАДАН"))
        if not bot.TOKEN:
            return self._send(500, "Не задан BOT_TOKEN. Добавьте его в Vercel (Settings → Environment Variables) "
                                   "и сделайте Redeploy.")
        try:
            cur = bot.api("getWebhookInfo").get("url", "")
            orders = parse_qs(urlparse(cur).query).get("orders", [""])[0]    # не теряем уже выбранный чат
            url = self._base_url() + (f"?orders={orders}" if is_chat_id(orders) else "")
            bot.api("setWebhook", {"url": url, "secret_token": secret(), "max_connections": 1,
                                   "allowed_updates": ["message", "callback_query"],
                                   "drop_pending_updates": True})
            bot.setup_bot()
        except Exception as e:  # noqa: BLE001
            return self._send(500, f"Не получилось: {e}")
        self._send(200, f"Готово. Вебхук установлен: {url}")

    def log_message(self, *args):
        pass
