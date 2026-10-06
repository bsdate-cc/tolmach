"""Startup checks behind tolmach.cmd: every check is a function that prints nothing and
returns a CheckResult; run_checks walks them in order; main() is the console.

No third-party import at import time, directly or through what this module imports:
it has to run on exactly the machine where a library is missing. Anything that needs
one (the device list, the HID button) is imported inside the function that uses it,
after the library check has passed.
"""
from __future__ import annotations

import ctypes
import importlib
import json
import msvcrt
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, NamedTuple

from tolmach import VERSION, config, keyfile, paths
from tolmach.tray import gatewayctl, models, updater


class CheckResult(NamedTuple):
    name: str
    status: str  # "OK" | "WARN" | "FAIL" - control flow
    summary: str
    details: tuple[str, ...] = ()
    label: str | None = None  # the word shown instead of the status ("MISSING"); "" = no column


class Outcome(NamedTuple):
    ok: bool  # no FAIL stopped the walk
    warned: bool  # at least one WARN - the console keeps its window open
    results: tuple[CheckResult, ...]


MIN_PYTHON = (3, 12)
# import name -> what pip installs
DEPS = (("aiohttp", "aiohttp"), ("numpy", "numpy"), ("sherpa_onnx", "sherpa-onnx"),
        ("sounddevice", "sounddevice"), ("websocket", "websocket-client"), ("pystray", "pystray"),
        ("PIL", "Pillow"), ("pyperclip", "pyperclip"), ("pycaw", "pycaw"), ("hid", "hidapi"))
USAGE_PAGE_CONSUMER = 0x0C
CHECK_COUNT = 8
TOTAL_STEPS = CHECK_COUNT + 1  # the tray start itself is printed as the last step
TRAY_START_WAIT_S = 5.0


# ---- the checks ----

def check_python(executable: str | None = None, version: tuple | None = None,
                 maxsize: int | None = None) -> CheckResult:
    exe = executable or sys.executable
    v = tuple(version or sys.version_info[:3])
    bits = 64 if (maxsize or sys.maxsize) > 2**32 else 32
    summary = f"{'.'.join(map(str, v))}, {bits}-bit, {exe}"
    if bits == 32:
        return CheckResult("Python", "FAIL", summary,
                           ("a 64-bit Python is needed: the recognition engine has no 32-bit build",))
    if v[:2] < MIN_PYTHON:
        return CheckResult("Python", "FAIL", summary, ("Python 3.12 or newer is needed",))
    return CheckResult("Python", "OK", summary)


def check_dependencies(import_module: Callable | None = None,
                       stale: Callable[[], bool] | None = None) -> CheckResult:
    """Every library imports - and, when `stale` is given, requirements.txt is the one they
    were installed from: a pinned version that moved imports fine and is still the wrong one."""
    imp = import_module or importlib.import_module
    missing = []
    for module, dist in DEPS:
        try:
            imp(module)
        except Exception:
            missing.append(dist)
    if missing:
        return CheckResult("Libraries", "FAIL", ", ".join(missing),
                           (r"by hand: .venv\Scripts\python -m pip install -r requirements.txt",),
                           label="MISSING")
    if stale is not None and stale():
        return CheckResult("Libraries", "FAIL", "requirements.txt changed since the last install",
                           (r"by hand: .venv\Scripts\python -m pip install -r requirements.txt",),
                           label="OUTDATED")
    return CheckResult("Libraries", "OK", "all in place")


def check_data_dir(base: Path) -> CheckResult:
    base = Path(base)
    probe = base / ".startup-probe"
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return CheckResult("Data folder", "FAIL", f"cannot write: {base}", (str(e),))
    return CheckResult("Data folder", "OK", str(base))


def check_config() -> CheckResult:
    """Absent is not a problem - a fresh machine runs on defaults - but a file that
    does not parse is, and naming the problems here turns 'my edit did not apply'
    into a five-second fix."""
    exists = paths.config_file().exists()
    loaded = config.load()
    details = tuple(f"{p.where}: {p.message}" for p in loaded.problems[:5])
    if config.unparsed(loaded):
        return CheckResult("Settings", "WARN", "config.json cannot be read - running on defaults",
                           details)
    gateway = loaded.config.gateway
    where = "config.json" if exists else "no file - defaults"
    summary = f"{where}, gateway on {gateway.host}:{gateway.port}"
    if loaded.problems:
        return CheckResult("Settings", "WARN", summary, details)
    return CheckResult("Settings", "OK", summary)


