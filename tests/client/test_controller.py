from tolmach.client import controller as c
from tolmach.client import events as ev

from .fakes import Rig, tone


def test_toggle_starts_recording():
    rig = Rig()
    rig.toggle()
    assert rig.recorder.calls == [("start", 1, "")]
    assert len(rig.streams.opened) == 1
    assert rig.muter.calls == ["mute"]
    assert rig.overlay.last == ("show", c.MSG_LISTENING)
    assert rig.states == [c.RECORDING]


def test_audio_is_forwarded_to_the_stream():
    rig = Rig()
    rig.toggle()
    rig.speak(1, seconds=1.0)
    assert rig.streams.opened[0].sent == 16000


def test_second_toggle_stops_commits_and_unmutes_at_once():
    rig = Rig()
    rig.dictate(1)
    assert rig.recorder.calls[-1] == ("stop",)
    assert rig.streams.opened[0].committed
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.overlay.last == ("show", c.MSG_PASTING)
    assert rig.controller.state == c.FINISHING
    assert rig.paster.pasted == []


def test_committed_pastes_joined_phrases_and_returns_to_idle():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Phrase(1, "Привет."), ev.Draft(1, "как"), ev.Phrase(1, "Как дела?"))
    rig.toggle()
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == ["Привет. Как дела? "]
    assert rig.controller.last_text == "Привет. Как дела?"
    assert rig.streams.opened[0].closed
    assert rig.overlay.last == ("hide",)
    assert rig.states == [c.RECORDING, c.FINISHING, c.IDLE]


def test_drafts_and_phrases_are_shown_while_recording():
    rig = Rig()
    rig.toggle()
    rig.send(ev.Phrase(1, "Привет."), ev.Draft(1, "как дел"))
    assert rig.overlay.last == ("show", "Привет. как дел")


def test_overlay_shows_only_the_tail_of_long_text():
    rig = Rig(overlay_tail_chars=10)
    rig.toggle()
    rig.send(ev.Phrase(1, "один два три четыре"))
    assert rig.overlay.last == ("show", "…три четыре")


def test_toggle_while_finishing_starts_a_new_recording_at_once():
    rig = Rig()
    rig.dictate(1)
    rig.toggle()
    assert rig.recorder.calls[-1] == ("start", 2, "")
    assert len(rig.streams.opened) == 2
    assert rig.controller.state == c.RECORDING


def test_pastes_follow_session_order():
    rig = Rig()
    rig.dictate(1, "первая")
    rig.dictate(2, "вторая")
    rig.send(ev.Committed(2))
    assert rig.paster.pasted == []
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == ["первая ", "вторая "]
    assert rig.controller.state == c.IDLE


def test_late_phrase_of_a_finishing_session_is_kept():
    rig = Rig()
    rig.dictate(1)
    rig.send(ev.Phrase(1, "хвост"), ev.Committed(1))
    assert rig.paster.pasted == ["хвост "]


def test_events_of_unknown_session_are_ignored():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Draft(7, "чужое"), ev.Phrase(7, "чужое"), ev.Committed(7),
             ev.StreamFailed(7, "x"), ev.AudioChunk(7, tone()))
    assert rig.streams.opened[0].sent == 16000
    assert rig.controller.state == c.RECORDING
    assert all("чужое" not in str(call) for call in rig.overlay.calls)
    rig.toggle()
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == []


def test_recording_shorter_than_minimum_is_cancelled():
    rig = Rig()
    rig.toggle()
    rig.speak(1, seconds=0.2)
    rig.toggle()
    stream = rig.streams.opened[0]
    assert stream.closed and not stream.committed
    assert rig.paster.pasted == []
    assert rig.overlay.last == ("hide",)
    assert rig.controller.state == c.IDLE


def test_microphone_failure_reports_and_does_not_start():
    rig = Rig()
    rig.recorder.fail = True
    rig.toggle()
    assert rig.streams.opened == []
    assert rig.muter.calls == []
    assert rig.overlay.messages() == [c.MSG_MIC_MISSING]
    assert rig.controller.state == c.ERROR


def test_gateway_down_at_start_stops_recorder_and_does_not_mute():
    rig = Rig()
    rig.streams.fail = True
    rig.toggle()
    assert rig.recorder.calls == [("start", 1, ""), ("stop",)]
    assert rig.muter.calls == []
    assert rig.overlay.messages() == [c.MSG_GATEWAY_DOWN]
    assert rig.controller.state == c.ERROR


def test_error_state_clears_on_next_successful_start():
    rig = Rig()
    rig.streams.fail = True
    rig.toggle()
    rig.streams.fail = False
    rig.toggle()
    assert rig.states == [c.ERROR, c.RECORDING]


def test_nothing_recognised_shows_message_and_pastes_nothing():
    rig = Rig()
    rig.dictate(1)
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == []
    assert rig.overlay.messages() == [c.MSG_NOTHING]
    assert rig.controller.state == c.IDLE


def test_failed_paste_keeps_last_text():
    rig = Rig()
    rig.paster.ok = False
    rig.dictate(1, "текст")
    rig.send(ev.Committed(1))
    assert rig.controller.last_text == "текст"
    assert rig.overlay.messages() == [c.MSG_PASTE_FAILED]
    assert rig.controller.state == c.ERROR


