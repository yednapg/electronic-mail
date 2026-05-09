from __future__ import annotations

"""Backend-owned per-source-record summaries for history and thread views."""

import hashlib
import json

from app.db.models import StoredSourceRecord
from app.db.repository import (
    DEFAULT_USER_ID,
    list_source_record_summary_hashes,
    list_source_records_by_ids,
    upsert_source_record_summary,
)
from app.services.ai.decision import summarize_source_records

SOURCE_RECORD_SUMMARY_HASH_VERSION = "source-record-summary-v2"


def refresh_source_record_summaries(database_path: str, source_record_ids: list[str]) -> int:
    """Refresh compact summaries for missing or changed source records."""
    unique_ids = sorted({source_record_id for source_record_id in source_record_ids if source_record_id})

    if not unique_ids:
        return 0

    records = list_source_records_by_ids(database_path, unique_ids)
    record_hashes = {record.id: source_record_summary_hash(record) for record in records}
    existing_hashes = list_source_record_summary_hashes(database_path, record_hashes.keys())
    stale_records = [
        record
        for record in records
        if existing_hashes.get(record.id) != record_hashes[record.id]
    ]

    if not stale_records:
        return 0

    generated = summarize_source_records(stale_records)
    refreshed = 0
    for record in stale_records:
        summary = generated.summaries.get(record.id)
        if summary is None or not summary.strip():
            continue
        upsert_source_record_summary(
            database_path,
            source_record_id=record.id,
            user_id=DEFAULT_USER_ID,
            summary=summary.strip(),
            model=generated.model,
            generated_from_hash=record_hashes[record.id],
        )
        refreshed += 1

    return refreshed


def source_record_summary_hash(record: StoredSourceRecord) -> str:
    """Hash the stable fields that determine one source-record summary."""
    payload = {
        "version": SOURCE_RECORD_SUMMARY_HASH_VERSION,
        "id": record.id,
        "source": record.source,
        "thread_id": record.thread_id,
        "subject": record.subject,
        "sender": record.sender,
        "timestamp": record.timestamp,
        "summary_fields": {
            key: record.raw_payload.get(key)
            for key in ("subject", "from", "sender", "snippet", "summary", "body", "start", "end")
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
