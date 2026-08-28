import json
import time
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "raid_stats.json"

_state = None


def _load() -> dict:
    global _state
    if _state is None:
        try:
            if DATA_FILE.exists():
                _state = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            else:
                _state = {}
        except Exception:
            _state = {}
    return _state


def _save():
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def state() -> dict:
    return dict(_load())


def start_raid():
    s = _load()
    s["status"] = "raid"
    s["started_at"] = time.time()
    s["ended_at"] = None
    _save()


def end_raid(survived: bool):
    s = _load()
    if s.get("status") != "raid":
        return
    s["status"] = "extract" if survived else "dead"
    s["ended_at"] = time.time()
    s["total_raids"] = s.get("total_raids", 0) + 1
    s["last_map"] = s.get("current_map")
    raids = s.setdefault("raids", [])
    raids.append({
        "map": s.get("current_map"),
        "started_at": s.get("started_at"),
        "ended_at": s["ended_at"],
        "survived": survived,
    })
    if survived:
        s["streak"] = s.get("streak", 0) + 1
        s["best_streak"] = max(s.get("best_streak", 0), s["streak"])
    else:
        s["streak"] = 0
    s["current_map"] = None
    s["started_at"] = None
    _save()


def reset():
    s = _load()
    s.update(
        {
            "status": None,
            "started_at": None,
            "ended_at": None,
            "streak": 0,
            "best_streak": 0,
            "current_map": None,
        }
    )
    _save()


def raid_auto_start(map_name):
    s = _load()
    if s.get("status") == "raid":
        return
    s["status"] = "raid"
    s["started_at"] = time.time()
    s["ended_at"] = None
    s["current_map"] = map_name
    _save()


def raid_auto_end():
    s = _load()
    if s.get("status") != "raid":
        return
    ended_at = time.time()
    raids = s.setdefault("raids", [])
    raids.append(
        {
            "map": s.get("current_map"),
            "started_at": s.get("started_at"),
            "ended_at": ended_at,
        }
    )
    s["total_raids"] = s.get("total_raids", 0) + 1
    s["last_map"] = s.get("current_map")
    s["streak"] = s.get("streak", 0) + 1
    s["best_streak"] = max(s.get("best_streak", 0), s["streak"])
    s["status"] = None
    s["started_at"] = None
    s["ended_at"] = ended_at
    s["current_map"] = None
    _save()
