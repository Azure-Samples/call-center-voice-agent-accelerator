"""Azure AI Speech real-time STT backend: one continuous recognizer per speaker channel."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

import azure.cognitiveservices.speech as speechsdk

from app.transcription.backend import EventCallback
from app.transcription.events import Speaker, StatusEvent, StreamState, TranscriptEvent

logger = logging.getLogger(__name__)

TICKS_PER_MS = 10_000

RecognizerFactory = Callable[[Any, int], tuple[Any, Any]]


def resolve_speech_settings(config: dict) -> tuple[str, str, str]:
    """Return (endpoint, api_key, managed_identity_client_id).

    Speech-specific settings win; otherwise reuse the Voice Live (AI Services) resource.
    """
    endpoint = config.get("AZURE_SPEECH_ENDPOINT") or config.get("AZURE_VOICE_LIVE_ENDPOINT") or ""
    key = config.get("AZURE_SPEECH_KEY") or config.get("AZURE_VOICE_LIVE_API_KEY") or ""
    client_id = config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID") or ""
    return endpoint, key, client_id


def build_speech_config(config: dict) -> speechsdk.SpeechConfig:
    endpoint, key, client_id = resolve_speech_settings(config)
    if client_id:
        from azure.identity import ManagedIdentityCredential

        speech_config = speechsdk.SpeechConfig(
            endpoint=endpoint, token_credential=ManagedIdentityCredential(client_id=client_id)
        )
    elif key:
        speech_config = speechsdk.SpeechConfig(endpoint=endpoint, subscription=key)
    else:
        from azure.identity import DefaultAzureCredential

        speech_config = speechsdk.SpeechConfig(
            endpoint=endpoint, token_credential=DefaultAzureCredential()
        )
    speech_config.speech_recognition_language = config.get("TRANSCRIPTION_LANGUAGE") or "en-US"
    return speech_config


def default_recognizer_factory(speech_config, sample_rate: int):
    fmt = speechsdk.audio.AudioStreamFormat(
        samples_per_second=sample_rate, bits_per_sample=16, channels=1
    )
    stream = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=speechsdk.audio.AudioConfig(stream=stream)
    )
    return stream, recognizer


@dataclass
class _Channel:
    stream: Any
    recognizer: Any


class AzureSpeechBackend:
    def __init__(
        self,
        config: dict,
        sample_rate: int,
        recognizer_factory: RecognizerFactory = default_recognizer_factory,
        restart_base_delay: float = 1.0,
        max_restarts: int = 3,
    ):
        self.config = config
        self.sample_rate = sample_rate
        self._factory = recognizer_factory
        self._restart_base_delay = restart_base_delay
        self._max_restarts = max_restarts
        self._speech_config = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_event: EventCallback | None = None
        self._cid = ""
        self._seq = 0
        self._channels: dict[Speaker, _Channel | None] = {}
        self._restarts: dict[Speaker, int] = {}
        self._restart_tasks: set[asyncio.Task] = set()
        self._stopped = False

    async def start(self, conversation_id, speakers, on_event):
        self._loop = asyncio.get_running_loop()
        self._cid = conversation_id
        self._on_event = on_event
        self._speech_config = build_speech_config(self.config)
        for speaker in speakers:
            self._restarts[speaker] = 0
            channel = self._create_channel(speaker)
            # Register before starting so stop() can clean up even if start is cancelled.
            self._channels[speaker] = channel
            await self._start_channel(speaker, channel)

    def _create_channel(self, speaker: Speaker) -> _Channel:
        stream, recognizer = self._factory(self._speech_config, self.sample_rate)
        recognizer.recognizing.connect(lambda e, s=speaker: self._from_sdk(self._on_result, s, e, False))
        recognizer.recognized.connect(lambda e, s=speaker: self._from_sdk(self._on_result, s, e, True))
        recognizer.canceled.connect(lambda e, s=speaker, r=recognizer: self._from_sdk(self._on_canceled, s, r, e))
        return _Channel(stream, recognizer)

    async def _start_channel(self, speaker: Speaker, channel: _Channel) -> None:
        await asyncio.to_thread(channel.recognizer.start_continuous_recognition)
        logger.info("[AzureSpeech] %s recognizer started (%d Hz) for %s", speaker, self.sample_rate, self._cid)

    def _from_sdk(self, fn, *args) -> None:
        """SDK callbacks fire on worker threads; hop onto the event loop."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(fn, *args)

    def _on_result(self, speaker: Speaker, evt, is_final: bool) -> None:
        if self._stopped or self._on_event is None:
            return
        result = evt.result
        if not result.text:
            return
        self._seq += 1
        self._on_event(
            TranscriptEvent(
                conversation_id=self._cid,
                speaker=speaker,
                text=result.text,
                is_final=is_final,
                offset_ms=int(result.offset) // TICKS_PER_MS,
                duration_ms=int(result.duration) // TICKS_PER_MS,
                seq=self._seq,
            )
        )

    def _on_canceled(self, speaker: Speaker, recognizer, evt) -> None:
        details = evt.cancellation_details
        channel = self._channels.get(speaker)
        if self._stopped or channel is None or channel.recognizer is not recognizer:
            return
        if details.reason != speechsdk.CancellationReason.Error:
            logger.info("[AzureSpeech] %s recognition ended: %s", speaker, details.reason)
            return
        logger.warning("[AzureSpeech] %s recognition error: %s", speaker, details.error_details)
        task = asyncio.ensure_future(self._restart(speaker, str(details.error_details)))
        self._restart_tasks.add(task)
        task.add_done_callback(self._restart_tasks.discard)

    def _status(self, state: StreamState, detail: str = "") -> None:
        if self._on_event is not None:
            self._on_event(StatusEvent(self._cid, state, detail))

    async def _restart(self, speaker: Speaker, reason: str) -> None:
        old = self._channels.get(speaker)
        self._channels[speaker] = None
        self._status(StreamState.DEGRADED, f"{speaker} recognizer error: {reason}")
        if old is not None:
            await self._close_channel(old)
        while not self._stopped:
            self._restarts[speaker] += 1
            attempt = self._restarts[speaker]
            if attempt > self._max_restarts:
                logger.error("[AzureSpeech] %s giving up after %d restarts", speaker, attempt - 1)
                return
            await asyncio.sleep(self._restart_base_delay * 2 ** (attempt - 1))
            if self._stopped:
                return
            try:
                channel = self._create_channel(speaker)
                self._channels[speaker] = channel
                await self._start_channel(speaker, channel)
            except Exception:
                logger.exception("[AzureSpeech] %s restart attempt %d failed", speaker, attempt)
                failed = self._channels.pop(speaker, None)
                self._channels[speaker] = None
                if failed is not None:
                    await self._close_channel(failed)
                continue
            self._status(StreamState.CONNECTED)
            return

    def push_audio(self, speaker, pcm16):
        channel = self._channels.get(speaker)
        if channel is not None and not self._stopped:
            channel.stream.write(pcm16)

    @staticmethod
    async def _close_channel(channel: _Channel) -> None:
        try:
            channel.stream.close()
            await asyncio.to_thread(channel.recognizer.stop_continuous_recognition)
        except Exception:
            logger.exception("[AzureSpeech] Error stopping recognizer")

    async def stop(self):
        self._stopped = True
        for task in list(self._restart_tasks):
            task.cancel()
        channels = [c for c in self._channels.values() if c is not None]
        self._channels = {}
        await asyncio.gather(*(self._close_channel(c) for c in channels))
        self._on_event = None
