import asyncio
import logging
import os
import sys

import discord
from discord.ext import commands

from config import TOKEN, CONFIG, PROXY_URL
from cogs.embed import EmbedBuilder, EmbedBuilderView
from cogs.leveling import Leveling
from cogs.setup import Setup
from cogs.tempvoice import TempChannelView, TempVoice
from cogs.permissions import Permissions
from cogs.ram_report import RamReport
from cogs.twitch import Twitch
from cogs.welcome import Welcome
from cogs.reaction_roles import ReactionRoles
from cogs.rules_gate import RulesGate
from cogs.season import Season
from cogs.overlay import Overlay
from cogs.youtube import YouTube
from cogs.giveaways import Giveaways
from cogs.engagement import Engagement, TwitchChatGames
from cogs.youtube_growth import YouTubeGrowth
from cogs.server_stats import ServerStats
from cogs.automod import DiscordAutomod
from cogs.socials import Socials
from cogs.poll import Poll
from cogs.voice_xp import VoiceXP
from cogs.birthdays import Birthdays
from cogs.role_menu import RoleMenu
from cogs.guild_logs import GuildLogs
from notify import DiscordLogHandler, mark_ready, notify_loop
from twitch_bot.eft_logs import EftLogWatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logging.getLogger().addHandler(DiscordLogHandler())

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

_proxy_url = PROXY_URL or ""
if _proxy_url.lower() in ("", "none", "system", "off", "0"):
    _proxy_url = ""

_bot_options = {}
if _proxy_url:
    _bot_options["proxy"] = _proxy_url

bot = commands.Bot(command_prefix="!", intents=intents, **_bot_options)

bot.add_view(TempChannelView())
bot.add_view(EmbedBuilderView())

bot.eft_logs_watcher = None


@bot.event
async def on_ready():
    print(f"Бот запущен: {bot.user} (ID: {bot.user.id})", flush=True)
    for g in bot.guilds:
        print(f"СЕРВЕР: {g.name} | ID: {g.id}", flush=True)
    if not getattr(bot, "_commands_synced", False):
        try:
            synced = await bot.tree.sync()
            print(f"Синхронизировано команд: {len(synced)}", flush=True)
            for g in bot.guilds:
                guild_synced = await bot.tree.sync(guild=g)
                print(f"Синхронизировано команд для гильды {g.name}: {len(guild_synced)}", flush=True)
        except Exception as e:
            print(f"Ошибка синхронизации команд: {e}", flush=True)
        bot._commands_synced = True
    if not getattr(bot, "_notify_started", False):
        bot._notify_started = True
        mark_ready()
        bot.loop.create_task(notify_loop(bot))
    eft_cfg = (CONFIG.get("twitch") or {}).get("live") or {}
    eft_cfg = eft_cfg.get("eft_logs") or {}
    if eft_cfg.get("enabled", False) and bot.eft_logs_watcher is None:
        bot.eft_logs_watcher = EftLogWatcher(eft_cfg)
        bot.eft_logs_watcher.start(bot.loop)
        print("Парсер логов EFT запущен", flush=True)


@bot.event
async def on_error(event, *args, **kwargs):
    logging.error("Необработанная ошибка в событии %s", event, exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    cmd = interaction.command.name if interaction.command else "?"
    logging.error("Ошибка команды %s: %s", cmd, error, exc_info=error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send("Произошла ошибка.", ephemeral=True)
        else:
            await interaction.response.send_message("Произошла ошибка.", ephemeral=True)
    except Exception:
        pass


async def main():
    async with bot:
        await bot.add_cog(Leveling(bot))
        await bot.add_cog(Setup(bot))
        await bot.add_cog(TempVoice(bot))
        await bot.add_cog(EmbedBuilder(bot))
        await bot.add_cog(Permissions(bot))
        await bot.add_cog(RamReport(bot))
        await bot.add_cog(Twitch(bot))
        await bot.add_cog(Welcome(bot))
        await bot.add_cog(ReactionRoles(bot))
        await bot.add_cog(RulesGate(bot))
        await bot.add_cog(Season(bot))
        await bot.add_cog(Overlay(bot))
        await bot.add_cog(YouTube(bot))
        await bot.add_cog(Giveaways(bot))
        await bot.add_cog(Engagement(bot))
        await bot.add_cog(TwitchChatGames(bot))
        await bot.add_cog(YouTubeGrowth(bot))
        await bot.add_cog(ServerStats(bot))
        await bot.add_cog(DiscordAutomod(bot))
        await bot.add_cog(Socials(bot))
        await bot.add_cog(Poll(bot))
        await bot.add_cog(VoiceXP(bot))
        await bot.add_cog(Birthdays(bot))
        await bot.add_cog(RoleMenu(bot))
        await bot.add_cog(GuildLogs(bot))
        await bot.start(TOKEN)


def _acquire_single_instance_mutex() -> bool:
    try:
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.CreateMutexW(None, False, "DendichRoleBotMutex")
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(handle)
            return False
        if not handle:
            return False
        _acquire_single_instance_mutex.handle = handle
        return True
    except Exception:
        logging.warning("Не удалось создать mutex одиночного инстанса")
        return True


if __name__ == "__main__":
    if not _acquire_single_instance_mutex():
        logging.warning("Уже запущен другой инстанс бота — выход.")
        sys.exit(0)
    asyncio.run(main())
