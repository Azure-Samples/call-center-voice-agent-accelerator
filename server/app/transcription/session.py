"""Binds one call's audio to a backend and publishes results to the hub."""

import asyncio
import logging
from typing import Iterable

from app.transcription.backend import TranscriptionBackend
from app.transcription.events import HubEvent, Speaker, StatusEvent, StreamState
from app.transcription.transcript_hub import TranscriptHub

logger = logging.getLogger(__name__)


class TranscriptionSession:
    """Lifecycle wrapper: a backend failure degrades the transcript, never the call."""

    def __init__(
        self, backend: TranscriptionBackend, hub: TranscriptHub, speakers: Iterable[Speaker]
    ):
        self.backend = backend
        self.hub = hub
        self.speakers = list(speakers)
        self.conversation_id: str | None = None
        self._ready = False
        self._closed = False

    async def open(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.hub.mark_active(conversation_id)
        try:
            await self.backend.start(conversation_id, self.speakers, self._publish)
        except Exception as e:
            logger.exception("[%s] Transcription backend failed to start", conversation_id)
            self.hub.publish(StatusEvent(conversation_id, StreamState.DEGRADED, str(e)))
            return
        self._ready = True
        self.hub.publish(StatusEvent(conversation_id, StreamState.CONNECTED))

    def push(self, speaker: Speaker, pcm16: bytes) -> None:
        if self._ready and not self._closed:
            self.backend.push_audio(speaker, pcm16)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._ready = False
        cid = self.conversation_id
        if cid is None:
            return
        try:
            # Stop even if start() never completed (e.g. cancelled) to release partial resources.
            # Shielded: the WebSocket task is often cancelled right after an AudioHook close,
            # while the recognizer is still draining.
            await asyncio.shield(self.backend.stop())
        except Exception:
            logger.exception("[%s] Error stopping transcription backend", cid)
        finally:
            self.hub.publish(StatusEvent(cid, StreamState.ENDED))
            self.hub.mark_ended(cid)

    def _publish(self, event: HubEvent) -> None:
        self.hub.publish(event)
