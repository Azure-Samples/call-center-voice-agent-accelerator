"""Manages active call lifecycle: concurrency limits, timeouts, and zombie cleanup."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_CONCURRENT_CALLS = 50
DEFAULT_MAX_CALL_DURATION = 3600  # 1 hour
DEFAULT_IDLE_TIMEOUT = 120  # 2 minutes no audio = zombie


@dataclass
class CallSession:
    """Tracks a single active call."""

    call_id: str
    provider: str
    started_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    _timeout_task: asyncio.Task | None = field(default=None, repr=False)


class CallManager:
    """Singleton that tracks active calls and enforces limits."""

    def __init__(
        self,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_CALLS,
        max_duration: int = DEFAULT_MAX_CALL_DURATION,
        idle_timeout: int = DEFAULT_IDLE_TIMEOUT,
    ):
        self._max_concurrent = max_concurrent
        self._max_duration = max_duration
        self._idle_timeout = idle_timeout
        self._calls: dict[str, CallSession] = {}
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return len(self._calls)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    async def acquire(self, call_id: str, provider: str) -> bool:
        """Try to register a new call. Returns False if at capacity."""
        async with self._lock:
            if len(self._calls) >= self._max_concurrent:
                logger.warning(
                    "Call rejected: at capacity (%d/%d) call_id=%s provider=%s",
                    len(self._calls),
                    self._max_concurrent,
                    call_id,
                    provider,
                )
                return False
            session = CallSession(call_id=call_id, provider=provider)
            self._calls[call_id] = session
            logger.info(
                "Call started: call_id=%s provider=%s active=%d/%d",
                call_id,
                provider,
                len(self._calls),
                self._max_concurrent,
            )
            return True

    async def release(self, call_id: str) -> None:
        """Unregister a call when it ends."""
        async with self._lock:
            session = self._calls.pop(call_id, None)
            if session and session._timeout_task:
                session._timeout_task.cancel()
            if session:
                duration = time.monotonic() - session.started_at
                logger.info(
                    "Call ended: call_id=%s provider=%s duration=%.1fs active=%d/%d",
                    call_id,
                    session.provider,
                    duration,
                    len(self._calls),
                    self._max_concurrent,
                )

    def touch(self, call_id: str) -> None:
        """Update last activity timestamp for a call (call on each audio frame)."""
        session = self._calls.get(call_id)
        if session:
            session.last_activity = time.monotonic()

    def is_expired(self, call_id: str) -> bool:
        """Check if a call has exceeded max duration or is idle."""
        session = self._calls.get(call_id)
        if not session:
            return False
        now = time.monotonic()
        if now - session.started_at > self._max_duration:
            logger.warning(
                "Call expired (max duration): call_id=%s duration=%.0fs",
                call_id,
                now - session.started_at,
            )
            return True
        if now - session.last_activity > self._idle_timeout:
            logger.warning(
                "Call expired (idle): call_id=%s idle=%.0fs",
                call_id,
                now - session.last_activity,
            )
            return True
        return False

    def get_stats(self) -> dict:
        """Return current call manager statistics."""
        now = time.monotonic()
        return {
            "active_calls": len(self._calls),
            "max_concurrent": self._max_concurrent,
            "max_call_duration_s": self._max_duration,
            "idle_timeout_s": self._idle_timeout,
            "calls": [
                {
                    "call_id": s.call_id,
                    "provider": s.provider,
                    "duration_s": round(now - s.started_at, 1),
                    "idle_s": round(now - s.last_activity, 1),
                }
                for s in self._calls.values()
            ],
        }
