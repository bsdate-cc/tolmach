"""Signal level helpers: RMS in dBFS and the muted-microphone check."""
from __future__ import annotations

import numpy as np

SILENCE_DBFS = -120.0


def dbfs(samples: np.ndarray) -> float:
    """RMS level of int16 samples in dBFS with DC removed; no signal is SILENCE_DBFS."""
    if samples.size == 0:
        return SILENCE_DBFS
    x = samples.astype(np.float64) / 32768.0
    x = x - x.mean()
    rms = float(np.sqrt(np.mean(x * x)))
    if rms <= 1e-6:
        return SILENCE_DBFS
    return 20.0 * float(np.log10(rms))


class FloorCheck:
    """Decides once, after `window_s` of audio, whether the microphone is muted.

    feed() returns None while undecided and after the decision, True when every
    chunk of the window stayed at or below the floor, False otherwise.
    """

    def __init__(self, floor_dbfs: float, window_s: float = 0.5, rate: int = 16000):
        self._floor = floor_dbfs
        self._need = int(window_s * rate)
        self._seen = 0
        self._above = False
        self._decided = False

    def feed(self, samples: np.ndarray) -> bool | None:
        if self._decided:
            return None
        self._seen += int(samples.size)
        if dbfs(samples) > self._floor:
            self._above = True
        if self._seen < self._need:
            return None
        self._decided = True
        return not self._above
