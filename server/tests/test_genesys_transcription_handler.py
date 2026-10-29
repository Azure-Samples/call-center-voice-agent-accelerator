import json

from app.providers.genesys.transcription_handler import GenesysTranscriptionHandler
from app.transcription.events import Speaker, StatusEvent, StreamState
from app.transcription.transcript_hub import TranscriptHub


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    def json_msgs(self):
        return [json.loads(m) for m in self.sent if isinstance(m, str)]


class RecordingBackend:
    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.audio = []
        self.started = None
        self.stopped = 0

    async def start(self, conversation_id, speakers, on_event):
        self.started = (conversation_id, list(speakers))

    def push_audio(self, speaker, pcm16):
        self.audio.append((speaker, pcm16))

    async def stop(self):
        self.stopped += 1


def _open(channels_list):
    media = [{"type": "audio", "format": "PCMU", "channels": c, "rate": 8000} for c in channels_list]
    return json.dumps({
        "version": "2", "type": "open", "seq": 1, "id": "sess-1",
        "parameters": {"conversationId": "conv-1", "participant": {}, "media": media},
    })


def _make(hub=None):
    backends = []

    def factory(config, sample_rate):
        b = RecordingBackend(sample_rate)
        backends.append(b)
        return b

    h = GenesysTranscriptionHandler(
        {"GENESYS_API_KEY": "secret"}, hub=hub or TranscriptHub(), backend_factory=factory
    )
    ws = FakeWS()
    h.genesys_ws = ws
    return h, ws, backends


async def test_open_prefers_stereo_and_starts_backend_with_both_speakers():
    hub = TranscriptHub()
    h, ws, backends = _make(hub)
    await h.start()
    await h.on_message(_open([["external"], ["external", "internal"]]))
    opened = ws.json_msgs()[0]
    assert opened["parameters"]["media"][0]["channels"] == ["external", "internal"]
    (be,) = backends
    assert be.sample_rate == 8000
    assert be.started == ("conv-1", [Speaker.CUSTOMER, Speaker.AGENT])
    assert hub.active() == ["conv-1"]


async def test_stereo_audio_is_split_by_channel_order():
    h, ws, backends = _make()
    await h.on_message(_open([["internal", "external"]]))
    # interleaved [internal, external] mu-law bytes: 0xFF silence, 0x00 loud negative
    await h.on_message(bytes([0xFF, 0x00, 0xFF, 0x00]))
    be = backends[0]
    assert be.started[1] == [Speaker.AGENT, Speaker.CUSTOMER]
    by_speaker = dict(be.audio)
    assert by_speaker[Speaker.AGENT] == b"\x00\x00" * 2
    assert by_speaker[Speaker.CUSTOMER] != b"\x00\x00" * 2
    assert len(by_speaker[Speaker.CUSTOMER]) == 4
    assert not any(isinstance(m, bytes) for m in ws.sent)  # never sends audio back


async def test_mono_external_is_labeled_customer():
    h, ws, backends = _make()
    await h.on_message(_open([["external"]]))
    await h.on_message(b"\xff" * 160)
    assert backends[0].started[1] == [Speaker.CUSTOMER]
    assert backends[0].audio[0][0] == Speaker.CUSTOMER
    assert len(backends[0].audio[0][1]) == 320


async def test_paused_audio_is_dropped():
    h, ws, backends = _make()
    await h.on_message(_open([["external"]]))
    await h.on_message(json.dumps({"type": "pause", "seq": 2, "id": "sess-1"}))
    await h.on_message(b"\xff" * 160)
    assert backends[0].audio == []


async def test_close_and_cleanup_end_session_once():
    hub = TranscriptHub()
    q = hub.subscribe("conv-1")
    h, ws, backends = _make(hub)
    await h.on_message(_open([["external", "internal"]]))
    await h.on_message(json.dumps({"type": "close", "seq": 2, "id": "sess-1", "parameters": {}}))
    await h.cleanup()
    assert ws.json_msgs()[-1]["type"] == "closed"
    assert backends[0].stopped == 1
    assert hub.active() == []
    events = [q.get_nowait() for _ in range(q.qsize())]
    assert [e.state for e in events if isinstance(e, StatusEvent)] == [
        StreamState.CONNECTED,
        StreamState.ENDED,
    ]


async def test_no_compatible_media_does_not_start_backend():
    h, ws, backends = _make()
    await h.on_message(json.dumps({
        "type": "open", "seq": 1, "id": "s",
        "parameters": {"conversationId": "c", "media": [{"format": "L16"}]},
    }))
    assert backends == []
    assert ws.json_msgs()[0]["type"] == "disconnect"


def test_validate_api_key():
    h, _, _ = _make()
    assert h.validate_api_key("secret")
    assert not h.validate_api_key("nope")
