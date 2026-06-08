"""Handles Twilio Media Stream WebSocket and bridges audio to Azure Voice Live API."""

import audioop
import base64
import hashlib
import hmac
import json
import logging
import time

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler

logger = logging.getLogger(__name__)

# Twilio sends mulaw 8000Hz; Voice Live expects PCM 24000Hz 16-bit mono.
TWILIO_SAMPLE_RATE = 8000
VOICELIVE_SAMPLE_RATE = 24000
_TOKEN_TTL = 60


class TwilioMediaHandler(VoiceLiveMediaHandler):
    """Bridges Twilio Media Stream WebSocket to Azure Voice Live API.

    Handles mulaw/PCM conversion, rate resampling, and Twilio protocol.
    """

    def __init__(self, config):
        super().__init__(config)
        self.auth_token = config.get("TWILIO_AUTH_TOKEN", "")
        self.twilio_ws = None
        self.stream_sid = None
        self._ratecv_state_in = None
        self._ratecv_state_out = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _verify_ws_token(self, token: str) -> bool:
        """Verify a WebSocket token is valid and not expired."""
        if not self.auth_token or not token:
            return False
        parts = token.split(".", 1)
        if len(parts) != 2:
            return False
        timestamp_str, sig = parts
        try:
            timestamp = int(timestamp_str)
        except ValueError:
            return False
        if time.time() - timestamp > _TOKEN_TTL:
            return False
        expected = hmac.new(
            self.auth_token.encode(), timestamp_str.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)

    async def authenticate_and_start(self) -> bool:
        """Wait for the Twilio 'start' message and validate the embedded token.

        Returns True if authenticated, False if rejected (WebSocket already closed).
        """
        while True:
            msg = await self.twilio_ws.receive()
            data = json.loads(msg)
            event = data.get("event")

            if event == "connected":
                logger.info("[TwilioMediaHandler] Twilio connected: protocol=%s", data.get("protocol"))
                continue

            if event == "start":
                custom_params = data.get("start", {}).get("customParameters", {})
                token = custom_params.get("token", "")
                if not self._verify_ws_token(token):
                    logger.warning("[TwilioMediaHandler] Invalid or expired stream token")
                    await self.twilio_ws.close(4403, "Forbidden")
                    return False
                # Process the start message
                await self.handle_twilio_message(msg)
                return True

            # Unexpected message before start
            logger.warning("[TwilioMediaHandler] Unexpected message before start: %s", event)
            await self.twilio_ws.close(4400, "Bad Request")
            return False

    # ------------------------------------------------------------------
    # Voice Live hooks
    # ------------------------------------------------------------------

    async def on_speech_started(self):
        """Barge-in: clear Twilio playback and TTS buffer."""
        await self._send_clear_to_twilio()
        if self._ambient_mixer is not None:
            async with self._tts_buffer_lock:
                self._tts_output_buffer.clear()
                self._tts_playback_started = False

    async def on_transcript_done(self, transcript: str):
        """Log only — Twilio has no transcript channel."""
        pass

    # ------------------------------------------------------------------
    # Audio output to client — PCM 24kHz → mulaw 8kHz → Twilio
    # ------------------------------------------------------------------

    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Convert PCM 24kHz to mulaw 8kHz and send to Twilio."""
        if not self.twilio_ws or not self.stream_sid:
            return

        pcm_8k, self._ratecv_state_out = audioop.ratecv(
            audio_bytes, 2, 1, VOICELIVE_SAMPLE_RATE, TWILIO_SAMPLE_RATE, self._ratecv_state_out
        )

        mulaw_bytes = audioop.lin2ulaw(pcm_8k, 2)
        mulaw_b64 = base64.b64encode(mulaw_bytes).decode("ascii")

        msg = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": mulaw_b64},
        }
        await self.twilio_ws.send(json.dumps(msg))

    # ------------------------------------------------------------------
    # Twilio message handling
    # ------------------------------------------------------------------

    async def handle_twilio_message(self, message: str):
        """Processes an incoming Twilio WebSocket message."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("[TwilioMediaHandler] Non-JSON message received")
            return

        event = data.get("event")

        match event:
            case "connected":
                logger.info("[TwilioMediaHandler] Twilio connected: protocol=%s", data.get("protocol"))

            case "start":
                self.stream_sid = data.get("streamSid")
                start_info = data.get("start", {})
                logger.info(
                    "[TwilioMediaHandler] Stream started: sid=%s, call=%s, format=%s",
                    self.stream_sid,
                    start_info.get("callSid"),
                    start_info.get("mediaFormat"),
                )

            case "media":
                media = data.get("media", {})
                payload = media.get("payload", "")
                if payload:
                    mulaw_bytes = base64.b64decode(payload)
                    await self.handle_audio(mulaw_bytes)

            case "stop":
                logger.info("[TwilioMediaHandler] Stream stopped: sid=%s", self.stream_sid)

            case "dtmf":
                digit = data.get("dtmf", {}).get("digit")
                logger.info("[TwilioMediaHandler] DTMF received: %s", digit)

            case "mark":
                mark_name = data.get("mark", {}).get("name")
                logger.debug("[TwilioMediaHandler] Mark received: %s", mark_name)

            case _:
                logger.debug("[TwilioMediaHandler] Unknown event: %s", event)

    # ------------------------------------------------------------------
    # Inbound audio — mulaw 8kHz → PCM 24kHz
    # ------------------------------------------------------------------

    def _receive_audio_from_client(self, data) -> tuple:
        """Convert Twilio mulaw/8kHz bytes to PCM 24kHz."""
        pcm_8k = audioop.ulaw2lin(data, 2)
        pcm_24k, self._ratecv_state_in = audioop.ratecv(
            pcm_8k, 2, 1, TWILIO_SAMPLE_RATE, VOICELIVE_SAMPLE_RATE, self._ratecv_state_in
        )
        return pcm_24k, len(pcm_24k)

    async def _send_clear_to_twilio(self):
        """Sends a clear message to Twilio to stop current audio playback."""
        if not self.twilio_ws or not self.stream_sid:
            return
        self._ratecv_state_out = None
        msg = {"event": "clear", "streamSid": self.stream_sid}
        await self.twilio_ws.send(json.dumps(msg))
