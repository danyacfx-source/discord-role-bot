import json
import logging
import re
from pathlib import Path

log = logging.getLogger("twitch")

QUEUE_FILE = Path(__file__).resolve().parent.parent / "data" / "song_queue.json"


class SongQueue:
    def __init__(self):
        self.queue = []
        self.current = None
        self._load()

    def _load(self):
        try:
            if QUEUE_FILE.exists():
                data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
                self.queue = data.get("queue", [])
                self.current = data.get("current")
        except Exception:
            log.exception("SongQueue: ошибка чтения очереди")

    def _save(self):
        try:
            QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            QUEUE_FILE.write_text(
                json.dumps({"queue": self.queue, "current": self.current}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            log.exception("SongQueue: ошибка записи очереди")

    def add(self, url, title, requester):
        self.queue.append({"url": url, "title": title, "requester": requester})
        self._save()

    def next_track(self):
        if self.queue:
            self.current = self.queue.pop(0)
            self._save()
            return self.current
        self.current = None
        self._save()
        return None

    def skip(self):
        return self.next_track()

    def clear(self):
        self.queue = []
        self.current = None
        self._save()

    def get_queue(self):
        return list(self.queue)

    def get_current(self):
        return self.current

    def length(self):
        return len(self.queue)


def extract_video_id(url):
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None
