import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config import SEASON
from db import get_season_leaderboard, season_reset

log = logging.getLogger("season")

MEDALS = ["🥇", "🥈", "🥉"]


class Season(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _resolve_member(self, guild, user_id):
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            return None

    @app_commands.command(name="season_top", description="Топ активности за текущий сезон")
    @app_commands.guild_only()
    async def season_top(self, interaction: discord.Interaction):
        rows = get_season_leaderboard(interaction.guild_id, 10)
        if not rows:
            await interaction.response.send_message(
                "Сезон только начался — данных пока нет."
            )
            return
        await interaction.response.defer()
        lines = []
        for pos, (user_id, points) in enumerate(rows, start=1):
            member = await self._resolve_member(interaction.guild, user_id)
            name = member.display_name if member else f"Пользователь {user_id}"
            medal = MEDALS[pos - 1] if pos <= 3 else f"**{pos}.**"
            lines.append(f"{medal} {name} — {points} сообщений")
        embed = discord.Embed(
            title="🏆 Сезонный топ",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="season_end", description="Подвести итоги сезона: наградить топ-3 и сбросить счётчики")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def season_end(self, interaction: discord.Interaction):
        if not SEASON.get("enabled", False):
            await interaction.response.send_message(
                "Сезонный модуль отключён в конфиге.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        reward_names = SEASON.get("reward_roles", [])
        if len(reward_names) < 3:
            await interaction.followup.send(
                "В конфиге меньше 3 ролей наград.", ephemeral=True
            )
            return
        rows = get_season_leaderboard(guild.id, 3)
        if not rows:
            await interaction.followup.send(
                "Нет данных за сезон — награждать некого.", ephemeral=True
            )
            return

        awarded = []
        for pos, (user_id, points) in enumerate(rows, start=1):
            member = await self._resolve_member(guild, user_id)
            role = discord.utils.get(guild.roles, name=reward_names[pos - 1])
            if member is None or role is None:
                awarded.append(f"{MEDALS[pos-1]} {user_id}: пропущен (нет участника/роли)")
                continue
            for old in reward_names:
                old_role = discord.utils.get(guild.roles, name=old)
                if old_role is not None and old_role in member.roles:
                    try:
                        await member.remove_roles(
                            old_role, reason="Награды нового сезона"
                        )
                    except discord.Forbidden:
                        pass
            try:
                await member.add_roles(role, reason="Награда за сезон")
                awarded.append(
                    f"{MEDALS[pos-1]} **{member.display_name}** + {role.mention} ({points} сообщений)"
                )
            except discord.Forbidden:
                awarded.append(f"{MEDALS[pos-1]} {member.display_name}: нет прав на выдачу")

        season_reset(guild.id)

        summary = "\n".join(awarded)
        announce_id = SEASON.get("announce_channel_id", 0)
        if announce_id:
            channel = guild.get_channel(announce_id)
            if channel is not None:
                embed = discord.Embed(
                    title="🏆 Итоги сезона!",
                    description=summary,
                    color=discord.Color.gold(),
                )
                embed.set_footer(
                    text=f"{datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} UTC"
                )
                try:
                    await channel.send(embed=embed)
                except Exception:
                    log.exception("Ошибка анонса итогов сезона")
        await interaction.followup.send(f"**Итоги сезона:**\n{summary}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Season(bot))
