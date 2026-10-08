import asyncio
import contextlib
import json
import logging
import queue
import socket

import aiohttp
import numpy as np
import pytest

from tolmach import VERSION, config, paths, terms
from tolmach.gateway import __main__ as gateway_main
from tolmach.gateway.__main__ import corrected, let_go_of_idle, load_engine, main, make_engine, serve
from tolmach.gateway.app import build_app
from tolmach.gateway.state import Engine
from tolmach.gateway.worker import Job
from tests.gateway.conftest import AUTH, UPDATE


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def real_config(models) -> config.GatewayConfig:
    cfg = config.GatewayConfig()
    for role in ("encoder", "decoder", "joiner", "tokens"):
        setattr(cfg.model, role, str(models / getattr(cfg.model, role)))
    cfg.vad.model = str(models / "silero_vad.onnx")
    return cfg


def test_load_engine_records_why_the_model_did_not_load():
    engine = Engine(model_name="gigaam-v3")
    load_engine(engine, config.GatewayConfig())            # the temp home has no models
    assert engine.status == "error"
    assert "model files not found" in engine.error
    assert engine.worker is None


async def test_busy_port_leaves_gateway_file_alone():
    first = '{"pid": 1, "port": 1, "version": "the first gateway"}'
    paths.gateway_file().write_text(first, encoding="utf-8")
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        cfg = config.GatewayConfig()
        cfg.port = occupied.getsockname()[1]
        engine = Engine(model_name="x")
        with pytest.raises(OSError):
            await serve(build_app(engine, "secret"), engine, cfg)
    assert paths.gateway_file().read_text(encoding="utf-8") == first


async def test_serve_publishes_itself_and_cleans_up():
    cfg = config.GatewayConfig()
    cfg.port = free_port()
    engine = Engine(model_name="gigaam-v3")
    task = asyncio.create_task(serve(build_app(engine, "secret"), engine, cfg))
    base = f"http://127.0.0.1:{cfg.port}"
    async with aiohttp.ClientSession() as http:
        for _ in range(100):                               # the port opens before the model is tried
            if paths.gateway_file().exists():
                break
            await asyncio.sleep(0.05)
        record = json.loads(paths.gateway_file().read_text(encoding="utf-8"))
        assert record["port"] == cfg.port and record["version"] == VERSION

        for _ in range(100):                               # no models in the temp home: ends in "error"
            health = await (await http.get(f"{base}/health")).json()
            if health["status"] != "loading":
                break
            await asyncio.sleep(0.05)
        assert health["status"] == "error"
        assert health["pid"] == record["pid"]

        assert (await http.post(f"{base}/shutdown", headers=AUTH)).status == 200
    await asyncio.wait_for(task, 10)
    assert not paths.gateway_file().exists()


@pytest.mark.model
async def test_real_gateway_end_to_end(real_models, benchmark_wav, aiohttp_client):
    cfg = real_config(real_models)
    engine = Engine(model_name=cfg.model.name, draft_interval_s=cfg.draft_interval_s)
    await asyncio.get_running_loop().run_in_executor(None, load_engine, engine, cfg)
    assert engine.status == "ok", engine.error
    try:
        client = await aiohttp_client(build_app(engine, "secret"))
        audio = benchmark_wav.read_bytes()

        mp = aiohttp.MultipartWriter("form-data")
        part = mp.append(audio, {"Content-Type": "audio/wav"})
        part.set_content_disposition("form-data", name="file", filename="benchmark.wav")
        r = await client.post("/v1/audio/transcriptions", data=mp, headers=AUTH)
        assert r.status == 200
        assert "лукоморья" in (await r.json())["text"].lower()

        import base64
        import wave
        with wave.open(str(benchmark_wav)) as w:
            pcm = w.readframes(w.getnframes())
        ws = await client.ws_connect("/v1/realtime", headers=AUTH)
        await ws.receive_json()
        await ws.send_json(UPDATE)
        await ws.receive_json()
        for start in range(0, len(pcm), 3200):             # 100 ms of PCM16 per message
            await ws.send_json({"type": "input_audio_buffer.append",
                                "audio": base64.b64encode(pcm[start:start + 3200]).decode("ascii")})
        await ws.send_json({"type": "input_audio_buffer.commit"})
        events = []
        while not events or events[-1]["type"] != "input_audio_buffer.committed":
            events.append(await asyncio.wait_for(ws.receive_json(), 60))
        await ws.close()
        phrases = [e["transcript"] for e in events if e["type"].endswith(".completed")]
        assert len(phrases) == 2
        assert "лукоморья" in " ".join(phrases).lower()
        assert [e["item_id"] for e in events if e["type"].endswith(".completed")] == ["item_1", "item_2"]
    finally:
        engine.worker.stop()


