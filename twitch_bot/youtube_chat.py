import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict, deque

import aiohttp

from config import PROXY_URL
from twitch_bot.youtube_token import get_token_manager
from twitch_bot.chat_overlay import push_chat

_yt_chat_buffer: deque = deque(maxlen=50)
_yt_chat_seen_ids: set = set()


def get_yt_chat_messages() -> list:
    return list(_yt_chat_buffer)


def clear_yt_chat_buffer():
    _yt_chat_buffer.clear()
    _yt_chat_seen_ids.clear()

log = logging.getLogger("youtube")

MSK = timezone(timedelta(hours=3))

API_BASE = "https://www.googleapis.com/youtube/v3"
_YT_CHAT_STATE = Path(__file__).resolve().parent.parent / "data" / "yt_chat_state.json"


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
        # Рекомендованный интервал опроса, возвращаемый API в pollingIntervalMillis
        # (дефект D14): используем его, чтобы не превышать суточную квоту.
        self._polling_interval = None
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
        self.moderation_enabled = config.get("moderation", {}).get("enabled", True)
        self.ban_on_violation = config.get("moderation", {}).get("ban_on_violation", False)
        self.ban_duration = config.get("moderation", {}).get("ban_duration_seconds", 300)
        self.screenshot_interval = config.get("screenshot_seconds", 600)
        self.screenshot_channel_id = config.get("screenshot_channel_id", 0)
        self._last_screenshot = 0
        self._last_screenshot_hash = None
        self._promo_message = config.get("promo_message", "")
        self._promo_interval = config.get("promo_interval_seconds", 1800)
        self._last_promo = 0
        cmds_cfg = config.get("commands") or {}
        self._cmd_prefix = cmds_cfg.get("prefix", "!")
        self._custom_commands = {}
        for cmd in cmds_cfg.get("list", []):
            name = str(cmd.get("name", "")).lower().lstrip(self._cmd_prefix)
            if name:
                self._custom_commands[name] = cmd
        self._cmd_cooldowns = {}
        self._recently_sent: set = set()
        self._load_chat_state()

    def _load_chat_state(self):
        try:
            if _YT_CHAT_STATE.exists():
                data = json.loads(_YT_CHAT_STATE.read_text(encoding="utf-8"))
                if data.get("video_id"):
                    self._video_id = data["video_id"]
                    self._page_token = data.get("page_token")
                    self._live_chat_id = data.get("live_chat_id")
                    self._seen = set(data.get("seen_ids", []))
                    _yt_chat_seen_ids.update(self._seen)
                    log.info("YouTube chat state restored: video=%s token=%s seen=%d", self._video_id, bool(self._page_token), len(self._seen))
        except Exception:
            pass

    def _save_chat_state(self):
        try:
            _YT_CHAT_STATE.parent.mkdir(parents=True, exist_ok=True)
            _YT_CHAT_STATE.write_text(json.dumps({
                "video_id": self._video_id,
                "page_token": self._page_token,
                "live_chat_id": self._live_chat_id,
                "seen_ids": list(self._seen)[-500:],
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    async def start(self):
        self._running = True
        timeout = aiohttp.ClientTimeout(total=30)
        self._session = aiohttp.ClientSession(timeout=timeout, proxy=PROXY_URL or None)
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
                interval = (self._polling_interval or self.poll_interval) or 5
                await asyncio.sleep(interval)

                if self._video_id:
                    await self._check_live()
                    await self._update_viewers()
                    await self._take_screenshot()
                    await self._maybe_send_promo()

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
            if resp.status != 200:
                log.debug("YouTube: страница %s вернула HTTP %s", url, resp.status)
                return ""
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
            if "error" in data:
                log.warning("YouTube: ошибка API в _check_live: %s", data.get("error", {}).get("message"))
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
                log.debug("YouTube broadcast %s: lifecycle=%s broadcastStatus=%s", bid, lifecycle, broadcast_status)

                is_live = (
                    broadcast_status in ("live", "testing")
                    and lifecycle in ("live", "testing")
                )

                if is_live:
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
                    self._save_chat_state()
                if not self._notified_live:
                    from twitch_bot.stream_state import set_stream_live
                    set_stream_live(True)
                    await self._notify_live_start()
                    self._notified_live = True
            else:
                if self._video_id:
                    from twitch_bot.stream_state import set_stream_live
                    set_stream_live(False)
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
                    self._save_chat_state()
        except Exception:
            log.exception("YouTube: ошибка проверки стрима")

    async def _update_viewers(self):
        if not self._video_id:
            return
        try:
            data = await self._api_request("GET", "/videos", params={
                "part": "liveStreamingDetails",
                "id": self._video_id,
            })
            if not data:
                return
            if "error" in data:
                return
            items = data.get("items", [])
            if not items:
                return
            details = items[0].get("liveStreamingDetails", {})
            count = int(details.get("concurrentViewers", 0))
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

    async def _take_screenshot(self):
        if not self.discord_bot or not self._video_id:
            return
        now = time.time()
        if now - self._last_screenshot < self.screenshot_interval:
            return
        if self.screenshot_interval <= 0:
            return

        channel_id = self.screenshot_channel_id or self.config.get("notify_channel_id", 0)
        if channel_id <= 0:
            return

        thumb_url = f"https://img.youtube.com/vi/{self._video_id}/maxresdefault.jpg"
        fallback_url = f"https://img.youtube.com/vi/{self._video_id}/hqdefault.jpg"
        try:
            async with self._session.get(thumb_url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                else:
                    async with self._session.get(fallback_url) as resp2:
                        if resp2.status != 200:
                            self._last_screenshot = now
                            return
                        data = await resp2.read()
                    thumb_url = fallback_url
                current_hash = hash(data)
                if current_hash == self._last_screenshot_hash:
                    self._last_screenshot = now
                    return
                self._last_screenshot_hash = current_hash
        except Exception:
            self._last_screenshot = now
            return

        self._last_screenshot = now
        channel = self.discord_bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.discord_bot.fetch_channel(channel_id)
            except Exception:
                return

        import discord
        url = f"https://www.youtube.com/watch?v={self._video_id}"
        embed = discord.Embed(
            title="📸 Интересный момент",
            description=f"**{self._stream_title or 'Стрим'}**",
            url=url,
            color=0xFF0000,
        )
        embed.set_image(url=thumb_url)
        embed.add_field(name="Зрители", value=str(self._current_viewers), inline=True)
        embed.set_footer(text=f"{datetime.now(MSK).strftime('%H:%M:%S')} МСК · YouTube")
        try:
            await channel.send(embed=embed)
            log.info("YouTube: скриншот отправлен")
        except Exception:
            log.exception("YouTube: ошибка отправки скриншота")

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
                    await self._refresh_upcoming(stream_time, vid, title, minutes_left)
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
        thumb_url = f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg"
        embed.set_image(url=thumb_url)
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
        thumb_url = f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg"
        embed.set_image(url=thumb_url)
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
        thumb_url = f"https://img.youtube.com/vi/{self._video_id}/maxresdefault.jpg"
        embed.set_image(url=thumb_url)
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

    async def _api_request(self, method, endpoint, params=None, json_body=None):
        tm = get_token_manager()
        kwargs = {}
        if params:
            kwargs["params"] = params
        if json_body:
            kwargs["json"] = json_body
        return await tm.api(method, endpoint, **kwargs)

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
            self._save_chat_state()
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

    async def send_chat_message(self, text):
        if not self._live_chat_id or not text:
            return False
        body = {
            "snippet": {
                "liveChatId": self._live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {
                    "messageText": text,
                },
            },
        }
        data = await self._api_request("POST", "/liveChat/messages", params={"part": "snippet"}, json_body=body)
        if data and "error" not in data:
            log.info("YouTube: сообщение отправлено в чат")
            return True
        else:
            log.warning("YouTube: ошибка отправки сообщения: %s", data)
            return False

    async def _maybe_send_promo(self):
        if not self._promo_message or self._promo_interval <= 0:
            return
        now = time.time()
        if now - self._last_promo < self._promo_interval:
            return
        self._last_promo = now
        await self.send_chat_message(self._promo_message)

    async def _handle_command(self, text, author, author_id, is_owner, is_mod):
        parts = text[len(self._cmd_prefix):].split(maxsplit=1)
        if not parts:
            return
        name = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        if name == "commands" or name == "команды":
            public = [n for n, cmd in self._custom_commands.items() if not cmd.get("mod_only")]
            listing = ", ".join(self._cmd_prefix + n for n in sorted(public))
            await self.send_chat_message(f"Команды: {listing}" if listing else "Команд нет")
            return

        cmd = self._custom_commands.get(name)
        if not cmd:
            return

        if cmd.get("mod_only") and not (is_owner or is_mod):
            return

        cooldown = cmd.get("cooldown", 10)
        now = time.time()
        last = self._cmd_cooldowns.get(name, 0)
        if now - last < cooldown:
            return
        # Дефект D07: кулдауны команд росли безгранично по имени команды.
        if len(self._cmd_cooldowns) > 10000:
            cutoff = now - max(cooldown, 600)
            for k in [k for k, t in self._cmd_cooldowns.items() if now - t > cutoff]:
                self._cmd_cooldowns.pop(k, None)
        self._cmd_cooldowns[name] = now

        response = cmd.get("text", "")
        if response:
            response = response.replace("{user}", author).replace("{args}", args)
            await self.send_chat_message(response)
            self._recently_sent.add(response)
            if len(self._recently_sent) > 50:
                self._recently_sent.clear()

    async def ban_user(self, channel_id, duration_seconds=None, reason="Нарушение правил"):
        if not self._live_chat_id or not channel_id:
            return False

        ban_type = "temporary" if duration_seconds else "permanent"
        ban_details = {}
        if duration_seconds:
            ban_details["banDurationSeconds"] = str(duration_seconds)
        body = {
            "snippet": {
                "liveChatId": self._live_chat_id,
                "type": ban_type,
                "bannedUserDetails": {
                    "channelId": channel_id,
                },
                "banDetails": ban_details,
            },
        }

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
        if data.get("pollingIntervalMillis"):
            self._polling_interval = max(1, int(data["pollingIntervalMillis"]) / 1000)
        self._save_chat_state()

        items = data.get("items", [])
        for item in items:
            msg_id = item.get("id", "")
            if msg_id in self._seen:
                continue
            self._seen.add(msg_id)
            if len(self._seen) > 5000:
                self._seen = {msg_id}

            snippet = item.get("snippet", {})
            author_details = item.get("authorDetails", {})

            display_text = snippet.get("displayMessage", "")
            author_name = author_details.get("displayName", "???")
            channel_id = author_details.get("channelId", "")
            is_owner = author_details.get("isChatOwner", False)
            is_mod = author_details.get("isChatModerator", False)

            if not display_text:
                continue

            if display_text in self._recently_sent:
                self._recently_sent.discard(display_text)
                continue

            badge = ""
            if is_owner:
                badge = "\U0001f3af"
            elif is_mod:
                badge = "\U0001f6e1"

            if not (is_owner or is_mod):
                violation = self.moderation.check(display_text, channel_id)
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

            if display_text.startswith(self._cmd_prefix):
                await self._handle_command(display_text, author_name, channel_id, is_owner, is_mod)

            if msg_id not in _yt_chat_seen_ids:
                _yt_chat_seen_ids.add(msg_id)
                if len(_yt_chat_seen_ids) > 5000:
                    _yt_chat_seen_ids.clear()
                _yt_chat_buffer.append({
                    "author": author_name,
                    "text": display_text,
                    "badge": badge,
                    "is_owner": is_owner,
                    "is_mod": is_mod,
                    "avatar": author_details.get("profileImageUrl", ""),
                    "time": time.time(),
                })
                push_chat(
                    "yt",
                    author_name,
                    display_text,
                    role="owner" if is_owner else "mod" if is_mod else "",
                    avatar=author_details.get("profileImageUrl", ""),
                )

            log.info("YouTube: %s%s: %s", badge, author_name, display_text[:80])
            if self.bridge:
                await self.bridge.forward_to_discord(f"YT {badge}{author_name}", display_text)

        self._save_chat_state()