def test_stream_failure_while_recording_saves_audio_and_pastes_ready_phrases():
    rig = Rig()
    rig.toggle()
    rig.speak(1, seconds=1.0)
    rig.send(ev.Phrase(1, "готово"), ev.StreamFailed(1, "closed"))
    assert rig.recorder.calls[-1] == ("stop",)
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.saved == [16000]
    assert rig.paster.pasted == ["готово "]
    assert rig.overlay.messages() == [c.MSG_LINK_LOST]
    assert rig.controller.state == c.ERROR


def test_missing_committed_times_out_after_15_seconds():
    rig = Rig()
    rig.dictate(1, "готово")
    rig.now += 14.0
    rig.send(ev.Tick())
    assert rig.paster.pasted == []
    rig.now += 1.5
    rig.send(ev.Tick())
    assert rig.paster.pasted == ["готово "]
    assert rig.saved == [16000]
    assert rig.overlay.messages() == [c.MSG_LINK_LOST]


def test_paste_comes_before_the_stream_is_closed():
    rig = Rig()
    order = []
    rig.paster.paste = lambda text, mode: order.append("paste") or True
    rig.toggle()
    rig.streams.opened[0].close = lambda: order.append("close")
    rig.speak(1)
    rig.send(ev.Phrase(1, "готово"))
    rig.toggle()
    rig.now += 15.5
    rig.send(ev.Tick())
    assert order == ["paste", "close"]


def test_mute_is_skipped_when_disabled():
    rig = Rig(mute_other_apps=False)
    rig.dictate(1)
    assert rig.muter.calls == []


def test_quit_stops_everything_without_pasting():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Phrase(1, "текст"))
    assert rig.controller.handle(ev.Quit()) is False
    assert rig.recorder.calls[-1] == ("stop",)
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.streams.opened[0].closed
    assert rig.paster.pasted == []


def test_run_survives_a_broken_handler():
    rig = Rig()

    def boom(session, microphone):
        raise RuntimeError("driver exploded")

    rig.recorder.start = boom
    rig.controller.post(ev.Toggle("hotkey"))
    rig.controller.post(ev.Quit())
    rig.controller.run()  # returns instead of raising
    assert rig.controller.state == c.IDLE


def test_recorder_stop_failure_still_unmutes_commits_and_pastes():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Phrase(1, "текст"))
    rig.recorder.fail_stop = True
    rig.toggle()
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.streams.opened[0].committed
    assert rig.controller.state == c.FINISHING
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == ["текст "]
    assert rig.controller.state == c.IDLE


def test_recorder_stop_failure_on_stream_failure_still_unmutes_and_pastes():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Phrase(1, "готово"))
    rig.recorder.fail_stop = True
    rig.send(ev.StreamFailed(1, "closed"))
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.saved == [16000]
    assert rig.paster.pasted == ["готово "]


def test_recorder_stop_failure_at_quit_still_unmutes_and_closes():
    rig = Rig()
    rig.toggle()
    rig.recorder.fail_stop = True
    assert rig.controller.handle(ev.Quit()) is False
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.streams.opened[0].closed


QUIET = 2  # sine amplitude of about -90 dBFS: under the -70 floor, yet not digital silence


def test_muted_microphone_cancels_without_commit_or_paste():
    rig = Rig()
    rig.toggle()
    rig.speak(1, seconds=0.5, amplitude=QUIET)
    stream = rig.streams.opened[0]
    assert stream.closed and not stream.committed
    assert rig.recorder.calls[-1] == ("stop",)
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.overlay.messages() == [c.MSG_MIC_MUTED]
    assert rig.controller.state == c.IDLE
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == []


def test_next_toggle_after_a_muted_cancel_records_normally():
    rig = Rig()
    rig.toggle()
    rig.speak(1, seconds=0.5, amplitude=QUIET)
    rig.toggle()
    rig.speak(2, seconds=1.0)
    assert rig.controller.state == c.RECORDING
    assert rig.streams.opened[1].sent == 16000


def test_live_microphone_is_not_cancelled():
    rig = Rig()
    rig.toggle()
    rig.speak(1, seconds=1.0)
    assert rig.controller.state == c.RECORDING


def test_one_loud_chunk_in_the_window_keeps_recording():
    rig = Rig()
    rig.toggle()
    rig.speak(1, seconds=0.2, amplitude=QUIET)
    rig.speak(1, seconds=0.1)
    rig.speak(1, seconds=0.5, amplitude=QUIET)
    assert rig.controller.state == c.RECORDING


def test_floor_check_is_off_when_the_threshold_is_null():
    rig = Rig(muted_floor_dbfs=None)
    rig.toggle()
    rig.speak(1, seconds=1.0, amplitude=QUIET)
    assert rig.controller.state == c.RECORDING


def test_autostop_after_silence():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.now += 59.0
    rig.send(ev.Tick())
    assert rig.controller.state == c.RECORDING
    rig.now += 1.5
    rig.send(ev.Tick())
    assert rig.controller.state == c.FINISHING
    assert rig.streams.opened[0].committed


def test_drafts_postpone_autostop():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.now += 50.0
    rig.send(ev.Draft(1, "ещё говорю"))
    rig.now += 50.0
    rig.send(ev.Tick())
    assert rig.controller.state == c.RECORDING


