import asyncio
import contextlib
import gc
import logging
import os
import threading

import aiohttp
import pytest

from tolmach import VERSION
from tolmach.gateway.app import acquired, build_app
from tolmach.gateway.state import STOP
from tests.gateway.conftest import AUTH, UPDATE, append_event, wav_bytes
from tests.gateway.fakes import ScriptedVad, silent, tone

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


# --- the phrases with their times


def two_phrases() -> ScriptedVad:
    """Windows 1..30 are one phrase; after a pause the speech goes on to the end of the file."""
    return ScriptedVad(speech=list(range(1, 20)) + list(range(60, 90)), close_after={30: tone(100)})


async def test_transcription_with_the_phrases_and_their_times(client, engine):
    engine.make_vad = two_phrases
    r = await client.post(URL, data=form(wav_bytes(3.0), response_format="verbose_json"), headers=AUTH)
    assert r.status == 200
    # the second phrase begins with its pre-roll (16 windows before window 60) and takes the tail of the file
    assert await r.json() == {
        "text": "len15360 len25984",
        "duration": 3.0,
        "segments": [{"id": 0, "start": 0.0, "end": 0.96, "text": "len15360"},
                     {"id": 1, "start": 1.376, "end": 3.0, "text": "len25984"}],
    }


async def test_a_phrase_without_words_is_not_among_the_segments(client, engine, recognize):
    engine.make_vad = two_phrases
    recognize.fn = lambda samples: "" if len(samples) == 15360 else "слова"
    r = await client.post(URL, data=form(wav_bytes(3.0), response_format="verbose_json"), headers=AUTH)
    body = await r.json()
    assert body["text"] == "слова"
    assert body["segments"] == [{"id": 0, "start": 1.376, "end": 3.0, "text": "слова"}]


async def test_a_silent_file_has_its_length_and_no_segments(client, recognize):
    recognize.fn = silent
    r = await client.post(URL, data=form(wav_bytes(2.0), response_format="verbose_json"), headers=AUTH)
    assert await r.json() == {"text": "", "duration": 2.0, "segments": []}


async def test_times_are_rounded_to_a_millisecond(client):
    r = await client.post(URL, data=form(wav_bytes(1.00059), response_format="verbose_json"), headers=AUTH)
    body = await r.json()
    assert body["duration"] == 1.001                       # 16009 samples
    assert body["segments"] == [{"id": 0, "start": 0.0, "end": 1.001, "text": "len16009"}]


async def test_cyrillic_in_the_phrases_is_not_escaped(client, recognize):
    recognize.fn = lambda samples: "Привет, мир."
    r = await client.post(URL, data=form(wav_bytes(1.0), response_format="verbose_json"), headers=AUTH)
    assert (await r.text()).count("Привет, мир.") == 2     # in "text" and in the one segment


# --- the models beside the main one

MODELS = "/v1/models"


async def test_a_file_may_ask_for_another_model(client, shelf):
    r = await client.post(URL, data=form(wav_bytes(2.0), model="english"), headers=AUTH)
    assert r.status == 200 and await r.json() == {"text": "english:32000"}
    assert shelf.loads == ["english"] and shelf.extras.loaded() == "english"
    r = await client.post(URL, data=form(wav_bytes(1.0), model="english", response_format="verbose_json"), headers=AUTH)
    assert (await r.json())["segments"][0]["text"] == "english:16000" and shelf.loads == ["english"]     # loaded once
    shelf.now += 600
    assert shelf.extras.sweep() == "english"                # the answers are given: nothing holds it in memory


async def test_any_other_name_means_the_main_model_as_it_always_did(client, shelf):
    for name in ("fake", "whisper-1", ""):                  # programs made for other services name models of theirs
        r = await client.post(URL, data=form(wav_bytes(2.0), model=name), headers=AUTH)
        assert await r.json() == {"text": "len32000"}, name
    assert shelf.loads == []


async def test_a_model_that_cannot_be_had_is_an_answer_that_says_why(client, shelf, caplog):
    shelf.missing = {"english"}
    r = await client.post(URL, data=form(wav_bytes(1.0), model="english"), headers=AUTH)
    assert r.status == 409 and (await r.json())["error"]["code"] == "model_not_installed" and shelf.loads == []
    shelf.missing, shelf.broken = set(), RuntimeError("model failed to load: bad file")
    with caplog.at_level(logging.WARNING, logger="tolmach.gateway"):
        r = await client.post(URL, data=form(wav_bytes(1.0), model="english"), headers=AUTH)
    error = (await r.json())["error"]
    assert r.status == 500 and error["code"] == "model_failed" and "bad file" in error["message"]
    assert "model english could not be loaded" in caplog.text
    shelf.broken = None
    r = await client.post(URL, data=form(wav_bytes(1.0), model="english"), headers=AUTH)
    assert r.status == 200 and await r.json() == {"text": "english:16000"}     # the gateway goes on, and so does the model
    r = await client.post(URL, data=form(wav_bytes(1.0)), headers=AUTH)
    assert await r.json() == {"text": "len16000"}


