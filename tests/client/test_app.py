import wave

from tolmach import paths
from tolmach.client.app import NullMuter, save_failed_wav

from .fakes import tone


def test_failed_recording_is_saved_as_16k_mono_wav():
    path = save_failed_wav(tone(n=16000))
    assert path.parent == paths.failed_dir()
    assert path.suffix == ".wav"
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 16000)


def test_null_muter_does_nothing():
    muter = NullMuter()
    assert muter.mute() is None and muter.unmute() is None


def test_two_failed_recordings_in_the_same_second_do_not_overwrite_each_other(monkeypatch):
    from tolmach.client import app

    monkeypatch.setattr(app.time, "strftime", lambda fmt: "20261003-120000")
    first = save_failed_wav(tone(n=1600))
    second = save_failed_wav(tone(n=3200))
    assert first != second and first.exists() and second.exists()
    assert first.name == "20261003-120000.wav"
    assert second.name == "20261003-120000-2.wav"
    for path, frames in ((first, 1600), (second, 3200)):
        with wave.open(str(path)) as w:
            assert w.getnframes() == frames


def test_client_config_reread_logs_problems_once_per_file_change(caplog):
    from tolmach.client import app

    paths.config_file().write_text('{"client": {"hotkey": 5}}', encoding="utf-8")
    with caplog.at_level("WARNING", logger="tolmach"):
        assert app.load_client_config().hotkey == "shift+win+q"
        app.load_client_config()
    assert [r.getMessage() for r in caplog.records] == ["config: client.hotkey: expected str, default kept"]
