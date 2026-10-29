"""Transcript event model shared by backends, the hub and the web UI."""

import json
from dataclasses import dataclass
from enum import StrEnum


class Speaker(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    UNKNOWN = "unknown"


class StreamState(StrEnum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    ENDED = "ended"


@dataclass(frozen=True)
class TranscriptEvent:
    conversation_id: str
    speaker: Speaker
    text: str
    is_final: bool
    offset_ms: int = 0
    duration_ms: int = 0
    seq: int = 0

    def to_dict(self) -> dict:
        return {
            "type": "transcript",
            "conversationId": self.conversation_id,
            "speaker": str(self.speaker),
            "text": self.text,
            "isFinal": self.is_final,
            "offsetMs": self.offset_ms,
            "durationMs": self.duration_ms,
            "seq": self.seq,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass(frozen=True)
class StatusEvent:
    conversation_id: str
    state: StreamState
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "type": "status",
            "conversationId": self.conversation_id,
            "state": str(self.state),
            "detail": self.detail,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


HubEvent = TranscriptEvent | StatusEvent
