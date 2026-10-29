"""HTTP/WebSocket routes for transcription mode (agent view + browser harness)."""

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Callable

from quart import request, send_file, websocket

from app.call_loop import run_call_loop
from app.call_manager import CallManager
from app.logging_config import new_correlation_id
from app.transcription.backend import create_backend
from app.transcription.transcript_hub import TranscriptHub, get_hub
from app.transcription.web_handler import WebTranscriptionHandler

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

_CID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _token_from(req) -> str:
    return req.args.get("token", "") or req.headers.get("X-Web-Token", "")


def register_transcription_routes(
    app,
    call_manager: CallManager,
    validate_token: Callable[[str], bool],
    hub: TranscriptHub | None = None,
    backend_factory=create_backend,
) -> None:
    hub = hub or get_hub()

    @app.route("/transcript")
    async def transcript_page():
        return await send_file(STATIC_DIR / "transcript.html")

    @app.route("/transcribe-test")
    async def transcribe_test_page():
        return await send_file(STATIC_DIR / "transcribe-test.html")

    @app.route("/transcript/active")
    async def transcript_active():
        if not validate_token(_token_from(request)):
            return {"error": "invalid token"}, 403
        return {"conversations": hub.active()}

    @app.websocket("/transcript/ws")
    async def transcript_ws():
        if not validate_token(_token_from(websocket)):
            await websocket.accept()
            await websocket.close(4403, "Invalid token")
            return
        cid = websocket.args.get("conversationId", "")
        if not _CID_RE.match(cid):
            await websocket.accept()
            await websocket.close(4400, "conversationId required")
            return
        await websocket.accept()
        q = hub.subscribe(cid)
        try:
            while True:
                event = await q.get()
                await websocket.send(json.dumps(event.to_dict()))
        except asyncio.CancelledError:
            pass
        finally:
            hub.unsubscribe(cid, q)

    @app.websocket("/web/transcribe/ws")
    async def web_transcribe_ws():
        call_id = new_correlation_id()
        if not validate_token(_token_from(websocket)):
            await websocket.accept()
            await websocket.close(4403, "Invalid token")
            return
        cid = websocket.args.get("conversationId", "") or f"web-{uuid.uuid4().hex[:8]}"
        if not _CID_RE.match(cid):
            await websocket.accept()
            await websocket.close(4400, "Invalid conversationId")
            return
        if not await call_manager.acquire(call_id, "web-transcribe"):
            await websocket.accept()
            await websocket.close(4429, "Too Many Connections")
            return
        logger.info("Web transcription session conversation=%s", cid)
        handler = WebTranscriptionHandler(app.config, cid, hub=hub, backend_factory=backend_factory)
        try:
            await websocket.accept()
            await websocket.send(json.dumps({"type": "session", "conversationId": cid}))
            await run_call_loop(call_manager=call_manager, call_id=call_id, ws=websocket, handler=handler)
        except asyncio.CancelledError:
            logger.info("Web transcription WebSocket cancelled")
        except Exception:
            logger.exception("Web transcription WebSocket closed")
        finally:
            await call_manager.release(call_id)
            await handler.cleanup()
