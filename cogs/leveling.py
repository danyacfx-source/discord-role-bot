import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    BOT_NAME,
    CONFIG,
    EXCLUDE_ROLES,
    LEVELS,
    WHITELIST_CHANNELS,
)
from db import (
    add_message,
    get_leaderboard,
    get_stats,
    level_for_xp,
    level_index_for,
    season_add_message,
    total_xp_for,
    xp_in_level,
    xp_to_next_level,
)
from utils import role_for_level


class Leveling(commands.Cog):
    XP_COOLDOWN_SECONDS = 30
    MAX_COOLDOWN_ENTRIES = 10000

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._xp_cooldowns: dict[int, float] = {}

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        role = role_for_level(member.guild, 0)
        if role is not None and role not in member.roles:
            try:
                await member.add_roles(role, reason="Выдача Newcomer при входе на сервер")
                logging.info("Newcomer выдан: %s (%s)", member, member.id)
            except discord.Forbidden:
                logging.error("Нет прав выдать Newcomer для %s", member)

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
        points, _xp = add_message(message.guild.id, message.author.id)
        await self.update_roles(message.author, points, message.channel)
        season_add_message(message.guild.id, message.author.id)

    async def _announce_level_up(self, member: discord.Member, idx: int, points: int):
        channel_id = CONFIG.get("level_up_channel_id", 0)
        if not channel_id:
            return
        ignore_roles = set(CONFIG.get("level_up_ignore_roles") or [])
        if any(r.name in ignore_roles for r in member.roles):
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        role = role_for_level(member.guild, idx)
        embed = discord.Embed(
            description=(
                f"{member.mention} добрался до уровня **{LEVELS[idx]['role_name']}**!\n"
                f"📊 Сообщений: **{points}**"
            ),
            color=role.color if role else discord.Color.blue(),
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_footer(text=BOT_NAME)
        try:
            await channel.send(embed=embed)
            logging.info("LevelUp: анонс уровня %s для %s", LEVELS[idx]["role_name"], member)
        except discord.HTTPException:
            logging.warning("LevelUp: не удалось отправить анонс для %s", member)

    async def _safe_remove(self, member: discord.Member, role: discord.Role):
        try:
            await member.remove_roles(role, reason="Обновление уровня активности")
        except discord.Forbidden:
            pass

    async def update_roles(
        self,
        member: discord.Member,
        points: int,
        channel: discord.abc.Messageable | None = None,
    ):
        if member.bot:
            return
        idx = level_index_for(points)
        current = (
            discord.utils.get(member.roles, name=LEVELS[idx]["role_name"])
            if idx >= 0
            else None
        )
        if current is not None:
            return

        if idx < 0:
            for i, lvl in enumerate(LEVELS):
                r = role_for_level(member.guild, i)
                if r is not None and r in member.roles:
                    await self._safe_remove(member, r)
            return

        target_role = role_for_level(member.guild, idx)
        if target_role is None:
            logging.error(
                "Роль '%s' не найдена на сервере %s",
                LEVELS[idx]["role_name"],
                member.guild.name,
            )
            return
        old_roles = [
            role_for_level(member.guild, i)
            for i, lvl in enumerate(LEVELS)
            if role_for_level(member.guild, i) is not None
            and role_for_level(member.guild, i) in member.roles
        ]
        for r in old_roles:
            await self._safe_remove(member, r)
        try:
            await member.add_roles(target_role, reason="Достигнут уровень активности")
        except (discord.Forbidden, discord.HTTPException):
            logging.error(
                "Нет прав на выдачу роли '%s' пользователю %s",
                LEVELS[idx]["role_name"],
                member,
            )
            for r in old_roles:
                try:
                    await member.add_roles(r, reason="Откат уровня (нет прав на новую роль)")
                except discord.Forbidden:
                    pass
            return
        for i, lvl in enumerate(LEVELS):
            r = role_for_level(member.guild, i)
            if r is not None and r in member.roles and r != target_role:
                await self._safe_remove(member, r)
        try:
            await member.send(
                f"**{BOT_NAME}**: {member.mention} собрал {points} сообщений "
                f"и получил роль **{LEVELS[idx]['role_name']}**!"
            )
        except discord.Forbidden:
            pass
        await self._announce_level_up(member, idx, points)

    @app_commands.command(name="level", description="Показать свой текущий уровень и XP")
    @app_commands.guild_only()
    async def level(self, interaction: discord.Interaction):
        stats = get_stats(interaction.guild_id, interaction.user.id)
        points = stats[0] if stats else 0
        xp = stats[1] if stats else 0
        idx = level_index_for(points)
        current_role = LEVELS[idx]["role_name"] if idx >= 0 else "нет роли"
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
        embed.add_field(name="Роль", value=current_role, inline=False)
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
