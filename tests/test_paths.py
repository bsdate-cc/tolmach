from pathlib import Path

from tolmach import paths


def test_home_follows_env_and_is_created(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere" / "deep"
    monkeypatch.setenv(paths.ENV, str(target))
    assert paths.home() == target
    assert target.is_dir()


def test_home_defaults_to_user_profile(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.ENV)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.home() == tmp_path / ".tolmach"


def test_files_live_in_home(home):
    assert paths.config_file() == home / "config.json"
    assert paths.key_file() == home / "key"
    assert paths.gateway_file() == home / "gateway.json"
    assert paths.tray_lock() == home / "tray.lock"
    assert paths.muted_file() == home / "muted.json"


def test_folders_are_created(home):
    for folder, name in ((paths.models_dir(), "models"), (paths.logs_dir(), "logs"), (paths.failed_dir(), "failed")):
        assert folder == home / name
        assert folder.is_dir()


def test_the_running_tray_publishes_itself_next_to_its_lock(home):
    assert paths.tray_file() == home / "tray.json"
