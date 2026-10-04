"""Handing the tray over to a new process.

The tray cannot restart itself in place - the code it runs is already in memory, and
a second tray gives up while the first holds the lock. So the old tray starts a
successor and exits. Two successors:

- the quiet one (`pythonw -m tolmach.tray.relaunch`): waits for the lock to come
  free and becomes the tray, importing the code as it is on disk now;
- the launcher window (`python -m tolmach.tray.startup --install-requirements`), for
  when requirements.txt changed: pip must run with the tray and the gateway gone - they
  hold the files it replaces - and in a window that shows what went wrong.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from tolmach.tray import gatewayctl

log = logging.getLogger("tolmach")

WAIT_S = 20.0
STEP_S = 0.2


def wait_until_free(running: Callable[[], bool], sleep: Callable[[float], None] = time.sleep,
                    timeout: float | None = None, clock: Callable[[], float] = time.monotonic) -> bool:
    """True once no tray is running; False if the old one is still there after `timeout`."""
    deadline = clock() + (WAIT_S if timeout is None else timeout)
    while running():
        if clock() >= deadline:
            return False
        sleep(STEP_S)
    return True


def _console_python() -> str:
    """python.exe next to the running pythonw.exe: the launcher window needs a console interpreter."""
    exe = Path(sys.executable)
    python = exe.with_name("python.exe")
    return str(python if python.exists() else exe)


def _start(argv: list[str], root: Path, flags: int) -> None:
    subprocess.Popen(argv, cwd=str(root), env={**os.environ, "PYTHONPATH": str(root)}, creationflags=flags,
                     stdin=subprocess.DEVNULL if flags == subprocess.CREATE_NO_WINDOW else None,
                     stdout=subprocess.DEVNULL if flags == subprocess.CREATE_NO_WINDOW else None,
                     stderr=subprocess.DEVNULL if flags == subprocess.CREATE_NO_WINDOW else None,
                     close_fds=True)


def spawn(root: Path) -> None:
    """Start the quiet successor, detached from the tray that is about to exit."""
    _start([gatewayctl.pythonw_path(), "-m", "tolmach.tray.relaunch"], root, subprocess.CREATE_NO_WINDOW)


def spawn_console(root: Path) -> None:
    """Open the launcher window that installs the libraries and then starts the tray."""
    _start([_console_python(), "-m", "tolmach.tray.startup", "--install-requirements"], root,
           subprocess.CREATE_NEW_CONSOLE)


def main(start_tray: Callable[[], None] | None = None) -> None:
    from tolmach import logsetup
    from tolmach.tray import startup

    if not wait_until_free(startup.tray_running):
        # Under pythonw nobody would ever learn why no icon came back.
        logsetup.setup("tray")
        log.error("restart: the old tray did not exit within %.0f s; no new one was started", WAIT_S)
        return
    if start_tray is None:
        from tolmach.tray.__main__ import main as start_tray
    start_tray()


if __name__ == "__main__":
    main()
