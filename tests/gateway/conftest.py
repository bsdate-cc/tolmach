import base64
import io
import threading
import wave
import weakref

import numpy as np
import pytest

from tolmach.config import ModelConfig
from tolmach.gateway.extras import Extras
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


class Shelf:
    """A model beside the main one, "english". What loads it is a stand-in: its recognizer answers
    with the name of the model and the length it was given."""

    def __init__(self):
        self.now = 1000.0
        self.loads: list[str] = []
        self.made: list[weakref.ref] = []           # what each load made: to see that nothing holds it afterwards
        self.missing: set[str] = set()              # the models whose files are not there
        self.broken: Exception | None = None
        self.gate: threading.Event | None = None    # a load waits for it, when a test wants loading to take a while
        english = ModelConfig(name="english", language="en", encoder="e", decoder="d", joiner="j", tokens="t")
        self.extras = Extras([english], self._load, 600.0, clock=lambda: self.now,
                             installed=lambda model: model.name not in self.missing)

    def _load(self, model):
        self.loads.append(model.name)
        if self.gate is not None:
            assert self.gate.wait(5)
        if self.broken is not None:
            raise self.broken
        heard = Heard(model.name)
        self.made.append(weakref.ref(heard))
        return heard.recognize


class Heard:
    """Stands in for the recognizer of a model: answers with the name of the model and the length it was given."""

    def __init__(self, name: str) -> None:
        self.name = name

    def recognize(self, samples) -> str:
        return f"{self.name}:{len(samples)}"


@pytest.fixture
def shelf():
    return Shelf()


@pytest.fixture
def engine(recognize, shelf):
    worker = SpyWorker(recognize)
    worker.start()
    yield Engine(model_name="fake", language="ru", status="ok", worker=worker, make_vad=WholeVad, extras=shelf.extras)
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
