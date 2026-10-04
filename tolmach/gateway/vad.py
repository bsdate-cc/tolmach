"""Silero VAD from sherpa-onnx behind the Segmenter's Vad protocol."""
from __future__ import annotations

import numpy as np

from tolmach.config import GatewayConfig, resolve_model_path
from tolmach.gateway.recognizer import SAMPLE_RATE, ModelError


class SileroVad:
    def __init__(self, cfg: GatewayConfig) -> None:
        path = resolve_model_path(cfg.vad.model)
        if not path.is_file():
            raise ModelError(f"VAD model not found: {path}")
        import sherpa_onnx  # deferred: heavy, and unit tests never need it

        c = sherpa_onnx.VadModelConfig()
        c.silero_vad.model = str(path)
        c.silero_vad.threshold = cfg.vad.threshold
        c.silero_vad.min_silence_duration = cfg.vad.min_silence_s
        c.silero_vad.min_speech_duration = cfg.vad.min_speech_s
        # Past this length the VAD itself looks for the nearest short pause.
        c.silero_vad.max_speech_duration = cfg.max_phrase_s
        c.sample_rate = SAMPLE_RATE
        self.window: int = c.silero_vad.window_size
        self._vad = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=60)

    def accept(self, window: np.ndarray) -> None:
        self._vad.accept_waveform(window)

    def is_speech(self) -> bool:
        return self._vad.is_speech_detected()

    def pop_segments(self) -> list[np.ndarray]:
        out = []
        while not self._vad.empty():
            out.append(np.asarray(self._vad.front.samples, dtype=np.float32))
            self._vad.pop()
        return out

    def flush(self) -> None:
        self._vad.flush()

    def reset(self) -> None:
        self._vad.reset()
