import re
from typing import List, Optional

LINK_RE = re.compile(r"(?:https?://|www\.)?([\w-]+\.\w{2,})(?:[/\s]|$)", re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(r"(?<![\w.])([\w-]+\.\w{2,})(?:[/\s]|$)", re.IGNORECASE)


def normalized_allowed_links(config, key: str = "allowed_links") -> List[str]:
    return [d.lower().strip(".") for d in (config.get(key) or []) if d]


def extract_hosts(content: str) -> List[str]:
    def _clean(host: str) -> str:
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    hosts: List[str] = []
    for m in LINK_RE.finditer(content):
        host = _clean(m.group(1))
        if host and host not in hosts:
            hosts.append(host)
    for m in BARE_DOMAIN_RE.finditer(content):
        host = _clean(m.group(1))
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def match_banned_word(content: str, banned_words: List[str]) -> Optional[str]:
    lowered = content.lower()
    for word in banned_words:
        if not word:
            continue
        w = word.strip()
        if re.search(r"\b" + re.escape(w) + r"\b", lowered):
            return w
    return None


def check_links(content: str, allowed_links: List[str], block_links: bool = True) -> Optional[str]:
    if not block_links:
        return None
    allowed = [a.lower() for a in allowed_links if a]
    for host in extract_hosts(content):
        if any(host == a or host.endswith("." + a) for a in allowed):
            continue
        return host
    return None


def check_caps(content: str, threshold: float = 0.75, min_len: int = 10) -> bool:
    if len(content) < min_len:
        return False
    letters = [c for c in content if c.isalpha()]
    if not letters:
        return False
    return sum(c.isupper() for c in letters) / len(letters) >= threshold


def check_stretch(content: str, min_len: int = 8, streak: int = 5) -> bool:
    if len(content) < min_len:
        return False
    prev = None
    count = 0
    for ch in content:
        if ch == prev:
            count += 1
        else:
            count = 1
            prev = ch
        if count >= streak:
            return True
    return False
