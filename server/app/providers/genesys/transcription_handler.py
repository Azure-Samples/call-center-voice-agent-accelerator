"""Genesys AudioHook Monitor -> live speaker-labeled transcription.

Listen-only: consumes stereo PCMU (external = customer, internal = agent) and
never sends audio back to Genesys.
"""

import logging

from app.providers.genesys.audiohook import AudioHookSession, validate_api_key
from app.transcription.audio import deinterleave, pcmu_to_pcm16
from app.transcription.backend import create_backend
from app.transcription.events import Speaker
from app.transcription.session import TranscriptionSession
from app.transcription.transcript_hub import TranscriptHub, get_hub

logger = logging.getLogger(__name__)

GENESYS_SAMPLE_RATE = 8000

CHANNEL_SPEAKERS = {"external": Speaker.CUSTOMER, "internal": Speaker.AGENT}

PREFERRED_CHANNELS = [["external", "internal"], ["internal", "external"], ["external"]]


class GenesysTranscriptionHandler:
    def __init__(self, config, hub: TranscriptHub | None = None, backend_factory=create_backend):
        self.config = config
        self.api_key = config.get("GENESYS_API_KEY", "")
        self.hub = hub or get_hub()
        self._backend_factory = backend_factory
        self.genesys_ws = None
        self._audiohook = AudioHookSession(
            send=lambda m: self.genesys_ws.send(m),
            preferred_channels=PREFERRED_CHANNELS,
            log_prefix="[GenesysTranscription]",
        )
        self._session: TranscriptionSession | None = None
        self._speakers: list[Speaker] = []
        self._frames = 0

    def validate_api_key(self, provided_key: str) -> bool:
        return validate_api_key(self.api_key, provided_key)

    async def init_websocket(self, socket):
        self.genesys_ws = socket

    async def start(self) -> None:
        """No-op: transcription starts on the AudioHook 'open' message."""

    async def on_message(self, message):
        if isinstance(message, bytes):
            self._handle_audio(message)
            return
        msg_type = await self._audiohook.handle_text(message)
        if msg_type == "open" and self._audiohook.is_open:
            await self._open_session()
        elif msg_type == "close":
            await self._close_session()

    async def _open_session(self) -> None:
        if self._session is not None:
            return
        channels = self._audiohook.channels or ["external"]
        self._speakers = [CHANNEL_SPEAKERS.get(c, Speaker.UNKNOWN) for c in channels]
        if len(channels) == 1:
            logger.warning(
                "[GenesysTranscription] Mono media (%s) — speaker separation unavailable", channels
            )
        cid = self._audiohook.conversation_id or self._audiohook.session_id or "unknown"
        backend = self._backend_factory(self.config, GENESYS_SAMPLE_RATE)
        self._session = TranscriptionSession(backend, self.hub, self._speakers)
        await self._session.open(cid)

    def _handle_audio(self, frame: bytes) -> None:
        if self._session is None or not self._audiohook.is_open or self._audiohook.paused:
            return
        self._frames += 1
        if self._frames == 1:
            logger.info("[GenesysTranscription] First audio frame: %d bytes", len(frame))
        for speaker, chan in zip(self._speakers, deinterleave(frame, len(self._speakers), 1)):
            self._session.push(speaker, pcmu_to_pcm16(chan))

    async def _close_session(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def cleanup(self) -> None:
        await self._close_session()
