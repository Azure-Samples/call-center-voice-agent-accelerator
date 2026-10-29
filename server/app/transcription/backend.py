"""Pluggable speech-to-text backend contract and a fake implementation."""

import logging
from typing import Callable, Iterable, Protocol

from app.transcription.events import HubEvent, Speaker, TranscriptEvent

logger = logging.getLogger(__name__)

EventCallback = Callable[[HubEvent], None]


class TranscriptionBackend(Protocol):
    """Streams per-speaker 16-bit mono PCM and emits transcript events.

    `on_event` is always invoked on the event loop thread.
    """

    sample_rate: int

    async def start(
        self, conversation_id: str, speakers: Iterable[Speaker], on_event: EventCallback
    ) -> None: ...

    def push_audio(self, speaker: Speaker, pcm16: bytes) -> None: ...

    async def stop(self) -> None: ...


class FakeBackend:
    """Offline backend for demos/tests: one final line per ~2 s of audio per speaker."""

    CHUNK_SECONDS = 2

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        self._cid = ""
        self._on_event: EventCallback | None = None
        self._bytes: dict[Speaker, int] = {}
        self._emitted: dict[Speaker, int] = {}
        self._seq = 0

    async def start(self, conversation_id, speakers, on_event):
        self._cid = conversation_id
        self._on_event = on_event
        self._bytes = {s: 0 for s in speakers}
        self._emitted = {s: 0 for s in speakers}

    def push_audio(self, speaker, pcm16):
        if self._on_event is None or speaker not in self._bytes:
            return
        self._bytes[speaker] += len(pcm16)
        chunk = self.sample_rate * 2 * self.CHUNK_SECONDS
        while self._bytes[speaker] - self._emitted[speaker] * chunk >= chunk:
            n = self._emitted[speaker]
            self._emitted[speaker] += 1
            self._seq += 1
            self._on_event(
                TranscriptEvent(
                    conversation_id=self._cid,
                    speaker=speaker,
                    text=f"[fake] {speaker} utterance {n + 1}",
                    is_final=True,
                    offset_ms=n * self.CHUNK_SECONDS * 1000,
                    duration_ms=self.CHUNK_SECONDS * 1000,
                    seq=self._seq,
                )
            )

    async def stop(self):
        self._on_event = None


def create_backend(config: dict, sample_rate: int) -> TranscriptionBackend:
    name = (config.get("TRANSCRIPTION_BACKEND") or "azure_speech").lower()
    if name == "fake":
        return FakeBackend(sample_rate)
    if name == "azure_speech":
        from app.transcription.azure_speech_backend import AzureSpeechBackend

        return AzureSpeechBackend(config, sample_rate)
    raise ValueError(f"Unknown TRANSCRIPTION_BACKEND: {name}")
