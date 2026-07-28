"""Handler for Bandwidth voice callbacks and BXML generation.

Bandwidth Programmable Voice uses BXML (Bandwidth XML) verbs to control calls.
On an incoming call we respond with a <StartStream> verb that opens a
bidirectional WebSocket media stream, kept alive by <StopStream wait="true">.

WebSocket audio is authenticated with a short-lived HMAC token passed as a
<StreamParam>, validated by the media handler when the stream 'start' message
arrives (mirrors the Twilio provider's approach).
"""

import base64
import hashlib
import hmac
import logging
import time
from xml.sax.saxutils import quoteattr

logger = logging.getLogger(__name__)

# Stream name used for the bidirectional AI agent stream.
_STREAM_NAME = "voice_agent"


class BandwidthEventHandler:
    """Validates Bandwidth webhooks and generates streaming BXML responses."""

    def __init__(self, config):
        self.account_id = config.get("BANDWIDTH_ACCOUNT_ID", "")
        # OAuth 2.0 Client Credentials (Bandwidth's current auth model). The
        # legacy API User username/password (Basic Auth) scheme is deprecated.
        self.client_id = config.get("BANDWIDTH_CLIENT_ID", "")
        self.client_secret = config.get("BANDWIDTH_CLIENT_SECRET", "")

    # ------------------------------------------------------------------
    # Webhook authentication (HTTP Basic Auth)
    # ------------------------------------------------------------------

    def validate_webhook(self, auth_header: str) -> bool | None:
        """Validate the Basic Auth credentials on an incoming Bandwidth callback.

        Bandwidth sends the username/password configured on the Voice Application's
        CallbackCreds as an HTTP Basic Authorization header. postdeploy sets those
        CallbackCreds to the Client ID/Secret pair, so we validate against them.

        Returns True if valid, False if invalid, None if not configured (503).
        """
        if not self.client_secret:
            # No secret configured — cannot validate. Treat as unavailable so we
            # don't accept unauthenticated callbacks.
            return None

        if not auth_header.startswith("Basic "):
            return False

        try:
            decoded = base64.b64decode(auth_header[len("Basic "):]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except Exception:
            return False

        user_ok = hmac.compare_digest(username, self.client_id)
        pass_ok = hmac.compare_digest(password, self.client_secret)
        return user_ok and pass_ok

    # ------------------------------------------------------------------
    # WebSocket token (HMAC, short-lived) — validated by the media handler
    # ------------------------------------------------------------------

    def _generate_ws_token(self) -> str:
        """Generate a short-lived HMAC token for WebSocket authentication."""
        timestamp = str(int(time.time()))
        sig = hmac.new(
            self.client_secret.encode(), timestamp.encode(), hashlib.sha256
        ).hexdigest()
        return f"{timestamp}.{sig}"

    # ------------------------------------------------------------------
    # BXML generation
    # ------------------------------------------------------------------

    def handle_voice_callback(self, request_data: dict, ws_url: str) -> str:
        """Return BXML for a Bandwidth voice callback.

        Opens a bidirectional media stream for call-initiating events and returns
        an empty response for lifecycle events (answer, disconnect, etc.).
        """
        event_type = str(request_data.get("eventType", "")).lower()
        call_id = request_data.get("callId", "")
        logger.info(
            "[BandwidthEventHandler] Voice callback: eventType=%s, callId=%s",
            event_type or "(none)",
            call_id or "(none)",
        )

        # Lifecycle events that must not restart the stream.
        if event_type in ("disconnect", "hangup", "bridgecomplete", "redirect"):
            return '<?xml version="1.0" encoding="UTF-8"?>\n<Response/>'

        return self.generate_stream_bxml(ws_url)

    def generate_stream_bxml(self, ws_url: str) -> str:
        """Generate BXML that opens a bidirectional media stream to Voice Live."""
        token = self._generate_ws_token()
        # quoteattr adds surrounding quotes and escapes XML special characters.
        dest = quoteattr(ws_url)
        name = quoteattr(_STREAM_NAME)
        token_attr = quoteattr(token)
        bxml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            "    <SpeakSentence>Please wait while we connect you to our AI assistant.</SpeakSentence>\n"
            f"    <StartStream name={name} mode=\"bidirectional\" tracks=\"inbound\" destination={dest}>\n"
            f"        <StreamParam name=\"token\" value={token_attr} />\n"
            "    </StartStream>\n"
            f"    <StopStream name={name} wait=\"true\" />\n"
            "</Response>"
        )
        logger.info("[BandwidthEventHandler] Returning stream BXML for: %s", ws_url)
        return bxml
