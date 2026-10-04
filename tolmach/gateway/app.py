"""The gateway's aiohttp application: the key check and the plain HTTP routes."""
from __future__ import annotations

import asyncio
import functools
import hmac
import json
import os
import uuid

import numpy as np
from aiohttp import web

from tolmach import VERSION
from tolmach.gateway import wav
from tolmach.gateway.realtime import realtime
from tolmach.gateway.recognizer import SAMPLE_RATE
from tolmach.gateway.segmenter import Final, Segmenter
from tolmach.gateway.state import ENGINE, KEY, STOP, Engine
from tolmach.gateway.worker import Job

_dumps = functools.partial(json.dumps, ensure_ascii=False)
_NO_KEY = {"/health"}                  # the tray polls it before it has read the key
_ALWAYS = {"/health", "/shutdown"}     # answer even while the model is not ready
# aiohttp's own default is 1 MiB, i.e. 32 s of audio. The longest thing the client
# saves is a 600 s recording (19 MB mono); this leaves room for stereo.
MAX_UPLOAD = 64 * 1024 * 1024


def _problem(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status, dumps=_dumps)


@web.middleware
async def guard(request: web.Request, handler):
    if request.path not in _NO_KEY:
        given = request.headers.get("Authorization", "")
        expected = f"Bearer {request.app[KEY]}"
        if not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            return _problem(401, "unauthorized", "missing or wrong key")
    engine = request.app[ENGINE]
    if request.path not in _ALWAYS and engine.status != "ok":
        return _problem(503, "not_ready", engine.error or "the model is still loading")
    return await handler(request)


async def health(request: web.Request) -> web.Response:
    engine = request.app[ENGINE]
    return web.json_response({"status": engine.status, "model": engine.model_name, "error": engine.error,
                              "version": VERSION, "pid": os.getpid()}, dumps=_dumps)


def _resolve(future: asyncio.Future, text: str) -> None:
    if not future.done():
        future.set_result(text)


async def recognize_all(engine: Engine, samples: np.ndarray) -> str:
    """Cut by pauses, recognize every phrase on the one worker thread, join with spaces."""
    loop = asyncio.get_running_loop()

    def cut() -> list[Final]:
        segmenter = Segmenter(engine.make_vad(), engine.draft_interval_s)
        return [a for a in segmenter.feed(samples) if isinstance(a, Final)] + segmenter.commit()

    finals = await loop.run_in_executor(None, cut)  # the VAD over a long file takes a while
    session = f"http-{uuid.uuid4().hex}"
    futures: list[asyncio.Future] = []
    for final in finals:
        future = loop.create_future()
        futures.append(future)

        def deliver(job: Job, text: str, future: asyncio.Future = future) -> None:
            loop.call_soon_threadsafe(_resolve, future, text)

        engine.worker.submit(Job(session, final.item, True, final.samples, deliver, background=True))
    try:
        texts = await asyncio.gather(*futures)
    finally:
        engine.worker.forget(session)  # the caller hung up: do not recognize the rest
    return " ".join(t for t in texts if t)


async def transcriptions(request: web.Request) -> web.Response:
    if not request.content_type.startswith("multipart/"):
        return _problem(400, "invalid_request", "expected multipart/form-data")
    data: bytes | None = None
    fields: dict[str, str] = {}
    try:
        async for part in await request.multipart():
            if part.name == "file":
                data = bytes(await part.read(decode=False))
            elif part.name:
                fields[part.name] = await part.text()
    except web.HTTPRequestEntityTooLarge:
        limit = request.app[MAX_UPLOAD_KEY]
        return _problem(413, "too_large", f"the file is larger than the limit of {limit} bytes")
    except (ValueError, AssertionError) as e:
        return _problem(400, "invalid_request", f"malformed multipart body: {e}")
    if data is None:
        return _problem(400, "invalid_request", "no 'file' part")
    response_format = fields.get("response_format", "json")
    if response_format not in ("json", "text"):
        return _problem(400, "invalid_request", "response_format must be 'json' or 'text'")
    try:
        rate, samples = wav.decode(data)
    except wav.WavError as e:
        return _problem(400, "invalid_request", f"cannot read the file as WAV: {e}")
    if rate != SAMPLE_RATE:
        return _problem(400, "invalid_request", f"only {SAMPLE_RATE} Hz WAV is supported, got {rate} Hz")
    text = await recognize_all(request.app[ENGINE], samples)
    if response_format == "text":
        return web.Response(text=text)
    return web.json_response({"text": text}, dumps=_dumps)


async def shutdown(request: web.Request) -> web.Response:
    request.app[STOP].set()
    return web.json_response({"status": "stopping"})


MAX_UPLOAD_KEY = web.AppKey("max_upload", int)


def build_app(engine: Engine, key: str, max_upload: int = MAX_UPLOAD) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=max_upload)
    app[ENGINE] = engine
    app[KEY] = key
    app[MAX_UPLOAD_KEY] = max_upload
    app[STOP] = asyncio.Event()
    app.router.add_get("/health", health)
    app.router.add_post("/v1/audio/transcriptions", transcriptions)
    app.router.add_get("/v1/realtime", realtime)
    app.router.add_post("/shutdown", shutdown)
    return app
