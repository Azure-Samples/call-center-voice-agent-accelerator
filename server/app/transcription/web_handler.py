"""Browser harness -> live transcription.

Receives interleaved stereo PCM16 at 16 kHz (left = customer, right = agent).
"""

import logging

from app.transcription.audio import deinterleave
from app.transcription.backend import create_backend
from app.transcription.events import Speaker
from app.transcription.session import TranscriptionSession
from app.transcription.transcript_hub import TranscriptHub, get_hub

logger = logging.getLogger(__name__)

WEB_SAMPLE_RATE = 16000
WEB_SPEAKERS = [Speaker.CUSTOMER, Speaker.AGENT]


class WebTranscriptionHandler:
    def __init__(
        self,
        config,
        conversation_id: str,
        hub: TranscriptHub | None = None,
        backend_factory=create_backend,
    ):
        self.config = config
        self.conversation_id = conversation_id
        self.hub = hub or get_hub()
        self._backend_factory = backend_factory
        self._session: TranscriptionSession | None = None

    async def start(self) -> None:
        backend = self._backend_factory(self.config, WEB_SAMPLE_RATE)
        self._session = TranscriptionSession(backend, self.hub, WEB_SPEAKERS)
        await self._session.open(self.conversation_id)

    async def on_message(self, message) -> None:
        if not isinstance(message, bytes) or self._session is None:
            return
        for speaker, chan in zip(WEB_SPEAKERS, deinterleave(message, 2, 2)):
            self._session.push(speaker, chan)

    async def cleanup(self) -> None:
        if self._session is not None:
            await self._session.close()