def model_paths(gateway: config.GatewayConfig) -> list[Path]:
    """Where the five files the settings name are looked for."""
    model = gateway.model
    return [config.resolve_model_path(name)
            for name in (model.encoder, model.decoder, model.joiner, model.tokens, gateway.vad.model)]


def _fetchable(gateway: config.GatewayConfig, folder: Path) -> list[models.ModelFile]:
    """Of the files the settings name, those this program knows where to get and that are
    absent or cut short. A model of the user's own is never among them."""
    return models.missing(folder, models.among(model_paths(gateway), folder))


def check_models(gateway: config.GatewayConfig) -> CheckResult:
    """A file of the user's own is checked by its presence; a file of the list also by its
    size - a copy or a download cut short would fail the gateway with an error nobody can read."""
    folder = paths.models_dir()
    required = model_paths(gateway)
    fetchable = {folder / file.path for file in _fetchable(gateway, folder)}
    problems = []
    for path in required:
        if not path.is_file():
            problems.append(str(path))
        elif path in fetchable:
            problems.append(f"{path} (not the expected size - a copy or a download cut short)")
    if problems:
        hint = ("this window can download them; your own model: README.md, Models" if fetchable
                else "put them in place by hand: README.md, Models")
        return CheckResult("Models", "FAIL", f"{len(problems)} of {len(required)} files are missing",
                           (*problems, hint), label="MISSING")
    return CheckResult("Models", "OK", f"{gateway.model.name}, {Path(gateway.vad.model).name}")


def check_microphone(setting: str, product: str, names: list[str]) -> CheckResult:
    """Which device a recording will come from: the chosen one, else the microphone
    whose button starts the dictation, else the system default."""
    if not names:
        return CheckResult("Microphone", "WARN", "no input devices found")
    if setting:
        match = next((name for name in names if setting.lower() in name.lower()), None)
        if match is None:
            return CheckResult("Microphone", "WARN", f"the chosen microphone is not here: {setting}",
                               ("available: " + ", ".join(names),))
        return CheckResult("Microphone", "OK", match)
    own = next((name for name in names if product and product.lower() in name.lower()), None)
    if own is not None:
        return CheckResult("Microphone", "OK", f"{own} (the microphone with the button)")
    return CheckResult("Microphone", "OK", "the system default device", label="")


def _button_device(devices: list[dict]) -> dict | None:
    return next((d for d in devices if d.get("usage_page") == USAGE_PAGE_CONSUMER), None)


def check_button(devices: list[dict]) -> CheckResult:
    device = _button_device(devices)
    if device is None:
        return CheckResult("Button", "WARN", "not found - the hotkey and the menu work")
    return CheckResult("Button", "OK", f"found: {device.get('product_string') or 'HID device'}")


def check_gateway(health) -> CheckResult:
    """Reported, never started here: starting the gateway is the tray's job."""
    if health is None:
        return CheckResult("Gateway", "OK", "not running - the tray will start it", label="")
    if health.status == "ok":
        return CheckResult("Gateway", "OK", f"running, model {health.model}", label="")
    if health.status == "loading":
        return CheckResult("Gateway", "OK", "starting", label="")
    return CheckResult("Gateway", "WARN", f"error: {health.error}")


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


LEGACY_HOME = ".speechkit"  # the data folder under the program's former name


MOVE_TRIES = 10  # a process that was just stopped lets go of its files a moment later


def migrate_legacy(say: Callable[[str], None], *, stop: Callable | None = None,
                   move: Callable[[Path, Path], None] = os.rename,
                   sleep: Callable[[float], None] = time.sleep) -> str:
    """Carry the data folder of the former name (settings, key, models, logs) over to the
    new one, once. Must run before anything asks paths.home(): that call creates the new
    folder, and an existing new folder is never overwritten.

    The old tray and gateway are stopped first - they hold files in the folder - with the
    ordinary stop logic pointed at where they live. Returns "none" (nothing to carry over),
    "moved", or "kept": the old program could not be stopped or the folder could not be
    moved, so this run works on the old folder and the move is tried again next time."""
    if os.environ.get(paths.ENV):
        return "none"  # an explicit data folder: nothing of ours to move
    old, new = Path.home() / LEGACY_HOME, Path.home() / paths.DEFAULT_FOLDER
    if not old.is_dir() or new.exists():
        return "none"
    stop = stop_everything if stop is None else stop
    say(f"Moving the data folder {old} to {new} (the program used to be called SpeechKit).")
    os.environ[paths.ENV] = str(old)
    if stop(say):
        error: OSError | None = None
        for attempt in range(MOVE_TRIES):
            try:
                move(old, new)
            except OSError as e:
                error = e
                sleep(0.3)
            else:
                del os.environ[paths.ENV]
                say("  moved")
                return "moved"
        say(f"  {old} could not be moved ({error.__class__.__name__}): close the windows opened in it. "
            "Using the old folder for now; the move is tried again at the next start.")
    else:
        say(f"  the old program was not stopped - using {old} for now; the move is tried again at the next start.")
    return "kept"


