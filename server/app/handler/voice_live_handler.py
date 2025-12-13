"""Utilities for streaming audio to the Azure Voice Live API."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Awaitable, Callable, Optional

from azure.identity.aio import ManagedIdentityCredential
from websockets.asyncio.client import connect as ws_connect

from app.queue import SmsQueueProducer
from app.storage import VoicemailTranscriptionStore

logger = logging.getLogger(__name__)


def session_config() -> dict:
    """Return the default session configuration for Voice Live."""

    return {
        "type": "session.update",
        "session": {
            "instructions": "You are a helpful AI assistant responding in natural, engaging language.",
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.3,
                "prefix_padding_ms": 200,
                "silence_duration_ms": 200,
                "remove_filler_words": False,
                "end_of_utterance_detection": {
                    "model": "semantic_detection_v1",
                    "threshold": 0.01,
                    "timeout": 2,
                },
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "voice": {
                "name": "en-US-Alloy:DragonHDLatestNeural",
                "type": "azure-standard",
                "temperature": 0.8,
            },
        },
    }


AsyncTextCallback = Optional[Callable[[str], Awaitable[None]]]
AsyncAudioCallback = Optional[Callable[[bytes], Awaitable[None]]]
AsyncVoidCallback = Optional[Callable[[], Awaitable[None]]]


class VoiceLiveStreamingHandler:
    """Manage bi-directional streaming between clients and the Voice Live API."""

    def __init__(
        self,
        config: dict,
        *,
        enable_responses: bool = True,
        transcription_store: Optional[VoicemailTranscriptionStore] = None,
        sms_queue: Optional[SmsQueueProducer] = None,
    ) -> None:
        self.endpoint = config.get("AZURE_VOICE_LIVE_ENDPOINT")
        if not self.endpoint:
            raise ValueError("AZURE_VOICE_LIVE_ENDPOINT must be provided")

        self.model = config.get("VOICE_LIVE_MODEL", "gpt-4o-mini").strip()
        self.api_key = config.get("AZURE_VOICE_LIVE_API_KEY", "").strip()
        self.client_id = config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "").strip()
        self.enable_responses = enable_responses
        self.transcription_store = transcription_store
        self.sms_queue = sms_queue

        self.send_queue: asyncio.Queue[str] = asyncio.Queue()
        self.ws = None
        self._sender_task: Optional[asyncio.Task] = None
        self._receiver_task: Optional[asyncio.Task] = None
        self._connection_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

        self._on_user_transcript: AsyncTextCallback = None
        self._on_ai_transcript: AsyncTextCallback = None
        self._on_audio_chunk: AsyncAudioCallback = None
        self._on_audio_stop: AsyncVoidCallback = None

        self.call_sid: Optional[str] = None
        self.stream_sid: Optional[str] = None
        self.recording_sid: Optional[str] = None
        self.attempt: Optional[int] = None

    def _generate_guid(self) -> str:
        return str(uuid.uuid4())

    def register_event_handlers(
        self,
        *,
        on_user_transcript: AsyncTextCallback = None,
        on_ai_transcript: AsyncTextCallback = None,
        on_audio_chunk: AsyncAudioCallback = None,
        on_audio_stop: AsyncVoidCallback = None,
    ) -> None:
        """Set asynchronous callbacks for downstream events."""

        self._on_user_transcript = on_user_transcript
        self._on_ai_transcript = on_ai_transcript
        self._on_audio_chunk = on_audio_chunk
        self._on_audio_stop = on_audio_stop

    def update_stream_context(
        self,
        *,
        call_sid: Optional[str] = None,
        stream_sid: Optional[str] = None,
        recording_sid: Optional[str] = None,
        attempt: Optional[int] = None,
    ) -> None:
        """Persist metadata from the upstream telephony provider."""

        if call_sid:
            self.call_sid = call_sid
        if stream_sid:
            self.stream_sid = stream_sid
        if recording_sid:
            self.recording_sid = recording_sid
        if attempt is not None:
            try:
                self.attempt = int(attempt)
            except (TypeError, ValueError):
                logger.warning("[VoiceLive] Invalid attempt value '%s' provided", attempt)

    async def start(self) -> None:
        """Establish the Voice Live WebSocket connection if needed."""

        async with self._connection_lock:
            if self.ws is not None:
                return

            endpoint = self.endpoint.rstrip("/")
            url = f"{endpoint}/voice-live/realtime?api-version=2025-05-01-preview&model={self.model}"
            url = url.replace("https://", "wss://", 1)

            headers = {"x-ms-client-request-id": self._generate_guid()}

            if self.client_id:
                async with ManagedIdentityCredential(client_id=self.client_id) as credential:
                    token = await credential.get_token("https://cognitiveservices.azure.com/.default")
                    headers["Authorization"] = f"Bearer {token.token}"
                    logger.info("[VoiceLive] Connected to Voice Live API using managed identity")
            elif self.api_key:
                headers["api-key"] = self.api_key
            else:
                raise ValueError(
                    "Either AZURE_VOICE_LIVE_API_KEY or AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID must be configured"
                )

            self.ws = await ws_connect(url, additional_headers=headers)
            logger.info("[VoiceLive] Connected to Voice Live API")

            await self._send_json(session_config())
            if self.enable_responses:
                await self._send_json({"type": "response.create"})

            self._sender_task = asyncio.create_task(self._sender_loop(), name="voice-live-sender")
            self._receiver_task = asyncio.create_task(self._receiver_loop(), name="voice-live-receiver")

    async def close(self) -> None:
        """Terminate background tasks and close the websocket."""

        async with self._close_lock:
            tasks = [task for task in (self._sender_task, self._receiver_task) if task]
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("[VoiceLive] Error during task shutdown")

            self._sender_task = None
            self._receiver_task = None

            for task in list(self._background_tasks):
                task.cancel()
            for task in list(self._background_tasks):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("[VoiceLive] Background task error during shutdown")
            self._background_tasks.clear()

            if self.ws:
                await self.ws.close()
                self.ws = None

            # drain queue to release pending awaiters
            while not self.send_queue.empty():
                self.send_queue.get_nowait()
                self.send_queue.task_done()

    async def send_pcm16(self, pcm_bytes: bytes) -> None:
        """Accept signed 16-bit PCM audio and enqueue for Voice Live."""

        audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
        await self._enqueue_audio(audio_b64)

    async def send_audio_base64(self, audio_b64: str) -> None:
        """Accept already-encoded base64 audio data."""

        await self._enqueue_audio(audio_b64)

    async def _enqueue_audio(self, audio_b64: str) -> None:
        await self.send_queue.put(json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64}))

    async def _send_json(self, payload: dict) -> None:
        if not self.ws:
            raise RuntimeError("Voice Live connection has not been established")
        await self.ws.send(json.dumps(payload))

    async def _sender_loop(self) -> None:
        try:
            while True:
                msg = await self.send_queue.get()
                if self.ws:
                    await self.ws.send(msg)
                self.send_queue.task_done()
        except asyncio.CancelledError:
            logger.debug("[VoiceLive] Sender loop stopped")
        except Exception:
            logger.exception("[VoiceLive] Sender loop error")

    async def _receiver_loop(self) -> None:
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")

                if event_type == "session.created":
                    session_id = event.get("session", {}).get("id")
                    logger.info("[VoiceLive] Session ID: %s", session_id)
                elif event_type == "input_audio_buffer.cleared":
                    logger.debug("[VoiceLive] Input audio buffer cleared")
                elif event_type == "input_audio_buffer.speech_started":
                    await self._emit_stop()
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript")
                    if transcript:
                        await self._emit_user_transcript(transcript)
                        self._schedule_transcript_persist(transcript, event)
                elif event_type == "conversation.item.input_audio_transcription.failed":
                    logger.warning("[VoiceLive] Transcription error: %s", event.get("error"))
                elif event_type == "response.audio_transcript.done":
                    transcript = event.get("transcript")
                    if transcript:
                        await self._emit_ai_transcript(transcript)
                elif event_type == "response.audio.delta":
                    delta = event.get("delta")
                    if delta:
                        await self._emit_audio_chunk(base64.b64decode(delta))
                elif event_type == "error":
                    logger.error("[VoiceLive] Error event received: %s", event)
                else:
                    logger.debug("[VoiceLive] Unhandled event: %s", event_type)
        except asyncio.CancelledError:
            logger.debug("[VoiceLive] Receiver loop cancelled")
        except Exception:
            logger.exception("[VoiceLive] Receiver loop error")

    async def _emit_user_transcript(self, text: str) -> None:
        if self._on_user_transcript:
            await self._on_user_transcript(text)

    async def _emit_ai_transcript(self, text: str) -> None:
        if self._on_ai_transcript:
            await self._on_ai_transcript(text)

    async def _emit_audio_chunk(self, chunk: bytes) -> None:
        if self._on_audio_chunk:
            await self._on_audio_chunk(chunk)

    async def _emit_stop(self) -> None:
        if self._on_audio_stop:
            await self._on_audio_stop()

    def _schedule_transcript_persist(self, transcript: str, event: dict) -> None:
        if not self.transcription_store or not self.call_sid or not self.recording_sid:
            return

        if self.attempt is None:
            logger.debug("[VoiceLive] Skipping persistence; attempt number not set")
            return

        confidence = self._extract_confidence(event)

        async def persist() -> None:
            try:
                blob_url = await self.transcription_store.store_transcript(
                    call_sid=self.call_sid or "",
                    recording_sid=self.recording_sid or "",
                    attempt=self.attempt,
                    transcript=transcript,
                    confidence=confidence,
                )
                if self.sms_queue:
                    candidate = await self.transcription_store.build_sms_candidate(
                        call_sid=self.call_sid,
                        recording_sid=self.recording_sid,
                        attempt=self.attempt,
                    )
                    if candidate:
                        try:
                            await self.sms_queue.enqueue(candidate)
                            await self.transcription_store.mark_sms_enqueued(
                                call_sid=self.call_sid,
                                recording_sid=self.recording_sid,
                                attempt=self.attempt,
                            )
                        except Exception:
                            logger.exception("[VoiceLive] Failed to enqueue SMS delivery job")
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("[VoiceLive] Failed to persist voicemail transcript")

        task = asyncio.create_task(persist(), name="voice-live-transcript-persist")
        task.add_done_callback(self._background_tasks.discard)
        self._background_tasks.add(task)

    @staticmethod
    def _extract_confidence(event: dict) -> Optional[float]:
        confidence = event.get("confidence")
        if confidence is not None:
            try:
                return float(confidence)
            except (TypeError, ValueError):
                return None

        alternatives = event.get("alternatives")
        if isinstance(alternatives, list) and alternatives:
            candidate = alternatives[0]
            if isinstance(candidate, dict):
                try:
                    value = candidate.get("confidence")
                    if value is not None:
                        return float(value)
                except (TypeError, ValueError):
                    return None
        return None
