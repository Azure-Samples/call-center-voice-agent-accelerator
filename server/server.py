import asyncio
import logging
import os

from dotenv import load_dotenv
from quart import Quart, request, websocket

from app.handler.voicelive_media_handler import VoiceLiveMediaHandler

load_dotenv()

# ---------------------------------------------------------------------------
# App configuration
# ---------------------------------------------------------------------------

app = Quart(__name__)
app.config["AZURE_VOICE_LIVE_API_KEY"] = os.getenv("AZURE_VOICE_LIVE_API_KEY", "")
app.config["AZURE_VOICE_LIVE_ENDPOINT"] = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
app.config["VOICE_LIVE_MODEL"] = os.getenv("VOICE_LIVE_MODEL", "gpt-4o-mini")
app.config["ACS_CONNECTION_STRING"] = os.getenv("ACS_CONNECTION_STRING")
app.config["ACS_DEV_TUNNEL"] = os.getenv("ACS_DEV_TUNNEL", "")
app.config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"] = os.getenv(
    "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", ""
)
app.config["AMBIENT_PRESET"] = os.getenv("AMBIENT_PRESET", "none")
app.config["TWILIO_AUTH_TOKEN"] = os.getenv("TWILIO_AUTH_TOKEN", "")
app.config["INFOBIP_API_KEY"] = os.getenv("INFOBIP_API_KEY", "")
app.config["INFOBIP_API_BASE_URL"] = os.getenv("INFOBIP_API_BASE_URL", "")
app.config["GENESYS_API_KEY"] = os.getenv("GENESYS_API_KEY", "")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Log ambient configuration on startup
ambient_preset = app.config["AMBIENT_PRESET"]
if ambient_preset and ambient_preset != "none":
    logger.info(f"Ambient scenes ENABLED: preset='{ambient_preset}'")
else:
    logger.info("Ambient scenes DISABLED (preset=none)")

# ---------------------------------------------------------------------------
# Telephony detection (exclusive: Twilio OR ACS, never both)
# ---------------------------------------------------------------------------

if app.config["TWILIO_AUTH_TOKEN"]:
    _telephony_client = "twilio"
elif app.config["INFOBIP_API_KEY"]:
    _telephony_client = "infobip"
elif app.config["GENESYS_API_KEY"]:
    _telephony_client = "genesys"
else:
    _telephony_client = "acs"
logger.info("Telephony client: %s", _telephony_client)

# ---------------------------------------------------------------------------
# Routes: Web client (always available)
# ---------------------------------------------------------------------------


@app.websocket("/web/ws")
async def web_ws():
    """WebSocket endpoint for web clients to send audio to Voice Live."""
    logger = logging.getLogger("web_ws")
    logger.info("Incoming Web WebSocket connection")
    handler = VoiceLiveMediaHandler(app.config)
    await handler.init_websocket(websocket)
    asyncio.create_task(handler.connect_voicelive())
    try:
        while True:
            msg = await websocket.receive()
            await handler.handle_audio(msg)
    except asyncio.CancelledError:
        logger.info("Web WebSocket cancelled")
    except Exception:
        logger.exception("Web WebSocket connection closed")
    finally:
        await handler._cleanup()


@app.route("/")
async def index():
    """Serves the static index page."""
    return await app.send_static_file("index.html")


@app.route("/genesys")
async def genesys_simulator():
    """Serves the Genesys AudioHook client simulator page."""
    return await app.send_static_file("genesys-simulator.html")


@app.route("/health")
async def health():
    """Liveness/readiness probe endpoint."""
    return {"status": "healthy"}, 200


# ---------------------------------------------------------------------------
# Routes: Telephony (only one provider is registered)
# ---------------------------------------------------------------------------

if _telephony_client == "twilio":
    from app.handler.twilio_event_handler import TwilioEventHandler
    from app.handler.twilio_media_handler import TwilioMediaHandler

    twilio_handler = TwilioEventHandler(app.config)

    @app.route("/voice", methods=["GET", "POST"])
    async def twilio_voice():
        """Handles incoming Twilio phone calls with bidirectional media stream."""
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
        logger = logging.getLogger("twilio_ws")
        logger.info("Incoming Twilio Media Stream WebSocket connection")

        handler = TwilioMediaHandler(app.config)
        handler.twilio_ws = websocket

        if not await handler.authenticate_and_start():
            return

        try:
            await handler.connect_voicelive()
            while True:
                msg = await websocket.receive()
                await handler.handle_twilio_message(msg)
        except asyncio.CancelledError:
            logger.info("Twilio WebSocket cancelled")
        except Exception:
            logger.exception("Twilio WebSocket connection closed")
        finally:
            await handler._cleanup()

