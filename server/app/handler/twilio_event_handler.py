"""Handler for Twilio webhook validation and incoming call TwiML generation."""

import hashlib
import hmac
import logging
import time
from urllib.parse import urlparse, urlunparse

from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

logger = logging.getLogger(__name__)

# Token validity period in seconds
_TOKEN_TTL = 60


class TwilioEventHandler:
    """Validates Twilio webhook signatures and generates TwiML responses."""

    def __init__(self, config):
        self.auth_token = config.get("TWILIO_AUTH_TOKEN", "")

    def _reconstruct_url(self, raw_url: str) -> str:
        """Reconstruct URL as Twilio sees it (https, no port for voice HTTPS)."""
        parsed = urlparse(raw_url)
        return urlunparse(("https", parsed.hostname, parsed.path, parsed.params, parsed.query, ""))

    def _generate_ws_token(self) -> str:
        """Generate a short-lived HMAC token for WebSocket authentication."""
        timestamp = str(int(time.time()))
        sig = hmac.new(
            self.auth_token.encode(), timestamp.encode(), hashlib.sha256
        ).hexdigest()
        return f"{timestamp}.{sig}"

    def verify_ws_token(self, token: str) -> bool:
        """Verify a WebSocket token is valid and not expired."""
        if not self.auth_token or not token:
            return False
        parts = token.split(".", 1)
        if len(parts) != 2:
            return False
        timestamp_str, sig = parts
        try:
            timestamp = int(timestamp_str)
        except ValueError:
            return False
        # Check expiry
        if time.time() - timestamp > _TOKEN_TTL:
            return False
        # Verify signature
        expected = hmac.new(
            self.auth_token.encode(), timestamp_str.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)

    def validate_request(self, url: str, params: dict, signature: str) -> bool:
        """Validate a Twilio HTTP webhook request signature.

        Returns True if valid, False if invalid, None if auth token not configured.
        """
        if not self.auth_token:
            return None
        validator = RequestValidator(self.auth_token)
        reconstructed_url = self._reconstruct_url(url)
        return validator.validate(reconstructed_url, params, signature)

    def generate_stream_twiml(self, ws_url: str) -> str:
        """Generate TwiML response that connects the call to a media stream with auth token."""
        token = self._generate_ws_token()
        resp = VoiceResponse()
        resp.say("Please wait while we connect you to our AI assistant.")
        connect = resp.connect()
        stream = connect.stream(url=ws_url)
        stream.parameter(name="token", value=token)
        logger.info("Returning TwiML with stream URL: %s", ws_url)
        return str(resp)