@pytest.mark.model
async def test_the_real_gateway_reads_an_english_file_with_the_english_model_and_lets_it_go(
        real_models, english_model, english_wav, benchmark_wav, aiohttp_client):
    cfg = real_config(real_models)
    cfg.extra_models, cfg.extra_idle_minutes = [english_model], 0.0
    engine = make_engine(cfg)
    await asyncio.get_running_loop().run_in_executor(None, load_engine, engine, cfg)
    assert engine.status == "ok", engine.error
    try:
        client = await aiohttp_client(build_app(engine, "secret"))

        def file(path, **fields):
            mp = aiohttp.MultipartWriter("form-data")
            part = mp.append(path.read_bytes(), {"Content-Type": "audio/wav"})
            part.set_content_disposition("form-data", name="file", filename=path.name)
            for name, value in fields.items():
                mp.append(value).set_content_disposition("form-data", name=name)
            return mp

        listed = (await (await client.get("/v1/models", headers=AUTH)).json())["data"]
        assert [(m["id"], m["installed"], m["loaded"]) for m in listed] == [
            ("gigaam-v3", True, True), ("parakeet-unified-en", True, False)]         # there, and not in memory yet
        r = await client.post("/v1/audio/transcriptions", data=file(english_wav, model="parakeet-unified-en"), headers=AUTH)
        assert r.status == 200 and "old portrait" in (await r.json())["text"].lower()
        assert engine.extras.loaded() == "parakeet-unified-en"
        r = await client.post("/v1/audio/transcriptions", data=file(benchmark_wav), headers=AUTH)
        assert "лукоморья" in (await r.json())["text"].lower()                # the main model is where it was
        assert engine.extras.sweep() == "parakeet-unified-en" and engine.extras.loaded() is None
    finally:
        engine.worker.stop()


