"""WS /v1/realtime: one connection is one dictation session."""
from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import uuid

from aiohttp import WSMsgType, web

from tolmach.gateway import protocol
from tolmach.gateway.protocol import Append, Commit, ProtocolError, SessionUpdate
from tolmach.gateway.segmenter import Draft, Final, Segmenter
from tolmach.gateway.state import ENGINE, Engine
from tolmach.gateway.worker import Job

# Cyrillic goes out as it is, not as \u escapes: a third of the bytes.
_dumps = functools.partial(json.dumps, ensure_ascii=False)
_NEED_RATE = (f"only {protocol.SUPPORTED_RATE} Hz audio is supported; send session.update with "
              f"input_audio_sample_rate={protocol.SUPPORTED_RATE} before any audio")


class _Session:
    def __init__(self, ws: web.WebSocketResponse, engine: Engine) -> None:
        self.id = f"ws-{uuid.uuid4().hex}"
        self._ws = ws
        self._engine = engine
        self._loop = asyncio.get_running_loop()
        self._segmenter: Segmenter | None = None  # exists once a valid session.update came
        self._results: asyncio.Queue = asyncio.Queue()
        self._pending_finals = 0
        self._committing = False

    async def send(self, event: dict) -> None:
        if self._ws.closed:
            return
        with contextlib.suppress(ConnectionResetError):  # the client left mid-send
            await self._ws.send_json(event, dumps=_dumps)

    def _deliver(self, job: Job, text: str) -> None:
        # worker thread -> event loop
        self._loop.call_soon_threadsafe(self._results.put_nowait, (job, text))

    def _submit(self, action: Draft | Final) -> None:
        final = isinstance(action, Final)
        if final:
            self._pending_finals += 1
        self._engine.worker.submit(Job(self.id, action.item, final, action.samples, self._deliver))

    async def pump(self) -> None:
        """Turn recognition results into events, in the order the worker produced them."""
        while True:
            job, text = await self._results.get()
            if job.final:
                self._pending_finals -= 1
                if text:
                    await self.send(protocol.completed(job.item, text))
                await self._maybe_committed()
            elif text:
                await self.send(protocol.delta(job.item, text))

    async def _maybe_committed(self) -> None:
        if self._committing and self._pending_finals == 0:
            self._committing = False
            await self.send(protocol.committed())

    async def handle(self, text: str) -> None:
        try:
            event = protocol.parse(text)
        except ProtocolError as e:
            await self.send(protocol.error(e.code, e.message))
            return
        if isinstance(event, SessionUpdate):
            if event.sample_rate != protocol.SUPPORTED_RATE:
                await self.send(protocol.error("unsupported_sample_rate", _NEED_RATE))
                return
            if self._segmenter is None:
                self._segmenter = Segmenter(self._engine.make_vad(), self._engine.draft_interval_s)
            await self.send(protocol.session_updated(event.session))
        elif isinstance(event, Append):
            if self._segmenter is None:
                # Guessing the rate would turn speech into garbage text; refuse out loud.
                await self.send(protocol.error("unsupported_sample_rate", _NEED_RATE))
                return
            for action in self._segmenter.feed(event.samples):
                self._submit(action)
        elif isinstance(event, Commit):
            if self._segmenter is not None:
                for action in self._segmenter.commit():
                    self._submit(action)
            self._committing = True
            await self._maybe_committed()

    def close(self) -> None:
        self._engine.worker.forget(self.id)


async def realtime(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=8 * 1024 * 1024)
    await ws.prepare(request)
    session = _Session(ws, request.app[ENGINE])
    pump = asyncio.create_task(session.pump())
    try:
        await session.send(protocol.session_created(session.id))
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await session.handle(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await session.send(protocol.error("invalid_event", "events are JSON text frames"))
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        session.close()
    return ws
