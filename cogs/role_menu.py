import logging

import discord
from discord.ext import commands

from config import CONFIG

log = logging.getLogger("role_menu")

PANEL_FOOTER = "Роли через меню выбора"


class RoleMenuView(discord.ui.View):
    def __init__(self, role_names: list[str], max_values: int):
        super().__init__(timeout=None)
        self.role_names = role_names
        options = [
            discord.SelectOption(label=name, value=name, description=f"Роль «{name}»")
            for name in role_names
        ]
        max_v = min(max(1, max_values), len(role_names)) if role_names else 1

        add = discord.ui.Select(
            custom_id="role_menu_add",
            placeholder="Получить роль…",
            min_values=0,
            max_values=max_v,
            options=options,
        )
        add.callback = self.on_add
        self.add_item(add)

        remove = discord.ui.Select(
            custom_id="role_menu_remove",
            placeholder="Снять роль…",
            min_values=0,
            max_values=max_v,
            options=options,
        )
        remove.callback = self.on_remove
        self.add_item(remove)

    async def _apply(self, interaction: discord.Interaction, add: bool):
        selected = set(interaction.data.get("values") or [])
        guild = interaction.guild
        if guild is None:
            return
        member = interaction.user
        changes = 0
        for name in selected:
            role = discord.utils.get(guild.roles, name=name)
            if role is None:
                continue
            try:
                if add and role not in member.roles:
                    await member.add_roles(role, reason="Выбор роли в меню")
                    changes += 1
                elif not add and role in member.roles:
                    await member.remove_roles(role, reason="Снятие роли в меню")
                    changes += 1
            except discord.Forbidden:
                log.warning("RoleMenu: нет прав изменить роль %s у %s", role.name, member)
        verb = "добавлены" if add else "сняты"
        await interaction.response.send_message(
            f"Роли {verb} ({changes} изменений).", ephemeral=True
        )

    async def on_add(self, interaction: discord.Interaction):
        await self._apply(interaction, add=True)

    async def on_remove(self, interaction: discord.Interaction):
        await self._apply(interaction, add=False)


class RoleMenu(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("role_menu") or {}
        self._started = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        if not self.config.get("enabled", True):
            return
        self.bot.loop.create_task(self._ensure_panel())

    async def _ensure_panel(self):
        channel = self.bot.get_channel(self.config.get("channel_id", 0))
        if channel is None:
            return
        role_names = self.config.get("roles") or []
        if not role_names:
            return
        existing = [r for r in role_names if discord.utils.get(channel.guild.roles, name=r)]
        role_names = existing or role_names

        desc = "Первое меню — получить роль, второе — снять.\n\n" + "\n".join(f"• **{name}**" for name in role_names)
        embed = discord.Embed(
            title=self.config.get("message", "Выбери уведомления"),
            description=desc,
            color=discord.Color.purple(),
        )
        embed.set_footer(text=PANEL_FOOTER)
        view = RoleMenuView(
            role_names,
            self.config.get("max_values", 10),
        )
        self.bot.add_view(view)

        async for old in channel.history(limit=30):
            if old.author.id != self.bot.user.id or not old.embeds:
                continue
            footer = (old.embeds[0].footer.text or "").strip()
            if footer != PANEL_FOOTER:
                continue
            try:
                await old.edit(embed=embed, view=view)
            except discord.HTTPException:
                pass
            log.info("RoleMenu: панель обновлена в #%s", channel.name)
            return
        try:
            await channel.send(embed=embed, view=view)
            log.info("RoleMenu: панель создана в #%s", channel.name)
        except discord.HTTPException:
            log.warning("RoleMenu: не удалось создать панель в #%s", channel.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleMenu(bot))