"""Cut a stream into phrases: drafts while speech runs, a final when the VAD closes it.

Time is counted in samples and never read from a clock, so a file pushed in
one go and a live stream behave the same, and the tests are deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16000
# Kept from before speech is detected: the VAD notices a word ~0.3 s late,
# so half a second of pre-roll keeps the first syllable.
PREROLL_WINDOWS = 16
# The VAD is asked to split at max_phrase_s; this is the backstop if it never finds a pause.
HARD_LIMIT_S = 30.0


class Vad(Protocol):
    window: int

    def accept(self, window: np.ndarray) -> None: ...
    def is_speech(self) -> bool: ...
    def pop_segments(self) -> list[np.ndarray]: ...
    def flush(self) -> None: ...
    def reset(self) -> None: ...


@dataclass
class Draft:
    item: int
    samples: np.ndarray


@dataclass
class Final:
    item: int
    samples: np.ndarray


class Segmenter:
    def __init__(self, vad: Vad, draft_interval_s: float = 1.0) -> None:
        self._vad = vad
        self._draft_every = int(draft_interval_s * SAMPLE_RATE)
        self._hard_limit = int(HARD_LIMIT_S * SAMPLE_RATE)
        self._item = 1
        self._clear()

    def _clear(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._fed = 0  # how much of _buf the VAD has seen
        self._started = False
        self._since_draft = 0

    def _next_phrase(self) -> None:
        self._buf = self._buf[self._fed:]  # keep what the VAD has not seen yet
        self._fed = 0
        self._started = False
        self._since_draft = 0

    def feed(self, samples: np.ndarray) -> list[Draft | Final]:
        out: list[Draft | Final] = []
        self._buf = np.concatenate([self._buf, np.asarray(samples, dtype=np.float32)])
        w = self._vad.window
        while self._fed + w <= len(self._buf):
            self._vad.accept(self._buf[self._fed:self._fed + w])
            self._fed += w
            if self._started:
                self._since_draft += w
            elif self._vad.is_speech():
                self._started = True
                self._since_draft = 0
            closed = self._vad.pop_segments()
            if closed:
                # The VAD only signals the end. The phrase itself comes from our own
                # buffer: the VAD's segment starts late and loses the first syllable
                # (measured: "Чьих" instead of "Ничьих").
                samples = self._buf[:self._fed].copy() if self._started else np.concatenate(closed)
                out.append(Final(self._item, samples))
                self._item += 1
                self._next_phrase()
            elif self._started and self._fed >= self._hard_limit:
                out.append(Final(self._item, self._buf[:self._fed].copy()))
                self._item += 1
                self._vad.reset()
                self._next_phrase()
            elif self._started and self._since_draft >= self._draft_every:
                out.append(Draft(self._item, self._buf[:self._fed].copy()))
                self._since_draft = 0
            elif not self._started and self._fed > PREROLL_WINDOWS * w:
                drop = self._fed - PREROLL_WINDOWS * w
                self._buf = self._buf[drop:]
                self._fed -= drop
        return out

    def commit(self) -> list[Final]:
        """No more audio is coming: close whatever phrase is open."""
        self._vad.flush()
        leftover = self._vad.pop_segments()
        out = []
        if self._started and len(self._buf):
            out.append(Final(self._item, self._buf.copy()))  # the tail the VAD has not seen included
        elif leftover:
            out.append(Final(self._item, np.concatenate(leftover)))
        self._item += len(out)
        self._vad.reset()
        self._clear()
        return out
