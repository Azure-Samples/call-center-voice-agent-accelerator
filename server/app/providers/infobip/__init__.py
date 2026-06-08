"""Infobip provider route registration."""

import asyncio
import logging

from quart import request, websocket

from app.call_manager import CallManager
from app.logging_config import new_correlation_id
from app.provider_registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(
    name="infobip",
    display_name="Infobip",
    detect_key="INFOBIP_API_KEY",
    required_config=["INFOBIP_API_KEY"],
)
def register_infobip_routes(app, call_manager: CallManager):
    """Register Infobip webhook and WebSocket routes."""
    import os

    from app.providers.infobip.event_handler import InfobipEventHandler
    from app.providers.infobip.media_handler import InfobipMediaHandler

    # Load provider-specific config
    app.config["INFOBIP_API_KEY"] = os.getenv("INFOBIP_API_KEY", "")
    app.config["INFOBIP_API_BASE_URL"] = os.getenv("INFOBIP_API_BASE_URL", "")

    infobip_handler = InfobipEventHandler(app.config)

    @app.route("/infobip/incoming", methods=["POST"])
    async def infobip_incoming_call():
        """Handles incoming Infobip voice call webhooks."""
        cid = new_correlation_id()
        logger.info("Infobip /infobip/incoming webhook called")

        if not infobip_handler.api_key:
            return "Service Unavailable", 503

        request_data = await request.get_json()
        host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
        return await infobip_handler.handle_incoming_call(request_data, host_url)

    @app.websocket("/infobip/ws")
    async def infobip_ws():
        """WebSocket endpoint for Infobip WEBSOCKET call legs to bridge to Voice Live."""
        cid = new_correlation_id()
        logger.info("Incoming Infobip WebSocket connection")

        call_id = cid
        if not await call_manager.acquire(call_id, "infobip"):
            await websocket.close(4429, "Too Many Connections")
            return

        handler = InfobipMediaHandler(app.config, token_validator=infobip_handler.validate_ws_token)
        handler.infobip_ws = websocket
        await handler.init_websocket(websocket)
        voicelive_task = None
        try:
            voicelive_task = asyncio.create_task(handler.connect_voicelive())
            while True:
                if call_manager.is_expired(call_id):
                    logger.warning("Call expired, disconnecting: call_id=%s", call_id)
                    break
                msg = await websocket.receive()
                call_manager.touch(call_id)
                await handler.handle_infobip_message(msg)
        except asyncio.CancelledError:
            logger.info("Infobip WebSocket cancelled")
        except Exception as e:
            logger.exception("Infobip WebSocket closed: %s", e)
        finally:
            if voicelive_task:
                voicelive_task.cancel()
            logger.info(
                "Infobip WebSocket ending — frames in=%d out=%d voicelive_connected=%s",
                handler._in_frame_count,
                handler._out_frame_count,
                handler._voicelive_connected,
            )
            await call_manager.release(call_id)
            await handler._cleanup()
