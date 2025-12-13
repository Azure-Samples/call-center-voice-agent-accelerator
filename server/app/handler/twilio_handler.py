"""Twilio websocket message handling utilities."""

from __future__ import annotations

import audioop
import base64
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SOURCE_SAMPLE_RATE_HZ = 8000
TARGET_SAMPLE_RATE_HZ = 24000


class TwilioMediaStreamHandler:
    """Process events from a Twilio Media Stream and forward audio to Voice Live."""

    def __init__(
        self,
        websocket,
        voice_live_handler,
        *,
        recording_sid: Optional[str] = None,
        attempt: Optional[int] = None,
    ) -> None:
        self.websocket = websocket
        self.voice_live_handler = voice_live_handler
        self.call_sid: Optional[str] = None
        self.stream_sid: Optional[str] = None
        self.recording_sid: Optional[str] = recording_sid
        self.attempt: Optional[int] = None
        if attempt is not None:
            try:
                self.attempt = int(attempt)
            except (TypeError, ValueError):
                logger.warning("Invalid attempt value from Twilio query: %s", attempt)

    async def handle_message(self, raw_message) -> None:
        """Route an incoming websocket frame from Twilio."""

        if isinstance(raw_message, bytes):
            logger.debug("Ignoring unexpected binary payload from Twilio")
            return

        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning("Failed to decode Twilio payload: %s", raw_message)
            return

        event_type = message.get("event")

        if event_type == "start":
            start = message.get("start", {})
            self.call_sid = start.get("callSid")
            self.stream_sid = start.get("streamSid")
            self.voice_live_handler.update_stream_context(
                call_sid=self.call_sid,
                stream_sid=self.stream_sid,
                recording_sid=self.recording_sid,
                attempt=self.attempt,
            )
            logger.info(
                "Twilio stream started: callSid=%s streamSid=%s",
                self.call_sid,
                self.stream_sid,
            )
        elif event_type == "media":
            payload = message.get("media", {}).get("payload")
            if not payload:
                logger.debug("Empty media payload received from Twilio")
                return

            pcm16 = mulaw_b64_to_pcm16(payload, target_sample_rate=TARGET_SAMPLE_RATE_HZ)
            await self.voice_live_handler.send_pcm16(pcm16)
        elif event_type == "stop":
            logger.info(
                "Twilio stream stopped: callSid=%s streamSid=%s",
                self.call_sid,
                self.stream_sid,
            )
        elif event_type in {"heartbeat", "mark"}:
            logger.debug("Received Twilio control event: %s", event_type)
        else:
            logger.debug("Unhandled Twilio event type: %s", event_type)

    async def send_transcript(self, text: str, *, speaker_prefix: Optional[str] = None) -> None:
        """Send a transcription message back to Twilio over the media stream."""

        if not self.stream_sid:
            logger.debug("Skipping transcript send; Twilio stream SID not available")
            return

        content = f"{speaker_prefix}: {text}" if speaker_prefix else text
        payload = {"event": "message", "streamSid": self.stream_sid, "text": content}
        await self.websocket.send(json.dumps(payload))

    async def send_user_transcript(self, text: str) -> None:
        await self.send_transcript(text, speaker_prefix="Caller")

    async def send_ai_transcript(self, text: str) -> None:
        await self.send_transcript(text, speaker_prefix="Assistant")


def mulaw_b64_to_pcm16(mulaw_b64: str, *, target_sample_rate: int = TARGET_SAMPLE_RATE_HZ) -> bytes:
    """Convert Base64 μ-law (G.711) audio into PCM16 bytes and up-sample if needed."""

    mulaw_bytes = base64.b64decode(mulaw_b64)
    pcm16 = audioop.ulaw2lin(mulaw_bytes, 2)

    if target_sample_rate and target_sample_rate != SOURCE_SAMPLE_RATE_HZ:
        pcm16, _ = audioop.ratecv(
            pcm16,
            2,  # width (bytes per sample)
            1,  # channels
            SOURCE_SAMPLE_RATE_HZ,
            target_sample_rate,
            None,
        )

    return pcm16


__all__ = ["TwilioMediaStreamHandler", "mulaw_b64_to_pcm16"]
