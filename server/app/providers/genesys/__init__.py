"""Genesys AudioHook provider route registration."""

import asyncio
import logging
from pathlib import Path

from quart import send_file, websocket

from app.call_loop import run_call_loop
from app.call_manager import CallManager
from app.logging_config import new_correlation_id
from app.provider_registry import register_provider

logger = logging.getLogger(__name__)

_PROVIDER_DIR = Path(__file__).parent


@register_provider(
    name="genesys",
    display_name="Genesys AudioHook",
    detect_key="GENESYS_API_KEY",
    required_config=["GENESYS_API_KEY"],
)
def register_genesys_routes(app, call_manager: CallManager):
    """Register Genesys AudioHook WebSocket routes."""
    import os

    from app.providers.genesys.media_handler import GenesysMediaHandler

    # Load provider-specific config
    app.config["GENESYS_API_KEY"] = os.getenv("GENESYS_API_KEY", "")

    @app.route("/genesys")
    async def genesys_simulator():
        """Serves the Genesys AudioHook client simulator page."""
        return await send_file(_PROVIDER_DIR / "simulator.html")

    @app.websocket("/audiohook/ws")
    async def genesys_ws():
        """WebSocket endpoint for Genesys AudioHook Audio Connector."""
        cid = new_correlation_id()
        logger.info("Incoming Genesys AudioHook WebSocket connection")

        # Validate API key: check X-API-KEY header (real Genesys) or query param (simulator)
        provided_key = websocket.headers.get("X-API-KEY", "") or websocket.args.get("apikey", "")
        handler = GenesysMediaHandler(app.config)
        if not handler.validate_api_key(provided_key):
            logger.warning("Invalid API key — rejecting connection")
            await websocket.accept()
            await websocket.close(4403, "Invalid API key")
            return

        call_id = cid
        if not await call_manager.acquire(call_id, "genesys"):
            await websocket.accept()
            await websocket.close(4429, "Too Many Connections")
            return

        handler.genesys_ws = websocket
        await handler.init_websocket(websocket)
        try:
            await run_call_loop(
                call_manager=call_manager,
                call_id=call_id,
                ws=websocket,
                handler=handler,
            )
        except asyncio.CancelledError:
            logger.info("Genesys WebSocket cancelled")
        except Exception as e:
            logger.exception("Genesys WebSocket closed: %s", e)
        finally:
            await call_manager.release(call_id)
            await handler.cleanup()
