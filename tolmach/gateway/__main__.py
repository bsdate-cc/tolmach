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
from typing import Callable

import numpy as np
from aiohttp import web

from tolmach import VERSION, config, keyfile, logsetup, paths, terms
from tolmach.gateway.app import build_app
from tolmach.gateway.recognizer import ModelError, Recognizer
from tolmach.gateway.state import STOP, Engine
from tolmach.gateway.vad import SileroVad
from tolmach.gateway.worker import Worker

log = logging.getLogger("tolmach.gateway")


def corrected(recognize: Callable[[np.ndarray], str], dictionary: terms.Dictionary) -> Callable[[np.ndarray], str]:
    """Recognition, then the terms dictionary. The worker runs this for every piece of speech -
    a draft, a finished phrase, a phrase of an uploaded file - so all of them read the same."""
    def run(samples: np.ndarray) -> str:
        return dictionary.apply(recognize(samples))
    return run


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
    try:
        await app[STOP].wait()
    finally:
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
    engine = Engine(model_name=cfg.model.name, draft_interval_s=cfg.draft_interval_s)
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
