import asyncio
import os
import threading

import aiohttp
import pytest

from tolmach import VERSION
from tolmach.gateway.app import build_app
from tolmach.gateway.state import STOP
from tests.gateway.conftest import AUTH, UPDATE, append_event, wav_bytes
from tests.gateway.fakes import silent

URL = "/v1/audio/transcriptions"


@pytest.fixture
async def client(aiohttp_client, engine):
    return await aiohttp_client(build_app(engine, "secret"))


def form(data: bytes | None, **fields) -> aiohttp.MultipartWriter:
    mp = aiohttp.MultipartWriter("form-data")
    if data is not None:
        part = mp.append(data, {"Content-Type": "audio/wav"})
        part.set_content_disposition("form-data", name="file", filename="a.wav")
    for name, value in fields.items():
        part = mp.append(value)
        part.set_content_disposition("form-data", name=name)
    return mp


async def test_health_needs_no_key(client):
    r = await client.get("/health")
    assert r.status == 200
    assert await r.json() == {"status": "ok", "model": "fake", "error": None,
                              "version": VERSION, "pid": os.getpid()}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "secret"}])
async def test_everything_else_needs_the_key(client, headers):
    for method, path in (("POST", URL), ("POST", "/shutdown"), ("GET", "/v1/realtime"), ("GET", "/nowhere")):
        r = await client.request(method, path, headers=headers)
        assert r.status == 401, path
        assert (await r.json())["error"]["code"] == "unauthorized"
    assert not client.app[STOP].is_set()


async def test_websocket_needs_the_key(client):
    with pytest.raises(aiohttp.WSServerHandshakeError) as e:
        await client.ws_connect("/v1/realtime")
    assert e.value.status == 401
    ws = await client.ws_connect("/v1/realtime", headers=AUTH)
    assert (await asyncio.wait_for(ws.receive_json(), 5))["type"] == "session.created"
    await ws.send_json(UPDATE)
    assert (await asyncio.wait_for(ws.receive_json(), 5))["type"] == "session.updated"
    await ws.close()


async def test_while_loading_only_health_and_shutdown_answer(client, engine):
    engine.status = "loading"
    assert (await (await client.get("/health")).json())["status"] == "loading"
    r = await client.post(URL, data=form(wav_bytes(1.0)), headers=AUTH)
    assert r.status == 503
    assert (await r.json())["error"]["code"] == "not_ready"
    with pytest.raises(aiohttp.WSServerHandshakeError) as e:
        await client.ws_connect("/v1/realtime", headers=AUTH)
    assert e.value.status == 503
    assert (await client.post("/shutdown", headers=AUTH)).status == 200


async def test_a_failed_model_says_why(client, engine):
    engine.status = "error"
    engine.error = "model files not found - encoder: C:\\nowhere\\enc.onnx"
    health = await (await client.get("/health")).json()
    assert health["status"] == "error"
    assert "enc.onnx" in health["error"]
    r = await client.post(URL, data=form(wav_bytes(1.0)), headers=AUTH)
    assert r.status == 503
    assert "enc.onnx" in (await r.json())["error"]["message"]


async def test_transcription_json(client):
    r = await client.post(URL, data=form(wav_bytes(2.0), model="gigaam-v3", language="ru"), headers=AUTH)
    assert r.status == 200
    assert await r.json() == {"text": "len32000"}          # all 2 s of the file


async def test_transcription_as_text(client):
    r = await client.post(URL, data=form(wav_bytes(2.0), response_format="text"), headers=AUTH)
    assert r.status == 200
    assert r.content_type == "text/plain"
    assert await r.text() == "len32000"


async def test_cyrillic_result(client, recognize):
    recognize.fn = lambda samples: "Привет, мир."
    r = await client.post(URL, data=form(wav_bytes(1.0)), headers=AUTH)
    assert "Привет, мир." in await r.text()                # not \u-escaped
    assert await r.json() == {"text": "Привет, мир."}


