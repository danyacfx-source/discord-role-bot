import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import CHANNELS, EXTRA_ROLES, GUILD_ID, LEVELS, ROLE_SETTINGS
from utils import _norm, find_channel

log = logging.getLogger("setup")


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not ROLE_SETTINGS or not GUILD_ID:
            return
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            log.warning("Setup: гильдия %s не найдена", GUILD_ID)
            return
        bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            return
        try:
            reports = await self._apply_role_settings(guild, bot_member.top_role, guild.roles)
            for line in reports:
                if "❌" in line or "⛔" in line:
                    log.info("Роли %s: %s", guild.name, line)
        except Exception as e:
            log.info("Роли %s: ошибка автонастройки: %s", guild.name, e)

    @staticmethod
    def _parse_color(value):
        if isinstance(value, (int, float)):
            return discord.Color(int(value))
        return discord.Color.from_str(str(value))

    async def _apply_role_settings(self, guild, bot_top, roles):
        reports = []
        ordered = sorted(ROLE_SETTINGS.items(), key=lambda kv: kv[1].get("order", 999))
        position = bot_top.position - 1
        for name, spec in ordered:
            role = discord.utils.get(roles, name=name)
            if role is None:
                reports.append(f"⚠️ **{name}** — не создана")
                continue
            kwargs = {}
            if spec.get("color"):
                try:
                    kwargs["color"] = self._parse_color(spec["color"])
                except ValueError:
                    reports.append(f"❌ **{name}** — неверный цвет")
                    continue
            perms = spec.get("permissions")
            if perms is not None:
                base = discord.Permissions(role.permissions.value)
                # Дефект D22: раньше в конфиге права только включались, а лишние
                # никогда не отзывались. Поддерживаем явный отзыв (False/0) и
                # считаем "permissions" точной маской для перечисленных флагов.
                for p in perms:
                    if not isinstance(p, dict):
                        if hasattr(base, p):
                            setattr(base, p, True)
                        continue
                    name, enabled = p.get("name"), p.get("allowed", True)
                    if name and hasattr(base, name):
                        setattr(base, name, bool(enabled))
                kwargs["permissions"] = base
            # Валидация позиции: роль не может быть выше роли бота или ниже everyone.
            if position >= bot_top.position:
                reports.append(f"❌ **{name}** — позиция выше/равна роли бота, пропуск")
                continue
            if position > 1:
                kwargs["position"] = position
            if spec.get("hoist"):
                kwargs["hoist"] = bool(spec.get("hoist"))
            if spec.get("mentionable") is not None:
                kwargs["mentionable"] = bool(spec.get("mentionable"))
            try:
                await role.edit(reason="Настройка ролей из конфига", **kwargs)
                reports.append(f"✅ **{name}** — цвет и позиция {position}")
            except discord.Forbidden:
                reports.append(f"⛔ **{name}** — нет прав на изменение")
            except discord.HTTPException as e:
                reports.append(f"❌ **{name}** — {e}")
            position -= 1
        return reports

    @app_commands.command(name="setup_roles", description="Создать все роли из конфига и настроить их (для админов)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def setup_roles(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            await interaction.followup.send("Бот не найден на сервере.", ephemeral=True)
            return
        bot_top = bot_member.top_role

        all_roles = [lvl["role_name"] for lvl in LEVELS] + EXTRA_ROLES
        created = []
        existing = []
        for name in all_roles:
            role = discord.utils.get(guild.roles, name=name)
            if role is not None:
                existing.append(name)
                continue
            role = await guild.create_role(name=name, reason="Настройка ролей ботом")
            created.append(name)

        reports = await self._apply_role_settings(guild, bot_top, guild.roles)
        report_lines = "\n".join(reports) if reports else "нет настроек в конфиге"

        await interaction.followup.send(
            f"**Создано ролей:** {', '.join(created) if created else 'нет (все уже есть)'}\n"
            f"**Уже существовали:** {', '.join(existing) if existing else 'нет'}\n\n"
            f"**Настройки:**\n{report_lines}",
            ephemeral=True,
        )

    @app_commands.command(name="apply_role_settings", description="Применить цвет, порядок и права ролей из конфига (для админов)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def apply_role_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            await interaction.followup.send("Бот не найден на сервере.", ephemeral=True)
            return
        bot_top = bot_member.top_role
        reports = await self._apply_role_settings(guild, bot_top, guild.roles)
        await interaction.followup.send(
            "**Применены настройки ролей:**\n"
            + ("\n".join(reports) if reports else "нет настроек в конфиге"),
            ephemeral=True,
        )

    @app_commands.command(name="setup_channels", description="Создать категории и каналы из конфига (для админов)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def setup_channels(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        created = []
        existing = []
        for cat_name, spec in CHANNELS.items():
            category = discord.utils.get(guild.categories, name=cat_name)
            created_cat = False
            if category is None:
                category = await guild.create_category(cat_name, reason="Настройка каналов ботом")
                created_cat = True

            sponsor_roles = []
            if spec.get("type") == "sponsor":
                for rname in spec.get("roles", []):
                    role = discord.utils.get(guild.roles, name=rname)
                    if role is not None:
                        sponsor_roles.append(role)

            cat_type = spec.get("type")
            if cat_type == "temp":
                create_name = spec.get("create", "➕ Создать канал")
                ch = find_channel(category.voice_channels, create_name)
                if ch is not None:
                    existing.append(f"🔊 {create_name}")
                else:
                    ch = await category.create_voice_channel(
                        create_name, reason="Настройка каналов ботом"
                    )
                    created.append(f"🔊 {create_name}")
                continue
            if cat_type == "voice":
                text_names = []
                voice_names = spec.get("channels", []) + spec.get("voice_channels", [])
            elif cat_type == "sponsor":
                text_names = spec.get("text_channels", [])
                voice_names = spec.get("voice_channels", [])
            else:
                text_names = spec.get("channels", [])
                voice_names = spec.get("voice_channels", [])

            wanted_text = {_norm(n) for n in text_names}
            wanted_voice = {_norm(n) for n in voice_names}

            # Не удаляем каналы автоматически: совпадение нормализованных имён
            # (нижний регистр с дефисами) может безвозвратно уничтожить канал
            # вместе с историей переписки. Только диагностируем возможный конфликт.
            for ch in list(category.text_channels):
                if _norm(ch.name) in wanted_voice:
                    log.warning(
                        "Setup: текстовый канал #%s совпадает по нормализованному имени "
                        "с ожидаемым войс-каналом — пропущен, удаление отключено",
                        ch.name,
                    )
            for ch in list(category.voice_channels):
                if _norm(ch.name) in wanted_text:
                    log.warning(
                        "Setup: войс-канал 🔊%s совпадает по нормализованному имени "
                        "с ожидаемым текстовым каналом — пропущен, удаление отключено",
                        ch.name,
                    )

            for name in text_names:
                ch = find_channel(category.text_channels, name)
                if ch is not None:
                    existing.append(f"#{name}")
                    continue
                ch = await category.create_text_channel(name, reason="Настройка каналов ботом")
                if sponsor_roles:
                    await ch.set_permissions(guild.default_role, view_channel=False)
                    for role in sponsor_roles:
                        await ch.set_permissions(role, view_channel=True)
                created.append(f"#{name}")

            for name in voice_names:
                ch = find_channel(category.voice_channels, name)
                if ch is not None:
                    existing.append(f"🔊 {name}")
                    continue
                ch = await category.create_voice_channel(name, reason="Настройка каналов ботом")
                if sponsor_roles:
                    await ch.set_permissions(guild.default_role, view_channel=False)
                    for role in sponsor_roles:
                        await ch.set_permissions(role, view_channel=True)
                created.append(f"🔊 {name}")

            if created_cat:
                created.append(f"Категория «{cat_name}»")

        await interaction.followup.send(
            f"**Создано:** {', '.join(created) if created else 'нет (всё уже есть)'}\n"
            f"**Уже существовали:** {', '.join(existing) if existing else 'нет'}",
            ephemeral=True,
        )

    @app_commands.command(name="debug_channels", description="Показать текущие категории и каналы (для админов)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def debug_channels(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        lines = []
        for cat in guild.categories:
            sub = []
            for ch in sorted(cat.text_channels, key=lambda c: c.name):
                sub.append(f"# {ch.name} ({ch.id})")
            for ch in sorted(cat.voice_channels, key=lambda c: c.name):
                sub.append(f"🔊 {ch.name} ({ch.id})")
            lines.append(f"**{cat.name}** ({cat.id}): " + (", ".join(sub) if sub else "— пусто"))
        for ch in sorted(guild.text_channels, key=lambda c: c.name):
            if ch.category is None:
                lines.append(f"# {ch.name} (без категории)")
        for ch in sorted(guild.voice_channels, key=lambda c: c.name):
            if ch.category is None:
                lines.append(f"🔊 {ch.name} (без категории)")
        msg = "\n".join(lines) if lines else "Нет каналов."
        if len(msg) > 1900:
            msg = msg[:1900]
        await interaction.followup.send(f"**Каналы сервера:**\n{msg}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
