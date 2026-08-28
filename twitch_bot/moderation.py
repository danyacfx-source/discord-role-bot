import logging
import re
import time
from collections import defaultdict, deque

log = logging.getLogger("moderation")

class Moderation:
    def __init__(self, config):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.banned = [w.lower() for w in self.config.get("banned_words", [])]
        self.allowed_links = [d.lower().strip(".") for d in self.config.get("allowed_links", [])]
        self._history = defaultdict(lambda: deque(maxlen=self.config.get("history_size", 10)))
        self._timestamps = defaultdict(list)
        self._timeouts = defaultdict(int)
        self._link_re = re.compile(r"(?:https?://|www\.)?([a-z0-9-]+\.[a-z]{2,})", re.IGNORECASE)
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
        if not self.config.get("block_links", True):
            return False
        for match in self._link_re.finditer(content):
            domain = match.group(1).lower()
            if any(domain == d or domain.endswith("." + d) for d in self.allowed_links):
                continue
            await self._timeout(message, "ссылки", user)
            return True
        return False

    async def _check_words(self, message, content, user):
        lowered = content.lower()
        for word in self.banned:
            if re.search(r"\b" + re.escape(word) + r"\b", lowered):
                await self._timeout(message, "запрещённые слова", user)
                return True
        return False

    async def _check_caps(self, message, content, user):
        if len(content) < self.config.get("caps_min_len", 10):
            return False
        letters = [c for c in content if c.isalpha()]
        if not letters:
            return False
        ratio = sum(c.isupper() for c in letters) / len(letters)
        if ratio >= self.config.get("caps_threshold", 0.75):
            await self._timeout(message, "капс", user)
            return True
        return False

    async def _check_stretch(self, message, content, user):
        if len(content) < 8:
            return False
        prev = None
        streak = 0
        for ch in content:
            if ch == prev:
                streak += 1
            else:
                streak = 1
                prev = ch
            if streak >= 5:
                await self._timeout(message, "растянутый спам", user)
                return True
        return False

    async def _check_spam(self, message, content, user, now):
        window = self.config.get("message_cooldown", 2)
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
        self._timeouts[user] += 1
        try:
            if self._timeouts[user] >= self.ban_threshold:
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
