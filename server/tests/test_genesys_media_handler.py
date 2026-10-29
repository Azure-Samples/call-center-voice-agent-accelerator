import json

import pytest

from app.providers.genesys.media_handler import GenesysMediaHandler

BASE_CONFIG = {
    "AZURE_VOICE_LIVE_ENDPOINT": "https://example.invalid",
    "VOICE_LIVE_MODEL": "m",
    "AZURE_VOICE_LIVE_API_KEY": "k",
    "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID": "",
    "GENESYS_API_KEY": "secret",
}


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    def json_msgs(self):
        return [json.loads(m) for m in self.sent if isinstance(m, str)]


def _open(media, seq=1):
    return json.dumps(
        {
            "version": "2",
            "type": "open",
            "seq": seq,
            "id": "sess-1",
            "parameters": {"conversationId": "conv-1", "participant": {}, "media": media},
        }
    )


EXT = {"type": "audio", "format": "PCMU", "channels": ["external"], "rate": 8000}
BOTH = {"type": "audio", "format": "PCMU", "channels": ["external", "internal"], "rate": 8000}


@pytest.fixture
def handler():
    h = GenesysMediaHandler(BASE_CONFIG)
    h.genesys_ws = FakeWS()
    return h


def test_validate_api_key(handler):
    assert handler.validate_api_key("secret")
    assert not handler.validate_api_key("wrong")
    assert not handler.validate_api_key("")


async def test_open_prefers_external_only(handler):
    await handler.on_message(_open([BOTH, EXT]))
    (opened,) = handler.genesys_ws.json_msgs()
    assert opened["type"] == "opened"
    assert opened["seq"] == 1 and opened["clientseq"] == 1 and opened["id"] == "sess-1"
    assert opened["parameters"]["media"] == [EXT]
    assert opened["parameters"]["startPaused"] is False


async def test_open_falls_back_to_first_pcmu(handler):
    await handler.on_message(_open([{"format": "L16"}, BOTH]))
    assert handler.genesys_ws.json_msgs()[0]["parameters"]["media"] == [BOTH]


async def test_open_without_pcmu_disconnects(handler):
    await handler.on_message(_open([{"format": "L16"}]))
    (msg,) = handler.genesys_ws.json_msgs()
    assert msg["type"] == "disconnect"
    assert msg["parameters"]["reason"] == "error"


@pytest.mark.parametrize(
    "req,resp",
    [("ping", "pong"), ("close", "closed"), ("update", "updated"), ("pause", "paused"), ("resume", "resumed")],
)
async def test_control_messages_are_acknowledged(handler, req, resp):
    await handler.on_message(_open([EXT]))
    await handler.on_message(json.dumps({"type": req, "seq": 2, "id": "sess-1", "parameters": {}}))
    msgs = handler.genesys_ws.json_msgs()
    assert msgs[-1]["type"] == resp
    assert msgs[-1]["seq"] == 2 and msgs[-1]["clientseq"] == 2


async def test_audio_ignored_while_paused(handler):
    await handler.on_message(_open([EXT]))
    await handler.on_message(json.dumps({"type": "pause", "seq": 2, "id": "sess-1"}))
    await handler.on_message(b"\xff" * 160)
    assert handler._in_frame_count == 0
