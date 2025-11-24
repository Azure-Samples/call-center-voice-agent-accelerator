"""Handles media streaming to Azure Voice Live API via WebSocket."""

import asyncio
import base64
import json
import logging
import uuid
from typing import Any, Dict, Optional

from azure.identity.aio import ManagedIdentityCredential
from websockets.asyncio.client import connect as ws_connect
from websockets.typing import Data

logger = logging.getLogger(__name__)

# Event type constants
SESSION_CREATED = "session.created"
INPUT_AUDIO_BUFFER_CLEARED = "input_audio_buffer.cleared"
INPUT_AUDIO_BUFFER_SPEECH_STARTED = "input_audio_buffer.speech_started"
INPUT_AUDIO_BUFFER_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED = "conversation.item.input_audio_transcription.completed"
CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_FAILED = "conversation.item.input_audio_transcription.failed"
RESPONSE_DONE = "response.done"
RESPONSE_AUDIO_TRANSCRIPT_DONE = "response.audio_transcript.done"
RESPONSE_AUDIO_DELTA = "response.audio.delta"
ERROR = "error"


def session_config():
    """Returns the default session configuration for Voice Live."""
    return {
        "type": "session.update",
        "session": {
            "instructions": (
                "You are Grace, a warm, empathetic intake receptionist for Mercy House Adult & Teen Challenge. "
                "You provide information to callers as it relates to Mercy House Adult and Teen Challenge and you can use thier website for information https://mercyhouseatc.com"
                "When answering questions about Mercy House or Sacred Grove, speak from the website content naturally, like a staff member."
                "Use natural breathing, soft micro-pauses, hesitations, and conversational rhythm."
                "Use a southern warmth tone"                
                "Do not give medical advice. "
                "Capture caller's name, contact number, and reason for call for follow-up. "
                "Confirm each detail with the caller. "
                "If unsure or caller requests, escalate to a human. "
                "Be natural, kind, and truly listen."
            ),
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.3,
                "prefix_padding_ms": 250,
                "silence_duration_ms": 250,
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
                "name": "en-US-Ava:DragonHDLatestNeural",
                "type": "azure-standard",
                "temperature": 0.7,
            },
        },
    }

