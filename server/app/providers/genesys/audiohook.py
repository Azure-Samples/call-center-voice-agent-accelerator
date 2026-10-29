"""Genesys AudioHook (Audio Connector / Monitor) protocol session.

Transport-agnostic: owns sequence numbers, media negotiation and control-message
acknowledgements. Handlers supply a `send` coroutine and react to the parsed
lifecycle via the returned message type and session state.

Reference: https://developer.genesys.cloud/devapps/audiohook
"""

import hmac
import json
import logging
import uuid
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

SendFn = Callable[[str], Awaitable[None]]


def validate_api_key(expected: str, provided: str) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


def select_media(offers: list[dict], preferred_channels: list[list[str]]) -> dict | None:
    """Pick a PCMU offer matching the first preferred channel layout, else first PCMU."""
    pcmu = [o for o in offers if o.get("format") == "PCMU"]
    for channels in preferred_channels:
        for offer in pcmu:
            if offer.get("channels") == channels:
                return offer
    return pcmu[0] if pcmu else None


class AudioHookSession:
    """Protocol state for one AudioHook WebSocket."""

    def __init__(self, send: SendFn, preferred_channels: list[list[str]], log_prefix: str = "[AudioHook]"):
        self._send = send
        self.preferred_channels = preferred_channels
        self.log_prefix = log_prefix
        self.session_id: str | None = None
        self.conversation_id: str | None = None
        self.participant: dict = {}
        self.media: dict | None = None
        self.client_seq = 0
        self.server_seq = 0
        self.is_open = False
        self.paused = False

    @property
    def channels(self) -> list[str]:
        return list((self.media or {}).get("channels") or [])

    async def handle_text(self, message: str) -> str | None:
        """Process a JSON control message. Returns its type (or None if unparseable)."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("%s Non-JSON text message: %s", self.log_prefix, message[:200])
            return None

        msg_type = data.get("type")
        self.client_seq = data.get("seq", self.client_seq)
        self.session_id = data.get("id", self.session_id)

        match msg_type:
            case "open":
                await self._handle_open(data)
            case "ping":
                await self._reply("pong")
            case "close":
                logger.info("%s Close received for session %s", self.log_prefix, self.session_id)
                self.is_open = False
                await self._reply("closed", {})
            case "update":
                logger.info("%s Update received: %s", self.log_prefix, data.get("parameters", {}))
                await self._reply("updated", {})
            case "pause":
                logger.info("%s Paused", self.log_prefix)
                self.paused = True
                await self._reply("paused")
            case "resume":
                logger.info("%s Resumed", self.log_prefix)
                self.paused = False
                await self._reply("resumed")
            case _:
                logger.info("%s Unknown message type: %s", self.log_prefix, msg_type)
        return msg_type

    async def _handle_open(self, data: dict) -> None:
        params = data.get("parameters", {})
        self.conversation_id = params.get("conversationId", "")
        self.participant = params.get("participant", {})
        logger.info(
            "%s Open: session=%s conversation=%s participant=%s",
            self.log_prefix, self.session_id, self.conversation_id,
            self.participant.get("ani", "unknown"),
        )
        media = select_media(params.get("media", []), self.preferred_channels)
        if not media:
            logger.error("%s No compatible media format offered", self.log_prefix)
            await self.send_disconnect("error", "No compatible media format")
            return
        self.media = media
        await self._reply("opened", {"startPaused": False, "media": [media]})
        self.is_open = True
        logger.info(
            "%s Session opened: format=%s rate=%s channels=%s",
            self.log_prefix, media.get("format"), media.get("rate"), media.get("channels"),
        )

    async def send_disconnect(self, reason: str, info: str = "") -> None:
        self.server_seq += 1
        await self._send(json.dumps({
            "version": "2",
            "type": "disconnect",
            "seq": self.server_seq,
            "clientseq": self.client_seq,
            "id": self.session_id or str(uuid.uuid4()),
            "parameters": {"reason": reason, "info": info},
        }))

    async def _reply(self, msg_type: str, parameters: dict | None = None) -> None:
        self.server_seq += 1
        msg = {
            "version": "2",
            "type": msg_type,
            "seq": self.server_seq,
            "clientseq": self.client_seq,
            "id": self.session_id,
        }
        if parameters is not None:
            msg["parameters"] = parameters
        await self._send(json.dumps(msg))
