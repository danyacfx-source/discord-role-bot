import asyncio
import logging

import discord
from discord.ext import commands

from config import CONFIG
from db import add_message, season_add_message

log = logging.getLogger("voice_xp")


class VoiceXP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("voice_xp") or {}
        self._task = None
        self._started = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        if not self.config.get("enabled", True):
            return
        self._task = self.bot.loop.create_task(self._loop())
        log.info("VoiceXP: награды за голосовые запущены")

    async def _loop(self):
        interval = max(3, min(60, self.config.get("interval_minutes", 5))) * 60
        while True:
            try:
                await self._award()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("VoiceXP: ошибка начисления очков")
            await asyncio.sleep(interval)

    async def _award(self):
        skip_muted = self.config.get("skip_muted", False)
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if member.bot:
                        continue
                    if skip_muted:
                        voice = member.voice
                        if member.mute or member.deafen or (
                            voice is not None and (voice.self_mute or voice.self_deaf)
                        ):
                            continue
                    points, _xp = add_message(guild.id, member.id)
                    season_add_message(guild.id, member.id)
                    leveling = self.bot.get_cog("Leveling")
                    if leveling is not None:
                        try:
                            await leveling.update_roles(member, points, None)
                        except Exception:
                            log.exception("VoiceXP: не удалось обновить роль %s", member)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceXP(bot))