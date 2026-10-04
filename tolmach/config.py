"""config.json: one file for both processes.

load() tolerates: it never raises, and every rejected field falls back to
its default with a Problem saying why — a gateway that will not start over
a stray comma is worse than one running on defaults and saying so.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import get_args, get_type_hints

from tolmach import paths

log = logging.getLogger("tolmach")


@dataclass
class ModelConfig:
    name: str = "gigaam-v3"
    type: str = "nemo_transducer"
    encoder: str = "gigaam-v3/gigaam_v3_e2e_rnnt_encoder_int8.onnx"
    decoder: str = "gigaam-v3/gigaam_v3_e2e_rnnt_decoder.onnx"
    joiner: str = "gigaam-v3/gigaam_v3_e2e_rnnt_joint.onnx"
    tokens: str = "gigaam-v3/gigaam_v3_e2e_rnnt_tokens.txt"


@dataclass
class VadConfig:
    model: str = "silero_vad.onnx"
    threshold: float = 0.5
    min_silence_s: float = 0.5
    min_speech_s: float = 0.25


@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    threads: int = 4
    model: ModelConfig = field(default_factory=ModelConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    draft_interval_s: float = 1.0
    max_phrase_s: float = 25.0


@dataclass
class ButtonConfig:
    vid: str = "1B3F"
    pid: str = "2008"
    mask: int = 128


@dataclass
class ClientConfig:
    microphone: str = ""
    hotkey: str = "shift+win+q"
    # Types the last recognised text again; "" switches it off.
    insert_last_hotkey: str = "shift+win+z"
    button: ButtonConfig = field(default_factory=ButtonConfig)
    muted_floor_dbfs: float | None = -70.0
    mute_other_apps: bool = True
    min_recording_s: float = 0.3
    silence_autostop_s: float = 60.0
    max_recording_s: float = 600.0
    overlay_tail_chars: int = 300
    # Where the overlay sits on the monitor of the window that gets the text: a 3x3 grid,
    # "top-left" ... "center" ... "bottom-right" (tolmach.client.overlay.POSITIONS).
    overlay_position: str = "center"
    # "type": the text is typed as keystrokes (reaches remote desktops, leaves the
    # clipboard alone); "paste": through the clipboard and Ctrl+V (arrives in one piece).
    insert_mode: str = "type"
    # A space after every inserted text, so that two dictations in a row do not stick together.
    append_space: bool = True


@dataclass
class Config:
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    client: ClientConfig = field(default_factory=ClientConfig)
    autostart: bool = True
    # The language of the tray menu, the dialogs and the overlay: "ru", "en", or "auto" -
    # Russian when Windows or its regional format is Russian, English otherwise.
    # (The launcher window is always English.)
    language: str = "auto"


@dataclass
class Problem:
    where: str
    message: str


@dataclass
class Loaded:
    config: Config
    problems: list[Problem]


# Kept here as well as in tolmach.client.overlay: this module must import without tkinter
# (the gateway and the startup checks read the config too). A test pins the two together.
OVERLAY_POSITIONS = ("top-left", "top", "top-right", "left", "center", "right",
                     "bottom-left", "bottom", "bottom-right")


def _is_hex4(v: str) -> bool:
    return len(v) == 4 and all(ch in "0123456789abcdefABCDEF" for ch in v)


_CHECKS = {
    # No LAN access in this version: the gateway must not be reachable from the network.
    "gateway.host": (lambda v: v in ("127.0.0.1", "localhost"), "only loopback is supported"),
    "gateway.port": (lambda v: 1 <= v <= 65535, "must be 1..65535"),
    "gateway.threads": (lambda v: v >= 1, "must be >= 1"),
    "gateway.draft_interval_s": (lambda v: v > 0, "must be > 0"),
    "gateway.max_phrase_s": (lambda v: 1 <= v <= 30, "must be 1..30"),
    "gateway.vad.threshold": (lambda v: 0 < v < 1, "must be between 0 and 1"),
    "gateway.vad.min_silence_s": (lambda v: v > 0, "must be > 0"),
    "gateway.vad.min_speech_s": (lambda v: v > 0, "must be > 0"),
    "client.button.vid": (_is_hex4, "must be 4 hex digits"),
    "client.button.pid": (_is_hex4, "must be 4 hex digits"),
    "client.button.mask": (lambda v: 1 <= v <= 255, "must be 1..255"),
    "client.min_recording_s": (lambda v: v >= 0, "must be >= 0"),
    "client.silence_autostop_s": (lambda v: v > 0, "must be > 0"),
    "client.max_recording_s": (lambda v: v > 0, "must be > 0"),
    "client.overlay_tail_chars": (lambda v: v >= 20, "must be >= 20"),
    "client.insert_mode": (lambda v: v in ("type", "paste"), "must be 'type' or 'paste'"),
    "language": (lambda v: v in ("auto", "ru", "en"), "must be 'auto', 'ru' or 'en'"),
    "client.overlay_position": (lambda v: v in OVERLAY_POSITIONS, "must be one of: " + ", ".join(OVERLAY_POSITIONS)),
}


def _coerce(value, hint):
    """(ok, value) for one leaf. A bool is not an int here; an int is a fine float."""
    if hint is bool:
        return isinstance(value, bool), value
    if hint is int:
        return isinstance(value, int) and not isinstance(value, bool), value
    if hint is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        return ok, float(value) if ok else value
    if hint is str:
        return isinstance(value, str), value
    return False, value


def _merge(obj, data, where: str, problems: list[Problem]) -> None:
    if not isinstance(data, dict):
        problems.append(Problem(where or "<root>", "expected an object, defaults kept"))
        return
    hints = get_type_hints(type(obj))
    known = {f.name for f in fields(obj)}
    for key in data:
        if key not in known:
            problems.append(Problem(f"{where}.{key}" if where else str(key), "unknown key, ignored"))
    for f in fields(obj):
        if f.name not in data:
            continue
        path = f"{where}.{f.name}" if where else f.name
        value, hint = data[f.name], hints[f.name]
        current = getattr(obj, f.name)
        if is_dataclass(current):
            _merge(current, value, path, problems)
            continue
        options = get_args(hint)
        if value is None and type(None) in options:
            setattr(obj, f.name, None)
            continue
        leaf = next(a for a in options if a is not type(None)) if options else hint
        ok, value = _coerce(value, leaf)
        if not ok:
            problems.append(Problem(path, f"expected {leaf.__name__}, default kept"))
            continue
        check = _CHECKS.get(path)
        if check is not None and not check[0](value):
            problems.append(Problem(path, f"{check[1]}, default kept"))
            continue
        setattr(obj, f.name, value)


def load(path: Path | None = None) -> Loaded:
    path = path or paths.config_file()
    cfg = Config()
    try:
        # utf-8-sig: Notepad saves a BOM, and json refuses one
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return Loaded(cfg, [])
    except (OSError, UnicodeDecodeError) as e:
        return Loaded(cfg, [Problem("<file>", f"cannot read, defaults used: {e}")])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return Loaded(cfg, [Problem("<file>", f"not JSON, defaults used: {e}")])
    problems: list[Problem] = []
    _merge(cfg, data, "", problems)
    return Loaded(cfg, problems)


def unparsed(loaded: Loaded) -> bool:
    """The file exists but nothing in it could be used: writing it back would destroy the user's edit."""
    return any(p.where in ("<file>", "<root>") for p in loaded.problems)


_reported_lock = threading.Lock()
_reported: tuple | None = None


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), st.st_mtime_ns, st.st_size)


def load_reported(path: Path | None = None) -> Loaded:
    """load(), logging its problems once per change of the file: the file is re-read
    before every recording and on every tray refresh, and the log must not fill up."""
    global _reported
    path = path or paths.config_file()
    stamp = _stamp(path)
    loaded = load(path)
    with _reported_lock:
        if stamp == _reported:
            return loaded
        _reported = stamp
    for problem in loaded.problems:
        log.warning("config: %s: %s", problem.where, problem.message)
    return loaded


def to_dict(cfg: Config) -> dict:
    return asdict(cfg)


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or paths.config_file()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(to_dict(cfg), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def resolve_model_path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else paths.models_dir() / q
