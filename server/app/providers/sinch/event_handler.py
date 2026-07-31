"""Handler for Sinch voice callbacks and connectStream SVAML generation.

Validates signed Sinch callbacks and builds the SVAML response that routes an
incoming call to our WebSocket media endpoint via the `connectStream` action.

Callback signing scheme (see Sinch docs):
    authorization = "application " + ApplicationKey + ":" + Signature
    Signature     = Base64(HMAC-SHA256(Base64-Decode(ApplicationSecret), StringToSign))
    StringToSign  = HTTP-Verb + "\\n" + Content-MD5 + "\\n" + Content-Type + "\\n"
                    + "x-timestamp:" + timestamp + "\\n" + path
    Content-MD5   = Base64(MD5(body))
"""

import base64
import hashlib
import hmac
import logging
import secrets

from quart import Response, jsonify

logger = logging.getLogger(__name__)


class SinchEventHandler:
    """Validates Sinch callbacks and generates connectStream SVAML responses."""

    def __init__(self, config):
        self.app_key = config.get("SINCH_APPLICATION_KEY", "")
        self.app_secret = config.get("SINCH_APPLICATION_SECRET", "")
        try:
            self.sample_rate = int(config.get("SINCH_SAMPLE_RATE", "24000"))
        except (TypeError, ValueError):
            self.sample_rate = 24000
        self._valid_ws_tokens: set[str] = set()

    # ------------------------------------------------------------------
    # Callback signature validation
    # ------------------------------------------------------------------

    def validate_signature(
        self,
        method: str,
        path: str,
        body: bytes,
        content_type: str,
        timestamp: str,
        authorization: str,
    ) -> bool:
        """Validate a signed Sinch callback request.

        Returns True only if the signature matches. Any missing/malformed input
        yields False (rejected) — callers map this to a generic 403.
        """
        if not self.app_secret or not authorization:
            return False

        # Expected: "application <key>:<signature>" (scheme is case-insensitive)
        try:
            scheme, credentials = authorization.split(" ", 1)
            provided_key, provided_sig = credentials.split(":", 1)
        except ValueError:
            return False

        if scheme.lower() != "application":
            return False
        if self.app_key and not hmac.compare_digest(provided_key, self.app_key):
            return False

        # Content-MD5 is mandated by Sinch's callback signing scheme; MD5 here is a
        # protocol requirement, not a security primitive (usedforsecurity=False).
        content_md5 = base64.b64encode(
            hashlib.md5(body, usedforsecurity=False).digest()
        ).decode("ascii")
        string_to_sign = "\n".join(
            [
                method,
                content_md5,
                content_type,
                f"x-timestamp:{timestamp}",
                path,
            ]
        )

        try:
            # validate=True rejects a malformed/corrupted secret (e.g. a
            # double-pasted value) up front, instead of silently decoding a
            # truncated key and computing a signature that never matches.
            secret_bytes = base64.b64decode(self.app_secret, validate=True)
        except (ValueError, TypeError):
            logger.error("[SinchEventHandler] SINCH_APPLICATION_SECRET is not valid base64")
            return False

        expected_sig = base64.b64encode(
            hmac.new(secret_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")

        return hmac.compare_digest(provided_sig, expected_sig)

    # ------------------------------------------------------------------
    # WebSocket one-time token
    # ------------------------------------------------------------------

    def _issue_ws_token(self) -> str:
        """Create and remember a one-time token embedded in the connect handshake."""
        token = secrets.token_urlsafe(32)
        self._valid_ws_tokens.add(token)
        return token

    def validate_ws_token(self, token: str) -> bool:
        """Validate and consume a one-time WebSocket token."""
        if token in self._valid_ws_tokens:
            self._valid_ws_tokens.discard(token)
            return True
        return False

    # ------------------------------------------------------------------
    # Callback dispatch
    # ------------------------------------------------------------------

    async def handle_callback(self, request_data: dict, host_url: str) -> Response:
        """Route a Sinch voice callback based on its event type."""
        event = (request_data.get("event") or "").lower()
        call_id = request_data.get("callid", "")
        logger.info("[SinchEventHandler] Callback event=%s callId=%s", event, call_id)

        if event == "ice":
            return self._handle_ice(request_data, host_url)
        if event == "ace":
            # Call answered — nothing to add, let it continue.
            return jsonify({"action": {"name": "continue"}})
        if event == "dice":
            # Call disconnected — acknowledge with an empty SVAML document.
            # Sinch parses the callback response as JSON, so return `{}` rather
            # than an empty body (a bare 200 triggers "Error processing callback
            # response" on their side).
            logger.info("[SinchEventHandler] Call disconnected: callId=%s", call_id)
            return jsonify({})

        logger.info("[SinchEventHandler] Unhandled event type: %s", event)
        return jsonify({})

    def _handle_ice(self, request_data: dict, host_url: str) -> Response:
        """Build the connectStream SVAML for an Incoming Call Event."""
        ws_url = host_url.replace("https://", "wss://").replace("http://", "ws://") + "/sinch/ws"
        token = self._issue_ws_token()
        # Deliver the one-time token via the WSS query string (the reliable primary
        # path). We also include it as a callHeader; the connect frame echoes these
        # under a top-level `callHeaders` object, which the handler uses as a fallback.
        ws_url_with_token = f"{ws_url}?token={token}"

        svaml = {
            "action": {
                "name": "connectStream",
                "destination": {
                    "type": "Websocket",
                    "endpoint": ws_url_with_token,
                },
                "streamingOptions": {
                    "version": 1,
                    "sampleRate": self.sample_rate,
                },
                "callHeaders": [
                    {"key": "token", "value": token},
                ],
            }
        }
        logger.info("[SinchEventHandler] Returning connectStream SVAML: endpoint=%s", ws_url)
        return jsonify(svaml)