async def test_a_file_that_cannot_be_read_loads_no_model(client, shelf):
    r = await client.post(URL, data=form(b"definitely not audio", model="english"), headers=AUTH)
    assert r.status == 400 and shelf.loads == []


async def test_nobody_waits_for_a_model_that_is_being_loaded_but_those_who_asked_for_it(client, shelf):
    """Loading takes seconds. A file for the main model and a dictation go on meanwhile; two files for the
    model that is being loaded are one load of it."""
    shelf.gate = threading.Event()
    first = asyncio.create_task(client.post(URL, data=form(wav_bytes(1.0), model="english"), headers=AUTH))
    second = asyncio.create_task(client.post(URL, data=form(wav_bytes(2.0), model="english"), headers=AUTH))
    for _ in range(200):
        if shelf.extras.loading() == "english":
            break
        await asyncio.sleep(0.01)
    r = await asyncio.wait_for(client.post(URL, data=form(wav_bytes(1.0)), headers=AUTH), 5)
    assert await r.json() == {"text": "len16000"}                        # the main model answers meanwhile
    ws = await client.ws_connect("/v1/realtime", headers=AUTH)
    assert (await asyncio.wait_for(ws.receive_json(), 5))["type"] == "session.created"       # ...and a dictation begins
    await ws.close()
    assert not first.done() and not second.done()
    shelf.now += 600
    assert shelf.extras.sweep() is None                                  # being loaded for somebody: not let go of
    shelf.gate.set()
    answers = [await (await asked).json() for asked in (first, second)]
    assert answers == [{"text": "english:16000"}, {"text": "english:32000"}] and shelf.loads == ["english"]
    shelf.now += 600
    assert shelf.extras.sweep() == "english"                             # both answered: nothing holds it


async def test_a_model_asked_for_by_one_who_went_away_is_not_held_for_ever(shelf):
    shelf.gate = threading.Event()
    asking = asyncio.create_task(acquired(shelf.extras, "english"))
    for _ in range(200):
        if shelf.extras.loading() == "english":
            break
        await asyncio.sleep(0.01)
    asking.cancel()                                          # the gateway is stopping while the model is being loaded
    with contextlib.suppress(asyncio.CancelledError):
        await asking
    shelf.gate.set()
    for _ in range(200):
        if shelf.extras.loaded() == "english":
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    shelf.now += 600
    assert shelf.extras.sweep() == "english"                # loaded for nobody: nobody holds it


async def test_the_models_are_told_to_whoever_has_the_key(client, engine):
    r = await client.get(MODELS, headers=AUTH)
    assert r.status == 200 and await r.json() == {"object": "list", "data": [
        {"id": "fake", "object": "model", "language": "ru", "main": True, "installed": True, "loaded": True},
        {"id": "english", "object": "model", "language": "en", "main": False, "installed": True, "loaded": False}]}
    assert (await client.get(MODELS)).status == 401
    await client.post(URL, data=form(wav_bytes(1.0), model="english"), headers=AUTH)
    assert (await (await client.get(MODELS, headers=AUTH)).json())["data"][1]["loaded"] is True
    engine.status = "loading"                               # asked while the main model is still being loaded
    data = (await (await client.get(MODELS, headers=AUTH)).json())["data"]
    assert (data[0]["installed"], data[0]["loaded"]) == (True, False)
    engine.status, engine.extras = "error", None            # the main model did not load; there are no others
    assert (await (await client.get(MODELS, headers=AUTH)).json())["data"] == [
        {"id": "fake", "object": "model", "language": "ru", "main": True, "installed": False, "loaded": False}]


async def test_a_file_that_could_not_be_recognized_does_not_leave_its_model_counted_as_used(client, engine, shelf):
    def no_vad():
        raise RuntimeError("the pauses could not be looked for")

    engine.make_vad = no_vad
    r = await client.post(URL, data=form(wav_bytes(1.0), model="english"), headers=AUTH)
    assert r.status == 500 and shelf.extras.loaded() == "english"
    shelf.now += 600
    assert shelf.extras.sweep() == "english"                # the request failed: the model is free all the same


async def test_a_model_that_was_let_go_of_is_held_by_nothing_of_the_request_that_used_it(client, shelf):
    r = await client.post(URL, data=form(wav_bytes(1.0), model="english"), headers=AUTH)
    assert await r.json() == {"text": "english:16000"}
    shelf.now += 600
    assert shelf.extras.sweep() == "english"
    for _ in range(200):                                    # the recognition thread still waits for its next job
        gc.collect()
        if shelf.made[0]() is None:
            break
        await asyncio.sleep(0.01)
    assert shelf.made[0]() is None                          # its memory is given back now, not when somebody dictates next
