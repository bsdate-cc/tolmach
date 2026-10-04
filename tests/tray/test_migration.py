"""The program used to be called SpeechKit: its data folder and its Run entry are carried over once."""
import os
import sys
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from tolmach import paths
from tolmach.tray import autostart, startup


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """A user profile with no explicit data folder: the default locations are in play."""
    monkeypatch.delenv(paths.ENV, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def old_install(profile) -> Path:
    old = profile / ".speechkit"
    (old / "logs").mkdir(parents=True)
    (old / "config.json").write_text('{"autostart": false}', encoding="utf-8")
    (old / "key").write_text("secret", encoding="utf-8")
    return old


def never(say):
    pytest.fail("there is nothing to stop")


def test_the_old_folder_becomes_the_new_one(profile):
    old = old_install(profile)
    assert startup.migrate_legacy(lambda text: None, stop=lambda say: True) == "moved"
    assert not old.exists()
    assert (profile / ".tolmach" / "config.json").read_text(encoding="utf-8") == '{"autostart": false}'
    assert (profile / ".tolmach" / "key").read_text(encoding="utf-8") == "secret"
    assert paths.ENV not in os.environ
    assert paths.home() == profile / ".tolmach"


def test_the_old_program_is_stopped_where_it_lives_before_anything_moves(profile):
    old = old_install(profile)
    seen = {}

    def stop(say):
        seen["home"] = paths.home()          # its lock, its pid file and its key are in the old folder
        seen["still_there"] = (old / "key").exists()
        return True

    startup.migrate_legacy(lambda text: None, stop=stop)
    assert seen == {"home": old, "still_there": True}


def test_nothing_happens_without_an_old_folder(profile):
    assert startup.migrate_legacy(lambda text: None, stop=never) == "none"
    assert not (profile / ".tolmach").exists()       # and the new folder is not made as a side effect


def test_an_existing_new_folder_is_never_overwritten(profile):
    old = old_install(profile)
    (profile / ".tolmach").mkdir()
    assert startup.migrate_legacy(lambda text: None, stop=never) == "none"
    assert old.exists()


def test_an_explicit_data_folder_means_nothing_to_carry_over(profile, monkeypatch):
    old = old_install(profile)
    monkeypatch.setenv(paths.ENV, str(profile / "elsewhere"))
    assert startup.migrate_legacy(lambda text: None, stop=never) == "none"
    assert old.exists()


def test_an_old_program_that_cannot_be_stopped_keeps_its_folder_for_this_run(profile):
    old = old_install(profile)
    said = []
    assert startup.migrate_legacy(said.append, stop=lambda say: False) == "kept"
    assert old.exists() and not (profile / ".tolmach").exists()
    assert paths.home() == old               # this run works on the old folder; the move is tried again next time
    assert any(".speechkit" in text and "not stopped" in text for text in said)


def test_a_folder_that_cannot_be_moved_is_kept_for_this_run(profile):
    old = old_install(profile)
    said = []

    def move(source, target):
        raise PermissionError("the folder is open in Explorer")

    waits = []
    assert startup.migrate_legacy(said.append, stop=lambda say: True, move=move, sleep=waits.append) == "kept"
    assert old.exists() and not (profile / ".tolmach").exists()
    assert paths.home() == old
    assert said and len(waits) == startup.MOVE_TRIES


def test_a_folder_still_held_for_a_moment_is_moved_on_a_later_try(profile):
    # The gateway that was just stopped closes its log file a little after it stops answering.
    old = old_install(profile)
    tries = []

    def move(source, target):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError("still in use")
        os.rename(source, target)

    assert startup.migrate_legacy(lambda text: None, stop=lambda say: True, move=move, sleep=lambda s: None) == "moved"
    assert len(tries) == 3 and (profile / ".tolmach" / "key").exists()


# --- the Run entry


class FakeRegistry:
    """Just enough of winreg for autostart: one key holding values."""

    HKEY_CURRENT_USER, KEY_SET_VALUE, REG_SZ = "HKCU", 2, 1

    def __init__(self, values):
        self.values = values
        registry = self

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        self._key = Key

        def open_key(root, path, reserved=0, access=0):
            return registry._key()

        def set_value(key, name, reserved, kind, value):
            registry.values[name] = value

        def delete_value(key, name):
            if name not in registry.values:
                raise FileNotFoundError(name)
            del registry.values[name]

        def query_value(key, name):
            if name not in registry.values:
                raise FileNotFoundError(name)
            return registry.values[name], 1

        self.module = SimpleNamespace(
            HKEY_CURRENT_USER=self.HKEY_CURRENT_USER, KEY_SET_VALUE=self.KEY_SET_VALUE, REG_SZ=self.REG_SZ,
            OpenKey=open_key, SetValueEx=set_value, DeleteValue=delete_value, QueryValueEx=query_value)


ROOT = PureWindowsPath(r"C:\projects\tolmach")
PYTHONW = r"C:\projects\tolmach\.venv\Scripts\pythonw.exe"


def test_the_run_entry_of_the_former_name_goes_when_the_new_one_is_written(monkeypatch):
    values = {"SpeechKit": '"pythonw" -c "from speechkit.tray.__main__ import main; main()"', "Other": "x"}
    monkeypatch.setitem(sys.modules, "winreg", FakeRegistry(values).module)
    autostart.apply(True, ROOT, PYTHONW)
    assert set(values) == {"Tolmach", "Other"}
    assert "tolmach.tray.__main__" in values["Tolmach"]


def test_the_run_entry_of_the_former_name_goes_even_when_autostart_is_off(monkeypatch):
    # It would start a module that no longer exists: a console-less failure at every logon.
    values = {"SpeechKit": "old"}
    monkeypatch.setitem(sys.modules, "winreg", FakeRegistry(values).module)
    autostart.apply(False, ROOT, PYTHONW)
    assert values == {}


def test_another_value_name_does_not_touch_the_former_entry(monkeypatch):
    values = {"SpeechKit": "old"}
    monkeypatch.setitem(sys.modules, "winreg", FakeRegistry(values).module)
    autostart.apply(True, ROOT, PYTHONW, value_name="TolmachTest")
    assert set(values) == {"SpeechKit", "TolmachTest"}
