"""Handler for Plivo answer-webhook validation and Audio Streaming XML generation."""

import logging

from plivo.utils.signature_v3 import validate_v3_signature
from plivo.xml import ResponseElement, StreamElement

logger = logging.getLogger(__name__)

# Linear PCM 8kHz (telephony narrowband); resampled to Voice Live's 24kHz in the media handler.
STREAM_CONTENT_TYPE = "audio/x-l16;rate=8000"


class PlivoEventHandler:
    """Validates Plivo webhook signatures and generates Audio Streaming XML."""

    def __init__(self, config):
        self.auth_token = config.get("PLIVO_AUTH_TOKEN", "")

    def validate_request(
        self, method: str, url: str, params: dict, headers
    ) -> bool | None:
        """Validate the Plivo V3 webhook signature. True/False, or None if no auth token configured."""
        if not self.auth_token:
            return None
        signature = headers.get("X-Plivo-Signature-V3", "")
        nonce = headers.get("X-Plivo-Signature-V3-Nonce", "")
        if not signature or not nonce:
            return False
        try:
            return validate_v3_signature(
                method.upper(), url, nonce, self.auth_token, signature, params
            )
        except Exception:
            # Fail closed: malformed input can make the SDK raise; treat as invalid, not a 500.
            logger.warning(
                "Plivo signature validation raised; treating as invalid", exc_info=True
            )
            return False

    def generate_stream_xml(self, ws_url: str, status_url: str) -> str:
        """Generate Plivo XML streaming call audio bidirectionally to ws_url."""
        stream = StreamElement(
            ws_url,
            bidirectional=True,
            audioTrack="inbound",
            contentType=STREAM_CONTENT_TYPE,
            keepCallAlive=True,
            statusCallbackUrl=status_url,
            statusCallbackMethod="POST",
        )
        resp = ResponseElement()
        resp.add(stream)
        logger.info("Returning Plivo XML with stream URL: %s", ws_url)
        return resp.to_string(pretty=False)
