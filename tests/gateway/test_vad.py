import pytest

from tolmach import config
from tolmach.gateway import wav
from tolmach.gateway.recognizer import SAMPLE_RATE, ModelError
from tolmach.gateway.segmenter import Draft, Final, Segmenter
from tolmach.gateway.vad import SileroVad


def test_missing_vad_model_is_a_model_error():
    with pytest.raises(ModelError, match="silero_vad.onnx"):
        SileroVad(config.GatewayConfig())


@pytest.mark.model
def test_real_vad_cuts_the_benchmark_into_two_phrases(real_models, benchmark_wav):
    cfg = config.GatewayConfig()
    cfg.vad.model = str(real_models / "silero_vad.onnx")
    vad = SileroVad(cfg)
    assert vad.window == 512

    _, samples = wav.decode(benchmark_wav.read_bytes())
    seg = Segmenter(vad, draft_interval_s=1.0)
    actions = []
    for start in range(0, len(samples), 1600):          # 100 ms chunks, like the client sends
        actions.extend(seg.feed(samples[start:start + 1600]))
    actions.extend(seg.commit())

    finals = [a for a in actions if isinstance(a, Final)]
    drafts = [a for a in actions if isinstance(a, Draft)]
    # measured on this recording: one pause at ~9 s, phrases of ~8.5 s and ~2 s
    assert [f.item for f in finals] == [1, 2]
    assert 7.5 * SAMPLE_RATE < len(finals[0].samples) < 9.5 * SAMPLE_RATE
    assert 1.0 * SAMPLE_RATE < len(finals[1].samples) < 3.0 * SAMPLE_RATE
    assert len([d for d in drafts if d.item == 1]) >= 6
    assert all(d.samples.dtype.name == "float32" for d in drafts)
    assert all(f.samples.dtype.name == "float32" for f in finals)
