import asyncio
import json
import struct

import pytest
from quart import Quart
from quart.testing.connections import WebsocketDisconnectError

from app.call_manager import CallManager
from app.transcription.backend import FakeBackend
from app.transcription.events import Speaker, TranscriptEvent
from app.transcription.routes import register_transcription_routes
from app.transcription.transcript_hub import TranscriptHub
from app.transcription.web_handler import WebTranscriptionHandler


def _app(token=""):
    app = Quart(__name__)
    hub = TranscriptHub()
    register_transcription_routes(
        app,
        CallManager(),
        validate_token=lambda t: not token or t == token,
        hub=hub,
        backend_factory=lambda config, rate: FakeBackend(rate),
    )
    return app, hub


def _stereo(seconds, rate=16000):
    frame = struct.pack("<hh", 100, -100)
    return frame * (rate * seconds)


async def test_web_handler_splits_stereo_and_labels_speakers():
    hub = TranscriptHub()
    q = hub.subscribe("w1")
    h = WebTranscriptionHandler({}, "w1", hub=hub, backend_factory=lambda c, r: FakeBackend(r))
    await h.start()
    await h.on_message(_stereo(2))
    await h.on_message("ignored text")
    await h.cleanup()
    events = [q.get_nowait() for _ in range(q.qsize())]
    finals = [e for e in events if isinstance(e, TranscriptEvent)]
    assert sorted(e.speaker for e in finals) == [Speaker.AGENT, Speaker.CUSTOMER]
    assert hub.active() == []


async def test_active_requires_token():
    app, hub = _app(token="t")
    hub.mark_active("c1")
    client = app.test_client()
    assert (await client.get("/transcript/active")).status_code == 403
    resp = await client.get("/transcript/active?token=t")
    assert resp.status_code == 200
    assert (await resp.get_json()) == {"conversations": ["c1"]}


async def test_transcript_ws_rejects_bad_token():
    app, _ = _app(token="t")
    async with app.test_client().websocket("/transcript/ws?conversationId=c1&token=bad") as ws:
        with pytest.raises(WebsocketDisconnectError) as e:
            await ws.receive()
    assert e.value.args[0] == 4403


async def test_web_transcribe_ws_streams_to_transcript_viewer():
    app, hub = _app()
    client = app.test_client()
    async with client.websocket("/transcript/ws?conversationId=demo1") as viewer:
        await asyncio.sleep(0.05)
        async with client.websocket("/web/transcribe/ws?conversationId=demo1") as source:
            session = json.loads(await source.receive())
            assert session == {"type": "session", "conversationId": "demo1"}
            await asyncio.sleep(0.05)
            await source.send(_stereo(2))
            seen = []
            while len([m for m in seen if m["type"] == "transcript"]) < 2:
                seen.append(json.loads(await asyncio.wait_for(viewer.receive(), 2)))
        speakers = {m["speaker"] for m in seen if m["type"] == "transcript"}
        assert speakers == {"customer", "agent"}
        assert seen[0] == {"type": "status", "conversationId": "demo1", "state": "connected", "detail": ""}


@pytest.mark.parametrize("path,marker", [
    ("/transcript", "/transcript/ws"),
    ("/transcribe-test", "/web/transcribe/ws"),
])
async def test_pages_are_served(path, marker):
    app, _ = _app(token="t")
    resp = await app.test_client().get(path)
    assert resp.status_code == 200
    assert marker in (await resp.get_data(as_text=True))
