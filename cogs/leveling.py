import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    CONFIG,
    EXCLUDE_ROLES,
    WHITELIST_CHANNELS,
)
from db import (
    add_message,
    get_leaderboard,
    get_stats,
    level_for_xp,
    season_add_message,
    total_xp_for,
    xp_in_level,
    xp_to_next_level,
)


class Leveling(commands.Cog):
    XP_COOLDOWN_SECONDS = 30
    MAX_COOLDOWN_ENTRIES = 10000

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._xp_cooldowns: dict[int, float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild is None:
            return
        if WHITELIST_CHANNELS and message.channel.id not in WHITELIST_CHANNELS:
            return
        if EXCLUDE_ROLES and any(r.name in EXCLUDE_ROLES for r in message.author.roles):
            return
        uid = message.author.id
        now = time.time()
        if len(self._xp_cooldowns) > self.MAX_COOLDOWN_ENTRIES:
            stale = [k for k, v in self._xp_cooldowns.items() if now - v > 300]
            for k in stale:
                del self._xp_cooldowns[k]
        last = self._xp_cooldowns.get(uid, 0)
        if now - last < self.XP_COOLDOWN_SECONDS:
            return
        self._xp_cooldowns[uid] = now
        add_message(message.guild.id, message.author.id)
        season_add_message(message.guild.id, message.author.id)

    @app_commands.command(name="level", description="Показать свой текущий уровень и XP")
    @app_commands.guild_only()
    async def level(self, interaction: discord.Interaction):
        stats = get_stats(interaction.guild_id, interaction.user.id)
        points = stats[0] if stats else 0
        xp = stats[1] if stats else 0
        level = level_for_xp(xp)
        next_xp = total_xp_for(level + 1)
        current_xp = xp_in_level(xp, level)
        need = xp_to_next_level(level)
        filled = round((current_xp / need) * 10) if need else 0
        bar = "█" * filled + "░" * (10 - filled)

        embed = discord.Embed(
            title=f"Уровень {interaction.user.display_name}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Уровень", value=f"**{level}**", inline=False)
        embed.add_field(name="XP", value=f"{current_xp} / {need}  `{bar}`", inline=False)
        embed.add_field(name="Сообщений", value=str(points), inline=False)
        if next_xp > xp:
            embed.add_field(
                name="До следующего уровня",
                value=f"ещё **{next_xp - xp}** XP",
                inline=False,
            )
        else:
            embed.add_field(name="Максимальный уровень", value="Молодец!", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="top", description="Топ пользователей по активности")
    @app_commands.guild_only()
    async def top(self, interaction: discord.Interaction):
        rows = get_leaderboard(interaction.guild_id, 10)
        if not rows:
            await interaction.response.send_message("Пока нет данных об активности.")
            return
        lines = []
        for pos, (user_id, points) in enumerate(rows, start=1):
            member = interaction.guild.get_member(user_id)
            name = member.display_name if member else f"Пользователь {user_id}"
            lines.append(f"**{pos}.** {name} — {points} сообщений")
        embed = discord.Embed(
            title="🏆 Топ активности",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))