"""Точка входа для Vercel: Telegram шлёт сюда обновления (вебхук).

GET /api/webhook?setup=<WEBHOOK_SECRET> один раз регистрирует вебхук у Telegram.
"""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402

SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()


class handler(BaseHTTPRequestHandler):
    def _send(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not SECRET or not hmac.compare_digest(got, SECRET):
            return self._send(403, "forbidden")
        try:
            size = int(self.headers.get("Content-Length") or 0)
            update = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self._send(400, "bad json")
        bot.handle(update)          # ошибки внутри логируются, Telegram всегда получает 200
        self._send(200, "ok")

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if "setup" not in q:
            return self._send(200, "Don Corleone bot is running")
        if not SECRET:
            return self._send(500, "Задайте переменную WEBHOOK_SECRET в настройках Vercel и сделайте Redeploy.")
        if not hmac.compare_digest(q["setup"][0], SECRET):
            return self._send(403, "forbidden")
        host = self.headers.get("x-forwarded-host") or self.headers.get("host")
        url = f"https://{host}/api/webhook"
        try:
            bot.api("setWebhook", {"url": url, "secret_token": SECRET, "max_connections": 1,
                                   "allowed_updates": ["message", "callback_query"],
                                   "drop_pending_updates": True})
            bot.setup_bot()
        except Exception as e:  # noqa: BLE001
            return self._send(500, f"Не получилось: {e}")
        self._send(200, f"Готово. Вебхук установлен: {url}")

    def log_message(self, *args):
        pass
