"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv

log = logging.getLogger(__name__)

load_dotenv()

MIB = 1024 * 1024


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _parse_allowed_user_ids(raw: str) -> frozenset[int]:
    if not raw:
        return frozenset()
    values: set[int] = set()
    for part in re.split(r"[\s,]+", raw):
        part = part.strip()
        if not part:
            continue
        try:
            values.add(int(part))
        except ValueError:
            log.warning("Ignoring invalid ALLOWED_USER_IDS entry: %r", part)
    return frozenset(values)


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_bot_token: str
    dropbox_app_key: str
    dropbox_app_secret: str
    dropbox_refresh_token: str
    dropbox_path: str
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    session_name: str = "bot_session"
    download_chunk_size: int = 512 * 1024
    upload_chunk_size: int = 4 * MIB
    small_file_threshold: int = 8 * MIB
    max_retries: int = 6
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0


def load_settings() -> Settings:
    missing = [
        name
        for name in (
            "TELEGRAM_API_ID",
            "TELEGRAM_API_HASH",
            "TELEGRAM_BOT_TOKEN",
            "DROPBOX_APP_KEY",
            "DROPBOX_APP_SECRET",
            "DROPBOX_REFRESH_TOKEN",
        )
        if not _env(name)
    ]
    if missing:
        raise SystemExit(
            "Missing required environment variables: " + ", ".join(missing)
        )

    try:
        api_id = int(_env("TELEGRAM_API_ID"))
    except ValueError as exc:
        raise SystemExit("TELEGRAM_API_ID must be an integer") from exc

    upload_chunk = int(_env("UPLOAD_CHUNK_SIZE_MB") or 4) * MIB
    if not 4 * MIB <= upload_chunk <= 16 * MIB:
        raise SystemExit("UPLOAD_CHUNK_SIZE_MB must be between 4 and 16")

    small_threshold = int(_env("SMALL_FILE_THRESHOLD_MB") or 8) * MIB
    if small_threshold < MIB:
        raise SystemExit("SMALL_FILE_THRESHOLD_MB must be at least 1")

    path = _env("DROPBOX_PATH") or "/telegram_2_dropbox"
    if not path.startswith("/"):
        path = "/" + path

    allowed = _parse_allowed_user_ids(_env("ALLOWED_USER_IDS"))
    if not allowed:
        log.warning(
            "ALLOWED_USER_IDS is empty: the bot will deny every upload and "
            "reply with the sender's user ID. Add that ID to ALLOWED_USER_IDS."
        )

    return Settings(
        telegram_api_id=api_id,
        telegram_api_hash=_env("TELEGRAM_API_HASH"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        dropbox_app_key=_env("DROPBOX_APP_KEY"),
        dropbox_app_secret=_env("DROPBOX_APP_SECRET"),
        dropbox_refresh_token=_env("DROPBOX_REFRESH_TOKEN"),
        dropbox_path=path.rstrip("/") or "/",
        allowed_user_ids=allowed,
        session_name=_env("SESSION_NAME") or "bot_session",
        upload_chunk_size=upload_chunk,
        small_file_threshold=small_threshold,
        max_retries=int(_env("MAX_RETRIES") or 6),
        retry_base_delay=float(_env("RETRY_BASE_DELAY") or 1.0),
        retry_max_delay=float(_env("RETRY_MAX_DELAY") or 30.0),
    )
