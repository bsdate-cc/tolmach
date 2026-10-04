"""Manual check: stream a 16 kHz WAV into a running gateway and print what comes back.

    .venv\\Scripts\\python scripts\\stream_file.py tests\\data\\benchmark.wav [--fast]
"""
import argparse
import asyncio
import base64
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp  # noqa: E402

from tolmach import config, keyfile  # noqa: E402

CHUNK = 3200  # 100 ms of 16 kHz PCM16


async def run(path: Path, fast: bool) -> int:
    cfg = config.load().config.gateway
    key = keyfile.read_key()
    if key is None:
        print("no key file yet - start the gateway first")
        return 1
    with wave.open(str(path)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            print("need a 16 kHz mono 16-bit WAV")
            return 1
        pcm = w.readframes(w.getnframes())

    url = f"http://{cfg.host}:{cfg.port}/v1/realtime"
    async with aiohttp.ClientSession() as http, http.ws_connect(
            url, headers={"Authorization": f"Bearer {key}"}) as ws:

        async def reader() -> None:
            async for msg in ws:
                event = json.loads(msg.data)
                kind = event["type"]
                if kind.endswith(".delta"):
                    print(f"  draft {event['item_id']}: {event['transcript']}")
                elif kind.endswith(".completed"):
                    print(f"PHRASE {event['item_id']}: {event['transcript']}")
                elif kind == "error":
                    print(f"ERROR  {event['error']}")
                elif kind == "input_audio_buffer.committed":
                    print("committed")
                    return
                else:
                    print(kind)

        reading = asyncio.create_task(reader())
        await ws.send_json({"type": "session.update", "session": {
            "input_audio_format": "pcm16", "input_audio_sample_rate": 16000,
            "turn_detection": {"type": "server_vad"}}})
        for start in range(0, len(pcm), CHUNK):
            await ws.send_json({"type": "input_audio_buffer.append",
                                "audio": base64.b64encode(pcm[start:start + CHUNK]).decode("ascii")})
            if not fast:
                await asyncio.sleep(0.1)
        await ws.send_json({"type": "input_audio_buffer.commit"})
        await asyncio.wait_for(reading, 120)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--fast", action="store_true", help="do not pace the audio in real time")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.wav, args.fast)))
