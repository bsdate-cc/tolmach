"""The /v1/realtime dialect: parse what a client sends, build what we answer.

A subset of the OpenAI Realtime events — the same one the phone gateway
speaks, so one client fits both.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

import numpy as np

SUPPORTED_RATE = 16000
# What an OpenAI client sends when it says nothing about the rate.
DEFAULT_RATE = 24000


class ProtocolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class SessionUpdate:
    sample_rate: int
    session: dict


@dataclass
class Append:
    samples: np.ndarray  # float32 mono


@dataclass
class Commit:
    pass


def _invalid(message: str) -> ProtocolError:
    return ProtocolError("invalid_event", message)


def parse(text: str) -> SessionUpdate | Append | Commit:
    try:
        msg = json.loads(text)
    except json.JSONDecodeError as e:
        raise _invalid(f"not JSON: {e}") from e
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise _invalid("an event is an object with a string 'type'")
    kind = msg["type"]

    if kind == "session.update":
        session = msg.get("session")
        if not isinstance(session, dict):
            raise _invalid("session.update needs a 'session' object")
        fmt = session.get("input_audio_format", "pcm16")
        if fmt != "pcm16":
            raise _invalid(f"unsupported input_audio_format {fmt!r}; only 'pcm16'")
        rate = session.get("input_audio_sample_rate", DEFAULT_RATE)
        if not isinstance(rate, int) or isinstance(rate, bool):
            raise _invalid("input_audio_sample_rate must be an integer")
        return SessionUpdate(rate, session)

    if kind == "input_audio_buffer.append":
        audio = msg.get("audio")
        if not isinstance(audio, str):
            raise _invalid("append needs a base64 'audio' string")
        try:
            raw = base64.b64decode(audio, validate=True)
        except (binascii.Error, ValueError) as e:
            raise _invalid(f"audio is not base64: {e}") from e
        if len(raw) % 2:
            raise _invalid("audio must be whole 16-bit samples")
        return Append(np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0)

    if kind == "input_audio_buffer.commit":
        return Commit()

    raise ProtocolError("unknown_event", f"unknown event type {kind!r}")


def session_created(session_id: str) -> dict:
    return {"type": "session.created", "session": {"id": session_id}}


def session_updated(session: dict) -> dict:
    return {"type": "session.updated", "session": session}


def delta(item: int, transcript: str) -> dict:
    # `delta` stays empty on purpose: the whole draft is in `transcript`, and
    # the client replaces what it showed instead of appending.
    return {"type": "conversation.item.input_audio_transcription.delta",
            "item_id": f"item_{item}", "delta": "", "transcript": transcript}


def completed(item: int, transcript: str) -> dict:
    return {"type": "conversation.item.input_audio_transcription.completed",
            "item_id": f"item_{item}", "transcript": transcript}


def committed() -> dict:
    return {"type": "input_audio_buffer.committed"}


def error(code: str, message: str) -> dict:
    return {"type": "error", "error": {"code": code, "message": message}}
