"""ACS (Azure Communication Services) provider route registration."""

import asyncio
import logging

from quart import request, websocket

from app.call_manager import CallManager
from app.logging_config import new_correlation_id
from app.provider_registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(
    name="acs",
    display_name="Azure Communication Services",
    detect_key="ACS_CONNECTION_STRING",
    required_config=["ACS_CONNECTION_STRING"],
)
def register_acs_routes(app, call_manager: CallManager):
    """Register ACS webhook and WebSocket routes."""
    import os

    from app.providers.acs.event_handler import AcsEventHandler
    from app.providers.acs.media_handler import ACSMediaHandler

    # Load provider-specific config
    app.config["ACS_CONNECTION_STRING"] = os.getenv("ACS_CONNECTION_STRING")
    # ACS_DEV_TUNNEL: local dev only — overrides callback URL for devtunnel/ngrok.
    # Not needed for azd up (Container Apps uses its own ingress URL).
    app.config["ACS_DEV_TUNNEL"] = os.getenv("ACS_DEV_TUNNEL", "")

    acs_handler = AcsEventHandler(app.config)

    @app.route("/acs/incomingcall", methods=["POST"])
    async def incoming_call_handler():
        """Handles initial incoming call event from EventGrid."""
        cid = new_correlation_id()
        logger.info("ACS incoming call event")
        events = await request.get_json()
        host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
        return await acs_handler.process_incoming_call(events, host_url, app.config)

    @app.route("/acs/callbacks/<context_id>", methods=["POST"])
    async def acs_event_callbacks(context_id):
        """Handles ACS event callbacks for call connection and streaming events."""
        raw_events = await request.get_json()
        return await acs_handler.process_callback_events(raw_events)

    @app.websocket("/acs/ws")
    async def acs_ws():
        """WebSocket endpoint for ACS to send audio to Voice Live."""
        cid = new_correlation_id()
        logger.info("Incoming ACS WebSocket connection")

        call_id = cid
        if not await call_manager.acquire(call_id, "acs"):
            await websocket.close(4429, "Too Many Connections")
            return

        handler = ACSMediaHandler(app.config)
        await handler.init_websocket(websocket)
        try:
            asyncio.create_task(handler.connect_voicelive())
            while True:
                if call_manager.is_expired(call_id):
                    logger.warning("Call expired, disconnecting: call_id=%s", call_id)
                    break
                msg = await websocket.receive()
                call_manager.touch(call_id)
                await handler.handle_audio(msg)
        except asyncio.CancelledError:
            logger.info("ACS WebSocket cancelled")
        except Exception:
            logger.exception("ACS WebSocket connection closed")
        finally:
            await call_manager.release(call_id)
            await handler._cleanup()
