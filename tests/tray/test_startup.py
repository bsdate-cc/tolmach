import json
import msvcrt
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tolmach import config, paths
from tolmach.tray import models, startup
from tolmach.tray.startup import CheckResult

ROOT = Path(__file__).resolve().parents[2]


class Health:
    def __init__(self, status, model="gigaam-v3", error=""):
        self.status, self.model, self.error = status, model, error


class Sources:
    """What the live machine would answer, scripted."""

    def __init__(self, names=("Microphone (Usb Audio Device)",), devices=None, health=None):
        self.names = list(names)
        self.devices = [{"usage_page": 0x0C, "product_string": "Usb Audio Device"}] if devices is None else devices
        self.health_value = health

    def input_names(self):
        return self.names

    def button_devices(self, vid, pid):
        return self.devices

    def health(self, gateway):
        return self.health_value


PINNED = models.FILES


@pytest.fixture(autouse=True)
def one_byte_models(monkeypatch):
    """put() writes one byte per file; the real list pins a 319 MB encoder."""
    monkeypatch.setattr(models, "FILES", tuple(file._replace(size=1) for file in PINNED))


def put(*names):
    for name in names:
        path = config.resolve_model_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def put_models():
    cfg = config.GatewayConfig()
    put(cfg.model.encoder, cfg.model.decoder, cfg.model.joiner, cfg.model.tokens, cfg.vad.model)


OWN = ("mine/encoder.onnx", "mine/decoder.onnx", "mine/joiner.onnx", "mine/tokens.txt")


def own_model() -> config.GatewayConfig:
    """Settings that name a model of the user's own; the pause detector stays the default one."""
    gateway = config.GatewayConfig()
    gateway.model = config.ModelConfig(name="mine", encoder=OWN[0], decoder=OWN[1], joiner=OWN[2], tokens=OWN[3])
    return gateway


# --- single checks -------------------------------------------------------------------------------

def test_python_must_be_64_bit_and_recent():
    assert startup.check_python("py.exe", (3, 12, 10), 2**63 - 1).status == "OK"
    assert startup.check_python("py.exe", (3, 13, 0), 2**31 - 1).status == "FAIL"
    assert startup.check_python("py.exe", (3, 11, 9), 2**63 - 1).status == "FAIL"


def test_missing_libraries_are_named_by_what_pip_installs():
    def importer(name):
        if name in ("sherpa_onnx", "hid"):
            raise ImportError(name)

    result = startup.check_dependencies(importer)
    assert result.status == "FAIL" and result.label == "MISSING"
    assert result.summary == "sherpa-onnx, hidapi"
    assert startup.check_dependencies(lambda name: None).status == "OK"


