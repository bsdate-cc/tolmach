"""Microphone capture with sounddevice: 16 kHz mono int16 in 100 ms chunks."""
from __future__ import annotations

import ctypes
import logging
import threading
from typing import Callable

import sounddevice as sd

from tolmach.client import events as ev

log = logging.getLogger("tolmach")

RATE = 16000
BLOCK = 1600
WASAPI = "Windows WASAPI"
COINIT_APARTMENTTHREADED = 0x2

_com = threading.local()


def _co_initialize() -> None:
    # S_OK, S_FALSE (already up) and RPC_E_CHANGED_MODE (up in the other mode) all mean
    # the same here: COM can be used on this thread. Apartment-threaded is what PortAudio
    # itself asks for, and what comtypes asks for later on the same thread (mutesound).
    ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)


def _ensure_com() -> None:
    """PortAudio's WASAPI backend needs COM on the thread that starts a stream, and it
    initialises COM only on the thread that built the device list. The list is built by
    the tray; the streams are started by the controller thread. Without this, the first
    start after every launch failed ("Unanticipated host error") and only the rescan that
    followed - which initialises COM as a side effect - made the microphone open."""
    if not getattr(_com, "ready", False):
        _co_initialize()
        _com.ready = True


def _is_wasapi(device: dict, hostapis: list[dict]) -> bool:
    return hostapis[device["hostapi"]]["name"] == WASAPI


def pick_device(name_part: str, devices: list[dict], hostapis: list[dict]) -> tuple[int | None, bool]:
    """Index of the input device whose name contains `name_part`, WASAPI first.

    An empty name means the system default device: (None, False).
    """
    if not name_part:
        return None, False
    wanted = name_part.lower()
    matches = [
        (index, _is_wasapi(device, hostapis))
        for index, device in enumerate(devices)
        if device["max_input_channels"] > 0 and wanted in device["name"].lower()
    ]
    if not matches:
        raise ev.RecorderError(f"no input device matches '{name_part}'")
    for index, wasapi in matches:
        if wasapi:
            return index, True
    return matches[0]


def input_names(devices: list[dict], hostapis: list[dict]) -> list[str]:
    """Input device names for the tray menu: WASAPI names (they are not truncated), sorted."""
    inputs = [d for d in devices if d["max_input_channels"] > 0]
    wasapi = [d for d in inputs if _is_wasapi(d, hostapis)]
    return sorted({d["name"] for d in (wasapi or inputs)})


def match_microphone(product: str, names: list[str]) -> str:
    """The input device named after a USB product ('Usb Audio Device' ->
    'Microphone (Usb Audio Device)'), or '' when there is none."""
    if not product:
        return ""
    wanted = product.lower()
    return next((name for name in names if wanted in name.lower()), "")


def resolve_microphone(setting: str, preferred_product: str, names: list[str]) -> str:
    """What to record from: the chosen microphone; with nothing chosen, the microphone
    whose button starts the dictation; failing that, '' - the system default."""
    return setting or match_microphone(preferred_product, names)


# PortAudio builds its device list once. Rebuilding it (so replugged microphones
# appear) invalidates open streams, so it happens only under this lock and only
# while nothing is being recorded.
_pa_lock = threading.Lock()
_open_streams = 0


def _rescan() -> None:
    try:
        sd._terminate()
    except sd.PortAudioError:
        pass  # not initialised: the previous _initialize() failed, so just try it again
    sd._initialize()


def refresh_devices() -> list[str] | None:
    """Rebuild PortAudio's device list and return the input device names. None when it
    cannot be done now - a recording is running, and a rebuild would cut it off - or failed."""
    with _pa_lock:
        if _open_streams:
            return None
        try:
            _rescan()
            return input_names(list(sd.query_devices()), list(sd.query_hostapis()))
        except Exception:
            log.exception("could not rebuild the device list")
            return None