def test_a_crash_before_serving_is_written_to_the_log(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("config exploded")

    monkeypatch.setattr(config, "load", boom)
    assert main() == 1
    for h in logging.getLogger("tolmach").handlers:
        h.flush()
    text = (paths.logs_dir() / "gateway.log").read_text(encoding="utf-8")
    assert "gateway crashed" in text
    assert "config exploded" in text


# --- the terms dictionary

SILENCE = np.zeros(1600, dtype=np.float32)


def test_recognised_text_goes_through_the_terms_dictionary():
    paths.terms_file().write_text("GitHub\n", encoding="utf-8")
    recognize = corrected(lambda samples: "выложил на Githab", terms.Dictionary(paths.terms_file()))
    assert recognize(SILENCE) == "выложил на GitHub"


def test_without_a_dictionary_the_text_is_what_was_recognised():
    recognize = corrected(lambda samples: "выложил на Githab", terms.Dictionary(paths.terms_file()))
    assert recognize(SILENCE) == "выложил на Githab"


def test_the_loaded_engine_writes_terms_their_own_way(monkeypatch):
    class Heard:
        def recognize(self, samples):
            return "выложил на Githab"

    monkeypatch.setattr(gateway_main.Recognizer, "load", classmethod(lambda cls, cfg: Heard()))
    monkeypatch.setattr(gateway_main, "SileroVad", lambda cfg: object())
    paths.terms_file().write_text("GitHub\n", encoding="utf-8")
    engine = Engine(model_name="gigaam-v3")
    load_engine(engine, config.GatewayConfig())
    assert engine.status == "ok"
    got: queue.Queue = queue.Queue()
    try:
        engine.worker.submit(Job("session", 1, True, SILENCE, lambda job, text: got.put(text)))
        assert got.get(timeout=5) == "выложил на GitHub"
    finally:
        engine.worker.stop()


# --- the models beside the main one


def test_the_engine_knows_the_models_beside_the_main_one_and_loads_none_of_them():
    engine = make_engine(config.GatewayConfig())
    assert (engine.model_name, engine.language, engine.status) == ("gigaam-v3", "ru", "loading")
    assert engine.extras.told() == [{"id": "parakeet-unified-en", "language": "en", "installed": False, "loaded": False}]


def test_the_text_of_another_model_does_not_go_through_the_terms_dictionary(monkeypatch, caplog):
    class Heard:
        def recognize(self, samples):
            return "pushed it to Githab"

    asked = []
    monkeypatch.setattr(gateway_main.Recognizer, "of",
                        classmethod(lambda cls, model, threads: asked.append((model.name, threads)) or Heard()))
    monkeypatch.setattr(gateway_main, "installed", lambda model: True)
    paths.terms_file().write_text("GitHub\n", encoding="utf-8")         # written for the main model's language
    cfg = config.GatewayConfig()
    cfg.threads = 3
    engine = make_engine(cfg)
    with caplog.at_level(logging.INFO, logger="tolmach.gateway"):
        recognize = engine.extras.acquire("parakeet-unified-en")
    assert recognize(SILENCE) == "pushed it to Githab" and asked == [("parakeet-unified-en", 3)]
    assert "model parakeet-unified-en loaded, 3 threads" in caplog.text


def test_a_model_is_kept_for_as_many_minutes_as_the_settings_say(monkeypatch):
    monkeypatch.setattr(gateway_main.Recognizer, "of", classmethod(lambda cls, model, threads: type("R", (), {"recognize": len})()))
    monkeypatch.setattr(gateway_main, "installed", lambda model: True)
    now = [1000.0]
    cfg = config.GatewayConfig()                                         # ten minutes by default
    engine = make_engine(cfg, clock=lambda: now[0])
    engine.extras.acquire("parakeet-unified-en")
    engine.extras.release("parakeet-unified-en")
    now[0] += 599
    assert engine.extras.sweep() is None
    now[0] += 1
    assert engine.extras.sweep() == "parakeet-unified-en"
    cfg.extra_idle_minutes = 0.5
    engine = make_engine(cfg, clock=lambda: now[0])
    engine.extras.acquire("parakeet-unified-en")
    engine.extras.release("parakeet-unified-en")
    now[0] += 29
    assert engine.extras.sweep() is None
    now[0] += 1
    assert engine.extras.sweep() == "parakeet-unified-en"


async def test_a_model_nobody_asks_for_is_let_go_of_while_the_gateway_runs(caplog):
    class Idle:
        def __init__(self):
            self.looks = 0

        def sweep(self):
            self.looks += 1
            return "parakeet-unified-en" if self.looks == 2 else None

    idle = Idle()
    with caplog.at_level(logging.INFO, logger="tolmach.gateway"):
        task = asyncio.create_task(let_go_of_idle(idle, every_s=0.01))
        for _ in range(300):
            if idle.looks >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert idle.looks >= 3 and caplog.text.count("model parakeet-unified-en is let go of") == 1


async def test_a_look_for_idle_models_that_failed_is_not_the_last_one(caplog):
    class Idle:
        def __init__(self):
            self.looks = 0

        def sweep(self):
            self.looks += 1
            if self.looks == 1:
                raise RuntimeError("the first look went wrong")
            return None

    idle = Idle()
    with caplog.at_level(logging.ERROR, logger="tolmach.gateway"):
        task = asyncio.create_task(let_go_of_idle(idle, every_s=0.01))
        for _ in range(300):
            if idle.looks >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert idle.looks >= 3 and caplog.text.count("a look for idle models failed") == 1


async def test_the_gateway_that_serves_looks_for_idle_models_and_stops_cleanly(monkeypatch):
    class Idle:
        looks = 0

        def sweep(self):
            Idle.looks += 1
            if Idle.looks == 1:
                raise RuntimeError("the first look went wrong")
            return None

    monkeypatch.setattr(gateway_main, "IDLE_EVERY_S", 0.01)
    cfg = config.GatewayConfig()
    cfg.port = free_port()
    engine = Engine(model_name="gigaam-v3", extras=Idle())
    task = asyncio.create_task(serve(build_app(engine, "secret"), engine, cfg))
    async with aiohttp.ClientSession() as http:
        for _ in range(300):
            if Idle.looks >= 3 and paths.gateway_file().exists():
                break
            await asyncio.sleep(0.01)
        assert Idle.looks >= 3                                            # it looks while it serves, a failed look or not
        assert (await http.post(f"http://127.0.0.1:{cfg.port}/shutdown", headers=AUTH)).status == 200
    stopped, _ = await asyncio.wait({task}, timeout=5)
    assert task in stopped                                               # by itself: the look did not hold it, and nobody cancelled it
    looked = Idle.looks
    await asyncio.sleep(0.05)
    assert not paths.gateway_file().exists() and Idle.looks == looked     # stopped: it looks no more


def test_the_gateway_is_started_with_the_models_of_its_settings(monkeypatch):
    started = []

    async def served(app, engine, cfg):
        started.append(engine)

    monkeypatch.setattr(gateway_main, "serve", served)
    assert gateway_main._run() == 0
    engine = started[0]
    assert engine.model_name == "gigaam-v3" and engine.extras.known("parakeet-unified-en")


def test_a_main_model_named_as_one_of_the_list_is_the_main_model_alone():
    """The list a file gives is checked against the name of the main model; the list of the program itself
    is not - a main model may be given the very name the English one has there."""
    cfg = config.GatewayConfig()
    cfg.model.name = "parakeet-unified-en"
    engine = make_engine(cfg)
    assert engine.extras.told() == [] and not engine.extras.known("parakeet-unified-en")
