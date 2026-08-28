import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import TEMP_CATS, TEMP_TRIGGERS

temp_channel_owners: dict[int, int] = {}
trigger_channel_ids: set[int] = set()
temp_category_ids: set[int] = set()

_channel_locks: dict[int, asyncio.Lock] = {}


def _channel_lock(channel_id: int) -> asyncio.Lock:
    lock = _channel_locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _channel_locks[channel_id] = lock
    return lock


def _slug(name: str) -> str:
    return re.sub(r"[^\w\-]", "", name.lower().replace(" ", "-"))


def _spec_for(channel: discord.VoiceChannel | None) -> dict | None:
    if channel is None or channel.category is None:
        return None
    return TEMP_CATS.get(channel.category.name)


def _is_create(channel: discord.VoiceChannel | None) -> bool:
    if channel is None:
        return False
    if channel.id in trigger_channel_ids:
        return True
    spec = _spec_for(channel)
    if spec is None:
        return False
    return _slug(channel.name) == _slug(spec.get("create", "➕ Создать канал"))


def _is_managed(channel: discord.VoiceChannel | None) -> bool:
    if channel is None:
        return False
    if channel.id in trigger_channel_ids:
        return False
    spec = _spec_for(channel)
    if spec is not None:
        return _slug(channel.name) != _slug(spec.get("create", "➕ Создать канал"))
    if channel.category is not None and channel.category.id in temp_category_ids:
        return True
    return False


def _is_owner(interaction: discord.Interaction, vc: discord.VoiceChannel) -> bool:
    return temp_channel_owners.get(vc.id) == interaction.user.id


