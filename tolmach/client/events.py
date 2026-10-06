"""Events of the client queue and the two errors a start can hit.

Producers (button, hotkeys, menu, overlay, recorder, gateway stream, ticker) only put
these on the queue; the controller is the only consumer.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class RecorderError(Exception):
    """The microphone could not be found or opened."""


class StreamError(Exception):
    """The gateway could not be reached."""


@dataclass(frozen=True)
class Toggle:
    source: str  # "button" | "hotkey" | "menu"
    at: float | None = None  # time.monotonic() of the press, for the start-up time in the log


@dataclass(frozen=True)
class InsertLast:
    """Put the last recognised text into the window in front once more."""
    source: str  # "hotkey" | "menu"


@dataclass(frozen=True)
class Cancel:
    """Drop the dictation in progress: nothing of it is typed or kept."""
    source: str  # "hotkey" | "overlay"


@dataclass(frozen=True)
class ButtonConnected:
    """The button is back after having been away (unplugged, or its HID handle was lost):
    a press may have gone unseen and the microphone may have lost power meanwhile."""


@dataclass(frozen=True, eq=False)
class AudioChunk:
    session: int
    samples: np.ndarray  # int16 mono, 16 kHz


@dataclass(frozen=True)
class Draft:
    session: int
    text: str


@dataclass(frozen=True)
class Phrase:
    session: int
    text: str


@dataclass(frozen=True)
class Committed:
    session: int


@dataclass(frozen=True)
class StreamFailed:
    session: int
    reason: str


@dataclass(frozen=True)
class Tick:
    pass


@dataclass(frozen=True)
class Quit:
    pass
