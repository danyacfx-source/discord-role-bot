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
    conn.execute(
        """CREATE TABLE IF NOT EXISTS birthdays (
            user_id INTEGER PRIMARY KEY,
            month INTEGER NOT NULL,
            day INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY,
            title TEXT,
            prize TEXT,
            description TEXT,
            winner_count INTEGER,
            end_time REAL,
            channel_id INTEGER,
            guild_id INTEGER,
            message_id INTEGER,
            author_id INTEGER,
            min_days INTEGER DEFAULT 0,
            participants TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active'
        )"""
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(members)")]
    if "xp" not in cols:
        conn.execute("ALTER TABLE members ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "UPDATE members SET xp = points * 20 WHERE xp = 0 AND points > 0"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_members_guild_points ON members (guild_id, points DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_season_members_guild_points ON season_members (guild_id, points DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_counters_channel ON counters (channel)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways (status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_giveaways_message ON giveaways (message_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_giveaways_end ON giveaways (end_time)"
    )
    conn.commit()
    conn.close()


_init_db()


import threading

_bind_conn: "sqlite3.Connection | None" = None
_bind_lock = threading.Lock()


@contextmanager
def _connect():
    """Общее соединение с БД вместо нового на каждую операцию.

    Раньше каждая операция открывала и закрывала отдельное соединение, а вызовы
    из корутин блокировали цикл событий. Теперь используем одно постоянное
    соединение (не пересоздаём его на каждую операцию) и ограничиваем доступ
    блокировкой. Записи внутри блока вызывают conn.commit() явно.
    """
    global _bind_conn
    with _bind_lock:
        if _bind_conn is None:
            conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            _bind_conn = conn
        yield _bind_conn


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


def birthday_set(user_id: int, month: int, day: int) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO birthdays (user_id, month, day) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET month = ?, day = ?""",
            (user_id, month, day, month, day),
        )
        conn.commit()


def birthday_get(user_id: int) -> tuple[int, int] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT month, day FROM birthdays WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row


def birthday_remove(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM birthdays WHERE user_id = ?", (user_id,))
        conn.commit()


def birthdays_all() -> list[tuple[int, int, int]]:
    with _connect() as conn:
        return conn.execute("SELECT user_id, month, day FROM birthdays").fetchall()


def giveaway_save(ga: dict) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO giveaways
               (id, title, prize, description, winner_count, end_time, channel_id,
                guild_id, message_id, author_id, min_days, participants, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 end_time = excluded.end_time,
                 message_id = excluded.message_id,
                 participants = excluded.participants,
                 status = excluded.status""",
            (
                ga["id"],
                ga["title"],
                ga["prize"],
                ga["description"],
                ga["winner_count"],
                ga["end_time"],
                ga["channel_id"],
                ga["guild_id"],
                ga.get("message_id") or 0,
                ga.get("author_id") or 0,
                ga.get("min_days") or 0,
                ga.get("participants_json", "[]"),
                ga.get("status", "active"),
            ),
        )
        conn.commit()


def giveaways_load_active() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, prize, description, winner_count, end_time, channel_id, "
            "guild_id, message_id, author_id, min_days, participants "
            "FROM giveaways WHERE status = 'active'"
        ).fetchall()
    result = []
    for row in rows:
        ga = {
            "id": row[0],
            "title": row[1],
            "prize": row[2],
            "description": row[3],
            "winner_count": row[4],
            "end_time": row[5],
            "channel_id": row[6],
            "guild_id": row[7],
            "message_id": row[8],
            "author_id": row[9],
            "min_days": row[10],
            "participants": row[11],
        }
        result.append(ga)
    return result


def giveaways_find_by_message(message_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, title, prize, description, winner_count, end_time, channel_id, "
            "guild_id, message_id, author_id, min_days, participants, status "
            "FROM giveaways WHERE message_id = ?",
            (message_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "prize": row[2],
        "description": row[3],
        "winner_count": row[4],
        "end_time": row[5],
        "channel_id": row[6],
        "guild_id": row[7],
        "message_id": row[8],
        "author_id": row[9],
        "min_days": row[10],
        "participants": row[11],
        "status": row[12],
    }


def giveaway_next_id() -> int:
    """Текущий максимум id розыгрышей в БД.

    Раньше счётчик хранился только в памяти и восстанавливался по активным
    розыгрышам — после перезапуска без активных он сбрасывался в 1, а upsert по
    primary key затирал историю завершённых розыгрышей. Теперь счётчик читается
    из БД, поэтому коллизий id не бывает.
    """
    with _connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM giveaways").fetchone()
        return int(row[0]) if row else 0


def giveaway_set_participants(giveaway_id: int, participants: list[int], status: str = "active") -> None:
    import json

    with _connect() as conn:
        conn.execute(
            "UPDATE giveaways SET participants = ?, status = ? WHERE id = ?",
            (json.dumps(participants), status, giveaway_id),
        )
        conn.commit()
