"""End-to-end check of transcription mode against a running server (no browser needed).

Streams customer.wav + agent.wav as one two-party call through both ingest paths and prints the
speaker-labelled transcript received on /transcript/ws:
  1. Genesys AudioHook  (/audiohook/ws)        8 kHz stereo PCMU, external=customer, internal=agent
  2. Browser harness    (/web/transcribe/ws)  16 kHz stereo PCM16, left=customer, right=agent

Usage (server running with CALL_MODE=transcription and GENESYS_API_KEY=test):
    .venv\\Scripts\\python samples\\transcription\\e2e_live.py [http://localhost:8000] [genesys-api-key]
"""

import asyncio
import json
import sys
import uuid
import wave
from pathlib import Path

import numpy as np
import websockets

HERE = Path(__file__).resolve().parent
CHUNK_SECONDS = 0.2


def load(name: str) -> np.ndarray:
    with wave.open(str(HERE / name), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, name
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def two_party_call() -> tuple[np.ndarray, np.ndarray]:
    """customer.wav on the left; agent.wav (already offset by leading silence) on the right."""
    customer, agent = load("customer.wav"), load("agent.wav")
    n = max(len(customer), len(agent)) + 16000
    left, right = np.zeros(n, np.int16), np.zeros(n, np.int16)
    left[: len(customer)] = customer
    right[: len(agent)] = agent
    return left, right


def to_8k(x: np.ndarray) -> np.ndarray:
    """Crude 16 kHz -> 8 kHz: 2-sample average (low-pass) then decimate. Good enough for a smoke test."""
    x = x[: len(x) // 2 * 2].astype(np.int32)
    return ((x[0::2] + x[1::2]) // 2).astype(np.int16)


def to_ulaw(x: np.ndarray) -> np.ndarray:
    """G.711 mu-law encode."""
    s = x.astype(np.int32)
    sign = np.where(s < 0, 0x80, 0)
    mag = np.minimum(np.abs(s), 32635) + 0x84
    exponent = np.floor(np.log2(mag)).astype(np.int32) - 7
    mantissa = (mag >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8)


async def watch(base_ws: str, cid: str, events: list, ready: asyncio.Event) -> None:
    async with websockets.connect(f"{base_ws}/transcript/ws?conversationId={cid}") as ws:
        ready.set()
        while True:
            try:
                event = json.loads(await asyncio.wait_for(ws.recv(), 30))
            except asyncio.TimeoutError:
                return
            events.append(event)
            if event.get("type") == "status" and event["state"] == "ended":
                return


def report(name: str, events: list) -> bool:
    finals = [e for e in events if e["type"] == "transcript" and e["isFinal"]]
    states = [e["state"] for e in events if e["type"] == "status"]
    print(f"\n=== {name}: {len(finals)} final lines, status={states}")
    for e in finals:
        print(f"  [{e['speaker']:8}] {e['text']}")
    speakers = {e["speaker"] for e in finals}
    ok = {"customer", "agent"} <= speakers and states[-1:] == ["ended"]
    print("  PASS" if ok else "  FAIL (expected customer + agent lines and an 'ended' status)")
    return ok


async def genesys(base_ws: str, api_key: str, left: np.ndarray, right: np.ndarray) -> bool:
    cid, events, ready = str(uuid.uuid4()), [], asyncio.Event()
    watcher = asyncio.create_task(watch(base_ws, cid, events, ready))
    await ready.wait()
    ext, internal = to_ulaw(to_8k(left)), to_ulaw(to_8k(right))
    stereo = np.empty(len(ext) * 2, np.uint8)
    stereo[0::2], stereo[1::2] = ext, internal
    stereo = stereo.tobytes()
    session_id = str(uuid.uuid4())
    async with websockets.connect(f"{base_ws}/audiohook/ws?apikey={api_key}") as ws:
        await ws.send(json.dumps({
            "version": "2", "type": "open", "seq": 1, "serverseq": 0, "id": session_id, "position": "PT0S",
            "parameters": {
                "organizationId": "demo-org", "conversationId": cid,
                "participant": {"id": "p1", "ani": "+15555550100", "aniName": "Demo", "dnis": "+18005550100"},
                "media": [{"type": "audio", "format": "PCMU", "channels": ["external", "internal"], "rate": 8000}],
                "language": "en-US",
            },
        }))
        opened = json.loads(await ws.recv())
        print(f"Genesys opened, media={opened['parameters']['media']}")
        step = int(8000 * CHUNK_SECONDS) * 2
        for i in range(0, len(stereo), step):
            await ws.send(stereo[i : i + step])
            await asyncio.sleep(CHUNK_SECONDS)
        await ws.send(json.dumps({"version": "2", "type": "close", "seq": 2, "serverseq": opened["seq"],
                                  "id": session_id, "parameters": {"reason": "end"}}))
        await ws.recv()
    await watcher
    return report("Genesys AudioHook (8 kHz stereo PCMU)", events)


async def web(base_ws: str, left: np.ndarray, right: np.ndarray) -> bool:
    interleaved = np.empty(len(left) * 2, np.int16)
    interleaved[0::2], interleaved[1::2] = left, right
    data = interleaved.tobytes()
    async with websockets.connect(f"{base_ws}/web/transcribe/ws") as ws:
        cid = json.loads(await ws.recv())["conversationId"]
        events, ready = [], asyncio.Event()
        watcher = asyncio.create_task(watch(base_ws, cid, events, ready))
        await ready.wait()
        step = int(16000 * CHUNK_SECONDS) * 4
        for i in range(0, len(data), step):
            await ws.send(data[i : i + step])
            await asyncio.sleep(CHUNK_SECONDS)
    await watcher
    return report("Browser harness (16 kHz stereo PCM16)", events)


async def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    api_key = sys.argv[2] if len(sys.argv) > 2 else "test"
    base_ws = base.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
    left, right = two_party_call()
    ok = await genesys(base_ws, api_key, left, right)
    ok = await web(base_ws, left, right) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
