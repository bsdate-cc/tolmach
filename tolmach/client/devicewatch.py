"""Tells when Windows changed its set of audio devices.

PortAudio builds its device list once and learns nothing afterwards, so the list has
to be rebuilt for a replugged microphone to be found. Rebuilding it on a timer works,
but does work every 30 seconds for an event that happens a few times a day; Windows
reports every change itself (IMMNotificationClient), and that is what is listened to
here. The notifications arrive on a thread of the audio service, so nothing is done
there but raising a flag: the list is rebuilt later, by whoever calls refresh().
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("tolmach")


def _subscribe(notify: Callable[[], None]) -> Callable[[], None]:
    """Register for audio endpoint notifications on the calling thread; returns the
    function that takes the registration back (to be called on the same thread)."""
    import comtypes
    from pycaw.callbacks import MMNotificationClient
    from pycaw.utils import AudioUtilities

    class Client(MMNotificationClient):
        # The raw methods are overridden, not pycaw's on_* hooks: its own wrappers look
        # the state up in a table and raise on a combination that is not in it.
        def OnDefaultDeviceChanged(self, flow_id, role_id, default_device_id):
            notify()

        def OnDeviceAdded(self, device_id):
            notify()

        def OnDeviceRemoved(self, device_id):
            notify()

        def OnDeviceStateChanged(self, device_id, new_state_id):
            notify()

        def OnPropertyValueChanged(self, device_id, property_struct):
            pass  # a renamed device or a changed format: frequent, and not a new device

    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        initialised = True
    except OSError:
        initialised = False  # the first import of comtypes on this thread did it already
    try:
        enumerator = AudioUtilities.GetDeviceEnumerator()
        client = Client()
        enumerator.RegisterEndpointNotificationCallback(client)
    except BaseException:
        if initialised:
            comtypes.CoUninitialize()
        raise

    def unsubscribe() -> None:
        nonlocal enumerator, client
        try:
            enumerator.UnregisterEndpointNotificationCallback(client)
        finally:
            enumerator = client = None  # the COM pointers go before COM itself does
            if initialised:
                comtypes.CoUninitialize()

    return unsubscribe


class DeviceWatch:
    """Knows whether the device list is due for a rebuild.

    With the subscription in place (`active`), it is due once at the start and after
    every notification. Without it - not up yet, or it could not be made - the caller's
    own timer decides, as before."""

    def __init__(self, subscribe: Callable[[Callable[[], None]], Callable[[], None]] = _subscribe):
        self._subscribe = subscribe
        self._changed = threading.Event()
        self._changed.set()  # nothing was built yet
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="devices", daemon=True)
        self._started = False
        self.active = False

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        if self._started:
            self._thread.join(2.0)

    def pending(self) -> bool:
        return self._changed.is_set()

    def refresh(self, periodic: bool, rebuild: Callable[[], list[str] | None]) -> list[str] | None:
        """Run `rebuild` if the list is due and return what it returned, else None.
        `periodic` is the caller's timer, used only while there are no notifications.
        A rebuild that returns None could not be done now (a recording is running):
        the list stays due."""
        if not (self._changed.is_set() if self.active else periodic):
            return None
        self._changed.clear()  # before the rebuild: a change reported during it must not be lost
        names = rebuild()
        if names is None:
            self._changed.set()
        elif self.active:
            log.info("audio devices: the list was rebuilt, %d input device(s)", len(names))
        return names

    def _run(self) -> None:
        # Subscribing and unsubscribing happen on this one thread: COM is initialised per thread.
        try:
            unsubscribe = self._subscribe(self._changed.set)
        except Exception:
            log.exception("no audio device notifications: the device list will be polled")
            return
        self.active = True
        log.info("watching audio devices")
        try:
            self._stopping.wait()
        finally:
            self.active = False
            try:
                unsubscribe()
            except Exception:
                log.exception("could not take the audio device subscription back")
