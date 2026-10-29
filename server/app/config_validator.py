"""Startup configuration validation.

Validates required settings and logs clear errors before the server accepts traffic.
Uses provider registry metadata — no per-provider logic here.
"""

import logging
import os
import sys

from app.provider_registry import get_provider

logger = logging.getLogger(__name__)

CALL_MODES = ("voice_agent", "transcription")
TRANSCRIPTION_BACKENDS = ("azure_speech", "fake")
# Providers that support listen-only transcription in CALL_MODE=transcription.
TRANSCRIPTION_PROVIDERS = ("genesys",)


def _core_errors(config: dict) -> list[str]:
    errors: list[str] = []
    mode = config.get("CALL_MODE") or "voice_agent"
    if mode not in CALL_MODES:
        return [f"CALL_MODE must be one of {', '.join(CALL_MODES)} (got '{mode}')."]

    if mode == "transcription":
        backend = (config.get("TRANSCRIPTION_BACKEND") or "azure_speech").lower()
        if backend not in TRANSCRIPTION_BACKENDS:
            return [
                f"TRANSCRIPTION_BACKEND must be one of {', '.join(TRANSCRIPTION_BACKENDS)} (got '{backend}')."
            ]
        if backend == "fake":
            logger.warning("TRANSCRIPTION_BACKEND=fake — transcripts are placeholders (demo/test only).")
            return []
        if not (config.get("AZURE_SPEECH_ENDPOINT") or config.get("AZURE_VOICE_LIVE_ENDPOINT")):
            errors.append(
                "Transcription mode requires AZURE_SPEECH_ENDPOINT or AZURE_VOICE_LIVE_ENDPOINT "
                "(your Azure AI Services endpoint)."
            )
        elif not (
            config.get("AZURE_SPEECH_KEY")
            or config.get("AZURE_VOICE_LIVE_API_KEY")
            or config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID")
        ):
            logger.warning(
                "No Speech key or managed identity configured — using DefaultAzureCredential "
                "(e.g. `az login`) for Azure AI Speech."
            )
        return errors

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
    return errors


def validate_config(config: dict, provider: str | None) -> bool:
    """Validate configuration at startup.

    Returns True if the telephony provider is fully configured, False if not.
    Exits the process only if core config for the selected CALL_MODE is missing
    (nothing can work). The web client always remains available.
    """
    errors = _core_errors(config)
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

    if (config.get("CALL_MODE") or "voice_agent") == "transcription" and provider not in TRANSCRIPTION_PROVIDERS:
        logger.warning(
            "Provider '%s' does not support CALL_MODE=transcription (supported: %s) — "
            "telephony routes will not be registered. Web harness remains available.",
            provider,
            ", ".join(TRANSCRIPTION_PROVIDERS),
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
