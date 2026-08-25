import logging

import discord
from discord.ext import commands

from config import CONFIG, GUILD_ID


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("welcome") or {}

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        if not self.config.get("enabled", True):
            return
        if self.config.get("send_dm", True):
            try:
                embed = self._build_embed(member)
                if len(embed) > 6000:
                    embed = discord.Embed(
                        title=self.config.get("title", "Добро пожаловать!"),
                        description=self.config.get(
                            "intro", "Рады видеть тебя на сервере! Загляни в чаты и приходи на стримы."
                        ),
                        color=discord.Color.purple(),
                    )
                await member.send(embed=embed)
                logging.info("Welcome: приветствие отправлено %s", member)
            except discord.HTTPException:
                logging.warning("Welcome: не удалось отправить ЛС %s", member)
            except discord.Forbidden:
                logging.warning("Welcome: нельзя отправить ЛС %s", member)

    def _build_embed(self, member: discord.Member) -> discord.Embed:
        guild = member.guild
        descriptions = self.config.get("channel_descriptions") or {}
        voice_descriptions = self.config.get("voice_descriptions") or {}
        hidden_voice = set(self.config.get("hidden_voice") or [])

        lines = []
        for cat in guild.categories:
            text_channels = [c for c in cat.text_channels if c.permissions_for(guild.default_role).read_messages]
            if text_channels:
                cat_lines = []
                for c in text_channels:
                    desc = descriptions.get(c.name, "Общение в канале")
                    cat_lines.append(f"• **<#{c.id}>** — {desc}")
                if cat_lines:
                    lines.append(f"**{cat.name}**")
                    lines.extend(cat_lines)
                    lines.append("")

        voice_lines = []
        for vc in guild.voice_channels:
            if vc.name in hidden_voice:
                continue
            if vc.permissions_for(guild.default_role).connect:
                desc = voice_descriptions.get(vc.name, "Голосовой канал")
                voice_lines.append(f"• **{vc.name}** — {desc}")
        if voice_lines:
            lines.append("**Голосовые каналы**")
            lines.extend(voice_lines)

        intro = self.config.get(
            "intro", "Рады видеть тебя на сервере! Загляни в чаты и приходи на стримы."
        )
        embed = discord.Embed(
            title=self.config.get("title", "Добро пожаловать!"),
            description=intro,
            color=discord.Color.purple(),
        )
        if lines:
            channel_text = "\n".join(lines).strip()
            if len(channel_text) > 1024:
                channel_text = channel_text[:1020] + "\n…"
            embed.add_field(name="📂 Наши каналы", value=channel_text, inline=False)
        if GUILD_ID and guild.rules_channel:
            embed.add_field(
                name="📜 Правила",
                value=f"Ознакомься с правилами сервера: {guild.rules_channel.mention}",
                inline=False,
            )
        embed.set_footer(text=self.config.get("footer", "Приятного времяпрепровождения!"))
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
