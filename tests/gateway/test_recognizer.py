import numpy as np
import pytest

from tolmach import config
from tolmach.gateway import wav
from tolmach.gateway.recognizer import MIN_SAMPLES, SAMPLE_RATE, ModelError, Recognizer


class FakeStream:
    def __init__(self):
        self.fed = None

        class Result:
            text = "  привет  "

        self.result = Result()

    def accept_waveform(self, rate, samples):
        self.fed = (rate, samples)


class FakeEngine:
    def __init__(self):
        self.streams = []
        self.decoded = 0

    def create_stream(self):
        self.streams.append(FakeStream())
        return self.streams[-1]

    def decode_stream(self, stream):
        self.decoded += 1


def test_unsupported_model_type():
    cfg = config.GatewayConfig()
    cfg.model.type = "whisper"
    with pytest.raises(ModelError, match="unsupported model type 'whisper'"):
        Recognizer.load(cfg)


def test_missing_files_are_named():
    with pytest.raises(ModelError) as e:
        Recognizer.load(config.GatewayConfig())
    message = str(e.value)
    for role in ("encoder", "decoder", "joiner", "tokens"):
        assert role in message
    assert "gigaam_v3_e2e_rnnt_tokens.txt" in message


def test_too_short_audio_never_reaches_the_engine():
    engine = FakeEngine()
    r = Recognizer(engine)
    assert r.recognize(np.zeros(0, dtype=np.float32)) == ""
    assert r.recognize(np.zeros(MIN_SAMPLES - 1, dtype=np.float32)) == ""
    assert engine.streams == []


def test_recognize_feeds_float32_at_16k_and_strips():
    engine = FakeEngine()
    r = Recognizer(engine)
    text = r.recognize(np.zeros(SAMPLE_RATE, dtype=np.float64))
    assert text == "привет"
    rate, samples = engine.streams[0].fed
    assert rate == SAMPLE_RATE
    assert samples.dtype == np.float32
    assert engine.decoded == 1


@pytest.mark.model
def test_real_model_reads_pushkin(real_models, benchmark_wav):
    cfg = config.GatewayConfig()
    for role in ("encoder", "decoder", "joiner", "tokens"):
        setattr(cfg.model, role, str(real_models / getattr(cfg.model, role)))
    r = Recognizer.load(cfg)
    rate, samples = wav.decode(benchmark_wav.read_bytes())
    assert rate == SAMPLE_RATE
    text = r.recognize(samples)
    assert "лукоморья" in text.lower()
    assert r.recognize(np.zeros(SAMPLE_RATE // 5, dtype=np.float32)) == ""
