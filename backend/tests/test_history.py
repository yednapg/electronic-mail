from __future__ import annotations

from pathlib import Path
import hashlib
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.routes import history as history_routes
from app.db.models import StoredSourceRecord
from app.db.repository import (
    DEFAULT_USER_ID,
    append_entity_outcome,
    attach_record_to_entity,
    create_entity,
    initialize_database,
    upsert_ai_suggestion,
    upsert_entity_state,
    upsert_source_record_summary,
    upsert_source_records,
)
from app.main import app
from app.services.ai.decision import SourceRecordSummaryBatch
from app.services.source_record_summaries import refresh_source_record_summaries, source_record_summary_hash


def make_record(
    *,
    record_id: str,
    received_at: str,
    source: str = "gmail",
    thread_id: str | None = None,
    subject: str | None = None,
    sender: str | None = None,
    snippet: str | None = None,
    body: str | None = None,
) -> StoredSourceRecord:
    raw_payload: dict[str, object] = {"user_id": DEFAULT_USER_ID}
    if subject is not None:
        raw_payload["subject"] = subject
    if sender is not None:
        raw_payload["from"] = sender
    if snippet is not None:
        raw_payload["snippet"] = snippet
    if body is not None:
        raw_payload["body"] = body

    return StoredSourceRecord(
        id=record_id,
        source=source,
        thread_id=thread_id,
        subject=subject,
        sender=sender,
        timestamp=received_at,
        raw_payload=raw_payload,
        created_at=received_at,
    )


def flatten_history_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for year in payload["years"]:
        for month in year["months"]:
            for day in month["days"]:
                rows.extend(day["rows"])
    return rows


class HistoryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.database_path = Path(self.tmp_dir.name) / "history.db"
        initialize_database(str(self.database_path))
        self.settings_patch = patch.object(
            history_routes,
            "settings",
            SimpleNamespace(database_path=self.database_path),
        )
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.tmp_dir.cleanup)
        self.client = TestClient(app)

    def test_history_groups_source_records_by_year_month_and_day(self) -> None:
        upsert_source_records(
            str(self.database_path),
            [
                make_record(
                    record_id="gmail-2025",
                    thread_id="thread-2025",
                    received_at="2025-12-31T23:00:00+00:00",
                    subject="Older",
                ),
                make_record(
                    record_id="gmail-2026-april",
                    thread_id="thread-april",
                    received_at="2026-04-05T08:30:00+00:00",
                    subject="April",
                ),
                make_record(
                    record_id="gmail-2026-may",
                    thread_id="thread-may",
                    received_at="2026-05-08T09:00:00+00:00",
                    subject="May",
                ),
            ],
        )

        response = self.client.get("/v1/history")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual([year["year"] for year in payload["years"]], ["2026", "2025"])
        self.assertEqual([month["month"] for month in payload["years"][0]["months"]], ["2026-05", "2026-04"])
        self.assertEqual(payload["years"][0]["months"][0]["days"][0]["date"], "2026-05-08")
        self.assertEqual(payload["years"][0]["months"][0]["days"][0]["rows"][0]["source_record_id"], "gmail-2026-may")
        self.assertEqual(payload["years"][1]["months"][0]["days"][0]["date"], "2025-12-31")

    def test_history_paginates_source_records_without_losing_total(self) -> None:
        upsert_source_records(
            str(self.database_path),
            [
                make_record(record_id="record-1", received_at="2026-05-08T09:00:00+00:00"),
                make_record(record_id="record-2", received_at="2026-05-07T09:00:00+00:00"),
                make_record(record_id="record-3", received_at="2026-05-06T09:00:00+00:00"),
            ],
        )

        response = self.client.get("/v1/history?limit=1&offset=1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["limit"], 1)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual(payload["total"], 3)
        self.assertEqual([row["source_record_id"] for row in flatten_history_rows(payload)], ["record-2"])

    @patch("app.services.source_record_summaries.summarize_source_records")
    def test_history_uses_backend_owned_source_record_summary(self, mock_summarize: Mock) -> None:
        record = make_record(
            record_id="message-1",
            thread_id="thread-1",
            received_at="2026-05-08T09:00:00+00:00",
            subject="Order update",
            snippet="Apple says your iPhone has shipped.",
        )
        upsert_source_records(str(self.database_path), [record])
        mock_summarize.return_value = SourceRecordSummaryBatch(
            model="test-summary-model",
            summaries={"message-1": "Apple shipped your iPhone order."},
        )

        refreshed = refresh_source_record_summaries(str(self.database_path), ["message-1"])
        response = self.client.get("/v1/history")

        self.assertEqual(refreshed, 1)
        row = flatten_history_rows(response.json())[0]
        self.assertEqual(row["title"], "Apple shipped your iPhone order.")
        self.assertEqual(row["summary"], "Apple shipped your iPhone order.")
        mock_summarize.assert_called_once()

    def test_source_record_summary_hash_includes_copy_prompt_version(self) -> None:
        record = make_record(
            record_id="message-1",
            thread_id="thread-1",
            received_at="2026-05-08T09:00:00+00:00",
            subject="Order update",
            snippet="Apple says your iPhone has shipped.",
        )
        legacy_payload = {
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
        legacy_hash = hashlib.sha256(json.dumps(legacy_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()

        self.assertNotEqual(source_record_summary_hash(record), legacy_hash)

    @patch("app.services.integrations.google.fetch_google_source_records")
    @patch("app.api.routes.gmail.archive_gmail_thread_service")
    def test_history_enriches_rows_without_touching_gmail(
        self,
        mock_archive_thread: Mock,
        mock_fetch_source_records: Mock,
    ) -> None:
        record = make_record(
            record_id="message-1",
            thread_id="thread-1",
            received_at="2026-05-08T09:00:00+00:00",
            subject="Your refund request",
            sender="Support <support@example.com>",
            snippet="We received your refund request.",
            body="Longer body that should not win over snippet.",
        )
        upsert_source_records(str(self.database_path), [record])
        entity = create_entity(str(self.database_path), "gmail-thread:thread-1")
        attach_record_to_entity(str(self.database_path), entity.id, record.id)
        upsert_entity_state(str(self.database_path), entity.id, "received", None)
        upsert_source_record_summary(
            str(self.database_path),
            source_record_id=record.id,
            user_id=DEFAULT_USER_ID,
            summary="Support received your refund request.",
            model="test-summary-model",
            generated_from_hash="hash-1",
        )
        upsert_ai_suggestion(
            str(self.database_path),
            entity_id=entity.id,
            title="Refund request is being reviewed",
            explanation="The provider acknowledged your refund and is reviewing it.",
            action="none",
            suggested_timing="later",
            suggested_priority=2,
            suggested_visibility=True,
            model="test-model",
            generated_from_updated_at=entity.updated_at,
        )
        append_entity_outcome(
            str(self.database_path),
            user_id=DEFAULT_USER_ID,
            entity_id=entity.id,
            outcome_type="snooze",
            snooze_until="2026-05-12T09:00:00+00:00",
            note="Wait for provider.",
        )

        response = self.client.get("/v1/history")

        self.assertEqual(response.status_code, 200)
        row = flatten_history_rows(response.json())[0]
        self.assertEqual(row["source_record_id"], "message-1")
        self.assertEqual(row["entity_id"], entity.id)
        self.assertEqual(row["source"], "gmail")
        self.assertEqual(row["thread_id"], "thread-1")
        self.assertEqual(row["received_at"], "2026-05-08T09:00:00+00:00")
        self.assertEqual(row["subject"], "Your refund request")
        self.assertEqual(row["title"], "Support received your refund request.")
        self.assertEqual(row["sender"], "Support <support@example.com>")
        self.assertEqual(row["snippet"], "We received your refund request.")
        self.assertEqual(row["summary"], "Support received your refund request.")
        self.assertEqual(row["current_state"], "waiting")
        self.assertEqual(row["lifecycle_state"], "active")
        self.assertEqual(row["outcome_type"], "snooze")
        self.assertIsNotNone(row["outcome_created_at"])
        mock_fetch_source_records.assert_not_called()
        mock_archive_thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
