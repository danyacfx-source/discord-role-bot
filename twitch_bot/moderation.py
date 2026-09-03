import logging
import time
from collections import defaultdict, deque

from moderation_rules import (
    check_caps,
    check_links,
    check_stretch,
    match_banned_word,
    normalized_allowed_links,
)

log = logging.getLogger("moderation")

class Moderation:
    def __init__(self, config):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.banned = [w.lower() for w in self.config.get("banned_words", [])]
        self.allowed_links = normalized_allowed_links(self.config)
        self._history = defaultdict(lambda: deque(maxlen=self.config.get("history_size", 10)))
        self._timestamps = defaultdict(list)
        # Храним метки времени нарушений, чтобы порог накопления имел временнóе окно
        # (дефект D21) и словарь не рос безгранично (дефект D07).
        self._timeouts = defaultdict(list)
        self.ban_threshold = self.config.get("ban_after_timeouts", 3)
        self.channel = None

    def set_channel(self, channel):
        self.channel = channel

    def _is_exempt(self, message):
        author = message.author
        return author is None or author.is_broadcaster or author.is_mod or author.is_vip

    async def check(self, message) -> bool:
        if not self.enabled:
            return False
        if self._is_exempt(message):
            return False
        content = (message.content or "").strip()
        if not content:
            return False
        user = message.author.name
        now = time.time()

        if await self._check_links(message, content, user):
            return True
        if await self._check_words(message, content, user):
            return True
        if await self._check_caps(message, content, user):
            return True
        if await self._check_stretch(message, content, user):
            return True
        if await self._check_spam(message, content, user, now):
            return True
        return False

    async def _check_links(self, message, content, user):
        host = check_links(content, self.allowed_links, block_links=self.config.get("block_links", True))
        if host:
            await self._timeout(message, "ссылки", user)
            return True
        return False

    async def _check_words(self, message, content, user):
        if match_banned_word(content, self.banned):
            await self._timeout(message, "запрещённые слова", user)
            return True
        return False

    async def _check_caps(self, message, content, user):
        if check_caps(
            content,
            threshold=self.config.get("caps_threshold", 0.75),
            min_len=self.config.get("caps_min_len", 10),
        ):
            await self._timeout(message, "капс", user)
            return True
        return False

    async def _check_stretch(self, message, content, user):
        if check_stretch(content):
            await self._timeout(message, "растянутый спам", user)
            return True
        return False

    async def _check_spam(self, message, content, user, now):
        window = self.config.get("message_cooldown", 2)
        # Ограничиваем размер общих словарей: вытесняем записи, неактивные дольше окна
        # (дефекты D07/D21 — раньше _timestamps и _timeouts росли безгранично).
        if len(self._timestamps) > 1000:
            cutoff = now - 3600
            stale = [u for u, ts in self._timestamps.items() if not ts or ts[-1] < cutoff]
            for u in stale:
                del self._timestamps[u]
        if len(self._timeouts) > 1000:
            cutoff = now - 3600
            stale = [u for u, ts in self._timeouts.items() if not ts or ts[-1] < cutoff]
            for u in stale:
                del self._timeouts[u]
        stamps = [t for t in self._timestamps[user] if now - t <= window]
        stamps.append(now)
        self._timestamps[user] = stamps
        if len(stamps) > self.config.get("max_messages_in_window", 3):
            await self._timeout(message, "флуд", user)
            return True
        history = self._history[user]
        history.append(content)
        threshold = self.config.get("duplicate_threshold", 3)
        if history.count(content) >= threshold:
            await self._timeout(message, "повтор сообщений", user)
            return True
        return False

    async def _timeout(self, message, reason, user):
        duration = self.config.get("timeout_duration", 300)
        safe_reason = reason.replace(" ", "_")
        now = time.time()
        window = self.config.get("ban_window_seconds", 1800)
        stamps = self._timeouts[user]
        while stamps and now - stamps[0] > window:
            stamps.pop(0)
        stamps.append(now)
        try:
            if len(stamps) >= self.ban_threshold:
                await message.channel.send(f"/ban {user} Повторные_нарушения_модерации")
                if self.config.get("announce_timeouts", True):
                    await message.channel.send(
                        f"@{user} — {reason}, повторные нарушения, бан"
                    )
                self._timeouts.pop(user, None)
                return
            await message.channel.send(f"/timeout {user} {duration} {safe_reason}")
            if self.config.get("announce_timeouts", True):
                await message.channel.send(f"@{user} — {reason}, тайм-аут {duration} сек")
        except Exception:
            log.warning("Moderation: не удалось выдать тайм-аут %s: %s", user, reason, exc_info=True)

    async def timeout_user(self, user, duration, reason=""):
        try:
            await self.channel.send(f"/timeout {user} {duration} {reason.replace(' ', '_')}")
            return True
        except Exception:
            return False

    async def ban_user(self, user, reason=""):
        try:
            await self.channel.send(f"/ban {user} {reason.replace(' ', '_')}")
            return True
        except Exception:
            return False

    async def unban_user(self, user):
        try:
            await self.channel.send(f"/unban {user}")
            return True
        except Exception:
            return False
