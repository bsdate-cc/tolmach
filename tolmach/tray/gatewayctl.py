"""The tray's view of the gateway: health probe, launch, stop, status line, icon state."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import http.client
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tolmach.i18n import _

log = logging.getLogger("tolmach")

# The gateway is on loopback: never route these requests through a system proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PROCESS_TERMINATE = 0x0001


@dataclass(frozen=True)
class Health:
    status: str  # "loading" | "ok" | "error"
    model: str
    error: str
    pid: int | None


def parse_health(body) -> Health | None:
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") not in ("loading", "ok", "error"):
        return None
    pid = data.get("pid")
    return Health(data["status"], str(data.get("model") or ""), str(data.get("error") or ""),
                  pid if isinstance(pid, int) else None)


def status_line(health: Health | None) -> str:
    if health is None:
        return _("Шлюз: не отвечает")
    if health.status == "ok":
        return _("Шлюз: работает ({model})").format(model=health.model)
    if health.status == "loading":
        return _("Шлюз: запускается")
    return _("Шлюз: ошибка: {error}").format(error=health.error)


def glance(client_state: str, health: Health | None) -> str:
    """What the icon shows: a running dictation outranks everything, then a gateway
    that is not ready (the ring), then the client's own error or idle."""
    if client_state in ("recording", "finishing"):
        return client_state
    if health is None or health.status != "ok":
        return "down"
    return client_state


def _base_url(gateway) -> str:
    return f"http://{gateway.host}:{gateway.port}"


def probe(gateway, timeout: float = 1.0) -> Health | None:
    try:
        with _opener.open(_base_url(gateway) + "/health", timeout=timeout) as response:
            return parse_health(response.read())
    except (OSError, ValueError, http.client.HTTPException):
        return None


def pythonw_path(executable: str | None = None) -> str:
    """pythonw.exe next to the interpreter (no console window), or the interpreter itself."""
    exe = Path(executable or sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe)


def launch_argv(root: Path, executable: str) -> tuple[list[str], dict]:
    return [pythonw_path(executable), "-m", "tolmach.gateway"], {"PYTHONPATH": str(root)}


def launch(root: Path) -> None:
    argv, extra = launch_argv(root, sys.executable)
    subprocess.Popen(argv, env={**os.environ, **extra}, cwd=str(root),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)
    log.info("gateway launched")


def _wait_down(gateway, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if probe(gateway, timeout=0.5) is None:
            return True
        time.sleep(0.2)
    return False


def _terminate(pid: int) -> None:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        log.warning("could not open gateway process %d", pid)
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


def stop(gateway, key: str | None, wait_s: float = 5.0) -> bool:
    """POST /shutdown, wait for /health to go quiet, then end the process /health names.
    True means no gateway answers any more."""
    if probe(gateway) is None:
        return True
    if key is not None:
        request = urllib.request.Request(_base_url(gateway) + "/shutdown", data=b"", method="POST",
                                         headers={"Authorization": f"Bearer {key}"})
        try:
            _opener.open(request, timeout=2.0).close()
        except (OSError, ValueError, http.client.HTTPException) as e:
            log.warning("gateway did not accept shutdown: %s", e)
    if _wait_down(gateway, wait_s):
        return True
    health = probe(gateway)
    if health is None:
        return True
    if health.pid is None:
        log.warning("gateway keeps answering and reports no pid")
        return False
    log.warning("gateway ignored shutdown: terminating pid %d", health.pid)
    _terminate(health.pid)
    return _wait_down(gateway, 2.0)
