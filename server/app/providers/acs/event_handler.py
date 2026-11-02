"""Handler for processing ACS (Azure Communication Services) call and callback events."""

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from urllib.parse import urlencode, urlparse, urlunparse

import jwt
from azure.communication.callautomation import (AudioFormat,
                                                MediaStreamingAudioChannelType,
                                                MediaStreamingContentType,
                                                MediaStreamingOptions,
                                                StreamingTransportType)
from azure.communication.callautomation.aio import CallAutomationClient
from azure.eventgrid import EventGridEvent, SystemEventNames
from quart import Response

logger = logging.getLogger(__name__)

# Media WebSocket token lifetime. The token only needs to survive the brief
# window between answering the call and ACS opening the media WebSocket.
_WS_TOKEN_TTL_SECONDS = 120
_WS_TOKEN_AUDIENCE = "acs-media-ws"


class AcsEventHandler:
    """Handles ACS event processing and call answering logic."""

    def __init__(self, config):
        connection_string = config["ACS_CONNECTION_STRING"]
        self.acs_client = CallAutomationClient.from_connection_string(
            connection_string
        )
        # Derive a dedicated signing key from the ACS connection string (which
        # every replica already has) so the raw access key is never used directly.
        self._ws_token_key = hmac.new(
            connection_string.encode(), b"acs-media-ws-token", hashlib.sha256
        ).digest()

    # ------------------------------------------------------------------
    # Media WebSocket token (stateless, signed, short-lived)
    # ------------------------------------------------------------------

    def _issue_ws_token(self) -> str:
        """Mint a signed, self-contained token for one call's media WebSocket.

        The token is embedded in the transport URL handed to ACS when answering
        the call, so only ACS (and this server) ever see it. Any replica can
        verify it with the shared key, so no cross-replica store is required.
        """
        now = int(time.time())
        payload = {
            "iat": now,
            "exp": now + _WS_TOKEN_TTL_SECONDS,
            "aud": _WS_TOKEN_AUDIENCE,
            "jti": secrets.token_urlsafe(8),
        }
        return jwt.encode(payload, self._ws_token_key, algorithm="HS256")

    def validate_ws_token(self, token: str) -> bool:
        """Validate a media WebSocket token: signature, audience and expiry."""
        if not token or not isinstance(token, str):
            return False
        try:
            jwt.decode(
                token,
                self._ws_token_key,
                algorithms=["HS256"],
                audience=_WS_TOKEN_AUDIENCE,
                options={"require": ["exp", "aud"]},
            )
        except jwt.InvalidTokenError as e:
            logger.warning("[AcsEventHandler] Rejecting media WS token: %s", e)
            return False
        return True

    async def process_incoming_call(self, events: list, host_url, config):
        """Processes incoming call events and answers calls with media streaming."""
        logger.info("incoming event data")

        for event_dict in events:
            event = EventGridEvent.from_dict(event_dict)
            logger.info("incoming event data --> %s", event.data)

            if (
                event.event_type
                == SystemEventNames.EventGridSubscriptionValidationEventName
            ):
                logger.info("Validating subscription")
                validation_code = event.data["validationCode"]
                return Response(
                    response=json.dumps({"validationResponse": validation_code}),
                    status=200,
                )

            if event.event_type == "Microsoft.Communication.IncomingCall":
                logger.info("Incoming call received: data=%s", event.data)

                caller_info = event.data["from"]
                caller_id = (
                    caller_info["phoneNumber"]["value"]
                    if caller_info["kind"] == "phoneNumber"
                    else caller_info["rawId"]
                )

                logger.info("incoming call handler caller id: %s", caller_id)
                incoming_call_context = event.data["incomingCallContext"]
                query_parameters = urlencode({"callerId": caller_id})
                guid = uuid.uuid4()

                callback_events_uri = (
                    f"{config['ACS_DEV_TUNNEL']}/acs/callbacks"
                    if config["ACS_DEV_TUNNEL"]
                    else f"{host_url}/acs/callbacks"
                )
                callback_uri = f"{callback_events_uri}/{guid}?{query_parameters}"

                parsed_url = urlparse(callback_events_uri)
                websocket_url = urlunparse(
                    ("wss", parsed_url.netloc, "/acs/ws", "", "", "")
                )

                logger.info("callback url: %s", callback_uri)
                # Log the URL before the token is attached so it never reaches logs.
                logger.info("websocket url: %s", websocket_url)
                transport_url = (
                    f"{websocket_url}?{urlencode({'token': self._issue_ws_token()})}"
                )

                media_streaming_options = MediaStreamingOptions(
                    transport_url=transport_url,
                    transport_type=StreamingTransportType.WEBSOCKET,
                    content_type=MediaStreamingContentType.AUDIO,
                    audio_channel_type=MediaStreamingAudioChannelType.MIXED,
                    start_media_streaming=True,
                    enable_bidirectional=True,
                    audio_format=AudioFormat.PCM24_K_MONO,
                )

                result = await self.acs_client.answer_call(
                    incoming_call_context=incoming_call_context,
                    operation_context="incomingCall",
                    callback_url=callback_uri,
                    media_streaming=media_streaming_options,
                )

                logger.info(
                    "Answered call for connection id: %s", result.call_connection_id
                )
                return Response(status=200)

        return Response(status=400)

    async def process_callback_events(self, raw_events: list):
        """Processes ACS callback events such as call connected, media started, etc."""
        for event in raw_events:
            try:
                event_data = event["data"]
                call_connection_id = event_data["callConnectionId"]

                logger.info(
                    "Received Event:-> %s, Correlation Id:-> %s, CallConnectionId:-> %s",
                    event["type"],
                    event_data["correlationId"],
                    call_connection_id,
                )

                if event["type"] == "Microsoft.Communication.CallConnected":
                    properties = await self.acs_client.get_call_connection(
                        call_connection_id
                    ).get_call_properties()

                    logger.info(
                        "MediaStreamingSubscription:--> %s",
                        properties.media_streaming_subscription,
                    )
                    logger.info(
                        "Received CallConnected event for connection id: %s",
                        call_connection_id,
                    )
                    logger.info("CORRELATION ID:--> %s", event_data["correlationId"])
                    logger.info("CALL CONNECTION ID:--> %s", call_connection_id)

                elif event["type"] == "Microsoft.Communication.MediaStreamingStarted":
                    update = event_data["mediaStreamingUpdate"]
                    logger.info(
                        "Media streaming content type:--> %s", update["contentType"]
                    )
                    logger.info(
                        "Media streaming status:--> %s", update["mediaStreamingStatus"]
                    )
                    logger.info(
                        "Media streaming status details:--> %s",
                        update["mediaStreamingStatusDetails"],
                    )

                elif event["type"] == "Microsoft.Communication.MediaStreamingStopped":
                    update = event_data["mediaStreamingUpdate"]
                    logger.info(
                        "Media streaming content type:--> %s", update["contentType"]
                    )
                    logger.info(
                        "Media streaming status:--> %s", update["mediaStreamingStatus"]
                    )
                    logger.info(
                        "Media streaming status details:--> %s",
                        update["mediaStreamingStatusDetails"],
                    )

                elif event["type"] == "Microsoft.Communication.MediaStreamingFailed":
                    result_info = event_data["resultInformation"]
                    logger.info(
                        "Code:-> %s, Subcode:-> %s",
                        result_info["code"],
                        result_info["subCode"],
                    )
                    logger.info("Message:-> %s", result_info["message"])

                elif event["type"] == "Microsoft.Communication.CallDisconnected":
                    logger.info(
                        "CallDisconnected event received for: %s", call_connection_id
                    )
            except Exception:
                logger.exception("Error processing ACS callback event: %s", event.get("type", "unknown"))

        return Response(status=200)
