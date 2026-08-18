import json
import threading
import time
from pathlib import Path

QUEUE_FILE = Path(__file__).resolve().parent.parent / "data" / "question_queue.json"

_lock = threading.Lock()
_queue = []


def _load():
    global _queue
    try:
        if QUEUE_FILE.exists():
            _queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        _queue = []


def _save():
    try:
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUEUE_FILE.write_text(
            json.dumps(_queue, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


_load()


def add(user: str, text: str):
    with _lock:
        _queue.append({"user": user, "text": text, "ts": time.time()})
        _save()
        return len(_queue)


def count() -> int:
    with _lock:
        return len(_queue)


def list_queue():
    with _lock:
        return list(_queue)


def pop_first():
    with _lock:
        if not _queue:
            return None
        item = _queue.pop(0)
        _save()
        return item


def remove(index: int):
    with _lock:
        if 0 <= index < len(_queue):
            item = _queue.pop(index)
            _save()
            return item
    return None


def clear():
    with _lock:
        _queue.clear()
        _save()