def test_hard_limit_stops_even_while_drafts_arrive():
    rig = Rig(max_recording_s=120.0)
    rig.toggle()
    rig.speak(1)
    for _ in range(3):
        rig.now += 45.0
        rig.send(ev.Draft(1, "говорю"), ev.Tick())
    assert rig.controller.state == c.FINISHING


def test_the_log_tells_how_a_dictation_went_but_never_what_was_said(caplog):
    rig = Rig()
    with caplog.at_level("INFO", logger="tolmach"):
        rig.toggle()
        rig.speak(1)
        rig.send(ev.Phrase(1, "Секретная фраза."))
        rig.toggle()
        rig.send(ev.Committed(1))
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "dictation 1 started" in text
    assert "dictation 1 stopped after" in text
    assert "dictation 1: pasted 16 characters" in text
    assert "Секретная" not in text


def test_the_log_says_when_nothing_was_recognised(caplog):
    rig = Rig()
    with caplog.at_level("INFO", logger="tolmach"):
        rig.dictate(1)
        rig.send(ev.Committed(1))
    assert "dictation 1: nothing recognised" in "\n".join(record.getMessage() for record in caplog.records)


def test_text_is_inserted_the_way_the_settings_say():
    from dataclasses import replace

    rig = Rig()
    rig.dictate(1, "Раз.")
    rig.send(ev.Committed(1))
    assert rig.paster.modes == ["type"]                     # the default

    rig.cfg = replace(rig.cfg, insert_mode="paste")
    rig.dictate(2, "Два.")
    rig.send(ev.Committed(2))
    assert rig.paster.modes == ["type", "paste"]
    assert rig.paster.pasted == ["Раз. ", "Два. "]


def test_a_space_follows_the_text_so_that_two_dictations_do_not_stick_together():
    rig = Rig()
    rig.dictate(1, "Привет.")
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == ["Привет. "]
    assert rig.controller.last_text == "Привет."          # the copy from the menu carries no tail


def test_no_space_follows_the_text_when_the_setting_is_off():
    rig = Rig(append_space=False)
    rig.dictate(1, "Привет.")
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == ["Привет."]


def test_the_error_mark_goes_out_by_itself():
    rig = Rig()
    rig.streams.fail = True
    rig.toggle()
    assert rig.controller.state == c.ERROR
    rig.now += c.ERROR_SHOWN_S - 1.0
    rig.send(ev.Tick())
    assert rig.controller.state == c.ERROR
    rig.now += 1.5
    rig.send(ev.Tick())
    assert rig.controller.state == c.IDLE


def test_the_error_mark_of_a_lost_link_goes_out_by_itself_too():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.StreamFailed(1, "closed"))
    assert rig.controller.state == c.ERROR
    rig.now += c.ERROR_SHOWN_S + 0.5
    rig.send(ev.Tick())
    assert rig.controller.state == c.IDLE


def test_listening_is_shown_as_soon_as_the_microphone_is_open():
    # The gateway connection and the muting of other programs must not delay the cue to speak.
    rig = Rig()
    shown_before = {}
    open_stream, mute = rig.streams.open, rig.muter.mute

    def spying_open(session):
        shown_before["gateway"] = list(rig.overlay.calls)
        return open_stream(session)

    def spying_mute():
        shown_before["mute"] = list(rig.overlay.calls)
        mute()

    rig.streams.open, rig.muter.mute = spying_open, spying_mute
    rig.toggle()
    assert shown_before["gateway"] == [("show", c.MSG_LISTENING)]
    assert shown_before["mute"] == [("show", c.MSG_LISTENING)]


def test_the_log_says_how_long_the_microphone_took_to_open(caplog):
    rig = Rig()
    with caplog.at_level("INFO", logger="tolmach"):
        rig.send(ev.Toggle("button", at=rig.now - 0.085))
    assert "dictation 1: microphone open 85 ms after the button" in caplog.text


# --- the button mutes the microphone in hardware; the program keeps track of which way it points


def stopped_without_the_button(rig):
    """A recording on the button's microphone, stopped by the silence: the microphone stays on."""
    rig.recorder.button_mic = True
    rig.toggle()
    rig.speak(1)
    rig.now += 61.0
    rig.send(ev.Tick(), ev.Committed(1))


def starts(rig):
    return [call for call in rig.recorder.calls if call[0] == "start"]


def test_a_press_that_switches_the_microphone_off_does_not_start_a_recording():
    rig = Rig()
    stopped_without_the_button(rig)
    rig.toggle()
    assert len(starts(rig)) == 1
    assert len(rig.streams.opened) == 1
    assert rig.overlay.last == ("message", c.MSG_MIC_MUTED)
    assert rig.controller.state == c.IDLE


def test_the_press_after_the_swallowed_one_records():
    rig = Rig()
    stopped_without_the_button(rig)
    rig.toggle()
    rig.toggle()
    assert starts(rig)[-1] == ("start", 2, "")
    assert rig.controller.state == c.RECORDING


def test_a_press_after_the_autostop_is_swallowed_too():
    rig = Rig()
    rig.recorder.button_mic = True
    rig.toggle()
    rig.speak(1)
    rig.now += 61.0
    rig.send(ev.Tick(), ev.Committed(1))
    rig.toggle()
    assert len(starts(rig)) == 1
    assert rig.overlay.last == ("message", c.MSG_MIC_MUTED)


