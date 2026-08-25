"""
Мастер настройки YouTube модерации.
Запусти: python setup_youtube.py
"""
import json
import os
import secrets
import sys
import webbrowser
from urllib.parse import urlencode, parse_qs
from urllib.request import Request, urlopen
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import time

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
REDIRECT_URI = "http://localhost:8090/callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
_oauth_state = None


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


class CallbackHandler(BaseHTTPRequestHandler):
    code = None
    state_ok = False

    def do_GET(self):
        qs = parse_qs(self.path.split("?", 1)[-1] if "?" in self.path else "")
        if "code" in qs:
            if qs.get("state", [None])[0] != _oauth_state:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"State mismatch - CSRF rejected")
                return
            CallbackHandler.code = qs["code"][0]
            CallbackHandler.state_ok = True
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>Готово! Можешь закрыть это окно и вернуться в терминал.</h2>".encode()
            )
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *args):
        pass


def step1_guide():
    print("=" * 60)
    print("  ШАГ 1: Создай Google Cloud проект")
    print("=" * 60)
    print()
    print("1) Откроется Google Cloud Console")
    print("2) Нажми 'Select a project' → 'New Project'")
    print("3) Назови: youtube-bot-mod")
    print("4) Нажми 'Create'")
    print()
    input("Нажми Enter когда создал проект...")
    webbrowser.open("https://console.cloud.google.com/project")


def step2_guide():
    print()
    print("=" * 60)
    print("  ШАГ 2: Включи YouTube Data API v3")
    print("=" * 60)
    print()
    print("1) В поиске сверху набери: YouTube Data API v3")
    print("2) Нажми 'Enable'")
    print()
    input("Нажми Enter когда включил API...")
    webbrowser.open("https://console.cloud.google.com/apis/library/youtube.googleapis.com")


def step3_guide():
    print()
    print("=" * 60)
    print("  ШАГ 3: Создай OAuth2 credentials")
    print("=" * 60)
    print()
    print("1) Слева выбери 'APIs & Services' → 'Credentials'")
    print("2) Нажми '+ Create Credentials' → 'OAuth client ID'")
    print("3) Application type: Desktop app")
    print("4) Name: youtube-bot")
    print("5) Нажми 'Create'")
    print("6) Скопируй Client ID и Client Secret")
    print()
    input("Нажми Enter когда создал credentials...")
    webbrowser.open("https://console.cloud.google.com/apis/credentials")


def step4_input():
    print()
    print("=" * 60)
    print("  ШАГ 4: Вставь credentials")
    print("=" * 60)
    print()
    client_id = input("Client ID: ").strip()
    client_secret = input("Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Пустые значения, попробуй снова.")
        sys.exit(1)

    return client_id, client_secret


def step5_auth(client_id, client_secret):
    global _oauth_state
    print()
    print("=" * 60)
    print("  ШАГ 5: Авторизация канала")
    print("=" * 60)
    print()

    _oauth_state = secrets.token_urlsafe(32)
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/youtube",
        "access_type": "offline",
        "prompt": "consent",
        "state": _oauth_state,
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    server = HTTPServer(("localhost", 8090), CallbackHandler)
    thread = threading.Thread(target=lambda: server.handle_request(), daemon=True)
    thread.start()

    print("Откроется браузер — авторизуй свой YouTube канал.")
    print("Если не открылся, скопируй ссылку:")
    print()
    print(auth_url)
    print()
    webbrowser.open(auth_url)

    print("Жду авторизацию...")
    thread.join(timeout=300)
    server.server_close()

    if not CallbackHandler.code:
        print("Таймаут! Попробуй снова.")
        sys.exit(1)

    print("Код получен! Обменяю на токен...")

    data = urlencode({
        "code": CallbackHandler.code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req) as resp:
        tokens = json.loads(resp.read())

    if "refresh_token" not in tokens:
        print("Refresh token не получен:", tokens)
        sys.exit(1)

    return tokens["refresh_token"]


def main():
    clear()
    print()
    print("  YouTube Модерация — Мастер настройки")
    print("  =====================================")
    print()
    print("Этот скрипт поможет настроить модерацию YouTube чата.")
    print("Тебе нужно иметь Google аккаунт, на котором стримишь.")
    print()

    cfg = load_config()
    yt = cfg.setdefault("youtube", {})

    if yt.get("refresh_token"):
        print("Refresh token уже настроен!")
        print(f"client_id: {yt.get('client_id', '???')[:20]}...")
        print()
        choice = input("Настроить заново? (y/n): ").strip().lower()
        if choice != "y":
            print("Ок, пропускаем.")
            return

    input("Нажми Enter чтобы начать...")

    step1_guide()
    step2_guide()
    step3_guide()
    client_id, client_secret = step4_input()
    refresh_token = step5_auth(client_id, client_secret)

    yt["client_id"] = client_id
    yt["client_secret"] = client_secret
    yt["refresh_token"] = refresh_token
    yt.setdefault("moderation", {})["ban_on_violation"] = True
    yt.setdefault("moderation", {})["ban_duration_seconds"] = 300

    save_config(cfg)

    print()
    print("=" * 60)
    print("  ГОТОВО!")
    print("=" * 60)
    print()
    print("Настройка сохранена в config.json")
    print("Теперь бот может:")
    print("  - Читать YouTube чат через API")
    print("  - Удалять сообщения с нарушениями")
    print("  - Банить нарушителей")
    print()
    print("Перезапусти бота чтобы изменения вступили в силу.")


if __name__ == "__main__":
    main()
