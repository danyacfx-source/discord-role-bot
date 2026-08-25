import random
import sqlite3
from contextlib import contextmanager

from config import DB_PATH, LEVELS

XP_MIN = 15
XP_MAX = 25


def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS members (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS counters (
            channel TEXT NOT NULL,
            name TEXT NOT NULL,
            value INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (channel, name)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS season_members (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )"""
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(members)")]
    if "xp" not in cols:
        conn.execute("ALTER TABLE members ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "UPDATE members SET xp = points * 20 WHERE xp = 0 AND points > 0"
        )
        conn.commit()
    conn.close()


_init_db()


@contextmanager
def _connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        yield conn
    finally:
        conn.close()


def add_message(guild_id: int, user_id: int) -> tuple[int, int]:
    xp_gain = random.randint(XP_MIN, XP_MAX)
    with _connect() as conn:
        conn.execute(
            """INSERT INTO members (guild_id, user_id, points, xp)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + 1, xp = xp + ?""",
            (guild_id, user_id, xp_gain, xp_gain),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT points, xp FROM members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return cur.fetchone()


def get_points(guild_id: int, user_id: int) -> int:
    row = get_stats(guild_id, user_id)
    return row[0] if row else 0


def get_stats(guild_id: int, user_id: int):
    with _connect() as conn:
        cur = conn.execute(
            "SELECT points, xp FROM members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return cur.fetchone()


def get_leaderboard(guild_id: int, limit: int = 10) -> list[tuple[int, int]]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT user_id, points FROM members WHERE guild_id = ? ORDER BY points DESC LIMIT ?",
            (guild_id, limit),
        )
        return cur.fetchall()


def level_index_for(points: int) -> int:
    idx = -1
    for i, lvl in enumerate(LEVELS):
        if points >= lvl["messages"]:
            idx = i
        else:
            break
    return idx


def xp_to_next_level(level: int) -> int:
    return 5 * level * level + 50 * level + 100


def total_xp_for(level: int) -> int:
    return sum(xp_to_next_level(i) for i in range(1, level))


def level_for_xp(xp: int) -> int:
    level = 1
    while xp >= total_xp_for(level + 1):
        level += 1
    return level


def xp_in_level(xp: int, level: int) -> int:
    return xp - total_xp_for(level)


def counter_get(channel: str, name: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM counters WHERE channel = ? AND name = ?",
            (channel, name),
        ).fetchone()
        return row[0] if row else 0


def counter_add(channel: str, name: str, delta: int) -> int:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO counters (channel, name, value) VALUES (?, ?, ?)
               ON CONFLICT(channel, name) DO UPDATE SET value = value + ?""",
            (channel, name, delta, delta),
        )
        conn.commit()
        value = conn.execute(
            "SELECT value FROM counters WHERE channel = ? AND name = ?",
            (channel, name),
        ).fetchone()[0]
        return value


def counter_list(channel: str) -> list[tuple[str, int]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, value FROM counters WHERE channel = ? ORDER BY value DESC",
            (channel,),
        ).fetchall()
        return rows


def season_reset(guild_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM season_members WHERE guild_id = ?", (guild_id,))
        conn.commit()


def season_add_message(guild_id: int, user_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO season_members (guild_id, user_id, points)
               VALUES (?, ?, 1)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + 1""",
            (guild_id, user_id),
        )
        conn.commit()


def get_season_leaderboard(guild_id: int, limit: int = 10) -> list[tuple[int, int]]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT user_id, points FROM season_members WHERE guild_id = ? ORDER BY points DESC LIMIT ?",
            (guild_id, limit),
        )
        return cur.fetchall()
