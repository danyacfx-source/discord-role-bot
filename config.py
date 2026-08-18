import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "data.db"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

LEVELS = sorted(CONFIG["levels"], key=lambda x: x["messages"])
EXTRA_ROLES = CONFIG.get("extra_roles", [])
ROLE_SETTINGS = CONFIG.get("role_settings", {})
CHANNELS = CONFIG.get("channels", {})
BOT_NAME = CONFIG.get("bot_name", "Собрали и съебали")
GUILD_ID = CONFIG.get("guild_id", 0)
TOKEN = CONFIG["token"]
WHITELIST_CHANNELS = set(CONFIG.get("whitelist_channels", []))
EXCLUDE_ROLES = set(CONFIG.get("exclude_roles", []))
ANNOUNCE_CHANNEL_ID = CONFIG.get("announce_channel_id", 0)
LOG_CHANNEL_ID = CONFIG.get("log_channel_id", 0)
PING_ROLES = CONFIG.get("ping_roles", ["Owner", "Moderator"])
TEMP_TRIGGERS = {int(k): v for k, v in CONFIG.get("temp_triggers", {}).items()}
SEASON = CONFIG.get("season", {})
OVERLAY = CONFIG.get("overlay", {})

TEMP_CATS = {
    name: spec
    for name, spec in CHANNELS.items()
    if spec.get("type") == "temp"
}
