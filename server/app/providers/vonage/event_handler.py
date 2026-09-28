"""Handler for Vonage voice webhooks, NCCO generation, and WebSocket auth.

Responsibilities:
- Build the NCCO (Nexmo Call Control Object) that connects an inbound call to
  our WebSocket media endpoint with a one-time token.
- Optionally validate Vonage signed webhooks (JWT, HS256) when a signature
  secret is configured.
- Mint and validate short-lived, self-contained tokens that gate the WebSocket
  media connection.

References:
- NCCO connect/websocket: https://developer.vonage.com/en/voice/voice-api/ncco-reference
- Signed webhooks: https://developer.vonage.com/en/getting-started/concepts/signing-messages
"""

import hashlib
import json
import logging
import secrets
import time

import jwt

logger = logging.getLogger(__name__)

# Voice Live uses PCM 24kHz 16-bit mono; request the same from Vonage so audio
# passes through untouched (no resampling on either leg).
VOICE_LIVE_CONTENT_TYPE = "audio/l16;rate=24000"

# One-time WebSocket token lifetime. The token only needs to survive the brief
# window between returning the NCCO and Vonage opening the WebSocket.
_WS_TOKEN_TTL_SECONDS = 120


class VonageEventHandler:
    """Generates NCCOs, validates signed webhooks, and issues WS tokens."""

    def __init__(self, config):
        self.api_key = config.get("VONAGE_API_KEY", "")
        self.api_secret = config.get("VONAGE_API_SECRET", "")
        self.signature_secret = config.get("VONAGE_SIGNATURE_SECRET", "")
        self.application_id = config.get("VONAGE_APPLICATION_ID", "")

    # ------------------------------------------------------------------
    # Token signing key
    # ------------------------------------------------------------------

    def _token_key(self) -> str:
        """Return the HMAC key used to sign/verify WS tokens.

        Prefer the signature secret (dedicated to signing); fall back to the API
        secret, which is always present when Vonage is the active provider.
        """
        return self.signature_secret or self.api_secret

    # ------------------------------------------------------------------
    # NCCO generation
    # ------------------------------------------------------------------

    def generate_answer_ncco(self, host_url: str) -> str:
        """Build the NCCO that connects the caller to our WebSocket endpoint.

        The one-time token is delivered on the WSS query string, which is the
        reliable path for authenticating the subsequent WebSocket handshake.
        """
        ws_url = host_url.replace("https://", "wss://").replace("http://", "ws://") + "/vonage/ws"
        token = self._issue_ws_token()
        ws_url_with_token = f"{ws_url}?token={token}"

        ncco = [
            {
                "action": "talk",
                "text": "Please wait while we connect you to our AI assistant.",
            },
            {
                "action": "connect",
                "endpoint": [
                    {
                        "type": "websocket",
                        "uri": ws_url_with_token,
                        "content-type": VOICE_LIVE_CONTENT_TYPE,
                    }
                ],
            },
        ]
        logger.info("[VonageEventHandler] Returning connect NCCO: endpoint=%s", ws_url)
        return json.dumps(ncco)

    # ------------------------------------------------------------------
    # Signed webhook validation
    # ------------------------------------------------------------------

    def validate_webhook(self, authorization: str, body: bytes):
        """Validate a Vonage signed-webhook JWT.

        Returns:
            True  — signature secret configured and the request is authentic.
            False — signature secret configured but the request is not authentic.
            None  — signature secret not configured; validation is not enforced
                    (the one-time WebSocket token still gates the media session).
        """
        if not self.signature_secret:
            return None

        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token:
            logger.warning("[VonageEventHandler] Missing signed-webhook JWT")
            return False

        try:
            claims = jwt.decode(
                token,
                self.signature_secret,
                algorithms=["HS256"],
                options={"require": [], "verify_exp": True},
            )
        except jwt.InvalidTokenError as e:
            logger.warning("[VonageEventHandler] Invalid signed-webhook JWT: %s", e)
            return False

        # When present, payload_hash binds the JWT to the exact request body,
        # preventing a captured signature from being reused with altered content.
        payload_hash = claims.get("payload_hash")
        if payload_hash:
            expected = hashlib.sha256(body or b"").hexdigest()
            if not secrets.compare_digest(str(payload_hash), expected):
                logger.warning("[VonageEventHandler] Signed-webhook payload_hash mismatch")
                return False

        return True

    # ------------------------------------------------------------------
    # WebSocket handshake token (stateless, signed, short-lived)
    # ------------------------------------------------------------------

    def _issue_ws_token(self, call_uuid: str = "") -> str:
        """Mint a signed, self-contained token bound to an expiry.

        Any replica can verify it with the shared secret, so no cross-replica
        session store is required.
        """
        key = self._token_key()
        if not key:
            return ""
        now = int(time.time())
        payload = {
            "iat": now,
            "exp": now + _WS_TOKEN_TTL_SECONDS,
            "jti": secrets.token_urlsafe(8),
            "cid": call_uuid or "",
        }
        return jwt.encode(payload, key, algorithm="HS256")

    def validate_ws_token(self, token: str, call_uuid: str = "") -> bool:
        """Validate a signed WS token: signature and expiry."""
        if not token or not isinstance(token, str):
            return False
        key = self._token_key()
        if not key:
            return False
        try:
            jwt.decode(token, key, algorithms=["HS256"], options={"verify_exp": True})
        except jwt.InvalidTokenError as e:
            logger.warning("[VonageEventHandler] Rejecting WS token: %s", e)
            return False
        return True
