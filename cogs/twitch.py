import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG
from twitch_bot.bridge import TwitchDiscordBridge
from twitch_bot.client import TwitchChatClient
from twitch_bot.live import StreamLiveNotifier

log = logging.getLogger("twitch")


class Twitch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("twitch") or {}
        self.client = None
        self.bridge = None
        self._task = None
        self.live = None
        self._status_task = None

    async def cog_load(self):
        live_cfg = self.config.get("live") or {}
        if live_cfg.get("enabled", False):
            self.live = StreamLiveNotifier(live_cfg, self.bot)
            self.live.start(self.bot.loop)
            log.info("Twitch: уведомления о старте стрима запущены")
        if (live_cfg.get("status_viewers", False)) and self.live is not None:
            self._status_task = self.bot.loop.create_task(self._status_loop())
            log.info("Twitch: обновление зрителей в статусе запущено")
        if not self.config.get("enabled", False):
            log.info("Twitch-модуль отключён в конфиге (twitch.enabled=false)")
            return
        self.bridge = TwitchDiscordBridge(self.config, self.bot)
        self.client = TwitchChatClient(self.config, self.bot.loop, self.bridge)
        self.bridge.client = self.client
        self.bot.twitch_client = self.client
        self._task = self.bot.loop.create_task(self._run_twitch())
        log.info("Twitch-модуль запускается в канал #%s", self.config.get("channel"))

    async def cog_unload(self):
        if self.live is not None:
            self.live.stop()
        if self._status_task is not None:
            self._status_task.cancel()
        if self._task is not None:
            self._task.cancel()
        if self.client is not None:
            try:
                await self.client.close()
            except Exception:
                pass

    async def _status_loop(self):
        status_cfg = self.live.config
        interval = max(30, status_cfg.get("status_poll_seconds", 300))
        while not self.bot.is_ready():
            await asyncio.sleep(2)
        while True:
            try:
                viewers = await self.live.get_viewer_count()
                if viewers is not None:
                    text = f"Смотрим стрим: {viewers} зрителей"
                    activity = discord.Activity(
                        type=discord.ActivityType.watching, name=text
                    )
                    await self.bot.change_presence(activity=activity)
                else:
                    next_start = await self.live.get_next_schedule_start()
                    if next_start is None:
                        activity = discord.Activity(
                            type=discord.ActivityType.watching,
                            name="Ждём эфир",
                        )
                        await self.bot.change_presence(activity=activity)
                    else:
                        text = self._schedule_text(next_start)
                        activity = discord.Activity(
                            type=discord.ActivityType.watching, name=text
                        )
                        await self.bot.change_presence(activity=activity)
            except Exception:
                log.exception("Twitch: ошибка обновления статуса зрителей")
            await asyncio.sleep(interval)

    @staticmethod
    def _schedule_text(start_dt) -> str:
        now = datetime.now(timezone.utc)
        delta = start_dt - now
        seconds = int(delta.total_seconds())
        if seconds <= 0:
            return "Эфир скоро!"
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days > 0:
            return f"Эфир через {days} д {hours} ч"
        if hours > 0:
            return f"Эфир через {hours} ч {minutes} мин"
        return f"Эфир через {max(1, minutes)} мин"

    async def _run_twitch(self):
        try:
            await self.client.start()
        except Exception:
            log.exception("Twitch-клиент завершился с ошибкой")

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message):
        if self.bridge is None:
            return
        await self.bridge.on_discord_message(message)

    @app_commands.command(name="stream", description="Статус стрима Twitch: зрители, игра, расписание")
    @app_commands.guild_only()
    async def stream(self, interaction: discord.Interaction):
        live = self.config.get("live") or {}
        if not live.get("enabled", False) or self.live is None:
            await interaction.response.send_message(
                "Модуль статуса стрима отключён в конфиге.", ephemeral=True
            )
            return
        await interaction.response.defer()
        embed = discord.Embed(
            title="📺 Статус стрима",
            color=0x9146FF,
        )
        try:
            viewers = await self.live.get_viewer_count()
            stream = await self.live.get_live_stream_info()
        except Exception:
            log.exception("Twitch: ошибка запроса статуса стрима")
            stream = None
            viewers = None
        if viewers is not None:
            title = stream.get("title") or "Стрим идёт"
            game = stream.get("game_name") or ""
            embed.description = f"**{title}**"
            embed.add_field(name="Статус", value="🔴 В эфире", inline=True)
            embed.add_field(name="Зрители", value=str(viewers), inline=True)
            if game:
                embed.add_field(name="Игра", value=game, inline=True)
            thumb = (stream.get("thumbnail_url") or "").replace("{width}", "1280").replace("{height}", "720")
            if thumb:
                embed.set_image(url=thumb)
            embed.add_field(
                name="Ссылка",
                value=f"https://www.twitch.tv/{live.get('channel')}",
                inline=False,
            )
        else:
            embed.description = "Сейчас эфира нет."
            embed.add_field(name="Статус", value="⚪ Офлайн", inline=True)
            next_start = await self.live.get_next_schedule_start()
            if next_start is None:
                embed.add_field(name="Ближайший эфир", value="Не запланирован", inline=True)
            else:
                embed.add_field(
                    name="Ближайший эфир",
                    value=self._schedule_text(next_start),
                    inline=True,
                )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Twitch(bot))
