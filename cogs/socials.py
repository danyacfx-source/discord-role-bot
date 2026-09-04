import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG

DEFAULT_LINKS = {
    "discord": "https://discord.gg/rEDcPBuk6c",
    "site": "https://danyacfx-source.github.io/dendich/",
    "youtube": "https://www.youtube.com/@Dendosich",
    "donate": "https://donatty.com/dendich",
    "kick": "https://kick.com/dendosich",
}

ICONS = {
    "discord": "💬",
    "site": "🌐",
    "youtube": "▶️",
    "donate": "💝",
    "kick": "🎥",
}


class Socials(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("socials") or {}

    @app_commands.command(name="socials", description="Все полезные ссылки")
    @app_commands.guild_only()
    async def socials(self, interaction: discord.Interaction):
        links = {**DEFAULT_LINKS, **{k: v for k, v in self.config.items() if isinstance(v, str) and v}}
        embed = discord.Embed(
            title="🔗 Наши ссылки",
            description="Подписывайся и приходи на стримы!",
            color=discord.Color.brand_green(),
        )
        for key, url in links.items():
            if not url or not url.startswith(("http://", "https://")):
                continue
            icon = ICONS.get(key, "•")
            embed.add_field(name=f"{icon} {key.capitalize()}", value=url, inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Socials(bot))