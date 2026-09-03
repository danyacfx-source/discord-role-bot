"""Уведомления о старте/завершении стрима на Kick.

Работает на публичном Kick API (безопасно). Каждые poll_interval запрашивает
livestream канала и в момент старта шлёт эмбед в Discord-канал (с пингом роли),
а при завершении переводит эмбед в офлайн и постит краткие итоги.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord

from config import DATA_DIR
from kick_bot.kick_api import KickAPI

log = logging.getLogger("kick")

STATE_FILE = DATA_DIR / "kick_live_state.json"
MSK = timezone(timedelta(hours=3))
_SENTINEL = object()


class KickLiveNotifier:
    def __init__(self, config, discord_bot):
        self.config = config
        self.discord_bot = discord_bot
        self.api = KickAPI(config)
        self._task = None
        self._status_task = None
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else None
        except Exception:
            log.exception("KickLive: ошибка чтения state")
            state = None
        self._notified_live = bool(state.get("live")) if state else False
        self._message_id = state.get("message_id") if state else None
        self._peak_viewers = state.get("peak_viewers") if state else 0
        self._start_ts = state.get("start_ts") if state else 0
        self._offline_checks = 0

    def start(self, loop):
        self._task = loop.create_task(self._run())
        if self.config.get("status_viewers", False):
            self._status_task = loop.create_task(self._status_loop())
        return self._task

    def stop(self):
        for t in (self._task, self._status_task):
            if t is not None:
                t.cancel()
        self._task = None
        self._status_task = None

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(
                json.dumps({
                    "live": self._notified_live,
                    "message_id": self._message_id,
                    "peak_viewers": self._peak_viewers,
                    "start_ts": self._start_ts,
                }),
                encoding="utf-8",
            )
        except Exception:
            log.exception("KickLive: ошибка записи state")

    async def _get_channel(self):
        return self.discord_bot.get_channel(self.config.get("channel_id", 0)) or await self._fetch_channel()

    async def _fetch_channel(self):
        try:
            return await self.discord_bot.fetch_channel(self.config.get("channel_id", 0))
        except Exception:
            log.exception("KickLive: канал %s недоступен", self.config.get("channel_id"))
            return None

    def _role_mention(self):
        role_id = self.config.get("ping_role_id")
        if role_id == "@everyone":
            return "@everyone"
        if role_id:
            return f"<@&{role_id}>"
        return None

    async def _run(self):
        interval = max(30, self.config.get("poll_interval_seconds", 60))
        log.info("KickLive: мониторинг стрима %s каждые %d сек", self.api.slug, interval)
        await self._wait_ready()
        while True:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("KickLive: ошибка цикла (продолжаю)")
            await asyncio.sleep(interval)

    async def _wait_ready(self):
        while not self.discord_bot.is_ready():
            await asyncio.sleep(2)

    async def _check_once(self):
        info = await self.api.summarize()
        if info is None:
            log.warning("KickLive: не удалось получить данные канала")
            return
        viewers = info.get("viewer_count") or 0
        if viewers > self._peak_viewers:
            self._peak_viewers = viewers
        if info["online"]:
            self._offline_checks = 0
            if not self._notified_live:
                log.info("KickLive: стрим начался: %s", info.get("title"))
                self._start_ts = info.get("start_time_ts") or time.time()
                await self._notify_start(info)
                self._notified_live = True
                self._save_state()
            else:
                await self._notify_update(info)
        else:
            self._offline_checks += 1
            if self._offline_checks < 2:
                return
            if self._notified_live:
                log.info("KickLive: стрим завершён")
                await self._post_summary()
                await self._notify_offline(info)
                self._notified_live = False
                self._message_id = None
                self._peak_viewers = 0
                self._start_ts = 0
                self._save_state()
            self._offline_checks = 0

    def _embed(self, info, title, color, footer):
        embed = discord.Embed(title=title, url=info["url"], color=color)
        desc = info.get("title")
        if info.get("category"):
            desc = f"**{info.get('title')}**\n🎮 {info['category']}"
        embed.description = desc
        embed.add_field(name="Зрители", value=str(info.get("viewer_count") or 0), inline=True)
        thumb = info.get("thumbnail_url")
        if thumb:
            embed.set_image(url=f"{thumb}")
        embed.set_footer(text=footer)
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Открыть стрим", url=info["url"], style=discord.ButtonStyle.link))
        return embed, view

    async def _send_or_edit(self, info, title, color, footer, ping):
        channel = await self._get_channel()
        if channel is None:
            return
        embed, view = self._embed(info, title, color, footer)
        if self._message_id:
            try:
                existing = await channel.fetch_message(self._message_id)
                if existing is not None:
                    await existing.edit(content=None, embed=embed, view=view)
                    return
            except Exception:
                pass
        try:
            msg = await channel.send(content=ping, embed=embed, view=view)
            self._message_id = msg.id
        except Exception:
            log.exception("KickLive: ошибка отправки уведомления")

    async def _notify_start(self, info):
        await self._send_or_edit(
            info,
            "🔴 Стрим начался!",
            0x53FC18,
            f"Kick · {datetime.now(MSK).strftime('%H:%M')} МСК · Подключайся!",
            self._role_mention(),
        )
        self._save_state()

    async def _notify_update(self, info):
        await self._send_or_edit(
            info,
            "🔴 Стрим идёт!",
            0x53FC18,
            f"Обновлено {datetime.now(MSK).strftime('%H:%M:%S')} МСК",
            None,
        )

    async def _notify_offline(self, info):
        channel = await self._get_channel()
        if channel is None or not self._message_id:
            return
        embed = discord.Embed(
            title="⚪ Стрим завершён",
            description=f"Спасибо, что смотрели! Новый эфир: {info['url']}",
            url=info["url"],
            color=0x53FC18,
        )
        embed.add_field(name="📊 Пик зрителей", value=str(self._peak_viewers), inline=False)
        embed.set_footer(text=f"Офлайн с {datetime.now(MSK).strftime('%H:%M')} МСК")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Канал", url=info["url"], style=discord.ButtonStyle.link))
        try:
            existing = await channel.fetch_message(self._message_id)
            await existing.edit(content=None, embed=embed, view=view)
        except Exception:
            log.exception("KickLive: ошибка офлайн-статуса")

    async def _post_summary(self):
        channel = await self._get_channel()
        if channel is None:
            return
        hours, rem = divmod(int((time.time() - self._start_ts) if self._start_ts else 0), 3600)
        m, s = divmod(rem, 60)
        uptime_str = f"{hours}ч {m}м" if hours else f"{m}м {s}с"
        embed = discord.Embed(
            title="📊 Итоги Kick-стрима",
            color=0x53FC18,
        )
        embed.add_field(name="Длительность", value=uptime_str, inline=True)
        embed.add_field(name="Пик зрителей", value=str(self._peak_viewers), inline=True)
        try:
            await channel.send(embed=embed)
        except Exception:
            log.exception("KickLive: ошибка итогов")

    async def _status_loop(self):
        await self._wait_ready()
        interval = max(60, self.config.get("status_poll_seconds", 120))
        while True:
            try:
                viewers = await self.api.get_viewer_count()
                if viewers is not None:
                    await self.discord_bot.change_presence(
                        activity=discord.Activity(
                            type=discord.ActivityType.watching,
                            name=f"Kick · {viewers} зрителей",
                        )
                    )
            except Exception:
                log.exception("KickLive: ошибка статуса")
            await asyncio.sleep(interval)
