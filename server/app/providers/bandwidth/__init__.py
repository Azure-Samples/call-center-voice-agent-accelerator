"""Bandwidth Programmable Voice provider route registration.

Bandwidth Programmable Voice flow:
1. Caller dials a Bandwidth number → Bandwidth POSTs a voice callback to /bandwidth/incoming
2. We respond with BXML that opens a bidirectional <StartStream> to /bandwidth/ws
3. Bandwidth connects the WebSocket and streams PCMU 8kHz audio (base64 JSON frames)
4. We bridge that audio to Azure Voice Live and send playAudio events back
"""

import asyncio
import logging

from quart import request, websocket

from app.call_loop import run_call_loop
from app.call_manager import CallManager
from app.logging_config import new_correlation_id
from app.provider_registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(
    name="bandwidth",
    display_name="Bandwidth",
    detect_key="BANDWIDTH_CLIENT_ID",
    required_config=[
        "BANDWIDTH_CLIENT_ID",
        "BANDWIDTH_CLIENT_SECRET",
        "BANDWIDTH_ACCOUNT_ID",
    ],
)
def register_bandwidth_routes(app, call_manager: CallManager):
    """Register Bandwidth voice callback and WebSocket routes."""
    import os

    from app.providers.bandwidth.event_handler import BandwidthEventHandler
    from app.providers.bandwidth.media_handler import BandwidthMediaHandler

    # Load provider-specific config. Bandwidth uses OAuth 2.0 Client Credentials
    # (Client ID / Client Secret); the legacy API User (username/password) scheme
    # is deprecated and can no longer be provisioned on new accounts.
    app.config["BANDWIDTH_ACCOUNT_ID"] = os.getenv("BANDWIDTH_ACCOUNT_ID", "")
    app.config["BANDWIDTH_CLIENT_ID"] = os.getenv("BANDWIDTH_CLIENT_ID", "")
    app.config["BANDWIDTH_CLIENT_SECRET"] = os.getenv("BANDWIDTH_CLIENT_SECRET", "")
    app.config["BANDWIDTH_APPLICATION_ID"] = os.getenv("BANDWIDTH_APPLICATION_ID", "")

    bandwidth_handler = BandwidthEventHandler(app.config)

    @app.route("/bandwidth/incoming", methods=["POST"])
    async def bandwidth_incoming_call():
        """Handles incoming Bandwidth voice callbacks and returns streaming BXML."""
        new_correlation_id()
        logger.info("Bandwidth /bandwidth/incoming callback called")

        # Validate the webhook Basic Auth credentials (if configured).
        auth_header = request.headers.get("Authorization", "")
        valid = bandwidth_handler.validate_webhook(auth_header)
        if valid is None:
            return "Service Unavailable", 503
        if not valid:
            return "Unauthorized", 401

        try:
            request_data = await request.get_json(force=True, silent=True) or {}
        except Exception:
            request_data = {}

        host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
        ws_url = host_url.replace("https://", "wss://") + "/bandwidth/ws"
        bxml = bandwidth_handler.handle_voice_callback(request_data, ws_url)
        return bxml, 200, {"Content-Type": "application/xml"}

    @app.websocket("/bandwidth/ws")
    async def bandwidth_ws():
        """WebSocket endpoint for Bandwidth media streams to bridge to Voice Live."""
        cid = new_correlation_id()
        logger.info("Incoming Bandwidth media stream WebSocket connection")

        handler = BandwidthMediaHandler(app.config)
        handler.bandwidth_ws = websocket
        handler.correlation_id = cid

        if not await handler.authenticate_and_start():
            return

        call_id = handler.stream_id or cid
        if not await call_manager.acquire(call_id, "bandwidth"):
            await websocket.close(4429, "Too Many Connections")
            return

        await handler.init_websocket(websocket)
        try:
            await run_call_loop(
                call_manager=call_manager,
                call_id=call_id,
                ws=websocket,
                handler=handler,
            )
        except asyncio.CancelledError:
            logger.info("Bandwidth WebSocket cancelled")
        except Exception:
            logger.exception("Bandwidth WebSocket connection closed")
        finally:
            await call_manager.release(call_id)
            await handler.cleanup()
