from tolmach.tray.__main__ import single_instance


def test_second_instance_is_refused_until_the_first_lets_go():
    first = single_instance()
    assert first is not None
    assert single_instance() is None
    first.close()
    third = single_instance()
    assert third is not None
    third.close()


class FakeTray:
    def __init__(self, root):
        pass

    def run(self):
        pass


def _run_main(monkeypatch, text):
    from tolmach import paths
    from tolmach.tray import __main__ as tray_main
    from tolmach.tray import app as tray_app
    from tolmach.tray import autostart

    if text is not None:
        paths.config_file().write_text(text, encoding="utf-8")
    applied = []
    monkeypatch.setattr(autostart, "apply", lambda enabled, *a: applied.append(enabled))
    monkeypatch.setattr(tray_app, "TrayApp", FakeTray)
    tray_main.main()
    return applied


def test_autostart_is_not_applied_from_a_file_that_could_not_be_parsed(monkeypatch):
    assert _run_main(monkeypatch, '{"autostart": false,}') == []


def test_autostart_is_applied_from_a_readable_file(monkeypatch):
    assert _run_main(monkeypatch, '{"autostart": false, "port": 1}') == [False]
    assert _run_main(monkeypatch, None) == [False]


def test_the_tray_publishes_its_pid_and_takes_it_back(home):
    import json
    import os

    from tolmach import paths
    from tolmach.tray import __main__ as entry

    entry.publish_pid()
    assert json.loads(paths.tray_file().read_text(encoding="utf-8")) == {"pid": os.getpid()}
    entry.withdraw_pid()
    assert not paths.tray_file().exists()
    entry.withdraw_pid()                                   # twice is fine