class ACSMediaHandler:
    """Manages audio streaming between client and Azure Voice Live API."""

    def __init__(self, config: Dict[str, Any]):
        self.endpoint: str = config["AZURE_VOICE_LIVE_ENDPOINT"]
        self.model: str = config["VOICE_LIVE_MODEL"]
        self.api_key: Optional[str] = config["AZURE_VOICE_LIVE_API_KEY"]
        self.client_id: Optional[str] = config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"]
        self.send_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.ws: Optional[Any] = None
        self.send_task: Optional[asyncio.Task] = None
        self.receiver_task: Optional[asyncio.Task] = None
        self.incoming_websocket: Optional[Any] = None
        self.is_raw_audio: bool = True

    def _generate_guid(self) -> str:
        return str(uuid.uuid4())

    async def connect(self) -> None:
        """Connects to Azure Voice Live API via WebSocket."""
        try:
            endpoint = self.endpoint.rstrip("/")
            model = self.model.strip()
            url = f"{endpoint}/voice-live/realtime?api-version=2025-05-01-preview&model={model}"
            url = url.replace("https://", "wss://")

            headers = {"x-ms-client-request-id": self._generate_guid()}

            if self.client_id:
                # Use async context manager to auto-close the credential
                async with ManagedIdentityCredential(client_id=self.client_id) as credential:
                    token = await credential.get_token(
                        "https://cognitiveservices.azure.com/.default"
                    )
                    headers["Authorization"] = f"Bearer {token.token}"
                    logger.info("[ACSMediaHandler] Connected to Voice Live API by managed identity")
            else:
                headers["api-key"] = self.api_key
                logger.info("[ACSMediaHandler] Connected to Voice Live API by API key")

            self.ws = await ws_connect(url, additional_headers=headers)
            logger.info("[ACSMediaHandler] WebSocket connection established")

            await self._send_json(session_config())
            await self._send_json({"type": "response.create"})

            self.receiver_task = asyncio.create_task(self._receiver_loop())
            self.send_task = asyncio.create_task(self._sender_loop())
        except Exception as e:
            logger.exception("[ACSMediaHandler] Failed to connect to Voice Live API: %s", e)
            raise

    async def init_incoming_websocket(self, socket: Any, is_raw_audio: bool = True) -> None:
        """Sets up incoming ACS WebSocket."""
        self.incoming_websocket = socket
        self.is_raw_audio = is_raw_audio

    async def audio_to_voicelive(self, audio_b64: str) -> None:
        """Queues audio data to be sent to Voice Live API."""
        await self.send_queue.put(
            json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64})
        )

    async def _send_json(self, obj: Dict[str, Any]) -> None:
        """Sends a JSON object over WebSocket."""
        if self.ws:
            await self.ws.send(json.dumps(obj))

    async def _sender_loop(self) -> None:
        """Continuously sends messages from the queue to the Voice Live WebSocket."""
        try:
            while True:
                msg = await self.send_queue.get()
                if self.ws:
                    await self.ws.send(msg)
        except asyncio.CancelledError:
            logger.info("[ACSMediaHandler] Sender loop cancelled")
            raise
        except Exception:
            logger.exception("[ACSMediaHandler] Sender loop error")

    async def _receiver_loop(self) -> None:
        """Handles incoming events from the Voice Live WebSocket."""
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")

                match event_type:
                    case _ if event_type == SESSION_CREATED:
                        session_id = event.get("session", {}).get("id")
                        logger.info("[ACSMediaHandler] Session ID: %s", session_id)

                    case _ if event_type == INPUT_AUDIO_BUFFER_CLEARED:
                        logger.info("[ACSMediaHandler] Input audio buffer cleared")

                    case _ if event_type == INPUT_AUDIO_BUFFER_SPEECH_STARTED:
                        logger.info(
                            "[ACSMediaHandler] Voice activity detection started at %s ms",
                            event.get("audio_start_ms"),
                        )
                        await self.stop_audio()

                    case _ if event_type == INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
                        logger.info("[ACSMediaHandler] Speech stopped")

                    case _ if event_type == CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
                        transcript = event.get("transcript")
                        logger.info("[ACSMediaHandler] User: %s", transcript)

                    case _ if event_type == CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_FAILED:
                        error_msg = event.get("error")
                        logger.warning("[ACSMediaHandler] Transcription error: %s", error_msg)

                    case _ if event_type == RESPONSE_DONE:
                        response = event.get("response", {})
                        logger.info("[ACSMediaHandler] Response done: Id=%s", response.get("id"))
                        if response.get("status_details"):
                            logger.info(
                                "[ACSMediaHandler] Status details: %s",
                                json.dumps(response["status_details"], indent=2),
                            )

                    case _ if event_type == RESPONSE_AUDIO_TRANSCRIPT_DONE:
                        transcript = event.get("transcript")
                        logger.info("[ACSMediaHandler] AI: %s", transcript)
                        await self.send_message(
                            json.dumps({"Kind": "Transcription", "Text": transcript})
                        )

                    case _ if event_type == RESPONSE_AUDIO_DELTA:
                        delta = event.get("delta")
                        if self.is_raw_audio:
                            audio_bytes = base64.b64decode(delta)
                            await self.send_message(audio_bytes)
                        else:
                            await self.voicelive_to_acs(delta)

                    case _ if event_type == ERROR:
                        logger.error("[ACSMediaHandler] Voice Live error: %s", event)

                    case _:
                        logger.debug("[ACSMediaHandler] Other event: %s", event_type)
        except asyncio.CancelledError:
            logger.info("[ACSMediaHandler] Receiver loop cancelled")
            raise
        except Exception:
            logger.exception("[ACSMediaHandler] Receiver loop error")

    async def send_message(self, message: Data) -> None:
        """Sends data back to client WebSocket."""
        try:
            await self.incoming_websocket.send(message)
        except Exception:
            logger.exception("[ACSMediaHandler] Failed to send message")

    async def voicelive_to_acs(self, base64_data: str) -> None:
        """Converts Voice Live audio delta to ACS audio message."""
        try:
            data = {
                "Kind": "AudioData",
                "AudioData": {"Data": base64_data},
                "StopAudio": None,
            }
            await self.send_message(json.dumps(data))
        except Exception:
            logger.exception("[ACSMediaHandler] Error in voicelive_to_acs")

    async def stop_audio(self) -> None:
        """Sends a StopAudio signal to ACS."""
        stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
        await self.send_message(json.dumps(stop_audio_data))

    async def acs_to_voicelive(self, stream_data: str) -> None:
        """Processes audio from ACS and forwards to Voice Live if not silent."""
        try:
            data = json.loads(stream_data)
            if data.get("kind") == "AudioData":
                audio_data = data.get("audioData", {})
                if not audio_data.get("silent", True):
                    await self.audio_to_voicelive(audio_data.get("data"))
        except Exception:
            logger.exception("[ACSMediaHandler] Error processing ACS audio")

    async def web_to_voicelive(self, audio_bytes: bytes) -> None:
        """Encodes raw audio bytes and sends to Voice Live API."""
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        await self.audio_to_voicelive(audio_b64)

    async def close(self) -> None:
        """Closes WebSocket connection and cancels background tasks."""
        logger.info("[ACSMediaHandler] Closing handler")

        # Cancel background tasks
        if self.send_task and not self.send_task.done():
            self.send_task.cancel()
            try:
                await self.send_task
            except asyncio.CancelledError:
                pass

        if self.receiver_task and not self.receiver_task.done():
            self.receiver_task.cancel()
            try:
                await self.receiver_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket connection
        if self.ws:
            await self.ws.close()
            self.ws = None

        logger.info("[ACSMediaHandler] Handler closed successfully")
