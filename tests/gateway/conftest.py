import base64
import io
import wave

import numpy as np
import pytest

from tolmach.gateway.state import Engine
from tolmach.gateway.worker import Worker
from tests.gateway.fakes import WholeVad, text_of

AUTH = {"Authorization": "Bearer secret"}
UPDATE = {"type": "session.update", "session": {
    "input_audio_format": "pcm16", "input_audio_sample_rate": 16000,
    "turn_detection": {"type": "server_vad"}}}


class Switch:
    """A recognizer the test can swap mid-flight."""

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, samples):
        return self.fn(samples)


class SpyWorker(Worker):
    def __init__(self, recognize):
        super().__init__(recognize)
        self.forgotten = []

    def forget(self, session):
        self.forgotten.append(session)
        super().forget(session)


@pytest.fixture
def recognize():
    return Switch(text_of)


@pytest.fixture
def engine(recognize):
    worker = SpyWorker(recognize)
    worker.start()
    yield Engine(model_name="fake", status="ok", worker=worker, make_vad=WholeVad)
    worker.stop()


def pcm(seconds: float, value: int = 3000) -> np.ndarray:
    return np.full(int(seconds * 16000), value, dtype="<i2")


def append_event(seconds: float) -> dict:
    return {"type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm(seconds).tobytes()).decode("ascii")}


def wav_bytes(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.full(int(seconds * rate), 3000, dtype="<i2").tobytes())
    return buf.getvalue()
