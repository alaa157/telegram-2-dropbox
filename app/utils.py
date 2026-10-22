"""Small helpers shared by the bot and the uploader."""

from __future__ import annotations

import os
import re
import unicodedata

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_MULTI_SPACE = re.compile(r"\s+")


def human_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "unknown size"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def sanitize_filename(name: str | None, fallback: str = "telegram_file") -> str:
    """Return a safe, single-component Dropbox filename.

    - strips directory components
    - removes characters that are invalid on common filesystems / Dropbox
    - normalises unicode and collapses whitespace
    - limits the name to 200 characters while keeping the extension
    """
    if not name:
        name = fallback

    # Dropbox paths are unicode; normalise so visually identical names behave.
    name = unicodedata.normalize("NFC", str(name))
    name = name.replace("\\", "/")
    name = os.path.basename(name)
    name = _BAD_CHARS.sub("_", name)
    name = _MULTI_SPACE.sub(" ", name).strip().strip(".")

    if not name or name in {".", ".."}:
        name = fallback

    stem, ext = os.path.splitext(name)
    if len(name) > 200:
        ext = ext[:20]
        stem = stem[: 200 - len(ext)]
        name = stem + ext

    return name or fallback


def dropbox_path(folder: str, filename: str) -> str:
    folder = "/" + folder.strip("/")
    if folder == "/":
        folder = ""
    return f"{folder}/{filename}"
