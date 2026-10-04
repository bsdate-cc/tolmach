import pytest

from tolmach.client import events as ev
from tolmach.client.recorder import input_names, match_microphone, pick_device, resolve_microphone

HOSTAPIS = [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}]
DEVICES = [
    {"name": "Microphone (Usb Audio Device)", "max_input_channels": 1, "hostapi": 0},
    {"name": "Speakers (Realtek)", "max_input_channels": 0, "hostapi": 0},
    {"name": "Microphone (Usb Audio Device)", "max_input_channels": 1, "hostapi": 1},
    {"name": "Headset Microphone (Poly BT700)", "max_input_channels": 1, "hostapi": 2},
    {"name": "Microphone (Usb Audio Device)", "max_input_channels": 1, "hostapi": 2},
    {"name": "Speakers (Realtek)", "max_input_channels": 0, "hostapi": 2},
]


def test_empty_name_means_default_device():
    assert pick_device("", DEVICES, HOSTAPIS) == (None, False)


def test_wasapi_device_is_preferred():
    assert pick_device("Usb Audio", DEVICES, HOSTAPIS) == (4, True)


def test_match_is_case_insensitive():
    assert pick_device("poly bt700", DEVICES, HOSTAPIS) == (3, True)


def test_falls_back_to_another_host_api():
    devices = DEVICES[:3]
    assert pick_device("Usb Audio", devices, HOSTAPIS) == (0, False)


def test_output_devices_never_match():
    with pytest.raises(ev.RecorderError):
        pick_device("Speakers", DEVICES, HOSTAPIS)


def test_unknown_name_raises_with_the_name_in_the_message():
    with pytest.raises(ev.RecorderError, match="Blue Yeti"):
        pick_device("Blue Yeti", DEVICES, HOSTAPIS)


def test_input_names_lists_wasapi_inputs_once():
    assert input_names(DEVICES, HOSTAPIS) == ["Headset Microphone (Poly BT700)", "Microphone (Usb Audio Device)"]


def test_input_names_without_wasapi_falls_back_to_all_inputs():
    assert input_names(DEVICES[:3], HOSTAPIS) == ["Microphone (Usb Audio Device)"]


def test_rescan_recovers_after_a_failed_initialisation(monkeypatch):
    from types import SimpleNamespace

    import sounddevice

    from tolmach.client import recorder

    state = {"initialised": True, "fail_init": True}

    def terminate():
        if not state["initialised"]:
            raise sounddevice.PortAudioError("Error terminating PortAudio: PortAudio not initialized")
        state["initialised"] = False

    def initialize():
        if state["fail_init"]:
            state["fail_init"] = False
            raise sounddevice.PortAudioError("Error initializing PortAudio")
        state["initialised"] = True

    monkeypatch.setattr(recorder, "sd", SimpleNamespace(
        PortAudioError=sounddevice.PortAudioError, _terminate=terminate, _initialize=initialize))
    with pytest.raises(sounddevice.PortAudioError):
        recorder._rescan()
    recorder._rescan()
    assert state["initialised"]


NAMES = ["Headset Microphone (Poly BT700)", "Microphone (Usb Audio Device)"]


def test_the_button_device_is_matched_to_its_microphone_by_product_name():
    assert match_microphone("Usb Audio Device", NAMES) == "Microphone (Usb Audio Device)"
    assert match_microphone("usb audio device", NAMES) == "Microphone (Usb Audio Device)"


def test_no_product_or_no_matching_input_matches_nothing():
    assert match_microphone("", NAMES) == ""
    assert match_microphone("Some Other Gadget", NAMES) == ""
    assert match_microphone("Usb Audio Device", []) == ""


def test_a_chosen_microphone_always_wins_over_the_button_device():
    assert resolve_microphone("Poly", "Usb Audio Device", NAMES) == "Poly"


def test_with_nothing_chosen_the_button_microphone_is_used():
    assert resolve_microphone("", "Usb Audio Device", NAMES) == "Microphone (Usb Audio Device)"


def test_with_nothing_chosen_and_no_button_microphone_the_system_default_is_used():
    assert resolve_microphone("", "", NAMES) == ""
    assert resolve_microphone("", "Some Other Gadget", NAMES) == ""


# --- opening without a rescan (the rescan costs the first syllable of a dictation)

from tolmach.client import recorder
from tolmach.client.recorder import open_fast, uses_button_microphone


def test_the_device_list_we_already_have_is_tried_first():
    calls = []

    def open_stream(rescan):
        calls.append(rescan)
        return "stream"

    assert open_fast(open_stream) == "stream"
    assert calls == [False]


def test_a_stale_device_list_is_rebuilt_and_the_microphone_opened_again():
    calls = []

    def open_stream(rescan):
        calls.append(rescan)
        if not rescan:
            raise ev.RecorderError("no input device matches 'Usb Audio'")   # plugged in after the last scan
        return "stream"

    assert open_fast(open_stream) == "stream"
    assert calls == [False, True]


