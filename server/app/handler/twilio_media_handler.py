"""Handles Twilio Media Stream WebSocket and bridges audio to Azure Voice Live API."""

import asyncio
import audioop
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid

from azure.identity.aio import ManagedIdentityCredential
from websockets.asyncio.client import connect as ws_connect

logger = logging.getLogger(__name__)

# Twilio sends mulaw 8000Hz; Voice Live expects PCM 24000Hz 16-bit mono.
TWILIO_SAMPLE_RATE = 8000
VOICELIVE_SAMPLE_RATE = 24000
_TOKEN_TTL = 60


def _session_config():
    """Returns the session configuration for Voice Live with semantic VAD."""
    return {
        "type": "session.update",
        "session": {
            "instructions": "You are a helpful AI assistant responding in natural, engaging language.",
            "turn_detection": {
                "type": "azure_semantic_vad",
            },
            "voice": {
                "name": "en-US-Aria:DragonHDLatestNeural",
                "type": "azure-standard",
                "temperature": 0.8,
            },
        },
    }


class TwilioMediaHandler:
    """Manages bidirectional audio streaming between Twilio and Azure Voice Live API."""

    def __init__(self, config):
        self.endpoint = config["AZURE_VOICE_LIVE_ENDPOINT"]
        self.model = config["VOICE_LIVE_MODEL"]
        self.api_key = config["AZURE_VOICE_LIVE_API_KEY"]
        self.client_id = config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"]
        self.auth_token = config.get("TWILIO_AUTH_TOKEN", "")
        self.ws = None
        self.twilio_ws = None
        self.stream_sid = None
        self._receiver_task = None
        self._ratecv_state_in = None
        self._ratecv_state_out = None

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

    async def connect_voicelive(self):
        """Connects to Azure Voice Live via raw WebSocket."""
        endpoint = self.endpoint.rstrip("/")
        model = self.model.strip()
        url = f"{endpoint}/voice-live/realtime?api-version=2026-01-01-preview&model={model}"
        url = url.replace("https://", "wss://")

        headers = {"x-ms-client-request-id": str(uuid.uuid4())}

        if self.client_id:
            async with ManagedIdentityCredential(client_id=self.client_id) as credential:
                token = await credential.get_token(
                    "https://cognitiveservices.azure.com/.default"
                )
                headers["Authorization"] = f"Bearer {token.token}"
        else:
            headers["api-key"] = self.api_key

        self.ws = await ws_connect(url, additional_headers=headers)
        logger.info("[TwilioMediaHandler] Connected to Voice Live API")

        await self._send_json(_session_config())
        await self._send_json({"type": "response.create"})

        # Start receiver loop
        self._receiver_task = asyncio.create_task(self._voicelive_receiver_loop())

    async def _send_json(self, obj):
        """Sends a JSON object to Voice Live WebSocket."""
        if self.ws:
            await self.ws.send(json.dumps(obj))

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
                    await self._twilio_audio_to_voicelive(payload)

            case "stop":
                logger.info("[TwilioMediaHandler] Stream stopped: sid=%s", self.stream_sid)
                await self._cleanup()

            case "dtmf":
                digit = data.get("dtmf", {}).get("digit")
                logger.info("[TwilioMediaHandler] DTMF received: %s", digit)

            case "mark":
                mark_name = data.get("mark", {}).get("name")
                logger.debug("[TwilioMediaHandler] Mark received: %s", mark_name)

            case _:
                logger.debug("[TwilioMediaHandler] Unknown event: %s", event)

    async def _twilio_audio_to_voicelive(self, mulaw_b64: str):
        """Converts Twilio mulaw/8000 audio to PCM/24000 and sends to Voice Live."""
        if not self.ws:
            return

        # Decode base64 mulaw
        mulaw_bytes = base64.b64decode(mulaw_b64)

        # Convert mulaw to PCM 16-bit
        pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)

        # Resample 8kHz -> 24kHz
        pcm_24k, self._ratecv_state_in = audioop.ratecv(
            pcm_8k, 2, 1, TWILIO_SAMPLE_RATE, VOICELIVE_SAMPLE_RATE, self._ratecv_state_in
        )

        # Send PCM bytes as base64 to Voice Live
        pcm_b64 = base64.b64encode(pcm_24k).decode("ascii")
        await self._send_json({"type": "input_audio_buffer.append", "audio": pcm_b64})

    async def _voicelive_audio_to_twilio(self, audio_b64: str):
        """Converts Voice Live PCM/24000 audio to mulaw/8000 and sends to Twilio."""
        if not self.twilio_ws or not self.stream_sid:
            return

        # Decode PCM audio from Voice Live
        pcm_24k = base64.b64decode(audio_b64)

        # Resample 24kHz -> 8kHz
        pcm_8k, self._ratecv_state_out = audioop.ratecv(
            pcm_24k, 2, 1, VOICELIVE_SAMPLE_RATE, TWILIO_SAMPLE_RATE, self._ratecv_state_out
        )

        # Convert PCM to mulaw
        mulaw_bytes = audioop.lin2ulaw(pcm_8k, 2)

        # Encode to base64 for Twilio
        mulaw_b64 = base64.b64encode(mulaw_bytes).decode("ascii")

        # Send media message back to Twilio
        msg = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": mulaw_b64},
        }
        await self.twilio_ws.send(json.dumps(msg))

    async def _send_clear_to_twilio(self):
        """Sends a clear message to Twilio to stop current audio playback."""
        if not self.twilio_ws or not self.stream_sid:
            return
        self._ratecv_state_out = None
        msg = {"event": "clear", "streamSid": self.stream_sid}
        await self.twilio_ws.send(json.dumps(msg))

    async def _voicelive_receiver_loop(self):
        """Handles incoming events from Voice Live and sends audio back to Twilio."""
        try:
            async for raw_msg in self.ws:
                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                match msg_type:
                    case "session.updated":
                        logger.info("[TwilioMediaHandler] Session updated")

                    case "input_audio_buffer.speech_started":
                        logger.info("[TwilioMediaHandler] Speech started")
                        await self._send_clear_to_twilio()

                    case "input_audio_buffer.speech_stopped":
                        logger.info("[TwilioMediaHandler] Speech stopped")

                    case "response.audio.delta":
                        delta = msg.get("delta", "")
                        if delta:
                            await self._voicelive_audio_to_twilio(delta)

                    case "response.done":
                        logger.info("[TwilioMediaHandler] Response done")

                    case "error":
                        logger.error("[TwilioMediaHandler] Voice Live error: %s", msg.get("error"))

                    case _:
                        logger.debug("[TwilioMediaHandler] Event: %s", msg_type)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[TwilioMediaHandler] Receiver loop error")

    async def _cleanup(self):
        """Cleans up resources."""
        if self._receiver_task:
            self._receiver_task.cancel()
        if self.ws:
            await self.ws.close()
            self.ws = None
        logger.info("[TwilioMediaHandler] Cleaned up")