def test_presses_in_pairs_never_swallow_anything():
    rig = Rig()
    rig.recorder.button_mic = True
    for session in (1, 2, 3):
        rig.toggle()
        rig.speak(session)
        rig.toggle()
    assert len(starts(rig)) == 3


def test_after_the_button_was_away_nothing_is_assumed_about_the_microphone():
    # While the button was gone a press may have gone unseen, or the microphone lost power.
    rig = Rig()
    stopped_without_the_button(rig)                 # believed "on"
    rig.send(ev.ButtonConnected())
    rig.toggle()
    assert len(starts(rig)) == 2                    # not swallowed: the level check will tell
    assert rig.controller.state == c.RECORDING


def test_a_button_that_was_only_reopened_costs_no_extra_press():
    rig = Rig()
    rig.recorder.button_mic = True
    rig.toggle()
    rig.speak(1)
    rig.toggle()                                    # an ordinary dictation: the microphone is off again
    rig.send(ev.Committed(1), ev.ButtonConnected()) # its HID handle was lost and reopened, nothing else changed
    rig.toggle()
    assert rig.controller.state == c.RECORDING
    assert c.MSG_MIC_MUTED not in rig.overlay.messages()


def test_nothing_is_swallowed_when_the_recording_comes_from_another_microphone():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Toggle("hotkey"), ev.Committed(1))
    rig.toggle()
    assert len(starts(rig)) == 2


def test_nothing_is_swallowed_before_the_microphone_was_heard():
    rig = Rig()
    rig.recorder.button_mic = True
    rig.toggle()
    assert len(starts(rig)) == 1


def test_a_wrong_guess_is_corrected_by_the_muted_check():
    rig = Rig()
    stopped_without_the_button(rig)
    rig.toggle()                                    # believed "off" - in fact the user had it off already
    rig.toggle()                                    # believed "on" - in fact off: the level check notices
    rig.speak(2, seconds=0.5, amplitude=QUIET)
    assert rig.overlay.last == ("message", c.MSG_MIC_MUTED)
    rig.toggle()                                    # now both agree
    rig.speak(3)
    assert rig.controller.state == c.RECORDING
    assert len(starts(rig)) == 3


def test_under_the_hotkey_the_microphone_stays_as_it_is_and_nothing_is_swallowed():
    rig = Rig(control="hotkey")
    rig.recorder.button_mic = True
    rig.send(ev.Toggle("hotkey"))
    rig.speak(1)
    rig.send(ev.Toggle("hotkey"), ev.Committed(1))
    rig.send(ev.Toggle("hotkey"))                   # the microphone is still on: this records
    assert len(starts(rig)) == 2
    assert rig.controller.state == c.RECORDING


# --- the last recognised text can be put in again (a hotkey of its own, and a menu item)


def test_the_last_text_is_inserted_again_the_way_a_dictation_is():
    rig = Rig()
    rig.dictate(1, "Привет.")
    rig.send(ev.Committed(1))
    rig.send(ev.InsertLast("hotkey"))
    assert rig.paster.pasted == ["Привет. ", "Привет. "]
    assert rig.paster.modes == ["type", "type"]
    assert rig.controller.state == c.IDLE


def test_inserting_again_follows_the_settings_of_the_moment():
    from dataclasses import replace

    rig = Rig()
    rig.dictate(1, "Привет.")
    rig.send(ev.Committed(1))
    rig.cfg = replace(rig.cfg, insert_mode="paste", append_space=False)
    rig.send(ev.InsertLast("menu"))
    assert rig.paster.pasted[-1] == "Привет." and rig.paster.modes[-1] == "paste"


def test_with_nothing_dictated_yet_there_is_nothing_to_insert():
    rig = Rig()
    rig.send(ev.InsertLast("hotkey"))
    assert rig.paster.pasted == []
    assert rig.overlay.last == ("message", c.MSG_NO_LAST_TEXT)


def test_an_insert_that_fails_says_so_and_keeps_the_text():
    rig = Rig()
    rig.dictate(1, "Привет.")
    rig.send(ev.Committed(1))
    rig.paster.ok = False
    rig.send(ev.InsertLast("hotkey"))
    assert rig.overlay.last == ("message", c.MSG_PASTE_FAILED)
    assert rig.controller.last_text == "Привет."


def test_the_log_counts_the_characters_inserted_again_and_never_shows_them(caplog):
    rig = Rig()
    rig.dictate(1, "Секрет.")
    rig.send(ev.Committed(1))
    with caplog.at_level("INFO", logger="tolmach"):
        rig.send(ev.InsertLast("hotkey"))
    assert "last text inserted again: 7 characters" in caplog.text
    assert "Секрет" not in caplog.text


# --- a dictation can be dropped: Esc, or the cross on the overlay


def test_a_cancel_while_recording_drops_everything_and_types_nothing():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Phrase(1, "не то"), ev.Cancel("hotkey"))
    stream = rig.streams.opened[0]
    assert stream.closed and not stream.committed
    assert rig.recorder.calls[-1] == ("stop",)
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.paster.pasted == []
    assert rig.saved == []
    assert rig.overlay.last == ("message", c.MSG_CANCELLED)
    assert rig.states == [c.RECORDING, c.IDLE]


