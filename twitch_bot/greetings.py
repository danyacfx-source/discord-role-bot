import json
import time
from pathlib import Path

class Greetings:
    def __init__(self, config):
        self.config = config or {}
        data_dir = Path(__file__).resolve().parent / "data"
        data_dir.mkdir(exist_ok=True)
        self.seen_path = data_dir / "seen_users.json"
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
        if user in self.seen:
            return
        now = time.time()
        if now - self._last_welcome.get(user, 0) < self.config.get("welcome_cooldown", 60):
            return
        self.seen[user] = now
        self._last_welcome[user] = now
        self._save()
        template = self.config.get("welcome_message", "Добро пожаловать в чат, {user}!")
        await message.channel.send(template.format(user=author.name))
