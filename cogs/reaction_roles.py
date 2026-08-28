import discord
from discord.ext import commands

from config import CONFIG


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("reaction_roles") or {}
        self._panel_message_id = None
        self._started_panel = False

    async def _ensure_panel(self):
        if not self.config.get("enabled", True):
            return
        channel = self.bot.get_channel(self.config.get("channel_id", 0))
        if channel is None:
            return
        roles = self.config.get("roles", [])
        if not roles:
            return
        desc = "\n".join(f"{r['emoji']} — **{r['role']}**" for r in roles)
        embed = discord.Embed(
            title=self.config.get("message", "Выбери уведомления"),
            description=desc,
            color=discord.Color.purple(),
        )
        embed.set_footer(text="Нажми на реакцию под этим сообщением")

        if self._panel_message_id:
            try:
                msg = await channel.fetch_message(self._panel_message_id)
            except Exception:
                msg = None
            if msg is not None:
                try:
                    await msg.edit(embed=embed)
                except discord.HTTPException:
                    pass
                await self._sync_reactions(msg, roles)
                return
        async for old in channel.history(limit=20):
            if old.author.id == self.bot.user.id and old.embeds:
                footer = (old.embeds[0].footer.text or "").strip()
                if footer != "Нажми на реакцию под этим сообщением":
                    continue
                msg = old
                try:
                    await msg.edit(embed=embed)
                except discord.HTTPException:
                    pass
                await self._sync_reactions(msg, roles)
                self._panel_message_id = msg.id
                return
        msg = await channel.send(embed=embed)
        await self._sync_reactions(msg, roles)
        self._panel_message_id = msg.id

    @staticmethod
    async def _sync_reactions(msg, roles):
        wanted = {r["emoji"] for r in roles}
        current = {r.emoji for r in msg.reactions if isinstance(r.emoji, str)}
        for emoji in wanted - current:
            try:
                await msg.add_reaction(emoji)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._started_panel:
            self._started_panel = True
            self.bot.loop.create_task(self._ensure_panel())

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.message_id != self._panel_message_id:
            return
        await self._toggle_role(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.message_id != self._panel_message_id:
            return
        await self._toggle_role(payload, add=False)

    async def _toggle_role(self, payload: discord.RawReactionActionEvent, add: bool):
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        for spec in self.config.get("roles", []):
            emoji = spec["emoji"]
            if payload.emoji.name != emoji:
                continue
            role = discord.utils.get(guild.roles, name=spec["role"])
            if role is None:
                return
            try:
                if add and role not in member.roles:
                    await member.add_roles(role, reason="Выбор роли по реакции")
                elif not add and role in member.roles:
                    await member.remove_roles(role, reason="Снятие роли по реакции")
            except discord.Forbidden:
                pass
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
