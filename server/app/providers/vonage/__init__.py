"""Vonage provider route registration.

Vonage Voice API flow (WebSocket media streaming):
1. Caller dials the Vonage number → Vonage requests the Answer URL /vonage/answer.
2. We return an NCCO with a `connect` action pointing at our WebSocket endpoint
   (/vonage/ws) with a one-time token on the query string and content-type
   audio/l16;rate=24000.
3. Vonage opens the WebSocket, sends a text `websocket:connected` event, then
   streams raw PCM 24kHz audio as binary frames in both directions.
4. Voice Live AI handles the conversation over the WebSocket.

Reference: https://developer.vonage.com/en/voice/voice-api/guides/websockets
"""

import asyncio
import logging

from quart import Response, request, websocket

from app.call_loop import run_call_loop
from app.call_manager import CallManager
from app.logging_config import new_correlation_id
from app.provider_registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(
    name="vonage",
    display_name="Vonage",
    detect_key="VONAGE_API_KEY",
    required_config=["VONAGE_API_KEY", "VONAGE_API_SECRET"],
)
def register_vonage_routes(app, call_manager: CallManager):
    """Register Vonage answer/event webhooks and the WebSocket media route."""
    import os

    from app.providers.vonage.event_handler import VonageEventHandler
    from app.providers.vonage.media_handler import VonageMediaHandler

    # Load provider-specific config
    app.config["VONAGE_API_KEY"] = os.getenv("VONAGE_API_KEY", "")
    app.config["VONAGE_API_SECRET"] = os.getenv("VONAGE_API_SECRET", "")
    app.config["VONAGE_APPLICATION_ID"] = os.getenv("VONAGE_APPLICATION_ID", "")

    vonage_handler = VonageEventHandler(app.config)

    # Vonage requests the Answer URL with GET by default, but the dashboard also
    # allows POST — accept both so configuration mistakes still connect.
    @app.route("/vonage/answer", methods=["GET", "POST"])
    async def vonage_answer():
        """Return the NCCO that bridges the caller to our WebSocket endpoint."""
        cid = new_correlation_id()
        logger.info("Vonage /vonage/answer webhook called")

        if not vonage_handler.api_key:
            return "Service Unavailable", 503

        host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
        ncco = vonage_handler.generate_answer_ncco(host_url)
        return Response(ncco, status=200, content_type="application/json")

    @app.route("/vonage/events", methods=["POST"])
    async def vonage_events():
        """Receive Vonage call lifecycle events (answered, disconnected, ...)."""
        logger.info("Vonage /vonage/events webhook called")

        data = await request.get_json(silent=True) or {}
        logger.info("[VonageEventHandler] Event: %s", data.get("status", data))
        return Response(status=200)

    @app.websocket("/vonage/ws")
    async def vonage_ws():
        """WebSocket endpoint for Vonage media streaming bridged to Voice Live."""
        cid = new_correlation_id()
        logger.info("Incoming Vonage media WebSocket connection")

        call_id = cid
        if not await call_manager.acquire(call_id, "vonage"):
            await websocket.close(4429, "Too Many Connections")
            return

        handler = VonageMediaHandler(app.config, token_validator=vonage_handler.validate_ws_token)
        handler.vonage_ws = websocket
        # Vonage connects to the endpoint we returned in the NCCO, which carries
        # the one-time token as a query parameter. Capture it for validation.
        handler.url_token = websocket.args.get("token", "")
        logger.info("Vonage WS query token present=%s", bool(handler.url_token))
        await handler.init_websocket(websocket)
        try:
            await run_call_loop(
                call_manager=call_manager,
                call_id=call_id,
                ws=websocket,
                handler=handler,
            )
        except asyncio.CancelledError:
            logger.info("Vonage WebSocket cancelled")
        except Exception as e:
            logger.exception("Vonage WebSocket closed: %s", e)
        finally:
            await call_manager.release(call_id)
            await handler.cleanup()
