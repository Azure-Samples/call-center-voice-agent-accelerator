import struct

import numpy as np
import pytest

from app.transcription.audio import deinterleave, pcmu_to_pcm16
from app.transcription.events import (
    Speaker,
    StatusEvent,
    StreamState,
    TranscriptEvent,
)


def test_transcript_event_to_dict_uses_camel_case():
    ev = TranscriptEvent(
        conversation_id="c1",
        speaker=Speaker.CUSTOMER,
        text="hello",
        is_final=True,
        offset_ms=100,
        duration_ms=500,
        seq=3,
    )
    d = ev.to_dict()
    assert d == {
        "type": "transcript",
        "conversationId": "c1",
        "speaker": "customer",
        "text": "hello",
        "isFinal": True,
        "offsetMs": 100,
        "durationMs": 500,
        "seq": 3,
    }


def test_status_event_to_dict():
    ev = StatusEvent(conversation_id="c1", state=StreamState.DEGRADED, detail="x")
    assert ev.to_dict() == {
        "type": "status",
        "conversationId": "c1",
        "state": "degraded",
        "detail": "x",
    }


def test_deinterleave_pcm16_stereo():
    left = [1, 2, 3]
    right = [-1, -2, -3]
    data = b"".join(struct.pack("<hh", l, r) for l, r in zip(left, right))
    chans = deinterleave(data, channels=2, sample_width=2)
    assert len(chans) == 2
    assert np.frombuffer(chans[0], dtype="<i2").tolist() == left
    assert np.frombuffer(chans[1], dtype="<i2").tolist() == right


def test_deinterleave_ulaw_bytes():
    chans = deinterleave(bytes([1, 2, 3, 4, 5, 6]), channels=2, sample_width=1)
    assert chans == [bytes([1, 3, 5]), bytes([2, 4, 6])]


def test_deinterleave_mono_passthrough():
    assert deinterleave(b"\x01\x02", channels=1, sample_width=2) == [b"\x01\x02"]


def test_deinterleave_drops_trailing_partial_frame():
    chans = deinterleave(bytes([1, 2, 3]), channels=2, sample_width=1)
    assert chans == [bytes([1]), bytes([2])]


def test_deinterleave_rejects_bad_channels():
    with pytest.raises(ValueError):
        deinterleave(b"", channels=0, sample_width=2)


def test_pcmu_to_pcm16_doubles_length_and_decodes_silence():
    # 0xFF is mu-law silence (0)
    out = pcmu_to_pcm16(bytes([0xFF, 0xFF]))
    assert len(out) == 4
    assert np.frombuffer(out, dtype="<i2").tolist() == [0, 0]