def test_a_cancel_after_the_stop_keeps_the_text_from_being_typed():
    rig = Rig()
    rig.dictate(1, "не то")
    rig.send(ev.Cancel("overlay"))
    assert rig.streams.opened[0].closed
    assert rig.overlay.last == ("message", c.MSG_CANCELLED)
    assert rig.controller.state == c.IDLE
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == []


def test_the_last_text_stays_what_it_was_before_the_cancelled_dictation():
    rig = Rig()
    rig.dictate(1, "Хорошо.")
    rig.send(ev.Committed(1))
    rig.toggle()
    rig.speak(2)
    rig.send(ev.Phrase(2, "не то"), ev.Cancel("hotkey"))
    assert rig.controller.last_text == "Хорошо."
    rig.send(ev.InsertLast("hotkey"))
    assert rig.paster.pasted == ["Хорошо. ", "Хорошо. "]


def test_what_the_gateway_says_after_a_cancel_changes_nothing():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Cancel("hotkey"))
    shown = len(rig.overlay.calls)
    rig.send(ev.Draft(1, "поздно"), ev.Phrase(1, "поздно"), ev.Committed(1), ev.StreamFailed(1, "closed"),
             ev.AudioChunk(1, tone()), ev.Tick())
    assert rig.paster.pasted == []
    assert rig.saved == []
    assert len(rig.overlay.calls) == shown
    assert rig.controller.state == c.IDLE


def test_a_cancel_with_nothing_in_progress_does_nothing():
    rig = Rig()
    rig.send(ev.Cancel("hotkey"))
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Cancel("hotkey"), ev.Cancel("hotkey"), ev.Cancel("overlay"))
    assert rig.overlay.messages() == [c.MSG_CANCELLED]
    assert rig.recorder.calls == [("start", 1, ""), ("stop",)]
    assert rig.states == [c.RECORDING, c.IDLE]


def test_a_cancel_drops_the_recording_and_leaves_the_dictation_before_it_to_be_typed():
    rig = Rig()
    rig.dictate(1, "первая")
    rig.toggle()
    rig.speak(2)
    rig.send(ev.Phrase(2, "вторая"), ev.Cancel("hotkey"))
    assert rig.streams.opened[1].closed and not rig.streams.opened[0].closed
    assert rig.overlay.last == ("show", c.MSG_PASTING)
    assert rig.controller.state == c.FINISHING
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == ["первая "]
    assert rig.controller.state == c.IDLE


def test_a_cancel_with_no_recording_drops_everything_that_waits_to_be_typed():
    rig = Rig()
    rig.dictate(1, "первая")
    rig.dictate(2, "вторая")
    rig.send(ev.Cancel("overlay"))
    assert [stream.closed for stream in rig.streams.opened] == [True, True]
    rig.send(ev.Committed(2), ev.Committed(1))
    assert rig.paster.pasted == []
    assert rig.controller.state == c.IDLE


def test_a_recorder_that_will_not_stop_does_not_keep_a_cancel_from_finishing():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.recorder.fail_stop = True
    rig.send(ev.Cancel("hotkey"))
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.streams.opened[0].closed
    assert rig.controller.state == c.IDLE


def test_a_new_dictation_after_a_cancel_goes_as_usual():
    rig = Rig()
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Phrase(1, "не то"), ev.Cancel("hotkey"))
    rig.dictate(2, "То.")
    rig.send(ev.Committed(2))
    assert rig.paster.pasted == ["То. "]
    assert rig.controller.last_text == "То."


def test_a_dictation_started_with_the_hotkey_is_cancelled_for_good_even_on_the_buttons_microphone():
    # The microphone was on before the hotkey and stays on after the cancel: the next press of
    # the hotkey records at once.
    rig = Rig(control="hotkey")
    rig.recorder.button_mic = True
    rig.send(ev.Toggle("hotkey"))
    rig.speak(1)
    rig.send(ev.Cancel("hotkey"))
    assert rig.controller.state == c.IDLE
    assert rig.overlay.last == ("message", c.MSG_CANCELLED)
    assert len(starts(rig)) == 1
    rig.send(ev.Toggle("hotkey"))
    assert starts(rig)[-1] == ("start", 2, "")


# --- the button switches its microphone on and off in hardware, and nothing else can: a dictation
# --- started with it listens again after a cancel, and it is the button that ends it


def started_with_the_button(rig):
    rig.recorder.button_mic = True
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Phrase(1, "не то"))


def test_a_cancel_of_a_dictation_started_with_the_button_listens_again():
    rig = Rig()
    started_with_the_button(rig)
    rig.send(ev.Cancel("hotkey"))
    first = rig.streams.opened[0]
    assert first.closed and not first.committed
    assert rig.recorder.calls == [("start", 1, ""), ("stop",), ("start", 2, "")]
    assert len(rig.streams.opened) == 2
    assert rig.overlay.last == ("show", c.MSG_LISTENING)
    assert rig.overlay.messages() == []
    assert rig.states == [c.RECORDING]                  # never left: the key that cancels stays ours
    rig.speak(2)
    rig.send(ev.Phrase(2, "То."))
    rig.toggle()
    rig.send(ev.Committed(2))
    assert rig.paster.pasted == ["То. "]
    assert rig.controller.state == c.IDLE


