"""Dictation rules: one thread, one queue, no Windows calls.

Everything external comes in through Ports, so tests drive the controller
with fakes. Recording and pasting are decoupled: a stopped session moves to
the finishing list and is pasted when the gateway confirms it, while a new
recording may already be running.
"""
from __future__ import annotations

import logging
import queue
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from tolmach.client import events as ev
from tolmach.client.level import FloorCheck
from tolmach.client.textbuf import TextBuffer, tail
from tolmach.config import ClientConfig
from tolmach.i18n import N_, _

log = logging.getLogger("tolmach")

RATE = 16000
COMMIT_TIMEOUT_S = 15.0
MESSAGE_SECONDS = 3.0
MUTED_MESSAGE_SECONDS = 2.0
CANCELLED_MESSAGE_SECONDS = 1.5
ERROR_SHOWN_S = 30.0

IDLE, RECORDING, FINISHING, ERROR = "idle", "recording", "finishing", "error"
# The states in which there is something to cancel: a recording, or a text waiting to be typed.
CANCELLABLE = (RECORDING, FINISHING)

MSG_LISTENING = N_("Слушаю…")
MSG_PASTING = N_("Вставляю…")
MSG_GATEWAY_DOWN = N_("Шлюз не отвечает")
MSG_LINK_LOST = N_("Связь потеряна, запись сохранена")
MSG_MIC_MISSING = N_("Микрофон не найден")
MSG_MIC_MUTED = N_("Микрофон выключен — нажмите кнопку ещё раз")
MSG_NOTHING = N_("Ничего не распознано")
MSG_PASTE_FAILED = N_("Не удалось вставить")
MSG_NO_LAST_TEXT = N_("Вставлять пока нечего")
MSG_CANCELLED = N_("Отменено")
MSG_BUTTON_RULES = N_("Диктовкой управляет кнопка микрофона")


def button_rules(control: str, button_mic: bool) -> bool:
    """Whether the microphone button - and not the hotkey or the menu item - starts and stops a
    dictation: the setting client.control, and whether the recording comes from the button's
    own microphone."""
    return control == "button" or (control == "auto" and button_mic)


@dataclass
class Ports:
    load_config: Callable[[], ClientConfig]
    recorder: Any   # start(session, microphone) raises RecorderError; stop(); on_button_microphone(microphone)
    streams: Any    # open(session) -> stream, raises StreamError; stream.send/commit/close never raise
    # show(text): something in progress, with the cross that cancels it;
    # message(text, seconds, back_to): a notice, then back to `back_to` (or hidden, with None); hide()
    overlay: Any
    paster: Any     # paste(text) -> bool
    muter: Any      # mute(), unmute(); never raise
    clock: Callable[[], float]
    save_wav: Callable[[np.ndarray], Any]
    on_state: Callable[[str], None]


@dataclass
class _Session:
    id: int
    cfg: ClientConfig
    stream: Any
    started: float
    text: TextBuffer = field(default_factory=TextBuffer)
    chunks: list = field(default_factory=list)
    samples: int = 0
    muted: bool = False
    button_mic: bool = False
    by_button: bool = False
    stopped: float | None = None
    done: bool = False
    failed: bool = False
    floor: FloorCheck | None = None
    last_activity: float = 0.0


