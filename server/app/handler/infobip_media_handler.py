"""Handles Infobip WEBSOCKET endpoint and bridges audio to Azure Voice Live API.

Infobip WEBSOCKET_ENDPOINT protocol:
- First text message: {"event": "websocket:connected", "content-type": "audio/l16;rate=24000"}
- Binary messages: raw PCM 16-bit audio at 24kHz, 20ms frames (960 bytes)
- Text messages: DTMF events {"event": "websocket:dtmf", "digit": "3", "duration": 250}
"""

import json
import logging

from .voicelive_media_handler import VoiceLiveMediaHandler

logger = logging.getLogger(__name__)

# Voice Live uses PCM 24kHz 16-bit mono (960 bytes per 20ms frame).
VOICE_LIVE_SAMPLE_RATE = 24000
VOICE_LIVE_FRAME_BYTES = 960  # 480 samples * 2 bytes = 20ms at 24kHz


class InfobipMediaHandler(VoiceLiveMediaHandler):
    """Bridges Infobip WEBSOCKET endpoint to Azure Voice Live API.

    Requires the Infobip media stream config to use audio/l16;rate=24000.
    Audio passes through directly — no format conversion needed.
    """

    def __init__(self, config, token_validator=None):
        super().__init__(config)
        self.infobip_ws = None
        self.call_id = None
        self._connected = False
        self._authenticated = False
        self._token_validator = token_validator  # callable: validate_ws_token(token) -> bool
        self._out_frame_count = 0
        self._in_frame_count = 0

    # ------------------------------------------------------------------
    # Voice Live hooks
    # ------------------------------------------------------------------

    async def on_speech_started(self):
        """Barge-in: clear TTS buffer."""
        if self._ambient_mixer is not None:
            async with self._tts_buffer_lock:
                self._tts_output_buffer.clear()
                self._tts_playback_started = False

    async def on_transcript_done(self, transcript: str):
        """Log only — Infobip has no transcript channel."""
        pass

    # ------------------------------------------------------------------
    # Audio output to client — send as raw binary frames
    # ------------------------------------------------------------------

    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Send PCM audio from Voice Live (24kHz) to Infobip WebSocket.

        Splits into 960-byte frames (20ms at 24kHz).
        """
        if not self.infobip_ws:
            logger.warning("[InfobipMediaHandler] infobip_ws is None, cannot send audio")
            return

        try:
            self._out_frame_count += 1
            if self._out_frame_count == 1:
                logger.info("[InfobipMediaHandler] First outgoing audio chunk: %d bytes", len(audio_bytes))
            elif self._out_frame_count % 100 == 0:
                logger.info("[InfobipMediaHandler] Outgoing audio chunks sent: %d", self._out_frame_count)

            offset = 0
            while offset + VOICE_LIVE_FRAME_BYTES <= len(audio_bytes):
                frame = audio_bytes[offset:offset + VOICE_LIVE_FRAME_BYTES]
                await self.infobip_ws.send(frame)
                offset += VOICE_LIVE_FRAME_BYTES

            # Pad partial frame with silence
            if offset < len(audio_bytes):
                remaining = audio_bytes[offset:]
                padded = remaining + b'\x00' * (VOICE_LIVE_FRAME_BYTES - len(remaining))
                await self.infobip_ws.send(padded)
        except Exception:
            logger.exception("[InfobipMediaHandler] Failed to send audio to Infobip")

    # ------------------------------------------------------------------
    # Infobip message handling
    # ------------------------------------------------------------------

    async def handle_infobip_message(self, message):
        """Processes an incoming Infobip WebSocket message.

        Binary messages = raw PCM 24kHz audio.
        Text messages = JSON (websocket:connected, websocket:dtmf).
        """
        if isinstance(message, bytes):
            if not self._authenticated:
                return  # Drop audio until token is validated

            self._in_frame_count += 1
            if self._in_frame_count == 1:
                logger.info("[InfobipMediaHandler] First incoming audio frame: %d bytes", len(message))
            elif self._in_frame_count % 500 == 0:
                logger.info("[InfobipMediaHandler] Incoming audio frames: %d", self._in_frame_count)

            await self.handle_audio(message)
            return

        # Text messages are JSON
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("[InfobipMediaHandler] Non-JSON text message: %s", message[:200])
            return

        event = data.get("event")
        if event == "websocket:connected":
            await self._handle_connected(data)
        elif event == "websocket:dtmf":
            digit = data.get("digit")
            logger.info("[InfobipMediaHandler] DTMF received: %s", digit)
        else:
            logger.info("[InfobipMediaHandler] Unknown event: %s", data)

    async def _handle_connected(self, data: dict):
        """Parse the websocket:connected event and validate authentication token."""
        content_type = data.get("content-type", "")
        logger.info("[InfobipMediaHandler] Connected: content-type=%s", content_type)

        # Validate WebSocket token — customData fields are flattened into the top-level message
        ws_token = data.get("ws_token", "")
        if self._token_validator:
            if not ws_token or not self._token_validator(ws_token):
                logger.warning("[InfobipMediaHandler] Invalid or missing WebSocket token — closing connection")
                await self.infobip_ws.close(1008)  # Policy Violation
                return
            logger.info("[InfobipMediaHandler] WebSocket token validated successfully")

        self._connected = True
        self._authenticated = True

        # Verify sample rate from content-type: "audio/l16;rate=24000"
        rate = VOICE_LIVE_SAMPLE_RATE  # default if not specified
        if "rate=" in content_type:
            try:
                rate = int(content_type.split("rate=")[1].split(";")[0].strip())
            except (ValueError, IndexError):
                pass

        if rate != VOICE_LIVE_SAMPLE_RATE:
            logger.error(
                "[InfobipMediaHandler] Unsupported sample rate: %dHz. "
                "Configure the Infobip media stream with audio/l16;rate=24000.",
                rate,
            )
            await self.infobip_ws.close(1008)
            return

        logger.info("[InfobipMediaHandler] Audio format confirmed: PCM 24kHz 16-bit mono")

    # ------------------------------------------------------------------
    # Inbound audio — direct passthrough to Voice Live
    # ------------------------------------------------------------------

    def _receive_audio_from_client(self, data) -> tuple:
        """No conversion needed — Infobip sends PCM 24kHz 16-bit mono directly."""
        return data, len(data)
