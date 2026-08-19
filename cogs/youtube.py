import asyncio
import logging

from discord.ext import commands

from config import CONFIG
from twitch_bot.bridge import TwitchDiscordBridge
from twitch_bot.youtube_chat import YouTubeChatClient

log = logging.getLogger("youtube")


class YouTube(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("youtube") or {}
        self.client = None
        self.bridge = None
        self._task = None

    async def cog_load(self):
        if not self.config.get("enabled", False):
            log.info("YouTube-модуль отключён (youtube.enabled=false)")
            return

        self.bridge = TwitchDiscordBridge(self.config, self.bot)
        self.client = YouTubeChatClient(self.config, self.bot.loop, self.bridge)
        self._task = self.bot.loop.create_task(self._run_youtube())
        log.info("YouTube-модуль запущен: @%s", self.config.get("channel"))

    async def cog_unload(self):
        if self.client:
            await self.client.stop()
        if self._task:
            self._task.cancel()

    async def _run_youtube(self):
        try:
            await self.client.start()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("YouTube-клиент завершился с ошибкой")


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTube(bot))
