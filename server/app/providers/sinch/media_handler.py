"""Handles the Sinch connectStream WebSocket and bridges audio to Azure Voice Live.

Sinch connectStream protocol (closed beta):
- Sinch opens the WebSocket, then sends a text `connect` command. We authenticate
  it with the one-time token we issued in the SVAML: primarily via the WSS URL
  query string, with the connect frame's top-level `callHeaders` object as a
  fallback.
- We reply with a text `answer` command to accept the call, or `reject` to
  decline it. Audio then flows as binary WebSocket messages in both directions.
- Audio is raw PCM 16-bit mono at the sample rate negotiated in `streamingOptions`.
  We request 24000 Hz to match Voice Live, in which case frames pass through
  unchanged; any other rate is resampled to/from 24 kHz.
- Barge-in: we send a text `clear` command to flush Sinch's playback buffer.
- Keepalive: we send a text `heartbeat` command when the channel is idle.

Reference: https://developers.sinch.com/docs/voice/api-reference/svaml/streams
"""

import asyncio
import audioop
import json
import logging
import time

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler

logger = logging.getLogger(__name__)

# Voice Live uses PCM 24kHz 16-bit mono.
VOICELIVE_SAMPLE_RATE = 24000
# Send a heartbeat if no frame has been sent to Sinch for this many seconds.
_HEARTBEAT_IDLE_SECONDS = 15


