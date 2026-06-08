"""Startup configuration validation.

Validates required settings and logs clear errors before the server accepts traffic.
Uses provider registry metadata — no per-provider logic here.
"""

import logging
import os
import sys

from app.provider_registry import get_provider

logger = logging.getLogger(__name__)


def validate_config(config: dict, provider: str | None) -> bool:
    """Validate configuration at startup.

    Returns True if the telephony provider is fully configured, False if not.
    Exits the process only if core Voice Live config is missing (nothing can work).
    The web client always remains available.
    """
    errors: list[str] = []

    # --- Core requirements (all providers) — fatal if missing ---
    if not config.get("AZURE_VOICE_LIVE_ENDPOINT"):
        errors.append(
            "AZURE_VOICE_LIVE_ENDPOINT is required. "
            "Set it in .env or as an environment variable pointing to your Azure AI Services endpoint."
        )

    if not config.get("AZURE_VOICE_LIVE_API_KEY") and not config.get(
        "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"
    ):
        errors.append(
            "No Voice Live credentials configured. "
            "Set either AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID (recommended) "
            "or AZURE_VOICE_LIVE_API_KEY."
        )

    if errors:
        logger.error("=" * 60)
        logger.error("STARTUP CONFIGURATION ERRORS:")
        for e in errors:
            logger.error("  - %s", e)
        logger.error("=" * 60)
        sys.exit(1)

    # --- No provider detected ---
    if not provider:
        logger.warning(
            "No telephony provider credentials found. "
            "Web client remains available."
        )
        return False

    # --- Provider-specific: warn if missing but don't exit ---
    provider_info = get_provider(provider)
    if provider_info:
        missing = [k for k in provider_info.required_config if not os.getenv(k)]
        if missing:
            for key in missing:
                logger.warning(
                    "%s not configured — %s telephony routes will not be registered. "
                    "Web client remains available.",
                    key,
                    provider_info.display_name,
                )
            return False

    logger.info("Configuration validated for provider=%s", provider)
    return True
