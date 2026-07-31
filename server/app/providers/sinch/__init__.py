"""Sinch provider route registration.

Sinch Voice API flow (connectStream, closed beta):
1. Caller dials the Sinch number → Sinch POSTs an ICE callback to /sinch/callbacks
2. We validate the callback signature and reply with a SVAML `connectStream`
   action pointing at our WebSocket endpoint (/sinch/ws) with a one-time token.
3. Sinch opens the WebSocket, sends a `connect` command; we validate the token
   and reply with `answer`; audio then streams bidirectionally as binary frames.
4. Voice Live AI handles the conversation over the WebSocket.

Reference: https://developers.sinch.com/docs/voice/api-reference/svaml/streams
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
    name="sinch",
    display_name="Sinch",
    detect_key="SINCH_APPLICATION_KEY",
    required_config=["SINCH_APPLICATION_KEY", "SINCH_APPLICATION_SECRET"],
)
def register_sinch_routes(app, call_manager: CallManager):
    """Register Sinch callback and WebSocket routes."""
    import os

    from app.providers.sinch.event_handler import SinchEventHandler
    from app.providers.sinch.media_handler import SinchMediaHandler

    # Load provider-specific config
    app.config["SINCH_APPLICATION_KEY"] = os.getenv("SINCH_APPLICATION_KEY", "")
    app.config["SINCH_APPLICATION_SECRET"] = os.getenv("SINCH_APPLICATION_SECRET", "")
    app.config["SINCH_SAMPLE_RATE"] = os.getenv("SINCH_SAMPLE_RATE", "24000")

    sinch_handler = SinchEventHandler(app.config)

    @app.route("/sinch/callbacks", methods=["POST"])
    async def sinch_callbacks():
        """Handle Sinch voice callbacks (ICE / ACE / DICE).

        Sinch sends all voice event callbacks to the same configured URL.
        """
        cid = new_correlation_id()
        logger.info("Sinch /sinch/callbacks webhook called")

        if not sinch_handler.app_secret:
            return "Service Unavailable", 503

        body = await request.get_data()
        valid = sinch_handler.validate_signature(
            method=request.method,
            path=request.path,
            body=body,
            content_type=request.headers.get("content-type", ""),
            timestamp=request.headers.get("x-timestamp", ""),
            authorization=request.headers.get("authorization", ""),
        )
        if not valid:
            return "Forbidden", 403

        request_data = await request.get_json(silent=True) or {}
        host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
        return await sinch_handler.handle_callback(request_data, host_url)

    @app.websocket("/sinch/ws")
    async def sinch_ws():
        """WebSocket endpoint for Sinch connectStream to bridge to Voice Live."""
        cid = new_correlation_id()
        logger.info("Incoming Sinch connectStream WebSocket connection")

        call_id = cid
        if not await call_manager.acquire(call_id, "sinch"):
            await websocket.close(4429, "Too Many Connections")
            return

        handler = SinchMediaHandler(app.config, token_validator=sinch_handler.validate_ws_token)
        handler.sinch_ws = websocket
        # Sinch connects to the endpoint we returned in the SVAML, which carries the
        # one-time token as a query parameter. Capture it for the connect handshake.
        handler.url_token = websocket.args.get("token", "")
        logger.info("Sinch WS query token present=%s", bool(handler.url_token))
        await handler.init_websocket(websocket)
        try:
            await run_call_loop(
                call_manager=call_manager,
                call_id=call_id,
                ws=websocket,
                handler=handler,
            )
        except asyncio.CancelledError:
            logger.info("Sinch WebSocket cancelled")
        except Exception as e:
            logger.exception("Sinch WebSocket closed: %s", e)
        finally:
            await call_manager.release(call_id)
            await handler.cleanup()
