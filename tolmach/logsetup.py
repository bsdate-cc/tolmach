"""One rotating log file per process. Dictated text and audio never go here."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from tolmach import paths


def setup(name: str) -> logging.Logger:
    log = logging.getLogger("tolmach")
    if not any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        handler = RotatingFileHandler(
            paths.logs_dir() / f"{name}.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        # A handler that raises is reported by aiohttp, a task that dies by asyncio:
        # without these two the log stays empty exactly when something broke.
        # WARNING and up only - the request log would be a record of who dictated when.
        for name in ("aiohttp", "asyncio"):
            other = logging.getLogger(name)
            other.addHandler(handler)
            other.setLevel(logging.WARNING)
    return log
