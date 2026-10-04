import http.client
import threading
from pathlib import Path

import pytest

from tolmach.tray import app as tray_app
from tolmach.tray import gatewayctl

OK = gatewayctl.Health("ok", "gigaam-v3", "", 1)


class FakeIcon:
    run_error = None
    last = None

    def __init__(self, name, image, title, menu=None):
        self.icon, self.menu, self.visible, self.title = image, menu, False, title
        self.menu_updates = 0
        self.stops = 0
        FakeIcon.last = self

    def update_menu(self):
        self.menu_updates += 1

    def stop(self):
        self.stops += 1

    def run(self, setup=None):
        if FakeIcon.run_error is not None:
            raise FakeIcon.run_error


class FakeClient:
    start_error = None

    def __init__(self, on_state=None):
        self.state, self.last_text, self.button_found = "idle", "", False
        self.starts = self.stops = 0

    def microphones(self, periodic):
        return None

    def insert_last(self, source="menu"):
        self.inserts = getattr(self, "inserts", []) + [source]

    def start(self):
        if FakeClient.start_error is not None:
            raise FakeClient.start_error
        self.starts += 1

    def stop(self):
        self.stops += 1


@pytest.fixture
def tray(monkeypatch):
    FakeIcon.run_error = None
    FakeClient.start_error = None
    monkeypatch.setattr(tray_app.pystray, "Icon", FakeIcon)
    monkeypatch.setattr(tray_app, "ClientApp", FakeClient)
    monkeypatch.setattr(tray_app.keyfile, "read_key", lambda: None)
    # No test may run git against whatever folder it happens to be started in (or its origin).
    monkeypatch.setattr(tray_app.updater, "status", lambda root: tray_app.updater.Update("off"))
    monkeypatch.setattr(tray_app.updater, "disk_version", lambda root: tray_app.VERSION)
    monkeypatch.setattr(tray_app.updater, "requirements_digest", lambda root: "digest-at-start")
    monkeypatch.setattr(tray_app.updater, "libraries_stale", lambda root: False)
    return tray_app.TrayApp(Path("."))


def test_poll_survives_a_probe_that_raises(tray, monkeypatch):
    monkeypatch.setattr(tray_app, "POLL_S", 0.01)
    tray._ready = True
    answers = iter([http.client.BadStatusLine("x"), OK])

    def probe(gateway):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        tray._stopping.set()
        return answer

    monkeypatch.setattr(tray_app.gatewayctl, "probe", probe)
    tray._poll()
    assert tray._health == OK


def test_setup_starts_the_poll_thread_when_the_first_check_raises(tray, monkeypatch):
    started = []

    def probe(gateway):
        raise http.client.BadStatusLine("x")

    monkeypatch.setattr(tray_app.gatewayctl, "probe", probe)
    monkeypatch.setattr(tray_app.gatewayctl, "launch", lambda root: None)
    monkeypatch.setattr(tray_app.TrayApp, "_await_gateway", lambda self, seconds=20.0: None)
    monkeypatch.setattr(tray_app.TrayApp, "_poll", lambda self: started.append(True))
    tray._setup(tray.icon)
    for t in threading.enumerate():
        if t.name == "health":
            t.join(2)
    assert started == [True]
    assert tray._client.starts == 1


def test_run_stops_a_started_client_when_the_icon_loop_raises(tray):
    tray._client.starts = 1
    tray._started = True
    FakeIcon.run_error = RuntimeError("boom")
    with pytest.raises(RuntimeError):   # run() stops the client, then re-raises for main() to log
        tray.run()
    assert tray._client.stops == 1


def test_exit_then_run_stops_the_client_once(tray, monkeypatch):
    tray._started = True
    monkeypatch.setattr(tray_app.ctypes, "windll", type("W", (), {"user32": type(
        "U", (), {"MessageBoxW": staticmethod(lambda *a: tray_app.IDNO)})})(), raising=False)
    monkeypatch.setattr(tray_app.gatewayctl, "stop", lambda *a: True)
    tray._exit(tray.icon, None)
    tray.run()
    assert tray._client.stops == 1


def test_run_does_not_stop_a_client_that_never_started(tray):
    tray.run()
    assert tray._client.stops == 0


def _status_rows(tray):
    return [item.text for item in tray.icon.menu.items if item.visible and not item.enabled]


def test_menu_change_leaves_a_file_that_is_not_json_untouched(tray, monkeypatch):
    applied = []
    monkeypatch.setattr(tray_app.autostart, "apply", lambda *a: applied.append(a))
    path = tray_app.paths.config_file()
    path.write_bytes(b'{"client": {"microphone": "USB",}}')
    tray._toggle_mute(tray.icon, None)
    tray._pick_microphone("Other")(tray.icon, None)
    tray._toggle_autostart(tray.icon, None)
    assert path.read_bytes() == b'{"client": {"microphone": "USB",}}'
    assert applied == []
    assert not path.with_name("config.json.bak").exists()


