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


# --- the wiring of a cancel: Esc and the cross both reach the controller, and Esc is ours only for a while


class FakeHotkey:
    made = []

    def __init__(self, post, text, make_event=None, **options):
        self.text, self.make_event, self.options = text, make_event, options
        self.calls = []
        self.registered = False
        FakeHotkey.made.append(self)

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def arm(self, on):
        self.calls.append(("arm", on))


class FakeOverlay:
    def __init__(self, target_window=None, on_cancel=None):
        self.on_cancel = on_cancel

    def set_position(self, position):
        pass

    def show(self, text):
        pass

    def message(self, text, seconds, back_to=None):
        pass

    def hide(self):
        pass

    def close(self):
        pass


class FakePart:
    """Stands in for the device watch and the button: started and stopped, nothing else."""
    found = active = False
    product = ""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def wired(monkeypatch, states):
    """A ClientApp in which everything that touches Windows is replaced: what is left is the wiring."""
    from tolmach.client import app

    FakeHotkey.made = []
    monkeypatch.setattr(app, "Hotkey", FakeHotkey)
    monkeypatch.setattr(app, "Overlay", FakeOverlay)
    monkeypatch.setattr(app, "DeviceWatch", FakePart)
    monkeypatch.setattr(app, "MicButton", FakePart)
    monkeypatch.setattr(app.mutesound, "restore", lambda: None)
    client = app.ClientApp(on_state=states.append)
    dictation, insert_last, cancel = FakeHotkey.made
    return client, dictation, insert_last, cancel


def test_the_cross_and_esc_both_ask_the_controller_to_cancel(monkeypatch):
    from tolmach.client import events as ev

    client, dictation, insert_last, cancel = wired(monkeypatch, [])
    client.overlay.on_cancel()
    assert client._events.get_nowait() == ev.Cancel("overlay")
    assert cancel.text == "esc" and cancel.make_event() == ev.Cancel("hotkey")


def test_only_the_key_that_cancels_is_bare_and_not_held_all_the_time(monkeypatch):
    client, dictation, insert_last, cancel = wired(monkeypatch, [])
    assert cancel.options == {"bare": True, "held": False}
    assert dictation.options == {} and insert_last.options == {}
    assert (dictation.text, insert_last.text) == ("shift+win+q", "shift+win+z")


def test_the_key_that_cancels_follows_the_settings(monkeypatch):
    paths.config_file().write_text('{"client": {"cancel_hotkey": ""}}', encoding="utf-8")
    client, dictation, insert_last, cancel = wired(monkeypatch, [])
    assert cancel.text == ""                                   # off: Hotkey takes "" for "no key"


def test_esc_is_ours_only_while_there_is_something_to_cancel(monkeypatch):
    states = []
    client, dictation, insert_last, cancel = wired(monkeypatch, states)
    for state in ("recording", "finishing", "idle", "recording", "error"):
        client.controller.ports.on_state(state)
    assert cancel.calls == [("arm", True), ("arm", True), ("arm", False), ("arm", True), ("arm", False)]
    assert states == ["recording", "finishing", "idle", "recording", "error"]     # the tray still hears them
    assert dictation.calls == [] and insert_last.calls == []


def test_every_hotkey_is_started_and_stopped_with_the_client(monkeypatch):
    client, *hotkeys = wired(monkeypatch, [])
    client.start()
    client.stop()
    assert [hotkey.calls for hotkey in hotkeys] == [["start", "stop"]] * 3


# --- what the tray menu is told: the hotkeys that are ours, and whether the button rules


def test_the_client_names_the_hotkeys_that_are_ours(monkeypatch):
    client, dictation, insert_last, cancel = wired(monkeypatch, [])
    assert client.hotkeys == {"dictation": "", "insert_last": ""}          # none registered yet
    dictation.registered = True
    assert client.hotkeys == {"dictation": "shift+win+q", "insert_last": ""}
    insert_last.registered = True
    assert client.hotkeys == {"dictation": "shift+win+q", "insert_last": "shift+win+z"}


def test_the_client_says_whether_the_button_rules_now(monkeypatch):
    client, *_ = wired(monkeypatch, [])
    assert client.button_rules() is False                      # "auto", and no button's microphone
    client._recorder.on_button_microphone = lambda microphone: True
    assert client.button_rules() is True                       # "auto", on the button's microphone
    paths.config_file().write_text('{"client": {"control": "hotkey"}}', encoding="utf-8")
    assert client.button_rules() is False
    client._recorder.on_button_microphone = lambda microphone: False
    paths.config_file().write_text('{"client": {"control": "button"}}', encoding="utf-8")
    assert client.button_rules() is True