def test_a_device_that_went_away_since_the_last_scan_is_retried_after_a_rescan():
    calls = []

    def open_stream(rescan):
        calls.append(rescan)
        if not rescan:
            raise OSError("Invalid device")
        return "stream"

    assert open_fast(open_stream) == "stream"
    assert calls == [False, True]


def test_a_failure_after_the_rescan_is_reported():
    def open_stream(rescan):
        raise ev.RecorderError("no input device matches 'Blue Yeti'")

    with pytest.raises(ev.RecorderError, match="Blue Yeti"):
        open_fast(open_stream)


def test_the_system_default_device_is_never_taken_from_an_old_list(monkeypatch):
    # Which device is "the default" is part of the list too, so it needs a fresh one.
    from types import SimpleNamespace

    rescans = []
    monkeypatch.setattr(recorder, "_rescan", lambda: rescans.append(1))
    monkeypatch.setattr(recorder, "sd", SimpleNamespace(
        query_devices=lambda: DEVICES, query_hostapis=lambda: HOSTAPIS,
        WasapiSettings=lambda auto_convert: None,
        InputStream=lambda **kwargs: SimpleNamespace(start=lambda: None, close=lambda: None)))
    stream = open_fast(lambda rescan: recorder.Recorder(lambda event: None)._open(1, "", rescan))
    assert stream is not None
    assert rescans == [1]


def test_a_named_microphone_in_the_list_is_opened_without_a_rescan(monkeypatch):
    from types import SimpleNamespace

    rescans, opened = [], []
    monkeypatch.setattr(recorder, "_rescan", lambda: rescans.append(1))
    monkeypatch.setattr(recorder, "sd", SimpleNamespace(
        query_devices=lambda: DEVICES, query_hostapis=lambda: HOSTAPIS,
        WasapiSettings=lambda auto_convert: None,
        InputStream=lambda **kwargs: opened.append(kwargs["device"]) or SimpleNamespace(
            start=lambda: None, close=lambda: None)))
    open_fast(lambda rescan: recorder.Recorder(lambda event: None)._open(1, "Usb Audio", rescan))
    assert rescans == []
    assert opened == [4]


# --- is the recording made with the microphone whose button toggles dictation?

def test_with_nothing_chosen_the_recording_is_on_the_button_microphone():
    assert uses_button_microphone("", "Usb Audio Device", DEVICES, HOSTAPIS) is True


def test_a_setting_that_names_the_button_microphone_counts():
    assert uses_button_microphone("Microphone (Usb Audio Device)", "Usb Audio Device", DEVICES, HOSTAPIS) is True
    assert uses_button_microphone("usb audio", "Usb Audio Device", DEVICES, HOSTAPIS) is True


def test_another_chosen_microphone_does_not_count():
    assert uses_button_microphone("Poly", "Usb Audio Device", DEVICES, HOSTAPIS) is False


def test_what_counts_is_the_device_that_is_really_opened():
    # "Microphone" is in both names; the recorder opens the first one, and that is the headset.
    assert pick_device("Microphone", DEVICES, HOSTAPIS) == (3, True)
    assert uses_button_microphone("Microphone", "Usb Audio Device", DEVICES, HOSTAPIS) is False


def test_without_a_button_or_without_its_microphone_nothing_counts():
    assert uses_button_microphone("", "", DEVICES, HOSTAPIS) is False
    assert uses_button_microphone("", "Usb Audio Device", DEVICES[3:4], HOSTAPIS) is False
    assert uses_button_microphone("Blue Yeti", "Usb Audio Device", DEVICES, HOSTAPIS) is False


# --- the Recorder itself, on a faked sounddevice

def fake_sounddevice(monkeypatch, fail_first_open=False):
    from types import SimpleNamespace

    log = SimpleNamespace(rescans=0, opened=[], closed=0, started=0)

    def rescan():
        log.rescans += 1

    def input_stream(**kwargs):
        if fail_first_open and not log.rescans:
            raise OSError("Invalid device")
        log.opened.append(kwargs["device"])
        return SimpleNamespace(start=lambda: setattr(log, "started", log.started + 1), stop=lambda: None,
                               close=lambda: setattr(log, "closed", log.closed + 1))

    monkeypatch.setattr(recorder, "_rescan", rescan)
    monkeypatch.setattr(recorder, "sd", SimpleNamespace(
        query_devices=lambda: DEVICES, query_hostapis=lambda: HOSTAPIS,
        WasapiSettings=lambda auto_convert: None, InputStream=input_stream))
    return log


def test_start_opens_a_listed_microphone_without_rebuilding_the_device_list(monkeypatch):
    log = fake_sounddevice(monkeypatch)
    rec = recorder.Recorder(lambda event: None)
    rec.start(1, "Usb Audio")
    assert (log.rescans, log.opened, log.started) == (0, [4], 1)
    rec.stop()
    assert log.closed == 1