def input_devices() -> list[str]:
    """Live list for the startup checks; never rescans under a running recording."""
    with _pa_lock:
        try:
            if _open_streams == 0:
                _rescan()
            return input_names(list(sd.query_devices()), list(sd.query_hostapis()))
        except Exception:
            log.exception("could not list input devices")
            return []


def uses_button_microphone(setting: str, product: str, devices: list[dict], hostapis: list[dict]) -> bool:
    """Whether a recording with this microphone setting is made with the microphone
    whose button toggles the dictation (its USB product name is `product`): the device
    that start() would open is looked at, not the setting."""
    if not product:
        return False
    try:
        index, _ = pick_device(resolve_microphone(setting, product, input_names(devices, hostapis)),
                               devices, hostapis)
    except ev.RecorderError:
        return False
    return index is not None and product.lower() in devices[index]["name"].lower()


class _DefaultNeedsRescan(Exception):
    """Which device is the system default is part of the device list: never trust an old one."""


def open_fast(open_stream: Callable[[bool], object]):
    """Rebuilding PortAudio's device list takes 60-80 ms, and that is the first syllable
    of a dictation. So the list we already have is tried first (the tray refreshes it
    while idle); it is rebuilt only when that fails - the microphone was plugged in, or
    replugged, after the list was built."""
    try:
        return open_stream(False)
    except _DefaultNeedsRescan:
        pass
    except Exception as e:
        log.info("device list is out of date (%s): rescanning", e)
    return open_stream(True)


class Recorder:
    """The controller's `recorder` port. Chunks go to `post` as AudioChunk events."""

    def __init__(self, post: Callable, list_is_fresh: Callable[[], bool] = lambda: False):
        self._post = post
        self._stream = None
        self._preferred = ""
        # True when somebody vouches that the device list was rebuilt after the last change
        # (the device watch): then even the system default can be taken from it.
        self._list_is_fresh = list_is_fresh

    def prefer(self, product: str) -> None:
        """Product name of the device whose button starts the dictation ('' for none):
        with no microphone chosen in the settings, that device is recorded from."""
        self._preferred = product

    def on_button_microphone(self, microphone: str) -> bool:
        """Whether a recording with this setting comes from the button's own microphone."""
        if not self._preferred:
            return False
        with _pa_lock:
            try:
                devices, hostapis = list(sd.query_devices()), list(sd.query_hostapis())
            except Exception:
                log.exception("could not list input devices")
                return False
        return uses_button_microphone(microphone, self._preferred, devices, hostapis)

    def start(self, session: int, microphone: str) -> None:
        global _open_streams
        self.stop()
        _ensure_com()
        with _pa_lock:
            try:
                stream = open_fast(lambda rescan: self._open(session, microphone, rescan))
            except ev.RecorderError:
                raise
            except Exception as e:
                raise ev.RecorderError(f"could not open the microphone: {e}") from e
            self._stream = stream
            _open_streams += 1

    def _open(self, session: int, microphone: str, rescan: bool):
        if rescan:
            _rescan()
        devices, hostapis = list(sd.query_devices()), list(sd.query_hostapis())
        name = resolve_microphone(microphone, self._preferred, input_names(devices, hostapis))
        index, wasapi = pick_device(name, devices, hostapis)
        if index is None and not rescan and not self._list_is_fresh():
            raise _DefaultNeedsRescan("the system default input is taken from a fresh list")
        extra = sd.WasapiSettings(auto_convert=True) if wasapi else None

        def callback(indata, frames, time_info, status):
            if status:
                log.warning("audio input status: %s", status)
            self._post(ev.AudioChunk(session, indata[:, 0].copy()))

        stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                device=index, callback=callback, extra_settings=extra)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        log.info("recording from %s", devices[index]["name"] if index is not None
                 else "the system default input")
        return stream

    def stop(self) -> None:
        global _open_streams
        with _pa_lock:
            stream, self._stream = self._stream, None
            if stream is None:
                return
            _open_streams -= 1
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("could not close the audio stream")
