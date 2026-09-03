import time
import logging

import aiohttp

from config import CONFIG, PROXY_URL

log = logging.getLogger("youtube_token")

_instance = None


class YouTubeTokenManager:
    def __init__(self):
        self._access_token: str | None = None
        self._expires_at: float = 0
        self._session: aiohttp.ClientSession | None = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), proxy=PROXY_URL or None)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_token(self) -> str | None:
        if self._access_token and time.time() < self._expires_at:
            return self._access_token
        yt = CONFIG.get("youtube") or {}
        client_id = yt.get("client_id", "")
        client_secret = yt.get("client_secret", "")
        refresh_token = yt.get("refresh_token", "")
        if not all([client_id, client_secret, refresh_token]):
            return None
        try:
            session = await self.get_session()
            async with session.post("https://oauth2.googleapis.com/token", data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    log.warning(
                        "YouTubeToken: refresh HTTP %s: %s",
                        resp.status, data.get("error_description") or data.get("error"),
                    )
                    self._access_token = None
                    self._expires_at = 0
                    return None
                if "access_token" in data:
                    self._access_token = data["access_token"]
                    self._expires_at = time.time() + data.get("expires_in", 3600) - 120
                    return self._access_token
                log.warning("YouTubeToken: refresh failed: %s", data.get("error_description"))
                self._access_token = None
                self._expires_at = 0
        except Exception:
            log.exception("YouTubeToken: refresh error")
        return None

    async def api(self, method: str, endpoint: str, **kwargs) -> dict | None:
        token = await self.get_token()
        if not token:
            return None
        session = await self.get_session()
        url = f"https://www.googleapis.com/youtube/v3{endpoint}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if method == "GET":
                async with session.get(url, headers=headers, **kwargs) as resp:
                    return await self._handle(resp, endpoint)
            elif method == "POST":
                async with session.post(url, headers=headers, **kwargs) as resp:
                    return await self._handle(resp, endpoint)
            elif method == "DELETE":
                async with session.delete(url, headers=headers, **kwargs) as resp:
                    if resp.status == 204:
                        return {"status": "ok"}
                    return await self._handle(resp, endpoint)
        except Exception:
            log.exception("YouTubeToken: API error %s %s", method, endpoint)
            return None

    async def _handle(self, resp, endpoint: str) -> dict | None:
        """Проверяет код состояния ответа YouTube API.

        Раньше коды не проверялись: 401 (просроченный токен) возвращался
        вызывающему коду как штатный ответ, а кешированный токен не
        инвалидировался; исчерпание квоты 429 не переводило клиент в режим
        отложенных повторов (дефект D13).
        """
        if resp.status == 401:
            # Токен недействителен — сбрасываем кеш, следующий вызов перевыпустит.
            self._access_token = None
            self._expires_at = 0
            log.warning("YouTubeToken: 401 на %s — токен инвалидирован", endpoint)
            return None
        if resp.status == 429:
            log.warning("YouTubeToken: 429 quota exhausted на %s", endpoint)
            return None
        if resp.status >= 400:
            log.warning("YouTubeToken: HTTP %s на %s", resp.status, endpoint)
            return None
        data = await resp.json()
        if data and "error" in data:
            log.warning("YouTubeToken: API error на %s: %s", endpoint, data["error"])
            return None
        return data


def get_token_manager() -> YouTubeTokenManager:
    global _instance
    if _instance is None:
        _instance = YouTubeTokenManager()
    return _instance