def test_menu_change_backs_up_a_file_with_a_bad_field_then_saves(tray):
    path = tray_app.paths.config_file()
    path.with_name("config.json.bak").write_text("older copy", encoding="utf-8")
    old = '{"client": {"microphone": "USB", "min_recording_s": -1}, "extra": 1}'
    path.write_text(old, encoding="utf-8")
    tray._toggle_mute(tray.icon, None)
    assert path.with_name("config.json.bak").read_text(encoding="utf-8") == old
    loaded = tray_app.config.load()
    assert loaded.problems == []
    assert loaded.config.client.microphone == "USB"
    assert loaded.config.client.mute_other_apps is False


def test_clean_file_is_saved_without_a_backup(tray):
    path = tray_app.paths.config_file()
    path.write_text('{"client": {"microphone": "USB"}}', encoding="utf-8")
    tray._toggle_mute(tray.icon, None)
    assert not path.with_name("config.json.bak").exists()
    assert tray_app.config.load().config.client.mute_other_apps is False


def test_menu_shows_a_config_error_line_while_the_file_has_problems(tray):
    tray._ready = True
    path = tray_app.paths.config_file()
    assert tray_app.CONFIG_ERROR not in _status_rows(tray)
    path.write_text("{", encoding="utf-8")
    tray.refresh()
    assert tray_app.CONFIG_ERROR in _status_rows(tray)
    path.write_text("{}", encoding="utf-8")
    tray.refresh()
    assert tray_app.CONFIG_ERROR not in _status_rows(tray)
    assert tray_app.CONFIG_ERROR == "Настройки: ошибка в config.json — см. журнал"


def test_refresh_rebuilds_the_menu_only_when_something_shown_changed(tray):
    tray._ready = True
    tray.refresh()
    tray.refresh()
    tray.refresh()
    assert tray.icon.menu_updates == 1
    tray._client.last_text = "готово"
    tray.refresh()
    assert tray.icon.menu_updates == 2
    tray._health = OK
    tray.refresh()
    tray.refresh()
    assert tray.icon.menu_updates == 3
    tray_app.paths.config_file().write_text('{"client": {"mute_other_apps": false}}', encoding="utf-8")
    tray.refresh()
    assert tray.icon.menu_updates == 4


def test_refresh_holds_a_lock(tray):
    tray._ready = True
    inside = []

    def update_menu():
        inside.append(tray._refresh_lock.locked())

    tray.icon.update_menu = update_menu
    tray.refresh()
    assert inside == [True]


def test_the_icon_tooltip_names_the_version(tray):
    from tolmach import VERSION

    assert tray.icon.title == f"Толмач {VERSION}"


def test_the_overlay_position_is_picked_from_the_menu(tray):
    tray._pick_position("bottom")(tray.icon, None)
    assert tray_app.config.load().config.client.overlay_position == "bottom"


def test_the_position_menu_offers_all_nine_places_in_russian_and_marks_the_current_one(tray):
    from tolmach.client import overlay

    rows = list(tray._positions())
    assert [row.text for row in rows] == [tray_app.POSITION_NAMES[p] for p in overlay.POSITIONS]
    assert len({row.text for row in rows}) == 9
    assert [row.text for row in rows if row.checked] == ["Посередине"]


def test_refresh_rebuilds_the_menu_when_the_overlay_position_changes(tray):
    tray._ready = True
    tray.refresh()
    before = tray.icon.menu_updates
    tray_app.paths.config_file().write_text('{"client": {"overlay_position": "top"}}', encoding="utf-8")
    tray.refresh()
    assert tray.icon.menu_updates == before + 1


def test_the_microphone_list_comes_from_the_client_when_it_has_a_new_one(tray, monkeypatch):
    monkeypatch.setattr(tray_app.gatewayctl, "probe", lambda gateway: OK)
    tray._ready = True
    asked = []

    def microphones(periodic):
        asked.append(periodic)
        return ["Microphone (Usb Audio Device)"] if len(asked) == 1 else None

    tray._client.microphones = microphones
    tray._check(devices=True)
    assert tray._devices == ["Microphone (Usb Audio Device)"]
    updates = tray.icon.menu_updates
    tray._check()
    assert asked == [True, False]
    assert tray._devices == ["Microphone (Usb Audio Device)"]     # no news: the list stays
    assert tray.icon.menu_updates == updates


def test_the_menu_offers_both_copying_and_inserting_the_last_text(tray):
    labels = [item.text for item in tray.icon.menu.items]
    assert labels.index("Вставить последний текст") == labels.index("Скопировать последний текст") + 1
    insert = next(item for item in tray.icon.menu.items if item.text == "Вставить последний текст")
    assert insert.enabled is False                    # nothing was dictated yet
    tray._client.last_text = "готово"
    assert insert.enabled is True
    tray._insert_last(tray.icon, None)
    assert tray._client.inserts == ["menu"]
