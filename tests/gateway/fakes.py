"""Stand-ins for the VAD and the recognizer, shared by the gateway tests."""
from __future__ import annotations

import numpy as np

WINDOW = 512


class ScriptedVad:
    """Says exactly what the test scripted, counted in windows from 1."""

    window = WINDOW

    def __init__(self, speech=(), close_after=None):
        self.n = 0                              # windows accepted so far
        self.speech = set(speech)               # window numbers during which speech is on
        self.close_after = dict(close_after or {})  # window number -> the segment that closes there
        self.on_flush: list[np.ndarray] = []    # segments a flush() releases
        self.resets = 0
        self._ready: list[np.ndarray] = []

    def accept(self, window):
        assert len(window) == self.window
        self.n += 1
        if self.n in self.close_after:
            self._ready.append(self.close_after[self.n])

    def is_speech(self):
        return self.n in self.speech

    def pop_segments(self):
        out, self._ready = self._ready, []
        return out

    def flush(self):
        self._ready.extend(self.on_flush)
        self.on_flush = []

    def reset(self):
        self.resets += 1


class WholeVad:
    """Everything is speech; the only segment is everything fed, released by flush()."""

    window = WINDOW

    def __init__(self):
        self._windows: list[np.ndarray] = []
        self._ready: list[np.ndarray] = []

    def accept(self, window):
        self._windows.append(np.array(window, dtype=np.float32))

    def is_speech(self):
        return bool(self._windows)

    def pop_segments(self):
        out, self._ready = self._ready, []
        return out

    def flush(self):
        if self._windows:
            self._ready.append(np.concatenate(self._windows))
            self._windows = []

    def reset(self):
        self._windows = []


def text_of(samples) -> str:
    """A recognizer that answers with the length it was given."""
    return f"len{len(samples)}"


def silent(samples) -> str:
    """A recognizer that heard nothing."""
    return ""


def tone(n: int, value: float = 0.1) -> np.ndarray:
    return np.full(n, value, dtype=np.float32)