class Controller:
    def __init__(self, ports: Ports, events: "queue.Queue | None" = None):
        self.ports = ports
        self.events = events if events is not None else queue.Queue()
        self.last_text = ""
        self._recording: _Session | None = None
        self._finishing: list[_Session] = []
        self._next_id = 1
        self._error = False
        self._error_since = 0.0
        # Whether the button's microphone is switched on, as far as we know. The button
        # toggles it in hardware and reports no state: every press flips this,
        # the level check of a recording sets it, None means "not known" - not heard yet,
        # or the button was away for a while.
        self._mic_live: bool | None = None
        self._state = IDLE

    @property
    def state(self) -> str:
        return self._state

    def post(self, event) -> None:
        self.events.put(event)

    def run(self) -> None:
        """Consume events until Quit. One broken handler must not kill the loop."""
        while True:
            event = self.events.get()
            try:
                if not self.handle(event):
                    return
            except Exception:
                log.exception("controller: handler failed for %s", type(event).__name__)

    def handle(self, event) -> bool:
        """Process one event; False means the loop must stop."""
        if isinstance(event, ev.AudioChunk):
            self._on_chunk(event)
        elif isinstance(event, ev.Toggle):
            self._on_toggle(event)
        elif isinstance(event, ev.InsertLast):
            self._insert_last()
        elif isinstance(event, ev.Cancel):
            self._cancel(event)
        elif isinstance(event, ev.ButtonConnected):
            # A wrong "on" would swallow a press that in fact switched the microphone on
            # (three presses to get a recording); "not known" costs at most the old
            # start-and-cancel. So nothing is assumed.
            self._mic_live = None
        elif isinstance(event, ev.Draft):
            self._on_text(event.session, draft=event.text)
        elif isinstance(event, ev.Phrase):
            self._on_text(event.session, phrase=event.text)
        elif isinstance(event, ev.Committed):
            self._on_committed(event.session)
        elif isinstance(event, ev.StreamFailed):
            self._on_stream_failed(event.session, event.reason)
        elif isinstance(event, ev.Tick):
            self._on_tick()
        elif isinstance(event, ev.Quit):
            self._quit()
            return False
        return True

    # -- recording ----------------------------------------------------------

    def _on_toggle(self, event: ev.Toggle) -> None:
        pressed = event.source == "button"
        if pressed and self._mic_live is not None:
            self._mic_live = not self._mic_live
        cfg = self.ports.load_config()
        session = self._recording
        if session is None:
            button_mic = self.ports.recorder.on_button_microphone(cfg.microphone)
        elif pressed:
            button_mic = session.button_mic  # the press itself says that the button is there
        else:
            button_mic = self._button_is_there(session)
        # A microphone with a button is switched by a finger and by nothing else, so the button
        # and the hotkey do not mix: one of them starts and stops a dictation (client.control).
        # A dictation being recorded keeps to the end what it began with.
        control = session.cfg.control if session is not None else cfg.control
        if pressed and control == "hotkey":
            log.info("button press not taken: the hotkey starts and stops a dictation")
            return  # it has switched its microphone, and that is all it does here
        if not pressed and button_rules(control, button_mic):
            log.info("%s press not taken: the microphone button starts and stops a dictation", event.source)
            self._notify(MSG_BUTTON_RULES, MUTED_MESSAGE_SECONDS)
            return
        if session is not None:
            self._stop()
            return
        if pressed and button_mic and self._mic_live is False:
            # The microphone was left on (the last recording was not stopped with the
            # button, or it was just plugged in): this press switched it off.
            log.info("button press not taken as a start: it switched the microphone off")
            self._notify(MSG_MIC_MUTED, MUTED_MESSAGE_SECONDS)
            return
        self._start(cfg, button_mic, event)

    def _start(self, cfg: ClientConfig, button_mic: bool, event: ev.Toggle,
               after: "_Session | None" = None) -> None:
        """Begin a recording. `after` is the cancelled recording this one takes the place of."""
        session_id = self._next_id
        self._next_id += 1
        # The microphone first, then the cue: whatever is said after "Слушаю…" is on the
        # recording. The gateway and the other programs' sound come after both.
        try:
            self.ports.recorder.start(session_id, cfg.microphone)
        except ev.RecorderError as e:
            log.warning("recording did not start: %s", e)
            self._fail(MSG_MIC_MISSING)
            return
        opened = self.ports.clock()
        self.ports.overlay.show(_(MSG_LISTENING))
        if event.at is not None:
            log.info("dictation %d: microphone open %.0f ms after the %s",
                     session_id, (opened - event.at) * 1000, event.source)
        try:
            stream = self.ports.streams.open(session_id)
        except ev.StreamError as e:
            log.warning("gateway is not reachable: %s", e)
            self._stop_recorder()
            self._fail(MSG_GATEWAY_DOWN)
            return
        session = _Session(session_id, cfg, stream, opened)
        session.last_activity = session.started
        session.button_mic = button_mic
        session.by_button = event.source == "button"
        if cfg.muted_floor_dbfs is not None:
            session.floor = FloorCheck(cfg.muted_floor_dbfs)
        self._recording = session
        self._error = False
        if after is not None and after.muted:
            session.muted = True  # the other programs are muted still: no gap of sound in between
        elif cfg.mute_other_apps:
            session.muted = True
            self.ports.muter.mute()
        log.info("dictation %d started%s", session_id, ": listening again" if after is not None else "")
        self._refresh_state()

    def _stop(self) -> None:
        session = self._recording
        self._recording = None
        self._stop_recorder()
        self._unmute(session)
        seconds = session.samples / RATE
        if session.samples < session.cfg.min_recording_s * RATE:
            log.info("dictation %d dropped: only %.1f s of audio", session.id, seconds)
            session.stream.close()
            self.ports.overlay.hide()
            self._refresh_state()
            return
        log.info("dictation %d stopped after %.1f s", session.id, seconds)
        session.stream.commit()
        session.stopped = self.ports.clock()
        self._finishing.append(session)
        self.ports.overlay.show(_(MSG_PASTING))
        self._refresh_state()

    def _stop_recorder(self) -> None:
        """Stop the recorder; a failing device must not skip unmute or commit."""
        try:
            self.ports.recorder.stop()
        except Exception:
            log.exception("recorder did not stop cleanly")

    def _unmute(self, session: _Session) -> None:
        if session.muted:
            session.muted = False
            self.ports.muter.unmute()

    def _on_chunk(self, event: ev.AudioChunk) -> None:
        session = self._recording
        if session is None or session.id != event.session:
            return
        session.chunks.append(event.samples)
        session.samples += int(event.samples.size)
        session.stream.send(event.samples)
        muted = session.floor.feed(event.samples) if session.floor is not None else None
        if muted is not None and session.button_mic:
            self._mic_live = not muted
        if muted:
            self._cancel_muted(session)

    def _cancel_muted(self, session: _Session) -> None:
        """The first half second stayed at the muted floor: the button just muted
        the microphone. Drop the recording; now we agree with the microphone."""
        log.info("recording cancelled: the microphone is muted")
        self._recording = None
        self._stop_recorder()
        self._unmute(session)
        session.stream.close()
        self._notify(MSG_MIC_MUTED, MUTED_MESSAGE_SECONDS)
        self._refresh_state()

    def _button_is_there(self, session: _Session) -> bool:
        """Whether this recording comes from a microphone whose button is there to be pressed
        now. It began that way or it did not - but the microphone may have been pulled out
        since, and with it the only thing that could end the dictation or listen again."""
        return session.button_mic and self.ports.recorder.on_button_microphone(session.cfg.microphone)

    def _cancel(self, event: ev.Cancel) -> None:
        """Esc, or the cross on the overlay: drop what is in progress - the recording if
        there is one, else everything that waits to be typed. Nothing of it is typed, kept
        or saved; the last text stays what it was, and so does the button's microphone.

        A recording started with the button, on the button's own microphone, is dropped and
        begun again: that microphone is still on and only the button can switch it off, so
        the dictation is not over - and it is the button that ends it, as always. Unless the
        microphone has been pulled out meanwhile: then the cancel is for good."""
        session = self._recording
        again = False
        if session is not None:
            self._recording = None
            self._stop_recorder()
            self.ports.load_config()  # as before any question about the button: the settings are read first
            again = session.by_button and self._button_is_there(session)
            if not again:
                self._unmute(session)
            dropped = [session]
        else:
            dropped, self._finishing = self._finishing, []
        if not dropped:
            return  # a press that came too late: the text is typed already, or there was none
        for gone in dropped:
            gone.stream.close()
            log.info("dictation %d cancelled (%s): %.1f s of audio dropped",
                     gone.id, event.source, gone.samples / RATE)
        if again:
            self._start(session.cfg, True, ev.Toggle("button"), after=session)
            if self._recording is None:
                self._unmute(session)  # it did not start: the other programs get their sound back
            return
        if self._finishing:
            # A dictation stopped before this one still waits to be typed: back to it.
            self.ports.overlay.show(_(MSG_PASTING))
        else:
            self._notify(MSG_CANCELLED, CANCELLED_MESSAGE_SECONDS)
        self._refresh_state()

    # -- gateway events -----------------------------------------------------

    def _find(self, session_id: int) -> _Session | None:
        if self._recording is not None and self._recording.id == session_id:
            return self._recording
        for session in self._finishing:
            if session.id == session_id:
                return session
        return None

    def _on_text(self, session_id: int, draft: str | None = None, phrase: str | None = None) -> None:
        session = self._find(session_id)
        if session is None or session.done:
            return
        if phrase is not None:
            session.text.add_phrase(phrase)
        else:
            session.text.set_draft(draft or "")
        if session is self._recording:
            session.last_activity = self.ports.clock()
            shown = tail(session.text.display(), session.cfg.overlay_tail_chars)
            self.ports.overlay.show(shown or _(MSG_LISTENING))

    def _on_committed(self, session_id: int) -> None:
        session = self._find(session_id)
        if session is None or session is self._recording:
            return
        session.done = True
        self._drain()

    def _on_stream_failed(self, session_id: int, reason: str) -> None:
        session = self._find(session_id)
        if session is None or session.done:
            return
        log.warning("gateway stream of session %d failed: %s", session_id, reason)
        if session is self._recording:
            self._recording = None
            self._stop_recorder()
            self._unmute(session)
            self._finishing.append(session)
        self._abandon(session)
        self._drain()

    def _on_tick(self) -> None:
        now = self.ports.clock()
        session = self._recording
        if session is not None:
            if now - session.started >= session.cfg.max_recording_s:
                log.info("recording stopped: hard limit reached")
                self._stop()
            elif now - session.last_activity >= session.cfg.silence_autostop_s:
                log.info("recording stopped: no speech for %.0f s", session.cfg.silence_autostop_s)
                self._stop()
        if self._error and now - self._error_since >= ERROR_SHOWN_S:
            self._error = False  # the mark says "the last dictation went wrong", not "something is broken now"
        for session in self._finishing:
            if not session.done and now - session.stopped >= COMMIT_TIMEOUT_S:
                log.warning("session %d: no 'committed' within %.0f s", session.id, COMMIT_TIMEOUT_S)
                self._abandon(session)
        self._drain()

    # -- finishing ----------------------------------------------------------

    def _abandon(self, session: _Session) -> None:
        """The gateway will not finish this session: keep the audio, paste what is ready."""
        session.failed = True
        session.done = True
        if session.stopped is None:
            session.stopped = self.ports.clock()
        if session.chunks:
            try:
                self.ports.save_wav(np.concatenate(session.chunks))
            except Exception:
                log.exception("could not save the failed recording")

    def _drain(self) -> None:
        """Paste finished sessions strictly in session order."""
        while self._finishing and self._finishing[0].done:
            self._finish(self._finishing.pop(0))
        self._refresh_state()

    def _finish(self, session: _Session) -> None:
        # Paste first: nothing the stream does on close may delay the paste.
        text = session.text.final()
        message = MSG_LINK_LOST if session.failed else None
        # The log says how the dictation went, never what was said.
        if text:
            self.last_text = text
            # The space keeps the next dictation from sticking to this one ("Привет.Как дела?").
            typed = text + " " if session.cfg.append_space else text
            if self.ports.paster.paste(typed, session.cfg.insert_mode):
                log.info("dictation %d: pasted %d characters", session.id, len(text))
                self._mark_error(session.failed)
            else:
                log.warning("dictation %d: %d characters could not be pasted", session.id, len(text))
                message = MSG_PASTE_FAILED
                self._mark_error(True)
        elif session.failed:
            self._mark_error(True)
        else:
            log.info("dictation %d: nothing recognised", session.id)
            message = MSG_NOTHING
        if message is not None:
            self._notify(message, MESSAGE_SECONDS)
        elif self._recording is None:
            self.ports.overlay.hide()
        session.stream.close()

    def _mark_error(self, error: bool) -> None:
        self._error = error
        if error:
            self._error_since = self.ports.clock()

    def _insert_last(self) -> None:
        """The last recognised text once more, into the window in front - the way a dictation goes in."""
        if not self.last_text:
            self._notify(MSG_NO_LAST_TEXT, MUTED_MESSAGE_SECONDS)
            return
        cfg = self.ports.load_config()
        typed = self.last_text + " " if cfg.append_space else self.last_text
        if self.ports.paster.paste(typed, cfg.insert_mode):
            log.info("last text inserted again: %d characters", len(self.last_text))
        else:
            log.warning("last text could not be inserted again")
            self._notify(MSG_PASTE_FAILED, MESSAGE_SECONDS)

    def _fail(self, message: str) -> None:
        self._mark_error(True)
        self._notify(message, MESSAGE_SECONDS)
        self._refresh_state()

    def _notify(self, message: str, seconds: float) -> None:
        """A notice on the overlay. It must not take the overlay away from a dictation in
        progress - an open microphone with nothing on the screen to say so: when the notice
        has been read, the overlay goes back to that dictation."""
        self.ports.overlay.message(_(message), seconds, self._in_progress())

    def _in_progress(self) -> str | None:
        """What the overlay shows of the dictations in progress; None when there is none."""
        session = self._recording
        if session is not None:
            return tail(session.text.display(), session.cfg.overlay_tail_chars) or _(MSG_LISTENING)
        return _(MSG_PASTING) if self._finishing else None

    def _refresh_state(self) -> None:
        if self._recording is not None:
            state = RECORDING
        elif self._finishing:
            state = FINISHING
        elif self._error:
            state = ERROR
        else:
            state = IDLE
        if state != self._state:
            self._state = state
            self.ports.on_state(state)

    def _quit(self) -> None:
        session = self._recording
        if session is not None:
            self._recording = None
            self._stop_recorder()
            self._unmute(session)
            session.stream.close()
        for session in self._finishing:
            session.stream.close()
        self._finishing.clear()
        self.ports.overlay.hide()
