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
import hmac
import json
import logging
import uuid

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler

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
        self._session_id = None
        self._client_seq = 0  # last seq received from client
        self._server_seq = 0  # our outgoing seq counter
        self._authenticated = False
        self._session_open = False
        self._paused = False
        self._ratecv_state_in = None
        self._ratecv_state_out = None
        self._in_frame_count = 0
        self._out_frame_count = 0
        # Paced output buffer for barge-in support
        self._out_buffer = collections.deque()
        self._pcmu_remainder = b''  # Carry partial frames to next chunk

    # ------------------------------------------------------------------
    # Authentication (WebSocket upgrade headers)
    # ------------------------------------------------------------------

    def validate_api_key(self, provided_key: str) -> bool:
        """Validate the X-API-KEY header from the WebSocket upgrade request."""
        if not self.api_key or not provided_key:
            return False
        return hmac.compare_digest(provided_key, self.api_key)

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

        # Text messages are JSON protocol messages
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("[GenesysHandler] Non-JSON text message: %s", message[:200])
            return

        msg_type = data.get("type")
        self._client_seq = data.get("seq", self._client_seq)
        self._session_id = data.get("id", self._session_id)

        match msg_type:
            case "open":
                await self._handle_open(data)
            case "ping":
                await self._handle_ping(data)
            case "close":
                await self._handle_close(data)
            case "update":
                await self._handle_update(data)
            case "pause":
                await self._handle_pause(data)
            case "resume":
                await self._handle_resume(data)
            case _:
                logger.info("[GenesysHandler] Unknown message type: %s", msg_type)

    async def _handle_open(self, data: dict):
        """Handle the 'open' message — negotiate media and respond with 'opened'."""
        params = data.get("parameters", {})
        media_offers = params.get("media", [])
        participant = params.get("participant", {})
        conversation_id = params.get("conversationId", "")

        logger.info(
            "[GenesysHandler] Open: session=%s conversation=%s participant=%s",
            self._session_id, conversation_id,
            participant.get("ani", "unknown"),
        )

        # Select media format: prefer PCMU external-only channel
        selected_media = None
        for offer in media_offers:
            if offer.get("format") == "PCMU" and offer.get("channels") == ["external"]:
                selected_media = offer
                break
        # Fall back to first PCMU offer
        if not selected_media:
            for offer in media_offers:
                if offer.get("format") == "PCMU":
                    selected_media = offer
                    break

        if not selected_media:
            logger.error("[GenesysHandler] No compatible media format offered")
            await self._send_disconnect("error", "No compatible media format")
            return

        # Respond with 'opened'
        self._server_seq += 1
        opened_msg = {
            "version": "2",
            "type": "opened",
            "seq": self._server_seq,
            "clientseq": self._client_seq,
            "id": self._session_id,
            "parameters": {
                "startPaused": False,
                "media": [selected_media],
            },
        }
        await self.genesys_ws.send(json.dumps(opened_msg))
        self._session_open = True
        self._authenticated = True
        logger.info(
            "[GenesysHandler] Session opened: format=%s rate=%s channels=%s",
            selected_media.get("format"),
            selected_media.get("rate"),
            selected_media.get("channels"),
        )

    async def _handle_ping(self, data: dict):
        """Respond to ping with pong immediately."""
        self._server_seq += 1
        pong_msg = {
            "version": "2",
            "type": "pong",
            "seq": self._server_seq,
            "clientseq": self._client_seq,
            "id": self._session_id,
        }
        await self.genesys_ws.send(json.dumps(pong_msg))

    async def _handle_close(self, data: dict):
        """Handle session close — respond with 'closed'."""
        logger.info("[GenesysHandler] Close received for session %s", self._session_id)
        self._session_open = False
        self._server_seq += 1
        closed_msg = {
            "version": "2",
            "type": "closed",
            "seq": self._server_seq,
            "clientseq": self._client_seq,
            "id": self._session_id,
            "parameters": {},
        }
        await self.genesys_ws.send(json.dumps(closed_msg))

    async def _handle_update(self, data: dict):
        """Handle update message — acknowledge with 'updated'."""
        logger.info("[GenesysHandler] Update received: %s", data.get("parameters", {}))
        self._server_seq += 1
        updated_msg = {
            "version": "2",
            "type": "updated",
            "seq": self._server_seq,
            "clientseq": self._client_seq,
            "id": self._session_id,
            "parameters": {},
        }
        await self.genesys_ws.send(json.dumps(updated_msg))

    async def _handle_pause(self, data: dict):
        """Handle pause — stop processing audio."""
        logger.info("[GenesysHandler] Paused")
        self._paused = True
        # Respond with 'paused'
        self._server_seq += 1
        paused_msg = {
            "version": "2",
            "type": "paused",
            "seq": self._server_seq,
            "clientseq": self._client_seq,
            "id": self._session_id,
        }
        await self.genesys_ws.send(json.dumps(paused_msg))

    async def _handle_resume(self, data: dict):
        """Handle resume — start processing audio again."""
        logger.info("[GenesysHandler] Resumed")
        self._paused = False
        # Respond with 'resumed'
        self._server_seq += 1
        resumed_msg = {
            "version": "2",
            "type": "resumed",
            "seq": self._server_seq,
            "clientseq": self._client_seq,
            "id": self._session_id,
        }
        await self.genesys_ws.send(json.dumps(resumed_msg))

    async def _send_disconnect(self, reason: str, message: str = ""):
        """Send a disconnect message to terminate the session."""
        self._server_seq += 1
        disconnect_msg = {
            "version": "2",
            "type": "disconnect",
            "seq": self._server_seq,
            "clientseq": self._client_seq,
            "id": self._session_id or str(uuid.uuid4()),
            "parameters": {
                "reason": reason,
                "info": message,
            },
        }
        await self.genesys_ws.send(json.dumps(disconnect_msg))

    # ------------------------------------------------------------------
    # Audio handling
    # ------------------------------------------------------------------

    async def _handle_audio_frame(self, frame: bytes):
        """Process incoming PCMU 8kHz audio from Genesys."""
        if not self._session_open or self._paused:
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

        # Convert PCMU 8kHz → PCM 16-bit 24kHz for Voice Live
        if not self._voicelive_connected:
            return

        try:
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
        """Convert PCM 24kHz from Voice Live → PCMU 8kHz, buffer for paced delivery."""
        self._out_frame_count += 1
        if self._out_frame_count == 1:
            logger.info("[GenesysHandler] First outgoing audio: %d bytes", len(audio_bytes))

        try:
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
