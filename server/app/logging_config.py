"""Structured logging with correlation ID support for call tracing."""

import contextvars
import logging
import uuid

# Context variable holding the current call's correlation ID
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
    """Injects correlation_id into every log record."""

    def filter(self, record):
        record.correlation_id = _correlation_id.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with structured format including correlation IDs."""
    fmt = "%(asctime)s %(levelname)s [%(name)s] [cid=%(correlation_id)s] %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(CorrelationFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
