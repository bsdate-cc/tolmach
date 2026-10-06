"""Tray entry point: single instance, autostart projection, then the icon."""
from __future__ import annotations

import json
import msvcrt
import os
from pathlib import Path

from tolmach import VERSION, config, logsetup, paths


def single_instance():
    """Take a byte lock on tray.lock and return the open file (keep it for the life of
    the process); None means another tray already holds it."""
    handle = open(paths.tray_lock(), "a+")
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def publish_pid() -> None:
    """Say which process holds the lock, so that tolmach.cmd can restart exactly this tray.
    Best effort: a tray that cannot write the file still runs."""
    try:
        paths.tray_file().write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass


def withdraw_pid() -> None:
    try:
        paths.tray_file().unlink()
    except OSError:
        pass


def main() -> None:
    from tolmach.tray import startup  # standard library only

    carried: list[str] = []
    startup.migrate_legacy(carried.append)  # before the lock: taking it creates the data folder
    lock = single_instance()
    if lock is None:
        return  # a second tray exits silently
    publish_pid()  # only the winner of the lock: the loser's pid would aim a restart at the wrong process
    log = logsetup.setup("tray")
    log.info("Tolmach tray %s starting", VERSION)
    for line in carried:
        log.info("data folder of the former name: %s", line.strip())
    try:
        from tolmach.tray import autostart, gatewayctl
        from tolmach.tray.app import TrayApp
    except Exception:
        # A missing library after an update: under pythonw this would be a tray that
        # never appears, with nothing anywhere to say why.
        log.exception("tray could not start: run tolmach.cmd, it checks and installs the libraries")
        withdraw_pid()
        lock.close()
        raise

    root = Path(__file__).resolve().parents[2]
    loaded = config.load_reported()
    if config.unparsed(loaded):
        # defaults say autostart: true — a broken file must not register the tray with Windows
        log.warning("autostart entry left as it is: config.json could not be parsed")
    else:
        try:
            autostart.apply(loaded.config.autostart, root, gatewayctl.pythonw_path())
        except OSError:
            log.exception("could not update the autostart entry")
    try:
        TrayApp(root).run()
    except Exception:
        log.exception("tray crashed")
        raise
    finally:
        withdraw_pid()
        lock.close()
        log.info("Tolmach tray stopped")


if __name__ == "__main__":
    main()
