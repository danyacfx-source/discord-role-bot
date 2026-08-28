import asyncio
import calendar
import logging
import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, GUILD_ID
from db import birthdays_all, birthday_get, birthday_remove, birthday_set

log = logging.getLogger("birthday")

DATE_RE = re.compile(r"^\s*(\d{1,2})[./\\-](\d{1,2})\s*$")


class Birthdays(commands.Cog):
    birthday = app_commands.Group(name="birthday", description="Дни рождения участников")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("birthday") or {}
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
        log.info("Birthday: ежедневный анонс запущен")

    async def _loop(self):
        hour = max(0, min(23, self.config.get("announce_hour", 9)))
        while True:
            now = datetime.now()
            target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            try:
                await self._announce()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Birthday: ошибка анонса")

    async def _announce(self):
        channel_id = self.config.get("channel_id", 0)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        guild = self.bot.get_guild(GUILD_ID) if GUILD_ID else None
        now = datetime.now()
        rows = birthdays_all()
        hits = [(uid, m, d) for uid, m, d in rows if m == now.month and d == now.day]
        if not hits:
            return
        lines = []
        for uid, _m, _d in hits:
            display = None
            if guild is not None:
                member = guild.get_member(uid)
                if member is not None:
                    display = member.display_name
            mention = f"<@{uid}>"
            name = display or mention
            lines.append(f"🎂 **{name}** {mention}")
        embed = discord.Embed(
            title="🎉 Сегодня день рождения!",
            description="\n".join(lines),
            color=discord.Color.magenta(),
        )
        role_ping = self.config.get("role_ping") or ""
        content = ""
        if guild is not None and role_ping:
            role = discord.utils.get(guild.roles, name=role_ping)
            if role is not None:
                content = role.mention
        try:
            await channel.send(content=content or None, embed=embed)
            log.info("Birthday: анонс отправлен (%s участников)", len(lines))
        except discord.HTTPException:
            log.warning("Birthday: не удалось отправить анонс")

    @birthday.command(name="set", description="Указать свою дату рождения (дд.мм)")
    @app_commands.describe(date="Дата в формате дд.мм")
    async def set_birthday(self, interaction: discord.Interaction, date: str):
        m = DATE_RE.match(date)
        if m is None:
            await interaction.response.send_message(
                "Неверный формат. Используй **дд.мм** (например `15.03`).", ephemeral=True
            )
            return
        day, month = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12 and 1 <= day <= calendar.monthrange(2000, month)[1]):
            await interaction.response.send_message("Такая дата не существует.", ephemeral=True)
            return
        birthday_set(interaction.user.id, month, day)
        await interaction.response.send_message(
            f"Дата сохранена: **{day:02d}.{month:02d}**\n"
            "В этот день бот поздравит тебя на сервере! 🎉",
            ephemeral=True,
        )

    @birthday.command(name="remove", description="Удалить свою дату рождения")
    async def remove_birthday(self, interaction: discord.Interaction):
        current = birthday_get(interaction.user.id)
        if current is None:
            await interaction.response.send_message("Дата не установлена.", ephemeral=True)
            return
        birthday_remove(interaction.user.id)
        await interaction.response.send_message("Дата удалена.", ephemeral=True)

    @birthday.command(name="list", description="Ближайшие дни рождения")
    async def list_birthdays(self, interaction: discord.Interaction):
        rows = birthdays_all()
        if not rows:
            await interaction.response.send_message("Пока никто не указал дату.", ephemeral=True)
            return
        now = datetime.now()
        upcoming = []
        for uid, month, day in rows:
            try:
                birthday_this_year = datetime(now.year, month, day)
            except ValueError:
                birthday_this_year = datetime(now.year, month, 28)
            delta = (birthday_this_year - now).days
            if delta < 0:
                try:
                    next_year = datetime(now.year + 1, month, day)
                except ValueError:
                    next_year = datetime(now.year + 1, month, 28)
                delta = (next_year - now).days
            upcoming.append((delta, uid, month, day))
        upcoming.sort(key=lambda x: x[0])
        guild = interaction.guild
        lines = []
        for delta, uid, month, day in upcoming:
            if delta >= 365:
                continue
            member = guild.get_member(uid)
            name = member.display_name if member else f"Пользователь {uid}"
            when = "Сегодня! 🎉" if delta == 0 else ("Завтра" if delta == 1 else f"через {delta} дн.")
            lines.append(f"**{name}** — {day:02d}.{month:02d} ({when})")
        if not lines:
            await interaction.response.send_message("Ближайших дней рождения нет.", ephemeral=True)
            return
        embed = discord.Embed(
            title="🎂 Ближайшие дни рождения",
            description="\n".join(lines[:25]),
            color=discord.Color.magenta(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Birthdays(bot))