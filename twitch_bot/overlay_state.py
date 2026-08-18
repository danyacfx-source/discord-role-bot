import time

_state = {
    "map": None,
    "quest": None,
    "allergy": None,
    "updated": None,
}


def set(key: str, value: str):
    _state[key] = value
    _state["updated"] = time.time()


def get_state() -> dict:
    return dict(_state)
