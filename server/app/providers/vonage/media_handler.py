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

import asyncio
import json
import logging
import time

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler

logger = logging.getLogger(__name__)

# Voice Live uses PCM 24kHz 16-bit mono (960 bytes per 20ms frame).
VOICE_LIVE_SAMPLE_RATE = 24000
VOICE_LIVE_FRAME_BYTES = 960  # 480 samples * 2 bytes = 20ms at 24kHz

# Match Vonage's real-time playback cadence.
_FRAME_INTERVAL_SECONDS = 0.02

# Limit queued audio to approximately five seconds.
_MAX_QUEUED_FRAMES = 250


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
        # Queue Voice Live bursts for real-time delivery to Vonage.
        self._out_queue: asyncio.Queue = asyncio.Queue()
        self._sender_task = None
        # Carry incomplete frames across Voice Live deltas.
        self._out_partial = bytearray()

    # ------------------------------------------------------------------
    # Voice Live hooks
    # ------------------------------------------------------------------

    async def on_speech_started(self):
        """Barge-in: drop queued audio and flush Vonage's playback buffer."""
        self._drain_queue()
        self._out_partial.clear()
        if self._authenticated and self.vonage_ws is not None:
            try:
                await self.vonage_ws.send(json.dumps({"action": "clear"}))
            except Exception as e:
                logger.debug("Vonage clear (barge-in) send failed: %s", e)

    async def on_transcript_done(self, transcript: str):
        """No-op — Vonage has no transcript channel."""
        pass

    async def on_response_done(self):
        """Response finished: pad and queue any carried-over remainder."""
        if self._out_partial:
            remaining = bytes(self._out_partial)
            self._out_partial.clear()
            self._enqueue_frame(remaining + b"\x00" * (VOICE_LIVE_FRAME_BYTES - len(remaining)))

    # ------------------------------------------------------------------
    # Audio output to client
    # ------------------------------------------------------------------

    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Queue complete 20ms PCM frames for paced delivery."""
        self._out_frame_count += 1
        if self._out_frame_count == 1:
            logger.info("[VonageMediaHandler] First outgoing audio chunk: %d bytes", len(audio_bytes))

        for frame in self._split_frames(audio_bytes):
            self._enqueue_frame(frame)

    def _split_frames(self, audio_bytes: bytes):
        """Yield whole frames and retain any remainder for the next delta."""
        if self._out_partial:
            audio_bytes = bytes(self._out_partial) + audio_bytes
            self._out_partial.clear()
        offset = 0
        total = len(audio_bytes)
        while offset + VOICE_LIVE_FRAME_BYTES <= total:
            yield audio_bytes[offset:offset + VOICE_LIVE_FRAME_BYTES]
            offset += VOICE_LIVE_FRAME_BYTES
        if offset < total:
            self._out_partial.extend(audio_bytes[offset:])

    def _enqueue_frame(self, frame: bytes):
        """Queue one outgoing frame, dropping the oldest if the cap is exceeded."""
        if self._out_queue.qsize() >= _MAX_QUEUED_FRAMES:
            try:
                self._out_queue.get_nowait()
                self._out_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        self._out_queue.put_nowait(frame)

    def _drain_queue(self):
        """Discard all pending outgoing frames (used on barge-in)."""
        dropped = 0
        try:
            while True:
                self._out_queue.get_nowait()
                self._out_queue.task_done()
                dropped += 1
        except asyncio.QueueEmpty:
            pass
        if dropped:
            logger.debug("[VonageMediaHandler] Dropped %d queued frames on barge-in", dropped)

    def _start_sender(self):
        """Launch the single paced sender task once (idempotent)."""
        if self._sender_task is None or self._sender_task.done():
            self._sender_task = asyncio.create_task(self._paced_sender())

    async def _paced_sender(self):
        """Send queued frames at Vonage's real-time cadence."""
        next_deadline = time.monotonic()
        while True:
            frame = await self._out_queue.get()
            try:
                await self._send_frame(frame)
            finally:
                self._out_queue.task_done()
            next_deadline += _FRAME_INTERVAL_SECONDS
            delay = next_deadline - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_deadline = time.monotonic()

    async def _send_frame(self, frame: bytes):
        try:
            await self.vonage_ws.send(frame)
        except Exception as e:
            logger.debug("Vonage audio send failed: %s", e)

    async def cleanup(self):
        """Stop the paced sender, then run base Voice Live cleanup."""
        self._out_partial.clear()
        if self._sender_task is not None:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sender_task = None
        await super().cleanup()

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
        self._start_sender()
