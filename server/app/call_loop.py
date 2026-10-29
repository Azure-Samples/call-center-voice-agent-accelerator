"""WebSocket call loop orchestration — owns the handler background task lifecycle."""

import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

from app.call_manager import CallManager

logger = logging.getLogger(__name__)


@runtime_checkable
class CallHandler(Protocol):
    """Contract for handlers used with run_call_loop.

    Voice-agent providers subclass VoiceLiveMediaHandler (whose start()
    connects to Voice Live); transcription handlers implement start() directly.
    """

    async def start(self) -> None: ...
    async def on_message(self, msg: Any) -> None: ...


async def run_call_loop(
    call_manager: CallManager,
    call_id: str,
    ws: Any,
    handler: CallHandler,
) -> None:
    """Run the WebSocket receive loop with zombie detection.

    Owns only the handler's start() background task. The caller retains ownership
    of call_manager.acquire/release and handler cleanup.

    Args:
        call_manager: Provides timeout config and activity tracking.
        call_id: The call identifier (already acquired by caller).
        ws: The WebSocket connection.
        handler: Implements start() and on_message().
    """
    start_task = asyncio.create_task(handler.start())
    start_task.add_done_callback(
        lambda t: logger.error("Handler start failed: %s", t.exception())
        if not t.cancelled() and t.exception()
        else None
    )
    try:
        while True:
            if start_task.done() and not start_task.cancelled() and start_task.exception():
                logger.warning("Handler start failed, ending call: call_id=%s", call_id)
                break
            if call_manager.is_expired(call_id):
                logger.warning("Call expired, disconnecting: call_id=%s", call_id)
                break
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=call_manager.receive_timeout)
            except TimeoutError:
                continue
            call_manager.touch(call_id)
            await handler.on_message(msg)
    finally:
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass  # Expected — we just cancelled it.
        except Exception:
            pass  # Already logged by add_done_callback above.
