import asyncio
import json
import logging
import re
import time
from collections import defaultdict, deque

import aiohttp

log = logging.getLogger("youtube")


class YouTubeModeration:
    def __init__(self, config):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.banned = [w.lower() for w in self.config.get("banned_words", [])]
        self.allowed_links = [d.lower().strip(".") for d in self.config.get("allowed_links", [])]
        self.block_links = self.config.get("block_links", True)
        self._history = defaultdict(lambda: deque(maxlen=self.config.get("history_size", 10)))
        self._timestamps = defaultdict(list)
        self._link_re = re.compile(r"(?:https?://|www\.)([^/\s]+)", re.IGNORECASE)

    def check(self, text, author):
        if not self.enabled:
            return None
        if not text:
            return None
        now = time.time()

        if self.block_links:
            for match in self._link_re.finditer(text):
                domain = match.group(1).lower()
                if not any(domain == d or domain.endswith("." + d) for d in self.allowed_links):
                    return {"reason": "ссылки", "user": author}

        lowered = text.lower()
        for word in self.banned:
            if re.search(r"\b" + re.escape(word) + r"\b", lowered):
                return {"reason": "запрещённые слова", "user": author}

        if len(text) >= 10:
            letters = [c for c in text if c.isalpha()]
            if letters:
                ratio = sum(c.isupper() for c in letters) / len(letters)
                if ratio >= self.config.get("caps_threshold", 0.75):
                    return {"reason": "капс", "user": author}

        if len(text) >= 8:
            prev = None
            streak = 0
            for ch in text:
                if ch == prev:
                    streak += 1
                else:
                    streak = 1
                    prev = ch
                if streak >= 5:
                    return {"reason": "растянутый спам", "user": author}

        window = self.config.get("message_cooldown", 2)
        stamps = [t for t in self._timestamps[author] if now - t <= window]
        stamps.append(now)
        self._timestamps[author] = stamps
        if len(stamps) > self.config.get("max_messages_in_window", 5):
            return {"reason": "флуд", "user": author}

        history = self._history[author]
        history.append(text)
        if history.count(text) >= self.config.get("duplicate_threshold", 3):
            return {"reason": "повтор сообщений", "user": author}

        return None


