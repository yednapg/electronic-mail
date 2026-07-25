from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError

from app.db.mail_groups import (
    GmailBodyUpdateResult,
    GmailInitialWindowEntry,
    GmailMessageRecord,
    GmailSyncProgress,
    _update_gmail_body_progress_on_connection,
    commit_gmail_initial_window_metadata_batch,
    delete_gmail_messages,
    initialize_gmail_initial_window,
    list_messages_needing_body_fetch,
    mark_gmail_messages_body_fetch_state,
    start_gmail_sync_progress,
)
from app.services.gmail_importer import (
    GmailInlineAttachmentRetryable,
    _batch_get_thread_metadata_payloads,
    _download_gmail_body_messages,
    _encode_full_thread_mailbox_cursor,
    _gmail_body_fetch_account_scope,
    _inline_attachment_resolver,
    _ensure_initial_window_body_jobs,
    _list_initial_window_threads,
    _persist_hydrated_body_messages,
    _run_progressive_gmail_bootstrap,
    enqueue_gmail_body_backfill,
    run_gmail_body_backfill,
    run_gmail_full_reconciliation,
)
from app.services.mail_groups import _sync_progress_kwargs


def _message(index: int, *, fetched: bool = False) -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id=f"message-{index:03d}",
        gmail_thread_id=f"thread-{index:03d}",
        history_id=str(index),
        label_ids=["INBOX"],
        internal_date=f"2026-07-25T00:{index % 60:02d}:00+00:00",
        subject=f"Subject {index}",
        sender="sender@example.com",
        recipients={"to": "me@example.com"},
        headers={"subject": f"Subject {index}"},
        snippet="Preview",
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body="Downloaded" if fetched else None,
        extracted_signals={},
        body_hash=f"hash-{index}",
        created_at="",
        updated_at="",
        body_fetch_status="fetched" if fetched else "missing",
    )


class _ThreadRequest:
    def __init__(self, service: "_ThreadBatchService", thread_id: str) -> None:
        self.service = service
        self.thread_id = thread_id

    def execute(self):
        self.service.direct_ids.append(self.thread_id)
        return {"id": self.thread_id, "messages": []}


class _Threads:
    def __init__(self, service: "_ThreadBatchService") -> None:
        self.service = service

    def get(self, **kwargs):
        self.service.get_calls.append(dict(kwargs))
        return _ThreadRequest(self.service, str(kwargs["id"]))

    def list(self, **kwargs):
        self.service.list_calls.append(dict(kwargs))
        index = len(self.service.list_calls) - 1
        response = (
            self.service.list_responses[index]
            if index < len(self.service.list_responses)
            else {"threads": [{"id": "thread-1"}]}
        )
        return SimpleNamespace(execute=lambda: response)


class _Users:
    def __init__(self, service: "_ThreadBatchService") -> None:
        self.service = service

    def threads(self):
        return _Threads(self.service)


class _ThreadBatch:
    def __init__(self, service: "_ThreadBatchService", callback) -> None:
        self.service = service
        self.callback = callback
        self.requests: list[tuple[str, _ThreadRequest]] = []

    def add(self, request, *, request_id: str) -> None:
        self.requests.append((request_id, request))

    def execute(self) -> None:
        self.service.batch_sizes.append(len(self.requests))
        for request_id, _request in self.requests:
            if request_id in self.service.omitted_ids:
                continue
            if request_id in self.service.error_statuses:
                self.callback(
                    request_id,
                    None,
                    HttpError(
                        SimpleNamespace(
                            status=self.service.error_statuses[request_id],
                            reason="Provider error",
                        ),
                        b"{}",
                    ),
                )
                continue
            self.callback(request_id, {"id": request_id, "messages": []}, None)


