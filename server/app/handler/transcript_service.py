"""Service for storing conversation transcripts to Azure Blob Storage."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob.aio import BlobServiceClient

logger = logging.getLogger(__name__)


class TranscriptService:
    """Manages conversation transcript storage to Azure Blob Storage."""

    def __init__(self, config: dict):
        self.blob_endpoint = config.get("AZURE_STORAGE_BLOB_ENDPOINT")
        self.container_name = config.get("AZURE_STORAGE_CONTAINER_NAME", "transcripts")
        self.client_id = config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID")
        self._blob_client: Optional[BlobServiceClient] = None
        self._credential: Optional[ManagedIdentityCredential] = None

    async def _get_blob_client(self) -> BlobServiceClient:
        """Gets or creates the blob service client with managed identity."""
        if self._blob_client is None:
            if not self.blob_endpoint:
                raise ValueError("AZURE_STORAGE_BLOB_ENDPOINT is not configured")

            if self.client_id:
                self._credential = ManagedIdentityCredential(client_id=self.client_id)
            else:
                self._credential = ManagedIdentityCredential()

            self._blob_client = BlobServiceClient(
                account_url=self.blob_endpoint,
                credential=self._credential,
            )
        return self._blob_client

    async def save_transcript(
        self,
        conversation_id: str,
        caller_id: str,
        transcript_entries: list[dict],
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Saves a conversation transcript to blob storage.

        Args:
            conversation_id: Unique identifier for the conversation
            caller_id: Identifier for the caller (phone number or web client ID)
            transcript_entries: List of transcript entries with speaker and text
            metadata: Optional additional metadata

        Returns:
            The blob name where the transcript was saved
        """
        if not self.blob_endpoint:
            logger.warning("Storage not configured, skipping transcript save")
            return ""

        try:
            blob_client = await self._get_blob_client()
            container_client = blob_client.get_container_client(self.container_name)

            # Create blob name with date-based path for organization
            now = datetime.now(timezone.utc)
            date_path = now.strftime("%Y/%m/%d")
            blob_name = f"{date_path}/{conversation_id}.json"

            # Prepare transcript data
            transcript_data = {
                "conversation_id": conversation_id,
                "caller_id": caller_id,
                "start_time": (
                    transcript_entries[0]["timestamp"]
                    if transcript_entries
                    else now.isoformat()
                ),
                "end_time": now.isoformat(),
                "entries": transcript_entries,
                "metadata": metadata or {},
            }

            # Upload to blob storage
            blob = container_client.get_blob_client(blob_name)
            await blob.upload_blob(
                json.dumps(transcript_data, indent=2, ensure_ascii=False),
                overwrite=True,
                content_settings={
                    "content_type": "application/json",
                },
            )

            logger.info(
                "Saved transcript for conversation %s to %s", conversation_id, blob_name
            )
            return blob_name

        except Exception:
            logger.exception(
                "Failed to save transcript for conversation %s", conversation_id
            )
            return ""

    async def close(self):
        """Closes the blob client and credential."""
        if self._blob_client:
            await self._blob_client.close()
            self._blob_client = None
        if self._credential:
            await self._credential.close()
            self._credential = None


class ConversationTracker:
    """Tracks conversation turns and manages transcript saving."""

    def __init__(self, conversation_id: str, caller_id: str, transcript_service: TranscriptService):
        self.conversation_id = conversation_id
        self.caller_id = caller_id
        self.transcript_service = transcript_service
        self.entries: list[dict] = []
        self.metadata: dict = {}
        self._start_time = datetime.now(timezone.utc)

    def add_entry(self, speaker: str, text: str):
        """Adds a transcript entry."""
        if not text or not text.strip():
            return

        entry = {
            "speaker": speaker,
            "text": text.strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.entries.append(entry)
        logger.info("[Transcript] %s: %s", speaker.upper(), text.strip())

    def add_user_message(self, text: str):
        """Adds a user message to the transcript."""
        self.add_entry("user", text)

    def add_ai_message(self, text: str):
        """Adds an AI message to the transcript."""
        self.add_entry("ai", text)

    def set_metadata(self, key: str, value):
        """Sets metadata for the conversation."""
        self.metadata[key] = value

    async def save(self) -> str:
        """Saves the transcript to storage."""
        if not self.entries:
            logger.info("No transcript entries to save for conversation %s", self.conversation_id)
            return ""

        return await self.transcript_service.save_transcript(
            conversation_id=self.conversation_id,
            caller_id=self.caller_id,
            transcript_entries=self.entries,
            metadata=self.metadata,
        )
