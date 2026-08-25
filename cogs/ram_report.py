import logging

import discord
from discord.ext import commands, tasks

from config import CONFIG

log = logging.getLogger("ram_report")


class RamReport(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        cfg = CONFIG.get("ram_report") or {}
        self.enabled = cfg.get("enabled", False)
        self.channel_id = cfg.get("channel_id", 0)
        self.interval_minutes = cfg.get("interval_minutes", 30)
        self.ram_report_loop.change_interval(minutes=self.interval_minutes)

    def _rss_mb(self) -> float:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)

    def _peak_mb(self) -> float:
        import psutil

        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", None)
        if peak is not None:
            return peak / (1024 * 1024)
        return info.rss / (1024 * 1024)

    @tasks.loop(minutes=30)
    async def ram_report_loop(self):
        if self.channel_id <= 0:
            return
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            log.warning("Канал %s для отчёта по ОЗУ не найден", self.channel_id)
            return
        embed = discord.Embed(
            title="📊 Память бота",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Текущее потребление", value=f"{self._rss_mb():.1f} МБ", inline=False)
        embed.add_field(name="Пик", value=f"{self._peak_mb():.1f} МБ", inline=False)
        try:
            await channel.send(embed=embed)
        except Exception:
            log.exception("Ошибка отправки отчёта по ОЗУ")

    @ram_report_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.enabled and not self.ram_report_loop.is_running():
            self.ram_report_loop.start()
            log.info("Отчёт по ОЗУ: каждые %s мин в канал %s", self.interval_minutes, self.channel_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(RamReport(bot))
