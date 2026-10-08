"""python -m tolmach.gateway - open the port at once, load the model behind it.

The port comes first so the tray can tell "still starting" from "not running".
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from typing import Callable

import numpy as np
from aiohttp import web

from tolmach import VERSION, config, keyfile, logsetup, paths, terms
from tolmach.gateway.app import build_app
from tolmach.gateway.extras import Extras
from tolmach.gateway.recognizer import ModelError, Recognizer, installed
from tolmach.gateway.state import STOP, Engine
from tolmach.gateway.vad import SileroVad
from tolmach.gateway.worker import Worker

log = logging.getLogger("tolmach.gateway")

IDLE_EVERY_S = 5.0      # how often the gateway looks whether a model beside the main one has idled long enough


def corrected(recognize: Callable[[np.ndarray], str], dictionary: terms.Dictionary) -> Callable[[np.ndarray], str]:
    """Recognition, then the terms dictionary. The worker runs this for every piece of speech -
    a draft, a finished phrase, a phrase of an uploaded file - so all of them read the same."""
    def run(samples: np.ndarray) -> str:
        return dictionary.apply(recognize(samples))
    return run


def make_engine(cfg: config.GatewayConfig, clock: Callable[[], float] = time.monotonic) -> Engine:
    """The engine before anything is loaded: the main model is loaded behind the open port, a
    model beside it only when a request asks for it."""
    def load(model: config.ModelConfig) -> Callable[[np.ndarray], str]:
        # No terms dictionary here: that one is written for what the main model hears.
        recognizer = Recognizer.of(model, cfg.threads)
        log.info("model %s loaded, %d threads", model.name, cfg.threads)
        return recognizer.recognize

    # A main model may bear the name of one of the list: then that name means the main model, as any name of it does.
    beside = [model for model in cfg.extra_models if model.name != cfg.model.name]
    extras = Extras(beside, load, cfg.extra_idle_minutes * 60.0, installed=lambda model: installed(model), clock=clock)
    return Engine(model_name=cfg.model.name, language=cfg.model.language, draft_interval_s=cfg.draft_interval_s,
                  extras=extras)


async def let_go_of_idle(extras: Extras, every_s: float) -> None:
    """For as long as the gateway runs: a model beside the main one that nobody has asked for
    in a while is let go of, and its memory with it."""
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(every_s)
        try:
            name = await loop.run_in_executor(None, extras.sweep)
        except Exception:       # whatever went wrong, the next look is taken all the same
            log.exception("a look for idle models failed")
            continue
        if name:
            log.info("model %s is let go of: nobody has asked for it for a while", name)


def load_engine(engine: Engine, cfg: config.GatewayConfig) -> None:
    """Runs in a thread. Fills the engine in, or records why it could not."""
    try:
        recognizer = Recognizer.load(cfg)
        SileroVad(cfg)  # fail now, not on the first connection
    except ModelError as e:
        engine.error = str(e)
        engine.status = "error"
        log.error("model: %s", e)
        return
    except Exception as e:
        engine.error = f"unexpected failure while loading the model: {e}"
        engine.status = "error"
        log.exception("model load crashed")
        return
    worker = Worker(corrected(recognizer.recognize, terms.Dictionary(paths.terms_file())))
    worker.start()
    engine.worker = worker
    engine.make_vad = lambda: SileroVad(cfg)
    engine.status = "ok"
    log.info("model %s loaded, %d threads", cfg.model.name, cfg.threads)


async def serve(app: web.Application, engine: Engine, cfg: config.GatewayConfig) -> None:
    # A short shutdown timeout: an open dictation socket must not hold "Stop gateway" for a minute.
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=2.0)
    await runner.setup()
    try:
        await web.TCPSite(runner, cfg.host, cfg.port).start()  # OSError when the port is taken
    except OSError:
        await runner.cleanup()
        raise
    record = paths.gateway_file()
    record.write_text(json.dumps({"pid": os.getpid(), "port": cfg.port, "version": VERSION}),
                      encoding="utf-8")
    log.info("listening on %s:%d", cfg.host, cfg.port)
    loading = asyncio.get_running_loop().run_in_executor(None, load_engine, engine, cfg)
    idle = asyncio.create_task(let_go_of_idle(engine.extras, IDLE_EVERY_S)) if engine.extras is not None else None
    try:
        await app[STOP].wait()
    finally:
        if idle is not None:
            idle.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await idle
        await runner.cleanup()
        await loading
        if engine.worker is not None:
            engine.worker.stop()
        with contextlib.suppress(OSError):
            record.unlink()
        log.info("stopped")


def _run() -> int:
    loaded = config.load()
    for problem in loaded.problems:
        log.warning("config: %s: %s", problem.where, problem.message)
    cfg = loaded.config.gateway
    engine = make_engine(cfg)
    app = build_app(engine, keyfile.ensure_key())
    try:
        asyncio.run(serve(app, engine, cfg))
    except OSError as e:
        log.error("cannot listen on %s:%d: %s", cfg.host, cfg.port, e)
        return 1
    return 0


def main() -> int:
    logsetup.setup("gateway")  # if this fails there is nowhere to report to
    try:
        return _run()
    except KeyboardInterrupt:
        return 0
    except Exception:
        # Under pythonw nothing else would ever show this.
        log.exception("gateway crashed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