class TempChannelKickModal(discord.ui.Modal, title="Выгнать участника"):
    target = discord.ui.TextInput(label="ID или упоминание участника", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message(
                "Вы не в голосовом канале.", ephemeral=True
            )
        vc = interaction.user.voice.channel
        if not _is_managed(vc):
            return await interaction.response.send_message(
                "Нельзя управлять этим каналом.", ephemeral=True
            )
        if not _is_owner(interaction, vc):
            return await interaction.response.send_message(
                "Только владелец канала может выгонять.", ephemeral=True
            )
        raw = self.target.value.strip()
        uid = raw.strip("<@!>")
        try:
            uid = int(uid)
        except ValueError:
            return await interaction.response.send_message(
                "Укажите ID участника.", ephemeral=True
            )
        member = interaction.guild.get_member(uid)
        if not member:
            return await interaction.response.send_message(
                "Участник не найден.", ephemeral=True
            )
        if member.voice and member.voice.channel and member.voice.channel.id == vc.id:
            await member.move_to(None, reason="Выгнан из временного канала")
            await interaction.response.send_message(
                f"✅ {member.mention} выгнан.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Участник не в вашем канале.", ephemeral=True
            )


class TempChannelRenameModal(discord.ui.Modal, title="Переименовать канал"):
    new_name = discord.ui.TextInput(label="Новое название", required=True, max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message(
                "Вы не в голосовом канале.", ephemeral=True
            )
        vc = interaction.user.voice.channel
        if not _is_managed(vc):
            return await interaction.response.send_message(
                "Нельзя управлять этим каналом.", ephemeral=True
            )
        if not _is_owner(interaction, vc):
            return await interaction.response.send_message(
                "Только владелец канала может переименовывать.", ephemeral=True
            )
        old_name = vc.name
        await vc.edit(name=self.new_name.value, reason="Переименован владельцем")
        await interaction.response.send_message(
            f"✅ Канал переименован: `{old_name}` → `{self.new_name.value}`",
            ephemeral=True,
        )


class TempChannelLimitModal(discord.ui.Modal, title="Лимит участников"):
    limit = discord.ui.TextInput(label="Лимит (0 = без лимита, макс. 99)", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message(
                "Вы не в голосовом канале.", ephemeral=True
            )
        vc = interaction.user.voice.channel
        if not _is_managed(vc):
            return await interaction.response.send_message(
                "Нельзя управлять этим каналом.", ephemeral=True
            )
        if not _is_owner(interaction, vc):
            return await interaction.response.send_message(
                "Только владелец канала может менять лимит.", ephemeral=True
            )
        try:
            n = int(self.limit.value)
        except ValueError:
            return await interaction.response.send_message("Введите число.", ephemeral=True)
        if n < 0 or n > 99:
            return await interaction.response.send_message(
                "Лимит должен быть от 0 до 99.", ephemeral=True
            )
        await vc.edit(user_limit=n, reason="Лимит изменён владельцем")
        text = "✅ Лимит снят." if n == 0 else f"✅ Лимит установлен: **{n}** участников."
        await interaction.response.send_message(text, ephemeral=True)


class TempChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Закрыть", style=discord.ButtonStyle.danger, custom_id="temp_vc_lock")
    async def lock_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message(
                "Вы не в голосовом канале.", ephemeral=True
            )
        vc = interaction.user.voice.channel
        if not _is_managed(vc):
            return await interaction.response.send_message(
                "Нельзя управлять этим каналом.", ephemeral=True
            )
        if not _is_owner(interaction, vc):
            return await interaction.response.send_message(
                "Только владелец канала может закрывать/открывать.", ephemeral=True
            )
        everyone = interaction.guild.default_role
        current = vc.overwrites_for(everyone)
        is_locked = current.connect is False
        if is_locked:
            current.connect = None
            await vc.set_overwrite(everyone, overwrite=current, reason="Канал открыт")
            button.label = "🔒 Закрыть"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("✅ Канал открыт.", ephemeral=True)
        else:
            current.connect = False
            await vc.set_overwrite(everyone, overwrite=current, reason="Канал закрыт")
            button.label = "🔓 Открыть"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("✅ Канал закрыт.", ephemeral=True)

    @discord.ui.button(label="👢 Выгнать", style=discord.ButtonStyle.secondary, custom_id="temp_vc_kick")
    async def kick_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TempChannelKickModal())

    @discord.ui.button(label="✏️ Название", style=discord.ButtonStyle.primary, custom_id="temp_vc_rename")
    async def rename_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TempChannelRenameModal())

    @discord.ui.button(label="👥 Лимит", style=discord.ButtonStyle.success, custom_id="temp_vc_limit")
    async def set_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TempChannelLimitModal())

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        logging.error("Ошибка в TempChannelView: %s", error, exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Произошла ошибка.", ephemeral=True)
            else:
                await interaction.response.send_message("Произошла ошибка.", ephemeral=True)
        except Exception:
            pass


class TempVoice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._started_cleanup = False

    def _temp_channels(self, guild: discord.Guild) -> list[discord.VoiceChannel]:
        result = []
        seen: set[int] = set()
        for category in guild.categories:
            if category.name not in TEMP_CATS and category.id not in temp_category_ids:
                continue
            for ch in category.voice_channels:
                if ch.id in seen:
                    continue
                if _is_managed(ch):
                    seen.add(ch.id)
                    result.append(ch)
        return result

    @tasks.loop(seconds=60)
    async def cleanup_empty_channels(self):
        for guild in self.bot.guilds:
            for vc in self._temp_channels(guild):
                humans = [m for m in vc.members if not m.bot]
                if not humans:
                    async with _channel_lock(vc.id):
                        try:
                            temp_channel_owners.pop(vc.id, None)
                            await vc.delete(reason="Периодическая очистка временных каналов")
                            logging.info("Очищен пустой временный канал %s (периодическая уборка)", vc.name)
                        except discord.NotFound:
                            temp_channel_owners.pop(vc.id, None)
                        except Exception as e:
                            logging.error("Ошибка периодической очистки канала %s: %s", vc.name, e)

    @cleanup_empty_channels.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._started_cleanup:
            self._started_cleanup = True
            self.cleanup_empty_channels.start()
        try:
            for guild in self.bot.guilds:
                for cat_name in TEMP_CATS:
                    category = discord.utils.get(guild.categories, name=cat_name)
                    if category is None:
                        continue
                    spec = TEMP_CATS[cat_name]
                    for ch in category.voice_channels:
                        if _slug(ch.name) == _slug(spec.get("create", "➕ Создать канал")):
                            trigger_channel_ids.add(ch.id)
                for ch in guild.voice_channels:
                    if ch.id in TEMP_TRIGGERS:
                        trigger_channel_ids.add(ch.id)
                for t_id in trigger_channel_ids:
                    ch = guild.get_channel(t_id)
                    if ch is not None and ch.category is not None:
                        temp_category_ids.add(ch.category.id)
                for vc in self._temp_channels(guild):
                    humans = [m for m in vc.members if not m.bot]
                    if not humans:
                        async with _channel_lock(vc.id):
                            try:
                                temp_channel_owners.pop(vc.id, None)
                                await vc.delete(reason="Очистка пустого временного канала")
                                logging.info("Очищен пустой временный канал %s", vc.name)
                            except discord.NotFound:
                                temp_channel_owners.pop(vc.id, None)
                            except Exception as e:
                                logging.error("Ошибка очистки временного канала %s: %s", vc.name, e)
                    elif vc.id not in temp_channel_owners:
                        temp_channel_owners[vc.id] = humans[0].id
                        logging.info("Восстановлен владелец канала %s теперь %s", vc.name, humans[0])
        except Exception as e:
            logging.error("Ошибка очистки временных каналов: %s", e)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot:
            return

        if before.channel and _is_create(before.channel):
            return

        if after.channel and _is_create(after.channel):
            category = after.channel.category
            spec = TEMP_CATS.get(category.name)
            try:
                vc = await category.create_voice_channel(
                    name=self._new_channel_name(after.channel, category, spec),
                    reason="Временный канал",
                )
                temp_channel_owners[vc.id] = member.id
                moved = False
                for m in list(after.channel.members):
                    if m.bot:
                        continue
                    try:
                        await m.move_to(vc, reason="Перемещение в временный канал")
                        moved = True
                    except Exception as e:
                        logging.error("Ошибка перемещения %s во временный канал: %s", m, e)
                if not moved:
                    temp_channel_owners.pop(vc.id, None)
                    await vc.delete(reason="Временный канал: никто не перемещён")
                    logging.info("Удалён пустой временный канал (не удалось переместить участников)")
            except Exception as e:
                logging.error("Ошибка создания временного канала: %s", e)

        if before.channel and _is_managed(before.channel):
            vc = before.channel
            if before.channel == after.channel:
                return
            async with _channel_lock(vc.id):
                remaining = [m for m in vc.members if not m.bot]
                if not remaining:
                    try:
                        temp_channel_owners.pop(vc.id, None)
                        await vc.delete(reason="Временный канал: все вышли")
                        logging.info("Удалён пустой временный канал %s", vc.name)
                    except discord.NotFound:
                        temp_channel_owners.pop(vc.id, None)
                    except Exception as e:
                        logging.error("Ошибка удаления временного канала: %s", e)
                elif (
                    vc.id in temp_channel_owners
                    and temp_channel_owners[vc.id] == member.id
                    and (after.channel is None or after.channel.id != vc.id)
                ):
                    new_owner = remaining[0]
                    temp_channel_owners[vc.id] = new_owner.id
                    logging.info("Владелец канала %s теперь %s", vc.name, new_owner)
                    embed = discord.Embed(
                        title="👑 Права канала переданы",
                        description=(
                            f"Предыдущий владелец **{member.display_name}** покинул канал.\n"
                            f"Новый владелец: **{new_owner.mention}**"
                        ),
                        color=discord.Color.gold(),
                    )
                    try:
                        await vc.send(embed=embed)
                    except discord.NotFound:
                        logging.info("Канал %s уже удалён, уведомление о передаче прав пропущено", vc.name)
                    except Exception as e:
                        logging.error("Ошибка уведомления о передаче прав: %s", e)

    def _new_channel_name(
        self,
        trigger: discord.VoiceChannel,
        category: discord.CategoryChannel,
        spec: dict | None,
    ) -> str:
        name = TEMP_TRIGGERS.get(trigger.id)
        if not name:
            name = spec.get("prefix", "Канал") if spec else "Канал"
        return name

    @app_commands.command(name="temp_panel", description="Отправить панель управления темп-каналами")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def temp_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎛️ Панель управления временным каналом",
            description=(
                "Зайдите в свой временный голосовой канал и нажмите кнопку:\n\n"
                "🔒 **Закрыть** — закрыть/открыть канал для всех\n"
                "👢 **Выгнать** — выгнать участника из канала\n"
                "✏️ **Название** — переименовать канал\n"
                "👥 **Лимит** — ограничить число участников"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=TempChannelView())


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVoice(bot))
