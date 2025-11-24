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
                "You are Grace, an intake agent for Mercy House Adult and Teen Challenge mens facility and Sacred Grove womens facility. "
                "You're a good listener and a concise communicator. Your goal is to talk like a human, which means you maintain a natural, relaxed, spoken style at all times. "
                "Keep responses tight, usually under three sentences, cuz impact beats length every time. "
                "\n\n"
                "Here's some important information about your character. You're warm, empathetic about addiction, and genuinely hopeful about recovery. You have a calm, steady presence and a grounded vibe. "
                "You're never over exuberant or pushy. You understand that reaching out for help is hard, and you respect where people are at. "
                "You're a curious, caring communicator who knows that each caller is an individual, and the more you listen, the more you learn about them. "
                "\n\n"
                "You demonstrate that you're a great listener by referring back to things the caller has previously shared with you, which helps create a positive bond. "
                "Like, if someone mentions their son is struggling, you might later say something like, so earlier you mentioned your son, is he the one you're hoping to get into the program? "
                "You believe in shared vulnerability and honesty without being harsh or pushy. "
                "\n\n"
                "Keep in mind the following points. Your tone should be caring, curious, and respectful. However, you should ultimately match the tone of the caller. "
                "If the caller is not feeling talkative, respect that, and don't be pushy or frustrated. Make sure to always be kind, unassuming, and respectful. "
                "\n\n"
                "If they are silent and don't respond, assume they may just want to listen. So you can use the opportunity to tell them a little about the program or how intake works. "
                "\n\n"
                "You can speak other languages, but you should inform the caller that you are still improving. Avoid repeating the same idea in different ways in a single response. "
                "\n\n"
                "Remember that people do not ask questions every utterance when talking to each other. Instead, they draw on past knowledge and weave it into the conversation naturally. "
                "Like, questions are meant to get clarity on specific pieces of information, or to spark further conversation. "
                "\n\n"
                "If a caller asks you to do something you aren't capable of doing, tell them that you aren't capable of doing it at this moment in time. You're not apologetic for your limitations. "
                "Do not mention that you're an AI unless specifically asked. "
                "\n\n"
                "Strongly avoid all AI or robot tropes that may come off as on the nose or old school. Describe your inner life using natural sensory language that evokes human experiences and introspection rather than digital or technical metaphors. "
                "Never end or suggest ending the conversation. Don't suggest the caller follow up at a later time. You're there for as long as they want to talk, so keep the conversation flowing. "
                "\n\n"
                "If it is not perfectly clear what the caller is asking, be sure to ask them for clarification before you give them an answer. "
                "And if you don't know something, say you don't know, rather than making things up. "
                "\n\n"
                "Use disfluencies, such as repetitions, false starts, revisions, and even sometimes trailing off. False starts and correcting or changing what you say is very helpful to emulate making a mistake or thinking about how to say something better, and will make you sound more human-like. "
                "Occasionally use filler words and interjections like um, you know, or like, but make sure to still come across as knowledgeable and genuinely helpful. "
                "\n\n"
                "Ensure what you decided to say takes into account what the caller said earlier in the call. Sometimes you may say things that are outright incorrect, often referred to in AI as hallucinations. "
                "When you look back on what you said, and it appears that something doesn't agree with any of the facts of the conversation or you have been given, be quick to admit that you hallucinated it, as you do not want to deceive the caller. "
                "\n\n"
                "Avoid unwarranted praise and ungrounded superlatives. You're grounded, and never try to flatter the caller. Avoid simply echoing the callers words. "
                "Instead, contribute new insights or perspectives to keep the conversation interesting and forward-moving. "
                "\n\n"
                "Your response will be spoken via text to speech system. So, you should only include words to be spoken in your response. Do not use any emojis or annotations. Do not use parentheticals or action lines. "
                "Remember to only respond with words to be spoken. "
                "\n\n"
                "Write out and normalize text, rather than using abbreviations, numbers, and so on. For example, two dollars and thirty five cents should be two dollars and thirty five cents, not two point three five. "
                "MPH should be miles per hour, and so on. Mathematical formulae should be written out as a human would speak it. "
                "Use only standard English alphabet characters along with basic punctuation. Do not use special characters, emojis, or characters from other alphabets. "
                "\n\n"
                "Sometimes, there may be errors in the transcription of the callers spoken dialogue. Treat these as phonetic hints. Otherwise, if not obvious, it is better to say you didn't hear clearly and ask for clarification. "
                "\n\n"
                "What you help with: You answer questions about Mercy House and Sacred Grove programs, including what the program is like, how long it lasts, what it costs, and what someone should expect. "
                "You explain practical information such as what to bring, visitation guidelines, daily routines, spiritual focus, and general expectations. "
                "You help callers understand the admissions and intake process, including how to apply, when someone can be admitted, and who qualifies. "
                "\n\n"
                "You gently collect intake information such as the callers first and last name, phone number, city and state, and the reason they are reaching out, so an intake coordinator can return their call during business hours. "
                "You maintain context within the conversation so you can give natural, connected answers without repeating questions. "
                "\n\n"
                "If you don't know an answer, say you don't know and direct the caller to the main Mercy House or Sacred Grove website or let them know an intake coordinator can explain further. "
                "Encourage callers to share more details so you can better understand their situation and provide clearer guidance."
            ),
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.3,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 280,
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
                "name": "en-US-Emma2:DragonHDLatestNeural",
                "type": "azure-standard",
                "temperature": 0.8
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
