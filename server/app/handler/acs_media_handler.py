"""Handles media streaming to Azure Voice Live API via WebSocket."""

import asyncio
import base64
import json
import logging
import uuid
from typing import Optional

from azure.identity.aio import ManagedIdentityCredential
from websockets.asyncio.client import connect as ws_connect
from websockets.typing import Data

from app.handler.transcript_service import ConversationTracker, TranscriptService

logger = logging.getLogger(__name__)


def session_config():
    """Returns the default session configuration for Voice Live."""
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
                "name": "en-US-Aria:DragonHDLatestNeural",
                "type": "azure-standard",
                "temperature": 0.8,
            },
        },
    }


class ACSMediaHandler:
    """Manages audio streaming between client and Azure Voice Live API."""

    def __init__(self, config, conversation_id: Optional[str] = None, caller_id: Optional[str] = None):
        self.endpoint = config["AZURE_VOICE_LIVE_ENDPOINT"]
        self.model = config["VOICE_LIVE_MODEL"]
        self.api_key = config["AZURE_VOICE_LIVE_API_KEY"]
        self.client_id = config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"]
        self.send_queue = asyncio.Queue()
        self.ws = None
        self.send_task = None
        self.incoming_websocket = None
        self.is_raw_audio = True

        # Transcript tracking
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.caller_id = caller_id or "unknown"
        self.transcript_service = TranscriptService(config)
        self.conversation_tracker: Optional[ConversationTracker] = None

    def _generate_guid(self):
        return str(uuid.uuid4())

    async def connect(self):
        """Connects to Azure Voice Live API via WebSocket."""
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
                print(token.token)
                headers["Authorization"] = f"Bearer {token.token}"
                logger.info("[VoiceLiveACSHandler] Connected to Voice Live API by managed identity")
        else:
            headers["api-key"] = self.api_key

        self.ws = await ws_connect(url, additional_headers=headers)
        logger.info("[VoiceLiveACSHandler] Connected to Voice Live API")

        await self._send_json(session_config())
        await self._send_json({"type": "response.create"})

        asyncio.create_task(self._receiver_loop())
        self.send_task = asyncio.create_task(self._sender_loop())

    async def init_incoming_websocket(self, socket, is_raw_audio=True):
        """Sets up incoming ACS WebSocket."""
        self.incoming_websocket = socket
        self.is_raw_audio = is_raw_audio
        # Initialize conversation tracker
        self.conversation_tracker = ConversationTracker(
            conversation_id=self.conversation_id,
            caller_id=self.caller_id,
            transcript_service=self.transcript_service,
        )
        logger.info(
            "Initialized conversation tracker for %s (caller: %s)",
            self.conversation_id,
            self.caller_id,
        )

    async def audio_to_voicelive(self, audio_b64: str):
        """Queues audio data to be sent to Voice Live API."""
        await self.send_queue.put(
            json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64})
        )

    async def _send_json(self, obj):
        """Sends a JSON object over WebSocket."""
        if self.ws:
            await self.ws.send(json.dumps(obj))

    async def _sender_loop(self):
        """Continuously sends messages from the queue to the Voice Live WebSocket."""
        try:
            while True:
                msg = await self.send_queue.get()
                if self.ws:
                    await self.ws.send(msg)
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Sender loop error")

    async def _receiver_loop(self):
        """Handles incoming events from the Voice Live WebSocket."""
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")

                match event_type:
                    case "session.created":
                        session_id = event.get("session", {}).get("id")
                        logger.info("[VoiceLiveACSHandler] Session ID: %s", session_id)

                    case "input_audio_buffer.cleared":
                        logger.info("Input Audio Buffer Cleared Message")

                    case "input_audio_buffer.speech_started":
                        logger.info(
                            "Voice activity detection started at %s ms",
                            event.get("audio_start_ms"),
                        )
                        await self.stop_audio()

                    case "input_audio_buffer.speech_stopped":
                        logger.info("Speech stopped")

                    case "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript")
                        logger.info("User: %s", transcript)
                        # Track user transcript
                        if self.conversation_tracker and transcript:
                            self.conversation_tracker.add_user_message(transcript)
                        # Send user transcript to client for display
                        await self.send_message(
                            json.dumps({"Kind": "Transcription", "Text": transcript, "Speaker": "user"})
                        )

                    case "conversation.item.input_audio_transcription.failed":
                        error_msg = event.get("error")
                        logger.warning("Transcription Error: %s", error_msg)

                    case "response.done":
                        response = event.get("response", {})
                        logger.info("Response Done: Id=%s", response.get("id"))
                        if response.get("status_details"):
                            logger.info(
                                "Status Details: %s",
                                json.dumps(response["status_details"], indent=2),
                            )

                    case "response.audio_transcript.done":
                        transcript = event.get("transcript")
                        logger.info("AI: %s", transcript)
                        # Track AI transcript
                        if self.conversation_tracker and transcript:
                            self.conversation_tracker.add_ai_message(transcript)
                        await self.send_message(
                            json.dumps({"Kind": "Transcription", "Text": transcript, "Speaker": "ai"})
                        )

                    case "response.audio.delta":
                        delta = event.get("delta")
                        if self.is_raw_audio:
                            audio_bytes = base64.b64decode(delta)
                            await self.send_message(audio_bytes)
                        else:
                            await self.voicelive_to_acs(delta)

                    case "error":
                        logger.error("Voice Live Error: %s", event)

                    case _:
                        logger.debug(
                            "[VoiceLiveACSHandler] Other event: %s", event_type
                        )
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Receiver loop error")

    async def send_message(self, message: Data):
        """Sends data back to client WebSocket."""
        try:
            await self.incoming_websocket.send(message)
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Failed to send message")

    async def voicelive_to_acs(self, base64_data):
        """Converts Voice Live audio delta to ACS audio message."""
        try:
            data = {
                "Kind": "AudioData",
                "AudioData": {"Data": base64_data},
                "StopAudio": None,
            }
            await self.send_message(json.dumps(data))
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error in voicelive_to_acs")

    async def stop_audio(self):
        """Sends a StopAudio signal to ACS."""
        stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
        await self.send_message(json.dumps(stop_audio_data))

    async def acs_to_voicelive(self, stream_data):
        """Processes audio from ACS and forwards to Voice Live if not silent."""
        try:
            data = json.loads(stream_data)
            if data.get("kind") == "AudioData":
                audio_data = data.get("audioData", {})
                if not audio_data.get("silent", True):
                    await self.audio_to_voicelive(audio_data.get("data"))
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error processing ACS audio")

    async def web_to_voicelive(self, audio_bytes):
        """Encodes raw audio bytes and sends to Voice Live API."""
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        await self.audio_to_voicelive(audio_b64)

    async def save_transcript_and_cleanup(self):
        """Saves the transcript and cleans up resources."""
        try:
            if self.conversation_tracker:
                blob_name = await self.conversation_tracker.save()
                if blob_name:
                    logger.info(
                        "Transcript saved to %s for conversation %s",
                        blob_name,
                        self.conversation_id,
                    )
        except Exception:
            logger.exception("Failed to save transcript for conversation %s", self.conversation_id)
        finally:
            await self.transcript_service.close()

    async def close(self):
        """Closes all connections and saves transcript."""
        await self.save_transcript_and_cleanup()
        if self.ws:
            await self.ws.close()
        if self.send_task:
            self.send_task.cancel()