async def test_silent_file_gives_empty_text(client, recognize):
    recognize.fn = silent
    r = await client.post(URL, data=form(wav_bytes(2.0)), headers=AUTH)
    assert await r.json() == {"text": ""}


async def test_empty_wav_gives_empty_text(client):
    r = await client.post(URL, data=form(wav_bytes(0.0)), headers=AUTH)
    assert r.status == 200
    assert await r.json() == {"text": ""}


async def test_not_multipart(client):
    r = await client.post(URL, json={"file": "x"}, headers=AUTH)
    assert r.status == 400
    assert (await r.json())["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("body, needle", [
    (lambda: form(None, model="x"), "file"),
    (lambda: form(b"definitely not audio"), "WAV"),
    (lambda: form(wav_bytes(1.0, rate=48000)), "16000"),
    (lambda: form(wav_bytes(1.0), response_format="srt"), "response_format"),
])
async def test_bad_requests(client, body, needle):
    r = await client.post(URL, data=body(), headers=AUTH)
    assert r.status == 400
    error = (await r.json())["error"]
    assert error["code"] == "invalid_request"
    assert needle in error["message"]


async def test_shutdown_sets_the_stop_signal(client):
    r = await client.post("/shutdown", headers=AUTH)
    assert r.status == 200
    assert client.app[STOP].is_set()


async def test_unknown_route_with_the_key_is_404(client):
    assert (await client.get("/nowhere", headers=AUTH)).status == 404


async def test_a_file_over_one_megabyte_is_accepted(client):
    data = wav_bytes(40.0)                                  # 1.28 MB; aiohttp's own default limit is 1 MiB
    assert len(data) > 1024 ** 2
    r = await client.post(URL, data=form(data), headers=AUTH)
    assert r.status == 200
    assert await r.json() == {"text": "len480256 len159744"}    # the 30 s hard limit cuts it in two


async def test_a_file_over_the_limit_is_refused_in_our_error_form(aiohttp_client, engine):
    client = await aiohttp_client(build_app(engine, "secret", max_upload=100_000))
    r = await client.post(URL, data=form(wav_bytes(5.0)), headers=AUTH)      # 160 KB
    assert r.status == 413
    error = (await r.json())["error"]
    assert error["code"] == "too_large"
    assert "100000" in error["message"]


async def test_a_long_file_does_not_hold_up_live_dictation(client, engine, recognize):
    calls = []
    first_started = threading.Event()
    release = threading.Event()

    def slow(samples):
        calls.append(len(samples))
        if len(calls) == 1:                                 # hold the engine on the file's first phrase
            first_started.set()
            assert release.wait(10)
        return f"len{len(samples)}"

    recognize.fn = slow
    upload = asyncio.create_task(client.post(URL, data=form(wav_bytes(70.0)), headers=AUTH))
    for _ in range(200):
        if first_started.is_set():
            break
        await asyncio.sleep(0.05)
    assert first_started.is_set()

    ws = await client.ws_connect("/v1/realtime", headers=AUTH)
    await ws.receive_json()
    await ws.send_json(UPDATE)
    await ws.receive_json()
    await ws.send_json(append_event(0.5))
    await ws.send_json({"type": "input_audio_buffer.commit"})
    for _ in range(200):                                    # the dictated phrase is queued behind the engine
        if engine.worker._finals:
            break
        await asyncio.sleep(0.05)
    release.set()
    events = []
    while not events or events[-1]["type"] != "input_audio_buffer.committed":
        events.append(await asyncio.wait_for(ws.receive_json(), 10))
    await ws.close()
    assert (await upload).status == 200
    # 70 s of file = phrases of 30 s, 30 s and 10 s. The dictated 0.5 s ran right after the file
    # phrase that was already in the engine, not after the whole file.
    assert calls == [480256, 8000, 480256, 159488]