def test_the_other_programs_stay_muted_while_it_listens_again():
    rig = Rig()
    started_with_the_button(rig)
    rig.send(ev.Cancel("overlay"))
    assert rig.muter.calls == ["mute"]                  # no gap of sound between the two recordings
    rig.speak(2)
    rig.toggle()
    assert rig.muter.calls == ["mute", "unmute"]


def test_the_button_ends_it_and_with_nothing_said_nothing_is_typed():
    rig = Rig()
    started_with_the_button(rig)
    rig.send(ev.Cancel("hotkey"))
    rig.toggle()                                        # the button: its microphone is off again
    assert rig.streams.opened[1].closed and not rig.streams.opened[1].committed
    assert rig.paster.pasted == []
    assert rig.controller.state == c.IDLE
    rig.toggle()                                        # and the next press records, at once
    assert starts(rig)[-1] == ("start", 3, "")
    assert c.MSG_MIC_MUTED not in rig.overlay.messages()


def test_every_cancel_listens_again():
    rig = Rig()
    started_with_the_button(rig)
    rig.send(ev.Cancel("hotkey"))
    rig.speak(2)
    rig.send(ev.Phrase(2, "опять не то"), ev.Cancel("overlay"))
    assert len(starts(rig)) == 3
    assert [stream.closed for stream in rig.streams.opened] == [True, True, False]
    assert rig.controller.state == c.RECORDING
    rig.speak(3)
    rig.send(ev.Phrase(3, "То."))
    rig.toggle()
    rig.send(ev.Committed(3))
    assert rig.paster.pasted == ["То. "]


def test_what_the_gateway_says_about_the_cancelled_one_does_not_reach_the_new_one():
    rig = Rig()
    started_with_the_button(rig)
    rig.send(ev.Cancel("hotkey"))
    rig.send(ev.Draft(1, "поздно"), ev.Phrase(1, "поздно"), ev.Committed(1), ev.StreamFailed(1, "closed"),
             ev.AudioChunk(1, tone()))
    assert rig.overlay.last == ("show", c.MSG_LISTENING)
    assert rig.streams.opened[1].sent == 0
    assert rig.controller.state == c.RECORDING and rig.saved == []
    rig.speak(2)
    rig.toggle()
    rig.send(ev.Committed(2))
    assert rig.paster.pasted == []


def test_listening_again_that_cannot_start_gives_the_sound_back():
    for broken, message in (("streams", c.MSG_GATEWAY_DOWN), ("recorder", c.MSG_MIC_MISSING)):
        rig = Rig()
        started_with_the_button(rig)
        getattr(rig, broken).fail = True
        rig.send(ev.Cancel("hotkey"))
        assert rig.muter.calls == ["mute", "unmute"], broken
        assert rig.overlay.last == ("message", message), broken
        assert rig.controller.state == c.ERROR, broken
        assert rig.paster.pasted == []


def test_with_muting_switched_off_listening_again_mutes_nothing():
    rig = Rig(mute_other_apps=False)
    started_with_the_button(rig)
    rig.send(ev.Cancel("hotkey"))
    rig.speak(2)
    rig.toggle()
    assert rig.muter.calls == []


def test_after_the_button_stopped_it_a_cancel_is_for_good():
    # "Вставляю…": the button has switched its microphone off already - nothing to listen with.
    rig = Rig()
    started_with_the_button(rig)
    rig.toggle()
    rig.send(ev.Cancel("overlay"))
    assert rig.controller.state == c.IDLE
    assert rig.overlay.last == ("message", c.MSG_CANCELLED)
    assert len(starts(rig)) == 1
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == []


def test_the_log_shows_the_cancel_and_the_new_start_and_never_what_was_said(caplog):
    rig = Rig()
    with caplog.at_level("INFO", logger="tolmach"):
        started_with_the_button(rig)
        rig.send(ev.Cancel("hotkey"))
    assert "dictation 1 cancelled (hotkey): 1.0 s of audio dropped" in caplog.text
    assert "dictation 2 started: listening again" in caplog.text
    assert "не то" not in caplog.text


def test_the_log_says_a_dictation_was_cancelled_and_never_what_was_said(caplog):
    rig = Rig()
    with caplog.at_level("INFO", logger="tolmach"):
        rig.toggle()
        rig.speak(1)
        rig.send(ev.Phrase(1, "Секретная фраза."), ev.Cancel("overlay"))
    assert "dictation 1 cancelled (overlay): 1.0 s of audio dropped" in caplog.text
    assert "Секретная" not in caplog.text


