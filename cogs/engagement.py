import json
import time
import logging
from pathlib import Path

from discord.ext import commands
from config import DATA_DIR

log = logging.getLogger("engagement")

LOYALTY_FILE = DATA_DIR / "loyalty.json"
STREAK_FILE = DATA_DIR / "streaks.json"


def _load_json(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            # Было: блок пустой — усечённые данные обнулялись без следов в журнале.
            log.exception("Не удалось прочитать файл состояния %s", path)
            return {}
    return {}


def _save_json(path, data):
    """Атомарная запись состояния.

    Раньше файл перезаписывался целиком без промежуточного файла и атомарной
    подмены, а обработчик ошибки записи был пуст: прерывание в момент записи
    оставляло усечённые данные, а нехватка места/прав оставалась невидимой
    (дефект D17). Пишем во временный файл, затем атомарно подменяем.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        log.exception("Не удалось сохранить файл состояния %s", path)


class Engagement(commands.Cog):
    """Loyalty points, streaks (core state for minigames)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.loyalty: dict = _load_json(LOYALTY_FILE)
        self.streaks: dict = _load_json(STREAK_FILE)
        self._polls: dict = {}

    def _save_loyalty(self):
        _save_json(LOYALTY_FILE, self.loyalty)

    def _save_streaks(self):
        _save_json(STREAK_FILE, self.streaks)

    def _points_key(self, user: str, channel: str) -> str:
        return f"{channel}:{user}"

    def add_points(self, user: str, channel: str, amount: int, reason: str = ""):
        key = self._points_key(user, channel)
        entry = self.loyalty.setdefault(key, {"points": 0, "total": 0, "user": user, "channel": channel})
        entry["points"] += amount
        entry["total"] += amount
        entry["last_active"] = time.time()
        if reason:
            entry["last_reason"] = reason
        self._save_loyalty()

    def get_points(self, user: str, channel: str) -> int:
        return self.loyalty.get(self._points_key(user, channel), {}).get("points", 0)

    def spend_points(self, user: str, channel: str, amount: int) -> bool:
        key = self._points_key(user, channel)
        entry = self.loyalty.get(key)
        if not entry or entry["points"] < amount:
            return False
        entry["points"] -= amount
        self._save_loyalty()
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(Engagement(bot))
