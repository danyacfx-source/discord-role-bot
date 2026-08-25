"""
YouTube OAuth2 авторизация — запустить один раз.

1. Создай проект в Google Cloud Console
2. Включи YouTube Data API v3
3. Создай OAuth2 credentials (Desktop App)
4. Впиши client_id и client_secret в config.json секцию youtube
5. Запусти: python -m twitch_bot.youtube_auth
6. Открой ссылку в браузере, авторизуйся
7. Вставь код обратно в консоль
8. Refresh token сохранится в .env
"""

import json
import os
import secrets
import sys
from urllib.parse import urlencode, parse_qs
from urllib.request import Request, urlopen
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import webbrowser

REDIRECT_URI = "http://localhost:8090/callback"
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_oauth_state = None


def load_config():
    import os
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f), config_path


def save_config(config, config_path):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


class CallbackHandler(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        qs = parse_qs(self.path.split("?", 1)[-1] if "?" in self.path else "")
        if "code" in qs:
            if qs.get("state", [None])[0] != _oauth_state:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"State mismatch - CSRF rejected")
                return
            CallbackHandler.code = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Авторизация прошла успешно! Можешь закрыть окно.".encode())
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error")

    def log_message(self, *args):
        pass


def exchange_code(client_id, client_secret, code):
    data = urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req) as resp:
        return json.loads(resp.read())


def refresh_access_token(client_id, client_secret, refresh_token):
    data = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req) as resp:
        return json.loads(resp.read())


def get_access_token(config):
    yt = config.get("youtube", {})
    client_id = yt.get("client_id", "")
    client_secret = yt.get("client_secret", "")
    refresh_token = yt.get("refresh_token", "")

    if not client_id or not client_secret:
        print("Секреты YouTube не найдены. Запусти setup_youtube.py или добавь в .env")
        return None

    if refresh_token:
        try:
            tokens = refresh_access_token(client_id, client_secret, refresh_token)
            return tokens.get("access_token")
        except Exception:
            pass

    return None


def main():
    global _oauth_state
    config, config_path = load_config()
    yt = config.setdefault("youtube", {})
    client_id = yt.get("client_id", "")
    client_secret = yt.get("client_secret", "")

    if not client_id or not client_secret:
        print("Секреты YouTube не найдены. Запусти setup_youtube.py или добавь в .env")
        sys.exit(1)

    _oauth_state = secrets.token_urlsafe(32)
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": _oauth_state,
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    server = HTTPServer(("localhost", 8090), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"\nОткрой в браузере:\n{auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("Жду авторизацию...")
    thread.join(timeout=300)
    server.server_close()

    if not CallbackHandler.code:
        print("Таймаут — код не получен")
        sys.exit(1)

    tokens = exchange_code(client_id, client_secret, CallbackHandler.code)

    if "refresh_token" in tokens:
        env_path = os.path.join(os.path.dirname(config_path), ".env")
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = [l for l in f.readlines() if not l.startswith("YOUTUBE_REFRESH_TOKEN")]
        with open(env_path, "a", encoding="utf-8") as f:
            f.writelines(lines)
            f.write(f"YOUTUBE_REFRESH_TOKEN={tokens['refresh_token']}\n")
        print(f"\nRefresh token сохранён в .env")
        print("Теперь бот сможет модерировать YouTube чат!")
    else:
        print("Refresh token не получен. Попробуй снова.")
        sys.exit(1)


if __name__ == "__main__":
    main()
