import base64
import json
import queue
import threading

import numpy as np
import websocket

from tolmach.client import events as ev
from tolmach.client import stream as s

from .fakes import tone


class FakeWs:
    def __init__(self, incoming=()):
        self.incoming = queue.Queue()
        for item in incoming:
            self.incoming.put(item)
        self.sent = []
        self.fail_send = False

    def recv(self):
        item = self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    def send(self, text):
        if self.fail_send:
            raise OSError("broken pipe")
        self.sent.append(text)

    def close(self):
        self.incoming.put(OSError("closed"))

    def abort(self):
        self.incoming.put(OSError("aborted"))

    def shutdown(self):
        pass


class StalledWs:
    """A gateway that hangs with its socket open: recv blocks, and close(),
    like websocket-client's handshake, waits for the lock recv holds."""

    def __init__(self):
        self.lock = threading.Lock()
        self.woken = threading.Event()
        self.in_recv = threading.Event()

    def recv(self):
        with self.lock:
            self.in_recv.set()
            self.woken.wait()
            raise OSError("aborted")

    def send(self, text):
        pass

    def close(self):
        with self.lock:
            pass

    def abort(self):
        self.woken.set()

    def shutdown(self):
        pass


def test_session_update_declares_16k_pcm16():
    message = json.loads(s.session_update())
    assert message["type"] == "session.update"
    assert message["session"]["input_audio_format"] == "pcm16"
    assert message["session"]["input_audio_sample_rate"] == 16000
    assert message["session"]["turn_detection"] == {"type": "server_vad"}


def test_append_message_carries_little_endian_pcm16():
    samples = np.array([0, 1, -1, 32767, -32768], dtype=np.int16)
    message = json.loads(s.append_message(samples))
    assert message["type"] == "input_audio_buffer.append"
    assert base64.b64decode(message["audio"]) == samples.astype("<i2").tobytes()


def test_commit_message():
    assert json.loads(s.commit_message()) == {"type": "input_audio_buffer.commit"}


def test_parse_draft_phrase_and_committed():
    draft = json.dumps({"type": s.DELTA, "item_id": "item_1", "delta": "", "transcript": "прив"})
    phrase = json.dumps({"type": s.COMPLETED, "item_id": "item_1", "transcript": "Привет."})
    assert s.parse_server_event(3, draft) == ev.Draft(3, "прив")
    assert s.parse_server_event(3, phrase) == ev.Phrase(3, "Привет.")
    assert s.parse_server_event(3, json.dumps({"type": s.COMMITTED})) == ev.Committed(3)


def test_parse_fatal_error_becomes_stream_failed():
    raw = json.dumps({"type": "error", "error": {"code": "unsupported_sample_rate", "message": "only 16000"}})
    event = s.parse_server_event(3, raw)
    assert isinstance(event, ev.StreamFailed)
    assert event.session == 3 and "unsupported_sample_rate" in event.reason


def test_parse_skips_non_fatal_unknown_and_garbage():
    assert s.parse_server_event(3, json.dumps({"type": "error", "error": {"code": "unknown_event"}})) is None
    assert s.parse_server_event(3, json.dumps({"type": "session.created", "session": {"id": "x"}})) is None
    assert s.parse_server_event(3, "not json") is None
    assert s.parse_server_event(3, b"\x03\xe8") is None
    assert s.parse_server_event(3, "[1, 2]") is None


def test_stream_posts_parsed_events():
    posted = queue.Queue()
    ws = FakeWs([json.dumps({"type": s.DELTA, "transcript": "раз"}), json.dumps({"type": s.COMMITTED})])
    s.GatewayStream(5, ws, posted.put)
    assert posted.get(timeout=2) == ev.Draft(5, "раз")
    assert posted.get(timeout=2) == ev.Committed(5)


def test_send_and_commit_write_protocol_messages():
    ws = FakeWs()
    stream = s.GatewayStream(5, ws, lambda event: None)
    stream.send(tone())
    stream.commit()
    assert [json.loads(m)["type"] for m in ws.sent] == ["input_audio_buffer.append", "input_audio_buffer.commit"]
    stream.close()


def test_lost_connection_is_reported_once_and_later_sends_are_dropped():
    posted = queue.Queue()
    ws = FakeWs([OSError("reset")])
    stream = s.GatewayStream(5, ws, posted.put)
    event = posted.get(timeout=2)
    assert isinstance(event, ev.StreamFailed) and event.session == 5
    stream.send(tone())
    stream.commit()
    assert ws.sent == [] and posted.empty()


def test_send_failure_is_reported_not_raised():
    posted = queue.Queue()
    ws = FakeWs()
    ws.fail_send = True
    stream = s.GatewayStream(5, ws, posted.put)
    stream.send(tone())
    assert isinstance(posted.get(timeout=2), ev.StreamFailed)
    stream.close()


def test_close_is_not_a_failure():
    posted = queue.Queue()
    stream = s.GatewayStream(5, FakeWs(), posted.put)
    stream.close()
    stream._thread.join(timeout=2)
    assert not stream._thread.is_alive()
    assert posted.empty()


def test_send_timeout_fails_the_stream_once_and_does_not_raise():
    posted = queue.Queue()
    ws = FakeWs()
    ws.send = lambda text: (_ for _ in ()).throw(websocket.WebSocketTimeoutException("timed out"))
    stream = s.GatewayStream(5, ws, posted.put)
    stream.send(tone())
    stream.send(tone())
    stream.commit()
    assert isinstance(posted.get(timeout=2), ev.StreamFailed)
    assert posted.empty()
    stream.close()


def test_receive_timeout_does_not_end_the_reader():
    posted = queue.Queue()
    ws = FakeWs([websocket.WebSocketTimeoutException("timed out"), json.dumps({"type": s.COMMITTED})])
    stream = s.GatewayStream(5, ws, posted.put)
    assert posted.get(timeout=2) == ev.Committed(5)
    assert stream._thread.is_alive()
    stream.close()


def test_close_returns_while_the_reader_is_blocked_in_recv():
    ws = StalledWs()
    stream = s.GatewayStream(5, ws, lambda event: None)
    assert ws.in_recv.wait(timeout=2)
    closer = threading.Thread(target=stream.close, daemon=True)
    closer.start()
    closer.join(timeout=2)
    blocked = closer.is_alive()
    ws.woken.set()  # release the threads either way
    assert not blocked, "close() blocked on the reader"
    stream._thread.join(timeout=2)
    assert not stream._thread.is_alive()


def test_factory_gives_the_socket_a_send_timeout(monkeypatch):
    from tolmach import keyfile

    keyfile.ensure_key()
    timeouts = []

    class Connected(FakeWs):
        def settimeout(self, value):
            timeouts.append(value)

    ws = Connected()
    monkeypatch.setattr(s.websocket, "create_connection", lambda url, **kw: ws)
    stream = s.StreamFactory(lambda event: None).open(1)
    assert timeouts == [s.IO_TIMEOUT_S] and s.IO_TIMEOUT_S == 5.0
    stream.close()


def test_factory_without_a_key_raises_stream_error():
    factory = s.StreamFactory(lambda event: None)
    try:
        factory.open(1)
    except ev.StreamError as e:
        assert "key" in str(e)
    else:
        raise AssertionError("StreamError expected")
