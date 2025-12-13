import asyncio
import json
import logging
import os
from typing import Optional

from app.handler.voice_live_handler import VoiceLiveStreamingHandler
from app.handler.twilio_handler import TwilioMediaStreamHandler
from app.storage.voicemail_store import StorageConfig, VoicemailTranscriptionStore
from app.queue import SmsQueueProducer
from dotenv import load_dotenv
from quart import Quart, Response, request, websocket
from twilio.request_validator import RequestValidator

load_dotenv()

app = Quart(__name__)
app.config["AZURE_VOICE_LIVE_API_KEY"] = os.getenv("AZURE_VOICE_LIVE_API_KEY", "")
app.config["AZURE_VOICE_LIVE_ENDPOINT"] = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
app.config["VOICE_LIVE_MODEL"] = os.getenv("VOICE_LIVE_MODEL", "gpt-4o-mini")
app.config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"] = os.getenv(
    "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", ""
)
app.config["TWILIO_STATUS_CALLBACK_URL"] = os.getenv("TWILIO_STATUS_CALLBACK_URL")
app.config["SMS_QUEUE_PRODUCER"] = None
app.config["TWILIO_REQUEST_VALIDATOR"] = None


def _derive_queue_url(blob_account_url: str) -> str:
    if ".blob." not in blob_account_url:
        raise ValueError("Expected a blob endpoint URL containing '.blob.'")
    return blob_account_url.replace(".blob.", ".queue.")

def _build_voicemail_store() -> Optional[VoicemailTranscriptionStore]:
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    blob_account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
    table_account_url = os.getenv("AZURE_STORAGE_TABLE_URL")
    container_name = os.getenv("AZURE_STORAGE_VOICEMAIL_CONTAINER", "twilio-transcriptions")
    table_name = os.getenv("AZURE_STORAGE_VOICEMAIL_TABLE", "VoicemailTranscripts")

    if not any([connection_string, blob_account_url]):
        logging.getLogger(__name__).info(
            "Voicemail storage is not configured; transcripts will not be persisted"
        )
        return None

    config = StorageConfig(
        connection_string=connection_string,
        blob_account_url=blob_account_url,
        table_account_url=table_account_url,
        container_name=container_name,
        table_name=table_name,
    )
    return VoicemailTranscriptionStore(config)


app.config["VOICEMAIL_STORE"] = _build_voicemail_store()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s"
)


@app.before_serving
async def _startup() -> None:
    twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if twilio_auth_token:
        app.config["TWILIO_REQUEST_VALIDATOR"] = RequestValidator(twilio_auth_token)
    else:
        logging.getLogger(__name__).warning("TWILIO_AUTH_TOKEN not set; webhook signature validation disabled")

    queue_name = os.getenv("AZURE_STORAGE_QUEUE_NAME", "outbound-sms")
    if not queue_name:
        logging.getLogger(__name__).info("SMS queue name not configured; outbound SMS disabled")
        return

    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    queue_account_url = os.getenv("AZURE_STORAGE_QUEUE_URL")
    blob_account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")

    account_url = queue_account_url
    if not account_url and blob_account_url:
        try:
            account_url = _derive_queue_url(blob_account_url)
        except ValueError:
            logging.getLogger(__name__).warning("Unable to derive queue endpoint from blob endpoint")

    if not connection_string and not account_url:
        logging.getLogger(__name__).warning(
            "No storage connection details available for SMS queue; outbound SMS disabled"
        )
        return

    try:
        producer = await SmsQueueProducer.create(
            connection_string=connection_string,
            account_url=account_url,
            queue_name=queue_name,
        )
    except Exception:
        logging.getLogger(__name__).exception("Failed to initialize SMS queue producer")
        producer = None

    app.config["SMS_QUEUE_PRODUCER"] = producer



@app.websocket("/web/ws")
async def web_ws():
    """WebSocket endpoint for web clients to send audio to Voice Live."""
    logger = logging.getLogger("web_ws")
    logger.info("Incoming Web WebSocket connection")
    handler = VoiceLiveStreamingHandler(
        app.config,
        enable_responses=True,
        sms_queue=app.config.get("SMS_QUEUE_PRODUCER"),
    )

    async def send_user_transcript(text: str) -> None:
        payload = {"Kind": "Transcription", "Speaker": "user", "Text": text}
        await websocket.send(json.dumps(payload))

    async def send_ai_transcript(text: str) -> None:
        payload = {"Kind": "Transcription", "Speaker": "assistant", "Text": text}
        await websocket.send(json.dumps(payload))

    async def send_audio_chunk(chunk: bytes) -> None:
        await websocket.send(chunk)

    async def send_stop_signal() -> None:
        await websocket.send(json.dumps({"Kind": "StopAudio"}))

    handler.register_event_handlers(
        on_user_transcript=send_user_transcript,
        on_ai_transcript=send_ai_transcript,
        on_audio_chunk=send_audio_chunk,
        on_audio_stop=send_stop_signal,
    )

    await handler.start()
    try:
        while True:
            msg = await websocket.receive()
            if isinstance(msg, (bytes, bytearray)):
                await handler.send_pcm16(bytes(msg))
            else:
                logger.debug("Ignoring non-binary message from web client")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Web WebSocket connection closed")
    finally:
        await handler.close()


