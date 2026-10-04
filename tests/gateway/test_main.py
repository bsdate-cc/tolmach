import asyncio
import json
import logging
import socket

import aiohttp
import pytest

from tolmach import VERSION, config, paths
from tolmach.gateway.__main__ import load_engine, main, serve
from tolmach.gateway.app import build_app
from tolmach.gateway.state import Engine
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
