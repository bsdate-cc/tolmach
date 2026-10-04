import io
import wave

import numpy as np
import pytest

from tolmach.gateway import wav


def make_wav(samples: np.ndarray, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def test_mono_pcm16():
    pcm = np.array([0, 16384, -16384, 32767], dtype=np.int16)
    rate, samples = wav.decode(make_wav(pcm))
    assert rate == 16000
    assert samples.dtype == np.float32
    np.testing.assert_allclose(samples, [0.0, 0.5, -0.5, 32767 / 32768], atol=1e-6)


def test_rate_is_reported_not_converted():
    rate, samples = wav.decode(make_wav(np.zeros(480, dtype=np.int16), rate=48000))
    assert rate == 48000
    assert len(samples) == 480


def test_stereo_is_averaged():
    interleaved = np.array([16384, 0, -16384, -16384], dtype=np.int16)  # L, R, L, R
    _, samples = wav.decode(make_wav(interleaved, channels=2))
    np.testing.assert_allclose(samples, [0.25, -0.5], atol=1e-6)


def test_empty_wav_is_empty_audio():
    rate, samples = wav.decode(make_wav(np.zeros(0, dtype=np.int16)))
    assert rate == 16000
    assert len(samples) == 0


def test_eight_bit_is_refused():
    with pytest.raises(wav.WavError, match="16-bit"):
        wav.decode(make_wav(np.zeros(10, dtype=np.uint8), width=1))


@pytest.mark.parametrize("junk", [b"", b"not a wav at all", b"RIFF\x00\x00\x00\x00WAVE", b"OggS" + b"\x00" * 64])
def test_garbage_is_refused(junk):
    with pytest.raises(wav.WavError):
        wav.decode(junk)
