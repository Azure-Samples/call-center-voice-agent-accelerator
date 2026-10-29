import asyncio

import pytest

from app.transcription.backend import FakeBackend, create_backend
from app.transcription.events import Speaker, StatusEvent, StreamState, TranscriptEvent
from app.transcription.session import TranscriptionSession
from app.transcription.transcript_hub import TranscriptHub


class RecordingBackend:
    def __init__(self, fail_start=False):
        self.sample_rate = 8000
        self.started = None
        self.audio = []
        self.stopped = 0
        self.fail_start = fail_start
        self.on_event = None

    async def start(self, conversation_id, speakers, on_event):
        if self.fail_start:
            raise RuntimeError("boom")
        self.started = (conversation_id, list(speakers))
        self.on_event = on_event

    def push_audio(self, speaker, pcm16):
        self.audio.append((speaker, pcm16))

    async def stop(self):
        self.stopped += 1


async def test_fake_backend_emits_final_per_two_seconds_of_audio():
    events = []
    be = FakeBackend(sample_rate=8000)
    await be.start("c1", [Speaker.CUSTOMER, Speaker.AGENT], events.append)
    be.push_audio(Speaker.CUSTOMER, b"\x00\x00" * 8000)  # 1 s
    assert [e for e in events if e.is_final] == []
    be.push_audio(Speaker.CUSTOMER, b"\x00\x00" * 8000)  # 2 s
    be.push_audio(Speaker.AGENT, b"\x00\x00" * 16000)
    finals = [e for e in events if e.is_final]
    assert [e.speaker for e in finals] == [Speaker.CUSTOMER, Speaker.AGENT]
    assert all(isinstance(e, TranscriptEvent) and e.conversation_id == "c1" for e in finals)
    assert finals[0].offset_ms == 0 and finals[0].duration_ms == 2000
    await be.stop()


def test_create_backend_fake():
    be = create_backend({"TRANSCRIPTION_BACKEND": "fake"}, sample_rate=16000)
    assert isinstance(be, FakeBackend)
    assert be.sample_rate == 16000


def test_create_backend_unknown_raises():
    with pytest.raises(ValueError):
        create_backend({"TRANSCRIPTION_BACKEND": "nope"}, sample_rate=8000)


async def test_session_open_push_close():
    hub = TranscriptHub()
    q = hub.subscribe("c1")
    be = RecordingBackend()
    s = TranscriptionSession(be, hub, [Speaker.CUSTOMER, Speaker.AGENT])
    s.push(Speaker.CUSTOMER, b"ignored-before-open")
    await s.open("c1")
    assert hub.active() == ["c1"]
    assert be.started == ("c1", [Speaker.CUSTOMER, Speaker.AGENT])
    s.push(Speaker.AGENT, b"ab")
    assert be.audio == [(Speaker.AGENT, b"ab")]
    be.on_event(TranscriptEvent("c1", Speaker.AGENT, "hello", True))
    await s.close()
    await s.close()  # idempotent
    assert be.stopped == 1
    assert hub.active() == []
    events = [q.get_nowait() for _ in range(q.qsize())]
    assert isinstance(events[0], StatusEvent) and events[0].state == StreamState.CONNECTED
    assert events[1].text == "hello"
    assert isinstance(events[-1], StatusEvent) and events[-1].state == StreamState.ENDED


async def test_session_start_failure_publishes_degraded_and_does_not_raise():
    hub = TranscriptHub()
    q = hub.subscribe("c1")
    be = RecordingBackend(fail_start=True)
    s = TranscriptionSession(be, hub, [Speaker.CUSTOMER])
    await s.open("c1")
    s.push(Speaker.CUSTOMER, b"x")
    assert be.audio == []
    ev = q.get_nowait()
    assert ev.state == StreamState.DEGRADED
    await s.close()
    assert hub.active() == []


class SlowStopBackend(FakeBackend):
    def __init__(self):
        super().__init__(16000)
        self.stop_finished = False

    async def stop(self):
        await asyncio.sleep(0.2)
        self.stop_finished = True


async def test_session_close_cancelled_mid_stop_still_publishes_ended():
    hub = TranscriptHub()
    q = hub.subscribe("c1")
    backend = SlowStopBackend()
    s = TranscriptionSession(backend, hub, [Speaker.CUSTOMER])
    await s.open("c1")
    task = asyncio.create_task(s.close())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    states = [e.state for e in (q.get_nowait() for _ in range(q.qsize())) if isinstance(e, StatusEvent)]
    assert states[-1] == StreamState.ENDED
    assert hub.active() == []
    await asyncio.sleep(0.3)
    assert backend.stop_finished  # stop keeps running so recognizer resources are released
