"""Mute other programs while recording and always give their sound back.

muted.json is written BEFORE anything is muted and removed only after the
sound is back, so a crash in between is repaired by restore() on the next start.
Windows keeps a program's mute flag across its restarts, so an entry whose pid
is gone is matched by process name; entries that could not be restored stay
for the next start, but not longer than MAX_AGE_S.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

from tolmach import paths

log = logging.getLogger("tolmach")

MAX_AGE_S = 7 * 24 * 3600


@dataclass(frozen=True)
class AudioSession:
    pid: int
    name: str
    muted: bool


def choose_targets(sessions: list[AudioSession], my_pid: int) -> list[AudioSession]:
    """Unmuted sessions of other processes, one entry per process."""
    targets: list[AudioSession] = []
    seen: set[int] = set()
    for session in sessions:
        if session.pid == my_pid or session.muted or session.pid in seen:
            continue
        seen.add(session.pid)
        targets.append(session)
    return targets


def read_muted() -> list[dict]:
    """Valid entries younger than MAX_AGE_S; one without a time (older format) counts as fresh."""
    try:
        data = json.loads(paths.muted_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    now = time.time()
    entries = []
    for e in data:
        if not (isinstance(e, dict) and isinstance(e.get("pid"), int) and isinstance(e.get("name"), str)):
            continue
        at = e.get("at")
        if not isinstance(at, (int, float)) or isinstance(at, bool):
            at = now
        if now - at > MAX_AGE_S:
            continue
        entries.append({"pid": e["pid"], "name": e["name"], "at": at})
    return entries


def _write_entries(entries: list[dict]) -> None:
    paths.muted_file().write_text(json.dumps(entries), encoding="utf-8")


def write_muted(targets: list[AudioSession]) -> None:
    """Add targets to the list on disk; entries already there stay (an earlier unmute may have failed)."""
    entries = read_muted()
    now = time.time()
    by_key = {(e["pid"], e["name"]): e for e in entries}
    for target in targets:
        entry = by_key.get((target.pid, target.name))
        if entry is not None:
            entry["at"] = now
        else:
            entry = {"pid": target.pid, "name": target.name, "at": now}
            by_key[(target.pid, target.name)] = entry
            entries.append(entry)
    _write_entries(entries)


def clear_muted() -> None:
    try:
        paths.muted_file().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("could not remove muted.json; it is retried at the next start")


def should_restore(entry: dict, session: AudioSession) -> bool:
    """Same pid AND same process name: a reused pid must not unmute a stranger."""
    return entry["pid"] == session.pid and entry["name"] == session.name


def _raw_sessions() -> list:
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetAllSessions()


def _live_sessions() -> list[tuple[AudioSession, object]]:
    """(description, ISimpleAudioVolume) for every audio session that belongs to a process.

    A session that cannot be read (its process just exited) is skipped, the others are still returned.
    """
    result = []
    for session in _raw_sessions():
        try:
            if session.Process is None:
                continue
            volume = session.SimpleAudioVolume
            info = AudioSession(session.Process.pid, session.Process.name(), bool(volume.GetMute()))
        except Exception:
            log.warning("skipping an audio session that could not be read", exc_info=True)
            continue
        result.append((info, volume))
    return result


def _unmute_entry(entry: dict, live: list[tuple[AudioSession, object]]) -> bool:
    """Unmute the sessions of one entry; True when nothing is left to do for it."""
    volumes = [volume for info, volume in live if should_restore(entry, info)]
    if not volumes:
        # The program restarted (new pid) and Windows kept its mute flag.
        same_name = [(info, volume) for info, volume in live if info.name == entry["name"]]
        if not same_name:
            return False
        volumes = [volume for info, volume in same_name if info.muted]
    done = True
    for volume in volumes:
        try:
            volume.SetMute(0, None)
        except Exception:
            log.warning("could not unmute %s", entry["name"], exc_info=True)
            done = False
    return done


def restore() -> None:
    """Unmute everything muted.json lists. Entries that could not be restored stay
    in the file for a retry at the next start; the file goes once nothing is left."""
    try:
        entries = read_muted()
        if not entries:
            clear_muted()
            return
        import comtypes

        comtypes.CoInitialize()
        try:
            live = _live_sessions()
            left = [entry for entry in entries if not _unmute_entry(entry, live)]
        finally:
            comtypes.CoUninitialize()
    except Exception:
        log.exception("could not give the sound back to other programs")
        return
    try:
        if left:
            _write_entries(left)
        else:
            clear_muted()
    except Exception:
        log.exception("could not update muted.json")


class Muter:
    """The controller's `muter` port. Neither method raises."""

    def mute(self) -> None:
        try:
            import comtypes

            comtypes.CoInitialize()
            try:
                live = _live_sessions()
                targets = choose_targets([info for info, _ in live], os.getpid())
                if not targets:
                    return
                write_muted(targets)
                wanted = {target.pid for target in targets}
                for info, volume in live:
                    if info.pid in wanted and not info.muted:
                        volume.SetMute(1, None)
            finally:
                comtypes.CoUninitialize()
        except Exception:
            log.exception("could not mute other programs")

    def unmute(self) -> None:
        try:
            restore()
        except Exception:
            log.exception("could not give the sound back to other programs")
