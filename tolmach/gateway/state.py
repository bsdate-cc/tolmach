"""What every handler shares: the loaded engine, the key, the stop signal."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from aiohttp import web

from tolmach.gateway.extras import Extras
from tolmach.gateway.segmenter import Vad
from tolmach.gateway.worker import Worker


@dataclass
class Engine:
    """status is 'loading' | 'ok' | 'error'; worker and make_vad exist once it is 'ok'.
    All of this is about the main model; `extras` are the ones beside it, if there are any."""

    model_name: str
    language: str = ""
    draft_interval_s: float = 1.0
    status: str = "loading"
    error: str | None = None
    worker: Worker | None = None
    make_vad: Callable[[], Vad] | None = None
    extras: Extras | None = None


ENGINE = web.AppKey("engine", Engine)
KEY = web.AppKey("key", str)
STOP = web.AppKey("stop", asyncio.Event)
