import asyncio

import pytest
from aiohttp import web

from tolmach.gateway.realtime import realtime
from tolmach.gateway.state import ENGINE
from tests.gateway.conftest import UPDATE, append_event
from tests.gateway.fakes import silent

COMMIT = {"type": "input_audio_buffer.commit"}


@pytest.fixture
async def client(aiohttp_client, engine):
    app = web.Application()
    app[ENGINE] = engine
    app.router.add_get("/v1/realtime", realtime)
    return await aiohttp_client(app)


@pytest.fixture
async def ws(client):
    ws = await client.ws_connect("/v1/realtime")
    yield ws
    await ws.close()


async def recv(ws):
    return await asyncio.wait_for(ws.receive_json(), 5)


async def until_committed(ws):
    events = []
    while not events or events[-1]["type"] != "input_audio_buffer.committed":
        events.append(await recv(ws))
    return events


async def ready(ws):
    assert (await recv(ws))["type"] == "session.created"
    await ws.send_json(UPDATE)
    assert (await recv(ws))["type"] == "session.updated"


async def test_session_created_comes_first(ws):
    event = await recv(ws)
    assert event["type"] == "session.created"
    assert event["session"]["id"].startswith("ws-")


async def test_audio_before_session_update_is_refused(ws):
    await recv(ws)
    await ws.send_json(append_event(0.1))
    event = await recv(ws)
    assert event["type"] == "error"
    assert event["error"]["code"] == "unsupported_sample_rate"
    assert "16000" in event["error"]["message"]


async def test_wrong_rate_is_refused_then_the_right_one_works(ws):
    await recv(ws)
    await ws.send_json({"type": "session.update", "session": {"input_audio_sample_rate": 24000}})
    assert (await recv(ws))["error"]["code"] == "unsupported_sample_rate"
    await ws.send_json({"type": "session.update", "session": {}})      # silent client = 24 kHz
    assert (await recv(ws))["error"]["code"] == "unsupported_sample_rate"
    await ws.send_json(UPDATE)
    event = await recv(ws)
    assert event == {"type": "session.updated", "session": UPDATE["session"]}


async def test_draft_then_phrase_then_committed(ws):
    await ready(ws)
    for _ in range(12):                                   # 1.2 s: one draft is due
        await ws.send_json(append_event(0.1))
    draft = await recv(ws)
    assert draft["type"] == "conversation.item.input_audio_transcription.delta"
    assert draft["item_id"] == "item_1"
    assert draft["delta"] == ""
    assert draft["transcript"].startswith("len")
    for _ in range(8):
        await ws.send_json(append_event(0.1))
    await ws.send_json(COMMIT)
    events = await until_committed(ws)
    done = events[-2]
    assert done["type"] == "conversation.item.input_audio_transcription.completed"
    assert done["item_id"] == "item_1"
    assert done["transcript"] == "len32000"               # all 2 s, the unfed tail included


async def test_silence_commits_without_phrases(ws, recognize):
    recognize.fn = silent
    await ready(ws)
    for _ in range(15):
        await ws.send_json(append_event(0.1))
    await ws.send_json(COMMIT)
    assert await until_committed(ws) == [{"type": "input_audio_buffer.committed"}]


async def test_commit_with_no_audio(ws):
    await ready(ws)
    await ws.send_json(COMMIT)
    assert await recv(ws) == {"type": "input_audio_buffer.committed"}


async def test_commit_before_session_update(ws):
    await recv(ws)
    await ws.send_json(COMMIT)
    assert await recv(ws) == {"type": "input_audio_buffer.committed"}


async def test_garbage_does_not_break_the_session(ws):
    await recv(ws)
    await ws.send_str("not json")
    assert (await recv(ws))["error"]["code"] == "invalid_event"
    await ws.send_json({"type": "response.create"})
    assert (await recv(ws))["error"]["code"] == "unknown_event"
    await ws.send_bytes(b"\x00\x01")
    assert (await recv(ws))["error"]["code"] == "invalid_event"
    await ws.send_json(UPDATE)
    assert (await recv(ws))["type"] == "session.updated"


async def test_big_first_chunk(ws):
    await ready(ws)
    await ws.send_json(append_event(5.03))                # audio buffered before the connection was up
    await ws.send_json(COMMIT)
    events = await until_committed(ws)
    done = events[-2]
    assert done["type"].endswith(".completed")
    assert done["transcript"] == "len80480"               # 5.03 s, not a sample lost
    assert all(e["type"].endswith(".delta") for e in events[:-2])


async def test_disconnect_forgets_session(client, engine):
    ws = await client.ws_connect("/v1/realtime")
    session_id = (await recv(ws))["session"]["id"]
    await ws.send_json(UPDATE)
    await recv(ws)
    await ws.send_json(append_event(0.5))
    await ws.close()
    for _ in range(100):                                  # the server side notices on its own schedule
        if session_id in engine.worker.forgotten:
            break
        await asyncio.sleep(0.05)
    assert session_id in engine.worker.forgotten

    again = await client.ws_connect("/v1/realtime")       # the gateway keeps serving
    assert (await recv(again))["type"] == "session.created"
    await again.close()


async def test_two_sessions_do_not_mix(client):
    a = await client.ws_connect("/v1/realtime")
    b = await client.ws_connect("/v1/realtime")
    await ready(a)
    await ready(b)
    await a.send_json(append_event(1.0))
    await b.send_json(append_event(2.0))
    await b.send_json(COMMIT)
    await a.send_json(COMMIT)
    done_a = (await until_committed(a))[-2]
    done_b = (await until_committed(b))[-2]
    assert done_a["transcript"] == "len16000"
    assert done_b["transcript"] == "len32000"
    assert done_a["item_id"] == done_b["item_id"] == "item_1"
    await a.close()
    await b.close()


async def test_cyrillic_travels_as_is(ws, recognize):
    recognize.fn = lambda samples: "Привет, мир."
    await ready(ws)
    await ws.send_json(append_event(0.5))
    await ws.send_json(COMMIT)
    frame = await asyncio.wait_for(ws.receive(), 5)
    assert "Привет, мир." in frame.data                   # not \u-escaped
    assert (await recv(ws))["type"] == "input_audio_buffer.committed"
