import logging
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from config import CONFIG
from moderation_rules import (
    check_caps,
    check_links,
    check_stretch,
    extract_hosts,
    match_banned_word,
    normalized_allowed_links,
)

log = logging.getLogger("automod")


class DiscordAutomod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("discord_automod") or {}
        self._messages: dict[int, deque[float]] = defaultdict(deque)
        # Дефект: счётчик накопления нарушений рос безгранично и не имел
        # временнóго окна — разрозненные нарушения за длительный период давали
        # тот же результат, что серия за минуту. Храним метки времени нарушений.
        self._timeout_counts: dict[int, deque[float]] = defaultdict(deque)
        self._ban_window = float(self.config.get("ban_window_seconds", 300))

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
        cog = self.bot.get_cog("GuildLogs")
        if cog is not None:
            try:
                await cog.post_mod_log(
                    member=member,
                    action="Автомод",
                    reason=reason,
                    channel=message.channel,
                    content=content,
                )
            except Exception:
                log.exception("Ошибка отправки лога модерации")

    def _has_ignored_role(self, member: discord.Member) -> bool:
        ignored = set(self.config.get("ignore_roles") or [])
        return any(r.name in ignored for r in member.roles)

    def _analyze(self, member: discord.Member, content: str) -> str | None:
        # Счётчик сообщений учитывает каждое проанализированное сообщение.
        # Раньше вызов стоял после проверок слова/ссылки/капса, из-за чего
        # сообщения, пойманные на этих проверках, не учитывались счётчиком флуда.
        self._track_spam(member)
        if self._is_spam(member):
            return "спам"

        word = match_banned_word(content, self.config.get("banned_words") or [])
        if word:
            return f"запрещённое слово: «{word}»"

        if self.config.get("block_links", True):
            allowed = normalized_allowed_links(self.config)
            host = check_links(content, allowed, block_links=True)
            if host:
                return f"ссылка на неразрешённый домен: {host}"

        if check_caps(
            content,
            threshold=self.config.get("caps_threshold", 0.8),
            min_len=self.config.get("caps_min_len", 12),
        ):
            return "капс"

        if check_stretch(content):
            return "растянутый спам"

        return None

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
            now = time.time()
            window = self._ban_window
            stamps = self._timeout_counts[member.id]
            while stamps and now - stamps[0] > window:
                stamps.popleft()
            stamps.append(now)
            if len(stamps) >= ban_after:
                try:
                    await member.ban(reason=f"Automod: {ban_after} нарушений за {int(window)}с")
                    log.warning("Automod: бан %s (%s)", member, reason)
                    cog = self.bot.get_cog("GuildLogs")
                    if cog is not None:
                        try:
                            await cog.post_mod_log(
                                member=member,
                                action="Автомод: бан",
                                reason=f"{ban_after} нарушения за окно {int(window)}с ({reason})",
                            )
                        except Exception:
                            log.exception("Ошибка отправки лога модерации")
                except discord.Forbidden:
                    pass
                stamps.clear()


async def setup(bot: commands.Bot):
    await bot.add_cog(DiscordAutomod(bot))