"""Asynchronous worker for processing outbound transcription SMS jobs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from azure.identity.aio import DefaultAzureCredential
from azure.storage.queue import BinaryBase64EncodePolicy
from azure.storage.queue.aio import QueueClient
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from app.queue import SmsQueueMessage
from app.storage.voicemail_store import StorageConfig, VoicemailTranscriptionStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QueueSettings:
    """Configuration for connecting to the outbound SMS queue."""

    queue_name: str
    connection_string: Optional[str] = None
    account_url: Optional[str] = None
    visibility_timeout: int = 30


class SmsQueueWorker:
    """Consume SMS jobs from Azure Queue Storage and send via Twilio."""

    def __init__(
        self,
        queue_client: QueueClient,
        store: VoicemailTranscriptionStore,
        twilio_client: TwilioClient,
        *,
        status_callback_url: Optional[str] = None,
        max_retries: int = 5,
        base_backoff_seconds: int = 30,
    ) -> None:
        self._queue_client = queue_client
        self._store = store
        self._twilio_client = twilio_client
        self._status_callback_url = status_callback_url
        self._max_retries = max_retries
        self._base_backoff_seconds = base_backoff_seconds
        self._credential: Optional[DefaultAzureCredential] = None
        self._visibility_timeout = 30

    @classmethod
    async def create(
        cls,
        queue_settings: QueueSettings,
        storage_config: StorageConfig,
        twilio_client: TwilioClient,
        *,
        status_callback_url: Optional[str] = None,
        max_retries: int = 5,
        base_backoff_seconds: int = 30,
    ) -> "SmsQueueWorker":
        """Instantiate the worker with configured Azure resources."""

        if queue_settings.connection_string:
            queue_client = QueueClient.from_connection_string(
                queue_settings.connection_string,
                queue_settings.queue_name,
                message_encode_policy=BinaryBase64EncodePolicy(),
            )
        elif queue_settings.account_url:
            credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            queue_client = QueueClient(
                queue_settings.account_url,
                queue_settings.queue_name,
                credential=credential,
                message_encode_policy=BinaryBase64EncodePolicy(),
            )
        else:
            raise ValueError("Either connection string or account URL must be provided for the queue")

        try:
            await queue_client.create_queue()
        except Exception:  # pragma: no cover - queue may already exist
            logger.debug("Queue '%s' already exists", queue_settings.queue_name)

        store = VoicemailTranscriptionStore(storage_config)
        worker = cls(
            queue_client,
            store,
            twilio_client,
            status_callback_url=status_callback_url,
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
        )

        worker._visibility_timeout = queue_settings.visibility_timeout

        if not queue_settings.connection_string:
            worker._credential = credential  # type: ignore[name-defined]

        return worker

    async def run_forever(self, *, poll_interval: int = 5) -> None:
        """Continuously poll the queue and process messages."""

        logger.info("Starting SMS queue worker loop")
        try:
            while True:
                processed = await self.run_once()
                if not processed:
                    await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:  # pragma: no cover - cooperative shutdown
            logger.info("SMS queue worker cancelled")
            raise
        finally:
            await self.close()

    async def run_once(self) -> bool:
        """Process up to one message from the queue."""

        async for message in self._queue_client.receive_messages(
            messages_per_page=1,
            visibility_timeout=self._visibility_timeout,
        ):
            await self._handle_message(message)
            return True
        return False

    async def _handle_message(self, message) -> None:
        """Decode, handle, and delete a queue message."""

        try:
            payload = SmsQueueMessage.from_json(message.content)
        except Exception as exc:  # pragma: no cover - defensive parsing
            logger.exception("Failed to parse queue message: %s", exc)
            await self._queue_client.delete_message(message.id, message.pop_receipt)
            return

        logger.info(
            "Processing SMS job callSid=%s recordingSid=%s attempt=%s retry=%s",
            payload.call_sid,
            payload.recording_sid,
            payload.attempt,
            payload.retry_count,
        )

        try:
            transcript = await self._store.download_transcript(payload.transcript_blob_url)
        except Exception as exc:
            logger.exception("Failed to download transcript for callSid=%s", payload.call_sid)
            await self._record_and_retry(message, payload, reason=f"blob-error:{exc}")
            return

        body = transcript.get("transcript")
        if not body:
            logger.warning("Transcript body missing for callSid=%s", payload.call_sid)
            await self._queue_client.delete_message(message.id, message.pop_receipt)
            return

        sms_body = body[:1600]

        message_kwargs = {
            "to": payload.to_number,
            "from_": payload.from_number,
            "body": sms_body,
        }
        if self._status_callback_url:
            message_kwargs["status_callback"] = self._status_callback_url

        try:
            twilio_message = await asyncio.to_thread(
                self._twilio_client.messages.create,
                **message_kwargs,
            )
        except TwilioRestException as exc:
            await self._record_and_retry(message, payload, reason=f"twilio:{exc.code}", status_code=exc.status)
            return
        except Exception as exc:  # pragma: no cover - network/runtime errors
            await self._record_and_retry(message, payload, reason=f"unexpected:{exc}")
            return

        await self._store.record_sms_send_result(
            call_sid=payload.call_sid,
            recording_sid=payload.recording_sid,
            attempt=payload.attempt,
            message_sid=twilio_message.sid,
            status=twilio_message.status or "sent",
        )

        logger.info(
            "SMS sent for callSid=%s recordingSid=%s messageSid=%s",
            payload.call_sid,
            payload.recording_sid,
            twilio_message.sid,
        )

        await self._queue_client.delete_message(message.id, message.pop_receipt)

    async def _record_and_retry(
        self,
        message,
        payload: SmsQueueMessage,
        *,
        reason: str,
        status_code: Optional[int] = None,
    ) -> None:
        """Record failure in storage and decide whether to retry."""

        should_retry = self._should_retry(status_code)

        new_retry_count = payload.retry_count + 1 if should_retry else payload.retry_count
        await self._store.record_sms_failure(
            call_sid=payload.call_sid,
            recording_sid=payload.recording_sid,
            attempt=payload.attempt,
            reason=reason,
            retry_count=new_retry_count,
        )

        await self._queue_client.delete_message(message.id, message.pop_receipt)

        if should_retry and new_retry_count <= self._max_retries:
            payload.retry_count = new_retry_count
            delay = min(self._base_backoff_seconds * (2 ** (new_retry_count - 1)), 3600)
            await self._queue_client.send_message(payload.to_json(), visibility_timeout=delay)
            logger.info(
                "Re-enqueued SMS job for callSid=%s attempt=%s retry=%s (delay=%ss)",
                payload.call_sid,
                payload.attempt,
                payload.retry_count,
                delay,
            )
        else:
            logger.error(
                "SMS job failed permanently for callSid=%s attempt=%s reason=%s",
                payload.call_sid,
                payload.attempt,
                reason,
            )

    @staticmethod
    def _should_retry(status_code: Optional[int]) -> bool:
        if status_code is None:
            return True
        if status_code >= 500:
            return True
        if status_code == 429:
            return True
        return False

    async def close(self) -> None:
        await self._queue_client.close()
        await self._store.close()
        if self._credential:
            await self._credential.close()


__all__ = ["QueueSettings", "SmsQueueWorker"]
