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
import json
import logging
import secrets
import time
from datetime import datetime, timezone

from quart import Response, jsonify

logger = logging.getLogger(__name__)

# Reject signed callbacks whose x-timestamp is outside this window (seconds) to
# limit replay of a captured request. Sinch always sends the current UTC time,
# so a few minutes of tolerance covers normal clock skew without affecting calls.
_TIMESTAMP_TOLERANCE_SECONDS = 300

_WS_TOKEN_TTL_SECONDS = 120


class SinchEventHandler:
    """Validates Sinch callbacks and generates connectStream SVAML responses."""

    def __init__(self, config):
        self.app_key = config.get("SINCH_APPLICATION_KEY", "")
        self.app_secret = config.get("SINCH_APPLICATION_SECRET", "")
        try:
            self.sample_rate = int(config.get("SINCH_SAMPLE_RATE", "24000"))
        except (TypeError, ValueError):
            self.sample_rate = 24000

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

        # Replay protection: reject stale/absent timestamps before doing any
        # signature work. A captured callback can otherwise be replayed forever.
        if not self._timestamp_is_fresh(timestamp):
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

    @staticmethod
    def _timestamp_is_fresh(timestamp: str) -> bool:
        """Return True only if `timestamp` (ISO8601 UTC) is within the tolerance.

        The value is validated for freshness only; the raw string is still used
        verbatim when computing the signature, so parsing never alters the STS.
        """
        if not timestamp:
            return False
        ts = timestamp.strip()
        # datetime.fromisoformat accepts a trailing 'Z' only on 3.11+; normalize
        # it to an explicit offset so parsing is robust across runtimes.
        if ts.endswith(("Z", "z")):
            ts = ts[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        skew = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        if skew > _TIMESTAMP_TOLERANCE_SECONDS:
            logger.warning(
                "[SinchEventHandler] Rejecting callback: x-timestamp skew %.0fs exceeds %ds",
                skew,
                _TIMESTAMP_TOLERANCE_SECONDS,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # WebSocket handshake token (stateless, signed, short-lived)
    # ------------------------------------------------------------------

    def _token_key(self) -> bytes | None:
        """HMAC key for signing WS tokens: the base64-decoded app secret.

        Returns None if the secret is missing/invalid; callers then decline to
        issue or accept a token. In practice the secret is already validated by
        the callback signature check before a token is ever issued.
        """
        if not self.app_secret:
            return None
        try:
            return base64.b64decode(self.app_secret, validate=True)
        except (ValueError, TypeError):
            return None

    def _issue_ws_token(self, call_id: str = "") -> str:
        """Mint a signed `<payload>.<sig>` token bound to a call and expiry.

        The token is self-contained: any replica can verify it with the shared
        app secret, so no cross-replica session store is required.
        """
        key = self._token_key()
        if key is None:
            return ""
        payload = {
            "exp": int(time.time()) + _WS_TOKEN_TTL_SECONDS,
            "cid": call_id or "",
            "jti": secrets.token_urlsafe(8),
        }
        body = self._b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = self._b64url(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def validate_ws_token(self, token: str, call_id: str = "") -> bool:
        """Validate a signed WS token: signature, expiry, and (soft) call binding."""
        if not token or not isinstance(token, str):
            return False
        key = self._token_key()
        if key is None:
            return False
        try:
            body, sig = token.split(".", 1)
        except ValueError:
            return False

        expected_sig = self._b64url(
            hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig, expected_sig):
            return False

        try:
            payload = json.loads(self._b64url_decode(body))
        except (ValueError, TypeError):
            return False

        if int(time.time()) > int(payload.get("exp", 0)):
            logger.warning("[SinchEventHandler] Rejecting WS token: expired")
            return False

        bound_cid = payload.get("cid", "")
        if bound_cid and call_id and not hmac.compare_digest(str(bound_cid), str(call_id)):
            logger.warning("[SinchEventHandler] Rejecting WS token: call id mismatch")
            return False

        return True

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64url_decode(data: str) -> bytes:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

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
        token = self._issue_ws_token(request_data.get("callid", ""))
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
