import base64
import json

import numpy as np
import pytest

from tolmach.gateway import protocol
from tolmach.gateway.protocol import Append, Commit, ProtocolError, SessionUpdate


def b64(pcm: np.ndarray) -> str:
    return base64.b64encode(pcm.astype("<i2").tobytes()).decode("ascii")


def test_session_update_with_our_rate():
    event = protocol.parse(json.dumps({"type": "session.update", "session": {
        "input_audio_format": "pcm16", "input_audio_sample_rate": 16000,
        "turn_detection": {"type": "server_vad"}}}))
    assert isinstance(event, SessionUpdate)
    assert event.sample_rate == 16000
    assert event.session["turn_detection"] == {"type": "server_vad"}


def test_session_update_without_a_rate_means_openai_default():
    event = protocol.parse(json.dumps({"type": "session.update", "session": {}}))
    assert event.sample_rate == protocol.DEFAULT_RATE == 24000


@pytest.mark.parametrize("session", [None, "x", {"input_audio_format": "g711_ulaw"},
                                     {"input_audio_sample_rate": "16000"}, {"input_audio_sample_rate": True}])
def test_bad_session_update(session):
    with pytest.raises(ProtocolError) as e:
        protocol.parse(json.dumps({"type": "session.update", "session": session}))
    assert e.value.code == "invalid_event"


def test_append_decodes_pcm16_to_float32():
    event = protocol.parse(json.dumps({"type": "input_audio_buffer.append",
                                       "audio": b64(np.array([0, 16384, -32768]))}))
    assert isinstance(event, Append)
    assert event.samples.dtype == np.float32
    np.testing.assert_allclose(event.samples, [0.0, 0.5, -1.0])


def test_append_of_nothing_is_empty_audio():
    event = protocol.parse(json.dumps({"type": "input_audio_buffer.append", "audio": ""}))
    assert len(event.samples) == 0


@pytest.mark.parametrize("audio", [None, 5, "***not base64***", base64.b64encode(b"abc").decode()])
def test_bad_append(audio):
    with pytest.raises(ProtocolError) as e:
        protocol.parse(json.dumps({"type": "input_audio_buffer.append", "audio": audio}))
    assert e.value.code == "invalid_event"


def test_commit():
    assert isinstance(protocol.parse('{"type": "input_audio_buffer.commit"}'), Commit)


@pytest.mark.parametrize("text", ["", "not json", "[]", "42", '{"no": "type"}', '{"type": 7}'])
def test_things_that_are_not_events(text):
    with pytest.raises(ProtocolError) as e:
        protocol.parse(text)
    assert e.value.code == "invalid_event"


def test_unknown_event_has_its_own_code():
    with pytest.raises(ProtocolError) as e:
        protocol.parse('{"type": "response.create"}')
    assert e.value.code == "unknown_event"
    assert "response.create" in e.value.message


def test_builders():
    assert protocol.session_created("ws-1") == {"type": "session.created", "session": {"id": "ws-1"}}
    assert protocol.session_updated({"a": 1}) == {"type": "session.updated", "session": {"a": 1}}
    assert protocol.delta(3, "привет") == {
        "type": "conversation.item.input_audio_transcription.delta",
        "item_id": "item_3", "delta": "", "transcript": "привет"}
    assert protocol.completed(3, "привет.") == {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "item_3", "transcript": "привет."}
    assert protocol.committed() == {"type": "input_audio_buffer.committed"}
    assert protocol.error("invalid_event", "why") == {
        "type": "error", "error": {"code": "invalid_event", "message": "why"}}
