import pytest

from app.config import MAX_UPLOAD_CHUNK_MIB, load_settings

REQUIRED_ENV = {
    "TELEGRAM_API_ID": "1",
    "TELEGRAM_API_HASH": "hash",
    "TELEGRAM_BOT_TOKEN": "token",
    "DROPBOX_APP_KEY": "key",
    "DROPBOX_APP_SECRET": "secret",
    "DROPBOX_REFRESH_TOKEN": "refresh",
}


def required_env(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)


def test_largest_safe_upload_chunk_is_accepted(monkeypatch):
    required_env(monkeypatch)
    monkeypatch.setenv("UPLOAD_CHUNK_SIZE_MB", str(MAX_UPLOAD_CHUNK_MIB))

    settings = load_settings()

    assert settings.upload_chunk_size == MAX_UPLOAD_CHUNK_MIB * 1024 * 1024


def test_upload_chunk_above_dropbox_limit_is_rejected(monkeypatch):
    required_env(monkeypatch)
    monkeypatch.setenv("UPLOAD_CHUNK_SIZE_MB", str(MAX_UPLOAD_CHUNK_MIB + 1))

    with pytest.raises(SystemExit, match="between 4 and 148"):
        load_settings()


def test_upload_chunk_must_be_multiple_of_four(monkeypatch):
    required_env(monkeypatch)
    monkeypatch.setenv("UPLOAD_CHUNK_SIZE_MB", "6")

    with pytest.raises(SystemExit, match="multiple of 4"):
        load_settings()