class _ThreadBatchService:
    def __init__(
        self,
        *,
        omitted_ids: set[str] | None = None,
        error_statuses: dict[str, int] | None = None,
        list_responses: list[dict] | None = None,
    ) -> None:
        self.omitted_ids = omitted_ids or set()
        self.error_statuses = error_statuses or {}
        self.batch_sizes: list[int] = []
        self.direct_ids: list[str] = []
        self.get_calls: list[dict] = []
        self.list_calls: list[dict] = []
        self.list_responses = list_responses or []

    def users(self):
        return _Users(self)

    def new_batch_http_request(self, *, callback):
        return _ThreadBatch(self, callback)


class _Result:
    def __init__(self, *, scalar=None, row=None, rows=None, rowcount=0) -> None:
        self.scalar = scalar
        self.row = row
        self.rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.scalar

    def mappings(self):
        return self

    def one(self):
        return self.row

    def first(self):
        return self.row

    def all(self):
        return self.rows


class _InitialCommitConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        values = dict(params or {})
        self.calls.append((sql, values))
        if "SELECT 1" in sql and "FOR UPDATE" in sql:
            return _Result(scalar=1)
        if "AS metadata_count" in sql:
            return _Result(
                row={
                    "metadata_count": 0,
                    "body_ready_count": 0,
                    "target_count": 0,
                }
            )
        return _Result(row=None)


class _PriorityProgressConnection:
    def __init__(self) -> None:
        self.update_params: dict | None = None

    def execute(self, statement, params=None):
        sql = str(statement)
        if "AS initial_body_ready_count" in sql:
            # Seventy-five lower-priority entries completed out of order. The
            # old unbounded COUNT would report 75; the priority predicate must
            # keep readiness at zero until positions 0...24 are done.
            priority_only = "entries.position < state.initial_body_target_count" in sql
            return _Result(
                row={
                    "initial_body_ready_count": 0 if priority_only else 75,
                    "ready_thread_count": 75,
                }
            )
        self.update_params = dict(params or {})
        return _Result()


class _TerminalBodyDeleteConnection:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, statement, _params=None):
        sql = str(statement)
        self.sql.append(sql)
        if "AS thread_id" in sql and "FROM gmail_messages" in sql:
            return _Result(rows=[{"thread_id": "thread-1"}])
        if "SELECT DISTINCT group_id" in sql:
            return _Result(rows=[])
        if "UPDATE gmail_initial_window_entries AS entries" in sql and "message_count = 0" in sql:
            return _Result(rowcount=1)
        return _Result()


class ProgressiveGmailBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql://example/db")
        descriptor_page = patch(
            "app.services.gmail_importer.backfill_gmail_attachment_descriptors_page",
            return_value=(0, False, None),
        )
        descriptor_page.start()
        self.addCleanup(descriptor_page.stop)

    def test_initial_window_uses_exact_cutoff_and_follows_short_pages(self) -> None:
        first_page = {
            "threads": [{"id": f"thread-{index}"} for index in range(37)],
            "nextPageToken": "page-2",
            "resultSizeEstimate": 358,
        }
        second_page = {
            "threads": [{"id": "thread-36"}, {"id": "thread-37"}],
            "resultSizeEstimate": 358,
        }
        service = _ThreadBatchService(list_responses=[first_page, second_page])
        now = datetime(2026, 7, 25, 12, 30, tzinfo=timezone.utc)
        with patch(
            "app.services.gmail_importer.create_authorized_credentials",
            return_value=object(),
        ), patch(
            "app.services.gmail_importer.build_google_service",
            return_value=service,
        ):
            response = _list_initial_window_threads(self.settings, user_id="user-1", now=now)

        self.assertEqual(len(response["threads"]), 38)
        self.assertEqual(response["resultSizeEstimate"], 358)
        self.assertNotIn("nextPageToken", response)
        exact_cutoff = int((now - timedelta(days=90)).timestamp())
        self.assertEqual(
            service.list_calls,
            [
                {
                    "userId": "me",
                    "q": f"after:{exact_cutoff}",
                    "maxResults": 100,
                    "includeSpamTrash": True,
                },
                {
                    "userId": "me",
                    "q": f"after:{exact_cutoff}",
                    "maxResults": 63,
                    "includeSpamTrash": True,
                    "pageToken": "page-2",
                },
            ],
        )

    def test_committed_initial_metadata_reconstructs_lost_body_jobs(self) -> None:
        entries = [
            GmailInitialWindowEntry(
                user_id="user-1",
                generation_id="generation-1",
                gmail_thread_id="thread-priority",
                position=0,
                message_count=1,
                metadata_ready_at="2026-07-25T00:00:00+00:00",
                body_ready_at=None,
            ),
            GmailInitialWindowEntry(
                user_id="user-1",
                generation_id="generation-1",
                gmail_thread_id="thread-remainder",
                position=25,
                message_count=1,
                metadata_ready_at="2026-07-25T00:00:00+00:00",
                body_ready_at=None,
            ),
        ]
        with patch(
            "app.services.gmail_importer.list_gmail_initial_window_entries_needing_body_fetch",
            return_value=entries,
        ), patch(
            "app.services.gmail_importer.enqueue_job",
        ) as enqueue:
            _ensure_initial_window_body_jobs(
                self.settings,
                user_id="user-1",
                generation_id="generation-1",
            )

        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(enqueue.call_args_list[0].kwargs["priority"], 90)
        self.assertEqual(enqueue.call_args_list[1].kwargs["priority"], 60)
        self.assertEqual(
            enqueue.call_args_list[0].kwargs["dedupe_key"],
            "gmail-body-fetch-thread:user-1:thread-priority",
        )
        self.assertFalse(enqueue.call_args_list[0].kwargs["wake_existing"])
        source = inspect.getsource(_run_progressive_gmail_bootstrap)
        self.assertLess(
            source.index("_ensure_initial_window_body_jobs"),
            source.index("pending = list_pending_gmail_initial_window_entries"),
        )

    def test_legacy_import_state_emits_no_progressive_contract(self) -> None:
        legacy = SimpleNamespace(
            sync_generation=None,
            phase=None,
            initial_target_count=0,
            initial_window_complete=False,
            full_backfill_completed_at="2026-07-25T00:00:00+00:00",
        )

        projected = _sync_progress_kwargs(legacy)

        self.assertTrue(all(value is None for value in projected.values()))

    def test_thread_metadata_batches_at_25_and_falls_back_only_for_omissions(self) -> None:
        thread_ids = [f"thread-{index:02d}" for index in range(30)]
        service = _ThreadBatchService(omitted_ids={"thread-03", "thread-27"})

        payloads = _batch_get_thread_metadata_payloads(service, thread_ids)

        self.assertEqual(service.batch_sizes, [25, 5])
        self.assertEqual(service.direct_ids, ["thread-03", "thread-27"])
        self.assertEqual(set(payloads), set(thread_ids))
        self.assertTrue(
            all(call["metadataHeaders"] for call in service.get_calls)
        )

    def test_thread_metadata_batch_propagates_quota_without_direct_amplification(self) -> None:
        service = _ThreadBatchService(error_statuses={"thread-02": 429})

        with self.assertRaises(HttpError) as raised:
            _batch_get_thread_metadata_payloads(
                service,
                ["thread-01", "thread-02", "thread-03"],
            )

        self.assertEqual(raised.exception.resp.status, 429)
        self.assertEqual(service.direct_ids, [])

    def test_thread_deleted_between_listing_and_get_is_terminally_resolved(self) -> None:
        service = _ThreadBatchService(error_statuses={"thread-deleted": 404})
        terminal: set[str] = set()

        payloads = _batch_get_thread_metadata_payloads(
            service,
            ["thread-live", "thread-deleted"],
            terminal_missing_thread_ids=terminal,
        )

        self.assertEqual(set(payloads), {"thread-live"})
        self.assertEqual(terminal, {"thread-deleted"})
        self.assertEqual(service.direct_ids, [])

    def test_terminal_initial_thread_resolves_without_decreasing_generation_counts(self) -> None:
        connection = _InitialCommitConnection()
        with patch(
            "app.db.mail_groups.get_engine",
            return_value=object(),
        ), patch(
            "app.db.mail_groups.user_mail_write_transaction",
            return_value=nullcontext(connection),
        ), patch(
            "app.db.mail_groups._upsert_gmail_messages_on_connection",
        ):
            result = commit_gmail_initial_window_metadata_batch(
                "postgresql://example/db",
                user_id="user-1",
                generation_id="generation-1",
                gmail_thread_ids=[],
                terminal_missing_thread_ids=["thread-deleted"],
                messages=[],
            )

        self.assertIsNone(result)
        sql = "\n".join(statement for statement, _params in connection.calls)
        self.assertIn("message_count = 0", sql)
        self.assertIn("metadata_ready_at = COALESCE(metadata_ready_at, now())", sql)
        self.assertIn("body_ready_at = COALESCE(body_ready_at, now())", sql)
        self.assertNotIn("DELETE FROM gmail_initial_window_entries", sql)
        self.assertNotIn("ROW_NUMBER() OVER", sql)
        self.assertIn("GREATEST(initial_target_count, :target_count)", sql)
        self.assertIn("GREATEST(initial_metadata_count, :metadata_count)", sql)
        self.assertIn("counts.message_count > 0", inspect.getsource(commit_gmail_initial_window_metadata_batch))
        state_update = next(
            params
            for statement, params in connection.calls
            if "UPDATE gmail_import_state" in statement and "initial_target_count" in statement
        )
        self.assertEqual(state_update["target_count"], 0)

    def test_lower_priority_75_cannot_unlock_newest_25_readiness(self) -> None:
        connection = _PriorityProgressConnection()

        _update_gmail_body_progress_on_connection(connection, user_id="user-1")

        self.assertIsNotNone(connection.update_params)
        self.assertEqual(connection.update_params["initial_ready"], 0)

    def test_stale_pending_body_rows_are_recovered_after_worker_death(self) -> None:
        selector_source = inspect.getsource(list_messages_needing_body_fetch)
        state_source = inspect.getsource(mark_gmail_messages_body_fetch_state)

        self.assertIn("body_fetch_status = 'pending'", selector_source)
        self.assertIn("interval '15 minutes'", selector_source)
        self.assertIn("updated_at = now()", state_source)

    def test_body_time_404_resolves_initial_entry_without_counter_regression(self) -> None:
        connection = _TerminalBodyDeleteConnection()
        with patch(
            "app.db.mail_groups.get_engine",
            return_value=object(),
        ), patch(
            "app.db.mail_groups.user_mail_write_transaction",
            return_value=nullcontext(connection),
        ), patch(
            "app.db.mail_groups._update_gmail_body_progress_on_connection",
        ) as refresh_progress:
            affected = delete_gmail_messages(
                "postgresql://example/db",
                user_id="user-1",
                message_ids=["message-1"],
            )

        self.assertEqual(affected, [])
        sql = "\n".join(connection.sql)
        self.assertIn("message_count = 0", sql)
        self.assertIn("NOT EXISTS", sql)
        self.assertNotIn("DELETE FROM gmail_initial_window_entries", sql)
        self.assertNotIn("ROW_NUMBER() OVER", sql)
        self.assertIn("GREATEST(state.initial_target_count, counts.target_count)", sql)
        self.assertIn("GREATEST(state.initial_body_ready_count, counts.body_ready_count)", sql)
        refresh_progress.assert_called_once_with(connection, user_id="user-1")

    def test_priority_25_readiness_starts_history_body_drain_for_100_window(self) -> None:
        hydrated = _message(1, fetched=True)
        progress = GmailSyncProgress(
            sync_generation="generation-1",
            phase="usable",
            initial_target_count=100,
            initial_metadata_count=100,
            initial_body_target_count=25,
            initial_body_ready_count=25,
            initial_window_complete=True,
            history_metadata_complete=False,
            history_body_complete=False,
        )
        update_result = GmailBodyUpdateResult([hydrated.message_id], progress)
        with patch(
            "app.services.gmail_importer.update_gmail_message_bodies",
            return_value=update_result,
        ), patch(
            "app.services.gmail_importer.rebuild_touched_mail_groups",
        ), patch(
            "app.services.gmail_importer.list_messages_for_gmail_thread",
            return_value=[hydrated],
        ), patch(
            "app.services.gmail_importer.list_gmail_initial_window_positions",
            return_value={hydrated.gmail_thread_id: 7},
        ), patch(
            "app.services.gmail_importer.emit_mailbox_event",
        ) as emit_event, patch(
            "app.services.gmail_importer._emit_sync_progress",
        ), patch(
            "app.services.gmail_importer.enqueue_gmail_body_backfill",
        ) as enqueue:
            count = _persist_hydrated_body_messages(
                self.settings,
                user_id="user-1",
                messages=[hydrated],
            )

        self.assertEqual(count, 1)
        enqueue.assert_called_once_with(self.settings, user_id="user-1")
        hydrated_event = emit_event.call_args.kwargs["payload"]
        self.assertEqual(hydrated_event["threads"][0]["initial_window_position"], 7)

    def test_body_backfill_processes_at_most_25_and_queues_progress_successor(self) -> None:
        missing = [_message(index) for index in range(30)]
        hydrated = [_message(index, fetched=True) for index in range(25)]
        progress = GmailSyncProgress(
            sync_generation="generation-1",
            phase="syncing_history",
            initial_target_count=100,
            initial_body_target_count=25,
            initial_body_ready_count=25,
            history_body_ready_count=25,
            initial_window_complete=True,
            history_metadata_complete=True,
            history_body_complete=False,
        )
        with patch(
            "app.services.gmail_importer.user_can_write_gmail",
            return_value=True,
        ), patch(
            "app.services.gmail_importer.list_messages_needing_body_fetch",
            side_effect=[missing[:25], [missing[25]]],
        ) as select_missing, patch(
            "app.services.gmail_importer.mark_gmail_messages_body_fetch_state",
        ) as mark_state, patch(
            "app.services.gmail_importer._download_gmail_body_messages",
            return_value=(hydrated, [], [], None),
        ) as download, patch(
            "app.services.gmail_importer._persist_hydrated_body_messages",
            return_value=25,
        ), patch(
            "app.services.gmail_importer.mark_gmail_history_body_complete_if_ready",
            return_value=progress,
        ), patch(
            "app.services.gmail_importer._emit_sync_progress",
        ), patch(
            "app.services.gmail_importer.enqueue_gmail_body_backfill",
        ) as enqueue:
            count = run_gmail_body_backfill(
                self.settings,
                user_id="user-1",
                batch_size=1000,
            )

        self.assertEqual(count, 25)
        self.assertEqual(
            [call.kwargs["limit"] for call in select_missing.call_args_list],
            [25, 1],
        )
        self.assertEqual(download.call_args.kwargs["message_ids"], [item.message_id for item in missing[:25]])
        self.assertEqual(mark_state.call_args.kwargs["status"], "pending")
        self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(enqueue.call_args.kwargs["user_id"], "user-1")
        self.assertEqual(len(enqueue.call_args.kwargs["continuation_key"]), 24)

    def test_body_backfill_successor_uses_scoped_dedupe_and_yields(self) -> None:
        with patch("app.services.gmail_importer.enqueue_job") as enqueue:
            enqueue.return_value = SimpleNamespace(id="job-1")
            job_id = enqueue_gmail_body_backfill(
                self.settings,
                user_id="user-1",
                continuation_key="progress-123",
            )

        self.assertEqual(job_id, "job-1")
        self.assertEqual(
            enqueue.call_args.kwargs["dedupe_key"],
            "gmail-body-backfill:user-1:resume:progress-123",
        )
        self.assertEqual(enqueue.call_args.kwargs["run_after_seconds"], 1)
        self.assertFalse(enqueue.call_args.kwargs["wake_existing"])

    def test_body_download_rechecks_after_lock_and_skips_prior_waiter_work(self) -> None:
        fetched = _message(1, fetched=True)
        missing = _message(2)
        parsed = asdict(_message(2, fetched=True))
        parsed.pop("created_at")
        parsed.pop("updated_at")
        with patch(
            "app.services.gmail_importer._gmail_body_fetch_account_scope",
            return_value=nullcontext(),
        ), patch(
            "app.services.gmail_importer.list_messages_by_ids",
            return_value=[fetched, missing],
        ), patch(
            "app.services.gmail_importer.create_authorized_credentials",
            return_value=object(),
        ), patch(
            "app.services.gmail_importer.build_google_service",
            return_value=object(),
        ), patch(
            "app.services.gmail_importer._batch_get_message_payloads",
            return_value={missing.message_id: {"id": missing.message_id}},
        ) as batch_get, patch(
            "app.services.gmail_importer.parse_gmail_message",
            return_value=parsed,
        ):
            hydrated, unresolved, terminal, _error = _download_gmail_body_messages(
                self.settings,
                user_id="user-1",
                message_ids=[fetched.message_id, missing.message_id],
            )

        self.assertEqual([message.message_id for message in hydrated], [missing.message_id])
        self.assertEqual(unresolved, [])
        self.assertEqual(terminal, [])
        self.assertEqual(batch_get.call_args.args[1], [missing.message_id])

    def test_body_download_retries_one_transient_parser_exception_before_terminalizing(self) -> None:
        missing = _message(1)
        parsed = asdict(_message(1, fetched=True))
        parsed.pop("created_at")
        parsed.pop("updated_at")
        with patch(
            "app.services.gmail_importer._gmail_body_fetch_account_scope",
            return_value=nullcontext(),
        ), patch(
            "app.services.gmail_importer.list_messages_by_ids",
            return_value=[missing],
        ), patch(
            "app.services.gmail_importer.create_authorized_credentials",
            return_value=object(),
        ), patch(
            "app.services.gmail_importer.build_google_service",
            return_value=object(),
        ), patch(
            "app.services.gmail_importer._batch_get_message_payloads",
            return_value={missing.message_id: {"id": missing.message_id}},
        ), patch(
            "app.services.gmail_importer.parse_gmail_message",
            side_effect=[RuntimeError("temporary decoder failure"), parsed],
        ) as parse:
            hydrated, unresolved, terminal, error = _download_gmail_body_messages(
                self.settings,
                user_id="user-1",
                message_ids=[missing.message_id],
            )

        self.assertEqual([message.message_id for message in hydrated], [missing.message_id])
        self.assertEqual(unresolved, [])
        self.assertEqual(terminal, [])
        self.assertIsNone(error)
        self.assertEqual(parse.call_count, 2)

    def test_inline_attachment_transport_failure_is_retryable_and_not_cached_as_missing(self) -> None:
        service = MagicMock()
        execute = service.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute
        execute.side_effect = TimeoutError("temporary Gmail timeout")
        resolve = _inline_attachment_resolver(service)

        for _ in range(2):
            with self.assertRaises(GmailInlineAttachmentRetryable):
                resolve("message-1", "inline-1")

        self.assertEqual(execute.call_count, 2)

    def test_inline_attachment_404_is_terminally_missing(self) -> None:
        service = MagicMock()
        execute = service.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute
        execute.side_effect = HttpError(
            SimpleNamespace(status=404, reason="Not found"),
            b"{}",
        )
        resolve = _inline_attachment_resolver(service)

        self.assertIsNone(resolve("message-1", "inline-1"))
        self.assertIsNone(resolve("message-1", "inline-1"))
        self.assertEqual(execute.call_count, 1)

    def test_body_backfill_deletes_404_without_leaving_failed_row(self) -> None:
        missing = _message(1)

        def download_404(*_args, **kwargs):
            kwargs["terminal_not_found_ids"].add(missing.message_id)
            return [], [], [], None

        progress = GmailSyncProgress(
            sync_generation="generation-1",
            initial_body_target_count=1,
            initial_body_ready_count=0,
            history_metadata_complete=True,
            history_body_complete=True,
        )
        with patch(
            "app.services.gmail_importer.user_can_write_gmail",
            return_value=True,
        ), patch(
            "app.services.gmail_importer.list_messages_needing_body_fetch",
            side_effect=[[missing], []],
        ), patch(
            "app.services.gmail_importer.mark_gmail_messages_body_fetch_state",
        ) as mark_state, patch(
            "app.services.gmail_importer._download_gmail_body_messages",
            side_effect=download_404,
        ), patch(
            "app.services.gmail_importer._delete_terminal_gmail_messages",
            return_value=1,
        ) as delete_terminal, patch(
            "app.services.gmail_importer.mark_gmail_history_body_complete_if_ready",
            return_value=progress,
        ), patch(
            "app.services.gmail_importer._emit_sync_progress",
        ):
            count = run_gmail_body_backfill(self.settings, user_id="user-1")

        self.assertEqual(count, 0)
        delete_terminal.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_ids=[missing.message_id],
            source="gmail_body_backfill",
        )
        self.assertEqual(mark_state.call_count, 1)
        self.assertEqual(mark_state.call_args.kwargs["status"], "pending")

    def test_body_backfill_malformed_body_becomes_terminal_and_continues(self) -> None:
        missing = _message(1)
        older = _message(2)
        malformed = ValueError("malformed MIME body")
        progress = GmailSyncProgress(
            sync_generation="generation-1",
            phase="syncing_history",
            history_metadata_complete=True,
            history_body_complete=False,
        )
        with patch(
            "app.services.gmail_importer.user_can_write_gmail",
            return_value=True,
        ), patch(
            "app.services.gmail_importer.list_messages_needing_body_fetch",
            side_effect=[[missing], [older]],
        ), patch(
            "app.services.gmail_importer.mark_gmail_messages_body_fetch_state",
        ) as mark_state, patch(
            "app.services.gmail_importer._download_gmail_body_messages",
            return_value=([], [], [missing.message_id], malformed),
        ), patch(
            "app.services.gmail_importer._emit_terminal_body_states",
        ) as emit_terminal, patch(
            "app.services.gmail_importer.mark_gmail_history_body_complete_if_ready",
            return_value=progress,
        ), patch(
            "app.services.gmail_importer._emit_sync_progress",
        ), patch(
            "app.services.gmail_importer.enqueue_gmail_body_backfill",
        ) as enqueue:
            count = run_gmail_body_backfill(self.settings, user_id="user-1")

        self.assertEqual(count, 0)
        self.assertEqual(mark_state.call_count, 2)
        self.assertEqual(mark_state.call_args.kwargs["status"], "unavailable")
        emit_terminal.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_ids=[missing.message_id],
            source="gmail_body_backfill",
        )
        enqueue.assert_called_once()

    def test_body_download_scope_uses_account_specific_advisory_lock(self) -> None:
        entered: list[bool] = []

        @contextmanager
        def scope():
            entered.append(True)
            yield

        with patch(
            "app.services.gmail_importer.gmail_body_fetch_lock_key",
            return_value=42,
        ), patch(
            "app.services.gmail_importer.advisory_session_lock",
            return_value=scope(),
        ) as lock:
            with _gmail_body_fetch_account_scope(self.settings, user_id="user-1"):
                pass

        self.assertEqual(entered, [True])
        lock.assert_called_once_with("postgresql://example/db", lock_key=42)

    def test_new_generation_resets_progress_timestamp(self) -> None:
        source = inspect.getsource(start_gmail_sync_progress)
        initializer_source = inspect.getsource(initialize_gmail_initial_window)

        self.assertIn("WHEN sync_generation = :generation_id", source)
        self.assertIn("THEN COALESCE(last_progress_at, now())", source)
        self.assertIn("ELSE now()", source)
        self.assertIn("phase NOT IN ('discovering_recent', 'failed')", initializer_source)

    def test_inconclusive_empty_discovery_does_not_publish_empty_readiness(self) -> None:
        state = SimpleNamespace(
            reconcile_generation="generation-1",
            initial_target_count=0,
            initial_window_complete=False,
        )
        with patch(
            "app.services.gmail_importer._ensure_gmail_reconciliation_started",
            return_value=state,
        ), patch(
            "app.services.gmail_importer.start_gmail_sync_progress",
            return_value=state,
        ), patch(
            "app.services.gmail_importer._list_initial_window_threads",
            return_value={
                "threads": [],
                "resultSizeEstimate": 12,
                "nextPageToken": "next-page",
            },
        ), patch(
            "app.services.gmail_importer.initialize_gmail_initial_window",
        ) as initialize:
            with self.assertRaisesRegex(RuntimeError, "inconclusive"):
                _run_progressive_gmail_bootstrap(self.settings, user_id="user-1")

        initialize.assert_not_called()

    def test_history_thread_page_emits_mailbox_invalidation_only_after_commit(self) -> None:
        cursor = _encode_full_thread_mailbox_cursor()
        state = SimpleNamespace(
            reconcile_generation="generation-1",
            reconcile_baseline_history_id="100",
            reconcile_cursor=cursor,
        )
        progress = GmailSyncProgress(
            sync_generation="generation-1",
            phase="syncing_history",
            history_metadata_count=1,
        )
        message = _message(1)
        with patch(
            "app.services.gmail_importer.user_can_write_gmail",
            return_value=True,
        ), patch(
            "app.services.gmail_importer.get_import_state",
            return_value=state,
        ), patch(
            "app.services.gmail_importer._hydrate_reconciliation_thread_cursor_page",
            return_value=([message], None, 1, 100),
        ), patch(
            "app.services.gmail_importer.commit_gmail_reconciliation_metadata_page",
            return_value=progress,
        ), patch(
            "app.services.gmail_importer._emit_sync_progress",
        ), patch(
            "app.services.gmail_importer.emit_mailbox_event",
        ) as emit, patch(
            "app.services.gmail_importer._finalize_gmail_full_reconciliation",
            return_value=0,
        ):
            touched = run_gmail_full_reconciliation(
                self.settings,
                user_id="user-1",
                batch_size=100,
                max_pages=1,
            )

        self.assertEqual(touched, 1)
        emit.assert_called_once_with(
            self.settings,
            user_id="user-1",
            event_type="mailbox-changed",
            mailbox_label="all",
            payload={
                "source": "history_metadata",
                "message_count": 1,
                "thread_count": 1,
                "history_metadata_count": 1,
                "sync_generation": "generation-1",
            },
        )

    def test_duplicate_history_thread_page_does_not_emit_mailbox_invalidation(self) -> None:
        cursor = _encode_full_thread_mailbox_cursor()
        state = SimpleNamespace(
            reconcile_generation="generation-1",
            reconcile_baseline_history_id="100",
            reconcile_cursor=cursor,
        )
        with patch(
            "app.services.gmail_importer.user_can_write_gmail",
            return_value=True,
        ), patch(
            "app.services.gmail_importer.get_import_state",
            return_value=state,
        ), patch(
            "app.services.gmail_importer._hydrate_reconciliation_thread_cursor_page",
            return_value=([_message(1)], None, 1, 100),
        ), patch(
            "app.services.gmail_importer.commit_gmail_reconciliation_metadata_page",
            return_value=None,
        ), patch(
            "app.services.gmail_importer.emit_mailbox_event",
        ) as emit:
            touched = run_gmail_full_reconciliation(
                self.settings,
                user_id="user-1",
                batch_size=100,
                max_pages=1,
            )

        self.assertEqual(touched, 0)
        emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
