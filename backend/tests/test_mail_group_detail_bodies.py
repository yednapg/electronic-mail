from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, replace
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, call, patch

from googleapiclient.errors import HttpError

from app.db.mail_groups import GmailMessageRecord, GmailThreadMessagePage, MailGroupDetail, MailGroupRecord
from app.services.gmail_importer import _batch_get_message_payloads, _mark_full_payload_body_fetch_status, run_gmail_body_fetch
from app.services.mail_groups import (
    _enqueue_body_fetch_for_gmail_thread,
    _gmail_thread_content_revision,
    _needs_body_fetch,
    build_group_detail_response,
)


def sample_message() -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id="msg-1",
        gmail_thread_id="thread-1",
        history_id="10",
        label_ids=["INBOX"],
        internal_date="2026-05-15T12:00:00+00:00",
        subject="California State University application started",
        sender="California State University <apply@example.com>",
        recipients={"to": "me@example.com"},
        headers={"subject": "California State University application started"},
        snippet="TestUser, Thank you for choosing the California State University.",
        raw_payload={"payload": {"headers": []}},
        html_body_sanitized=None,
        html_render_document=None,
        text_body="TestUser, Thank you for choosing the California State University.",
        extracted_signals={"sender_domain": "example.com"},
        body_hash="hash-1",
        created_at="2026-05-15T12:00:00+00:00",
        updated_at="2026-05-15T12:00:00+00:00",
    )


RICH_APPLICATION_HTML = '<html><body><table style="width:100%"><tr><td><img src="https://example.com/logo.png">Full application email</td></tr></table></body></html>'


def reader_page(messages: list[GmailMessageRecord]) -> GmailThreadMessagePage | None:
    if not messages:
        return None
    latest = max(messages, key=lambda item: item.internal_date or item.updated_at)
    return GmailThreadMessagePage(
        gmail_thread_id=messages[0].gmail_thread_id or messages[0].message_id,
        messages=messages,
        total_messages=len(messages),
        latest_subject=latest.subject,
        incomplete_body_count=sum(1 for message in messages if _needs_body_fetch(message)),
        content_revision=_gmail_thread_content_revision(messages),
    )


def sample_group() -> MailGroupRecord:
    return MailGroupRecord(
        id="group-1",
        user_id="user-1",
        group_key="gmail-thread:thread-1",
        group_type="conversation",
        status="active",
        enrichment_status="ready",
        membership_source="gmail_thread",
        ai_model=None,
        ai_error=None,
        ai_generated_at=None,
        ai_title="California State University application started",
        ai_summary="Cal State Apply started the application.",
        labels=["application"],
        action_needed=False,
        action_type="open",
        priority=20,
        timing_band="later",
        dashboard_visible=False,
        latest_message_at="2026-05-15T12:00:00+00:00",
        latest_message_id="msg-1",
        generated_from_hash="hash-1",
        generated_at="2026-05-15T12:00:00+00:00",
        created_at="2026-05-15T12:00:00+00:00",
        updated_at="2026-05-15T12:00:00+00:00",
    )


class _PartialRequest:
    def __init__(self, message_id: str, service: "_PartialBatchService") -> None:
        self.message_id = message_id
        self.service = service

    def execute(self):
        self.service.direct_execute_ids.append(self.message_id)
        if self.message_id != "msg-good":
            raise HttpError(
                SimpleNamespace(status=self.service.failing_status, reason="Provider error"),
                b"{}",
            )
        return {"id": self.message_id}


class _PartialBatch:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.requests: list[tuple[str, _PartialRequest]] = []

    def add(self, request: _PartialRequest, *, request_id: str) -> None:
        self.requests.append((request_id, request))

    def execute(self) -> None:
        for request_id, _request in self.requests:
            if request_id == "msg-good":
                self.callback(request_id, {"id": request_id}, None)
            else:
                error = HttpError(
                    SimpleNamespace(status=_request.service.failing_status, reason="Provider error"),
                    b"{}",
                )
                self.callback(request_id, None, error)


