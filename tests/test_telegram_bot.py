import asyncio
from types import SimpleNamespace

from app.config import Settings
from app.telegram_bot import UploadBot


class FakeEvent:
    def __init__(self, sender_id, message):
        self.sender_id = sender_id
        self.message = message
        self.replies = []

    async def reply(self, text, parse_mode=None):
        self.replies.append(text)
        return SimpleNamespace(edit=self._edit)

    async def _edit(self, text, parse_mode=None):
        self.replies.append(text)


def bot(allowed=frozenset({42})):
    settings = Settings(
        telegram_api_id=1,
        telegram_api_hash="hash",
        telegram_bot_token="token",
        dropbox_app_key="key",
        dropbox_app_secret="secret",
        dropbox_refresh_token="refresh",
        dropbox_path="/dest",
        allowed_user_ids=allowed,
    )
    return UploadBot(settings, uploader=SimpleNamespace())


def test_start_returns_user_id():
    event = FakeEvent(42, SimpleNamespace(text="/start", file=None, media=None))
    asyncio.run(bot()._on_message(event))
    assert len(event.replies) == 1
    assert "42" in event.replies[0]


def test_unauthorized_file_is_rejected():
    message = SimpleNamespace(text="", file=SimpleNamespace(size=10), media=object())
    event = FakeEvent(99, message)
    asyncio.run(bot()._on_message(event))
    assert len(event.replies) == 1
    assert "not allowed" in event.replies[0]
    assert "99" in event.replies[0]


def test_text_without_command_is_ignored():
    event = FakeEvent(42, SimpleNamespace(text="hello", file=None, media=None))
    asyncio.run(bot()._on_message(event))
    assert event.replies == []


class FakeUploader:
    async def upload_stream(self, chunks, size, filename, on_progress=None):
        data = b"".join([chunk async for chunk in chunks])
        assert data == b"payload"
        return f"/dest/{filename}"


class FakeClient:
    def iter_download(self, media, **kwargs):
        async def gen():
            yield b"pay"
            yield b"load"

        return gen()


def test_allowed_file_is_uploaded_and_reported():
    event = FakeEvent(
        42,
        SimpleNamespace(
            text="",
            file=SimpleNamespace(size=7, name="report.pdf"),
            media=object(),
            date=None,
            photo=None,
        ),
    )
    b = bot()
    b._uploader = FakeUploader()
    b._client = FakeClient()
    asyncio.run(b._on_message(event))
    assert len(event.replies) == 1
    assert "Uploaded" in event.replies[0]
    assert "/dest/report.pdf" in event.replies[0]