@app.websocket("/twilio/stream")
async def twilio_stream():
    """WebSocket endpoint for Twilio Media Streams to deliver audio for transcription."""

    logger = logging.getLogger("twilio_stream")
    logger.info("Incoming Twilio WebSocket connection")

    voicemail_store: Optional[VoicemailTranscriptionStore] = app.config.get("VOICEMAIL_STORE")

    handler = VoiceLiveStreamingHandler(
        app.config,
        enable_responses=False,
        transcription_store=voicemail_store,
        sms_queue=app.config.get("SMS_QUEUE_PRODUCER"),
    )

    attempt_param = websocket.args.get("attempt")
    recording_sid = websocket.args.get("RecordingSid") or websocket.args.get("recordingSid")
    call_sid_hint = websocket.args.get("CallSid") or websocket.args.get("callSid")

    handler.update_stream_context(
        call_sid=call_sid_hint,
        recording_sid=recording_sid,
        attempt=_safe_int(attempt_param),
    )

    twilio_handler = TwilioMediaStreamHandler(
        websocket,
        handler,
        recording_sid=recording_sid,
        attempt=_safe_int(attempt_param),
    )

    handler.register_event_handlers(
        on_user_transcript=twilio_handler.send_user_transcript,
        on_ai_transcript=twilio_handler.send_ai_transcript,
    )

    await handler.start()
    try:
        while True:
            msg = await websocket.receive()
            await twilio_handler.handle_message(msg)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Twilio WebSocket connection closed")
    finally:
        await handler.close()


@app.route("/")
async def index():
    """Serves the static index page."""
    return await app.send_static_file("index.html")


@app.post("/twilio/voicemail/textme")
async def twilio_voicemail_textme():
    store: Optional[VoicemailTranscriptionStore] = app.config.get("VOICEMAIL_STORE")
    if not store:
        return Response("", status=503)

    payload: dict
    if request.mimetype == "application/json":
        payload = (await request.get_json(silent=True)) or {}
    else:
        payload = (await request.form).to_dict()

    call_sid = payload.get("CallSid") or payload.get("callSid")
    recording_sid = payload.get("RecordingSid") or payload.get("recordingSid")
    attempt_value = payload.get("Attempt") or payload.get("attempt")

    if not call_sid or not recording_sid:
        return Response("Missing CallSid or RecordingSid", status=400)

    attempt = _safe_int(attempt_value, default=0)

    sms_to = payload.get("To") or payload.get("to")
    sms_from = payload.get("From") or payload.get("from")

    try:
        resolved_attempt = await store.mark_sms_requested(
            call_sid=call_sid,
            recording_sid=recording_sid,
            attempt=attempt,
            sms_to=sms_to,
            sms_from=sms_from,
        )
    except KeyError:
        return Response("Transcript attempt not found", status=404)
    except Exception:
        logging.getLogger(__name__).exception("Failed to flag SMS request for voicemail")
        return Response("Internal Server Error", status=500)

    producer: Optional[SmsQueueProducer] = app.config.get("SMS_QUEUE_PRODUCER")
    if producer:
        try:
            candidate = await store.build_sms_candidate(
                call_sid=call_sid,
                recording_sid=recording_sid,
                attempt=resolved_attempt,
            )
            if candidate:
                await producer.enqueue(candidate)
                await store.mark_sms_enqueued(
                    call_sid=call_sid,
                    recording_sid=recording_sid,
                    attempt=resolved_attempt,
                )
        except Exception:
            logging.getLogger(__name__).exception("Failed to enqueue SMS job after request flag")

    twiml = """<Response><Say>Okay, I'm sending that to you now.</Say><Hangup/></Response>"""
    return Response(twiml, mimetype="application/xml")

@app.post("/twilio/sms/status")
async def twilio_sms_status() -> Response:
    store: Optional[VoicemailTranscriptionStore] = app.config.get("VOICEMAIL_STORE")
    if not store:
        return Response("", status=503)

    validator: Optional[RequestValidator] = app.config.get("TWILIO_REQUEST_VALIDATOR")
    form_data = (await request.form).to_dict()
    signature = request.headers.get("X-Twilio-Signature", "")

    if validator:
        if not validator.validate(str(request.url), form_data, signature):
            logging.getLogger(__name__).warning("Twilio signature validation failed for SMS status callback")
            return Response("Forbidden", status=403)
    else:
        logging.getLogger(__name__).debug("Skipping Twilio signature validation; validator not configured")

    message_sid = form_data.get("MessageSid")
    status = form_data.get("MessageStatus") or form_data.get("SmsStatus")
    error_code = form_data.get("ErrorCode")

    if not message_sid or not status:
        return Response("Bad Request", status=400)

    updated = await store.update_sms_status_by_sid(
        message_sid=message_sid,
        status=status,
        error_code=error_code,
    )

    if not updated:
        return Response("Not Found", status=404)

    return Response("", status=204)


def _safe_int(value: Optional[str], default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning("Invalid integer value received: %s", value)
        return default


@app.after_serving
async def _shutdown() -> None:
    store: Optional[VoicemailTranscriptionStore] = app.config.get("VOICEMAIL_STORE")
    if store:
        await store.close()

    producer: Optional[SmsQueueProducer] = app.config.get("SMS_QUEUE_PRODUCER")
    if producer:
        await producer.close()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)
