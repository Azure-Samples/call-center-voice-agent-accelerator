"""Handles Genesys AudioHook Audio Connector WebSocket and bridges audio to Azure Voice Live API.

Genesys AudioHook protocol (Audio Connector feature):
- WebSocket upgrade with X-API-KEY header for authentication
- JSON text messages: open/opened, ping/pong, close/closed, update/updated, pause/resume
- Binary messages: raw PCMU audio at 8kHz (mono, external channel only)
- Server sends binary PCMU 8kHz audio back to client for playback

Reference: https://developer.genesys.cloud/devapps/audiohook
"""

import audioop
import collections
import logging

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler
from app.providers.genesys.audiohook import AudioHookSession, validate_api_key
from azure.ai.voicelive.models import InputAudioFormat, OutputAudioFormat

logger = logging.getLogger(__name__)

# Genesys sends PCMU 8000Hz; Voice Live expects PCM 24000Hz 16-bit mono.
GENESYS_SAMPLE_RATE = 8000
VOICELIVE_SAMPLE_RATE = 24000

# Output pacing: 20ms frames at 8kHz PCMU = 160 bytes per frame
PCMU_FRAME_BYTES = 160  # 8000 samples/sec * 0.02 sec * 1 byte (PCMU)


class GenesysMediaHandler(VoiceLiveMediaHandler):
    """Bridges Genesys AudioHook Audio Connector to Azure Voice Live API.

    Handles PCMU/PCM conversion, rate resampling (8kHz↔24kHz), and the
    AudioHook session lifecycle protocol.
    """

    def __init__(self, config):
        super().__init__(config)
        self.api_key = config.get("GENESYS_API_KEY", "")
        self.genesys_ws = None
        # Voice agent consumes the caller (external) channel only.
        self._audiohook = AudioHookSession(
            send=lambda m: self.genesys_ws.send(m),
            preferred_channels=[["external"]],
            log_prefix="[GenesysHandler]",
        )
        self._ratecv_state_in = None
        self._ratecv_state_out = None
        self._in_frame_count = 0
        self._out_frame_count = 0
        # Paced output buffer for barge-in support
        self._out_buffer = collections.deque()
        self._pcmu_remainder = b''  # Carry partial frames to next chunk

        # Native 8kHz g711 mode: have Voice Live send/receive mu-law 8kHz
        # directly so we skip the 8k<->24k resampling round-trip (lower
        # latency, no quality loss). Configurable via GENESYS_VOICELIVE_FORMAT:
        #   "pcm16" (default)     = legacy 8k<->24k resample, ambient supported
        #   "g711_ulaw" (opt-in)  = native 8kHz, ambient mixing not available
        fmt = str(config.get("GENESYS_VOICELIVE_FORMAT", "pcm16")).strip().lower()
        self._native_8k = fmt in ("g711_ulaw", "g711", "ulaw", "8k")
        if self._native_8k:
            self.input_audio_format = InputAudioFormat.G711_ULAW
            self.output_audio_format = OutputAudioFormat.G711_ULAW
            if self._ambient_mixer is not None:
                logger.warning(
                    "[GenesysHandler] Ambient mixing is not supported in native "
                    "8kHz g711 mode \u2014 disabling it. Set GENESYS_VOICELIVE_FORMAT=pcm16 "
                    "to keep ambient (with 8k\u219224k resampling)."
                )
                self._ambient_mixer = None
            logger.info("[GenesysHandler] Native 8kHz g711 mode ENABLED (no resampling)")
        else:
            logger.info("[GenesysHandler] Legacy PCM16 mode (8k\u219224k resampling)")

    # ------------------------------------------------------------------
    # Authentication (WebSocket upgrade headers)
    # ------------------------------------------------------------------

    def validate_api_key(self, provided_key: str) -> bool:
        """Validate the X-API-KEY header from the WebSocket upgrade request."""
        return validate_api_key(self.api_key, provided_key)

    # ------------------------------------------------------------------
    # AudioHook protocol message handling
    # ------------------------------------------------------------------

    async def on_message(self, message):
        """Process one incoming Genesys AudioHook WebSocket message.

        Binary messages = PCMU 8kHz audio frames.
        Text messages = JSON protocol messages (open, ping, close, update, pause, resume).
        """
        if isinstance(message, bytes):
            await self._handle_audio_frame(message)
            return
        await self._audiohook.handle_text(message)

    # ------------------------------------------------------------------
    # Audio handling
    # ------------------------------------------------------------------

    async def _handle_audio_frame(self, frame: bytes):
        """Process incoming PCMU 8kHz audio from Genesys."""
        if not self._audiohook.is_open or self._audiohook.paused:
            return

        self._in_frame_count += 1
        if self._in_frame_count == 1:
            logger.info("[GenesysHandler] First audio frame: %d bytes", len(frame))
        elif self._in_frame_count % 500 == 0:
            logger.info("[GenesysHandler] Audio frames in: %d", self._in_frame_count)

        # Send buffered output audio proportional to input duration.
        # Only send when we have real audio — avoids low-level noise from
        # decoding silence PCMU frames on the client side.
        frames_to_send = max(1, len(frame) // PCMU_FRAME_BYTES)
        try:
            if self._out_buffer:
                chunks = []
                for _ in range(frames_to_send):
                    if self._out_buffer:
                        chunks.append(self._out_buffer.popleft())
                    else:
                        break
                await self.genesys_ws.send(b''.join(chunks))
        except Exception as e:
            logger.debug("Genesys audio send failed: %s", e)

        # Forward inbound audio to Voice Live.
        if not self._voicelive_connected:
            return

        try:
            if self._native_8k:
                # Native g711: forward mu-law 8kHz straight through (no resample).
                await self.handle_audio(frame)
            else:
                # Convert PCMU 8kHz \u2192 PCM 16-bit 24kHz for Voice Live
                pcm_8k = audioop.ulaw2lin(frame, 2)
                pcm_24k, self._ratecv_state_in = audioop.ratecv(
                    pcm_8k, 2, 1, GENESYS_SAMPLE_RATE, VOICELIVE_SAMPLE_RATE, self._ratecv_state_in
                )
                await self.handle_audio(pcm_24k)
        except Exception:
            logger.exception("[GenesysHandler] Error converting audio frame %d", self._in_frame_count)

    # ------------------------------------------------------------------
    # Voice Live hooks
    # ------------------------------------------------------------------

    async def on_speech_started(self):
        """Barge-in: clear buffered AI audio and reset output state."""
        self._out_buffer.clear()
        self._pcmu_remainder = b''
        self._ratecv_state_out = None

    async def on_transcript_done(self, transcript: str):
        """Log transcript — AudioHook doesn't have a text channel for this."""
        logger.info("[GenesysHandler] AI transcript: %s", transcript)

    # ------------------------------------------------------------------
    # Audio output to Genesys (from Voice Live TTS)
    # ------------------------------------------------------------------

    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Buffer Voice Live TTS as paced PCMU 8kHz frames for delivery.
        """
        self._out_frame_count += 1
        if self._out_frame_count == 1:
            logger.info("[GenesysHandler] First outgoing audio: %d bytes", len(audio_bytes))

        try:
            if self._native_8k:
                pcmu = audio_bytes
            else:
                pcm_8k, self._ratecv_state_out = audioop.ratecv(
                    audio_bytes, 2, 1, VOICELIVE_SAMPLE_RATE, GENESYS_SAMPLE_RATE, self._ratecv_state_out
                )
                pcmu = audioop.lin2ulaw(pcm_8k, 2)
            # Prepend any remainder from previous call for continuity
            pcmu = self._pcmu_remainder + pcmu
            # Buffer complete 160-byte (20ms) frames for paced delivery
            offset = 0
            while offset + PCMU_FRAME_BYTES <= len(pcmu):
                self._out_buffer.append(pcmu[offset:offset + PCMU_FRAME_BYTES])
                offset += PCMU_FRAME_BYTES
            # Keep remainder for next call (no silence padding = no squeaks)
            self._pcmu_remainder = pcmu[offset:]
        except Exception:
            logger.exception("[GenesysHandler] Error converting outgoing audio")
