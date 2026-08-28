import asyncio
import logging

import discord
from discord.ext import commands

from config import CONFIG

log = logging.getLogger("server_stats")


class ServerStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("server_stats") or {}
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
        log.info("ServerStats: счётчики сервера запущены")

    async def _loop(self):
        interval = max(60, self.config.get("update_seconds", 300))
        while True:
            try:
                await self._update()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("ServerStats: ошибка обновления счётчиков")
            await asyncio.sleep(interval)

    async def _update(self):
        for guild in self.bot.guilds:
            try:
                await self._update_guild(guild)
            except discord.HTTPException:
                log.warning("ServerStats: не удалось обновить счётчики сервера %s", guild.name)

    async def _update_guild(self, guild: discord.Guild):
        channels_cfg = self.config.get("channels") or []
        if not channels_cfg:
            return
        category_name = self.config.get("category", "Статистика")
        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            try:
                category = await guild.create_category(category_name, reason="Счётчики сервера")
            except discord.Forbidden:
                log.warning("ServerStats: нет прав создать категорию «%s» в %s", category_name, guild.name)
                return

        members = sum(1 for m in guild.members if not m.bot)
        online = sum(1 for m in guild.members if not m.bot and m.status is not discord.Status.offline)

        for spec in channels_cfg:
            kind = spec.get("type", "members")
            value = online if kind == "online" else members
            emoji = spec.get("emoji", "")
            name = f"{emoji} {value}"[:100] if emoji else str(value)[:100]
            if emoji:
                channel = discord.utils.find(
                    lambda c: c.name.startswith(emoji), category.voice_channels
                )
            else:
                channel = discord.utils.find(
                    lambda c: c.name == name, category.voice_channels
                )
            if channel is None:
                try:
                    await category.create_voice_channel(name, reason="Счётчик сервера")
                except discord.Forbidden:
                    log.warning("ServerStats: нет прав создать канал счётчика в %s", guild.name)
                continue
            if channel.name != name:
                try:
                    await channel.edit(name=name, reason="Обновление счётчика сервера")
                except discord.Forbidden:
                    log.warning("ServerStats: нет прав переименовать канал в %s", guild.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerStats(bot))