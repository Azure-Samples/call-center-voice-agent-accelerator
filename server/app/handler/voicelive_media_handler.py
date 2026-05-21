"""Base handler for Azure Voice Live API WebSocket connections.

Provides the shared Voice Live connection, sender/receiver loops, web client
audio handling with ambient mixing, and cleanup logic. Telephony subclasses
override hooks and handle_audio() to implement protocol-specific behavior.
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Optional

import numpy as np
from azure.identity.aio import ManagedIdentityCredential
from websockets.asyncio.client import connect as ws_connect
from websockets.typing import Data

from .ambient_mixer import AmbientMixer

logger = logging.getLogger(__name__)

# Default chunk size in bytes (100ms of audio at 24kHz, 16-bit mono)
DEFAULT_CHUNK_SIZE = 4800  # 24000 samples/sec * 0.1 sec * 2 bytes


class VoiceLiveMediaHandler:
    """Handles the WebSocket connection to Azure Voice Live API and web clients.

    Provides web client audio handling (raw PCM + ambient mixing) by default.
    Telephony subclasses (ACS, Twilio) override hooks for their specific protocols.
    """

    _api_version = "2026-01-01-preview"

    def __init__(self, config):
        self.endpoint = config["AZURE_VOICE_LIVE_ENDPOINT"]
        self.model = config["VOICE_LIVE_MODEL"]
        self.api_key = config["AZURE_VOICE_LIVE_API_KEY"]
        self.client_id = config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"]
        self.ws = None
        self._send_queue = asyncio.Queue()
        self._send_task = None
        self._receiver_task = None
        self._voicelive_connected = False  # True while Voice Live WS is healthy

        # Client WebSocket
        self.client_ws = None

        # TTS output buffering for continuous ambient mixing
        self._tts_output_buffer = bytearray()
        self._tts_buffer_lock = asyncio.Lock()
        self._max_buffer_size = 480000  # 10 seconds of audio
        self._buffer_warning_logged = False
        self._tts_playback_started = False
        self._min_buffer_to_start = 9600  # 200ms buffer before starting TTS playback

        # Ambient mixer initialization
        self._ambient_mixer: Optional[AmbientMixer] = None
        ambient_preset = config.get("AMBIENT_PRESET", "none")
        if ambient_preset and ambient_preset != "none":
            try:
                self._ambient_mixer = AmbientMixer(preset=ambient_preset)
            except Exception as e:
                logger.error(f"Failed to initialize AmbientMixer: {e}")

    def _session_config(self):
        """Return the session configuration to send on connect."""
        return {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": "You are a helpful AI assistant responding in natural, engaging language.",
                "turn_detection": {
                    "type": "azure_semantic_vad",
                },
                "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
                "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
                "voice": {
                    "name": "en-US-Aria:DragonHDLatestNeural",
                    "type": "azure-standard",
                    "temperature": 0.8,
                },
            },
        }

    # ------------------------------------------------------------------
    # Voice Live connection
    # ------------------------------------------------------------------

    async def connect_voicelive(self):
        """Connect to Azure Voice Live API via WebSocket."""
        endpoint = self.endpoint.rstrip("/")
        model = self.model.strip()
        url = (
            f"{endpoint}/voice-live/realtime"
            f"?api-version={self._api_version}&model={model}"
        )
        url = url.replace("https://", "wss://")

        headers = {"x-ms-client-request-id": str(uuid.uuid4())}

        t0 = time.perf_counter()
        if self.client_id:
            async with ManagedIdentityCredential(client_id=self.client_id) as credential:
                token = await credential.get_token(
                    "https://cognitiveservices.azure.com/.default"
                )
                headers["Authorization"] = f"Bearer {token.token}"
        else:
            headers["api-key"] = self.api_key
        t1 = time.perf_counter()
        logger.info("[VoiceLive] Auth completed in %.2fs", t1 - t0)

        self.ws = await ws_connect(url, additional_headers=headers)
        t2 = time.perf_counter()
        logger.info("[VoiceLive] WebSocket connected in %.2fs (total %.2fs)", t2 - t1, t2 - t0)
        self._voicelive_connected = True

        await self._send_json(self._session_config())
        await self._send_json({"type": "response.create"})

        self._receiver_task = asyncio.create_task(self._receiver_loop())
        self._send_task = asyncio.create_task(self._sender_loop())

    async def send_audio(self, audio_b64: str):
        """Queue PCM 24kHz 16-bit mono audio (base64) to send to Voice Live."""
        if not self._voicelive_connected:
            return
        await self._send_queue.put(
            json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64})
        )

    async def _send_json(self, obj):
        """Send a JSON object directly to the Voice Live WebSocket."""
        if self.ws:
            await self.ws.send(json.dumps(obj))

    async def _sender_loop(self):
        """Continuously sends queued messages to Voice Live."""
        try:
            while True:
                msg = await self._send_queue.get()
                if self.ws:
                    await self.ws.send(msg)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[VoiceLive] Sender loop error")

    async def _receiver_loop(self):
        """Receives events from Voice Live and dispatches to hook methods."""
        cancelled = False
        try:
            async for message in self.ws:
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                match event_type:
                    case "session.created":
                        session_id = event.get("session", {}).get("id")
                        logger.info("[VoiceLive] Session ID: %s", session_id)

                    case "session.updated":
                        logger.info("[VoiceLive] Session updated")

                    case "input_audio_buffer.cleared":
                        logger.debug("[VoiceLive] Input audio buffer cleared")

                    case "input_audio_buffer.speech_started":
                        logger.info(
                            "[VoiceLive] Speech started at %s ms",
                            event.get("audio_start_ms"),
                        )
                        await self.on_speech_started()

                    case "input_audio_buffer.speech_stopped":
                        logger.info("[VoiceLive] Speech stopped")

                    case "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript")
                        logger.debug("[VoiceLive] User: %s", transcript)

                    case "conversation.item.input_audio_transcription.failed":
                        logger.warning(
                            "[VoiceLive] Transcription error: %s", event.get("error")
                        )

                    case "response.audio.delta":
                        delta = event.get("delta", "")
                        if delta:
                            await self.on_audio_delta(delta)

                    case "response.audio_transcript.done":
                        transcript = event.get("transcript")
                        logger.debug("[VoiceLive] AI: %s", transcript)
                        await self.on_transcript_done(transcript)

                    case "response.done":
                        response = event.get("response", {})
                        logger.info(
                            "[VoiceLive] Response done: id=%s", response.get("id")
                        )

                    case "error":
                        logger.error("[VoiceLive] Error: %s", event.get("error"))

                    case _:
                        logger.debug("[VoiceLive] Event: %s", event_type)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            logger.exception("[VoiceLive] Receiver loop error")
        finally:
            self._voicelive_connected = False
            # If Voice Live dropped unexpectedly (not a normal cancellation),
            # close the client WebSocket so the caller-side loop exits cleanly.
            if not cancelled and self.client_ws:
                try:
                    logger.warning("[VoiceLive] Voice Live disconnected — closing client WebSocket")
                    await self.client_ws.close(1001)  # Going Away
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Client WebSocket
    # ------------------------------------------------------------------

    async def init_websocket(self, socket):
        """Sets up the client WebSocket."""
        self.client_ws = socket

    async def send_message(self, message: Data):
        """Sends data back to client WebSocket."""
        try:
            await self.client_ws.send(message)
        except Exception:
            logger.exception("[VoiceLive] Failed to send message to client")

    # ------------------------------------------------------------------
    # Hooks — web client implementations (override in telephony subclasses)
    # ------------------------------------------------------------------

    async def on_speech_started(self):
        """Barge-in: send StopAudio to client and clear TTS buffer."""
        stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
        await self.send_message(json.dumps(stop_audio_data))

        if self._ambient_mixer is not None:
            async with self._tts_buffer_lock:
                self._tts_output_buffer.clear()
                self._tts_playback_started = False

    async def on_audio_delta(self, audio_b64: str):
        """Handle audio from Voice Live — buffer for ambient or send directly."""
        audio_bytes = base64.b64decode(audio_b64)

        if self._ambient_mixer is not None and self._ambient_mixer.is_enabled():
            async with self._tts_buffer_lock:
                self._tts_output_buffer.extend(audio_bytes)
                if len(self._tts_output_buffer) > self._max_buffer_size:
                    if not self._buffer_warning_logged:
                        logger.warning(
                            f"TTS buffer large: {len(self._tts_output_buffer)} bytes. "
                            "Speech may be delayed but will not be cut."
                        )
                        self._buffer_warning_logged = True
                elif self._buffer_warning_logged and len(self._tts_output_buffer) < self._max_buffer_size // 2:
                    self._buffer_warning_logged = False
        else:
            await self._send_audio_to_client(audio_bytes)

    async def on_transcript_done(self, transcript: str):
        """Forward transcript to client."""
        await self.send_message(
            json.dumps({"Kind": "Transcription", "Text": transcript})
        )

    # ------------------------------------------------------------------
    # Audio output to client
    # ------------------------------------------------------------------

    async def _send_audio_to_client(self, audio_bytes: bytes):
        """Send audio bytes to the client. Override in subclasses for wrapping."""
        await self.send_message(audio_bytes)

    # ------------------------------------------------------------------
    # Inbound audio from client
    # ------------------------------------------------------------------

    def _receive_audio_from_client(self, data) -> tuple:
        """Convert client audio to PCM 24kHz. Override for format conversion.

        Returns (pcm_bytes | None, chunk_size). Return None for silent frames.
        """
        return data, len(data)

    async def handle_audio(self, data):
        """Process inbound audio: convert, mix ambient, forward to Voice Live."""
        pcm_bytes, chunk_size = self._receive_audio_from_client(data)
        await self._send_continuous_audio(chunk_size)
        if pcm_bytes:
            audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
            await self.send_audio(audio_b64)

    # ------------------------------------------------------------------
    # Ambient mixing
    # ------------------------------------------------------------------

    async def _send_continuous_audio(self, chunk_size: int) -> None:
        """Send continuous audio (ambient + TTS if available) back to client."""
        if self._ambient_mixer is None or not self._ambient_mixer.is_enabled():
            return

        try:
            async with self._tts_buffer_lock:
                buffer_len = len(self._tts_output_buffer)
                ambient_bytes = self._ambient_mixer.get_ambient_only_chunk(chunk_size)

                should_play_tts = False
                if self._tts_playback_started:
                    if buffer_len >= chunk_size:
                        should_play_tts = True
                    elif buffer_len > 0:
                        should_play_tts = True
                    else:
                        self._tts_playback_started = False
                else:
                    if buffer_len >= self._min_buffer_to_start:
                        self._tts_playback_started = True
                        should_play_tts = True

                if should_play_tts and buffer_len >= chunk_size:
                    tts_chunk = bytes(self._tts_output_buffer[:chunk_size])
                    del self._tts_output_buffer[:chunk_size]

                    ambient = np.frombuffer(ambient_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    tts = np.frombuffer(tts_chunk, dtype=np.int16).astype(np.float32) / 32768.0
                    mixed = np.clip(ambient + tts, -0.95, 0.95)
                    output_bytes = (mixed * 32767).astype(np.int16).tobytes()

                elif should_play_tts and buffer_len > 0:
                    tts_chunk = bytes(self._tts_output_buffer[:])
                    self._tts_output_buffer.clear()
                    self._tts_playback_started = False

                    ambient = np.frombuffer(ambient_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    tts_samples = len(tts_chunk) // 2
                    tts = np.frombuffer(tts_chunk, dtype=np.int16).astype(np.float32) / 32768.0
                    ambient[:tts_samples] += tts
                    mixed = np.clip(ambient, -0.95, 0.95)
                    output_bytes = (mixed * 32767).astype(np.int16).tobytes()

                else:
                    output_bytes = ambient_bytes

            await self._send_audio_to_client(output_bytes)

        except Exception:
            logger.exception("[VoiceLive] Error in _send_continuous_audio")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _cleanup(self):
        """Cancel background tasks and close the Voice Live WebSocket."""
        for task in (self._receiver_task, self._send_task):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._receiver_task = None
        self._send_task = None
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        logger.info("[VoiceLive] Cleaned up")
