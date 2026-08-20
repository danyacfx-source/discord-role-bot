import time

_stream_live = False
_stream_started_at = None


def set_stream_live(live: bool):
    global _stream_live, _stream_started_at
    _stream_live = live
    if live:
        _stream_started_at = time.time()
    else:
        _stream_started_at = None


def is_stream_live() -> bool:
    return _stream_live


def stream_uptime() -> float:
    if _stream_started_at:
        return time.time() - _stream_started_at
    return 0