def test_every_way_a_dictation_ends_leaves_nothing_to_cancel():
    # Esc is taken from the other programs for as long as the state is one of CANCELLABLE:
    # a dictation that ended in any way must not leave it there.
    def typed(rig):
        rig.dictate(1, "текст")
        rig.send(ev.Committed(1))

    def nothing_recognised(rig):
        rig.dictate(1)
        rig.send(ev.Committed(1))

    def cancelled(rig):
        rig.toggle()
        rig.speak(1)
        rig.send(ev.Cancel("hotkey"))

    def cancelled_after_the_stop(rig):
        rig.dictate(1, "текст")
        rig.send(ev.Cancel("overlay"))

    def too_short(rig):
        rig.toggle()
        rig.speak(1, seconds=0.2)
        rig.toggle()

    def muted(rig):
        rig.toggle()
        rig.speak(1, seconds=0.5, amplitude=QUIET)

    def link_lost(rig):
        rig.toggle()
        rig.speak(1)
        rig.send(ev.StreamFailed(1, "closed"))

    def no_answer(rig):
        rig.dictate(1, "текст")
        rig.now += c.COMMIT_TIMEOUT_S + 0.5
        rig.send(ev.Tick())

    def gateway_down(rig):
        rig.streams.fail = True
        rig.toggle()

    def no_microphone(rig):
        rig.recorder.fail = True
        rig.toggle()

    assert c.CANCELLABLE == (c.RECORDING, c.FINISHING)
    for ending in (typed, nothing_recognised, cancelled, cancelled_after_the_stop, too_short, muted,
                   link_lost, no_answer, gateway_down, no_microphone):
        rig = Rig()
        ending(rig)
        assert rig.controller.state not in c.CANCELLABLE, ending.__name__
        assert rig.states[-1] not in c.CANCELLABLE, ending.__name__


# --- what starts and stops a dictation: the microphone button, or the hotkey and the menu. A microphone
# --- with a button is switched by a finger only, so the two do not mix: "auto" gives it to the button
# --- when the recording comes from the button's own microphone, and to the hotkey otherwise


def test_on_the_buttons_microphone_the_hotkey_and_the_menu_start_nothing():
    for source in ("hotkey", "menu"):
        rig = Rig()                                 # control: "auto"
        rig.recorder.button_mic = True
        rig.send(ev.Toggle(source))
        assert starts(rig) == [], source
        assert rig.overlay.calls == [("message", c.MSG_BUTTON_RULES)], source
        assert rig.states == [], source


def test_on_the_buttons_microphone_the_hotkey_stops_nothing_either():
    rig = Rig()
    rig.recorder.button_mic = True
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Toggle("hotkey"))
    assert rig.controller.state == c.RECORDING
    assert not rig.streams.opened[0].committed
    assert rig.overlay.last == ("message", c.MSG_BUTTON_RULES)
    rig.toggle()                                    # the button does
    assert rig.streams.opened[0].committed


def test_on_any_other_microphone_the_hotkey_and_the_button_both_work():
    rig = Rig()                                     # control: "auto", no button's microphone
    rig.send(ev.Toggle("hotkey"))
    rig.speak(1)
    rig.send(ev.Toggle("hotkey"), ev.Committed(1))
    rig.toggle()                                    # a button of another device: a remote control
    assert len(starts(rig)) == 2
    assert c.MSG_BUTTON_RULES not in rig.overlay.messages()


def test_set_to_the_button_the_hotkey_does_nothing_on_any_microphone():
    rig = Rig(control="button")
    rig.send(ev.Toggle("hotkey"))
    assert starts(rig) == []
    assert rig.overlay.last == ("message", c.MSG_BUTTON_RULES)
    rig.toggle()
    assert len(starts(rig)) == 1


def test_set_to_the_hotkey_the_button_only_switches_its_microphone():
    rig = Rig(control="hotkey")
    rig.recorder.button_mic = True
    rig.toggle()                                    # the user switches the microphone on
    assert starts(rig) == [] and rig.overlay.calls == []
    rig.send(ev.Toggle("hotkey"))
    rig.speak(1)
    rig.send(ev.Phrase(1, "Привет."))
    rig.toggle()                                    # a press in the middle is not a stop
    assert rig.controller.state == c.RECORDING
    rig.send(ev.Toggle("hotkey"), ev.Committed(1))
    assert rig.paster.pasted == ["Привет. "]


def test_set_to_the_hotkey_a_microphone_switched_off_is_noticed_as_before():
    rig = Rig(control="hotkey")
    rig.recorder.button_mic = True
    rig.send(ev.Toggle("hotkey"))
    rig.speak(1, seconds=0.5, amplitude=QUIET)      # the microphone is off: its button was never pressed
    assert rig.overlay.last == ("message", c.MSG_MIC_MUTED)
    assert rig.controller.state == c.IDLE


def test_the_last_text_is_inserted_again_whatever_starts_a_dictation():
    rig = Rig()
    rig.recorder.button_mic = True
    rig.dictate(1, "Привет.")
    rig.send(ev.Committed(1), ev.InsertLast("hotkey"))
    assert rig.paster.pasted == ["Привет. ", "Привет. "]


def test_the_setting_is_read_at_every_press():
    from dataclasses import replace

    rig = Rig()
    rig.recorder.button_mic = True
    rig.send(ev.Toggle("hotkey"))
    assert starts(rig) == []
    rig.cfg = replace(rig.cfg, control="hotkey")    # chosen in the menu meanwhile
    rig.send(ev.Toggle("hotkey"))
    assert len(starts(rig)) == 1


def test_a_dictation_keeps_to_the_end_what_it_began_with():
    # The setting changed in the middle - by hand in the file, the menu does not let one: the
    # button that began this dictation still ends it, and the hotkey still does not.
    from dataclasses import replace

    rig = Rig()
    rig.recorder.button_mic = True
    rig.toggle()
    rig.speak(1)
    rig.cfg = replace(rig.cfg, control="hotkey")
    rig.send(ev.Toggle("hotkey"))
    assert rig.controller.state == c.RECORDING
    assert rig.overlay.last == ("message", c.MSG_BUTTON_RULES)
    rig.toggle()
    assert rig.controller.state == c.FINISHING
    rig.send(ev.Committed(1), ev.Toggle("hotkey"))  # the next one follows the new setting
    assert len(starts(rig)) == 2


