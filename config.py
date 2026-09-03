import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DATA_DIR = Path(os.environ.get("DATA_DIR") or (BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("DB_DIR") or BASE_DIR) / "data.db"

_env_path = BASE_DIR / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

CONFIG["token"] = os.environ.get("DISCORD_TOKEN") or os.environ.get("DISCORD_BOT_TOKEN") or CONFIG.get("token", "")
CONFIG.setdefault("youtube", {})["client_id"] = os.environ.get("YOUTUBE_CLIENT_ID") or CONFIG.get("youtube", {}).get("client_id", "")
CONFIG.setdefault("youtube", {})["client_secret"] = os.environ.get("YOUTUBE_CLIENT_SECRET") or CONFIG.get("youtube", {}).get("client_secret", "")
CONFIG.setdefault("youtube", {})["refresh_token"] = os.environ.get("YOUTUBE_REFRESH_TOKEN") or CONFIG.get("youtube", {}).get("refresh_token", "")

LEVELS = sorted(CONFIG["levels"], key=lambda x: x["messages"])
EXTRA_ROLES = CONFIG.get("extra_roles", [])
ROLE_SETTINGS = CONFIG.get("role_settings", {})
CHANNELS = CONFIG.get("channels", {})
BOT_NAME = CONFIG.get("bot_name", "Милый Килла")
GUILD_ID = CONFIG.get("guild_id", 0)
TOKEN = CONFIG["token"]
PROXY_URL = os.environ.get("DISCORD_PROXY", "")
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
