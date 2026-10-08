import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from tolmach import config, paths


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own data folder; the real one is never touched."""
    monkeypatch.setenv(paths.ENV, str(tmp_path / "home"))
    return paths.home()


@pytest.fixture(autouse=True)
def russian(monkeypatch):
    """Every test starts in Russian, whatever the language of this Windows: the texts the
    tests name are the Russian ones, and "auto" must not depend on the machine."""
    from tolmach import i18n

    monkeypatch.setattr(i18n, "system_language", lambda: "ru")
    i18n.set_language("ru")
    yield
    i18n.set_language("ru")


@pytest.fixture(autouse=True)
def libraries_record(tmp_path, monkeypatch):
    """The record of which requirements.txt the libraries came from lives in the real
    environment; no test may read or write that one."""
    from tolmach.tray import updater

    path = tmp_path / "tolmach-requirements.sha256"
    monkeypatch.setattr(updater, "stamp_file", lambda: path)
    return path


@pytest.fixture(autouse=True)
def detach_log_files():
    """logsetup.setup() opens a file in the temp home; let go of it after the test."""
    yield
    for name in ("tolmach", "aiohttp", "asyncio"):
        log = logging.getLogger(name)
        for handler in [h for h in log.handlers if isinstance(h, RotatingFileHandler)]:
            log.removeHandler(handler)
            handler.close()


@pytest.fixture
def real_models():
    """The folder with real model files, or skip. Captured before `home` hides it."""
    root = Path(os.environ.get("TOLMACH_TEST_MODELS", Path.home() / ".tolmach" / "models"))
    needed = [root / "silero_vad.onnx", root / "gigaam-v3" / "gigaam_v3_e2e_rnnt_tokens.txt"]
    if not all(p.is_file() for p in needed):
        pytest.skip(f"real models not found in {root}")
    return root


DATA = Path(__file__).parent / "data"


@pytest.fixture
def english_model(real_models):
    """The English model among the real model files, or skip: the tray menu downloads it."""
    model = config.english_model()
    if not all((real_models / getattr(model, role)).is_file() for role in ("encoder", "decoder", "joiner", "tokens")):
        pytest.skip(f"the English model is not in {real_models}")
    for role in ("encoder", "decoder", "joiner", "tokens"):
        setattr(model, role, str(real_models / getattr(model, role)))
    return model


@pytest.fixture
def english_wav():
    """A few seconds of English speech, 16 kHz mono: the sample that comes with the model
    (test_wavs/0.wav of its page). Not in the repository; without it the test is skipped."""
    path = Path(__file__).parent / "data" / "english.wav"
    if not path.is_file():
        pytest.skip(f"no {path}")
    return path


@pytest.fixture
def benchmark_wav():
    """A short recording of real speech (Pushkin's "У лукоморья...", 16 kHz mono) for the
    tests that run the real models. It is somebody's voice, so it is not in the repository:
    without the file these tests are skipped."""
    path = DATA / "benchmark.wav"
    if not path.is_file():
        pytest.skip(f"{path} is not here: record the first lines of \"У лукоморья дуб зелёный\" "
                    "as a 16 kHz mono WAV and put it there")
    return path
