import json
import time

from config import DATA_DIR

SEEN_TTL = 7 * 24 * 3600


class Greetings:
    def __init__(self, config):
        self.config = config or {}
        self.seen_path = DATA_DIR / "seen_users.json"
        self.seen = self._load()
        self._last_welcome = {}

    def _load(self):
        if self.seen_path.exists():
            try:
                return json.loads(self.seen_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save(self):
        try:
            self.seen_path.write_text(
                json.dumps(self.seen, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    async def handle(self, message):
        author = message.author
        if author is None or author.is_broadcaster or author.is_mod:
            return
        user = author.name.lower()
        now = time.time()
        last_seen = self.seen.get(user, 0)
        if now - last_seen < SEEN_TTL:
            return
        if now - self._last_welcome.get(user, 0) < self.config.get("welcome_cooldown", 60):
            return
        self.seen[user] = now
        self._last_welcome[user] = now
        self._gc()
        self._save()
        template = self.config.get("welcome_message", "Добро пожаловать в чат, {user}!")
        try:
            text = template.format(user=author.name)
        except (KeyError, IndexError, ValueError):
            text = f"Добро пожаловать в чат, {author.name}!"
        await message.channel.send(text)

    def _gc(self):
        cutoff = time.time() - SEEN_TTL
        stale = [k for k, ts in self.seen.items() if ts < cutoff]
        for k in stale:
            del self.seen[k]
