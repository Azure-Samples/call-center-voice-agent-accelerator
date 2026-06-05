"""Handler for Infobip voice webhook events and call lifecycle.

Infobip Calls API flow:
1. Receive CALL_RECEIVED webhook → answer the call
2. Receive CALL_ESTABLISHED webhook → create Dialog (bridges caller to WebSocket endpoint)
3. Voice Live AI handles the conversation over the WebSocket
"""

import logging
import secrets

import aiohttp
from quart import Response

logger = logging.getLogger(__name__)


class InfobipEventHandler:
    """Handles Infobip webhook events and manages call lifecycle via API."""

    def __init__(self, config):
        self.api_key = config.get("INFOBIP_API_KEY", "")
        self.api_base_url = config.get("INFOBIP_API_BASE_URL", "").rstrip("/")
        self.media_stream_config_id = ""
        self._answered_calls = set()
        self._pending_media_streams = {}
        self._valid_ws_tokens = set()  # one-time tokens for WebSocket auth

    def validate_ws_token(self, token: str) -> bool:
        """Validate and consume a one-time WebSocket token.

        Returns True if the token is valid (and removes it to prevent reuse).
        """
        if token in self._valid_ws_tokens:
            self._valid_ws_tokens.discard(token)
            return True
        return False

    async def discover_media_stream_config(self, host_url: str) -> None:
        """Auto-discover media stream config ID by matching the WebSocket URL.

        Calls GET /calls/1/media-stream-configs and finds the config whose URL
        matches our /infobip/ws endpoint. Skipped if already discovered.
        """
        if self.media_stream_config_id:
            return

        ws_url = host_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/infobip/ws"
        url = self._build_url(self.api_base_url, "/calls/1/media-stream-configs")
        logger.info("[InfobipEventHandler] Discovering media stream config for: %s", ws_url)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers()) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            "[InfobipEventHandler] Failed to list media stream configs: status=%s, body=%s",
                            resp.status, body,
                        )
                        return

                    data = await resp.json()
                    results = data.get("results", [])
                    for config in results:
                        if config.get("url", "").rstrip("/") == ws_url:
                            self.media_stream_config_id = config["id"]
                            logger.info(
                                "[InfobipEventHandler] Auto-discovered media stream config: id=%s, name=%s",
                                config["id"], config.get("name", ""),
                            )
                            return

                    logger.warning(
                        "[InfobipEventHandler] No media stream config found matching URL: %s. "
                        "Available configs: %s",
                        ws_url, [c.get("url") for c in results],
                    )
        except Exception as e:
            logger.error("[InfobipEventHandler] Error discovering media stream config: %s", e)

    def _headers(self) -> dict:
        """Return standard Infobip API headers."""
        return {
            "Authorization": f"App {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_url(self, base: str, path: str) -> str:
        """Build an Infobip API URL with the piIntegrator tracking query parameter."""
        return f"{base}{path}?piIntegrator=mpj4"

    async def _answer_call(self, call_id: str) -> bool:
        """Answer an incoming call via Infobip API."""
        url = self._build_url(self.api_base_url, f"/calls/1/calls/{call_id}/answer")
        logger.info("[InfobipEventHandler] Answering call: %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json={}) as resp:
                if resp.status in (200, 201):
                    logger.info("[InfobipEventHandler] Call answered: callId=%s", call_id)
                    return True
                else:
                    body = await resp.text()
                    logger.error(
                        "[InfobipEventHandler] Failed to answer call: status=%s, body=%s",
                        resp.status, body,
                    )
                    return False

    async def _create_dialog(self, call_id: str, api_base: str = None) -> bool:
        """Create a Dialog to bridge the call to a WebSocket endpoint."""
        base = api_base or self.api_base_url
        url = self._build_url(base, "/calls/1/dialogs")

        # Generate a one-time token for WebSocket authentication
        ws_token = secrets.token_urlsafe(32)
        self._valid_ws_tokens.add(ws_token)

        payload = {
            "parentCallId": call_id,
            "childCallRequest": {
                "endpoint": {
                    "type": "WEBSOCKET",
                    "websocketEndpointConfigId": self.media_stream_config_id,
                },
                "customData": {
                    "ws_token": ws_token,
                },
            },
        }
        logger.info(
            "[InfobipEventHandler] Creating dialog: callId=%s, configId=%s",
            call_id, self.media_stream_config_id,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json=payload) as resp:
                body = await resp.text()
                if resp.status in (200, 201):
                    logger.info(
                        "[InfobipEventHandler] Dialog created: callId=%s, response=%s",
                        call_id, body,
                    )
                    return True
                else:
                    logger.error(
                        "[InfobipEventHandler] Failed to create dialog: status=%s, body=%s",
                        resp.status, body,
                    )
                    return False

    async def handle_incoming_call(self, request_data: dict, host_url: str) -> Response:
        """Handle all Infobip call webhooks (incoming + state changes).

        Infobip sends all events to the same configured URL.
        Dispatches based on event type in the payload.
        """
        logger.info("[InfobipEventHandler] Webhook payload: %s", request_data)

        # Auto-discover media stream config ID if not set
        if not self.media_stream_config_id:
            await self.discover_media_stream_config(host_url)

        event_type = request_data.get("type", "")
        call_id = request_data.get("callId", "")

        # Route based on event type
        if event_type == "CALL_RECEIVED":
            return await self._handle_call_received(call_id, request_data, host_url)
        elif event_type == "CALL_ESTABLISHED":
            return await self._handle_call_established(call_id, request_data)
        elif event_type in ("CALL_FINISHED", "CALL_FAILED"):
            return await self._handle_call_ended(call_id, request_data)
        else:
            logger.info("[InfobipEventHandler] Unhandled event type: %s", event_type)
            return Response(status=200)

    async def _handle_call_received(self, call_id: str, request_data: dict, host_url: str) -> Response:
        """Handle CALL_RECEIVED: answer the call."""
        props = request_data.get("properties", {}).get("call", {})
        caller = props.get("from", "unknown")
        logger.info(
            "[InfobipEventHandler] Incoming call: callId=%s, from=%s", call_id, caller
        )

        if not call_id:
            logger.error("[InfobipEventHandler] No callId in incoming call event")
            return Response(status=400)

        # Deduplicate — Infobip retries the webhook if response is slow
        if call_id in self._answered_calls:
            logger.info("[InfobipEventHandler] Already handled callId=%s, returning 200", call_id)
            return Response(status=200)

        self._answered_calls.add(call_id)

        # Store API base for later (when ESTABLISHED event arrives)
        api_base = request_data.get("properties", {}).get("apiBaseUrl", self.api_base_url).rstrip("/")
        self._pending_media_streams[call_id] = {"api_base": api_base}
        logger.info("[InfobipEventHandler] Pending call: callId=%s, apiBase=%s", call_id, api_base)

        # Answer the call — media stream starts after ESTABLISHED event
        answered = await self._answer_call(call_id)
        if not answered:
            self._answered_calls.discard(call_id)
            self._pending_media_streams.pop(call_id, None)
            return Response(status=500)

        return Response(status=200)

    async def _handle_call_established(self, call_id: str, request_data: dict) -> Response:
        """Handle CALL_ESTABLISHED: create dialog to bridge caller to WebSocket."""
        logger.info(
            "[InfobipEventHandler] Call established: callId=%s", call_id,
        )
        pending = self._pending_media_streams.pop(call_id, None)
        if pending:
            api_base = pending["api_base"]
            await self._create_dialog(call_id, api_base)
        else:
            logger.warning("[InfobipEventHandler] No pending media stream for callId=%s", call_id)
        return Response(status=200)

    async def _handle_call_ended(self, call_id: str, request_data: dict) -> Response:
        """Handle CALL_FINISHED/CALL_FAILED: clean up."""
        event_type = request_data.get("type", "")
        logger.info(
            "[InfobipEventHandler] Call ended: type=%s, callId=%s", event_type, call_id,
        )
        self._answered_calls.discard(call_id)
        self._pending_media_streams.pop(call_id, None)
        return Response(status=200)
