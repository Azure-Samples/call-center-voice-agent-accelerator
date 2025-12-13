"""Azure Storage helpers for voicemail transcription persistence."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables.aio import TableClient, TableServiceClient
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from app.queue.sms_queue import SmsQueueMessage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StorageConfig:
    """Configuration for constructing storage clients."""

    connection_string: Optional[str] = None
    blob_account_url: Optional[str] = None
    table_account_url: Optional[str] = None
    container_name: str = "twilio-transcriptions"
    table_name: str = "VoicemailTranscripts"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_table_url(blob_account_url: str) -> str:
    if ".blob." not in blob_account_url:
        raise ValueError("Expected a blob endpoint URL containing '.blob.'")
    return blob_account_url.replace(".blob.", ".table.")


class VoicemailTranscriptionStore:
    """Persist voicemail transcripts to blob and table storage."""

    def __init__(self, config: StorageConfig) -> None:
        self._config = config
        self._clients_ready = False
        self._clients_lock = asyncio.Lock()
        self._blob_service: Optional[BlobServiceClient] = None
        self._container_client: Optional[ContainerClient] = None
        self._table_service: Optional[TableServiceClient] = None
        self._table_client: Optional[TableClient] = None
        self._credential: Optional[DefaultAzureCredential] = None

    async def close(self) -> None:
        if self._credential:
            await self._credential.close()

    async def store_transcript(
        self,
        *,
        call_sid: str,
        recording_sid: str,
        attempt: int,
        transcript: str,
        confidence: Optional[float],
        to_number: Optional[str] = None,
        from_number: Optional[str] = None,
    ) -> str:
        """Persist the latest transcript, returning the blob URL."""

        await self._ensure_clients()
        if not self._container_client or not self._table_client:
            raise RuntimeError("Storage clients are not initialized")

        blob_path = f"voicemails/{call_sid}/{recording_sid}.json"
        blob_client = self._container_client.get_blob_client(blob_path)

        payload = {
            "callSid": call_sid,
            "recordingSid": recording_sid,
            "attempt": attempt,
            "transcript": transcript,
            "confidence": confidence,
            "updatedAt": _utc_now_iso(),
        }

        body = json.dumps(payload).encode("utf-8")
        await blob_client.upload_blob(
            body,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
        blob_url = blob_client.url

        await self._upsert_table_record(
            call_sid=call_sid,
            recording_sid=recording_sid,
            attempt=attempt,
            blob_url=blob_url,
            confidence=confidence,
            to_number=to_number,
            from_number=from_number,
        )

        return blob_url

    async def mark_sms_requested(
        self,
        *,
        call_sid: str,
        recording_sid: str,
        attempt: Optional[int],
        sms_to: Optional[str] = None,
        sms_from: Optional[str] = None,
    ) -> int:
        await self._ensure_clients()
        if not self._table_client:
            raise RuntimeError("Storage clients are not initialized")

        now_iso = _utc_now_iso()

        entity = None
        resolved_attempt = attempt

        if attempt is None:
            query_filter = "PartitionKey eq @callSid and RecordingSid eq @recordingSid"
            parameters = {"callSid": call_sid, "recordingSid": recording_sid}
            max_attempt = -1
            async for existing in self._table_client.list_entities(filter=query_filter, parameters=parameters):
                existing_attempt = int(existing.get("Attempt", -1))
                if existing_attempt > max_attempt:
                    max_attempt = existing_attempt
                    entity = existing
            if entity is None:
                raise KeyError("Transcript attempt not found")
            resolved_attempt = int(entity.get("Attempt", max_attempt))
        else:
            row_key = self._row_key(recording_sid, attempt)
            try:
                entity = await self._table_client.get_entity(partition_key=call_sid, row_key=row_key)
            except ResourceNotFoundError as exc:
                raise KeyError("Transcript attempt not found") from exc

        row_key = self._row_key(recording_sid, resolved_attempt)
        entity["Attempt"] = resolved_attempt

        entity["SmsRequested"] = True
        if sms_to:
            entity["SmsDestination"] = sms_to
        if sms_from:
            entity["SmsSender"] = sms_from
        entity["UpdatedAt"] = now_iso
        await self._table_client.update_entity(entity=entity, mode="MERGE")

        return resolved_attempt

    async def _ensure_clients(self) -> None:
        if self._clients_ready:
            return

        async with self._clients_lock:
            if self._clients_ready:
                return

            cfg = self._config
            if cfg.connection_string:
                self._blob_service = BlobServiceClient.from_connection_string(cfg.connection_string)
                self._table_service = TableServiceClient.from_connection_string(cfg.connection_string)
            else:
                if not cfg.blob_account_url:
                    raise ValueError(
                        "Either AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_URL must be configured"
                    )
                table_url = cfg.table_account_url or _derive_table_url(cfg.blob_account_url)
                self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
                self._blob_service = BlobServiceClient(cfg.blob_account_url, credential=self._credential)
                self._table_service = TableServiceClient(endpoint=table_url, credential=self._credential)

            self._container_client = self._blob_service.get_container_client(cfg.container_name)
            if not await self._container_client.exists():
                raise RuntimeError(
                    "Blob container '%s' is not provisioned" % cfg.container_name
                )

            await self._ensure_table(cfg.table_name)
            self._table_client = self._table_service.get_table_client(cfg.table_name)
            self._clients_ready = True

    async def _ensure_table(self, table_name: str) -> None:
        assert self._table_service is not None
        await self._table_service.create_table_if_not_exists(table_name)

    async def build_sms_candidate(
        self,
        *,
        call_sid: str,
        recording_sid: str,
        attempt: int,
    ) -> Optional[SmsQueueMessage]:
        await self._ensure_clients()
        if not self._table_client:
            raise RuntimeError("Storage clients are not initialized")

        row_key = self._row_key(recording_sid, attempt)

        try:
            entity = await self._table_client.get_entity(partition_key=call_sid, row_key=row_key)
        except ResourceNotFoundError:
            logger.warning(
                "Sms candidate lookup failed; entity missing for callSid=%s recordingSid=%s attempt=%s",
                call_sid,
                recording_sid,
                attempt,
            )
            return None

        is_final = bool(entity.get("IsFinal"))
        sms_requested = bool(entity.get("SmsRequested"))
        sms_sent = bool(entity.get("SmsSent"))
        sms_queued = bool(entity.get("SmsQueued"))

        if not (is_final and sms_requested and not sms_sent and not sms_queued):
            return None

        destination = entity.get("SmsDestination") or entity.get("ToNumber")
        sender = entity.get("SmsSender") or entity.get("FromNumber")
        blob_url = entity.get("TranscriptBlobUrl")

        if not destination or not sender or not blob_url:
            logger.info(
                "Skipping SMS enqueue; missing sender/destination/blob for callSid=%s recordingSid=%s",
                call_sid,
                recording_sid,
            )
            return None

        retry_count = int(entity.get("SmsRetryCount", 0))

        return SmsQueueMessage(
            call_sid=call_sid,
            recording_sid=recording_sid,
            attempt=attempt,
            to_number=str(destination),
            from_number=str(sender),
            transcript_blob_url=str(blob_url),
            retry_count=retry_count,
        )

    async def mark_sms_enqueued(
        self,
        *,
        call_sid: str,
        recording_sid: str,
        attempt: int,
    ) -> None:
        await self._ensure_clients()
        if not self._table_client:
            raise RuntimeError("Storage clients are not initialized")

        patch = {
            "PartitionKey": call_sid,
            "RowKey": self._row_key(recording_sid, attempt),
            "SmsQueued": True,
            "UpdatedAt": _utc_now_iso(),
        }
        await self._table_client.update_entity(entity=patch, mode="MERGE")

    async def record_sms_send_result(
        self,
        *,
        call_sid: str,
        recording_sid: str,
        attempt: int,
        message_sid: str,
        status: str,
    ) -> None:
        await self._ensure_clients()
        if not self._table_client:
            raise RuntimeError("Storage clients are not initialized")

        patch = {
            "PartitionKey": call_sid,
            "RowKey": self._row_key(recording_sid, attempt),
            "SmsSent": True,
            "SmsQueued": True,
            "SmsMessageSid": message_sid,
            "SmsDeliveryStatus": status,
            "SmsRetryCount": 0,
            "UpdatedAt": _utc_now_iso(),
        }
        await self._table_client.update_entity(entity=patch, mode="MERGE")

    async def record_sms_failure(
        self,
        *,
        call_sid: str,
        recording_sid: str,
        attempt: int,
        reason: str,
        retry_count: int,
    ) -> None:
        await self._ensure_clients()
        if not self._table_client:
            raise RuntimeError("Storage clients are not initialized")

        patch = {
            "PartitionKey": call_sid,
            "RowKey": self._row_key(recording_sid, attempt),
            "SmsFailureReason": reason,
            "SmsRetryCount": retry_count,
            "UpdatedAt": _utc_now_iso(),
        }
        await self._table_client.update_entity(entity=patch, mode="MERGE")

    async def update_sms_status_by_sid(
        self,
        *,
        message_sid: str,
        status: str,
        error_code: Optional[str],
    ) -> bool:
        await self._ensure_clients()
        if not self._table_client:
            raise RuntimeError("Storage clients are not initialized")

        filter_query = "SmsMessageSid eq @sid"
        parameters = {"sid": message_sid}

        entities = []
        async for entity in self._table_client.list_entities(filter=filter_query, parameters=parameters):
            entities.append(entity)

        if not entities:
            logger.warning("No voicemail transcript record found for MessageSid=%s", message_sid)
            return False

        now_iso = _utc_now_iso()
        for entity in entities:
            patch = {
                "PartitionKey": entity["PartitionKey"],
                "RowKey": entity["RowKey"],
                "SmsDeliveryStatus": status,
                "UpdatedAt": now_iso,
            }
            if status in {"sent", "delivered", "delivered:final"}:
                patch["SmsSent"] = True
            if error_code is not None:
                patch["SmsFailureReason"] = error_code
            await self._table_client.update_entity(entity=patch, mode="MERGE")

        return True

    async def download_transcript(self, transcript_blob_url: str) -> Dict[str, Any]:
        await self._ensure_clients()
        if not self._blob_service:
            raise RuntimeError("Storage clients are not initialized")

        blob_client = self._blob_service.get_blob_client(container=self._config.container_name, blob=self._blob_name_from_url(transcript_blob_url))
        stream = await blob_client.download_blob()
        data = await stream.readall()
        return json.loads(data.decode("utf-8"))

    async def _upsert_table_record(
        self,
        *,
        call_sid: str,
        recording_sid: str,
        attempt: int,
        blob_url: str,
        confidence: Optional[float],
        to_number: Optional[str],
        from_number: Optional[str],
    ) -> None:
        if not self._table_client:
            raise RuntimeError("Storage clients are not initialized")

        table_client = self._table_client
        now_iso = _utc_now_iso()
        row_key = self._row_key(recording_sid, attempt)

        existing_entities: Dict[int, Dict[str, Any]] = {}
        query_filter = "PartitionKey eq @callSid and RecordingSid eq @recordingSid"
        parameters = {"callSid": call_sid, "recordingSid": recording_sid}

        async for entity in table_client.list_entities(filter=query_filter, parameters=parameters):
            entity_attempt = int(entity.get("Attempt", 0))
            existing_entities[entity_attempt] = entity

        max_existing_attempt = max(existing_entities.keys(), default=-1)
        max_attempt = max(attempt, max_existing_attempt)

        prior_entity = existing_entities.get(attempt)
        created_at = prior_entity.get("CreatedAt") if prior_entity else now_iso
        sms_requested = bool(prior_entity.get("SmsRequested", False)) if prior_entity else False
        sms_sent = bool(prior_entity.get("SmsSent", False)) if prior_entity else False
        sms_queued = bool(prior_entity.get("SmsQueued", False)) if prior_entity else False
        sms_delivery_status = prior_entity.get("SmsDeliveryStatus") if prior_entity else None
        sms_message_sid = prior_entity.get("SmsMessageSid") if prior_entity else None
        sms_failure_reason = prior_entity.get("SmsFailureReason") if prior_entity else None
        sms_destination = prior_entity.get("SmsDestination") if prior_entity else None
        sms_sender = prior_entity.get("SmsSender") if prior_entity else None
        existing_to_number = prior_entity.get("ToNumber") if prior_entity else None
        existing_from_number = prior_entity.get("FromNumber") if prior_entity else None
        retry_count = int(prior_entity.get("SmsRetryCount", 0)) if prior_entity else 0

        entity: Dict[str, Any] = {
            "PartitionKey": call_sid,
            "RowKey": row_key,
            "CallSid": call_sid,
            "RecordingSid": recording_sid,
            "Attempt": attempt,
            "TranscriptBlobUrl": blob_url,
            "IsFinal": attempt == max_attempt,
            "SmsRequested": sms_requested,
            "SmsSent": sms_sent,
            "SmsQueued": sms_queued,
            "SmsRetryCount": retry_count,
            "CreatedAt": created_at,
            "UpdatedAt": now_iso,
        }
        if confidence is not None:
            entity["Confidence"] = confidence
        if sms_delivery_status is not None:
            entity["SmsDeliveryStatus"] = sms_delivery_status
        if sms_message_sid is not None:
            entity["SmsMessageSid"] = sms_message_sid
        if sms_failure_reason is not None:
            entity["SmsFailureReason"] = sms_failure_reason

        if sms_destination:
            entity["SmsDestination"] = sms_destination
        if sms_sender:
            entity["SmsSender"] = sms_sender

        if to_number or existing_to_number:
            entity["ToNumber"] = to_number or existing_to_number
        if from_number or existing_from_number:
            entity["FromNumber"] = from_number or existing_from_number

        await table_client.upsert_entity(entity=entity, mode="MERGE")

        # Update older attempts to ensure only the highest attempt is marked as final.
        await self._update_attempt_flags(
            table_client=table_client,
            call_sid=call_sid,
            recording_sid=recording_sid,
            max_attempt=max_attempt,
            now_iso=now_iso,
            attempts=existing_entities,
            current_attempt=attempt,
        )

    async def _update_attempt_flags(
        self,
        *,
        table_client: TableClient,
        call_sid: str,
        recording_sid: str,
        max_attempt: int,
        now_iso: str,
        attempts: Dict[int, Dict[str, Any]],
        current_attempt: int,
    ) -> None:
        tracked: Dict[int, Tuple[str, Dict[str, Any]]] = {
            attempt: (entity["RowKey"], entity)
            for attempt, entity in attempts.items()
        }
        tracked.setdefault(current_attempt, (self._row_key(recording_sid, current_attempt), {}))

        for attempt_value, (row_key, entity) in tracked.items():
            desired_final = attempt_value == max_attempt
            if attempt_value == current_attempt and entity.get("IsFinal") == desired_final:
                continue
            if attempt_value != current_attempt and bool(entity.get("IsFinal")) == desired_final:
                continue

            patch = {
                "PartitionKey": call_sid,
                "RowKey": row_key,
                "IsFinal": desired_final,
                "UpdatedAt": now_iso,
            }
            await table_client.update_entity(entity=patch, mode="MERGE")

    @staticmethod
    def _row_key(recording_sid: str, attempt: int) -> str:
        return f"{recording_sid}:{attempt}"

    def _blob_name_from_url(self, blob_url: str) -> str:
        parsed = urlparse(blob_url)
        path = parsed.path.lstrip("/")
        prefix = f"{self._config.container_name}/"
        if not path.startswith(prefix):
            raise ValueError("Blob URL does not match configured container")
        return path[len(prefix):]


__all__ = ["VoicemailTranscriptionStore", "StorageConfig"]
