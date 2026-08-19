import asyncio
import json
import logging
import re

import aiohttp

log = logging.getLogger("youtube")


class YouTubeChatClient:
    def __init__(self, config, loop, bridge=None):
        self.config = config
        self.bridge = bridge
        self.loop = loop
        self.channel_handle = config.get("channel", "Dendosich")
        self.poll_interval = config.get("poll_seconds", 5)
        self.check_interval = config.get("check_seconds", 30)
        self._running = False
        self._video_id = None
        self._continuation = None
        self._seen = set()
        self._session = None

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        log.info("YouTube: мониторинг @%s запущен", self.channel_handle)
        try:
            await self._loop()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("YouTube: клиент завершился с ошибкой")
        finally:
            if self._session and not self._session.closed:
                await self._session.close()

    async def stop(self):
        self._running = False

    async def _loop(self):
        while self._running:
            try:
                if self._video_id is None:
                    await self._check_live()
                    if self._video_id is None:
                        await asyncio.sleep(self.check_interval)
                        continue
                    await self._get_continuation()
                    if self._continuation is None:
                        await asyncio.sleep(5)
                        continue

                await self._poll_messages()
                await asyncio.sleep(self.poll_interval)

                # Re-check if still live every 60s
                if self._video_id:
                    await self._check_live()

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("YouTube: ошибка в цикле")
                await asyncio.sleep(5)

    async def _check_live(self):
        url = f"https://www.youtube.com/@{self.channel_handle}/live"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        }
        try:
            async with self._session.get(url, headers=headers) as resp:
                html = await resp.text()
                match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                if match:
                    vid = match.group(1)
                    if vid != self._video_id:
                        self._video_id = vid
                        self._seen.clear()
                        log.info("YouTube: стрим найден! video=%s", vid)
                        if self.bridge:
                            await self.bridge.forward_to_discord("YouTube", "Стрим начался! подключаюсь к чату...")
                else:
                    if self._video_id:
                        self._video_id = None
                        self._continuation = None
                        log.info("YouTube: стрим окончен")
                        if self.bridge:
                            await self.bridge.forward_to_discord("YouTube", "Стрим окончен")
        except Exception:
            log.exception("YouTube: ошибка проверки стрима")

    async def _get_continuation(self):
        url = f"https://www.youtube.com/watch?v={self._video_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        }
        try:
            async with self._session.get(url, headers=headers) as resp:
                html = await resp.text()
                match = re.search(r'"continuation":"([^"]{50,})"', html)
                if match:
                    self._continuation = match.group(1)
                    log.info("YouTube: continuation obtained")
                else:
                    log.warning("YouTube: continuation не найден")
        except Exception:
            log.exception("YouTube: ошибка получения continuation")

    async def _poll_messages(self):
        if not self._continuation:
            return
        url = "https://www.youtube.com/youtubei/v1/live_chat/get_messages"
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": "ru",
                    "gl": "RU",
                }
            },
            "continuation": self._continuation,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        }
        try:
            async with self._session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if "error" in data:
                    log.warning("YouTube API error: %s", data["error"].get("message", "?"))
                    return

                actions = data.get("actions", [])
                for action in actions:
                    panel = action.get("updateContinuationItemsAction", {})
                    items = panel.get("continuationItems", [])
                    for item in items:
                        renderer = item.get("chatItemRenderer", {})
                        if not renderer:
                            continue
                        msg_id = renderer.get("id", "")
                        if msg_id in self._seen:
                            continue
                        self._seen.add(msg_id)
                        if len(self._seen) > 500:
                            self._seen.clear()

                        snippet = renderer.get("message", {})
                        runs = snippet.get("runs", [])
                        text = "".join(r.get("text", "") for r in runs).strip()
                        if not text:
                            continue

                        author = renderer.get("authorName", {}).get("simpleText", "???")
                        badges = renderer.get("authorBadges", [])
                        badge = ""
                        for b in badges:
                            icon = b.get("liveChatAuthorBadgeRenderer", {}).get("icon", {})
                            if icon.get("iconType") == "OWNER":
                                badge = "[OWNER] "
                                break
                            elif icon.get("iconType") == "MODERATOR":
                                badge = "[MOD] "
                                break

                        log.info("YouTube: %s%s: %s", badge, author, text[:80])
                        if self.bridge:
                            await self.bridge.forward_to_discord(
                                f"YT {badge}{author}", text
                            )

                # Update continuation token
                for action in actions:
                    panel = action.get("updateContinuationItemsAction", {})
                    items = panel.get("continuationItems", [])
                    for item in items:
                        cont = item.get("continuationItemRenderer", {})
                        token = (
                            cont.get("continuationEndpoint", {})
                            .get("continuationCommand", {})
                            .get("token", "")
                        )
                        if token:
                            self._continuation = token

        except Exception:
            log.exception("YouTube: ошибка poll")
