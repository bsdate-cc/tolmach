import json

from tolmach import paths
from tolmach.client.mutesound import (AudioSession, choose_targets, clear_muted, read_muted,
                                        should_restore, write_muted)

ME = 100
CHROME = AudioSession(200, "chrome.exe", False)
CHROME_TAB = AudioSession(200, "chrome.exe", False)
PLAYER_MUTED = AudioSession(300, "vlc.exe", True)
MINE = AudioSession(ME, "pythonw.exe", False)


def keys(entries):
    return [{"pid": e["pid"], "name": e["name"]} for e in entries]


def test_targets_are_unmuted_sessions_of_other_processes_once_per_pid():
    assert choose_targets([CHROME, CHROME_TAB, PLAYER_MUTED, MINE], ME) == [CHROME]


def test_no_targets_when_everything_is_muted_or_ours():
    assert choose_targets([PLAYER_MUTED, MINE], ME) == []


def test_write_then_read_round_trip():
    write_muted([CHROME])
    assert keys(read_muted()) == [{"pid": 200, "name": "chrome.exe"}]


def test_write_merges_with_what_is_already_listed():
    write_muted([CHROME])
    write_muted([AudioSession(400, "spotify.exe", False), CHROME])
    assert keys(read_muted()) == [{"pid": 200, "name": "chrome.exe"}, {"pid": 400, "name": "spotify.exe"}]


def test_read_tolerates_a_missing_or_broken_file():
    assert read_muted() == []
    paths.muted_file().write_text("{not json", encoding="utf-8")
    assert read_muted() == []
    paths.muted_file().write_text(json.dumps({"pid": 1}), encoding="utf-8")
    assert read_muted() == []


def test_clear_removes_the_file_and_tolerates_its_absence():
    write_muted([CHROME])
    clear_muted()
    assert not paths.muted_file().exists()
    clear_muted()


def test_restore_requires_the_same_pid_and_name():
    entry = {"pid": 200, "name": "chrome.exe"}
    assert should_restore(entry, AudioSession(200, "chrome.exe", True)) is True
    assert should_restore(entry, AudioSession(200, "notepad.exe", True)) is False   # the pid was reused
    assert should_restore(entry, AudioSession(201, "chrome.exe", True)) is False


# --- recovery path must never raise ---

import pathlib
import time
from types import SimpleNamespace

import pytest

from tolmach.client import mutesound


class FakeVolume:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def SetMute(self, value, context):
        if self.fail:
            raise OSError("boom")
        self.calls.append(value)


@pytest.fixture
def no_com(monkeypatch):
    import comtypes
    monkeypatch.setattr(comtypes, "CoInitialize", lambda *a, **k: None)
    monkeypatch.setattr(comtypes, "CoUninitialize", lambda *a, **k: None)


def test_restore_unmutes_recorded_sessions_and_removes_the_file(monkeypatch, no_com):
    chrome, other = FakeVolume(), FakeVolume()
    monkeypatch.setattr(mutesound, "_live_sessions", lambda: [
        (AudioSession(200, "chrome.exe", True), chrome),
        (AudioSession(300, "vlc.exe", True), other),
    ])
    write_muted([CHROME])
    mutesound.restore()
    assert chrome.calls == [0]
    assert other.calls == []
    assert not paths.muted_file().exists()


def test_restore_does_not_raise_when_enumeration_fails_and_keeps_the_file(monkeypatch, no_com):
    def broken():
        raise RuntimeError("no audio service")
    monkeypatch.setattr(mutesound, "_live_sessions", broken)
    write_muted([CHROME])
    mutesound.restore()
    assert paths.muted_file().exists()


