import asyncio

from app.transcription.events import Speaker, StatusEvent, StreamState, TranscriptEvent
from app.transcription.transcript_hub import TranscriptHub


def _t(text, final=True, cid="c1"):
    return TranscriptEvent(cid, Speaker.CUSTOMER, text, final)


async def test_subscriber_receives_published_events():
    hub = TranscriptHub()
    q = hub.subscribe("c1")
    hub.publish(_t("hi"))
    ev = await asyncio.wait_for(q.get(), 1)
    assert ev.text == "hi"


async def test_events_are_scoped_to_conversation():
    hub = TranscriptHub()
    q1 = hub.subscribe("c1")
    q2 = hub.subscribe("c2")
    hub.publish(_t("one", cid="c1"))
    assert q1.qsize() == 1
    assert q2.qsize() == 0


async def test_unsubscribe_stops_delivery():
    hub = TranscriptHub()
    q = hub.subscribe("c1")
    hub.unsubscribe("c1", q)
    hub.publish(_t("x"))
    assert q.qsize() == 0


async def test_overflow_drops_oldest():
    hub = TranscriptHub(max_queue=2)
    q = hub.subscribe("c1")
    hub.publish(_t("f1"))
    hub.publish(_t("p", final=False))
    hub.publish(StatusEvent("c1", StreamState.ENDED))
    assert q.get_nowait().text == "p"
    assert isinstance(q.get_nowait(), StatusEvent)


def test_active_registry():
    hub = TranscriptHub()
    hub.mark_active("c1")
    hub.mark_active("c2")
    hub.mark_ended("c1")
    assert hub.active() == ["c2"]
