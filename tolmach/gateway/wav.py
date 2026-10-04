"""WAV bytes -> mono float32. PCM16 only; anything else is the caller's 400."""
from __future__ import annotations

import io
import wave

import numpy as np


class WavError(ValueError):
    pass


def decode(data: bytes) -> tuple[int, np.ndarray]:
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                raise WavError("only 16-bit PCM WAV is supported")
            rate, channels = w.getframerate(), w.getnchannels()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise WavError(f"not a WAV file: {e}") from e
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        whole = len(samples) // channels * channels
        samples = samples[:whole].reshape(-1, channels).mean(axis=1)
    return rate, samples