def test_the_log_says_which_press_was_not_taken(caplog):
    with caplog.at_level("INFO", logger="tolmach"):
        rig = Rig()
        rig.recorder.button_mic = True
        rig.send(ev.Toggle("hotkey"))
        rig = Rig(control="hotkey")
        rig.toggle()
    assert "hotkey press not taken: the microphone button starts and stops a dictation" in caplog.text
    assert "button press not taken: the hotkey starts and stops a dictation" in caplog.text


# --- a notice must not take the overlay away from a dictation in progress: a microphone that is
# --- open with nothing on the screen to say so is the worst the overlay can do


def test_a_notice_during_a_recording_goes_back_to_what_was_said_so_far():
    rig = Rig()
    rig.recorder.button_mic = True
    rig.toggle()
    rig.send(ev.Toggle("hotkey"))                   # the hint about the button, before a word was said
    assert rig.overlay.last == ("message", c.MSG_BUTTON_RULES)
    assert rig.overlay.back_to[-1] == c.MSG_LISTENING
    rig.speak(1)
    rig.send(ev.Phrase(1, "Привет."), ev.Toggle("hotkey"))
    assert rig.overlay.back_to[-1] == "Привет."


def test_a_notice_about_the_dictation_before_goes_back_to_the_one_being_recorded():
    rig = Rig()
    rig.dictate(1)
    rig.toggle()
    rig.speak(2)
    rig.send(ev.Phrase(2, "вторая"), ev.Committed(1))
    assert rig.overlay.last == ("message", c.MSG_NOTHING)
    assert rig.overlay.back_to[-1] == "вторая"


def test_a_notice_while_a_text_waits_to_be_typed_goes_back_to_that():
    rig = Rig()
    rig.dictate(1, "текст")
    rig.streams.fail = True
    rig.toggle()                                    # a new dictation that cannot start
    assert rig.overlay.last == ("message", c.MSG_GATEWAY_DOWN)
    assert rig.overlay.back_to[-1] == c.MSG_PASTING


def test_a_notice_with_nothing_in_progress_goes_away():
    rig = Rig()
    rig.send(ev.InsertLast("hotkey"))               # nothing to insert yet
    rig.toggle()
    rig.speak(1)
    rig.send(ev.Cancel("hotkey"))                   # cancelled
    rig.dictate(2)
    rig.send(ev.Committed(2))                       # nothing recognised
    assert rig.overlay.messages() == [c.MSG_NO_LAST_TEXT, c.MSG_CANCELLED, c.MSG_NOTHING]
    assert rig.overlay.back_to == [None, None, None]


# --- the button's microphone is pulled out in the middle of a dictation it began: there is no button
# --- left to end it with, and nothing to listen again with


def test_with_the_buttons_microphone_gone_a_cancel_is_for_good():
    rig = Rig()
    started_with_the_button(rig)
    rig.recorder.button_mic = False                 # unplugged
    rig.send(ev.Cancel("overlay"))
    assert rig.controller.state == c.IDLE
    assert rig.overlay.last == ("message", c.MSG_CANCELLED)
    assert len(starts(rig)) == 1
    assert rig.muter.calls == ["mute", "unmute"]
    assert rig.paster.pasted == []


def test_with_the_buttons_microphone_gone_the_hotkey_ends_the_dictation():
    rig = Rig()                                     # control: "auto"
    started_with_the_button(rig)
    rig.recorder.button_mic = False
    rig.send(ev.Toggle("hotkey"))
    assert rig.controller.state == c.FINISHING
    assert c.MSG_BUTTON_RULES not in rig.overlay.messages()
    rig.send(ev.Committed(1))
    assert rig.paster.pasted == ["не то "]          # what was said before the microphone went


def test_set_to_the_button_the_hotkey_stays_out_even_then_and_the_cross_ends_it():
    rig = Rig(control="button")
    started_with_the_button(rig)
    rig.recorder.button_mic = False
    rig.send(ev.Toggle("hotkey"))
    assert rig.controller.state == c.RECORDING
    assert rig.overlay.last == ("message", c.MSG_BUTTON_RULES)
    rig.send(ev.Cancel("overlay"))
    assert rig.controller.state == c.IDLE


def test_a_microphone_plugged_back_in_is_the_buttons_again():
    rig = Rig()
    started_with_the_button(rig)
    rig.recorder.button_mic = False
    rig.recorder.button_mic = True                  # and back before anything was pressed
    rig.send(ev.Toggle("hotkey"))
    assert rig.controller.state == c.RECORDING
    rig.send(ev.Cancel("hotkey"))
    assert len(starts(rig)) == 2                    # listening again, as ever


def test_a_buttons_microphone_plugged_in_meanwhile_does_not_take_over_a_dictation_on_another():
    rig = Rig()
    rig.send(ev.Toggle("hotkey"))
    rig.speak(1)
    rig.recorder.button_mic = True                  # the next dictation would come from it; this one does not
    rig.send(ev.Toggle("hotkey"))
    assert rig.controller.state == c.FINISHING
    assert c.MSG_BUTTON_RULES not in rig.overlay.messages()
