"""Лёгкий REST-клиент для официального публичного API Kick.

Используется для безопасных, легальных вещей: статус стрима, кол-во
зрителей, инфо о канале/трансляции. Без отправки сообщений в чат.
"""

import asyncio
import logging
from typing import Optional

import aiohttp

from config import PROXY_URL

log = logging.getLogger("kick")

API_BASE = "https://kick.com/api/v2"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)


class KickAPI:
    def __init__(self, config):
        self.config = config
        self.slug = (config.get("channel") or "RUDendich").strip().lstrip("@")
        self._session = None

    async def _get_session(self):
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                proxy=PROXY_URL or None,
                headers={"User-Agent": UA, "Accept": "application/json"},
            )
        return self._session

    async def close(self):
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def get_channel(self) -> Optional[dict]:
        """Базовые данные канала: id, slug, категория, playback_url."""
        session = await self._get_session()
        try:
            async with session.get(f"{API_BASE}/channels/{self.slug}") as resp:
                if resp.status != 200:
                    log.warning("Kick: канал %s -> HTTP %s", self.slug, resp.status)
                    return None
                return await resp.json()
        except Exception:
            log.exception("Kick: ошибка запроса канала %s", self.slug)
            return None

    async def get_livestream(self) -> Optional[dict]:
        """Данные текущей трансляции или None, если офлайн."""
        session = await self._get_session()
        try:
            async with session.get(f"{API_BASE}/channels/{self.slug}/livestream") as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    log.warning("Kick: livestream %s -> HTTP %s", self.slug, resp.status)
                    return None
                data = await resp.json()
                return data.get("livestream") or None
        except Exception:
            log.exception("Kick: ошибка запроса livestream %s", self.slug)
            return None

    async def is_live(self) -> bool:
        stream = await self.get_livestream()
        return stream is not None

    async def get_viewer_count(self) -> Optional[int]:
        stream = await self.get_livestream()
        if not stream:
            return None
        viewers = stream.get("viewers") or {}
        if isinstance(viewers, dict):
            return viewers.get("count")
        return viewers

    @staticmethod
    def _parse_ts(value):
        if not value:
            return None
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except Exception:
            return None

    async def summarize(self) -> Optional[dict]:
        """Удобный словарь для эмбеда: статус + заголовок + категория + зрители."""
        ch = await self.get_channel()
        stream = await self.get_livestream()
        if ch is None:
            return None
        out = {
            "online": stream is not None,
            "slug": self.slug,
            "id": ch.get("id"),
            "url": f"https://kick.com/{self.slug}",
        }
        if stream:
            out["title"] = stream.get("session_title") or ch.get("name") or "Стрим"
            cat = stream.get("category") or {}
            out["category"] = cat.get("name") if isinstance(cat, dict) else cat
            viewers = stream.get("viewers") or {}
            out["viewer_count"] = viewers.get("count") if isinstance(viewers, dict) else viewers
            thumbnail = stream.get("thumbnail") or {}
            if isinstance(thumbnail, dict):
                out["thumbnail_url"] = thumbnail.get("url")
            out["start_time"] = stream.get("created_at")
            out["start_time_ts"] = self._parse_ts(stream.get("created_at"))
        else:
            out["title"] = ch.get("name") or self.slug
            out["category"] = None
            out["viewer_count"] = 0
            out["thumbnail_url"] = None
        return out
