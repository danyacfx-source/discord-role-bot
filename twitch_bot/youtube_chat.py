import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque

import aiohttp

log = logging.getLogger("youtube")

MSK = timezone(timedelta(hours=3))

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/youtube/v3"


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
        self._live_chat_id = None
        self._page_token = None
        self._seen = set()
        self._session = None
        self.moderation = YouTubeModeration(config.get("moderation") or {})
        self._notified_live = False
        self._notified_upcoming = False
        self._notified_prep = False
        self._stream_title = ""
        self._current_viewers = 0
        self._peak_viewers = 0
        self._start_notify_message_id = None
        self._live_message_id = None
        self._access_token = None
        self._token_expires = 0
        self.moderation_enabled = config.get("moderation", {}).get("enabled", True)
        self.ban_on_violation = config.get("moderation", {}).get("ban_on_violation", False)
        self.ban_duration = config.get("moderation", {}).get("ban_duration_seconds", 300)

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
                        await self._check_upcoming()
                        await asyncio.sleep(self.check_interval)
                        continue
                    await self._resolve_live_chat_id()

                await self._poll_messages()
                await asyncio.sleep(self.poll_interval)

                if self._video_id:
                    await self._check_live()
                    await self._update_viewers()

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("YouTube: ошибка в цикле")
                await asyncio.sleep(5)

    async def _fetch_page(self, url):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        }
        async with self._session.get(url, headers=headers) as resp:
            return await resp.text()

    async def _check_live(self):
        try:
            data = await self._api_request("GET", "/liveBroadcasts", params={
                "part": "id,snippet,status",
                "broadcastStatus": "all",
                "maxResults": 10,
            })
            if not data:
                return

            broadcasts = data.get("items", [])
            active_vid = None
            active_title = None
            for b in broadcasts:
                bid = b.get("id", "")
                snippet = b.get("snippet", {})
                status = b.get("status", {})
                lifecycle = status.get("lifeCycleStatus", "")
                broadcast_status = status.get("broadcastStatus", "")

                is_live = (
                    lifecycle == "live"
                    or broadcast_status == "started"
                    or lifecycle in ("ready", "revived") and broadcast_status != "upcoming"
                )

                if is_live and lifecycle not in ("complete", "revoked", "expired", "deleted"):
                    active_vid = bid
                    active_title = snippet.get("title", "")
                    break

            if active_vid:
                if active_vid != self._video_id:
                    self._video_id = active_vid
                    self._stream_title = active_title
                    self._live_chat_id = None
                    self._page_token = None
                    self._seen.clear()
                    self._notified_live = False
                    self._peak_viewers = 0
                    self._live_message_id = None
                    log.info("YouTube: стрим LIVE! video=%s", active_vid)
                if not self._notified_live:
                    await self._notify_live_start()
                    self._notified_live = True
            else:
                if self._video_id:
                    await self._notify_live_end()
                    self._video_id = None
                    self._live_chat_id = None
                    self._page_token = None
                    self._notified_live = False
                    self._notified_upcoming = False
                    self._notified_prep = False
                    self._peak_viewers = 0
                    self._current_viewers = 0
                    self._live_message_id = None
                    log.info("YouTube: стрим окончен")
        except Exception:
            log.exception("YouTube: ошибка проверки стрима")

    async def _update_viewers(self):
        if not self._video_id:
            return
        try:
            data = await self._api_request("GET", "/videos", params={
                "part": "statistics",
                "id": self._video_id,
            })
            if not data:
                return
            items = data.get("items", [])
            if not items:
                return
            stats = items[0].get("statistics", {})
            count = int(stats.get("viewCount", 0))
            self._current_viewers = count
            if count > self._peak_viewers:
                self._peak_viewers = count
            await self._edit_live_embed()
        except Exception:
            pass

    async def _edit_live_embed(self):
        if not self.discord_bot or not self._live_message_id:
            return
        channel_id = self.config.get("notify_channel_id", 0)
        if channel_id <= 0:
            return
        channel = self.discord_bot.get_channel(channel_id)
        if not channel:
            return
        import discord
        url = f"https://www.youtube.com/watch?v={self._video_id}"
        embed = discord.Embed(
            title="🔴 YouTube стрим идёт!",
            description=f"**{self._stream_title or 'Стрим'}**",
            url=url,
            color=0xFF0000,
        )
        embed.add_field(name="Канал", value=f"@{self.channel_handle}", inline=True)
        viewers_text = str(self._current_viewers)
        if self._peak_viewers > self._current_viewers:
            viewers_text = f"{self._current_viewers} (пик {self._peak_viewers})"
        embed.add_field(name="Зрители", value=viewers_text, inline=True)
        embed.set_footer(text=f"Обновлено {datetime.now(MSK).strftime('%H:%M:%S')} МСК")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Открыть стрим", url=url, style=discord.ButtonStyle.link))
        try:
            existing = await channel.fetch_message(self._live_message_id)
            if existing:
                await existing.edit(content=None, embed=embed, view=view)
        except Exception:
            pass

    async def _check_upcoming(self):
        lead_minutes = self.config.get("upcoming_lead_minutes", 10)
        try:
            html = await self._fetch_page(f"https://www.youtube.com/@{self.channel_handle}/streams")
            scheduled = re.findall(
                r'"videoId":"([a-zA-Z0-9_-]{11})".*?"title":\{"runs":\[\{"text":"([^"]+)"\}\].*?"startText":\{"simpleText":"([^"]+)"\}',
                html,
            )
            if not scheduled:
                return

            for item in scheduled[:1]:
                vid, title, when_text = item[0], item[1], item[2]

                time_match = re.search(r"(\d{1,2}):(\d{2})", when_text)
                if not time_match:
                    continue

                now = datetime.now(MSK)
                hour, minute = int(time_match.group(1)), int(time_match.group(2))
                stream_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if stream_time < now:
                    stream_time += timedelta(days=1)

                delta = stream_time - now
                minutes_left = int(delta.total_seconds() // 60)

                if minutes_left <= self.config.get("prep_lead_minutes", 5) and minutes_left > 0:
                    key = f"{vid}_{stream_time.isoformat()}"
                    if self._notified_prep != key:
                        self._notified_prep = key
                        await self._notify_prep(stream_time)
                elif minutes_left <= lead_minutes:
                    key = f"{vid}_{stream_time.isoformat()}"
                    if self._notified_upcoming == key:
                        await self._refresh_upcoming(stream_time, vid, title, minutes_left)
                    else:
                        self._notified_upcoming = key
                        await self._notify_upcoming(vid, title, stream_time, minutes_left)

        except Exception:
            log.debug("YouTube: расписание не найдено")

    async def _notify_upcoming(self, vid, title, stream_time, minutes_left):
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
        url = f"https://www.youtube.com/watch?v={vid}"
        msk_time = stream_time.strftime("%H:%M")
        embed = discord.Embed(
            title="🔔 Стрим скоро!",
            description=(
                f"Трансляция начнётся **через ~{minutes_left} мин** — **{msk_time} МСК**\n"
                f"**{title}**\n"
                f"Не пропусти: {url}"
            ),
            url=url,
            color=0xFF0000,
        )
        embed.set_footer(text=f"Старт в {msk_time} МСК · Не пропустите эфир!")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Напомнить себе", url=url, style=discord.ButtonStyle.link))
        try:
            msg = await channel.send(embed=embed, view=view)
            self._start_notify_message_id = msg.id
            log.info("YouTube: уведомление о скором стриме (%d мин)", minutes_left)
        except Exception:
            log.exception("YouTube: ошибка уведомления")

    async def _refresh_upcoming(self, stream_time, vid, title, minutes_left):
        if not self.discord_bot or not self._start_notify_message_id:
            return
        channel_id = self.config.get("notify_channel_id", 0)
        if channel_id <= 0:
            return
        channel = self.discord_bot.get_channel(channel_id)
        if not channel:
            return
        import discord
        url = f"https://www.youtube.com/watch?v={vid}"
        msk_time = stream_time.strftime("%H:%M")
        embed = discord.Embed(
            title="🔔 Стрим скоро!",
            description=(
                f"Трансляция начнётся **через ~{minutes_left} мин** — **{msk_time} МСК**\n"
                f"**{title}**\n"
                f"Не пропусти: {url}"
            ),
            url=url,
            color=0xFF0000,
        )
        embed.set_footer(text=f"Старт в {msk_time} МСК · Обновлено {datetime.now(MSK).strftime('%H:%M:%S')}")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Напомнить себе", url=url, style=discord.ButtonStyle.link))
        try:
            existing = await channel.fetch_message(self._start_notify_message_id)
            if existing:
                await existing.edit(content=None, embed=embed, view=view)
        except Exception:
            pass

    async def _notify_prep(self, stream_time):
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
        msk_time = stream_time.strftime("%H:%M")
        try:
            await channel.send(content=f"⏰ До стрима **{msk_time} МСК** меньше 5 минут — готовься!")
        except Exception:
            pass

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
        embed.add_field(name="Зрители", value="...", inline=True)
        embed.set_footer(text=f"Обновлено {datetime.now(MSK).strftime('%H:%M:%S')} МСК · Подключайся к эфиру!")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Открыть стрим", url=url, style=discord.ButtonStyle.link))
        content = None
        role_id = self.config.get("ping_role_id")
        if role_id == "@everyone":
            content = "@everyone"
        elif role_id:
            content = f"<@&{role_id}>"
        try:
            msg = await channel.send(content=content, embed=embed, view=view)
            self._live_message_id = msg.id
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
        url = f"https://www.youtube.com/@{self.channel_handle}/live"
        embed = discord.Embed(
            title="⚪ Стрим завершён",
            description=f"Спасибо, что смотрели! Новый эфир — на {url}",
            url=url,
            color=0x808080,
        )
        embed.add_field(name="📊 Пик зрителей", value=str(self._peak_viewers), inline=False)
        embed.set_footer(text=f"Офлайн с {datetime.now(MSK).strftime('%H:%M')} МСК")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Канал стримера", url=url, style=discord.ButtonStyle.link))
        try:
            await channel.send(embed=embed, view=view)
            log.info("YouTube: уведомление об окончании стрима")
        except Exception:
            log.exception("YouTube: ошибка отправки уведомления")

    async def _ensure_access_token(self):
        if self._access_token and time.time() < self._token_expires - 60:
            return self._access_token

        client_id = self.config.get("client_id", "")
        client_secret = self.config.get("client_secret", "")
        refresh_token = self.config.get("refresh_token", "")

        if not client_id or not client_secret or not refresh_token:
            return None

        try:
            async with self._session.post(TOKEN_URL, data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }) as resp:
                data = await resp.json()
                if "access_token" in data:
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 3600)
                    return self._access_token
                else:
                    log.error("YouTube OAuth: %s", data.get("error_description", "?"))
                    return None
        except Exception:
            log.exception("YouTube OAuth: ошибка обновления токена")
            return None

    async def _api_request(self, method, endpoint, params=None, json_body=None):
        token = await self._ensure_access_token()
        if not token:
            return None

        url = f"{API_BASE}{endpoint}"
        headers = {"Authorization": f"Bearer {token}"}
        if json_body:
            headers["Content-Type"] = "application/json; charset=UTF-8"

        try:
            if method == "GET":
                async with self._session.get(url, params=params, headers=headers) as resp:
                    return await resp.json()
            elif method == "POST":
                async with self._session.post(url, params=params, headers=headers, json=json_body) as resp:
                    return await resp.json()
            elif method == "DELETE":
                async with self._session.delete(url, params=params, headers=headers) as resp:
                    return await resp.json()
        except Exception:
            log.exception("YouTube API: ошибка %s %s", method, endpoint)
            return None

    async def _resolve_live_chat_id(self):
        if not self._video_id:
            return
        data = await self._api_request("GET", "/videos", params={
            "part": "liveStreamingDetails",
            "id": self._video_id,
        })
        if not data:
            return
        items = data.get("items", [])
        if not items:
            return
        details = items[0].get("liveStreamingDetails", {})
        self._live_chat_id = details.get("activeLiveChatId")
        if self._live_chat_id:
            log.info("YouTube: liveChatId=%s", self._live_chat_id)
        else:
            log.warning("YouTube: liveChatId не найден")

    async def delete_message(self, message_id):
        if not self._live_chat_id or not message_id:
            return False
        data = await self._api_request("DELETE", "/liveChat/messages", params={
            "id": message_id,
        })
        if data and "error" not in data:
            log.info("YouTube: сообщение %s удалено", message_id)
            return True
        else:
            log.warning("YouTube: ошибка удаления %s: %s", message_id, data)
            return False

    async def ban_user(self, channel_id, duration_seconds=None, reason="Нарушение правил"):
        if not self._live_chat_id or not channel_id:
            return False

        ban_type = "temporary" if duration_seconds else "permanent"
        body = {
            "snippet": {
                "liveChatId": self._live_chat_id,
                "type": ban_type,
                "banDetails": {
                    "type": "chatOwner",
                    "banDurationSeconds": str(duration_seconds) if duration_seconds else None,
                },
            },
        }
        if duration_seconds:
            body["snippet"]["banDurationSeconds"] = str(duration_seconds)

        data = await self._api_request("POST", "/liveChat/bans", params={"part": "snippet"}, json_body=body)
        if data and "error" not in data:
            log.info("YouTube: пользователь %s забанен (%s)", channel_id, ban_type)
            return True
        else:
            log.warning("YouTube: ошибка бана %s: %s", channel_id, data)
            return False

    async def _poll_messages(self):
        if not self._live_chat_id:
            return

        params = {
            "part": "snippet,authorDetails,id",
            "liveChatId": self._live_chat_id,
            "maxResults": 200,
        }
        if self._page_token:
            params["pageToken"] = self._page_token

        data = await self._api_request("GET", "/liveChat/messages", params=params)
        if not data:
            return

        if "error" in data:
            log.warning("YouTube API error: %s", data["error"].get("message", "?"))
            return

        self._page_token = data.get("nextPageToken")

        items = data.get("items", [])
        for item in items:
            msg_id = item.get("id", "")
            if msg_id in self._seen:
                continue
            self._seen.add(msg_id)
            if len(self._seen) > 500:
                self._seen.clear()

            snippet = item.get("snippet", {})
            author_details = item.get("authorDetails", {})

            display_text = snippet.get("displayMessage", "")
            author_name = author_details.get("displayName", "???")
            channel_id = author_details.get("channelId", "")
            is_owner = author_details.get("isChatOwner", False)
            is_mod = author_details.get("isChatModerator", False)

            if not display_text:
                continue

            badge = ""
            if is_owner:
                badge = "\U0001f3af"
            elif is_mod:
                badge = "\U0001f6e1"

            if not (is_owner or is_mod):
                violation = self.moderation.check(display_text, author_name)
                if violation:
                    log.warning("YouTube модерация: %s (%s) — %s", author_name, violation["reason"], display_text[:60])
                    if self.bridge:
                        await self.bridge.forward_to_discord(
                            "YT \u26a0\ufe0f Модерация",
                            f"**{author_name}** — {violation['reason']}: {display_text[:100]}",
                        )
                    await self.delete_message(msg_id)
                    if self.ban_on_violation and channel_id:
                        await self.ban_user(channel_id, self.ban_duration)
                    continue

            log.info("YouTube: %s%s: %s", badge, author_name, display_text[:80])
            if self.bridge:
                await self.bridge.forward_to_discord(f"YT {badge}{author_name}", display_text)
