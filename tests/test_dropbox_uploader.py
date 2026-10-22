import asyncio
from types import SimpleNamespace

import pytest
from dropbox.exceptions import RateLimitError

from app.config import MIB, Settings
from app.dropbox_uploader import DropboxUploader


class FakeDropbox:
    def __init__(self):
        self.uploads = []
        self.starts = []
        self.appends = []
        self.finishes = []
        self.start_failures = 0

    def files_upload(self, data, path, **kwargs):
        self.uploads.append((data, path, kwargs))
        return SimpleNamespace(path_display=path)

    def files_upload_session_start(self, data, **kwargs):
        if self.start_failures:
            self.start_failures -= 1
            raise RateLimitError("req", None, 0.01)
        self.starts.append(data)
        return SimpleNamespace(session_id="session-1")

    def files_upload_session_append_v2(self, data, cursor, **kwargs):
        self.appends.append((data, cursor.offset))
        return SimpleNamespace()

    def files_upload_session_finish(self, data, cursor, commit, **kwargs):
        self.finishes.append((data, cursor.offset, commit.path))
        return SimpleNamespace(path_display=commit.path)


def settings(**overrides):
    values = {
        "telegram_api_id": 1,
        "telegram_api_hash": "hash",
        "telegram_bot_token": "token",
        "dropbox_app_key": "key",
        "dropbox_app_secret": "secret",
        "dropbox_refresh_token": "refresh",
        "dropbox_path": "/dest",
        "upload_chunk_size": 8 * MIB,
        "small_file_threshold": 8 * MIB,
        "max_retries": 3,
        "retry_base_delay": 0.01,
        "retry_max_delay": 0.01,
    }
    values.update(overrides)
    return Settings(**values)


def make_uploader(fake, **overrides):
    uploader = DropboxUploader(settings(**overrides))
    uploader._dbx = fake
    return uploader


async def chunks_from(data: bytes, chunk_size: int):
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


def test_small_file_uses_single_upload():
    fake = FakeDropbox()
    uploader = make_uploader(fake)
    data = b"a" * 1024

    async def run():
        return await uploader.upload_stream(chunks_from(data, 256), len(data), "x.bin")

    path = asyncio.run(run())
    assert path == "/dest/x.bin"
    assert len(fake.uploads) == 1
    assert fake.uploads[0][0] == data
    assert not fake.starts and not fake.appends and not fake.finishes


def test_large_file_uses_session_with_correct_offsets():
    fake = FakeDropbox()
    uploader = make_uploader(fake)
    data = b"b" * (17 * MIB)

    async def run():
        return await uploader.upload_stream(chunks_from(data, 1 * MIB), len(data), "big.bin")

    path = asyncio.run(run())
    assert path == "/dest/big.bin"
    assert not fake.uploads
    assert len(fake.starts) == 1
    assert fake.starts[0] == data[: 8 * MIB]
    assert [offset for _, offset in fake.appends] == [8 * MIB]
    assert fake.appends[0][0] == data[8 * MIB : 16 * MIB]
    final_data, final_offset, final_path = fake.finishes[0]
    assert final_data == data[16 * MIB :]
    assert final_offset == 16 * MIB
    assert final_path == "/dest/big.bin"
    assert len(fake.starts[0]) + sum(len(d) for d, _ in fake.appends) + len(final_data) == len(data)


def test_exact_multiple_finishes_with_empty_chunk():
    fake = FakeDropbox()
    uploader = make_uploader(fake)
    data = b"c" * (16 * MIB)

    async def run():
        return await uploader.upload_stream(chunks_from(data, 1 * MIB), len(data), "exact.bin")

    asyncio.run(run())
    assert fake.starts[0] == data[: 8 * MIB]
    assert fake.appends[0][0] == data[8 * MIB :]
    assert fake.finishes[0][0] == b""
    assert fake.finishes[0][1] == 16 * MIB


def test_size_unknown_small_file_still_uses_single_upload():
    fake = FakeDropbox()
    uploader = make_uploader(fake)
    data = b"d" * 4096

    async def run():
        return await uploader.upload_stream(chunks_from(data, 512), None, "unknown.bin")

    asyncio.run(run())
    assert len(fake.uploads) == 1
    assert fake.uploads[0][0] == data


def test_transient_error_is_retried():
    fake = FakeDropbox()
    fake.start_failures = 1
    uploader = make_uploader(fake, max_retries=3, retry_base_delay=0.01, retry_max_delay=0.01)
    data = b"e" * (9 * MIB)

    async def run():
        return await uploader.upload_stream(chunks_from(data, 1 * MIB), len(data), "retry.bin")

    asyncio.run(run())
    assert len(fake.starts) == 1
    assert len(fake.finishes) == 1


def test_permanent_api_error_is_not_retried():
    from dropbox.exceptions import ApiError

    fake = FakeDropbox()

    def fail(data, **kwargs):
        raise ApiError("req", "path/not_found", "bad path", None)

    fake.files_upload_session_start = fail
    uploader = make_uploader(fake)
    data = b"f" * (9 * MIB)

    async def run():
        return await uploader.upload_stream(chunks_from(data, 1 * MIB), len(data), "bad.bin")

    with pytest.raises(ApiError):
        asyncio.run(run())
