"""In-process fan-out of transcript events to UI subscribers, per conversation."""

import asyncio
import logging
from collections import defaultdict

from app.transcription.events import HubEvent, TranscriptEvent

logger = logging.getLogger(__name__)


def _is_partial(ev: HubEvent) -> bool:
    return isinstance(ev, TranscriptEvent) and not ev.is_final


class TranscriptHub:
    """Publishes events to bounded per-subscriber queues.

    Must be used from the event loop thread. When a queue is full, the oldest
    partial is dropped first; partials are cheap to lose because a final
    follows. If only finals/status are queued, an incoming partial is dropped,
    otherwise the oldest final is dropped with a warning.
    """

    def __init__(self, max_queue: int = 500):
        self._max = max_queue
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._active: dict[str, None] = {}

    def subscribe(self, conversation_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max)
        self._subs[conversation_id].add(q)
        return q

    def unsubscribe(self, conversation_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(conversation_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            del self._subs[conversation_id]

    def publish(self, event: HubEvent) -> None:
        for q in list(self._subs.get(event.conversation_id, ())):
            self._offer(q, event)

    def _offer(self, q: asyncio.Queue, event: HubEvent) -> None:
        if not q.full():
            q.put_nowait(event)
            return
        items = list(q._queue)  # deque snapshot; safe on the loop thread
        drop = next((i for i, e in enumerate(items) if _is_partial(e)), None)
        if drop is None:
            if _is_partial(event):
                return
            drop = next(
                (i for i, e in enumerate(items) if isinstance(e, TranscriptEvent)), 0
            )
            logger.warning(
                "Transcript subscriber queue full for %s; dropping oldest final",
                event.conversation_id,
            )
        del q._queue[drop]
        q.put_nowait(event)

    def mark_active(self, conversation_id: str) -> None:
        self._active[conversation_id] = None

    def mark_ended(self, conversation_id: str) -> None:
        self._active.pop(conversation_id, None)

    def active(self) -> list[str]:
        return list(self._active)


_hub: TranscriptHub | None = None


def get_hub() -> TranscriptHub:
    global _hub
    if _hub is None:
        _hub = TranscriptHub()
    return _hub