def test_restore_does_not_raise_when_comtypes_cannot_be_imported(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "comtypes":
            raise ImportError("no comtypes")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    write_muted([CHROME])
    mutesound.restore()
    assert paths.muted_file().exists()


def test_clear_and_restore_do_not_raise_on_oserror(monkeypatch, no_com):
    def locked(self, *a, **k):
        raise PermissionError("locked")
    monkeypatch.setattr(pathlib.Path, "unlink", locked)
    monkeypatch.setattr(mutesound, "_live_sessions", lambda: [])
    write_muted([CHROME])
    clear_muted()
    mutesound.restore()
    assert paths.muted_file().exists()


def test_failed_unmute_keeps_the_file_for_the_next_attempt(monkeypatch, no_com):
    monkeypatch.setattr(mutesound, "_live_sessions",
                        lambda: [(AudioSession(200, "chrome.exe", True), FakeVolume(fail=True))])
    write_muted([CHROME])
    mutesound.restore()
    assert keys(read_muted()) == [{"pid": 200, "name": "chrome.exe"}]


def test_a_session_that_cannot_be_read_is_skipped(monkeypatch):
    def raising_name():
        raise OSError("process gone")

    def raising_mute():
        raise OSError("session gone")

    good = SimpleNamespace(Process=SimpleNamespace(pid=5, name=lambda: "good.exe"),
                           SimpleAudioVolume=SimpleNamespace(GetMute=lambda: 0))
    dead_process = SimpleNamespace(Process=SimpleNamespace(pid=6, name=raising_name),
                                   SimpleAudioVolume=SimpleNamespace(GetMute=lambda: 0))
    dead_volume = SimpleNamespace(Process=SimpleNamespace(pid=7, name=lambda: "x.exe"),
                                  SimpleAudioVolume=SimpleNamespace(GetMute=raising_mute))
    system = SimpleNamespace(Process=None, SimpleAudioVolume=None)
    monkeypatch.setattr(mutesound, "_raw_sessions", lambda: [dead_process, system, dead_volume, good])
    result = mutesound._live_sessions()
    assert [info for info, _ in result] == [AudioSession(5, "good.exe", False)]


# --- recovery after a crash: the muted program may have restarted ---


def test_restore_finds_a_restarted_program_by_name(monkeypatch, no_com):
    chrome, chrome_tab, unmuted = FakeVolume(), FakeVolume(), FakeVolume()
    monkeypatch.setattr(mutesound, "_live_sessions", lambda: [
        (AudioSession(900, "chrome.exe", True), chrome),
        (AudioSession(901, "chrome.exe", True), chrome_tab),
        (AudioSession(902, "chrome.exe", False), unmuted),
    ])
    write_muted([CHROME])
    mutesound.restore()
    assert chrome.calls == [0] and chrome_tab.calls == [0]
    assert unmuted.calls == []
    assert not paths.muted_file().exists()


def test_a_failing_unmute_does_not_stop_the_others_and_its_entry_stays(monkeypatch, no_com):
    spotify, vlc = FakeVolume(), FakeVolume()
    monkeypatch.setattr(mutesound, "_live_sessions", lambda: [
        (AudioSession(200, "chrome.exe", True), FakeVolume(fail=True)),
        (AudioSession(400, "spotify.exe", True), spotify),
        (AudioSession(500, "vlc.exe", True), vlc),
    ])
    write_muted([CHROME, AudioSession(400, "spotify.exe", False), AudioSession(500, "vlc.exe", False)])
    mutesound.restore()
    assert spotify.calls == [0] and vlc.calls == [0]
    assert keys(read_muted()) == [{"pid": 200, "name": "chrome.exe"}]


def test_unmute_tries_every_session_too(monkeypatch, no_com):
    spotify = FakeVolume()
    monkeypatch.setattr(mutesound, "_live_sessions", lambda: [
        (AudioSession(200, "chrome.exe", True), FakeVolume(fail=True)),
        (AudioSession(400, "spotify.exe", True), spotify),
    ])
    write_muted([CHROME, AudioSession(400, "spotify.exe", False)])
    mutesound.Muter().unmute()
    assert spotify.calls == [0]
    assert keys(read_muted()) == [{"pid": 200, "name": "chrome.exe"}]


def test_an_entry_without_a_live_session_stays_for_the_next_start(monkeypatch, no_com):
    chrome = FakeVolume()
    monkeypatch.setattr(mutesound, "_live_sessions", lambda: [(AudioSession(200, "chrome.exe", True), chrome)])
    write_muted([CHROME, AudioSession(400, "spotify.exe", False)])
    mutesound.restore()
    assert chrome.calls == [0]
    assert keys(read_muted()) == [{"pid": 400, "name": "spotify.exe"}]


def test_entries_older_than_a_week_are_dropped(monkeypatch, no_com):
    monkeypatch.setattr(mutesound, "_live_sessions", lambda: [])
    now = time.time()
    paths.muted_file().write_text(json.dumps([
        {"pid": 200, "name": "chrome.exe", "at": now - 8 * 86400},
        {"pid": 400, "name": "spotify.exe", "at": now - 6 * 86400},
        {"pid": 500, "name": "vlc.exe"},
    ]), encoding="utf-8")
    assert keys(read_muted()) == [{"pid": 400, "name": "spotify.exe"}, {"pid": 500, "name": "vlc.exe"}]
    mutesound.restore()
    assert keys(read_muted()) == [{"pid": 400, "name": "spotify.exe"}, {"pid": 500, "name": "vlc.exe"}]
    paths.muted_file().write_text(json.dumps([{"pid": 200, "name": "chrome.exe", "at": now - 8 * 86400}]),
                                  encoding="utf-8")
    mutesound.restore()
    assert not paths.muted_file().exists()


def test_written_entries_carry_the_time(monkeypatch):
    monkeypatch.setattr(mutesound.time, "time", lambda: 1234.5)
    write_muted([CHROME])
    assert json.loads(paths.muted_file().read_text(encoding="utf-8")) == [
        {"pid": 200, "name": "chrome.exe", "at": 1234.5}]


def test_mute_does_not_raise_when_comtypes_cannot_be_imported(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "comtypes":
            raise ImportError("no comtypes")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    mutesound.Muter().mute()
