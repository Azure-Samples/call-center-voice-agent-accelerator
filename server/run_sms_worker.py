"""Entry point for running the outbound SMS queue worker."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Optional

from twilio.rest import Client as TwilioClient

from app.storage import StorageConfig
from app.worker import QueueSettings, SmsQueueWorker


def _derive_queue_url(blob_account_url: str) -> str:
    if ".blob." not in blob_account_url:
        raise ValueError("Expected a blob endpoint URL containing '.blob.'")
    return blob_account_url.replace(".blob.", ".queue.")


async def _run_worker() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

    queue_name = os.getenv("AZURE_STORAGE_QUEUE_NAME", "outbound-sms")
    if not queue_name:
        logging.error("AZURE_STORAGE_QUEUE_NAME must be configured")
        sys.exit(1)

    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    queue_account_url = os.getenv("AZURE_STORAGE_QUEUE_URL")
    blob_account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")

    account_url: Optional[str] = queue_account_url
    if not account_url and blob_account_url:
        try:
            account_url = _derive_queue_url(blob_account_url)
        except ValueError:
            logging.warning("Unable to derive queue endpoint from blob endpoint; continuing without queue URL")

    if not connection_string and not account_url:
        logging.error("Either AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_QUEUE_URL must be set")
        sys.exit(1)

    queue_settings = QueueSettings(
        queue_name=queue_name,
        connection_string=connection_string,
        account_url=account_url,
    )

    storage_config = StorageConfig(
        connection_string=connection_string,
        blob_account_url=blob_account_url,
        table_account_url=os.getenv("AZURE_STORAGE_TABLE_URL"),
        container_name=os.getenv("AZURE_STORAGE_VOICEMAIL_CONTAINER", "twilio-transcriptions"),
        table_name=os.getenv("AZURE_STORAGE_VOICEMAIL_TABLE", "VoicemailTranscripts"),
    )

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    if not account_sid or not auth_token:
        logging.error("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured")
        sys.exit(1)

    twilio_client = TwilioClient(account_sid, auth_token)
    status_callback_url = os.getenv("TWILIO_STATUS_CALLBACK_URL")

    worker = await SmsQueueWorker.create(
        queue_settings,
        storage_config,
        twilio_client,
        status_callback_url=status_callback_url,
    )

    try:
        await worker.run_forever()
    finally:
        await worker.close()


if __name__ == "__main__":
    try:
        asyncio.run(_run_worker())
    except KeyboardInterrupt:
        print("\nSMS worker stopped by user", file=sys.stderr)