# ---- the walk ----

class LiveSources:
    """What the machine answers right now. Imports are deferred: see the module docstring."""

    def input_names(self) -> list[str]:
        from tolmach.client import recorder
        return recorder.input_devices()

    def button_devices(self, vid: str, pid: str) -> list[dict]:
        import hid
        try:
            return hid.enumerate(int(vid, 16), int(pid, 16))
        except (OSError, ValueError):
            return []

    def health(self, gateway):
        return gatewayctl.probe(gateway)


def run_checks(emit: Callable[[int, int, CheckResult], None], *, sources=None,
               import_module: Callable | None = None,
               repair_deps: Callable[[CheckResult], bool] | None = None,
               repair_models: Callable[[CheckResult], bool] | None = None,
               libraries_stale: Callable[[], bool] | None = None) -> Outcome:
    """Walk the checks in order, handing each result to emit. The first FAIL stops the
    walk - unless that step has a repair and it says it repaired: the FAIL is emitted
    first (the repair's question refers to it), then the check runs again and that
    result is emitted too. Nothing here starts the tray or the gateway."""
    sources = sources or LiveSources()
    cfg = config.load().config
    button = cfg.client.button
    if libraries_stale is None:
        def libraries_stale() -> bool:
            return updater.libraries_stale(_root())

    def product() -> str:
        return (_button_device(sources.button_devices(button.vid, button.pid)) or {}).get("product_string") or ""

    checks: list[tuple[Callable[[], CheckResult], Callable | None]] = [
        (lambda: check_python(), None),
        (lambda: check_dependencies(import_module, libraries_stale), repair_deps),
        (lambda: check_data_dir(paths.home()), None),
        (lambda: check_config(), None),
        (lambda: check_models(config.load().config.gateway), repair_models),
        (lambda: check_microphone(cfg.client.microphone, product(), sources.input_names()), None),
        (lambda: check_button(sources.button_devices(button.vid, button.pid)), None),
        (lambda: check_gateway(sources.health(cfg.gateway)), None),
    ]
    results: list[CheckResult] = []
    warned = False
    for i, (check, repair) in enumerate(checks, 1):
        result = check()
        emit(i, TOTAL_STEPS, result)
        if result.status == "FAIL" and repair is not None and repair(result):
            result = check()
            emit(i, TOTAL_STEPS, result)
        results.append(result)
        warned = warned or result.status == "WARN"
        if result.status == "FAIL":
            return Outcome(False, warned, tuple(results))
    return Outcome(True, warned, tuple(results))


def format_line(i: int, total: int, result: CheckResult) -> str:
    """`[5/9] Models ............ OK       gigaam-v3, silero_vad.onnx`; details on indented lines."""
    dots = "." * max(2, 18 - len(result.name))
    shown = result.status if result.label is None else result.label
    column = f"{shown:<8} " if shown else ""
    head = f"[{i}/{total}] {result.name} {dots} {column}{result.summary}".rstrip()
    return "\n".join([head, *(" " * 8 + line for line in result.details)])


# ---- the running tray ----

def tray_running() -> bool:
    """Whether a tray holds tray.lock (the same byte lock its entry point takes)."""
    try:
        handle = open(paths.tray_lock(), "a+")
    except OSError:
        return False
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return True
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    finally:
        handle.close()


