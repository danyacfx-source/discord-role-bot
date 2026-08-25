import discord

from config import GUILD_ID, LEVELS

COMMAND_GUILDS = [discord.Object(id=GUILD_ID)] if GUILD_ID else []


def _norm(name: str) -> str:
    return name.lower().replace(" ", "-")


def find_channel(channels: list, name: str):
    n = _norm(name)
    return next((c for c in channels if _norm(c.name) == n), None)


def role_for_level(guild: discord.Guild, level_idx: int) -> discord.Role | None:
    if level_idx < 0:
        return None
    name = LEVELS[level_idx]["role_name"]
    return discord.utils.get(guild.roles, name=name)
