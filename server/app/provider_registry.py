"""Provider registry for telephony plugins.

Adding a new provider requires a package: app/providers/<name>/__init__.py
with a @register_provider decorator. No other files need changes.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Metadata about a registered telephony provider."""

    name: str
    display_name: str
    detect_key: str = ""  # config key that activates this provider
    required_config: list[str] = field(default_factory=list)
    register_routes: Callable | None = None


_PROVIDERS: dict[str, ProviderInfo] = {}

# Priority-ordered detection: (config_key, provider_name).
# First match wins. Returns None if no credentials configured.
# This is populated by @register_provider calls.
_DETECTION_ORDER: list[tuple[str, str]] = []


def register_provider(
    name: str,
    display_name: str,
    detect_key: str = "",
    required_config: list[str] | None = None,
):
    """Decorator to register a provider's route-registration function.

    Args:
        name: Provider identifier (matches the package name: app/providers/{name}/)
        display_name: Human-readable name for logs
        detect_key: Config key that activates this provider (empty = fallback)
        required_config: Keys validated at startup
    """

    def decorator(fn: Callable):
        _PROVIDERS[name] = ProviderInfo(
            name=name,
            display_name=display_name,
            detect_key=detect_key,
            required_config=required_config or [],
            register_routes=fn,
        )
        if detect_key:
            _DETECTION_ORDER.append((detect_key, name))
        return fn

    return decorator


def get_provider(name: str) -> ProviderInfo | None:
    return _PROVIDERS.get(name)


def detect_provider() -> str | None:
    """Detect the active provider from environment. First configured credential wins.

    Returns None if no provider credentials are found.
    """
    import os

    for key, name in _DETECTION_ORDER:
        if os.getenv(key):
            return name
    return None


def get_configured_providers() -> list[str]:
    """Return display names of all providers whose detect_key is set in env."""
    import os

    return [
        _PROVIDERS[name].display_name
        for key, name in _DETECTION_ORDER
        if os.getenv(key)
    ]