def read_tray_pid() -> int | None:
    try:
        pid = json.loads(paths.tray_file().read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        return None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def plan_stop(lock_held: bool, pid: int | None) -> tuple[str, str]:
    """("nothing" | "refuse" | "kill", reason). A process is ended only when the lock
    says a tray is alive AND the tray itself said which process it is: a guessed pid
    on Windows is a reused pid, and something unrelated dies for it."""
    if not lock_held:
        return "nothing", ""
    if pid is None:
        return "refuse", ("the running tray did not publish its process number (an older version started it) - "
                          "close it from its menu (Exit) and run tolmach.cmd again")
    return "kill", ""


def _terminate(pid: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


def stop_everything(say: Callable[[str], None]) -> bool:
    """Stop the running tray and the gateway. True: no tray holds the lock any more.
    The proof is the lock going free, never the kill's return code."""
    pid = read_tray_pid()
    action, reason = plan_stop(tray_running(), pid)
    if action == "refuse":
        say("  " + reason)
        return False
    if action == "kill":
        _terminate(pid)
    deadline = time.monotonic() + 5.0
    while tray_running() and time.monotonic() < deadline:
        time.sleep(0.2)
    if tray_running():
        say("  the tray did not stop")
        return False
    say("  tray stopped")
    stopped = gatewayctl.stop(config.load().config.gateway, keyfile.read_key())
    say("  gateway stopped" if stopped else "  the gateway did not stop - see gateway.log")
    return True


# ---- the console (tolmach.cmd -> python -m tolmach.tray.startup) ----

def ask_yes(prompt: str, default: bool = True, input_fn=input, isatty: bool | None = None) -> bool:
    """Y/n. A stdin that is not a console answers no without blocking: nothing is
    installed or stopped unattended. default=False is for the question whose yes
    ENDS something - a bare Enter there must mean 'leave it alone'."""
    tty = sys.stdin.isatty() if isatty is None else isatty
    if not tty:
        return False
    yes = ("", "y", "yes", "д", "да") if default else ("y", "yes", "д", "да")
    try:
        return input_fn(prompt).strip().lower() in yes
    except EOFError:
        return False


def hold(reason: str) -> None:
    try:
        input(f"\n{reason} - press Enter to close this window... ")
    except EOFError:
        pass


def pip_install(root: Path) -> bool:
    """pip in view: its output goes to the console, never to the log (an index URL can carry credentials)."""
    try:
        return subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")],
                              cwd=str(root)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_requirements(root: Path, say: Callable[[str], None], *, running: Callable[[], bool] | None = None,
                         stop_all: Callable | None = None, stop_gateway: Callable[[], bool] | None = None,
                         pip: Callable[[Path], bool] | None = None, mark: Callable[[Path], None] | None = None,
                         sleep: Callable[[float], None] = time.sleep,
                         clock: Callable[[], float] = time.monotonic, wait_s: float = 10.0) -> bool:
    """The update door of the launcher (--install-requirements): the libraries are installed
    with the tray and the gateway gone - they hold the files pip replaces. The tray that
    opened this window is on its way out; it is given a moment, then stopped like any other."""
    running = tray_running if running is None else running
    stop_all = stop_everything if stop_all is None else stop_all
    pip = pip_install if pip is None else pip
    mark = updater.mark_installed if mark is None else mark
    if stop_gateway is None:
        def stop_gateway() -> bool:
            return gatewayctl.stop(config.load().config.gateway, keyfile.read_key())
    deadline = clock() + wait_s
    while running() and clock() < deadline:
        sleep(0.2)
    if running() and not stop_all(say):
        return False
    # Asked separately: stopping the tray reports the tray, and a gateway that is still up
    # holds the very files pip is about to replace.
    if not stop_gateway():
        say("  the gateway did not stop - see gateway.log")
        return False
    say("  gateway stopped")
    say("Installing the libraries from requirements.txt...")
    if not pip(root):
        return False
    mark(root)
    return True


def offer_models(say: Callable[[str], None], *, gateway: config.GatewayConfig | None = None,
                 ask: Callable[..., bool] | None = None, ensure: Callable | None = None) -> bool:
    """Model files are missing: offer to download those this program knows where to get,
    the way missing libraries are offered for install. Only files the settings name are
    fetched - a model of the user's own is theirs to put in place, and a no gets that way out."""
    folder = paths.models_dir()
    gateway = config.load().config.gateway if gateway is None else gateway
    ask = ask_yes if ask is None else ask
    wanted = _fetchable(gateway, folder)
    theirs = [path for path in model_paths(gateway) if not path.is_file()
              and path not in {folder / file.path for file in wanted}]
    if not wanted:
        say("        These are not the default model files: put them in place by hand (README.md, Models).")
        return False
    if theirs:
        say(f"        {len(theirs)} of the missing files are not the default ones: those go in place by hand")
        say("        (README.md, Models). The rest can be downloaded.")
    megabytes = max(1, round(sum(file.size for file in wanted) / 1e6))
    if not ask(f"        Download them now (about {megabytes} MB)? [Y/n] "):
        say("        Nothing is recognised without the models. To use a model of your own, put its files")
        say(f"        into {folder} and name them in config.json (gateway.model): README.md, Models.")
        return False
    if not (models.ensure if ensure is None else ensure)(folder, say, tuple(wanted)):
        say("        The download did not finish. Run tolmach.cmd again to retry; where the files come from")
        say("        and how to put them in place by hand: README.md, Models.")
        return False
    return True


def _spawn_tray(root: Path) -> None:
    subprocess.Popen([gatewayctl.pythonw_path(), "-m", "tolmach.tray"], cwd=str(root),
                     env={**os.environ, "PYTHONPATH": str(root)},
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)


class _Log:
    """startup.log: what this window printed, for 'send me the log'."""

    def __init__(self, title: str):
        self.path = paths.logs_dir() / "startup.log"
        try:
            if self.path.stat().st_size > 500_000:
                os.replace(self.path, self.path.with_name("startup.log.1"))
        except OSError:
            pass
        self.line(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} {title} ===")

    def line(self, text: str) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass


def banner() -> str:
    """First line of the window and of every run in startup.log: which version is being started."""
    return f"Tolmach {VERSION}"


def main(argv: list[str] | None = None) -> int:
    """Print each step, offer repairs, start the tray, close. Always returns 0: this
    function holds its own window; tolmach.cmd's `|| pause` is only for 'python blew up'.
    With --install-requirements (the tray's update hands over to this window) the
    libraries are installed first, without questions."""
    args = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    root = Path(__file__).resolve().parents[2]
    carried: list[str] = []

    def announce(text: str) -> None:
        print(text, flush=True)
        carried.append(text)

    migrate_legacy(announce)  # before the log: opening it creates the data folder
    slog = _Log("tolmach.cmd")
    for line in carried:
        slog.line(line)

    def say(text: str) -> None:
        print(text, flush=True)
        slog.line(text)

    def emit(i: int, total: int, result: CheckResult) -> None:
        say(format_line(i, total, result))

    def repair_deps(result: CheckResult) -> bool:
        if not ask_yes("        Install them with pip now? [Y/n] "):
            slog.line("libraries missing; pip declined or no console")
            return False
        ok = pip_install(root)
        slog.line("pip install -r requirements.txt -> " + ("ok" if ok else "FAILED"))
        if ok:
            updater.mark_installed(root)
        return ok

    def repair_models(result: CheckResult) -> bool:
        ok = offer_models(say)
        slog.line("models missing -> " + ("downloaded" if ok else "not downloaded"))
        return ok

    say(banner())
    if "--install-requirements" in args:
        say("Update: stopping the tray and the gateway, installing the libraries, starting again.")
        if not install_requirements(root, say):
            slog.line("update: libraries not installed; tray not started")
            say(f"\nLog: {slog.path}")
            hold("The libraries were not installed - the tray was NOT started. To try again: tolmach.cmd")
            return 0
    elif tray_running():
        pid = read_tray_pid()
        say(f"The tray is already running{f' (process {pid})' if pid else ''}.")
        if not ask_yes("Restart the tray and the gateway? [y/N] ", default=False):
            say("Left as it is.")
            time.sleep(3)
            return 0
        if not stop_everything(say):
            hold("The tray was NOT restarted")
            return 0

    outcome = run_checks(emit, repair_deps=repair_deps, repair_models=repair_models)
    if not outcome.ok:
        say(f"\nLog: {slog.path}")
        hold("A check failed - the tray was NOT started")
        return 0
    if updater.installed_digest() is None:
        updater.mark_installed(root)  # an install older than the record: everything imports, so this is it

    _spawn_tray(root)
    up = False
    deadline = time.monotonic() + TRAY_START_WAIT_S
    while time.monotonic() < deadline:  # the launch is fire-and-forget: verify it
        if tray_running():
            up = True
            break
        time.sleep(0.2)
    emit(TOTAL_STEPS, TOTAL_STEPS,
         CheckResult("Tray", "OK", "started") if up
         else CheckResult("Tray", "WARN", r"did not come up - see logs\tray.log"))
    if outcome.warned or not up:
        hold("There are warnings")  # a warning in a window that closes by itself was never read
    else:
        say("This window closes in 3 seconds.")
        time.sleep(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