class YouTubeChatClient:
    def __init__(self, config, loop, bridge=None, discord_bot=None):
        self.config = config
        self.bridge = bridge
        self.discord_bot = discord_bot
        self.loop = loop
        self.channel_handle = config.get("channel", "Dendosich")
        self.poll_interval = config.get("poll_seconds", 5)
        self.check_interval = config.get("check_seconds", 30)
        self._running = False
        self._video_id = None
        self._continuation = None
        self._seen = set()
        self._session = None
        self.moderation = YouTubeModeration(config.get("moderation") or {})
        self._notified_live = False
        self._stream_title = ""

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
                        await self._check_schedule()
                        await asyncio.sleep(self.check_interval)
                        continue
                    await self._get_continuation()
                    if self._continuation is None:
                        await asyncio.sleep(5)
                        continue

                await self._poll_messages()
                await asyncio.sleep(self.poll_interval)

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

                title_match = re.search(r'"title":"([^"]{1,200})"', html)
                if title_match:
                    self._stream_title = title_match.group(1)

                match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                if match:
                    vid = match.group(1)
                    if vid != self._video_id:
                        self._video_id = vid
                        self._seen.clear()
                        self._notified_live = False
                        log.info("YouTube: стрим найден! video=%s", vid)
                    if not self._notified_live:
                        await self._notify_live_start()
                        self._notified_live = True
                else:
                    if self._video_id:
                        self._video_id = None
                        self._continuation = None
                        self._notified_live = False
                        log.info("YouTube: стрим окончен")
                        await self._notify_live_end()
        except Exception:
            log.exception("YouTube: ошибка проверки стрима")

    async def _notify_live_start(self):
        if not self.discord_bot:
            return
        channel_id = self.config.get("notify_channel_id", 0)
        if channel_id <= 0:
            return
        channel = self.discord_bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.discord_bot.fetch_channel(channel_id)
            except Exception:
                return
        import discord
        url = f"https://www.youtube.com/watch?v={self._video_id}"
        embed = discord.Embed(
            title="🔴 YouTube стрим начался!",
            description=f"**{self._stream_title or 'Новый стрим'}**",
            url=url,
            color=0xFF0000,
        )
        embed.add_field(name="Канал", value=f"@{self.channel_handle}", inline=True)
        embed.set_footer(text="Подключайся к эфиру!")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Открыть стрим", url=url, style=discord.ButtonStyle.link))
        content = None
        role_id = self.config.get("ping_role_id")
        if role_id == "@everyone":
            content = "@everyone"
        elif role_id:
            content = f"<@&{role_id}>"
        try:
            await channel.send(content=content, embed=embed, view=view)
            log.info("YouTube: уведомление о стриме отправлено в канал %s", channel_id)
        except Exception:
            log.exception("YouTube: ошибка отправки уведомления")

    async def _notify_live_end(self):
        if not self.discord_bot:
            return
        channel_id = self.config.get("notify_channel_id", 0)
        if channel_id <= 0:
            return
        channel = self.discord_bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.discord_bot.fetch_channel(channel_id)
            except Exception:
                return
        import discord
        embed = discord.Embed(
            title="⚪ YouTube стрим завершён",
            description=f"Трансляция **{self._stream_title or ''}** окончена.",
            url=f"https://www.youtube.com/@{self.channel_handle}/live",
            color=0x808080,
        )
        embed.set_footer(text="Ждём следующий эфир!")
        try:
            await channel.send(embed=embed)
            log.info("YouTube: уведомление об окончании стрима")
        except Exception:
            log.exception("YouTube: ошибка отправки уведомления")

    async def _check_schedule(self):
        url = f"https://www.youtube.com/@{self.channel_handle}/streams"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        }
        try:
            async with self._session.get(url, headers=headers) as resp:
                html = await resp.text()

            scheduled = re.findall(
                r'"videoId":"([a-zA-Z0-9_-]{11})".*?"title":\{"runs":\[\{"text":"([^"]+)"\}\].*?"startText":\{"simpleText":"([^"]+)"\}',
                html,
            )

            if not scheduled:
                scheduled = re.findall(
                    r'"videoId":"([a-zA-Z0-9_-]{11})".*?"title":\{"simpleText":"([^"]+)"\}',
                    html,
                )

            if scheduled and self.discord_bot:
                channel_id = self.config.get("notify_channel_id", 0)
                if channel_id > 0:
                    channel = self.discord_bot.get_channel(channel_id)
                    if not channel:
                        try:
                            channel = await self.discord_bot.fetch_channel(channel_id)
                        except Exception:
                            return
                    import discord
                    for item in scheduled[:3]:
                        vid = item[0]
                        title = item[1]
                        when = item[2] if len(item) > 2 else "Запланирован"
                        embed = discord.Embed(
                            title="📅 Запланирован YouTube стрим",
                            description=f"**{title}**\nНачало: {when}",
                            url=f"https://www.youtube.com/watch?v={vid}",
                            color=0xFFD700,
                        )
                        view = discord.ui.View(timeout=None)
                        view.add_item(discord.ui.Button(label="Напомнить", url=f"https://www.youtube.com/watch?v={vid}", style=discord.ButtonStyle.link))
                        try:
                            await channel.send(embed=embed, view=view)
                        except Exception:
                            pass

        except Exception:
            log.debug("YouTube: расписание не найдено (канал может не расписывать стримы)")

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
                        is_owner = False
                        is_mod = False
                        badge = ""
                        for b in badges:
                            icon = b.get("liveChatAuthorBadgeRenderer", {}).get("icon", {})
                            icon_type = icon.get("iconType", "")
                            if icon_type == "OWNER":
                                badge = "\U0001f3af"
                                is_owner = True
                                break
                            elif icon_type == "MODERATOR":
                                badge = "\U0001f6e1"
                                is_mod = True
                                break

                        if is_owner or is_mod:
                            pass
                        else:
                            violation = self.moderation.check(text, author)
                            if violation:
                                log.warning(
                                    "YouTube модерация: %s (%s) — %s: %s",
                                    author, violation["reason"], text[:60],
                                )
                                if self.bridge:
                                    await self.bridge.forward_to_discord(
                                        f"YT \u26a0\ufe0f Модерация",
                                        f"**{author}** — {violation['reason']}: {text[:100]}",
                                    )
                                continue

                        log.info("YouTube: %s%s: %s", badge, author, text[:80])
                        if self.bridge:
                            await self.bridge.forward_to_discord(
                                f"YT {badge}{author}", text
                            )

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
