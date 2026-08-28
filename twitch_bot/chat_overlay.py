from collections import deque
import time

_buffer = deque(maxlen=80)
_seq = 0


def push_chat(source, author, text, role="", avatar=""):
    global _seq
    if not text:
        return
    _seq += 1
    _buffer.append({
        "id": _seq,
        "source": source,
        "author": author,
        "text": text,
        "role": role,
        "avatar": avatar or "",
        "time": time.time(),
    })


def get_chat_messages():
    return list(_buffer)