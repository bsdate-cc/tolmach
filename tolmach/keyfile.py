"""The gateway's access key: one random token in a file both processes read."""
from __future__ import annotations

import secrets

from tolmach import paths


def read_key() -> str | None:
    try:
        key = paths.key_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


def ensure_key() -> str:
    key = read_key()
    if key is None:
        key = secrets.token_urlsafe(32)
        paths.key_file().write_text(key, encoding="utf-8")
    return key
