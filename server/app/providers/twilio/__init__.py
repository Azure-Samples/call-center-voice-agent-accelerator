"""Twilio provider route registration."""

import asyncio
import logging

from quart import request, websocket

from app.call_loop import run_call_loop
from app.call_manager import CallManager
from app.logging_config import new_correlation_id
from app.provider_registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(
    name="twilio",
    display_name="Twilio",
    detect_key="TWILIO_AUTH_TOKEN",
    required_config=["TWILIO_AUTH_TOKEN"],
)
def register_twilio_routes(app, call_manager: CallManager):
    """Register Twilio webhook and WebSocket routes."""
    import os

    from app.providers.twilio.event_handler import TwilioEventHandler
    from app.providers.twilio.media_handler import TwilioMediaHandler

    # Load provider-specific config
    app.config["TWILIO_AUTH_TOKEN"] = os.getenv("TWILIO_AUTH_TOKEN", "")

    twilio_handler = TwilioEventHandler(app.config)

    @app.route("/voice", methods=["GET", "POST"])
    async def twilio_voice():
        """Handles incoming Twilio phone calls with bidirectional media stream."""
        cid = new_correlation_id()
        logger.info("Twilio /voice webhook called")

        signature = request.headers.get("X-Twilio-Signature", "")
        params = dict(await request.form) if request.method == "POST" else {}
        valid = twilio_handler.validate_request(request.url, params, signature)
        if valid is None:
            return "Service Unavailable", 503
        if not valid:
            return "Forbidden", 403

        host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
        ws_url = host_url.replace("https://", "wss://") + "/twilio/ws"
        twiml = twilio_handler.generate_stream_twiml(ws_url)
        return twiml, 200, {"Content-Type": "text/xml"}

    @app.websocket("/twilio/ws")
    async def twilio_ws():
        """WebSocket endpoint for Twilio Media Streams to bridge to Voice Live."""
        cid = new_correlation_id()
        logger.info("Incoming Twilio Media Stream WebSocket connection")

        handler = TwilioMediaHandler(app.config)
        handler.twilio_ws = websocket
        handler.correlation_id = cid

        if not await handler.authenticate_and_start():
            return

        call_id = handler.stream_sid or cid
        if not await call_manager.acquire(call_id, "twilio"):
            await websocket.close(4429, "Too Many Connections")
            return

        try:
            await run_call_loop(
                call_manager=call_manager,
                call_id=call_id,
                ws=websocket,
                handler=handler,
            )
        except asyncio.CancelledError:
            logger.info("Twilio WebSocket cancelled")
        except Exception:
            logger.exception("Twilio WebSocket connection closed")
        finally:
            await call_manager.release(call_id)
            await handler.cleanup()
