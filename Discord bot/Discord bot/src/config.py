from __future__ import annotations
from typing import Dict, Set

# =========================
#  Discord / bot identity
# =========================

DISCORD_TOKEN: str = "PUT_YOUR_DISCORD_BOT_TOKEN_HERE"

# Owner IDs (Discord user IDs) allowed to control the panel.
OWNER_IDS: Set[int] = set()  # e.g. {123456789012345678}


# =========================
#  External v4-bot processes
# =========================

LOG_DIR: str = r"C:\v4logs"

BOT_EXECUTABLES: Dict[str, str] = {
    # "examplebot": r"C:\Users\You\Desktop\examplebot\v4-bot.exe",
}


# =========================
#  Monitor / alert settings
# =========================

ALERT_CHANNEL_ID: int = 0
ALERT_USER_ID: int = 0
STATUS_REFRESH_SECONDS: int = 60