elif _telephony_client == "infobip":
    from app.handler.infobip_event_handler import InfobipEventHandler
    from app.handler.infobip_media_handler import InfobipMediaHandler

    infobip_handler = InfobipEventHandler(app.config)

    @app.route("/infobip/incoming", methods=["POST"])
    async def infobip_incoming_call():
        """Handles incoming Infobip voice call webhooks."""
        logger.info("Infobip /infobip/incoming webhook called")

        if not infobip_handler.api_key:
            return "Service Unavailable", 503

        request_data = await request.get_json()
        host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
        return await infobip_handler.handle_incoming_call(request_data, host_url)

    @app.websocket("/infobip/ws")
    async def infobip_ws():
        """WebSocket endpoint for Infobip WEBSOCKET call legs to bridge to Voice Live."""
        logger = logging.getLogger("infobip_ws")
        logger.info("Incoming Infobip WebSocket connection")

        handler = InfobipMediaHandler(app.config, token_validator=infobip_handler.validate_ws_token)
        handler.infobip_ws = websocket
        await handler.init_websocket(websocket)
        voicelive_task = None
        try:
            # Start Voice Live connection in background so we can respond to
            # Infobip immediately. The platform requires continuous bidirectional
            # audio; blocking here would cause a disconnect.
            voicelive_task = asyncio.create_task(handler.connect_voicelive())
            while True:
                msg = await websocket.receive()
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
            await handler._cleanup()

elif _telephony_client == "genesys":
    from app.handler.genesys_media_handler import GenesysMediaHandler

    @app.websocket("/audiohook/ws")
    async def genesys_ws():
        """WebSocket endpoint for Genesys AudioHook Audio Connector."""
        logger = logging.getLogger("genesys_ws")
        logger.info("Incoming Genesys AudioHook WebSocket connection")

        # Validate API key: check X-API-KEY header (real Genesys) or query param (simulator)
        provided_key = websocket.headers.get("X-API-KEY", "") or websocket.args.get("apikey", "")
        handler = GenesysMediaHandler(app.config)
        if not handler.validate_api_key(provided_key):
            logger.warning("[GenesysHandler] Invalid API key — rejecting connection")
            await websocket.accept()
            await websocket.close(4403, "Invalid API key")
            return

        handler.genesys_ws = websocket
        await handler.init_websocket(websocket)
        voicelive_task = None
        try:
            voicelive_task = asyncio.create_task(handler.connect_voicelive())
            while True:
                msg = await websocket.receive()
                await handler.handle_message(msg)
        except asyncio.CancelledError:
            logger.info("Genesys WebSocket cancelled")
        except Exception as e:
            logger.exception("Genesys WebSocket closed: %s", e)
        finally:
            if voicelive_task:
                voicelive_task.cancel()
            logger.info(
                "Genesys WebSocket ending — frames in=%d out=%d voicelive_connected=%s",
                handler._in_frame_count,
                handler._out_frame_count,
                handler._voicelive_connected,
            )
            await handler._cleanup()

elif _telephony_client == "acs":
    from app.handler.acs_event_handler import AcsEventHandler
    from app.handler.acs_media_handler import ACSMediaHandler

    acs_handler = AcsEventHandler(app.config)

    @app.route("/acs/incomingcall", methods=["POST"])
    async def incoming_call_handler():
        """Handles initial incoming call event from EventGrid."""
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
        logger = logging.getLogger("acs_ws")
        logger.info("Incoming ACS WebSocket connection")
        handler = ACSMediaHandler(app.config)
        await handler.init_websocket(websocket)
        asyncio.create_task(handler.connect_voicelive())
        try:
            while True:
                msg = await websocket.receive()
                await handler.handle_audio(msg)
        except asyncio.CancelledError:
            logger.info("ACS WebSocket cancelled")
        except Exception:
            logger.exception("ACS WebSocket connection closed")
        finally:
            await handler._cleanup()


if __name__ == "__main__":
    app.run(debug=os.getenv("DEBUG_MODE", "false").lower() == "true", host="0.0.0.0", port=8000)
