import logging

import discord
from discord.ext import commands

from config import CONFIG

log = logging.getLogger("rules_gate")


class RulesGate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("rules_gate") or {}
        self._message_id = None
        self._started = False

    async def _find_target(self):
        channel = self.bot.get_channel(self.config.get("channel_id", 0))
        if channel is None:
            return None
        message_id = self.config.get("message_id", 0)
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                if message is not None:
                    return message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        title = self.config.get("title", "Правила сервера")
        async for old in channel.history(limit=50):
            if old.author.id != self.bot.user.id or not old.embeds:
                continue
            if (old.embeds[0].title or "").strip() == title:
                return old
        return None

    async def _ensure_reaction(self):
        if not self.config.get("enabled", True):
            return
        emoji = self.config.get("emoji", "✅")
        message = await self._find_target()
        if message is None:
            log.warning("RulesGate: целевое сообщение правил не найдено")
            return
        if emoji not in [str(r.emoji) for r in message.reactions]:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                pass
        self._message_id = message.id
        log.info("RulesGate: реакция %s на сообщение %s", emoji, message.id)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._started:
            self._started = True
            self.bot.loop.create_task(self._ensure_reaction())

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._toggle(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._toggle(payload, add=False)

    async def _toggle(self, payload: discord.RawReactionActionEvent, add: bool):
        if self._message_id is None or payload.message_id != self._message_id:
            return
        emoji = str(payload.emoji)
        if emoji != self.config.get("emoji", "✅"):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        role = discord.utils.get(guild.roles, name=self.config.get("role", "Ознакомлен"))
        if role is None:
            log.warning("RulesGate: роль «%s» не найдена", self.config.get("role"))
            return
        try:
            if add and role not in member.roles:
                await member.add_roles(role, reason="Принятие правил ✅")
            elif not add and role in member.roles:
                await member.remove_roles(role, reason="Снятие реакции ✅")
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RulesGate(bot))