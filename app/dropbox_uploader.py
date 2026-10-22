"""Streaming Dropbox uploads.

The uploader never writes to disk.  Large files are sent with Dropbox upload
sessions: the first chunk starts the session, subsequent chunks are appended,
and the final chunk is committed.  Every blocking Dropbox SDK call runs in a
worker thread via :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any

import requests
from dropbox import Dropbox
from dropbox.exceptions import ApiError, HttpError, RateLimitError
from dropbox.files import CommitInfo, UploadSessionCursor, WriteMode

from .config import Settings
from .utils import dropbox_path, sanitize_filename

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int | None], Awaitable[None]]

_TRANSIENT_API_MARKERS = (
    "too_many_write_operations",
    "too_many_requests",
    "internal_error",
    "server_error",
)


class DropboxUploader:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dbx = Dropbox(
            app_key=settings.dropbox_app_key,
            app_secret=settings.dropbox_app_secret,
            oauth2_refresh_token=settings.dropbox_refresh_token,
            timeout=120,
        )
        self._chunk_size = settings.upload_chunk_size
        self._small_threshold = settings.small_file_threshold
        self._max_retries = settings.max_retries
        self._base_delay = settings.retry_base_delay
        self._max_delay = settings.retry_max_delay
        # Serialise uploads: one Dropbox client/session is shared, and this
        # also keeps duplicate-name autorenames deterministic.
        self._lock = asyncio.Lock()

    async def upload_stream(
        self,
        chunks: AsyncIterable[bytes],
        size: int | None,
        filename: str,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        """Upload a byte stream and return the final Dropbox path.

        Uploads are serialised so the shared Dropbox client is never used
        concurrently.  ``size`` may be ``None`` when Telegram does not report
        it.  Known small files use a single ``files_upload`` call; everything
        else uses an upload session.
        """
        async with self._lock:
            return await self._upload_stream(chunks, size, filename, on_progress)

    async def _upload_stream(
        self,
        chunks: AsyncIterable[bytes],
        size: int | None,
        filename: str,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        safe_name = sanitize_filename(filename)
        path = dropbox_path(self._settings.dropbox_path, safe_name)

        if size is not None and size <= self._small_threshold:
            data, too_big = await self._collect_small(chunks)
            if not too_big:
                metadata = await self._call(
                    self._dbx.files_upload,
                    data,
                    path,
                    mode=WriteMode.add,
                    autorename=True,
                    mute=True,
                )
                if on_progress is not None:
                    await on_progress(len(data), len(data))
                return metadata.path_display

            # The reported size was wrong; fall back to a session upload.
            return await self._upload_session(
                _prepend(data, chunks), None, path, on_progress
            )

        return await self._upload_session(chunks, size, path, on_progress)

    async def _collect_small(
        self, chunks: AsyncIterable[bytes]
    ) -> tuple[bytes, bool]:
        """Collect at most ``small_threshold + 1`` bytes."""
        buffer = bytearray()
        limit = self._small_threshold + 1
        async for chunk in chunks:
            buffer.extend(chunk)
            if len(buffer) >= limit:
                return bytes(buffer), True
        return bytes(buffer), False

    async def _upload_session(
        self,
        chunks: AsyncIterable[bytes],
        size: int | None,
        path: str,
        on_progress: ProgressCallback | None,
    ) -> str:
        # A fixed-size buffer avoids bytearray over-allocation: peak memory is
        # one upload chunk plus the immutable copy handed to the SDK.
        buffer = bytearray(self._chunk_size)
        filled = 0
        session_id: str | None = None
        offset = 0
        last_reported = 0

        async def flush(piece: bytes, *, final: bool = False) -> str | None:
            nonlocal session_id, offset, last_reported

            if session_id is None:
                session = await self._call(
                    self._dbx.files_upload_session_start, piece
                )
                session_id = session.session_id
            elif final:
                cursor = UploadSessionCursor(session_id, offset)
                commit = CommitInfo(
                    path=path,
                    mode=WriteMode.add,
                    autorename=True,
                    mute=True,
                )
                metadata = await self._call(
                    self._dbx.files_upload_session_finish,
                    piece,
                    cursor,
                    commit,
                )
                offset += len(piece)
                if on_progress is not None:
                    await on_progress(offset, size or offset)
                return metadata.path_display
            else:
                cursor = UploadSessionCursor(session_id, offset)
                await self._call(
                    self._dbx.files_upload_session_append_v2, piece, cursor
                )

            offset += len(piece)
            # Throttle progress updates to roughly once per chunk.
            if on_progress is not None and offset - last_reported >= len(piece):
                last_reported = offset
                await on_progress(offset, size)
            return None

        async for chunk in chunks:
            view = memoryview(chunk)
            while view:
                take = min(self._chunk_size - filled, len(view))
                buffer[filled : filled + take] = view[:take]
                filled += take
                view = view[take:]
                if filled == self._chunk_size:
                    result = await flush(bytes(buffer))
                    filled = 0
                    if result is not None:  # pragma: no cover
                        return result

        if session_id is None:
            # Everything fit in one chunk.  This can happen when Telegram did
            # not report a size, or reported one incorrectly.
            data = bytes(buffer[:filled])
            metadata = await self._call(
                self._dbx.files_upload,
                data,
                path,
                mode=WriteMode.add,
                autorename=True,
                mute=True,
            )
            if on_progress is not None:
                await on_progress(len(data), len(data) or None)
            return metadata.path_display

        # ``buffer[:filled]`` is the final chunk; it may be empty if the file
        # size was an exact multiple of the chunk size.  Dropbox accepts an
        # empty final chunk for upload sessions.
        return await flush(bytes(buffer[:filled]), final=True)  # type: ignore[return-value]

    async def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking Dropbox SDK call in a thread, with retries."""
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            except RateLimitError as exc:
                delay = exc.backoff if exc.backoff else self._backoff(attempt)
                last_exc = exc
                log.warning(
                    "Dropbox rate limit (attempt %d/%d), retrying in %.1fs",
                    attempt,
                    self._max_retries,
                    delay,
                )
            except HttpError as exc:
                if exc.status_code < 500:
                    raise
                delay = self._backoff(attempt)
                last_exc = exc
                log.warning(
                    "Dropbox HTTP %s (attempt %d/%d), retrying in %.1fs",
                    exc.status_code,
                    attempt,
                    self._max_retries,
                    delay,
                )
            except ApiError as exc:
                if not self._is_transient_api_error(exc):
                    raise
                delay = self._backoff(attempt)
                last_exc = exc
                log.warning(
                    "Transient Dropbox API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    self._max_retries,
                    delay,
                    exc,
                )
            except (
                requests.exceptions.RequestException,
                TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                delay = self._backoff(attempt)
                last_exc = exc
                log.warning(
                    "Dropbox network error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    self._max_retries,
                    delay,
                    exc,
                )

            if attempt == self._max_retries:
                break
            await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    def _backoff(self, attempt: int) -> float:
        delay = min(self._max_delay, self._base_delay * (2 ** (attempt - 1)))
        return delay * (0.5 + random.random() * 0.5)

    @staticmethod
    def _is_transient_api_error(exc: ApiError) -> bool:
        text = str(exc.error).lower()
        return any(marker in text for marker in _TRANSIENT_API_MARKERS)


async def _prepend(first: bytes, chunks: AsyncIterable[bytes]) -> AsyncIterable[bytes]:
    if first:
        yield first
    async for chunk in chunks:
        yield chunk
