"""Azure Queue producer for outbound voicemail transcription SMS."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

from azure.identity.aio import DefaultAzureCredential
from azure.storage.queue import BinaryBase64EncodePolicy
from azure.storage.queue.aio import QueueClient

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SmsQueueMessage:
    """Payload describing an SMS send request."""

    call_sid: str
    recording_sid: str
    attempt: int
    to_number: str
    from_number: str
    transcript_blob_url: str
    retry_count: int = 0

    def to_json(self) -> str:
        payload = {
            "callSid": self.call_sid,
            "recordingSid": self.recording_sid,
            "attempt": self.attempt,
            "to": self.to_number,
            "from": self.from_number,
            "transcriptBlobUrl": self.transcript_blob_url,
            "retryCount": self.retry_count,
        }
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "SmsQueueMessage":
        data = json.loads(raw)
        return cls(
            call_sid=data["callSid"],
            recording_sid=data["recordingSid"],
            attempt=int(data.get("attempt", 0)),
            to_number=data["to"],
            from_number=data["from"],
            transcript_blob_url=data["transcriptBlobUrl"],
            retry_count=int(data.get("retryCount", 0)),
        )


class SmsQueueProducer:
    """Lightweight wrapper for sending SMS jobs to Azure Queue Storage."""

    def __init__(self, queue_client: QueueClient) -> None:
        self._queue_client = queue_client
        self._lock = asyncio.Lock()
        self._credential: Optional[DefaultAzureCredential] = None

    @classmethod
    async def create(
        cls,
        *,
        connection_string: Optional[str] = None,
        account_url: Optional[str] = None,
        queue_name: str,
    ) -> Optional["SmsQueueProducer"]:
        if not connection_string and not account_url:
            logger.info("SMS queue producer disabled; no storage configuration supplied")
            return None

        credential: Optional[DefaultAzureCredential] = None

        if connection_string:
            queue_client = QueueClient.from_connection_string(
                connection_string,
                queue_name,
                message_encode_policy=BinaryBase64EncodePolicy(),
            )
        else:
            credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            queue_client = QueueClient(
                account_url,
                queue_name,
                credential=credential,
                message_encode_policy=BinaryBase64EncodePolicy(),
            )

        try:
            await queue_client.create_queue()
        except Exception:  # pragma: no cover - queue may already exist
            logger.debug("Queue '%s' already exists", queue_name)

        producer = cls(queue_client)
        producer._credential = credential
        return producer

    async def enqueue(self, message: SmsQueueMessage, *, visibility_timeout: int = 0) -> None:
        payload = message.to_json()
        async with self._lock:
            await self._queue_client.send_message(payload, visibility_timeout=visibility_timeout)
            logger.info(
                "Queued SMS job for callSid=%s recordingSid=%s attempt=%s",
                message.call_sid,
                message.recording_sid,
                message.attempt,
            )

    async def close(self) -> None:
        await self._queue_client.close()
        if self._credential:
            await self._credential.close()


__all__ = ["SmsQueueMessage", "SmsQueueProducer"]
