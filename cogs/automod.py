import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from config import CONFIG

log = logging.getLogger("automod")

LINK_RE = re.compile(r"https?://([\w.-]+)", re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(r"(?<![\w.])([\w-]+\.\w{2,})(?:[/\s]|$)", re.IGNORECASE)


class DiscordAutomod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("discord_automod") or {}
        self._messages: dict[int, deque[float]] = defaultdict(deque)
        self._timeout_counts: dict[int, int] = defaultdict(int)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.config.get("enabled", True):
            return
        if message.author.bot:
            return
        if message.guild is None:
            return
        member = message.guild.get_member(message.author.id)
        if member is None:
            return
        if self._has_ignored_role(member):
            return
        if member.guild_permissions.manage_messages:
            return
        if message.channel.id in set(self.config.get("ignored_channels") or []):
            return

        content = message.content or ""
        reason = self._analyze(member, content)
        if reason is None:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        await self._punish(member, reason)
        log.warning(
            "Automod: %s в #%s: %s",
            member,
            getattr(message.channel, "name", message.channel.id),
            reason,
        )

    def _has_ignored_role(self, member: discord.Member) -> bool:
        ignored = set(self.config.get("ignore_roles") or [])
        return any(r.name in ignored for r in member.roles)

    def _analyze(self, member: discord.Member, content: str) -> str | None:
        lowered = content.lower()
        banned = self.config.get("banned_words") or []
        for word in banned:
            if not word:
                continue
            if word in lowered:
                return f"запрещённое слово: «{word.strip()}»"

        if self.config.get("block_links", True):
            hosts = self._extract_hosts(content)
            allowed = [h.lower() for h in (self.config.get("allowed_links") or []) if h]
            for host in hosts:
                ok = any(host == a or host.endswith("." + a) for a in allowed)
                if not ok:
                    return f"ссылка на неразрешённый домен: {host}"

        caps_threshold = self.config.get("caps_threshold", 0.8)
        caps_min_len = self.config.get("caps_min_len", 12)
        letters = [ch for ch in content if ch.isalpha()]
        if len(letters) >= caps_min_len:
            upper = sum(1 for ch in letters if ch.isupper())
            if upper / len(letters) >= caps_threshold:
                return "капс"

        self._track_spam(member)
        if self._is_spam(member):
            return "спам"

        return None

    @staticmethod
    def _extract_hosts(content: str) -> list[str]:
        hosts = []
        for m in LINK_RE.finditer(content):
            host = m.group(1).lower().lstrip("www.")
            if host:
                hosts.append(host)
        for m in BARE_DOMAIN_RE.finditer(content):
            host = m.group(1).lower().lstrip("www.")
            if host and host not in hosts:
                hosts.append(host)
        return hosts

    def _track_spam(self, member: discord.Member):
        now = time.time()
        window = 5.0
        queue = self._messages[member.id]
        while queue and now - queue[0] > window:
            queue.popleft()
        queue.append(now)
        while queue and len(queue) > 20:
            queue.popleft()

    def _is_spam(self, member: discord.Member) -> bool:
        max_in_window = self.config.get("max_messages_in_window", 5)
        if max_in_window <= 0:
            return False
        q = self._messages[member.id]
        now = time.time()
        recent = sum(1 for ts in q if now - ts <= 5.0)
        return len(q) > max_in_window or recent > max_in_window

    async def _punish(self, member: discord.Member, reason: str):
        duration = self.config.get("timeout_duration", 300)
        if duration > 0:
            try:
                await member.timeout(timedelta(seconds=duration), reason=f"Automod: {reason}")
            except discord.Forbidden:
                pass

        ban_after = self.config.get("ban_after_timeouts", 0)
        if ban_after and ban_after > 0:
            self._timeout_counts[member.id] += 1
            if self._timeout_counts[member.id] >= ban_after:
                try:
                    await member.ban(reason=f"Automod: {ban_after} нарушений подряд")
                    log.warning("Automod: бан %s (%s)", member, reason)
                except discord.Forbidden:
                    pass
                self._timeout_counts[member.id] = 0


async def setup(bot: commands.Bot):
    await bot.add_cog(DiscordAutomod(bot))