def test_data_folder_must_be_writable(home, monkeypatch):
    assert startup.check_data_dir(home).status == "OK"

    def refuse(self, *args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_text", refuse)
    assert startup.check_data_dir(home).status == "FAIL"


def test_config_absent_is_fine_and_problems_are_listed():
    assert startup.check_config().status == "OK"
    paths.config_file().write_text(json.dumps({"gateway": {"port": "x"}, "extra": 1}), encoding="utf-8")
    result = startup.check_config()
    assert result.status == "WARN"
    assert any("gateway.port" in line for line in result.details)
    paths.config_file().write_text('{"client": {,}}', encoding="utf-8")
    result = startup.check_config()
    assert result.status == "WARN" and "cannot be read" in result.summary


def test_models_missing_then_present():
    result = startup.check_models(config.GatewayConfig())
    assert result.status == "FAIL" and result.label == "MISSING"
    assert any("silero_vad.onnx" in line for line in result.details)
    put_models()
    result = startup.check_models(config.GatewayConfig())
    assert result.status == "OK" and "gigaam-v3" in result.summary


def test_a_default_file_of_another_size_is_not_in_place():
    # A copy or a download cut short: the gateway would fail on it with an error nobody can read.
    put_models()
    config.resolve_model_path("silero_vad.onnx").write_bytes(b"cut")
    result = startup.check_models(config.GatewayConfig())
    assert result.status == "FAIL" and "1 of 5" in result.summary
    assert any("silero_vad.onnx" in line and "size" in line for line in result.details)
    assert "download" in result.details[-1]


def test_a_model_of_ones_own_is_checked_by_presence_alone():
    put("silero_vad.onnx", *OWN)
    result = startup.check_models(own_model())
    assert result.status == "OK" and "mine" in result.summary


def test_a_missing_model_of_ones_own_is_not_called_downloadable():
    put("silero_vad.onnx")
    result = startup.check_models(own_model())
    assert result.status == "FAIL" and "4 of 5" in result.summary
    assert "by hand" in result.details[-1] and "download" not in result.details[-1]


NAMES = ["Headset Microphone (Poly BT700)", "Microphone (Usb Audio Device)"]


def test_microphone_tells_what_will_be_recorded():
    chosen = startup.check_microphone("Poly", "Usb Audio Device", NAMES)
    assert chosen.status == "OK" and "Poly BT700" in chosen.summary
    own = startup.check_microphone("", "Usb Audio Device", NAMES)
    assert own.status == "OK" and "Usb Audio Device" in own.summary and "button" in own.summary
    default = startup.check_microphone("", "", NAMES)
    assert default.status == "OK" and "default" in default.summary


def test_a_chosen_microphone_that_is_gone_is_a_warning():
    result = startup.check_microphone("Rode", "", NAMES)
    assert result.status == "WARN" and "Rode" in result.summary
    assert any("Poly BT700" in line for line in result.details)


def test_no_input_devices_at_all_is_a_warning():
    assert startup.check_microphone("", "", []).status == "WARN"


def test_button_found_or_not():
    found = startup.check_button([{"usage_page": 0x0C, "product_string": "Usb Audio Device"}])
    assert found.status == "OK" and "Usb Audio Device" in found.summary
    assert startup.check_button([{"usage_page": 0xFF00}]).status == "WARN"
    assert startup.check_button([]).status == "WARN"


def test_gateway_is_reported_never_started():
    assert "the tray will start it" in startup.check_gateway(None).summary
    assert startup.check_gateway(None).status == "OK"
    assert "gigaam-v3" in startup.check_gateway(Health("ok")).summary
    assert startup.check_gateway(Health("loading")).status == "OK"
    failed = startup.check_gateway(Health("error", error="model files not found"))
    assert failed.status == "WARN" and "model files not found" in failed.summary


# --- the walk -------------------------------------------------------------------------------------

def walk(monkeypatch, sources=None, repair_deps=None, repair_models=None, importer=lambda name: None):
    monkeypatch.setattr(startup, "check_python", lambda: CheckResult("Python", "OK", "3.12"))
    seen = []
    outcome = startup.run_checks(lambda i, n, res: seen.append((i, n, res.name, res.status)),
                                 sources=sources or Sources(), import_module=importer,
                                 repair_deps=repair_deps, repair_models=repair_models,
                                 libraries_stale=lambda: False)
    return outcome, seen


def test_all_checks_pass_and_are_numbered_with_room_for_the_tray_step(monkeypatch):
    put_models()
    outcome, seen = walk(monkeypatch)
    assert outcome.ok and not outcome.warned
    assert [name for _, _, name, _ in seen] == ["Python", "Libraries", "Data folder", "Settings",
                                               "Models", "Microphone", "Button", "Gateway"]
    assert [(i, n) for i, n, _, _ in seen] == [(i, 9) for i in range(1, 9)]


def test_the_first_fail_stops_the_walk(monkeypatch):
    outcome, seen = walk(monkeypatch)                      # no models in the temp home
    assert not outcome.ok
    assert seen[-1][2:] == ("Models", "FAIL")
    assert len(seen) == 5


def test_a_repaired_step_is_checked_again_and_the_walk_goes_on(monkeypatch):
    missing = {"hid"}

    def importer(name):
        if name in missing:
            raise ImportError(name)

    def repair_deps(result):
        missing.clear()
        return True

    def repair_models(result):
        put_models()
        return True

    outcome, seen = walk(monkeypatch, repair_deps=repair_deps, repair_models=repair_models, importer=importer)
    assert outcome.ok
    statuses = [(name, status) for _, _, name, status in seen]
    assert statuses.count(("Libraries", "FAIL")) == 1 and statuses.count(("Libraries", "OK")) == 1
    assert statuses.count(("Models", "FAIL")) == 1 and statuses.count(("Models", "OK")) == 1


def test_a_declined_repair_stops_the_walk(monkeypatch):
    def importer(name):
        if name == "hid":
            raise ImportError(name)

    outcome, seen = walk(monkeypatch, repair_deps=lambda result: False, importer=importer)
    assert not outcome.ok and seen[-1][2:] == ("Libraries", "FAIL")


def test_warnings_do_not_stop_the_walk_but_are_remembered(monkeypatch):
    put_models()
    outcome, seen = walk(monkeypatch, sources=Sources(devices=[]))
    assert outcome.ok and outcome.warned
    assert ("Button", "WARN") in [(name, status) for _, _, name, status in seen]


def test_format_line():
    line = startup.format_line(3, 9, CheckResult("Models", "FAIL", "no files", ("a.onnx", "b.onnx"), "MISSING"))
    head, first, second = line.split("\n")
    assert head.startswith("[3/9] Models ") and "MISSING" in head and head.endswith("no files")
    assert first.strip() == "a.onnx" and second.strip() == "b.onnx"
    assert first.startswith(" " * 8)
    assert "OK" not in startup.format_line(8, 9, CheckResult("Gateway", "OK", "not running", label=""))


# --- prompts and the running tray -----------------------------------------------------------------

def test_ask_yes():
    assert startup.ask_yes("?", input_fn=lambda p: "", isatty=True) is True
    assert startup.ask_yes("?", input_fn=lambda p: "n", isatty=True) is False
    assert startup.ask_yes("?", default=False, input_fn=lambda p: "", isatty=True) is False
    assert startup.ask_yes("?", default=False, input_fn=lambda p: "Y", isatty=True) is True
    assert startup.ask_yes("?", input_fn=lambda p: "y", isatty=False) is False     # nothing happens unattended


def test_tray_running_follows_the_lock():
    assert startup.tray_running() is False
    handle = open(paths.tray_lock(), "a+")
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    try:
        assert startup.tray_running() is True
    finally:
        handle.close()
    assert startup.tray_running() is False


def test_the_pid_of_the_running_tray_is_read_from_its_file():
    assert startup.read_tray_pid() is None
    paths.tray_file().write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    assert startup.read_tray_pid() == 4242
    paths.tray_file().write_text("not json", encoding="utf-8")
    assert startup.read_tray_pid() is None


def test_a_tray_is_only_stopped_when_we_know_which_process_it_is():
    assert startup.plan_stop(lock_held=False, pid=None)[0] == "nothing"
    action, reason = startup.plan_stop(lock_held=True, pid=None)
    assert action == "refuse" and "Exit" in reason          # an old tray published no pid: use its menu
    assert startup.plan_stop(lock_held=True, pid=4242) == ("kill", "")


def test_the_module_imports_without_any_third_party_package():
    """It must run on exactly the machine where a library is missing."""
    blocked = [name for name, _ in startup.DEPS]
    code = textwrap.dedent(f"""
        import sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {blocked!r}:
                    raise ImportError("blocked: " + name)
        sys.meta_path.insert(0, Block())
        import tolmach.tray.startup
    """)
    done = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --- the console run ------------------------------------------------------------------------------

def console(monkeypatch, capsys, outcome, running=(False, True), answers=()):
    states = list(running)
    spawned = []
    held = []
    replies = list(answers)
    monkeypatch.setattr(startup, "run_checks", lambda emit, **kw: outcome)
    monkeypatch.setattr(startup, "tray_running", lambda: states.pop(0) if len(states) > 1 else states[0])
    monkeypatch.setattr(startup, "_spawn_tray", lambda root: spawned.append(root))
    monkeypatch.setattr(startup, "hold", lambda reason: held.append(reason))
    monkeypatch.setattr(startup, "ask_yes", lambda prompt, default=True: replies.pop(0))
    monkeypatch.setattr(startup.time, "sleep", lambda seconds: None)
    code = startup.main()
    return code, spawned, held, capsys.readouterr().out


def test_console_starts_the_tray_and_closes_by_itself(monkeypatch, capsys):
    code, spawned, held, out = console(monkeypatch, capsys, startup.Outcome(True, False, ()))
    assert code == 0 and len(spawned) == 1 and held == []
    assert "[9/9] Tray" in out and "started" in out
    assert "tray.cmd" in (paths.logs_dir() / "startup.log").read_text(encoding="utf-8")


def test_console_holds_the_window_after_a_warning(monkeypatch, capsys):
    code, spawned, held, out = console(monkeypatch, capsys, startup.Outcome(True, True, ()))
    assert len(spawned) == 1 and len(held) == 1


def test_console_does_not_start_the_tray_after_a_fail(monkeypatch, capsys):
    code, spawned, held, out = console(monkeypatch, capsys, startup.Outcome(False, False, ()))
    assert code == 0 and spawned == [] and len(held) == 1
    assert "startup.log" in out


def test_console_leaves_a_running_tray_alone_unless_told_to_restart(monkeypatch, capsys):
    code, spawned, held, out = console(monkeypatch, capsys, startup.Outcome(True, False, ()),
                                       running=(True,), answers=(False,))
    assert spawned == [] and "already running" in out


def test_console_restarts_a_running_tray_when_asked(monkeypatch, capsys):
    stops = []
    monkeypatch.setattr(startup, "stop_everything", lambda say: stops.append(1) or True)
    code, spawned, held, out = console(monkeypatch, capsys, startup.Outcome(True, False, ()),
                                       running=(True, False, True), answers=(True,))
    assert stops == [1] and len(spawned) == 1


def test_console_does_not_start_a_second_tray_when_the_stop_failed(monkeypatch, capsys):
    monkeypatch.setattr(startup, "stop_everything", lambda say: False)
    code, spawned, held, out = console(monkeypatch, capsys, startup.Outcome(True, False, ()),
                                       running=(True,), answers=(True,))
    assert spawned == [] and len(held) == 1


# --- the update door: python -m tolmach.tray.startup --install-requirements

class UpdateDoor:
    def __init__(self, tray_leaves_after=0, stop_all_ok=True, gateway_stops=True, pip_ok=True):
        self.polls_left = tray_leaves_after
        self.stop_all_ok, self.gateway_stops, self.pip_ok = stop_all_ok, gateway_stops, pip_ok
        self.now = 0.0
        self.calls = []
        self.said = []

    def running(self):
        if self.polls_left > 0:
            self.polls_left -= 1
            return True
        return False

    def sleep(self, seconds):
        self.now += seconds

    def stop_all(self, say):
        self.calls.append("stop tray")
        self.polls_left = 0 if self.stop_all_ok else self.polls_left
        return self.stop_all_ok

    def mark(self, root):
        self.calls.append("record")

    def stop_gateway(self):
        self.calls.append("stop gateway")
        return self.gateway_stops

    def pip(self, root):
        self.calls.append("pip")
        return self.pip_ok

    def run(self):
        from pathlib import Path

        return startup.install_requirements(
            Path("."), self.said.append, running=self.running, stop_all=self.stop_all,
            stop_gateway=self.stop_gateway, pip=self.pip, mark=self.mark, sleep=self.sleep,
            clock=lambda: self.now)


def test_libraries_are_installed_only_after_the_tray_left_and_the_gateway_stopped():
    door = UpdateDoor(tray_leaves_after=3)
    assert door.run() is True
    assert door.calls == ["stop gateway", "pip", "record"]


def test_a_tray_that_does_not_leave_by_itself_is_stopped_before_pip():
    door = UpdateDoor(tray_leaves_after=10 ** 6)
    assert door.run() is True
    assert door.calls == ["stop tray", "stop gateway", "pip", "record"]
    assert door.now >= 10.0


def test_pip_is_not_run_over_a_gateway_that_survived_the_stop_of_the_tray():
    # stop_everything reports the tray, not the gateway: the gateway is asked separately.
    door = UpdateDoor(tray_leaves_after=10 ** 6, gateway_stops=False)
    assert door.run() is False
    assert door.calls == ["stop tray", "stop gateway"]


def test_a_failed_install_leaves_no_record_so_that_it_is_offered_again():
    door = UpdateDoor(pip_ok=False)
    assert door.run() is False
    assert "record" not in door.calls


def test_libraries_installed_from_an_older_requirements_file_fail_the_check():
    result = startup.check_dependencies(lambda name: None, stale=lambda: True)
    assert (result.status, result.label) == ("FAIL", "OUTDATED")
    assert startup.check_dependencies(lambda name: None, stale=lambda: False).status == "OK"


def test_the_walk_offers_pip_for_outdated_libraries_and_passes_once_they_are_installed(monkeypatch):
    monkeypatch.setattr(startup, "check_python", lambda: CheckResult("Python", "OK", "3.12"))
    state = {"stale": True, "repairs": 0}

    def repair(result):
        state["repairs"] += 1
        state["stale"] = False
        return True

    seen = []
    startup.run_checks(lambda i, n, res: seen.append((res.name, res.status)), sources=Sources(),
                       import_module=lambda name: None, repair_deps=repair,
                       libraries_stale=lambda: state["stale"])
    assert [status for name, status in seen if name == "Libraries"] == ["FAIL", "OK"]
    assert state["repairs"] == 1


def test_pip_is_not_run_over_a_tray_that_cannot_be_stopped():
    door = UpdateDoor(tray_leaves_after=10 ** 6, stop_all_ok=False)
    assert door.run() is False
    assert "pip" not in door.calls


def test_pip_is_not_run_over_a_gateway_that_cannot_be_stopped():
    door = UpdateDoor(gateway_stops=False)
    assert door.run() is False
    assert door.calls == ["stop gateway"]


def test_a_failed_install_is_reported():
    assert UpdateDoor(pip_ok=False).run() is False


def test_an_install_without_a_record_gets_one_after_a_clean_walk(monkeypatch, capsys, libraries_record):
    from tolmach.tray import updater

    assert updater.installed_digest() is None
    console(monkeypatch, capsys, startup.Outcome(True, False, ()))
    assert libraries_record.exists() and updater.installed_digest() is not None


def test_a_failed_walk_leaves_no_record(monkeypatch, capsys, libraries_record):
    console(monkeypatch, capsys, startup.Outcome(False, False, ()))
    assert not libraries_record.exists()


# --- missing models: an offer to download, or the way to a model of one's own

ALL = tuple(file.path for file in PINNED)


def offer(answer, gateway=None, downloads=True):
    said, asked, fetched = [], [], []

    def ask(prompt):
        asked.append(prompt)
        return answer

    def ensure(folder, say, files):
        fetched.append(tuple(file.path for file in files))
        return downloads

    result = startup.offer_models(said.append, gateway=gateway or config.GatewayConfig(), ask=ask, ensure=ensure)
    return result, said, asked, fetched


def test_missing_models_are_offered_for_download_with_their_size(monkeypatch):
    monkeypatch.setattr(models, "FILES", PINNED)
    result, said, asked, fetched = offer(answer=True)
    assert result is True and fetched == [ALL]
    assert len(asked) == 1 and f"{models.TOTAL_MB} MB" in asked[0]


def test_a_no_points_to_a_model_of_ones_own_and_downloads_nothing():
    result, said, asked, fetched = offer(answer=False)
    assert result is False and fetched == []
    text = " ".join(said)
    assert "gateway.model" in text and "README.md" in text and str(paths.models_dir()) in text


def test_a_failed_download_fails_the_offer_and_says_what_to_do_next():
    result, said, asked, fetched = offer(answer=True, downloads=False)
    assert result is False and fetched == [ALL]
    assert "README.md" in " ".join(said)


def test_a_model_of_ones_own_is_never_offered_for_download():
    # The settings name other files: there is nothing this program knows where to get.
    put("silero_vad.onnx")
    result, said, asked, fetched = offer(answer=True, gateway=own_model())
    assert result is False and asked == [] and fetched == []
    assert "by hand" in " ".join(said)


def test_with_a_model_of_ones_own_only_the_missing_default_file_is_offered():
    put(*OWN)
    result, said, asked, fetched = offer(answer=True, gateway=own_model())
    assert result is True and fetched == [("silero_vad.onnx",)]
    assert "by hand" not in " ".join(said)


def test_missing_files_of_ones_own_are_named_as_work_by_hand_next_to_the_offer():
    result, said, asked, fetched = offer(answer=True, gateway=own_model())
    assert fetched == [("silero_vad.onnx",)]
    assert any("4 of the missing files" in line and "by hand" in line for line in said)


def test_a_default_file_cut_short_is_offered_again():
    put_models()
    config.resolve_model_path("silero_vad.onnx").write_bytes(b"cut")
    result, said, asked, fetched = offer(answer=True)
    assert fetched == [("silero_vad.onnx",)]