class SinchMediaHandler(VoiceLiveMediaHandler):
    """Bridges the Sinch connectStream WebSocket to Azure Voice Live API.

    When the negotiated sample rate is 24000 Hz the audio passes through
    untouched; otherwise it is resampled to/from Voice Live's 24kHz.
    """

    def __init__(self, config, token_validator=None):
        super().__init__(config)
        self.sinch_ws = None
        self._token_validator = token_validator  # callable: validate_ws_token(token) -> bool
        self.url_token = ""  # token captured from the WSS query string by the route
        try:
            self.sample_rate = int(config.get("SINCH_SAMPLE_RATE", "24000"))
        except (TypeError, ValueError):
            self.sample_rate = VOICELIVE_SAMPLE_RATE
        self._answered = False
        self._answered_event = asyncio.Event()
        self._call_id = None
        self._in_frame_count = 0
        self._out_frame_count = 0
        self._ratecv_state_in = None
        self._ratecv_state_out = None
        self._last_send = time.monotonic()
        self._heartbeat_task = None

    # ------------------------------------------------------------------
    # Voice Live connection — defer until the call is answered
    # ------------------------------------------------------------------

    async def connect_voicelive(self):
        """Wait for the connect/answer handshake before starting Voice Live.

        This ensures the AI greeting is generated only after the call is
        answered, so no audio is produced before the media channel is live.
        """
        await self._answered_event.wait()
        await super().connect_voicelive()

    # ------------------------------------------------------------------
    # Voice Live hooks
    # ------------------------------------------------------------------

    async def on_speech_started(self):
        """Barge-in: tell Sinch to clear its playback buffer immediately."""
        self._ratecv_state_out = None
        await self._send_command("clear")

    async def on_transcript_done(self, transcript: str):
        """No-op — Sinch has no transcript channel."""
        pass

    # ------------------------------------------------------------------
    # Audio output to client — PCM 24kHz → Sinch (resample if negotiated rate differs)
    # ------------------------------------------------------------------

    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Send PCM audio from Voice Live to Sinch as binary frames."""
        if not self._answered or not self.sinch_ws:
            return

        if self.sample_rate != VOICELIVE_SAMPLE_RATE:
            audio_bytes, self._ratecv_state_out = audioop.ratecv(
                audio_bytes, 2, 1, VOICELIVE_SAMPLE_RATE, self.sample_rate, self._ratecv_state_out
            )

        self._out_frame_count += 1
        if self._out_frame_count == 1:
            logger.info("[SinchMediaHandler] First outgoing audio chunk: %d bytes", len(audio_bytes))
        elif self._out_frame_count % 100 == 0:
            logger.info("[SinchMediaHandler] Outgoing audio chunks sent: %d", self._out_frame_count)

        try:
            await self.sinch_ws.send(audio_bytes)
            self._last_send = time.monotonic()
        except Exception as e:
            logger.debug("[SinchMediaHandler] Audio send failed: %s", e)

    # ------------------------------------------------------------------
    # Inbound audio — Sinch → PCM 24kHz (resample if negotiated rate differs)
    # ------------------------------------------------------------------

    def _receive_audio_from_client(self, data) -> tuple:
        """Resample inbound Sinch PCM to Voice Live's 24 kHz when rates differ."""
        if self.sample_rate != VOICELIVE_SAMPLE_RATE:
            data, self._ratecv_state_in = audioop.ratecv(
                data, 2, 1, self.sample_rate, VOICELIVE_SAMPLE_RATE, self._ratecv_state_in
            )
        return data, len(data)

    # ------------------------------------------------------------------
    # Sinch message handling
    # ------------------------------------------------------------------

    async def on_message(self, message):
        """Process one incoming Sinch WebSocket message.

        Binary messages = raw PCM audio.
        Text messages = JSON command frames (connect, clear, heartbeat, ...).
        """
        if isinstance(message, bytes):
            if not self._answered or not self._voicelive_connected:
                return  # Drop audio until answered and Voice Live is ready
            self._in_frame_count += 1
            if self._in_frame_count == 1:
                logger.info("[SinchMediaHandler] First incoming audio frame: %d bytes", len(message))
            elif self._in_frame_count % 500 == 0:
                logger.info("[SinchMediaHandler] Incoming audio frames: %d", self._in_frame_count)
            try:
                await self.handle_audio(message)
            except Exception:
                logger.exception("[SinchMediaHandler] Error processing audio frame %d", self._in_frame_count)
            return

        # Text messages are JSON command frames
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[SinchMediaHandler] Non-JSON text message: %.200s", message)
            return

        command = (data.get("command") or "").lower()
        if command == "connect":
            await self._handle_connect(data)
        elif command == "heartbeat":
            logger.debug("[SinchMediaHandler] Heartbeat received")
        elif command == "clear":
            logger.debug("[SinchMediaHandler] Clear received")
        else:
            logger.info("[SinchMediaHandler] Unknown command: %s", command)

    async def _handle_connect(self, data: dict):
        """Handle the `connect` handshake: validate token, then answer or reject."""
        self._call_id = data.get("CallId") or data.get("callId")
        # The token is primarily delivered via the WSS query string. As a fallback,
        # the connect frame carries it under a top-level `callHeaders` dict
        # (this beta) or a `Headers` dict (per the older doc example).
        header_token = ""
        for container in (data.get("callHeaders"), data.get("Headers"), data.get("headers")):
            if isinstance(container, dict):
                for key, value in container.items():
                    if isinstance(key, str) and key.lower() == "token":
                        header_token = value or ""
                        break
            if header_token:
                break
        token = self.url_token or header_token
        logger.info(
            "[SinchMediaHandler] Connect received: callId=%s urlToken=%s callHeaderToken=%s",
            self._call_id,
            bool(self.url_token),
            bool(header_token),
        )

        if self._token_validator:
            if not token or not self._token_validator(token, self._call_id or ""):
                logger.warning("[SinchMediaHandler] Invalid or missing token — rejecting call")
                await self._send_command("reject")
                await self.sinch_ws.close(1008)  # Policy Violation
                return

        await self._send_command("answer")
        self._answered = True
        self._answered_event.set()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("[SinchMediaHandler] Call answered: callId=%s, sampleRate=%d", self._call_id, self.sample_rate)

    # ------------------------------------------------------------------
    # Command frames + keepalive
    # ------------------------------------------------------------------

    async def _send_command(self, command: str):
        """Send a text command frame to Sinch."""
        if not self.sinch_ws:
            return
        try:
            await self.sinch_ws.send(json.dumps({"command": command}))
        except Exception as e:
            logger.debug("[SinchMediaHandler] Failed to send '%s' command: %s", command, e)

    async def _heartbeat_loop(self):
        """Keep the channel open by sending a heartbeat when idle."""
        try:
            while True:
                await asyncio.sleep(5)
                if time.monotonic() - self._last_send >= _HEARTBEAT_IDLE_SECONDS:
                    await self._send_command("heartbeat")
                    self._last_send = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("[SinchMediaHandler] Heartbeat loop ended")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self):
        """Cancel the heartbeat task and tear down the Voice Live connection."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
            self._heartbeat_task = None
        # Unblock connect_voicelive if it is still waiting so cleanup can proceed.
        self._answered_event.set()
        await super().cleanup()
