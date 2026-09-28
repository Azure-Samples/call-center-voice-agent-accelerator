"""Handles the Vonage WebSocket media endpoint and bridges audio to Voice Live.

Vonage WebSocket protocol (audio/l16;rate=24000):
- First text message: {"event": "websocket:connected", "content-type": "audio/l16;rate=24000"}
- Binary messages: raw PCM 16-bit audio at 24kHz (both directions).
- DTMF text events: {"event": "websocket:dtmf", "digit": "5", "duration": 260}
- Barge-in: send {"action": "clear"} to flush Vonage's internal playback buffer;
  Vonage replies with {"event": "websocket:cleared"}.

Because Voice Live and Vonage both use PCM 24kHz 16-bit mono, audio passes
through untouched — no resampling (and therefore no audioop dependency).

Reference: https://developer.vonage.com/en/voice/voice-api/guides/websockets
"""

import collections
import json
import logging

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler

logger = logging.getLogger(__name__)

# Voice Live uses PCM 24kHz 16-bit mono (960 bytes per 20ms frame).
VOICE_LIVE_SAMPLE_RATE = 24000
VOICE_LIVE_FRAME_BYTES = 960  # 480 samples * 2 bytes = 20ms at 24kHz

# Cap on audio buffered before the WebSocket is authenticated. Vonage emits
# websocket:connected almost immediately, so this only guards against a stalled
# handshake (250 frames = ~5s of audio).
_MAX_PRE_AUTH_FRAMES = 250


class VonageMediaHandler(VoiceLiveMediaHandler):
    """Bridges the Vonage WebSocket media endpoint to Azure Voice Live API.

    Requires the NCCO connect endpoint to use content-type audio/l16;rate=24000
    so audio passes through directly with no format conversion.
    """

    def __init__(self, config, token_validator=None):
        super().__init__(config)
        self.vonage_ws = None
        self.url_token = ""  # one-time token from the WSS query string
        self._authenticated = False
        self._token_validator = token_validator  # callable: validate_ws_token(token) -> bool
        self._out_frame_count = 0
        self._in_frame_count = 0
        # Frames produced before authentication completes are held here and
        # flushed once the handshake is validated so the greeting is not lost.
        self._pre_auth_buffer = collections.deque(maxlen=_MAX_PRE_AUTH_FRAMES)

    # ------------------------------------------------------------------
    # Voice Live hooks
    # ------------------------------------------------------------------

    async def on_speech_started(self):
        """Barge-in: flush Vonage's playback buffer so the caller can interrupt."""
        self._pre_auth_buffer.clear()
        if self._authenticated and self.vonage_ws is not None:
            try:
                await self.vonage_ws.send(json.dumps({"action": "clear"}))
            except Exception as e:
                logger.debug("Vonage clear (barge-in) send failed: %s", e)

    async def on_transcript_done(self, transcript: str):
        """No-op — Vonage has no transcript channel."""
        pass

    # ------------------------------------------------------------------
    # Audio output to client — send raw binary frames directly
    # ------------------------------------------------------------------

    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Split Voice Live PCM (24kHz) into 20ms frames and send them to Vonage.

        Vonage buffers audio internally, so frames are sent directly rather than
        paced. Before authentication they are queued and flushed on connect.
        """
        self._out_frame_count += 1
        if self._out_frame_count == 1:
            logger.info("[VonageMediaHandler] First outgoing audio chunk: %d bytes", len(audio_bytes))

        for frame in self._split_frames(audio_bytes):
            if not self._authenticated:
                self._pre_auth_buffer.append(frame)
                continue
            await self._send_frame(frame)

    @staticmethod
    def _split_frames(audio_bytes: bytes):
        """Yield 960-byte frames, padding a trailing partial frame with silence."""
        offset = 0
        total = len(audio_bytes)
        while offset + VOICE_LIVE_FRAME_BYTES <= total:
            yield audio_bytes[offset:offset + VOICE_LIVE_FRAME_BYTES]
            offset += VOICE_LIVE_FRAME_BYTES
        if offset < total:
            remaining = audio_bytes[offset:]
            yield remaining + b"\x00" * (VOICE_LIVE_FRAME_BYTES - len(remaining))

    async def _send_frame(self, frame: bytes):
        try:
            await self.vonage_ws.send(frame)
        except Exception as e:
            logger.debug("Vonage audio send failed: %s", e)

    async def _flush_pre_auth_buffer(self):
        """Send any audio buffered before authentication completed."""
        if not self._pre_auth_buffer:
            return
        logger.info("[VonageMediaHandler] Flushing %d buffered frames", len(self._pre_auth_buffer))
        while self._pre_auth_buffer:
            await self._send_frame(self._pre_auth_buffer.popleft())

    # ------------------------------------------------------------------
    # Vonage message handling
    # ------------------------------------------------------------------

    async def on_message(self, message):
        """Process one incoming Vonage WebSocket message.

        Binary messages = raw PCM 24kHz audio from the caller.
        Text messages = JSON (websocket:connected, websocket:dtmf, websocket:cleared).
        """
        if isinstance(message, bytes):
            if not self._authenticated:
                return  # Drop caller audio until the handshake is validated

            self._in_frame_count += 1
            if self._in_frame_count == 1:
                logger.info("[VonageMediaHandler] First incoming audio frame: %d bytes", len(message))
            elif self._in_frame_count % 500 == 0:
                logger.info("[VonageMediaHandler] Incoming audio frames: %d", self._in_frame_count)

            if not self._voicelive_connected:
                return
            try:
                await self.handle_audio(message)
            except Exception:
                logger.exception("[VonageMediaHandler] Error processing audio frame %d", self._in_frame_count)
            return

        # Text messages are JSON
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[VonageMediaHandler] Non-JSON text message: %s", str(message)[:200])
            return

        event = data.get("event")
        if event == "websocket:connected":
            await self._handle_connected(data)
        elif event == "websocket:dtmf":
            logger.info("[VonageMediaHandler] DTMF received: %s", data.get("digit"))
        elif event == "websocket:cleared":
            logger.debug("[VonageMediaHandler] Playback buffer cleared")
        else:
            logger.info("[VonageMediaHandler] Unknown event: %s", data)

    async def _handle_connected(self, data: dict):
        """Parse websocket:connected, validate the token, confirm the audio format."""
        content_type = data.get("content-type", "")
        logger.info("[VonageMediaHandler] Connected: content-type=%s", content_type)

        # Validate the one-time token delivered on the WSS query string.
        if self._token_validator:
            if not self.url_token or not self._token_validator(self.url_token):
                logger.warning("[VonageMediaHandler] Invalid or missing WebSocket token — closing connection")
                await self.vonage_ws.close(1008)  # Policy Violation
                return
            logger.info("[VonageMediaHandler] WebSocket token validated")

        # Confirm the negotiated sample rate matches Voice Live (audio/l16;rate=24000).
        rate = VOICE_LIVE_SAMPLE_RATE
        if "rate=" in content_type:
            try:
                rate = int(content_type.split("rate=")[1].split(";")[0].strip())
            except (ValueError, IndexError):
                pass
        if rate != VOICE_LIVE_SAMPLE_RATE:
            logger.error(
                "[VonageMediaHandler] Unsupported sample rate: %dHz. "
                "Set the NCCO connect content-type to audio/l16;rate=24000.",
                rate,
            )
            await self.vonage_ws.close(1008)
            return

        self._authenticated = True
        logger.info("[VonageMediaHandler] Audio format confirmed: PCM 24kHz 16-bit mono")
        await self._flush_pre_auth_buffer()
