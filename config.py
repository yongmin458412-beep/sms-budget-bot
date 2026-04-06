"""환경변수 로더."""
from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: Optional[str] = os.getenv("BOT_TOKEN") or None
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "changeme")
SESSION_SECRET: str = os.getenv("SESSION_SECRET", "dev-secret-change-me")
PORT: int = int(os.getenv("PORT", "8000"))
DB_PATH: str = os.getenv("DB_PATH", "./data/budget.db")

_raw_ids = os.getenv("ALLOWED_CHAT_IDS", "")
ALLOWED_CHAT_IDS: list[int] = [int(x.strip()) for x in _raw_ids.split(",") if x.strip()]

# Notion
NOTION_TOKEN: Optional[str] = os.getenv("NOTION_TOKEN") or None
NOTION_DB_ID: Optional[str] = os.getenv("NOTION_DB_ID") or None
