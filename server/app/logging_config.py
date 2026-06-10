"""Structured logging with per-call correlation ID.

Every log line includes a `cid` field — a unique ID generated when a call or
request arrives. Filter logs by cid to see the full lifecycle of one call.

Provider-native IDs (Twilio call SID, ACS call connection ID, etc.) appear in
the log messages where they are used.
"""

import contextvars
import logging
import uuid

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def get_correlation_id() -> str:
    """Get the current correlation ID."""
    return _correlation_id.get()


def set_correlation_id(cid: str) -> contextvars.Token:
    """Set the correlation ID for the current async context."""
    return _correlation_id.set(cid)


def new_correlation_id() -> str:
    """Generate and set a new correlation ID. Returns the ID."""
    cid = uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


class CorrelationFilter(logging.Filter):
    """Injects cid into every log record."""

    def filter(self, record):
        record.correlation_id = _correlation_id.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with correlation ID in every line."""
    fmt = "%(asctime)s %(levelname)s [%(name)s] [cid=%(correlation_id)s] %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(CorrelationFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
