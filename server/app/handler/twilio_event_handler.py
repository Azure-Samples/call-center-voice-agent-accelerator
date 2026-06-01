"""Handler for Twilio webhook validation and incoming call TwiML generation."""

import hashlib
import hmac
import logging
import time
from urllib.parse import urlparse, urlunparse

from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

logger = logging.getLogger(__name__)


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
