import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord

log = logging.getLogger("twitch")

STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "twitch_live_state.json"
MSK = timezone(timedelta(hours=3))


class StreamLiveNotifier:
    ANON_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

    def __init__(self, config, discord_bot):
        self.config = config
        self.discord_bot = discord_bot
        self._task = None
        self._session = None
        self._access_token = None
        self._token_expires_at = 0
        state = self._load_state()
        self._notified_stream_id = state.get("stream_id") if isinstance(state, dict) else None
        self._message_id = state.get("message_id") if isinstance(state, dict) else None
        self._upcoming_notified = state.get("upcoming_notified") if isinstance(state, dict) else None
        self._prep_notified = state.get("prep_notified") if isinstance(state, dict) else None
        self._peak_viewers = state.get("peak_viewers") if isinstance(state, dict) else 0
        self._offline_checks = 0

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.exception("StreamLive: ошибка чтения файла состояния")
        return None

    def _save_state(self, stream_id=None, message_id=None, upcoming_notified=None, prep_notified=None, peak_viewers=None):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(
                json.dumps({
                    "stream_id": stream_id,
                    "message_id": message_id,
                    "upcoming_notified": upcoming_notified,
                    "prep_notified": prep_notified,
                    "peak_viewers": peak_viewers if peak_viewers is not None else self._peak_viewers,
                }),
                encoding="utf-8",
            )
        except Exception:
            log.exception("StreamLive: ошибка записи файла состояния")

    def start(self, loop):
        self._task = loop.create_task(self._run())
        return self._task

    def stop(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self):
        channel_id = self.config.get("channel_id", 0)
        interval = max(1, self.config.get("poll_interval_minutes", 2)) * 60
        if channel_id <= 0:
            log.info("StreamLive: нет channel_id, модуль неактивен")
            return
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self._session = session
            log.info("StreamLive: мониторинг старта стрима каждые %d мин", interval // 60)
            while True:
                try:
                    await self._check_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("StreamLive: ошибка цикла мониторинга (продолжаю)")
                await asyncio.sleep(interval)

    async def _check_once(self):
        try:
            stream = await self._get_live_stream()
        except Exception:
            log.warning("StreamLive: ошибка запроса, статус стрима не меняю")
            return
        if stream is None:
            self._offline_checks += 1
            if self._offline_checks < 2:
                log.info("StreamLive: стрим не найден, повторная проверка (%d/2)", self._offline_checks)
                return
            if self._notified_stream_id:
                log.info("StreamLive: стрим завершён")
            await self._notify_offline()
            self._notified_stream_id = None
            self._message_id = None
            self._peak_viewers = 0
            self._save_state(None)
            await self._check_upcoming()
            return
        self._offline_checks = 0
        stream_id = stream.get("id")
        viewers = stream.get("viewer_count") or 0
        if viewers > self._peak_viewers:
            self._peak_viewers = viewers
        if self._notified_stream_id != stream_id:
            log.info("StreamLive: стрим начался: %s", stream.get("title"))
            await self._notify(stream, edit=False)
            self._notified_stream_id = stream_id
            self._save_state(stream_id, self._message_id)
        else:
            await self._notify(stream, edit=True)

    async def _find_stream_message(self, channel, online_only=False):
        try:
            async for msg in channel.history(limit=30):
                if msg.author.id != self.discord_bot.user.id:
                    continue
                if not msg.embeds:
                    continue
                for emb in msg.embeds:
                    title = emb.title or ""
                    if online_only:
                        if title.startswith("🔴"):
                            return msg
                    elif title.startswith(("🔴", "⚪")):
                        return msg
        except Exception:
            log.exception("StreamLive: ошибка поиска эмбеда стрима")
        return None

    async def _notify_offline(self):
        channel = self.discord_bot.get_channel(self.config.get("channel_id", 0))
        if channel is None:
            try:
                channel = await self.discord_bot.fetch_channel(self.config.get("channel_id", 0))
            except Exception:
                channel = None
        if channel is None:
            return
        if not self._message_id:
            found = await self._find_stream_message(channel, online_only=True)
            if found is None:
                log.info("StreamLive: активный эмбед стрима не найден, пропускаю")
                return
            self._message_id = found.id
        existing = None
        try:
            existing = await channel.fetch_message(self._message_id)
        except Exception:
            existing = None
        if existing is None:
            return
        if existing.embeds and (existing.embeds[0].title or "").startswith("⚪"):
            return
        streamer = self.config.get("channel")
        embed = discord.Embed(
            title="⚪ Стрим завершён",
            description=f"Спасибо, что смотрели! Новый эфир — на https://www.twitch.tv/{streamer}",
            url=f"https://www.twitch.tv/{streamer}",
            color=0x9146FF,
        )
        embed.add_field(name="📊 Пик зрителей", value=str(self._peak_viewers), inline=False)
        embed.set_footer(text=f"Офлайн с {datetime.now(MSK).strftime('%H:%M')} МСК")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Канал стримера", url=f"https://www.twitch.tv/{streamer}", style=discord.ButtonStyle.link))
        try:
            await existing.edit(content=None, embed=embed, view=view)
            log.info("StreamLive: уведомление переведено в офлайн-статус")
        except Exception:
            log.exception("StreamLive: ошибка обновления офлайн-статуса")

    async def _check_upcoming(self):
        lead_minutes = self.config.get("upcoming_lead_minutes", 10)
        prep_minutes = self.config.get("prep_lead_minutes", 5)
        next_start = await self.get_next_schedule_start()
        if next_start is None:
            self._upcoming_notified = None
            self._prep_notified = None
            self._save_state(None, upcoming_notified=None, prep_notified=None)
            return
        key = next_start.isoformat()
        now = datetime.now(timezone.utc)
        delta = next_start - now
        seconds = int(delta.total_seconds())
        if not (0 < seconds <= lead_minutes * 60):
            return
        if self._upcoming_notified != key:
            await self._notify_upcoming(next_start)
            self._upcoming_notified = key
            self._save_state(None, upcoming_notified=key)
        else:
            await self._refresh_upcoming_embed(next_start, key)
        if 0 < seconds <= prep_minutes * 60 and self._prep_notified != key:
            await self._notify_prep(next_start)
            self._prep_notified = key
            self._save_state(None, prep_notified=key)

    async def _notify_prep(self, start_dt):
        channel = self.discord_bot.get_channel(self.config.get("prep_channel_id", 0))
        if channel is None:
            try:
                channel = await self.discord_bot.fetch_channel(self.config.get("prep_channel_id", 0))
            except Exception:
                channel = None
        if channel is None:
            log.warning("StreamLive: prep-канал %s не найден", self.config.get("prep_channel_id"))
            return
        user_id = self.config.get("prep_user_id")
        if not user_id:
            return
        msk = start_dt.astimezone(MSK)
        try:
            await channel.send(
                f"<@{user_id}> ⏰ До старта стрима **{msk.strftime('%H:%M')} МСК** меньше "
                f"{self.config.get('prep_lead_minutes', 5)} минут — готовься! 🎬"
            )
            log.info("StreamLive: prep-пинг отправлен за %d мин до эфира", self.config.get("prep_lead_minutes", 5))
        except Exception:
            log.exception("StreamLive: ошибка prep-пинга")

    async def _refresh_upcoming_embed(self, start_dt, key):
        channel = self.discord_bot.get_channel(self.config.get("channel_id", 0))
        if channel is None:
            return
        now = datetime.now(timezone.utc)
        minutes_left = max(1, int((start_dt - now).total_seconds() // 60))
        found = None
        try:
            async for msg in channel.history(limit=30):
                if msg.author.id != self.discord_bot.user.id:
                    continue
                if not msg.embeds:
                    continue
                if (msg.embeds[0].title or "").startswith("🔔"):
                    found = msg
                    break
        except Exception:
            log.exception("StreamLive: ошибка поиска эмбеда напоминания")
        if found is None:
            return
        msk = start_dt.astimezone(MSK)
        streamer = self.config.get("channel")
        embed = discord.Embed(
            title="🔔 Стрим скоро!",
            description=(
                f"Трансляция начнётся **через ~{minutes_left} мин** — **{msk.strftime('%H:%M')} МСК**\n"
                f"Не пропусти: https://www.twitch.tv/{streamer}"
            ),
            url=f"https://www.twitch.tv/{streamer}",
            color=0x9146FF,
        )
        embed.set_footer(text=f"Старт в {msk.strftime('%H:%M')} МСК · Обновлено {datetime.now(MSK).strftime('%H:%M:%S')}")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Напомнить себе", url=f"https://www.twitch.tv/{streamer}", style=discord.ButtonStyle.link))
        try:
            await found.edit(content=None, embed=embed, view=view)
            log.info("StreamLive: напоминание обновлено: %d мин до эфира", minutes_left)
        except Exception:
            log.exception("StreamLive: ошибка обновления напоминания")

    async def _notify_upcoming(self, start_dt):
        channel = self.discord_bot.get_channel(self.config.get("channel_id", 0))
        if channel is None:
            try:
                channel = await self.discord_bot.fetch_channel(self.config.get("channel_id", 0))
            except Exception:
                channel = None
        if channel is None:
            log.warning("StreamLive: канал %s не найден", self.config.get("channel_id"))
            return
        streamer = self.config.get("channel")
        msk = start_dt.astimezone(MSK)
        now = datetime.now(timezone.utc)
        minutes_left = max(1, int((start_dt - now).total_seconds() // 60))
        url = f"https://www.twitch.tv/{streamer}"
        embed = discord.Embed(
            title="🔔 Стрим скоро!",
            description=(
                f"Трансляция начнётся **через ~{minutes_left} мин** — **{msk.strftime('%H:%M')} МСК**\n"
                f"Не пропусти: https://www.twitch.tv/{streamer}"
            ),
            url=url,
            color=0x9146FF,
        )
        embed.set_footer(text=f"Старт в {msk.strftime('%H:%M')} МСК · Не пропустите эфир!")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Напомнить себе", url=url, style=discord.ButtonStyle.link))
        content = None
        role_id = self.config.get("ping_role_id")
        if role_id == "@everyone":
            content = "@everyone"
        elif role_id:
            content = f"<@&{role_id}>"
        try:
            await channel.send(content=content, embed=embed, view=view)
            log.info("StreamLive: отправлено уведомление о скором стриме (%d мин)", minutes_left)
        except Exception:
            log.exception("StreamLive: ошибка отправки уведомления о скором стриме")

    async def _get_live_stream(self):
        channel = self.config.get("channel")
        if not channel:
            return None
        client_id = self.config.get("client_id")
        if client_id and client_id not in ("ВАШ_CLIENT_ID", "your_client_id"):
            try:
                return await self._get_live_stream_helix(channel, client_id)
            except Exception:
                log.exception("StreamLive: ошибка Helix, пробую анонимный endpoint")
        return await self._get_live_stream_anon(channel)

    async def get_live_stream_info(self):
        try:
            return await self._get_live_stream()
        except Exception:
            log.exception("StreamLive: ошибка запроса информации о стриме")
            return None

    async def get_viewer_count(self) -> int | None:
        channel = self.config.get("channel")
        if not channel:
            return None
        headers = {"Client-Id": self.ANON_CLIENT_ID, "Content-Type": "application/json"}
        query = (
            "query($login: String!){user(login: $login){stream{viewersCount}}}"
        )
        payload = {"query": query, "variables": {"login": channel}}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://gql.twitch.tv/gql", headers=headers, json=payload
                ) as resp:
                    data = await resp.json()
            stream = (data.get("data") or {}).get("user") or {}
            stream = stream.get("stream")
            if not stream:
                return None
            return stream.get("viewersCount")
        except Exception:
            log.exception("StreamLive: ошибка запроса зрителей")
            return None

    async def get_next_schedule_start(self):
        channel = self.config.get("channel")
        if not channel:
            return None
        channel_id = await self._get_twitch_user_id(channel)
        if channel_id is None:
            return None
        headers = {"Client-Id": self.ANON_CLIENT_ID, "Content-Type": "application/json"}
        query = "query($id: ID!){channel(id: $id){schedule{nextSegment{startAt}}}}"
        payload = {"query": query, "variables": {"id": channel_id}}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://gql.twitch.tv/gql", headers=headers, json=payload
                ) as resp:
                    data = await resp.json()
            segment = (data.get("data") or {}).get("channel") or {}
            segment = segment.get("schedule") or {}
            segment = segment.get("nextSegment") or {}
            start = segment.get("startAt")
            if not start:
                return None
            return datetime.fromisoformat(start.replace("Z", "+00:00"))
        except Exception:
            log.exception("StreamLive: ошибка запроса расписания")
            return None

    async def _get_twitch_user_id(self, channel):
        headers = {"Client-Id": self.ANON_CLIENT_ID, "Content-Type": "application/json"}
        query = "query($login: String!){user(login: $login){id}}"
        payload = {"query": query, "variables": {"login": channel}}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://gql.twitch.tv/gql", headers=headers, json=payload
                ) as resp:
                    data = await resp.json()
            user = (data.get("data") or {}).get("user") or {}
            return user.get("id")
        except Exception:
            log.exception("StreamLive: ошибка получения ID канала")
            return None

    async def _get_live_stream_anon(self, channel):
        headers = {"Client-Id": self.ANON_CLIENT_ID, "Content-Type": "application/json"}
        query = (
            "query($login: String!){user(login: $login){stream{id createdAt title game{name} "
            "viewersCount previewImageURL}}}"
        )
        payload = {"query": query, "variables": {"login": channel}}
        async with self._session.post(
            "https://gql.twitch.tv/gql", headers=headers, json=payload
        ) as resp:
            data = await resp.json()
        if not resp.ok or data.get("errors"):
            raise RuntimeError(f"Twitch GraphQL ошибка: {data.get('errors') or resp.status}")
        stream = (data.get("data") or {}).get("user") or {}
        stream = stream.get("stream")
        if not stream:
            return None
        return {
            "id": stream.get("id"),
            "created_at": stream.get("createdAt"),
            "title": stream.get("title"),
            "game_name": (stream.get("game") or {}).get("name"),
            "viewer_count": stream.get("viewersCount"),
            "thumbnail_url": stream.get("previewImageURL"),
        }

    async def _get_live_stream_helix(self, channel, client_id):
        headers = {
            "Client-Id": client_id,
            "Authorization": f"Bearer {await self._get_token()}",
        }
        params = {"user_login": channel}
        async with self._session.get(
            "https://api.twitch.tv/helix/streams", headers=headers, params=params
        ) as resp:
            data = await resp.json()
        streams = data.get("data") or []
        if not streams:
            return None
        return streams[0]

    async def _get_token(self):
        now = asyncio.get_event_loop().time()
        if self._access_token is not None and now < self._token_expires_at - 60:
            return self._access_token
        client_id = self.config["client_id"]
        client_secret = self.config["client_secret"]
        async with self._session.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
        ) as resp:
            data = await resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = now + data.get("expires_in", 3600)
        log.info("StreamLive: получен access token, действителен %d сек", data.get("expires_in", 0))
        return self._access_token

    async def _notify(self, stream, edit=False):
        channel = self.discord_bot.get_channel(self.config.get("channel_id", 0))
        if channel is None:
            try:
                channel = await self.discord_bot.fetch_channel(self.config.get("channel_id", 0))
            except Exception:
                channel = None
        if channel is None:
            log.warning("StreamLive: канал %s не найден", self.config.get("channel_id"))
            return
        streamer = self.config.get("channel")
        title = stream.get("title") or "Стрим начался!"
        game = stream.get("game_name") or ""
        viewer = stream.get("viewer_count") or 0
        thumbnail = (stream.get("thumbnail_url") or "").replace("{width}", "1280").replace("{height}", "720")
        if thumbnail:
            thumbnail = f"{thumbnail}?t={int(time.time())}"
        url = f"https://www.twitch.tv/{streamer}"
        embed = discord.Embed(
            title="🔴 Стрим идёт!" if edit else "🔴 Стрим начался!",
            description=f"**{title}**",
            url=url,
            color=0x9146FF,
        )
        if game:
            embed.add_field(name="Игра", value=game, inline=True)
        viewers_text = str(viewer)
        if self._peak_viewers > viewer:
            viewers_text = f"{viewer} (пик {self._peak_viewers})"
        embed.add_field(name="Зрители", value=viewers_text, inline=True)
        if thumbnail:
            embed.set_image(url=thumbnail)
        embed.set_footer(
            text=f"Обновлено {datetime.now(MSK).strftime('%H:%M:%S')} МСК · Подключайся к эфиру!"
        )
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Открыть стрим", url=url, style=discord.ButtonStyle.link))
        content = None
        if not edit:
            role_id = self.config.get("ping_role_id")
            if role_id == "@everyone":
                content = "@everyone"
            elif role_id:
                content = f"<@&{role_id}>"

        if edit:
            existing = None
            if self._message_id:
                try:
                    existing = await channel.fetch_message(self._message_id)
                except Exception:
                    existing = None
            try:
                if existing is not None:
                    await existing.edit(content=None, embed=embed, view=view)
                    log.info("StreamLive: уведомление обновлено в канале %s", channel.id)
                else:
                    msg = await channel.send(content=content, embed=embed, view=view)
                    self._message_id = msg.id
                    self._save_state(self._notified_stream_id or stream.get("id"), msg.id)
                    log.info("StreamLive: уведомление отправлено в канал %s", channel.id)
            except Exception:
                log.exception("StreamLive: ошибка отправки уведомления")
            return

        try:
            msg = await channel.send(content=content, embed=embed, view=view)
            self._message_id = msg.id
            self._save_state(stream.get("id"), msg.id)
            log.info("StreamLive: уведомление отправлено в канал %s", channel.id)
        except Exception:
            log.exception("StreamLive: ошибка отправки уведомления")
