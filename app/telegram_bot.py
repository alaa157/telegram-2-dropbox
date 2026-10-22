"""Telegram bot powered by Telethon (MTProto), streaming media to Dropbox."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from .config import Settings, load_settings
from .dropbox_uploader import DropboxUploader
from .utils import human_size

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 3.0


def _filename_for(message: Any) -> str:
    file = getattr(message, "file", None)
    name = getattr(file, "name", None) if file else None
    if name:
        return name

    ext = (getattr(file, "ext", None) if file else None) or ".bin"
    kind = "photo" if getattr(message, "photo", None) else "file"
    stamp = message.date.strftime("%Y%m%d_%H%M%S") if message.date else "unknown"
    return f"{kind}_{stamp}{ext}"


def _file_size(message: Any) -> int | None:
    file = getattr(message, "file", None)
    return getattr(file, "size", None) if file else None


class UploadBot:
    def __init__(self, settings: Settings, uploader: DropboxUploader) -> None:
        self._settings = settings
        self._uploader = uploader
        # A persistent session avoids re-running ImportBotAuthorization on every
        # restart (Telegram rate-limits that heavily).  The file is gitignored.
        self._client = TelegramClient(
            settings.session_name,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )

    async def run(self) -> None:
        await self._client.start(bot_token=self._settings.telegram_bot_token)
        me = await self._client.get_me()
        log.info("Logged in as @%s (id=%s)", me.username, me.id)
        if not self._settings.allowed_user_ids:
            log.warning("ALLOWED_USER_IDS is empty; every upload will be denied")

        self._client.add_event_handler(self._on_message, events.NewMessage)
        await self._client.run_until_disconnected()

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        message = event.message
        if message is None:
            return

        sender_id = event.sender_id
        text = (message.text or "").strip()

        # Help unauthenticated users discover the ID they must allow.  Text
        # commands are harmless and never trigger an upload.
        if text in {"/start", "/id", "/help"} or text.startswith("/start@"):
            await self._reply_safe(
                event,
                "Send me a photo or file and I will upload it to Dropbox.\n"
                f"Your Telegram user ID is: {sender_id}\n"
                "The bot owner must add it to ALLOWED_USER_IDS before uploads "
                "are accepted.",
            )
            return

        if getattr(message, "file", None) is None:
            return
        if sender_id not in self._settings.allowed_user_ids:
            log.warning("Denied upload from user id=%s", sender_id)
            await self._reply_safe(
                event,
                "⛔ You are not allowed to use this bot.\n"
                f"Your Telegram user ID is: {sender_id}\n"
                "Ask the owner to add it to ALLOWED_USER_IDS.",
            )
            return

        filename = _filename_for(message)
        size = _file_size(message)
        log.info(
            "Received file=%r size=%s from user=%s",
            filename,
            human_size(size),
            sender_id,
        )

        status = None
        try:
            if size is None or size > self._settings.small_file_threshold:
                status = await self._reply_safe(
                    event,
                    f"⏳ Uploading {filename} ({human_size(size)})…",
                )
                on_progress = self._make_progress(status, filename, size)
            else:
                on_progress = None

            chunks = self._client.iter_download(
                message.media,
                chunk_size=self._settings.download_chunk_size,
                request_size=self._settings.download_chunk_size,
                file_size=size,
            )
            path = await self._uploader.upload_stream(
                chunks, size, filename, on_progress
            )
        except Exception as exc:
            log.exception("Upload failed for %r", filename)
            text = f"❌ Upload failed: {type(exc).__name__}: {exc}"
            if status is not None:
                await self._edit_safe(status, text)
            else:
                await self._reply_safe(event, text)
            return

        text = (
            f"✅ Uploaded {filename} ({human_size(size)})\n"
            f"Dropbox path: {path}"
        )
        if status is not None:
            await self._edit_safe(status, text)
        else:
            await self._reply_safe(event, text)

    def _make_progress(self, status: Any, filename: str, size: int | None):
        loop = asyncio.get_running_loop()
        last_update = 0.0
        last_percent = -1

        async def on_progress(done: int, total: int | None) -> None:
            nonlocal last_update, last_percent
            now = loop.time()
            total = total or size
            percent = int(done * 100 / total) if total else 0
            if percent == last_percent:
                return
            if now - last_update < PROGRESS_INTERVAL and percent < 100:
                return
            last_update = now
            last_percent = percent
            await self._edit_safe(
                status,
                f"⏳ Uploading {filename}\n"
                f"{percent}% ({human_size(done)} / {human_size(total)})",
            )

        return on_progress

    @staticmethod
    async def _reply_safe(event: events.NewMessage.Event, text: str) -> Any:
        try:
            return await event.reply(text, parse_mode=None)
        except FloodWaitError as exc:
            log.warning("Telegram flood wait while replying: %ss", exc.seconds)
            await asyncio.sleep(exc.seconds)
            return await event.reply(text, parse_mode=None)

    @staticmethod
    async def _edit_safe(message: Any, text: str) -> None:
        try:
            await message.edit(text, parse_mode=None)
        except FloodWaitError as exc:
            log.warning("Telegram flood wait while editing progress: %ss", exc.seconds)
        except Exception:
            log.debug("Could not edit progress message", exc_info=True)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    # The Dropbox client may refresh its OAuth token in the constructor.
    uploader = await asyncio.to_thread(DropboxUploader, settings)
    bot = UploadBot(settings, uploader)
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
