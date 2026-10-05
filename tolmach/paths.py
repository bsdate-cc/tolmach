"""Where Tolmach keeps its data: one folder per user, overridable for tests."""
from __future__ import annotations

import os
from pathlib import Path

ENV = "TOLMACH_HOME"
DEFAULT_FOLDER = ".tolmach"


def home() -> Path:
    base = os.environ.get(ENV)
    p = Path(base) if base else Path.home() / DEFAULT_FOLDER
    p.mkdir(parents=True, exist_ok=True)
    return p


def _folder(name: str) -> Path:
    p = home() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_file() -> Path:
    return home() / "config.json"


def key_file() -> Path:
    return home() / "key"


def terms_file() -> Path:
    return home() / "terms.txt"


def gateway_file() -> Path:
    return home() / "gateway.json"


def tray_lock() -> Path:
    return home() / "tray.lock"


def muted_file() -> Path:
    return home() / "muted.json"


def models_dir() -> Path:
    return _folder("models")


def logs_dir() -> Path:
    return _folder("logs")


def failed_dir() -> Path:
    return _folder("failed")


def tray_file() -> Path:
    return home() / "tray.json"
