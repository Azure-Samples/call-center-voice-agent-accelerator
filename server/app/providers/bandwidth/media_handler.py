"""Handles Bandwidth media stream WebSocket and bridges audio to Azure Voice Live API.

Bandwidth <StartStream> WebSocket protocol (JSON text frames):
- start:  {"eventType":"start","metadata":{...},"streamParams":{"token":"..."}}
- media:  {"eventType":"media","track":"inbound","payload":"<base64 PCMU>","sequenceNumber":"1"}
- stop:   {"eventType":"stop","metadata":{...}}

Inbound audio is PCMU/G711 8kHz (mu-law); Voice Live expects PCM 24kHz 16-bit mono.
Outbound audio is sent back as playAudio events using raw PCM 24kHz (audio/pcm;rate=24000),
which avoids re-encoding the Voice Live output.
"""

import asyncio
import audioop
import base64
import hashlib
import hmac
import json
import logging
import time

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler

logger = logging.getLogger(__name__)

# Bandwidth streams mu-law (PCMU) 8000Hz; Voice Live uses PCM 24000Hz 16-bit mono.
BANDWIDTH_SAMPLE_RATE = 8000
VOICELIVE_SAMPLE_RATE = 24000
_TOKEN_TTL = 60


class BandwidthMediaHandler(VoiceLiveMediaHandler):
    """Bridges a Bandwidth media stream WebSocket to Azure Voice Live API.

    Handles PCMU/PCM conversion, rate resampling, and the Bandwidth stream protocol.
    """

    def __init__(self, config):
        super().__init__(config)
        self.client_secret = config.get("BANDWIDTH_CLIENT_SECRET", "")
        self.bandwidth_ws = None
        self.correlation_id = None
        self.stream_id = None
        self.call_id = None
        self._inbound_track = "inbound"
        self._ratecv_state_in = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _verify_ws_token(self, token: str) -> bool:
        """Verify a WebSocket token is valid and not expired."""
        if not self.client_secret or not token:
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
            self.client_secret.encode(), timestamp_str.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)

    async def authenticate_and_start(self) -> bool:
        """Wait for the Bandwidth 'start' message and validate the embedded token.

        Returns True if authenticated, False if rejected (WebSocket already closed).
        """
        while True:
            try:
                msg = await asyncio.wait_for(self.bandwidth_ws.receive(), timeout=30)
            except TimeoutError:
                logger.warning("[BandwidthMediaHandler] Timed out waiting for start message")
                await self.bandwidth_ws.close(4408, "Timeout")
                return False
            except Exception:
                logger.info("[BandwidthMediaHandler] WebSocket closed before start message")
                return False

            if isinstance(msg, bytes):
                logger.warning("[BandwidthMediaHandler] Unexpected binary frame before start")
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                logger.warning("[BandwidthMediaHandler] Non-JSON message before start")
                await self.bandwidth_ws.close(4400, "Bad Request")
                return False

            event = data.get("eventType")
            if event == "start":
                stream_params = data.get("streamParams", {}) or {}
                token = stream_params.get("token", "")
                if not self._verify_ws_token(token):
                    logger.warning("[BandwidthMediaHandler] Invalid or expired stream token")
                    await self.bandwidth_ws.close(4403, "Forbidden")
                    return False
                self._process_start(data)
                return True

            # Unexpected message before start
            logger.warning("[BandwidthMediaHandler] Unexpected message before start: %s", event)
            await self.bandwidth_ws.close(4400, "Bad Request")
            return False

    def _process_start(self, data: dict):
        """Record stream metadata from the 'start' message."""
        metadata = data.get("metadata", {}) or {}
        self.stream_id = metadata.get("streamId")
        self.call_id = metadata.get("callId")
        tracks = metadata.get("tracks", []) or []
        if tracks:
            self._inbound_track = tracks[0].get("name", "inbound")
        logger.info(
            "[BandwidthMediaHandler] Stream started: streamId=%s, callId=%s, track=%s",
            self.stream_id,
            self.call_id,
            self._inbound_track,
        )

    # ------------------------------------------------------------------
    # Voice Live hooks
    # ------------------------------------------------------------------

    async def on_speech_started(self):
        """Barge-in: clear Bandwidth playback buffer and local TTS buffer."""
        await self._send_clear_to_bandwidth()
        if self._ambient_mixer is not None:
            async with self._tts_buffer_lock:
                self._tts_output_buffer.clear()
                self._tts_playback_started = False

    async def on_transcript_done(self, transcript: str):
        """No-op — Bandwidth has no transcript channel."""
        pass

    # ------------------------------------------------------------------
    # Audio output to client — PCM 24kHz → playAudio event
    # ------------------------------------------------------------------

    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Send PCM 24kHz audio to Bandwidth as a playAudio event.

        Bandwidth accepts audio/pcm;rate=24000 (mono, 16-bit, little-endian, signed)
        and resamples it downstream, so no local re-encoding is needed.
        """
        if not self.bandwidth_ws:
            return

        payload_b64 = base64.b64encode(audio_bytes).decode("ascii")
        msg = {
            "eventType": "playAudio",
            "media": {
                "contentType": "audio/pcm;rate=24000;channels=1;bit-depth=16;endian=little;encoding=signed",
                "payload": payload_b64,
            },
        }
        try:
            await self.bandwidth_ws.send(json.dumps(msg))
        except Exception as e:
            logger.debug("[BandwidthMediaHandler] Audio send failed: %s", e)

    # ------------------------------------------------------------------
    # Bandwidth message handling
    # ------------------------------------------------------------------

    async def on_message(self, message):
        """Process one incoming Bandwidth WebSocket message."""
        if isinstance(message, bytes):
            logger.debug("[BandwidthMediaHandler] Ignoring unexpected binary frame")
            return

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("[BandwidthMediaHandler] Non-JSON message received")
            return

        event = data.get("eventType")

        match event:
            case "start":
                # Already handled in authenticate_and_start, but tolerate a repeat.
                self._process_start(data)

            case "media":
                track = data.get("track", self._inbound_track)
                if track != self._inbound_track:
                    return  # Only forward the caller's (inbound) audio
                payload = data.get("payload", "")
                if payload and self._voicelive_connected:
                    pcmu_bytes = base64.b64decode(payload)
                    await self.handle_audio(pcmu_bytes)

            case "stop":
                logger.info("[BandwidthMediaHandler] Stream stopped: streamId=%s", self.stream_id)

            case _:
                logger.debug("[BandwidthMediaHandler] Unknown event: %s", event)

    # ------------------------------------------------------------------
    # Inbound audio — PCMU 8kHz → PCM 24kHz
    # ------------------------------------------------------------------

    def _receive_audio_from_client(self, data) -> tuple:
        """Convert Bandwidth mu-law/8kHz bytes to PCM 24kHz."""
        pcm_8k = audioop.ulaw2lin(data, 2)
        pcm_24k, self._ratecv_state_in = audioop.ratecv(
            pcm_8k, 2, 1, BANDWIDTH_SAMPLE_RATE, VOICELIVE_SAMPLE_RATE, self._ratecv_state_in
        )
        return pcm_24k, len(pcm_24k)

    async def _send_clear_to_bandwidth(self):
        """Send a clear event to Bandwidth to discard buffered playback audio."""
        if not self.bandwidth_ws:
            return
        try:
            await self.bandwidth_ws.send(json.dumps({"eventType": "clear"}))
        except Exception as e:
            logger.debug("[BandwidthMediaHandler] Clear send failed: %s", e)
