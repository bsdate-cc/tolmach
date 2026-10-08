"""A transducer model through sherpa-onnx: load once, recognize float32 16 kHz mono."""
from __future__ import annotations

import numpy as np

from tolmach.config import GatewayConfig, ModelConfig, resolve_model_path

SAMPLE_RATE = 16000
# The engine raises on empty input, and 0.1 s is shorter than any word.
MIN_SAMPLES = SAMPLE_RATE // 10

_ROLES = ("encoder", "decoder", "joiner", "tokens")


class ModelError(RuntimeError):
    pass


def installed(model: ModelConfig) -> bool:
    """Is every file of the model on this computer?"""
    return all(resolve_model_path(getattr(model, role)).is_file() for role in _ROLES)


class Recognizer:
    def __init__(self, engine) -> None:
        self._engine = engine

    @classmethod
    def load(cls, cfg: GatewayConfig) -> "Recognizer":
        """The main model of the gateway."""
        return cls.of(cfg.model, cfg.threads)

    @classmethod
    def of(cls, m: ModelConfig, threads: int) -> "Recognizer":
        if m.type != "nemo_transducer":
            raise ModelError(f"unsupported model type {m.type!r}; only 'nemo_transducer' is supported")
        files = {role: resolve_model_path(getattr(m, role)) for role in _ROLES}
        missing = [f"{role}: {path}" for role, path in files.items() if not path.is_file()]
        if missing:
            raise ModelError("model files not found - " + "; ".join(missing))
        import sherpa_onnx  # deferred: heavy, and unit tests never need it

        try:
            engine = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(files["encoder"]),
                decoder=str(files["decoder"]),
                joiner=str(files["joiner"]),
                tokens=str(files["tokens"]),
                num_threads=threads,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                model_type="nemo_transducer",
                provider="cpu",
            )
        except Exception as e:  # sherpa raises plain RuntimeError for anything it dislikes
            raise ModelError(f"model failed to load: {e}") from e
        return cls(engine)

    def recognize(self, samples: np.ndarray) -> str:
        if len(samples) < MIN_SAMPLES:
            return ""
        stream = self._engine.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(samples, dtype=np.float32))
        self._engine.decode_stream(stream)
        return stream.result.text.strip()
