"""Gateway stream of one dictation session over websocket-client."""
from __future__ import annotations

import base64
import json
import logging
import threading
from typing import Callable

import numpy as np
import websocket

from tolmach import config, keyfile
from tolmach.client import events as ev

log = logging.getLogger("tolmach")

CONNECT_TIMEOUT_S = 2.0
# A stalled gateway must not freeze the controller: a send that cannot go out
# in this time fails the stream, and the reader just reads again.
IO_TIMEOUT_S = 5.0
FATAL_CODES = {"unsupported_sample_rate", "not_ready"}

DELTA = "conversation.item.input_audio_transcription.delta"
COMPLETED = "conversation.item.input_audio_transcription.completed"
COMMITTED = "input_audio_buffer.committed"


def session_update() -> str:
    return json.dumps({
        "type": "session.update",
        "session": {
            "input_audio_format": "pcm16",
            "input_audio_sample_rate": 16000,
            "turn_detection": {"type": "server_vad"},
        },
    })


def append_message(samples: np.ndarray) -> str:
    audio = base64.b64encode(samples.astype("<i2").tobytes()).decode("ascii")
    return json.dumps({"type": "input_audio_buffer.append", "audio": audio})


def commit_message() -> str:
    return json.dumps({"type": "input_audio_buffer.commit"})


def parse_server_event(session: int, raw):
    """Translate one gateway message into a controller event, or None to skip it."""
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(message, dict):
        return None
    kind = message.get("type")
    if kind == DELTA:
        return ev.Draft(session, str(message.get("transcript") or ""))
    if kind == COMPLETED:
        return ev.Phrase(session, str(message.get("transcript") or ""))
    if kind == COMMITTED:
        return ev.Committed(session)
    if kind == "error":
        error = message.get("error")
        if not isinstance(error, dict):
            error = {}
        code = str(error.get("code") or "")
        if code in FATAL_CODES:
            return ev.StreamFailed(session, f"{code}: {error.get('message') or ''}")
        log.warning("gateway reported %s", code or "an error")
    return None


class GatewayStream:
    """One WebSocket connection. send/commit/close never raise: a broken
    connection is reported once, as StreamFailed, through `post`."""

    def __init__(self, session: int, ws, post: Callable):
        self._session = session
        self._ws = ws
        self._post = post
        self._closing = False
        self._failed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read, name=f"stream-{session}", daemon=True)
        self._thread.start()

    def send(self, samples: np.ndarray) -> None:
        self._send(append_message(samples))

    def commit(self) -> None:
        self._send(commit_message())

    def close(self) -> None:
        """Return at once: no closing handshake, which would wait for the reader's
        lock; shutting the socket down wakes a reader blocked in recv."""
        self._closing = True
        try:
            self._ws.abort()
        except Exception:
            pass

    def _send(self, text: str) -> None:
        if self._closing or self._failed:
            return
        try:
            self._ws.send(text)
        except Exception as e:
            self._fail(f"send failed: {e}")

    def _fail(self, reason: str) -> None:
        with self._lock:
            if self._closing or self._failed:
                return
            self._failed = True
        self._post(ev.StreamFailed(self._session, reason))

    def _read(self) -> None:
        try:
            self._read_loop()
        finally:
            try:
                self._ws.shutdown()
            except Exception:
                pass

    def _read_loop(self) -> None:
        while not self._closing:
            try:
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                self._fail(f"connection lost: {e}")
                return
            if not raw:  # websocket-client returns "" once the peer has closed
                self._fail("connection closed by the gateway")
                return
            event = parse_server_event(self._session, raw)
            if isinstance(event, ev.StreamFailed):
                self._fail(event.reason)
                return
            if event is not None:
                self._post(event)


class StreamFactory:
    """The controller's `streams` port: reads the gateway address and key anew for each session."""

    def __init__(self, post: Callable):
        self._post = post

    def open(self, session: int) -> GatewayStream:
        gateway = config.load().config.gateway
        key = keyfile.read_key()
        if key is None:
            raise ev.StreamError("no gateway key yet: the gateway has never run")
        url = f"ws://{gateway.host}:{gateway.port}/v1/realtime"
        try:
            ws = websocket.create_connection(
                url, timeout=CONNECT_TIMEOUT_S, header=[f"Authorization: Bearer {key}"])
            ws.send(session_update())
            ws.settimeout(IO_TIMEOUT_S)
        except Exception as e:
            raise ev.StreamError(f"{url}: {e}") from e
        return GatewayStream(session, ws, self._post)
