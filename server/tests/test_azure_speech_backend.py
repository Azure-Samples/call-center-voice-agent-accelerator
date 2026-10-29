import asyncio
from types import SimpleNamespace

import azure.cognitiveservices.speech as speechsdk

from app.transcription.azure_speech_backend import (
    AzureSpeechBackend,
    build_speech_config,
    resolve_speech_settings,
)
from app.transcription.events import Speaker, StatusEvent, StreamState, TranscriptEvent


class Signal:
    def __init__(self):
        self.cbs = []

    def connect(self, cb):
        self.cbs.append(cb)

    def fire(self, evt):
        for cb in self.cbs:
            cb(evt)


class FakeRecognizer:
    def __init__(self):
        self.recognizing = Signal()
        self.recognized = Signal()
        self.canceled = Signal()
        self.started = 0
        self.stopped = 0

    def start_continuous_recognition(self):
        self.started += 1

    def stop_continuous_recognition(self):
        self.stopped += 1


class FakeStream:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, b):
        self.data += b

    def close(self):
        self.closed = True


class Factory:
    def __init__(self):
        self.made = []

    def __call__(self, speech_config, sample_rate):
        pair = (FakeStream(), FakeRecognizer())
        self.made.append(pair)
        return pair


def _result(text, offset=10_000_000, duration=5_000_000):
    return SimpleNamespace(result=SimpleNamespace(text=text, offset=offset, duration=duration))


def _cancel(reason=speechsdk.CancellationReason.Error):
    return SimpleNamespace(
        cancellation_details=SimpleNamespace(reason=reason, error_details="net down")
    )


def _backend(factory, **kw):
    return AzureSpeechBackend(
        {"AZURE_VOICE_LIVE_ENDPOINT": "https://x.cognitiveservices.azure.com/", "AZURE_VOICE_LIVE_API_KEY": "k"},
        sample_rate=8000,
        recognizer_factory=factory,
        restart_base_delay=0,
        **kw,
    )


async def _drain():
    for _ in range(10):
        await asyncio.sleep(0.02)


async def test_start_creates_one_recognizer_per_speaker_and_routes_audio():
    f = Factory()
    be = _backend(f)
    await be.start("c1", [Speaker.CUSTOMER, Speaker.AGENT], lambda e: None)
    assert len(f.made) == 2 and all(r.started == 1 for _, r in f.made)
    be.push_audio(Speaker.AGENT, b"ab")
    assert f.made[1][0].data == b"ab" and f.made[0][0].data == b""
    await be.stop()
    assert all(s.closed and r.stopped == 1 for s, r in f.made)


async def test_events_marshalled_with_speaker_seq_and_ms():
    f = Factory()
    events = []
    be = _backend(f)
    await be.start("c1", [Speaker.CUSTOMER, Speaker.AGENT], events.append)
    cust, agent = f.made[0][1], f.made[1][1]
    # fire from a worker thread like the SDK does
    await asyncio.to_thread(cust.recognizing.fire, _result("hel"))
    await asyncio.to_thread(agent.recognized.fire, _result("hi there"))
    await asyncio.to_thread(cust.recognized.fire, _result(""))  # ignored
    await _drain()
    assert [(e.speaker, e.text, e.is_final, e.seq) for e in events] == [
        (Speaker.CUSTOMER, "hel", False, 1),
        (Speaker.AGENT, "hi there", True, 2),
    ]
    assert events[0].offset_ms == 1000 and events[0].duration_ms == 500
    assert all(isinstance(e, TranscriptEvent) and e.conversation_id == "c1" for e in events)
    await be.stop()


async def test_error_cancellation_restarts_channel_and_reports_status():
    f = Factory()
    events = []
    be = _backend(f)
    await be.start("c1", [Speaker.CUSTOMER], events.append)
    await asyncio.to_thread(f.made[0][1].canceled.fire, _cancel())
    await _drain()
    statuses = [e.state for e in events if isinstance(e, StatusEvent)]
    assert statuses == [StreamState.DEGRADED, StreamState.CONNECTED]
    assert len(f.made) == 2
    be.push_audio(Speaker.CUSTOMER, b"z")
    assert f.made[1][0].data == b"z"
    await be.stop()


async def test_gives_up_after_max_restarts():
    f = Factory()
    events = []
    be = _backend(f, max_restarts=1)
    await be.start("c1", [Speaker.CUSTOMER], events.append)
    await asyncio.to_thread(f.made[0][1].canceled.fire, _cancel())
    await _drain()
    await asyncio.to_thread(f.made[1][1].canceled.fire, _cancel())
    await _drain()
    assert len(f.made) == 2
    assert [e.state for e in events if isinstance(e, StatusEvent)][-1] == StreamState.DEGRADED
    be.push_audio(Speaker.CUSTOMER, b"z")  # dead channel: no error
    await be.stop()


async def test_end_of_stream_cancellation_does_not_restart():
    f = Factory()
    be = _backend(f)
    await be.start("c1", [Speaker.CUSTOMER], lambda e: None)
    await asyncio.to_thread(f.made[0][1].canceled.fire, _cancel(speechsdk.CancellationReason.EndOfStream))
    await _drain()
    assert len(f.made) == 1
    await be.stop()


def test_build_speech_config_with_key():
    cfg = build_speech_config(
        {"AZURE_VOICE_LIVE_ENDPOINT": "https://x.cognitiveservices.azure.com/", "AZURE_VOICE_LIVE_API_KEY": "k",
         "TRANSCRIPTION_LANGUAGE": "es-MX"}
    )
    assert cfg.speech_recognition_language == "es-MX"


def test_resolve_speech_settings_prefers_speech_specific_settings():
    assert resolve_speech_settings(
        {"AZURE_SPEECH_ENDPOINT": "https://s/", "AZURE_SPEECH_KEY": "sk",
         "AZURE_VOICE_LIVE_ENDPOINT": "https://other/", "AZURE_VOICE_LIVE_API_KEY": "k"}
    ) == ("https://s/", "sk", "")


def test_resolve_speech_settings_falls_back_to_voice_live_settings():
    assert resolve_speech_settings(
        {"AZURE_VOICE_LIVE_ENDPOINT": "https://v/", "AZURE_VOICE_LIVE_API_KEY": "k",
         "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID": "mi"}
    ) == ("https://v/", "k", "mi")
