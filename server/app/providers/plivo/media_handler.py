"""Bridges the Plivo Audio Streaming WebSocket to the Azure Voice Live API."""

import asyncio
import audioop
import base64
import binascii
import json
import logging

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler

logger = logging.getLogger(__name__)

# Plivo streams PCM 8kHz; Voice Live expects PCM 24kHz. Resample both ways.
PLIVO_SAMPLE_RATE = 8000
VOICELIVE_SAMPLE_RATE = 24000
PLAY_CONTENT_TYPE = "audio/x-l16"

# Tear down after this many consecutive send failures (~2s of audio).
MAX_CONSECUTIVE_SEND_FAILURES = 20


class PlivoMediaHandler(VoiceLiveMediaHandler):
    """Bridges Plivo Audio Streaming WebSocket to Azure Voice Live API.

    Handles PCM 8kHz <-> 24kHz resampling and the Plivo streaming protocol.
    """

    def __init__(self, config):
        super().__init__(config)
        self.plivo_ws = None
        self.stream_id = None
        self.call_id = None
        self._ratecv_state_in = None
        self._ratecv_state_out = None
        self._consecutive_send_failures = 0

    async def authenticate_and_start(self) -> bool:
        """Read the first frame; require a well-formed 'start', else close. Returns True if started."""
        try:
            msg = await asyncio.wait_for(self.plivo_ws.receive(), timeout=30)
        except TimeoutError:
            logger.warning("[PlivoMediaHandler] Timed out waiting for start message")
            await self.plivo_ws.close(4408, "Timeout")
            return False
        except Exception:
            logger.warning(
                "[PlivoMediaHandler] Error receiving start message", exc_info=True
            )
            return False

        try:
            data = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[PlivoMediaHandler] Non-JSON message before start")
            await self.plivo_ws.close(4400, "Bad Request")
            return False

        if data.get("event") != "start":
            logger.warning(
                "[PlivoMediaHandler] Expected 'start' as first frame, got: %s",
                data.get("event"),
            )
            await self.plivo_ws.close(4400, "Bad Request")
            return False

        self._capture_start(data)
        if not self.stream_id:
            logger.warning("[PlivoMediaHandler] start event missing streamId")
            await self.plivo_ws.close(4400, "Bad Request")
            return False
        return True

    def _capture_start(self, data: dict):
        """Record streamId/callId from a Plivo 'start' event."""
        start_info = data.get("start", {})
        self.stream_id = start_info.get("streamId")
        self.call_id = start_info.get("callId")
        logger.info(
            "[PlivoMediaHandler] Stream started: streamId=%s, callId=%s, format=%s",
            self.stream_id,
            self.call_id,
            start_info.get("mediaFormat"),
        )

    async def on_speech_started(self):
        """Barge-in: clear Plivo playback and drop the TTS buffer."""
        await self._send_clear_to_plivo()
        if self._ambient_mixer is not None:
            async with self._tts_buffer_lock:
                self._tts_output_buffer.clear()
                self._tts_playback_started = False

    async def on_transcript_done(self, transcript: str):
        """No-op — Plivo has no transcript channel."""
        pass

    # Outbound: PCM 24kHz -> 8kHz -> Plivo playAudio
    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Resample PCM 24kHz to 8kHz and send to Plivo as a playAudio event."""
        if not self.plivo_ws or not self.stream_id:
            logger.debug(
                "[PlivoMediaHandler] Dropping outbound audio; stream not ready/closed"
            )
            return

        pcm_8k, self._ratecv_state_out = audioop.ratecv(
            audio_bytes,
            2,
            1,
            VOICELIVE_SAMPLE_RATE,
            PLIVO_SAMPLE_RATE,
            self._ratecv_state_out,
        )
        payload = base64.b64encode(pcm_8k).decode("ascii")

        msg = {
            "event": "playAudio",
            "media": {
                "contentType": PLAY_CONTENT_TYPE,
                "sampleRate": PLIVO_SAMPLE_RATE,
                "payload": payload,
            },
        }
        try:
            await self.plivo_ws.send(json.dumps(msg))
            self._consecutive_send_failures = 0
        except Exception as e:
            self._consecutive_send_failures += 1
            logger.warning(
                "[PlivoMediaHandler] Audio send failed (%d consecutive): %s",
                self._consecutive_send_failures,
                e,
            )
            if self._consecutive_send_failures >= MAX_CONSECUTIVE_SEND_FAILURES:
                logger.warning(
                    "[PlivoMediaHandler] Too many consecutive send failures — "
                    "closing WebSocket: streamId=%s",
                    self.stream_id,
                )
                try:
                    await self.plivo_ws.close(1011)  # Internal Error
                except Exception:
                    pass

    async def _send_clear_to_plivo(self):
        """Send a clearAudio event to stop current playback (barge-in)."""
        if not self.plivo_ws or not self.stream_id:
            return
        self._ratecv_state_out = None
        msg = {"event": "clearAudio", "streamId": self.stream_id}
        try:
            await self.plivo_ws.send(json.dumps(msg))
        except Exception as e:
            logger.warning("[PlivoMediaHandler] clearAudio send failed: %s", e)

    # Inbound: Plivo PCM 8kHz -> 24kHz
    def _receive_audio_from_client(self, data) -> tuple:
        """Resample Plivo PCM/8kHz bytes to PCM 24kHz."""
        pcm_24k, self._ratecv_state_in = audioop.ratecv(
            data, 2, 1, PLIVO_SAMPLE_RATE, VOICELIVE_SAMPLE_RATE, self._ratecv_state_in
        )
        return pcm_24k, len(pcm_24k)

    async def on_message(self, message):
        """Process one incoming Plivo WebSocket message."""
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[PlivoMediaHandler] Non-JSON message received")
            return

        event = data.get("event")

        match event:
            case "start":
                # Duplicate start mid-stream — ignore so it can't clobber the established ids.
                logger.warning(
                    "[PlivoMediaHandler] Unexpected duplicate 'start' after stream "
                    "established; ignoring (streamId=%s)",
                    self.stream_id,
                )

            case "media":
                payload = data.get("media", {}).get("payload", "")
                if payload:
                    try:
                        pcm_bytes = base64.b64decode(payload)
                        await self.handle_audio(pcm_bytes)
                    except (binascii.Error, audioop.error, ValueError) as e:
                        logger.warning(
                            "[PlivoMediaHandler] Dropping bad media frame streamId=%s: %s",
                            self.stream_id,
                            e,
                        )

            case "dtmf":
                # DTMF captured for observability only; not forwarded to Voice Live
                # by design (matches the twilio/infobip providers).
                digit = data.get("dtmf", {}).get("digit")
                logger.info("[PlivoMediaHandler] DTMF received: %s", digit)

            case "clearedAudio" | "playedStream":
                logger.debug(
                    "[PlivoMediaHandler] Playback event: %s streamId=%s",
                    event,
                    self.stream_id,
                )

            case _:
                logger.debug("[PlivoMediaHandler] Unknown event: %s", event)
