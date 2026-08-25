import asyncio
import datetime
import logging
import re
import traceback

import discord

from config import GUILD_ID, LOG_CHANNEL_ID, PING_ROLES, CONFIG

_handler_queue: asyncio.Queue = asyncio.Queue()
_startup_done: bool = False

_SENSITIVE_RE = re.compile(
    r"(token|oauth|secret|password|api_key|authorization)"
    r"[:\s]*[=:]\s*[\"']?(\S{8,})",
    re.IGNORECASE,
)

_BEARER_RE = re.compile(
    r"(Bearer\s+)(\S{8,})",
    re.IGNORECASE,
)

_OAUTH_URL_RE = re.compile(
    r"(://[^:]+:)(\S{8,})(@)",
)

_RAW_TOKEN_RE = re.compile(
    r"\b(oauth:\S{8,})\b",
    re.IGNORECASE,
)


def _sanitize(text: str) -> str:
    def _mask(m):
        key = m.group(1)
        val = m.group(2)
        if len(val) <= 8:
            return f"{key}=***"
        return f"{key}={val[:4]}...{val[-4:]}"

    def _mask_bearer(m):
        prefix = m.group(1)
        val = m.group(2)
        if len(val) <= 8:
            return f"{prefix}***"
        return f"{prefix}{val[:4]}...{val[-4:]}"

    def _mask_url(m):
        before = m.group(1)
        val = m.group(2)
        after = m.group(3)
        if len(val) <= 8:
            return f"{before}***{after}"
        return f"{before}{val[:4]}...{val[-4:]}{after}"

    text = _SENSITIVE_RE.sub(_mask, text)
    text = _BEARER_RE.sub(_mask_bearer, text)
    text = _OAUTH_URL_RE.sub(_mask_url, text)
    text = _RAW_TOKEN_RE.sub("oauth:***", text)
    return text


def mark_ready():
    global _startup_done
    _startup_done = True


class DiscordLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        if record.levelno < logging.ERROR:
            return
        if not _startup_done:
            return
        try:
            asyncio.get_running_loop().call_soon_threadsafe(
                _handler_queue.put_nowait, record
            )
        except Exception:
            pass


def _record_text(record: logging.LogRecord) -> str:
    text = record.getMessage()
    if record.exc_info and record.exc_info[0] is not None:
        text += "\n" + "".join(traceback.format_exception(*record.exc_info))
    text = _sanitize(text)
    if len(text) > 3900:
        text = text[:3900] + "\n…"
    return text


async def notify_loop(bot: discord.Client):
    await bot.wait_until_ready()
    await asyncio.sleep(10)
    while True:
        record = await _handler_queue.get()
        try:
            await _post(bot, record)
        except Exception:
            print("Не удалось отправить лог в Discord", flush=True)


async def _post(bot: discord.Client, record: logging.LogRecord):
    if not LOG_CHANNEL_ID:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        print(f"Лог-канал {LOG_CHANNEL_ID} не найден", flush=True)
        return
    is_error = record.levelno >= logging.ERROR
    embed = discord.Embed(
        title="⚠️ Ошибка" if is_error else "ℹ️ Лог",
        description=f"```\n{_record_text(record)}\n```",
        color=discord.Color.red() if is_error else discord.Color.blurple(),
    )
    embed.set_footer(text=datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
    content = None
    if is_error and GUILD_ID:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            mentions = [
                role.mention
                for rname in PING_ROLES
                if (role := discord.utils.get(guild.roles, name=rname))
            ]
            if mentions:
                content = " ".join(mentions)
    await channel.send(content=content, embed=embed)
