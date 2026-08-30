import asyncio
import json
import logging
import re
import time
from pathlib import Path

from twitch_bot.raid_state import raid_auto_end, raid_auto_start
from twitch_bot.stream_state import is_stream_live
from config import DATA_DIR

log = logging.getLogger("eft_logs")

LOG_ROOT = Path(r"D:\EscapeFromTarkov\fixer\Logs")
STATE_FILE = DATA_DIR / "eft_logs_state.json"

TRANSIT_RE = re.compile(r"\[Transit\] Flag:Common.*Locations:(\w+)")
MENU_RE = re.compile(r"\[DevLog\] === MENU LOAD PROFILE ===")
SESSION_END_RE = re.compile(r"EFT\.Player:OnGameSessionEnd")

MAP_NAMES = {
    "bigmap": "Таможня",
    "laboratory": "Лаборатория",
    "Shoreline": "Побережье",
    "Woods": "Лес",
    "Interchange": "Развязка",
    "factory4": "Завод",
    "factory4_day": "Завод (день)",
    "factory4_night": "Завод (ночь)",
    "customs": "Таможня",
    "Lighthouse": "Маяк",
    "Streets": "Улицы Таркова",
    "TarkovStreets": "Улицы Таркова",
    "RezervBase": "Резерв",
    "Reserve": "Резерв",
    "Suburbs": "Пригород",
    "Terminal": "Терминал",
    "GroundZero": "Нулевой уровень",
}


def map_name(code):
    if not code:
        return None
    return MAP_NAMES.get(code) or code


class EftLogWatcher:
    def __init__(self, config=None):
        cfg = config or {}
        self.root = Path(cfg.get("log_dir") or LOG_ROOT)
        self.poll_seconds = max(5, int(cfg.get("poll_seconds", 15)))
        self._task = None
        self._offsets = {}
        self._active_dir = None
        self._first_scan = True
        self._load_offsets()

    def _load_offsets(self):
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self._offsets = data.get("offsets", {})
                self._active_dir = data.get("active_dir")
        except Exception:
            log.exception("EftLogs: ошибка чтения файла состояния")

    def _save_offsets(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(
                json.dumps(
                    {"offsets": self._offsets, "active_dir": self._active_dir},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            log.exception("EftLogs: ошибка записи файла состояния")

    def start(self, loop):
        self._task = loop.create_task(self._run())
        return self._task

    def stop(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self):
        log.info("EftLogs: мониторинг логов %s каждые %d сек", self.root, self.poll_seconds)
        while True:
            try:
                self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("EftLogs: ошибка цикла мониторинга (продолжаю)")
            await asyncio.sleep(self.poll_seconds)

    def _tick(self):
        if not self.root.exists():
            return
        dirs = [d for d in self.root.iterdir() if d.is_dir()]
        if not dirs:
            return
        latest = max(dirs, key=lambda d: d.stat().st_mtime)
        if self._active_dir != str(latest):
            log.info("EftLogs: переключаюсь на свежую папку логов %s", latest.name)
            self._active_dir = str(latest)
            self._snapshot_offsets(latest)
            self._save_offsets()
            return
        if self._first_scan:
            self._first_scan = False
            self._snapshot_offsets(latest)
            log.info("EftLogs: история логов пропущена, слежу только за новыми событиями")
            self._save_offsets()
            return
        candidates = {p.name: p for p in latest.iterdir() if p.is_file()}
        for suffix, handler in (
            ("application_000.log", self._scan_transit),
            ("output_000.log", self._scan_menu),
            ("errors_000.log", self._scan_session_end),
        ):
            path = next((p for n, p in candidates.items() if n.endswith(suffix)), None)
            if path is not None:
                self._process_file(path, handler)
        self._save_offsets()

    def _snapshot_offsets(self, latest):
        try:
            for p in latest.iterdir():
                if p.is_file():
                    self._offsets[str(p)] = p.stat().st_size
        except OSError:
            log.exception("EftLogs: ошибка чтения размеров логов")

    def _process_file(self, path, handler):
        key = str(path)
        offset = self._offsets.get(key, 0)
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size <= offset:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                for raw in f:
                    handler(raw.rstrip("\n"))
        except Exception:
            log.exception("EftLogs: ошибка чтения %s", path)
            return
        self._offsets[key] = size

    def _scan_transit(self, line):
        m = TRANSIT_RE.search(line)
        if m:
            code = m.group(1)
            name = map_name(code)
            if not is_stream_live():
                return
            log.info("EftLogs: обнаружен вход в рейд, карта: %s (%s)", name, code)
            raid_auto_start(name)

    def _scan_menu(self, line):
        if MENU_RE.search(line):
            if not is_stream_live():
                return
            log.info("EftLogs: рейд завершён (возврат в меню)")
            raid_auto_end()

    def _scan_session_end(self, line):
        if SESSION_END_RE.search(line):
            if not is_stream_live():
                return
            log.info("EftLogs: рейд завершён (OnGameSessionEnd)")
            raid_auto_end()
