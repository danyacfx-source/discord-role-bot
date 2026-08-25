import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, GUILD_ID

log = logging.getLogger("permissions")


class Permissions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        cfg = CONFIG.get("permissions") or {}
        self.categories = cfg.get("categories") or {}
        self.auto_apply = cfg.get("auto_apply", True)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.auto_apply or not self.categories or not GUILD_ID:
            return
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            log.warning("Permissions: гильдия %s не найдена", GUILD_ID)
            return
        lines = await self.apply_all(guild)
        for line in lines:
            log.info("Права категорий %s: %s", guild.name, line)

    def _resolve_role(self, guild: discord.Guild, name: str) -> discord.Role | None:
        if name == "@everyone":
            return guild.default_role
        return discord.utils.get(guild.roles, name=name)

    async def apply_category(self, guild: discord.Guild, cat_id, spec) -> str:
        try:
            category = guild.get_channel(int(cat_id))
        except (ValueError, TypeError):
            return f"❌ Ключ категории «{cat_id}» не является числом"
        if category is None or not isinstance(category, discord.CategoryChannel):
            return f"❌ Категория {cat_id} не найдена"
        try:
            for rule in spec.get("rules", []):
                role = self._resolve_role(guild, rule["role"])
                if role is None:
                    return f"❌ {category.name}: роль «{rule['role']}» не найдена"
                kwargs = {k: bool(v) for k, v in rule.items() if k != "role" and v is not None}
                await category.set_permissions(
                    role, reason="Права категорий из конфига", **kwargs
                )
            return f"✅ {category.name}"
        except discord.Forbidden:
            return f"⛔ {category.name}: у бота нет прав"
        except Exception as e:
            return f"❌ {category.name}: {e}"

    async def apply_all(self, guild: discord.Guild) -> list[str]:
        lines = []
        for cat_id, spec in self.categories.items():
            lines.append(await self.apply_category(guild, cat_id, spec))
        return lines

    @app_commands.command(name="apply_permissions", description="Применить права категорий из конфига (для админов)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def apply_permissions(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lines = await self.apply_all(interaction.guild)
        await interaction.followup.send(
            f"**Права категорий:**\n" + "\n".join(lines), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Permissions(bot))