class _PartialMessages:
    def __init__(self, service: "_PartialBatchService") -> None:
        self.service = service

    def get(self, **kwargs) -> _PartialRequest:
        return _PartialRequest(str(kwargs["id"]), self.service)


class _PartialUsers:
    def __init__(self, service: "_PartialBatchService") -> None:
        self.service = service

    def messages(self) -> _PartialMessages:
        return _PartialMessages(self.service)


class _PartialBatchService:
    def __init__(self, *, failing_status: int = 404) -> None:
        self.failing_status = failing_status
        self.direct_execute_ids: list[str] = []

    def new_batch_http_request(self, *, callback) -> _PartialBatch:
        return _PartialBatch(callback)

    def users(self) -> _PartialUsers:
        return _PartialUsers(self)


class MailGroupDetailBodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body_lock_patch = patch(
            "app.services.gmail_importer._gmail_body_fetch_account_scope",
            return_value=nullcontext(),
        )
        self.body_lock_patch.start()
        self.addCleanup(self.body_lock_patch.stop)
        self.body_recheck_patch = patch(
            "app.services.gmail_importer.list_messages_by_ids",
            side_effect=lambda _database_url, *, user_id, message_ids: [
                replace(
                    sample_message(),
                    user_id=user_id,
                    message_id=message_id,
                    body_fetch_status="missing",
                )
                for message_id in message_ids
            ],
        )
        self.body_recheck_patch.start()
        self.addCleanup(self.body_recheck_patch.stop)

    def test_batch_body_fetch_preserves_success_when_another_message_is_missing(self) -> None:
        service = _PartialBatchService()
        terminal_not_found: set[str] = set()

        payloads = _batch_get_message_payloads(
            service,
            ["msg-good", "msg-missing"],
            format="full",
            continue_on_error=True,
            terminal_not_found_ids=terminal_not_found,
        )

        self.assertEqual(payloads, {"msg-good": {"id": "msg-good"}})
        self.assertEqual(terminal_not_found, {"msg-missing"})
        self.assertEqual(service.direct_execute_ids, [])

    def test_bulk_body_warmup_does_not_wake_delayed_retry(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        with patch("app.services.mail_groups.enqueue_job") as enqueue:
            enqueue.return_value = SimpleNamespace(id="job-1")
            _enqueue_body_fetch_for_gmail_thread(
                settings,
                user_id="user-1",
                gmail_thread_id="thread-1",
                priority=70,
            )

        self.assertFalse(enqueue.call_args.kwargs["wake_existing"])

    def test_batch_body_fetch_propagates_transient_errors_without_request_amplification(self) -> None:
        for status in (429, 503):
            with self.subTest(status=status):
                service = _PartialBatchService(failing_status=status)

                with self.assertRaises(HttpError) as raised:
                    _batch_get_message_payloads(
                        service,
                        ["msg-good", "msg-throttled", "msg-later"],
                        format="full",
                        continue_on_error=True,
                    )

                self.assertEqual(raised.exception.resp.status, status)
                self.assertEqual(service.direct_execute_ids, ["msg-throttled"])

    def test_full_attachment_only_payload_is_a_terminal_body_fetch(self) -> None:
        parsed = {
            "raw_payload": {
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "application/pdf",
                            "filename": "statement.pdf",
                            "body": {"attachmentId": "attachment-1", "size": 1024},
                        }
                    ],
                }
            }
        }

        self.assertEqual(
            _mark_full_payload_body_fetch_status(parsed)["body_fetch_status"],
            "fetched",
        )

    def test_synthetic_group_detail_fails_closed_in_no_ai_release(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        group = replace(sample_group(), id="group-ai", group_key="ai:psu", membership_source="ai_batch", ai_title="State University application updates")
        sent = replace(
            sample_message(),
            message_id="sent-1",
            gmail_thread_id="thread-psu",
            label_ids=["SENT"],
            internal_date="2026-05-15T12:00:00+00:00",
            sender="TestUser <hi@example.com>",
            subject="Question About Reconsideration Request",
            text_body="Can I request reconsideration?",
        )
        reply = replace(
            sample_message(),
            message_id="reply-1",
            gmail_thread_id="thread-psu",
            label_ids=["INBOX"],
            internal_date="2026-05-15T13:00:00+00:00",
            sender="State University <university-admissions@example.edu>",
            subject="RE: Question About Reconsideration Request",
            text_body="Yes, you may request reconsideration.",
        )

        with patch(
            "app.services.mail_groups.get_mail_group_detail",
            return_value=MailGroupDetail(group=group, messages=[reply]),
        ) as get_detail, patch(
            "app.services.mail_groups.get_gmail_thread_message_page",
            return_value=None,
        ), patch("app.services.mail_groups.enqueue_job") as enqueue_job:
            response = build_group_detail_response(settings, user_id="user-1", group_id="group-ai")

        self.assertIsNone(response)
        get_detail.assert_not_called()
        enqueue_job.assert_not_called()

    def test_reader_queues_full_body_without_calling_gmail_on_request(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        metadata_only = sample_message()

        with patch(
            "app.services.mail_groups.get_gmail_thread_message_page",
            return_value=reader_page([metadata_only]),
        ), patch("app.services.mail_groups.enqueue_job") as enqueue_job, patch("app.services.gmail_importer.run_gmail_body_fetch") as body_fetch:
            enqueue_job.return_value = SimpleNamespace(id="job-1")
            response = build_group_detail_response(settings, user_id="user-1", group_id="thread-1")

        self.assertIsNotNone(response)
        body_fetch.assert_not_called()
        enqueue_job.assert_called_once_with(
            "postgresql://example/db",
            kind="gmail_body_fetch",
            queue="reader",
            user_id="user-1",
            dedupe_key="gmail-body-fetch-thread:user-1:thread-1",
            priority=100,
            payload={"user_id": "user-1", "gmail_thread_id": "thread-1"},
            wake_existing=True,
        )
        self.assertEqual(response.messages[0].body, metadata_only.snippet)
        self.assertFalse(response.messages[0].body_complete)
        self.assertIsNone(response.messages[0].html_body)
        self.assertIsNone(response.messages[0].html_render_document)
        self.assertIsNotNone(response.messages[0].reader)
        self.assertFalse(response.messages[0].reader.original_html_available)

    def test_reader_returns_cached_content_when_body_queue_is_temporarily_unavailable(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        metadata_only = sample_message()

        with patch(
            "app.services.mail_groups.get_gmail_thread_message_page",
            return_value=reader_page([metadata_only]),
        ), patch("app.services.mail_groups.enqueue_job") as enqueue_job, patch("app.services.gmail_importer.run_gmail_body_fetch") as body_fetch:
            enqueue_job.side_effect = RuntimeError("queue unavailable")
            response = build_group_detail_response(settings, user_id="user-1", group_id="thread-1")

        self.assertIsNotNone(response)
        body_fetch.assert_not_called()
        enqueue_job.assert_called_once_with(
            "postgresql://example/db",
            kind="gmail_body_fetch",
            queue="reader",
            user_id="user-1",
            dedupe_key="gmail-body-fetch-thread:user-1:thread-1",
            priority=100,
            payload={"user_id": "user-1", "gmail_thread_id": "thread-1"},
            wake_existing=True,
        )
        self.assertEqual(response.messages[0].body, "TestUser, Thank you for choosing the California State University.")
        self.assertFalse(response.messages[0].body_complete)
        self.assertIsNone(response.messages[0].html_body)
        self.assertIsNone(response.messages[0].html_render_document)
        self.assertIsNotNone(response.messages[0].reader)
        self.assertFalse(response.messages[0].reader.original_html_available)

    def test_reader_returns_cached_html_without_enqueuing_body_fetch(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        hydrated = replace(sample_message(), html_body_sanitized=RICH_APPLICATION_HTML, html_render_document=RICH_APPLICATION_HTML, text_body="Full application email")

        with patch(
            "app.services.mail_groups.get_gmail_thread_message_page",
            return_value=reader_page([hydrated]),
        ), patch("app.services.mail_groups.enqueue_job") as enqueue_job, patch("app.services.gmail_importer.run_gmail_body_fetch") as body_fetch:
            response = build_group_detail_response(settings, user_id="user-1", group_id="thread-1")

        self.assertIsNotNone(response)
        body_fetch.assert_not_called()
        enqueue_job.assert_not_called()
        self.assertEqual(response.messages[0].body, "Full application email")
        self.assertEqual(response.messages[0].html_body, RICH_APPLICATION_HTML)
        self.assertEqual(response.messages[0].html_render_document, RICH_APPLICATION_HTML)
        self.assertTrue(response.messages[0].body_complete)
        self.assertIsNotNone(response.messages[0].reader)
        self.assertEqual(response.messages[0].reader.primary_text, "Full application email")
        self.assertTrue(response.messages[0].reader.original_html_available)

    def test_reader_treats_fetched_attachment_only_message_as_complete(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        attachment_only = replace(
            sample_message(),
            text_body=None,
            body_fetch_status="fetched",
            raw_payload={
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "partId": "1",
                            "mimeType": "application/pdf",
                            "filename": "statement.pdf",
                            "body": {"attachmentId": "attachment-1", "size": 1024},
                        }
                    ],
                }
            },
        )

        with patch(
            "app.services.mail_groups.get_gmail_thread_message_page",
            return_value=reader_page([attachment_only]),
        ), patch("app.services.mail_groups.enqueue_job") as enqueue_job:
            response = build_group_detail_response(settings, user_id="user-1", group_id="thread-1")

        self.assertIsNotNone(response)
        enqueue_job.assert_not_called()
        self.assertTrue(response.messages[0].body_complete)
        self.assertEqual(response.messages[0].attachments[0].filename, "statement.pdf")

    def test_reader_treats_terminal_unavailable_body_as_complete_snippet(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        unavailable = replace(
            sample_message(),
            text_body=None,
            html_body_sanitized=None,
            html_render_document=None,
            raw_payload={},
            body_fetch_status="unavailable",
            body_fetch_error="Malformed provider MIME payload",
        )

        with patch(
            "app.services.mail_groups.get_gmail_thread_message_page",
            return_value=reader_page([unavailable]),
        ), patch("app.services.mail_groups.enqueue_job") as enqueue_job:
            response = build_group_detail_response(
                settings,
                user_id="user-1",
                group_id="thread-1",
            )

        self.assertIsNotNone(response)
        enqueue_job.assert_not_called()
        self.assertTrue(response.messages[0].body_complete)
        self.assertEqual(response.messages[0].body, unavailable.snippet)

    def test_reader_pages_keep_complete_identity_count_and_revision_stable(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        messages = [
            replace(
                sample_message(),
                message_id=f"msg-{index}",
                internal_date=f"2026-05-15T1{index}:00:00+00:00",
                subject="Newest subject" if index == 2 else f"Subject {index}",
                text_body=f"Complete body {index}",
                body_fetch_status="fetched",
                content_revision=index + 1,
            )
            for index in range(3)
        ]
        revision = _gmail_thread_content_revision(messages)
        first_page = GmailThreadMessagePage(
            gmail_thread_id="thread-1",
            messages=messages[:2],
            total_messages=3,
            latest_subject="Newest subject",
            incomplete_body_count=0,
            content_revision=revision,
        )
        second_page = GmailThreadMessagePage(
            gmail_thread_id="thread-1",
            messages=messages[2:],
            total_messages=3,
            latest_subject="Newest subject",
            incomplete_body_count=0,
            content_revision=revision,
        )

        with patch(
            "app.services.mail_groups.get_gmail_thread_message_page",
            side_effect=[first_page, second_page],
        ) as get_page, patch("app.services.mail_groups.enqueue_job") as enqueue_job:
            first = build_group_detail_response(
                settings,
                user_id="user-1",
                group_id="thread-1",
                limit=2,
                offset=0,
            )
            second = build_group_detail_response(
                settings,
                user_id="user-1",
                group_id="thread-1",
                limit=2,
                offset=2,
            )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.entity_id, second.entity_id)
        self.assertEqual(first.gmail_thread_id, second.gmail_thread_id)
        self.assertEqual(first.title, "Newest subject")
        self.assertEqual(second.title, "Newest subject")
        self.assertEqual(first.total_messages, 3)
        self.assertEqual(second.total_messages, 3)
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        self.assertEqual(first.content_revision, revision)
        self.assertEqual(second.content_revision, revision)
        self.assertEqual([message.id for message in first.messages], ["msg-0", "msg-1"])
        self.assertEqual([message.id for message in second.messages], ["msg-2"])
        self.assertEqual(get_page.call_args_list[0].kwargs["limit"], 2)
        self.assertEqual(get_page.call_args_list[0].kwargs["offset"], 0)
        self.assertEqual(get_page.call_args_list[1].kwargs["limit"], 2)
        self.assertEqual(get_page.call_args_list[1].kwargs["offset"], 2)
        enqueue_job.assert_not_called()

    def test_body_worker_emits_targeted_hydration_event_after_persisting_full_content(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        metadata_only = sample_message()
        hydrated = replace(
            metadata_only,
            raw_payload={
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": "RnVsbCBhcHBsaWNhdGlvbiBlbWFpbA=="},
                }
            },
            text_body="Full application email",
            body_fetch_status="missing",
        )
        parsed = asdict(hydrated)
        parsed.pop("created_at")
        parsed.pop("updated_at")

        with patch("app.services.gmail_importer.user_can_write_gmail", return_value=True), patch(
            "app.services.gmail_importer.list_messages_for_gmail_thread",
            return_value=[metadata_only],
        ), patch("app.services.gmail_importer.mark_gmail_messages_body_fetch_state"), patch(
            "app.services.gmail_importer.create_authorized_credentials",
            return_value=object(),
        ), patch("app.services.gmail_importer.build_google_service", return_value=object()), patch(
            "app.services.gmail_importer._batch_get_message_payloads",
            return_value={"msg-1": {"id": "msg-1"}},
        ), patch("app.services.gmail_importer.parse_gmail_message", return_value=parsed), patch(
            "app.services.gmail_importer.update_gmail_message_bodies",
            return_value=["msg-1"],
        ) as upsert, patch("app.services.gmail_importer.rebuild_touched_mail_groups"), patch(
            "app.services.gmail_importer.emit_mailbox_event"
        ) as emit:
            count = run_gmail_body_fetch(settings, user_id="user-1", gmail_thread_id="thread-1")

        self.assertEqual(count, 1)
        upsert.assert_called_once()
        persisted = upsert.call_args.args[1][0]
        self.assertEqual(persisted.text_body, "Full application email")
        self.assertEqual(persisted.body_fetch_status, "fetched")
        emit.assert_called_once_with(
            settings,
            user_id="user-1",
            event_type="thread-content-hydrated",
            payload={
                "source": "gmail_body_fetch",
                "thread_ids": ["thread-1"],
                "hydrated_message_count": 1,
            },
        )

    def test_body_worker_retries_when_large_text_attachment_cannot_be_resolved(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        metadata_only = sample_message()
        unresolved = replace(
            metadata_only,
            raw_payload={
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "large-text-1", "size": 4096},
                }
            },
            text_body=None,
            body_fetch_status="missing",
        )
        parsed = asdict(unresolved)
        parsed.pop("created_at")
        parsed.pop("updated_at")

        with patch("app.services.gmail_importer.user_can_write_gmail", return_value=True), patch(
            "app.services.gmail_importer.list_messages_for_gmail_thread",
            return_value=[metadata_only],
        ), patch("app.services.gmail_importer.mark_gmail_messages_body_fetch_state") as mark_state, patch(
            "app.services.gmail_importer.create_authorized_credentials",
            return_value=object(),
        ), patch("app.services.gmail_importer.build_google_service", return_value=object()), patch(
            "app.services.gmail_importer._batch_get_message_payloads",
            return_value={"msg-1": {"id": "msg-1"}},
        ), patch("app.services.gmail_importer.parse_gmail_message", return_value=parsed), patch(
            "app.services.gmail_importer.update_gmail_message_bodies"
        ) as upsert, patch("app.services.gmail_importer.emit_mailbox_event") as emit:
            with self.assertRaisesRegex(RuntimeError, "could not be downloaded"):
                run_gmail_body_fetch(settings, user_id="user-1", gmail_thread_id="thread-1")

        self.assertEqual(mark_state.call_count, 2)
        self.assertEqual(mark_state.call_args.kwargs["status"], "missing")
        upsert.assert_not_called()
        emit.assert_not_called()

    def test_body_worker_terminalizes_malformed_payload_without_retry_loop(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        metadata_only = sample_message()
        malformed = ValueError("malformed MIME payload")

        with patch("app.services.gmail_importer.user_can_write_gmail", return_value=True), patch(
            "app.services.gmail_importer.list_messages_for_gmail_thread",
            return_value=[metadata_only],
        ), patch(
            "app.services.gmail_importer.mark_gmail_messages_body_fetch_state"
        ) as mark_state, patch(
            "app.services.gmail_importer.create_authorized_credentials",
            return_value=object(),
        ), patch(
            "app.services.gmail_importer.build_google_service",
            return_value=object(),
        ), patch(
            "app.services.gmail_importer._batch_get_message_payloads",
            return_value={"msg-1": {"id": "msg-1"}},
        ), patch(
            "app.services.gmail_importer.parse_gmail_message",
            side_effect=malformed,
        ), patch(
            "app.services.gmail_importer._emit_terminal_body_states"
        ) as emit_terminal:
            count = run_gmail_body_fetch(
                settings,
                user_id="user-1",
                gmail_thread_id="thread-1",
            )

        self.assertEqual(count, 0)
        self.assertEqual(mark_state.call_count, 2)
        self.assertEqual(mark_state.call_args.kwargs["status"], "unavailable")
        emit_terminal.assert_called_once_with(
            settings,
            user_id="user-1",
            message_ids=["msg-1"],
            source="gmail_body_fetch",
        )

    def test_body_worker_persists_successful_subset_before_retrying_unresolved_message(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        good_metadata = replace(sample_message(), message_id="msg-good", gmail_thread_id="thread-mixed")
        bad_metadata = replace(sample_message(), message_id="msg-bad", gmail_thread_id="thread-mixed")
        good = replace(
            good_metadata,
            raw_payload={
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": "RnVsbCBnb29kIGJvZHk="},
                }
            },
            text_body="Full good body",
            body_fetch_status="missing",
        )
        bad = replace(
            bad_metadata,
            raw_payload={
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "unresolved-text", "size": 4096},
                }
            },
            text_body=None,
            body_fetch_status="missing",
        )
        good_parsed = asdict(good)
        good_parsed.pop("created_at")
        good_parsed.pop("updated_at")
        bad_parsed = asdict(bad)
        bad_parsed.pop("created_at")
        bad_parsed.pop("updated_at")

        with patch("app.services.gmail_importer.user_can_write_gmail", return_value=True), patch(
            "app.services.gmail_importer.list_messages_for_gmail_thread",
            return_value=[good_metadata, bad_metadata],
        ), patch("app.services.gmail_importer.mark_gmail_messages_body_fetch_state") as mark_state, patch(
            "app.services.gmail_importer.create_authorized_credentials",
            return_value=object(),
        ), patch("app.services.gmail_importer.build_google_service", return_value=object()), patch(
            "app.services.gmail_importer._batch_get_message_payloads",
            return_value={"msg-good": {"id": "msg-good"}, "msg-bad": {"id": "msg-bad"}},
        ), patch(
            "app.services.gmail_importer.parse_gmail_message",
            side_effect=[good_parsed, bad_parsed],
        ), patch(
            "app.services.gmail_importer.update_gmail_message_bodies",
            return_value=["msg-good"],
        ) as upsert, patch(
            "app.services.gmail_importer.rebuild_touched_mail_groups"
        ) as rebuild, patch("app.services.gmail_importer.emit_mailbox_event") as emit:
            with self.assertRaisesRegex(RuntimeError, "could not be downloaded"):
                run_gmail_body_fetch(settings, user_id="user-1", gmail_thread_id="thread-mixed")

        persisted = upsert.call_args.args[1]
        self.assertEqual([message.message_id for message in persisted], ["msg-good"])
        self.assertEqual(persisted[0].body_fetch_status, "fetched")
        rebuild.assert_called_once_with(
            settings,
            user_id="user-1",
            message_ids=["msg-good"],
            use_ai=False,
        )
        emit.assert_called_once_with(
            settings,
            user_id="user-1",
            event_type="thread-content-hydrated",
            payload={
                "source": "gmail_body_fetch",
                "thread_ids": ["thread-mixed"],
                "hydrated_message_count": 1,
            },
        )
        self.assertEqual(
            mark_state.call_args_list,
            [
                call(
                    "postgresql://example/db",
                    user_id="user-1",
                    message_ids=["msg-good", "msg-bad"],
                    status="pending",
                ),
                call(
                    "postgresql://example/db",
                    user_id="user-1",
                    message_ids=["msg-bad"],
                    status="missing",
                    error=ANY,
                ),
            ],
        )

    def test_body_worker_retry_skips_message_already_persisted_as_complete(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        complete = replace(
            sample_message(),
            message_id="msg-good",
            gmail_thread_id="thread-mixed",
            raw_payload={
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": "RnVsbCBnb29kIGJvZHk="},
                }
            },
            text_body="Full good body",
            body_fetch_status="fetched",
        )
        unresolved_metadata = replace(sample_message(), message_id="msg-bad", gmail_thread_id="thread-mixed")
        unresolved = replace(
            unresolved_metadata,
            raw_payload={
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "unresolved-text", "size": 4096},
                }
            },
            text_body=None,
        )
        unresolved_parsed = asdict(unresolved)
        unresolved_parsed.pop("created_at")
        unresolved_parsed.pop("updated_at")

        with patch("app.services.gmail_importer.user_can_write_gmail", return_value=True), patch(
            "app.services.gmail_importer.list_messages_for_gmail_thread",
            return_value=[complete, unresolved_metadata],
        ), patch("app.services.gmail_importer.mark_gmail_messages_body_fetch_state") as mark_state, patch(
            "app.services.gmail_importer.create_authorized_credentials",
            return_value=object(),
        ), patch("app.services.gmail_importer.build_google_service", return_value=object()) as service, patch(
            "app.services.gmail_importer._batch_get_message_payloads",
            return_value={"msg-bad": {"id": "msg-bad"}},
        ) as batch_get, patch(
            "app.services.gmail_importer.parse_gmail_message",
            return_value=unresolved_parsed,
        ), patch("app.services.gmail_importer.update_gmail_message_bodies") as upsert:
            with self.assertRaisesRegex(RuntimeError, "could not be downloaded"):
                run_gmail_body_fetch(settings, user_id="user-1", gmail_thread_id="thread-mixed")

        batch_get.assert_called_once_with(
            service.return_value,
            ["msg-bad"],
            format="full",
            continue_on_error=True,
            terminal_not_found_ids=set(),
        )
        self.assertEqual(mark_state.call_args_list[0].kwargs["message_ids"], ["msg-bad"])
        self.assertEqual(mark_state.call_args_list[1].kwargs["message_ids"], ["msg-bad"])
        upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