def test_start_rebuilds_the_list_when_the_microphone_does_not_open(monkeypatch):
    log = fake_sounddevice(monkeypatch, fail_first_open=True)
    rec = recorder.Recorder(lambda event: None)
    rec.start(1, "Usb Audio")
    assert (log.rescans, log.opened) == (1, [4])
    rec.stop()


def test_start_reports_a_microphone_that_is_not_there_even_after_a_rescan(monkeypatch):
    log = fake_sounddevice(monkeypatch)
    with pytest.raises(ev.RecorderError, match="Blue Yeti"):
        recorder.Recorder(lambda event: None).start(1, "Blue Yeti")
    assert log.rescans == 1


def test_the_recorder_knows_whether_it_records_from_the_button_microphone(monkeypatch):
    fake_sounddevice(monkeypatch)
    rec = recorder.Recorder(lambda event: None)
    assert rec.on_button_microphone("") is False            # no button known yet
    rec.prefer("Usb Audio Device")
    assert rec.on_button_microphone("") is True
    assert rec.on_button_microphone("Poly") is False


def test_a_device_list_that_cannot_be_read_means_not_the_button_microphone(monkeypatch):
    from types import SimpleNamespace

    def broken():
        raise OSError("PortAudio not initialized")

    monkeypatch.setattr(recorder, "sd", SimpleNamespace(query_devices=broken, query_hostapis=broken))
    rec = recorder.Recorder(lambda event: None)
    rec.prefer("Usb Audio Device")
    assert rec.on_button_microphone("") is False


# --- rebuilding the device list on request (the tray asks when Windows reports a change)

def test_refresh_rebuilds_the_list_and_returns_the_input_names(monkeypatch):
    log = fake_sounddevice(monkeypatch)
    assert recorder.refresh_devices() == ["Headset Microphone (Poly BT700)", "Microphone (Usb Audio Device)"]
    assert log.rescans == 1


def test_refresh_does_nothing_while_a_recording_runs(monkeypatch):
    log = fake_sounddevice(monkeypatch)
    rec = recorder.Recorder(lambda event: None)
    rec.start(1, "Usb Audio")
    assert recorder.refresh_devices() is None           # a rebuild would cut the recording off
    assert log.rescans == 0
    rec.stop()
    assert recorder.refresh_devices() is not None


def test_refresh_that_fails_reports_nothing(monkeypatch):
    fake_sounddevice(monkeypatch)

    def broken():
        raise OSError("PortAudio is gone")

    monkeypatch.setattr(recorder, "_rescan", broken)
    assert recorder.refresh_devices() is None


def test_the_system_default_is_opened_without_a_rebuild_when_the_list_is_known_fresh(monkeypatch):
    log = fake_sounddevice(monkeypatch)
    rec = recorder.Recorder(lambda event: None, list_is_fresh=lambda: True)
    rec.start(1, "")
    assert (log.rescans, log.opened) == (0, [None])
    rec.stop()


def test_the_system_default_still_gets_a_fresh_list_when_nobody_vouches_for_it(monkeypatch):
    log = fake_sounddevice(monkeypatch)
    rec = recorder.Recorder(lambda event: None, list_is_fresh=lambda: False)
    rec.start(1, "")
    assert (log.rescans, log.opened) == (1, [None])
    rec.stop()


# --- COM on the thread that opens the microphone
#
# PortAudio's WASAPI backend needs COM on the thread that starts a stream and initialises
# it only on the thread that built the device list. Since 0.2.0 the list is built elsewhere
# (the tray), so the controller thread had no COM: the first start after every tray launch
# failed with "Unanticipated host error", and the rescan that followed hid it at the price
# of 100 ms. Reproduced 2026-10-04 by opening on a fresh thread.

import threading


def test_start_makes_sure_com_is_up_on_its_thread_before_the_microphone_is_opened(monkeypatch):
    log = fake_sounddevice(monkeypatch)
    order = []
    monkeypatch.setattr(recorder, "_com", threading.local())       # this thread has not been seen yet
    monkeypatch.setattr(recorder, "_co_initialize", lambda: order.append("com"))
    stream_factory = recorder.sd.InputStream
    recorder.sd.InputStream = lambda **kwargs: order.append("open") or stream_factory(**kwargs)
    rec = recorder.Recorder(lambda event: None)
    rec.start(1, "Usb Audio")
    rec.stop()
    assert order == ["com", "open"]
    assert log.rescans == 0


def test_com_is_initialised_once_per_thread(monkeypatch):
    fake_sounddevice(monkeypatch)
    calls = []
    monkeypatch.setattr(recorder, "_co_initialize", lambda: calls.append(threading.current_thread().name))

    def record():
        rec = recorder.Recorder(lambda event: None)
        for session in (1, 2):
            rec.start(session, "Usb Audio")
            rec.stop()

    for name in ("controller-a", "controller-b"):
        thread = threading.Thread(target=record, name=name)
        thread.start()
        thread.join()
    assert calls == ["controller-a", "controller-b"]
