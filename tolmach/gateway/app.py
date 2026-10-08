"""The gateway's aiohttp application: the key check and the plain HTTP routes."""
from __future__ import annotations

import asyncio
import functools
import hmac
import json
import logging
import os
import uuid
from typing import Callable

import numpy as np
from aiohttp import web

from tolmach import VERSION
from tolmach.gateway import wav
from tolmach.gateway.extras import Extras, ModelFailed, NotInstalled
from tolmach.gateway.realtime import realtime
from tolmach.gateway.recognizer import SAMPLE_RATE
from tolmach.gateway.segmenter import Final, Segmenter
from tolmach.gateway.state import ENGINE, KEY, STOP, Engine
from tolmach.gateway.worker import Job

log = logging.getLogger("tolmach.gateway")

_dumps = functools.partial(json.dumps, ensure_ascii=False)
_NO_KEY = {"/health"}                  # the tray polls it before it has read the key
_ALWAYS = {"/health", "/shutdown", "/v1/models"}     # answer even while the model is not ready
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


def _seconds(samples: int) -> float:
    return round(samples / SAMPLE_RATE, 3)


async def models(request: web.Request) -> web.Response:
    """Which models there are: the main one, then those beside it. A program that sees this
    answer knows that a file may name its model."""
    engine = request.app[ENGINE]
    main = {"id": engine.model_name, "language": engine.language, "main": True,
            "installed": engine.status != "error", "loaded": engine.status == "ok"}
    others = [dict(told, main=False) for told in (engine.extras.told() if engine.extras is not None else [])]
    data = [{"id": told["id"], "object": "model", "language": told["language"], "main": told["main"],
             "installed": told["installed"], "loaded": told["loaded"]} for told in [main, *others]]
    return web.json_response({"object": "list", "data": data}, dumps=_dumps)


async def acquired(extras: Extras, name: str) -> Callable[[np.ndarray], str]:
    """The recognizer of a model beside the main one, loaded first if it has to be - in a thread
    of its own: loading takes seconds, and nothing else waits for it. The caller says
    extras.release(name) when it is done. A caller that is cancelled while the model is being
    loaded - the gateway is stopping - leaves it released as soon as it is there. (A client
    that merely hangs up cancels nothing: its file is read to the end, as files always were.)"""
    loading = asyncio.get_running_loop().run_in_executor(None, extras.acquire, name)
    try:
        return await asyncio.shield(loading)
    except asyncio.CancelledError:
        loading.add_done_callback(lambda done: done.exception() is None and extras.release(name))
        raise


async def recognize_phrases(engine: Engine, samples: np.ndarray,
                            recognize: Callable[[np.ndarray], str] | None = None) -> list[tuple[Final, str]]:
    """Cut by pauses and recognize every phrase on the one worker thread: the phrases in
    order, each with its text. `recognize` is the recognizer of another model, if the file
    asked for one."""
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

        engine.worker.submit(Job(session, final.item, True, final.samples, deliver, background=True, recognize=recognize))
    try:
        texts = await asyncio.gather(*futures)
    finally:
        engine.worker.forget(session)  # the caller hung up: do not recognize the rest
    return list(zip(finals, texts))


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
    if response_format not in ("json", "text", "verbose_json"):
        return _problem(400, "invalid_request", "response_format must be 'json', 'text' or 'verbose_json'")
    try:
        rate, samples = wav.decode(data)
    except wav.WavError as e:
        return _problem(400, "invalid_request", f"cannot read the file as WAV: {e}")
    if rate != SAMPLE_RATE:
        return _problem(400, "invalid_request", f"only {SAMPLE_RATE} Hz WAV is supported, got {rate} Hz")
    # A file may name a model beside the main one. Any other name means the main model, as it
    # always did: programs made for other services name models of theirs.
    engine, name = request.app[ENGINE], fields.get("model", "")
    extras = engine.extras if engine.extras is not None and engine.extras.known(name) else None
    recognize = None
    if extras is not None:
        try:
            recognize = await acquired(extras, name)
        except NotInstalled:
            return _problem(409, "model_not_installed", f"the files of the model {name!r} are not on this computer")
        except ModelFailed as e:
            log.warning("model %s could not be loaded: %s", name, e)
            return _problem(500, "model_failed", f"the model {name!r} could not be loaded: {e}")
    try:
        # a phrase the model found no words in is noise: it is in no answer
        phrases = [(final, text) for final, text in await recognize_phrases(engine, samples, recognize) if text]
    finally:
        if extras is not None:
            extras.release(name)
    text = " ".join(text for _, text in phrases)
    if response_format == "text":
        return web.Response(text=text)
    if response_format == "json":
        return web.json_response({"text": text}, dumps=_dumps)
    # the phrases with their places in the file, in seconds: for a caller that puts several
    # recordings on one timeline
    segments = [{"id": n, "start": _seconds(final.start), "end": _seconds(final.end), "text": text}
                for n, (final, text) in enumerate(phrases)]
    return web.json_response({"text": text, "duration": _seconds(len(samples)), "segments": segments},
                             dumps=_dumps)


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
    app.router.add_get("/v1/models", models)
    app.router.add_get("/v1/realtime", realtime)
    app.router.add_post("/shutdown", shutdown)
    return app
