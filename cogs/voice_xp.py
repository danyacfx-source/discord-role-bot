import asyncio
import logging

import discord
from discord.ext import commands

from config import CONFIG
from db import add_message, season_add_message, level_index_for
from utils import role_for_level

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

    async def _ensure_level_role(self, member: discord.Member, points: int):
        """Выдать роль текущего уровня по голосовой активности.

        Раньше модуль обращался к несуществующему методу leveling.update_roles и
        падал при каждой попытке. Теперь роль уровня начисляется напрямую по
        количеству очков (уровни задаются в config.json).
        """
        try:
            level_idx = level_index_for(points)
            if level_idx < 0:
                return
            target = role_for_level(member.guild, level_idx)
            if target is None:
                return
            if target in member.roles:
                return
            for idx in range(level_idx):
                lower = role_for_level(member.guild, idx)
                if lower is not None and lower in member.roles and lower != target:
                    await member.remove_roles(lower, reason="VoiceXP: повышение уровня")
            await member.add_roles(target, reason="VoiceXP: начисление за голос")
        except discord.Forbidden:
            log.warning("VoiceXP: нет прав на выдачу роли %s", getattr(member, "name", member.id))
        except Exception:
            log.exception("VoiceXP: ошибка обновления роли уровня %s", getattr(member, "name", member.id))

    async def _award(self):
        skip_muted = self.config.get("skip_muted", False)
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if member.bot:
                        continue
                    if skip_muted:
                        voice = member.voice
                        if voice is not None and (voice.self_mute or voice.self_deaf):
                            continue
                    points, _xp = add_message(guild.id, member.id)
                    season_add_message(guild.id, member.id)
                    await self._ensure_level_role(member, points)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceXP(bot))