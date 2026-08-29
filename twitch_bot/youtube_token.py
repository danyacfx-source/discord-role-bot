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
                if "access_token" in data:
                    self._access_token = data["access_token"]
                    self._expires_at = time.time() + data.get("expires_in", 3600) - 120
                    return self._access_token
                log.warning("YouTubeToken: refresh failed: %s", data.get("error_description"))
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
                    return await resp.json()
            elif method == "POST":
                async with session.post(url, headers=headers, **kwargs) as resp:
                    return await resp.json()
            elif method == "DELETE":
                async with session.delete(url, headers=headers, **kwargs) as resp:
                    if resp.status == 204:
                        return {"status": "ok"}
                    return await resp.json()
        except Exception:
            log.exception("YouTubeToken: API error %s %s", method, endpoint)
            return None


def get_token_manager() -> YouTubeTokenManager:
    global _instance
    if _instance is None:
        _instance = YouTubeTokenManager()
    return _instance
