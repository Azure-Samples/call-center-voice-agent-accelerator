"""Plivo provider route registration."""

import asyncio
import logging

from quart import request, websocket

from app.call_loop import run_call_loop
from app.call_manager import CallManager
from app.logging_config import new_correlation_id
from app.provider_registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(
    name="plivo",
    display_name="Plivo",
    detect_key="PLIVO_AUTH_TOKEN",
    required_config=["PLIVO_AUTH_TOKEN"],
)
def register_plivo_routes(app, call_manager: CallManager):
    """Register Plivo answer webhook and Audio Streaming WebSocket routes."""
    import os

    from app.providers.plivo.event_handler import PlivoEventHandler
    from app.providers.plivo.media_handler import PlivoMediaHandler

    # Load provider-specific config
    app.config["PLIVO_AUTH_TOKEN"] = os.getenv("PLIVO_AUTH_TOKEN", "")

    plivo_handler = PlivoEventHandler(app.config)

    @app.route("/plivo/answer", methods=["GET", "POST"])
    async def plivo_answer():
        """Handles incoming Plivo calls, returning Audio Streaming XML."""
        new_correlation_id()
        logger.info("Plivo /plivo/answer webhook called")

        params = (
            dict(await request.form) if request.method == "POST" else dict(request.args)
        )
        valid = plivo_handler.validate_request(
            request.method, request.url, params, request.headers
        )
        if valid is None:
            return "Service Unavailable", 503
        if not valid:
            return "Forbidden", 403

        host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
        ws_url = host_url.replace("https://", "wss://") + "/plivo/ws"
        status_url = host_url + "/plivo/stream-status"
        xml = plivo_handler.generate_stream_xml(ws_url, status_url)
        return xml, 200, {"Content-Type": "text/xml"}

    @app.route("/plivo/stream-status", methods=["POST"])
    async def plivo_stream_status():
        """Receives Plivo audio stream status callbacks (started/stopped/failed)."""
        params = dict(await request.form)
        event = params.get("Event")
        # A "failed" stream is a real problem worth surfacing; log it louder.
        # Teardown is not driven from here — the WebSocket close handles that.
        log = logger.warning if event == "failed" else logger.info
        log(
            "Plivo stream status: event=%s streamId=%s callId=%s reason=%s",
            event,
            params.get("StreamID"),
            params.get("CallUUID"),
            params.get("StatusReason"),
        )
        return "", 200

    @app.websocket("/plivo/ws")
    async def plivo_ws():
        """WebSocket endpoint for Plivo Audio Streaming to bridge to Voice Live."""
        cid = new_correlation_id()
        logger.info("Incoming Plivo Audio Streaming WebSocket connection")

        handler = PlivoMediaHandler(app.config)
        handler.plivo_ws = websocket
        handler.correlation_id = cid
        await handler.init_websocket(websocket)

        if not await handler.authenticate_and_start():
            return

        # Use the server-generated correlation id as the call key (collision-proof);
        # handler.stream_id remains available as Plivo protocol metadata.
        call_id = cid
        if not await call_manager.acquire(call_id, "plivo"):
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
            logger.info("Plivo WebSocket cancelled")
        except Exception:
            logger.exception("Plivo WebSocket connection closed")
        finally:
            await call_manager.release(call_id)
            await handler.cleanup()
