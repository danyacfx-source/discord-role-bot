import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG
from kick_bot.live import KickLiveNotifier

log = logging.getLogger("kick")


class Kick(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("kick") or {}
        self.live = None

    async def cog_load(self):
        if not self.config.get("enabled", False):
            log.info("Kick-модуль отключён в конфиге (kick.enabled=false)")
            return
        live_cfg = self.config.get("live") or {}
        if live_cfg.get("enabled", True):
            self.live = KickLiveNotifier(live_cfg, self.bot)
            self.live.start(self.bot.loop)
            log.info("Kick: live-уведомления запущены (%s)", self.live.api.slug)
        self.bot.kick_api = self.live.api if self.live else None

    async def cog_unload(self):
        if self.live is not None:
            self.live.stop()

    @app_commands.command(name="kick_status", description="Текущий статус стрима на Kick")
    async def kick_status(self, interaction: discord.Interaction):
        if self.live is None or self.live.api is None:
            await interaction.response.send_message("Kick-модуль не настроен.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        info = await self.live.api.summarize()
        if info is None:
            await interaction.followup.send("Не удалось получить данные Kick.", ephemeral=True)
            return
        emoji = "🔴" if info["online"] else "⚪"
        txt = f"{emoji} **{info['slug']}** — {'в эфире' if info['online'] else 'офлайн'}\n"
        txt += f"📺 **{info.get('title') or info['slug']}**\n"
        if info.get("category"):
            txt += f"🎮 {info['category']}\n"
        txt += f"👥 Зрители: {info.get('viewer_count') or 0}\n"
        txt += info["url"]
        await interaction.followup.send(txt, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Kick(bot))
