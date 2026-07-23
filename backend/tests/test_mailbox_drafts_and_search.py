from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import replace
from email import message_from_bytes, policy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from app.core.error_safety import GoogleCredentialsUnavailable
from app.db import mail_groups as mail_group_db
from app.db.mail_groups import ClientDraftRecord
from app.main import app
from app.schemas.domain import MailDraftResponse, MailDraftSaveRequest, MailboxResponse
from app.services.integrations.google import (
    MultipleSentGmailMessagesFound,
    find_sent_gmail_message_by_rfc822_message_id,
)
from app.services.mailbox_drafts import (
    MailDraftIdentityConflict,
    _preserved_attachment_inputs,
    delete_draft_by_id,
    get_draft,
    save_draft,
    send_saved_draft,
)
from app.services.gmail_importer import (
    GMAIL_BACKFILL_JOB_PRIORITY,
    GMAIL_SEARCH_MAX_PAGES,
    GMAIL_SEARCH_PAGE_SIZE,
    hydrate_gmail_search_results,
)
from app.services.mailbox_search import (
    GMAIL_SEARCH_JOB_PRIORITY,
    GMAIL_SEARCH_MAX_CONTINUATION_JOBS,
    enqueue_mailbox_search_hydration,
    mailbox_search_key,
    run_mailbox_search_hydration,
)
from app.workers.main import _run_job
from backend.tests.test_mailbox_sends import sample_message


def draft_record(state: str = "saved") -> ClientDraftRecord:
    return ClientDraftRecord(
        user_id="user-1",
        client_draft_id="client-draft-1",
        gmail_draft_id="draft-1",
        gmail_message_id="message-1",
        gmail_thread_id="thread-1",
        content_hash="hash-1",
        state=state,
        last_client_send_id="send-1" if state == "sent" else None,
        sent_message_id="sent-message-1" if state == "sent" else None,
        error=None,
        created_at="2026-07-13T09:00:00+00:00",
        saved_at="2026-07-13T09:01:00+00:00",
        updated_at="2026-07-13T09:02:00+00:00",
    )


def google_http_error(status: int) -> HttpError:
    return HttpError(SimpleNamespace(status=status, reason="Provider rejection"), b"provider rejection")


class MailboxDraftServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            database_path="postgresql://example/db",
            backend_origin="http://127.0.0.1:3001",
        )
        self.provider_lock_patch = patch(
            "app.services.mailbox_drafts.shared_user_mail_lock",
            return_value=nullcontext(),
        )
        self.provider_lock_patch.start()
        self.addCleanup(self.provider_lock_patch.stop)

    def _save_response_draft(
        self,
        request: MailDraftSaveRequest,
        *,
        messages: list[object] | None = None,
        detail_present: bool = True,
        gmail_thread_messages: list[object] | None = None,
    ) -> tuple[MailDraftResponse, dict[str, Mock]]:
        mocks: dict[str, Mock] = {}
        with ExitStack() as stack:
            mocks["can_manage"] = stack.enter_context(
                patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True)
            )
            mocks["lock"] = stack.enter_context(patch("app.services.mailbox_drafts.client_draft_lock"))
            mocks["lock"].return_value = nullcontext()
            mocks["get_draft"] = stack.enter_context(
                patch("app.services.mailbox_drafts.get_client_draft", return_value=None)
            )
            mocks["find_draft"] = stack.enter_context(
                patch("app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None)
            )
            mocks["create"] = stack.enter_context(patch("app.services.mailbox_drafts.create_gmail_draft"))
            mocks["create"].return_value = {
                "id": "draft-1",
                "message": {"id": "message-1", "threadId": "thread-a"},
            }
            mocks["import"] = stack.enter_context(
                patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message())
            )
            mocks["upsert"] = stack.enter_context(
                patch("app.services.mailbox_drafts.upsert_client_draft", return_value=draft_record())
            )
            mocks["after"] = stack.enter_context(patch("app.services.mailbox_drafts._after_draft_change"))
            mocks["detail"] = stack.enter_context(patch("app.services.mailbox_sends.get_mail_group_detail"))
            mocks["detail"].return_value = (
                SimpleNamespace(messages=list(messages or [sample_message()])) if detail_present else None
            )
            mocks["thread_messages"] = stack.enter_context(
                patch(
                    "app.services.mailbox_sends.list_messages_for_gmail_thread",
                    return_value=list(gmail_thread_messages or []),
                )
            )
            mocks["user"] = stack.enter_context(
                patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
            )
            response = save_draft(self.settings, user_id="user-1", request=request)
        return response, mocks

    def test_get_draft_resolves_large_text_body_before_returning_editable_content(self) -> None:
        encoded_body = base64.urlsafe_b64encode(b"Complete large draft body").decode("ascii").rstrip("=")
        payload = {
            "message": {
                "id": "message-1",
                "threadId": "thread-1",
                "labelIds": ["DRAFT"],
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "large-draft-body", "size": 4096},
                },
            }
        }
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch(
            "app.services.mailbox_drafts.get_client_draft",
            return_value=draft_record(),
        ), patch("app.services.mailbox_drafts._local_draft_message", return_value=None), patch(
            "app.services.mailbox_drafts.fetch_gmail_draft",
            return_value=payload,
        ), patch(
            "app.services.mailbox_drafts.fetch_gmail_attachment",
            return_value={"data": encoded_body},
        ) as fetch_attachment, patch(
            "app.services.mailbox_drafts.upsert_gmail_messages"
        ) as upsert_messages, patch(
            "app.services.mailbox_drafts.upsert_client_draft",
            return_value=draft_record(),
        ):
            response = get_draft(
                self.settings,
                user_id="user-1",
                mailbox_thread_id="draft-1",
            )

        self.assertEqual(response.state, "saved")
        self.assertEqual(response.body_text, "Complete large draft body")
        fetch_attachment.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_id="message-1",
            attachment_id="large-draft-body",
        )
        imported = upsert_messages.call_args.args[1][0]
        self.assertEqual(imported.body_fetch_status, "fetched")
        self.assertEqual(imported.text_body, "Complete large draft body")

    def test_get_draft_keeps_usable_html_when_optional_parts_fail_to_download(self) -> None:
        html = base64.urlsafe_b64encode(
            b'<html><body><table><tr><td>Usable HTML draft</td></tr></table>'
            b'<img src="cid:missing-logo" width="120"></body></html>'
        ).decode("ascii").rstrip("=")
        payload = {
            "message": {
                "id": "message-1",
                "threadId": "thread-1",
                "labelIds": ["DRAFT"],
                "payload": {
                    "mimeType": "multipart/related",
                    "parts": [
                        {
                            "mimeType": "multipart/alternative",
                            "parts": [
                                {
                                    "mimeType": "text/plain",
                                    "body": {"attachmentId": "missing-plain", "size": 4096},
                                },
                                {"mimeType": "text/html", "body": {"data": html}},
                            ],
                        },
                        {
                            "mimeType": "image/png",
                            "headers": [{"name": "Content-ID", "value": "<missing-logo>"}],
                            "body": {"attachmentId": "missing-cid", "size": 1024},
                        },
                    ],
                },
            }
        }
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch(
            "app.services.mailbox_drafts.get_client_draft",
            return_value=draft_record(),
        ), patch("app.services.mailbox_drafts._local_draft_message", return_value=None), patch(
            "app.services.mailbox_drafts.fetch_gmail_draft",
            return_value=payload,
        ), patch(
            "app.services.mailbox_drafts.fetch_gmail_attachment",
            side_effect=RuntimeError("optional attachment unavailable"),
        ), patch("app.services.mailbox_drafts.upsert_gmail_messages"), patch(
            "app.services.mailbox_drafts.upsert_client_draft",
            return_value=draft_record(),
        ):
            response = get_draft(
                self.settings,
                user_id="user-1",
                mailbox_thread_id="draft-1",
            )

        self.assertEqual(response.state, "saved")
        self.assertIn("Usable HTML draft", response.body_text)
        self.assertIsNotNone(response.body_html)

    def test_get_draft_never_regresses_an_inflight_or_finalized_mapping(self) -> None:
        for state in ("sending", "sent", "deleted"):
            with self.subTest(state=state):
                mapping = replace(
                    draft_record(state="sent" if state == "sent" else "saved"),
                    state=state,
                    last_client_send_id="send-2" if state == "sending" else None,
                )
                with (
                    patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
                    patch("app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()),
                    patch("app.services.mailbox_drafts.get_client_draft", return_value=mapping),
                    patch("app.services.mailbox_drafts._local_draft_message", return_value=None),
                    patch("app.services.mailbox_drafts.fetch_gmail_draft") as fetch,
                    patch("app.services.mailbox_drafts.upsert_client_draft") as upsert,
                ):
                    response = get_draft(
                        self.settings,
                        user_id="user-1",
                        mailbox_thread_id="draft-1",
                    )

                self.assertEqual(response.state, "failed" if state == "sending" else state)
                fetch.assert_not_called()
                upsert.assert_not_called()

    def test_get_draft_does_not_bless_an_ambiguous_create_intent_hash(self) -> None:
        body = base64.urlsafe_b64encode(b"Provider-observed body").decode("ascii").rstrip("=")
        mapping = replace(
            draft_record(),
            state="creating",
            content_hash="unconfirmed-newer-intent-hash",
        )
        payload = {
            "message": {
                "id": "message-1",
                "threadId": "thread-1",
                "labelIds": ["DRAFT"],
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": body},
                },
            }
        }
        with (
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()),
            patch("app.services.mailbox_drafts.get_client_draft", return_value=mapping),
            patch("app.services.mailbox_drafts._local_draft_message", return_value=None),
            patch("app.services.mailbox_drafts.fetch_gmail_draft", return_value=payload),
            patch("app.services.mailbox_drafts.upsert_gmail_messages") as upsert_messages,
            patch(
                "app.services.mailbox_drafts.upsert_client_draft",
                return_value=replace(mapping, state="saved"),
            ) as upsert_draft,
        ):
            response = get_draft(
                self.settings,
                user_id="user-1",
                mailbox_thread_id="draft-1",
            )

        imported = upsert_messages.call_args.args[1][0]
        self.assertEqual(response.state, "saved")
        self.assertNotEqual(imported.body_hash, mapping.content_hash)
        self.assertEqual(upsert_draft.call_args.kwargs["content_hash"], imported.body_hash)

    @patch("app.services.mailbox_drafts._after_draft_change")
    @patch("app.services.mailbox_drafts.upsert_client_draft", return_value=draft_record())
    @patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message())
    @patch("app.services.mailbox_drafts.create_gmail_draft")
    @patch("app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None)
    @patch("app.services.mailbox_drafts.get_client_draft", return_value=None)
    @patch("app.services.mailbox_drafts.client_draft_lock")
    @patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True)
    def test_create_draft_uses_stable_identity_and_persists_mapping(
        self,
        _mock_can_manage: Mock,
        mock_lock: Mock,
        _mock_get: Mock,
        _mock_find: Mock,
        mock_create: Mock,
        _mock_import: Mock,
        mock_upsert: Mock,
        _mock_after: Mock,
    ) -> None:
        mock_lock.return_value = nullcontext()
        mock_create.return_value = {"id": "draft-1", "message": {"id": "message-1", "threadId": "thread-1"}}
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            to=["recipient@example.com"],
            subject="Launch",
            body_text="Draft body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )

        response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "saved")
        self.assertEqual(response.gmail_draft_id, "draft-1")
        raw = mock_create.call_args.kwargs["raw_message"]
        self.assertTrue(raw)
        self.assertEqual(mock_upsert.call_args.kwargs["client_draft_id"], "client-draft-1")
        self.assertEqual(mock_upsert.call_args.kwargs["gmail_message_id"], "message-1")

    def test_create_checkpoints_provider_identity_before_best_effort_projection(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            subject="Launch",
            body_text="Draft body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        states: list[str] = []

        def checkpoint(*_args, **kwargs):
            states.append(kwargs["state"])
            return replace(
                draft_record(),
                gmail_draft_id=kwargs["gmail_draft_id"],
                gmail_message_id=kwargs["gmail_message_id"],
                gmail_thread_id=kwargs["gmail_thread_id"],
                content_hash=kwargs["content_hash"],
                state=kwargs["state"],
            )

        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=None), patch(
            "app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None
        ), patch(
            "app.services.mailbox_drafts.upsert_client_draft", side_effect=checkpoint
        ), patch(
            "app.services.mailbox_drafts.create_gmail_draft",
            return_value={"id": "draft-1", "message": {"id": "message-1", "threadId": "thread-1"}},
        ), patch(
            "app.services.mailbox_drafts._import_gmail_message",
            side_effect=GoogleCredentialsUnavailable("credentials expired after provider success"),
        ), patch("app.services.mailbox_drafts._after_draft_change"):
            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "saved")
        self.assertEqual(response.gmail_draft_id, "draft-1")
        self.assertEqual(states, ["creating", "saved"])

    def test_delete_between_provider_create_and_checkpoint_never_persists_saved_mapping(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            subject="Launch",
            body_text="Draft body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        states: list[str] = []
        provider_deleted = False

        def checkpoint(*_args, **kwargs):
            states.append(kwargs["state"])
            return replace(
                draft_record(),
                gmail_draft_id=kwargs["gmail_draft_id"],
                gmail_message_id=kwargs["gmail_message_id"],
                gmail_thread_id=kwargs["gmail_thread_id"],
                content_hash=kwargs["content_hash"],
                state=kwargs["state"],
                error=kwargs.get("error"),
            )

        class LockHandle:
            def adopt_gmail_draft_id(self, gmail_draft_id: str) -> None:
                nonlocal provider_deleted
                self_outer.assertEqual(gmail_draft_id, "draft-1")
                deleted = delete_draft_by_id(
                    self_outer.settings,
                    user_id="user-1",
                    gmail_draft_id=gmail_draft_id,
                )
                self_outer.assertTrue(deleted)
                provider_deleted = True

        @contextmanager
        def draft_lock(*_args, **_kwargs):
            yield LockHandle()

        def verify_provider(*_args, **_kwargs):
            if provider_deleted:
                raise google_http_error(404)
            return {"id": "draft-1", "message": {"id": "message-1", "threadId": "thread-1"}}

        self_outer = self
        with (
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", side_effect=draft_lock),
            patch("app.services.mailbox_drafts.get_client_draft", return_value=None),
            patch("app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None),
            patch("app.services.mailbox_drafts.upsert_client_draft", side_effect=checkpoint),
            patch(
                "app.services.mailbox_drafts.create_gmail_draft",
                return_value={"id": "draft-1", "message": {"id": "message-1", "threadId": "thread-1"}},
            ),
            patch("app.services.mailbox_drafts.fetch_gmail_draft", side_effect=verify_provider),
            patch("app.services.mailbox_drafts._delete_draft_by_id_locked", return_value=True),
            patch("app.services.mailbox_drafts._import_gmail_message") as import_message,
        ):
            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertTrue(provider_deleted)
        self.assertEqual(response.state, "failed")
        self.assertEqual(states, ["creating", "deleted"])
        self.assertNotIn("saved", states)
        import_message.assert_not_called()

    def test_provider_create_handoff_does_not_overwrite_a_waiters_newer_checkpoint(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            subject="Older creator content",
            body_text="Older body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        creating = replace(
            draft_record(),
            gmail_draft_id=None,
            gmail_message_id=None,
            state="creating",
            content_hash="creator-hash",
        )
        newer = replace(
            draft_record(),
            content_hash="newer-waiter-hash",
            gmail_message_id="message-newer",
        )
        reads = iter([None, None, newer])
        adopted: list[str] = []

        class LockHandle:
            def adopt_gmail_draft_id(self, gmail_draft_id: str) -> None:
                adopted.append(gmail_draft_id)

        @contextmanager
        def draft_lock(*_args, **_kwargs):
            yield LockHandle()

        def checkpoint(*_args, **kwargs):
            self.assertEqual(kwargs["state"], "creating")
            return creating

        with (
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", side_effect=draft_lock),
            patch("app.services.mailbox_drafts.get_client_draft", side_effect=lambda *_args, **_kwargs: next(reads)),
            patch("app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None),
            patch("app.services.mailbox_drafts.upsert_client_draft", side_effect=checkpoint) as upsert,
            patch(
                "app.services.mailbox_drafts.create_gmail_draft",
                return_value={"id": "draft-1", "message": {"id": "message-creator", "threadId": "thread-1"}},
            ),
            patch(
                "app.services.mailbox_drafts.fetch_gmail_draft",
                return_value={"id": "draft-1", "message": {"id": "message-newer", "threadId": "thread-1"}},
            ),
            patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message()),
            patch("app.services.mailbox_drafts._after_draft_change") as after_change,
        ):
            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "saved")
        self.assertEqual(response.gmail_message_id, "message-newer")
        self.assertEqual(adopted, ["draft-1"])
        self.assertEqual([call.kwargs["state"] for call in upsert.call_args_list], ["creating"])
        after_change.assert_not_called()

    def test_provider_create_handoff_preserves_a_waiters_ambiguous_newer_create(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            subject="Older creator content",
            body_text="Older body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        creator_checkpoint = replace(
            draft_record(),
            gmail_draft_id=None,
            gmail_message_id=None,
            state="creating",
            content_hash="creator-hash",
        )
        newer_checkpoint = replace(
            draft_record(),
            state="creating",
            content_hash="newer-waiter-hash",
            gmail_message_id="message-newer",
        )
        reads = iter([None, None, newer_checkpoint])

        class LockHandle:
            def adopt_gmail_draft_id(self, _gmail_draft_id: str) -> None:
                return None

        @contextmanager
        def draft_lock(*_args, **_kwargs):
            yield LockHandle()

        def checkpoint(*_args, **kwargs):
            self.assertEqual(kwargs["state"], "creating")
            return creator_checkpoint

        with (
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", side_effect=draft_lock),
            patch("app.services.mailbox_drafts.get_client_draft", side_effect=lambda *_args, **_kwargs: next(reads)),
            patch("app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None),
            patch("app.services.mailbox_drafts.upsert_client_draft", side_effect=checkpoint) as upsert,
            patch(
                "app.services.mailbox_drafts.create_gmail_draft",
                return_value={"id": "draft-1", "message": {"id": "message-creator", "threadId": "thread-1"}},
            ),
            patch(
                "app.services.mailbox_drafts.fetch_gmail_draft",
                return_value={"id": "draft-1", "message": {"id": "message-newer", "threadId": "thread-1"}},
            ),
            patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message()),
            patch("app.services.mailbox_drafts._after_draft_change") as after_change,
        ):
            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "failed")
        self.assertIn("newer draft save", response.error or "")
        self.assertEqual([call.kwargs["state"] for call in upsert.call_args_list], ["creating"])
        after_change.assert_not_called()

    def test_delete_rejects_sending_or_sent_mapping_before_provider_mutation(self) -> None:
        for state in ("sending", "sent"):
            with self.subTest(state=state):
                mapping = replace(
                    draft_record(state="sent" if state == "sent" else "saved"),
                    state=state,
                    last_client_send_id="send-2",
                )
                with (
                    patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
                    patch("app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()),
                    patch("app.services.mailbox_drafts.get_client_draft", return_value=mapping),
                    patch("app.services.mailbox_drafts.fetch_gmail_draft") as fetch,
                    patch("app.services.mailbox_drafts.delete_gmail_draft") as delete,
                    patch("app.services.mailbox_drafts.mark_client_draft_deleted") as mark_deleted,
                ):
                    with self.assertRaisesRegex(MailDraftIdentityConflict, "cannot be deleted"):
                        delete_draft_by_id(
                            self.settings,
                            user_id="user-1",
                            gmail_draft_id="draft-1",
                        )

                fetch.assert_not_called()
                delete.assert_not_called()
                mark_deleted.assert_not_called()

    def test_deleted_checkpoint_is_conditioned_against_send_states(self) -> None:
        connection = Mock()
        connection.execute.return_value.mappings.return_value.first.return_value = None
        with (
            patch("app.db.mail_groups.get_engine", return_value=object()),
            patch(
                "app.db.mail_groups.user_mail_write_transaction",
                return_value=nullcontext(connection),
            ),
        ):
            deleted = mail_group_db.mark_client_draft_deleted(
                "postgresql://example/db",
                user_id="user-1",
                client_draft_id="client-draft-1",
            )

        self.assertIsNone(deleted)
        sql = str(connection.execute.call_args.args[0])
        self.assertIn("state NOT IN ('sending', 'sent')", sql)

    def test_waiting_save_adopts_provider_lock_from_authoritative_reread_before_update(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            subject="Changed after waiting",
            body_text="Updated body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        mapped = replace(draft_record(), content_hash="old-content")
        reads = iter([None, mapped, mapped])
        events: list[str] = []

        class LockHandle:
            def adopt_gmail_draft_id(self, gmail_draft_id: str) -> None:
                self_outer.assertEqual(gmail_draft_id, "draft-1")
                events.append("adopt")

        @contextmanager
        def draft_lock(*_args, **_kwargs):
            yield LockHandle()

        def fetch_existing(*_args, **_kwargs):
            self.assertEqual(events, ["adopt"])
            events.append("fetch")
            return sample_message()

        def update(*_args, **_kwargs):
            events.append("update")
            return {"id": "draft-1", "message": {"id": "message-1", "threadId": "thread-1"}}

        self_outer = self
        with (
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", side_effect=draft_lock),
            patch("app.services.mailbox_drafts.get_client_draft", side_effect=lambda *_args, **_kwargs: next(reads)),
            patch("app.services.mailbox_drafts._gmail_draft_message", side_effect=fetch_existing),
            patch("app.services.mailbox_drafts.update_gmail_draft", side_effect=update),
            patch("app.services.mailbox_drafts.upsert_client_draft", return_value=mapped),
            patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message()),
            patch("app.services.mailbox_drafts._after_draft_change"),
        ):
            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "saved")
        self.assertEqual(events, ["adopt", "fetch", "update"])

    def test_message_id_recovery_adopts_provider_lock_before_fetch_and_update(self) -> None:
        creating = replace(
            draft_record(),
            gmail_draft_id=None,
            gmail_message_id=None,
            state="creating",
            content_hash="old-content",
        )
        request = MailDraftSaveRequest(
            client_draft_id=creating.client_draft_id,
            subject="Recovered",
            body_text="Recovered body",
            attachments=[],
            created_at=creating.created_at,
        )
        events: list[str] = []

        class LockHandle:
            def adopt_gmail_draft_id(self, gmail_draft_id: str) -> None:
                self_outer.assertEqual(gmail_draft_id, "draft-1")
                events.append("adopt")

        @contextmanager
        def draft_lock(*_args, **_kwargs):
            yield LockHandle()

        def fetch_existing(*_args, **_kwargs):
            self.assertEqual(events, ["adopt"])
            events.append("fetch")
            return sample_message()

        def update(*_args, **_kwargs):
            events.append("update")
            return {"id": "draft-1", "message": {"id": "message-1", "threadId": "thread-1"}}

        def checkpoint(*_args, **kwargs):
            return replace(
                creating,
                gmail_draft_id=kwargs["gmail_draft_id"],
                gmail_message_id=kwargs["gmail_message_id"],
                gmail_thread_id=kwargs["gmail_thread_id"],
                content_hash=kwargs["content_hash"],
                state=kwargs["state"],
            )

        self_outer = self
        with (
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", side_effect=draft_lock),
            patch("app.services.mailbox_drafts.get_client_draft", return_value=creating),
            patch(
                "app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id",
                return_value={"id": "draft-1"},
            ),
            patch("app.services.mailbox_drafts._gmail_draft_message", side_effect=fetch_existing),
            patch("app.services.mailbox_drafts.update_gmail_draft", side_effect=update),
            patch("app.services.mailbox_drafts.upsert_client_draft", side_effect=checkpoint),
            patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message()),
            patch("app.services.mailbox_drafts._after_draft_change"),
        ):
            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "saved")
        self.assertEqual(events, ["adopt", "fetch", "update"])

    def test_ambiguous_creating_draft_search_miss_never_blindly_recreates(self) -> None:
        creating = replace(
            draft_record(),
            gmail_draft_id=None,
            gmail_message_id=None,
            state="creating",
        )
        request = MailDraftSaveRequest(
            client_draft_id=creating.client_draft_id,
            subject="Launch",
            body_text="Draft body",
            attachments=[],
            created_at=creating.created_at,
        )
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=creating), patch(
            "app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None
        ), patch("app.services.mailbox_drafts.create_gmail_draft") as create:
            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "failed")
        self.assertIn("still being confirmed", response.error or "")
        create.assert_not_called()

    def test_saving_draft_while_send_is_pending_never_mutates_provider_draft(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            gmail_draft_id="draft-1",
            subject="Edited after send",
            body_text="This must become a different draft.",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        sending = replace(draft_record(), state="sending", last_client_send_id="send-2")
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=sending), patch(
            "app.services.mailbox_drafts.fetch_gmail_draft"
        ) as fetch, patch("app.services.mailbox_drafts.update_gmail_draft") as update:
            with self.assertRaisesRegex(MailDraftIdentityConflict, "being sent"):
                save_draft(self.settings, user_id="user-1", request=request)

        fetch.assert_not_called()
        update.assert_not_called()

    def test_definite_create_rejection_marks_checkpoint_failed(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            subject="Rejected draft",
            body_text="Draft body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        creating = replace(
            draft_record(),
            gmail_draft_id=None,
            gmail_message_id=None,
            state="creating",
        )
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=None), patch(
            "app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None
        ), patch("app.services.mailbox_drafts.upsert_client_draft", return_value=creating) as upsert, patch(
            "app.services.mailbox_drafts.create_gmail_draft", side_effect=google_http_error(400)
        ):
            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "failed")
        self.assertEqual([entry.kwargs["state"] for entry in upsert.call_args_list], ["creating", "failed"])

    def test_ambiguous_create_rejection_keeps_creating_checkpoint(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            subject="Delayed draft",
            body_text="Draft body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        creating = replace(
            draft_record(),
            gmail_draft_id=None,
            gmail_message_id=None,
            state="creating",
        )
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=None), patch(
            "app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None
        ), patch("app.services.mailbox_drafts.upsert_client_draft", return_value=creating) as upsert, patch(
            "app.services.mailbox_drafts.create_gmail_draft", side_effect=google_http_error(408)
        ):
            with self.assertRaises(HttpError):
                save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual([entry.kwargs["state"] for entry in upsert.call_args_list], ["creating"])

    def test_durable_mapping_rejects_mismatched_provider_draft_before_mutation(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            gmail_draft_id="another-draft",
            subject="Launch",
            body_text="Draft body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record()), patch(
            "app.services.mailbox_drafts.update_gmail_draft"
        ) as update:
            with self.assertRaises(MailDraftIdentityConflict):
                save_draft(self.settings, user_id="user-1", request=request)

        update.assert_not_called()

    def test_reply_draft_uses_response_recipient_thread_headers_and_quote(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-response-1",
            response_mode="reply",
            mailbox_thread_id="group-1",
            source_message_id="msg-1",
            body_text="Reply body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )

        response, mocks = self._save_response_draft(request)

        self.assertEqual(response.state, "saved")
        create_kwargs = mocks["create"].call_args.kwargs
        self.assertEqual(create_kwargs["gmail_thread_id"], "thread-a")
        parsed = message_from_bytes(base64.urlsafe_b64decode(create_kwargs["raw_message"]), policy=policy.default)
        self.assertEqual(parsed["To"], "Partner <partner@example.com>")
        self.assertEqual(parsed["Subject"], "Re: Project check-in")
        self.assertEqual(parsed["In-Reply-To"], "<msg-1@example.com>")
        self.assertEqual(parsed["References"], "<root@example.com> <msg-1@example.com>")
        self.assertRegex(str(parsed["Message-ID"]), r"^<draft\.[0-9a-f]{32}@electronic-mail\.local>$")
        body = parsed.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("Reply body", body)
        self.assertIn("> Reply test", body)
        self.assertEqual(mocks["upsert"].call_args.kwargs["client_draft_id"], "client-response-1")

    def test_reply_all_draft_uses_sender_and_original_cc_without_self(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-response-2",
            response_mode="reply_all",
            mailbox_thread_id="group-1",
            body_text="Reply everyone",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )

        response, mocks = self._save_response_draft(request)

        self.assertEqual(response.state, "saved")
        parsed = message_from_bytes(
            base64.urlsafe_b64decode(mocks["create"].call_args.kwargs["raw_message"]),
            policy=policy.default,
        )
        self.assertEqual(parsed["To"], "Partner <partner@example.com>")
        self.assertEqual(parsed["Cc"], "Observer <observer@example.com>")
        recipients = f"{parsed['To']} {parsed['Cc']}".lower()
        self.assertNotIn("me@example.com", recipients)

    def test_forward_draft_quotes_source_and_materializes_original_attachments(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-forward-1",
            response_mode="forward",
            mailbox_thread_id="group-1",
            source_message_id="msg-1",
            to=["recipient@example.com"],
            body_text="For your review",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        with patch(
            "app.services.mailbox_sends.gmail_attachments_for_message",
            return_value=[SimpleNamespace(filename="original.txt", mime_type="text/plain", attachment_id="att-1")],
        ), patch(
            "app.services.mailbox_sends.fetch_gmail_attachment",
            return_value={"data": "b3JpZ2luYWw="},
        ) as mock_fetch:
            response, mocks = self._save_response_draft(request)

        self.assertEqual(response.state, "saved")
        create_kwargs = mocks["create"].call_args.kwargs
        self.assertIsNone(create_kwargs["gmail_thread_id"])
        parsed = message_from_bytes(base64.urlsafe_b64decode(create_kwargs["raw_message"]), policy=policy.default)
        self.assertEqual(parsed["To"], "recipient@example.com")
        self.assertEqual(parsed["Subject"], "Fwd: Project check-in")
        self.assertIsNone(parsed["In-Reply-To"])
        self.assertIn("---------- Forwarded message ---------", parsed.get_body(preferencelist=("plain",)).get_content())
        attachment_parts = list(parsed.iter_attachments())
        self.assertEqual([part.get_filename() for part in attachment_parts], ["original.txt"])
        self.assertEqual(attachment_parts[0].get_payload(decode=True), b"original")
        mock_fetch.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_id="msg-1",
            attachment_id="att-1",
        )

    def test_forward_draft_autosave_retains_original_attachment_without_duplicating_it(self) -> None:
        existing_message = replace(
            sample_message(),
            message_id="draft-message-1",
            gmail_thread_id="forward-draft-thread",
            label_ids=["DRAFT"],
        )
        retained_attachment = SimpleNamespace(
            filename="original.txt",
            mime_type="text/plain",
            attachment_id="att-1",
        )
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            gmail_draft_id="draft-1",
            response_mode="forward",
            mailbox_thread_id="group-1",
            source_message_id="msg-1",
            to=["recipient@example.com"],
            body_text="Updated note",
            # Omission means retain the existing Gmail draft attachments.
            attachments=None,
            created_at="2026-07-13T09:00:00+00:00",
        )
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True))
            mock_lock = stack.enter_context(patch("app.services.mailbox_drafts.client_draft_lock"))
            mock_lock.return_value = nullcontext()
            stack.enter_context(
                patch(
                    "app.services.mailbox_drafts.get_client_draft",
                    return_value=replace(
                        draft_record(),
                        gmail_thread_id="stale-forward-thread",
                        content_hash="forward-originals:included:previous-hash",
                    ),
                )
            )
            stack.enter_context(
                patch("app.services.mailbox_drafts._gmail_draft_message", return_value=existing_message)
            )
            stack.enter_context(
                patch(
                    "app.services.mailbox_drafts.gmail_attachments_for_message",
                    side_effect=lambda message: [retained_attachment]
                    if message.message_id == "draft-message-1"
                    else [],
                )
            )
            mock_retained_fetch = stack.enter_context(
                patch(
                    "app.services.mailbox_drafts.fetch_gmail_attachment",
                    return_value={"data": "b3JpZ2luYWw="},
                )
            )
            stack.enter_context(
                patch(
                    "app.services.mailbox_sends.get_mail_group_detail",
                    return_value=SimpleNamespace(messages=[sample_message()]),
                )
            )
            stack.enter_context(
                patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
            )
            mock_source_attachments = stack.enter_context(
                patch("app.services.mailbox_sends.gmail_attachments_for_message")
            )
            mock_source_fetch = stack.enter_context(
                patch("app.services.mailbox_sends.fetch_gmail_attachment")
            )
            mock_update = stack.enter_context(patch("app.services.mailbox_drafts.update_gmail_draft"))
            mock_update.return_value = {
                "id": "draft-1",
                "message": {"id": "message-2", "threadId": "forward-draft-thread"},
            }
            mock_create = stack.enter_context(patch("app.services.mailbox_drafts.create_gmail_draft"))
            stack.enter_context(
                patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message())
            )
            stack.enter_context(patch("app.services.mailbox_drafts._remove_local_messages"))
            stack.enter_context(
                patch("app.services.mailbox_drafts.upsert_client_draft", return_value=draft_record())
            )
            stack.enter_context(patch("app.services.mailbox_drafts._after_draft_change"))

            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "saved")
        parsed = message_from_bytes(
            base64.urlsafe_b64decode(mock_update.call_args.kwargs["raw_message"]),
            policy=policy.default,
        )
        attachment_parts = list(parsed.iter_attachments())
        self.assertEqual([part.get_filename() for part in attachment_parts], ["original.txt"])
        self.assertEqual(attachment_parts[0].get_payload(decode=True), b"original")
        self.assertEqual(mock_update.call_args.kwargs["gmail_thread_id"], "forward-draft-thread")
        mock_retained_fetch.assert_called_once()
        mock_source_attachments.assert_not_called()
        mock_source_fetch.assert_not_called()
        mock_create.assert_not_called()

    def test_forward_draft_toggle_from_excluded_to_included_adds_original_once(self) -> None:
        existing_message = replace(
            sample_message(),
            message_id="draft-message-1",
            gmail_thread_id="forward-draft-thread",
            label_ids=["DRAFT"],
        )
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            gmail_draft_id="draft-1",
            response_mode="forward",
            mailbox_thread_id="group-1",
            source_message_id="msg-1",
            to=["recipient@example.com"],
            body_text="Updated note",
            attachments=None,
            include_original_attachments=True,
            created_at="2026-07-13T09:00:00+00:00",
        )
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True))
            mock_lock = stack.enter_context(patch("app.services.mailbox_drafts.client_draft_lock"))
            mock_lock.return_value = nullcontext()
            stack.enter_context(
                patch(
                    "app.services.mailbox_drafts.get_client_draft",
                    return_value=replace(
                        draft_record(),
                        gmail_thread_id="forward-draft-thread",
                        content_hash="forward-originals:excluded:previous-hash",
                    ),
                )
            )
            stack.enter_context(
                patch("app.services.mailbox_drafts._gmail_draft_message", return_value=existing_message)
            )
            stack.enter_context(
                patch("app.services.mailbox_drafts.gmail_attachments_for_message", return_value=[])
            )
            mock_retained_fetch = stack.enter_context(
                patch("app.services.mailbox_drafts.fetch_gmail_attachment")
            )
            stack.enter_context(
                patch(
                    "app.services.mailbox_sends.get_mail_group_detail",
                    return_value=SimpleNamespace(messages=[sample_message()]),
                )
            )
            stack.enter_context(
                patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
            )
            stack.enter_context(
                patch(
                    "app.services.mailbox_sends.gmail_attachments_for_message",
                    return_value=[
                        SimpleNamespace(filename="original.txt", mime_type="text/plain", attachment_id="att-1")
                    ],
                )
            )
            mock_source_fetch = stack.enter_context(
                patch(
                    "app.services.mailbox_sends.fetch_gmail_attachment",
                    return_value={"data": "b3JpZ2luYWw="},
                )
            )
            mock_update = stack.enter_context(patch("app.services.mailbox_drafts.update_gmail_draft"))
            mock_update.return_value = {
                "id": "draft-1",
                "message": {"id": "message-2", "threadId": "forward-draft-thread"},
            }
            stack.enter_context(
                patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message())
            )
            stack.enter_context(patch("app.services.mailbox_drafts._remove_local_messages"))
            mock_upsert = stack.enter_context(
                patch("app.services.mailbox_drafts.upsert_client_draft", return_value=draft_record())
            )
            stack.enter_context(patch("app.services.mailbox_drafts._after_draft_change"))

            response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "saved")
        parsed = message_from_bytes(
            base64.urlsafe_b64decode(mock_update.call_args.kwargs["raw_message"]),
            policy=policy.default,
        )
        attachment_parts = list(parsed.iter_attachments())
        self.assertEqual([part.get_filename() for part in attachment_parts], ["original.txt"])
        self.assertEqual(attachment_parts[0].get_payload(decode=True), b"original")
        self.assertTrue(
            mock_upsert.call_args.kwargs["content_hash"].startswith("forward-originals:included:")
        )
        mock_source_fetch.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_id="msg-1",
            attachment_id="att-1",
        )
        mock_retained_fetch.assert_not_called()

    def test_response_draft_rejects_source_outside_selected_thread(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-invalid-source",
            response_mode="reply",
            mailbox_thread_id="group-1",
            source_message_id="another-users-message",
            body_text="Reply body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )

        response, mocks = self._save_response_draft(request)

        self.assertEqual(response.state, "failed")
        self.assertEqual(response.error, "Selected email is not part of this thread.")
        mocks["detail"].assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            group_id="group-1",
        )
        mocks["create"].assert_not_called()
        mocks["upsert"].assert_not_called()

    def test_response_draft_thread_fallback_is_scoped_to_authenticated_user(self) -> None:
        request = MailDraftSaveRequest(
            client_draft_id="client-isolation",
            response_mode="reply",
            mailbox_thread_id="thread-not-owned",
            body_text="Reply body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )

        response, mocks = self._save_response_draft(
            request,
            detail_present=False,
            gmail_thread_messages=[],
        )

        self.assertEqual(response.state, "failed")
        self.assertEqual(response.error, "Email thread not found.")
        mocks["thread_messages"].assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            gmail_thread_id="thread-not-owned",
        )
        mocks["create"].assert_not_called()

    @patch("app.services.mailbox_drafts.create_gmail_draft")
    @patch(
        "app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id",
        side_effect=GoogleCredentialsUnavailable("Google credentials are not connected"),
    )
    @patch("app.services.mailbox_drafts.get_client_draft", return_value=None)
    @patch("app.services.mailbox_drafts.client_draft_lock")
    @patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True)
    def test_response_draft_returns_reauth_when_runtime_credentials_are_unavailable(
        self,
        _mock_can_manage: Mock,
        mock_lock: Mock,
        _mock_get: Mock,
        _mock_find: Mock,
        mock_create: Mock,
    ) -> None:
        mock_lock.return_value = nullcontext()
        request = MailDraftSaveRequest(
            client_draft_id="client-reauth",
            response_mode="reply",
            mailbox_thread_id="group-1",
            body_text="Reply body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )

        response = save_draft(self.settings, user_id="user-1", request=request)

        self.assertEqual(response.state, "reauth_required")
        self.assertEqual(response.reauth_url, "http://127.0.0.1:3001/auth/google")
        mock_create.assert_not_called()

    @patch("app.services.mailbox_drafts.send_gmail_draft")
    @patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record(state="sent"))
    @patch("app.services.mailbox_drafts.client_draft_lock")
    @patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True)
    def test_send_draft_replays_idempotent_sent_response_without_google_call(
        self,
        _mock_can_manage: Mock,
        mock_lock: Mock,
        _mock_get: Mock,
        mock_send: Mock,
    ) -> None:
        mock_lock.return_value = nullcontext()

        response = send_saved_draft(
            self.settings,
            user_id="user-1",
            gmail_draft_id="draft-1",
            request=SimpleNamespace(client_send_id="send-1", client_draft_id="client-draft-1"),
        )

        self.assertEqual(response.state, "sent")
        self.assertEqual(response.gmail_message_id, "sent-message-1")
        mock_send.assert_not_called()

    @patch(
        "app.services.mailbox_drafts.send_gmail_draft",
        side_effect=GoogleCredentialsUnavailable("Google credentials are not connected"),
    )
    @patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record())
    @patch("app.services.mailbox_drafts.client_draft_lock")
    @patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True)
    def test_send_saved_draft_returns_reauth_when_runtime_credentials_are_unavailable(
        self,
        _mock_can_manage: Mock,
        mock_lock: Mock,
        _mock_get: Mock,
        _mock_send: Mock,
    ) -> None:
        mock_lock.return_value = nullcontext()
        sending = replace(draft_record(), state="sending", last_client_send_id="send-2")
        with patch(
            "app.services.mailbox_drafts.mark_client_draft_sending",
            return_value=sending,
        ), patch("app.services.mailbox_drafts.restore_client_draft_after_definite_send_failure") as restore:
            response = send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
            )

        self.assertEqual(response.state, "reauth_required")
        self.assertEqual(response.reauth_url, "http://127.0.0.1:3001/auth/google")
        restore.assert_called_once()

    def test_send_marks_sent_before_projection_and_projection_failure_stays_sent(self) -> None:
        sending = replace(draft_record(), state="sending", last_client_send_id="send-2")
        sent = replace(
            draft_record(state="sent"),
            last_client_send_id="send-2",
            sent_message_id="sent-message-2",
        )
        order: list[str] = []

        def mark_sent(*_args, **_kwargs):
            order.append("sent-checkpoint")
            return sent

        def import_message(*_args, **_kwargs):
            order.append("projection")
            raise GoogleCredentialsUnavailable("credentials expired after provider success")

        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record()), patch(
            "app.services.mailbox_drafts.mark_client_draft_sending", return_value=sending
        ), patch(
            "app.services.mailbox_drafts.send_gmail_draft",
            return_value={"id": "sent-message-2", "threadId": "thread-1"},
        ), patch("app.services.mailbox_drafts.mark_client_draft_sent", side_effect=mark_sent), patch(
            "app.services.mailbox_drafts._import_gmail_message", side_effect=import_message
        ), patch("app.services.mailbox_drafts._after_draft_change"):
            response = send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
            )

        self.assertEqual(response.state, "sent")
        self.assertEqual(order, ["sent-checkpoint", "projection"])

    def test_sending_retry_search_miss_never_calls_provider_send_again(self) -> None:
        sending = replace(draft_record(), state="sending", last_client_send_id="send-2")
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=sending), patch(
            "app.services.mailbox_drafts.find_sent_gmail_message_by_rfc822_message_id", return_value=None
        ), patch("app.services.mailbox_drafts.send_gmail_draft") as send:
            response = send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
            )

        self.assertEqual(response.state, "sending")
        self.assertEqual(response.client_send_id, "send-2")
        send.assert_not_called()

    def test_definite_send_rejection_restores_saved_state(self) -> None:
        sending = replace(draft_record(), state="sending", last_client_send_id="send-2")
        restored = replace(sending, state="saved", error="Google rejected the send.")
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record()), patch(
            "app.services.mailbox_drafts.mark_client_draft_sending", return_value=sending
        ), patch(
            "app.services.mailbox_drafts.send_gmail_draft", side_effect=google_http_error(400)
        ), patch(
            "app.services.mailbox_drafts.restore_client_draft_after_definite_send_failure",
            return_value=restored,
        ) as restore:
            response = send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
            )

        self.assertEqual(response.state, "failed")
        restore.assert_called_once()

    def test_ambiguous_send_rejection_keeps_sending_state(self) -> None:
        sending = replace(draft_record(), state="sending", last_client_send_id="send-2")
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record()), patch(
            "app.services.mailbox_drafts.mark_client_draft_sending", return_value=sending
        ), patch(
            "app.services.mailbox_drafts.send_gmail_draft", side_effect=google_http_error(409)
        ), patch("app.services.mailbox_drafts.restore_client_draft_after_definite_send_failure") as restore:
            response = send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
            )

        self.assertEqual(response.state, "sending")
        restore.assert_not_called()

    def test_multiple_sent_matches_remain_ambiguous_without_finalizing(self) -> None:
        sending = replace(draft_record(), state="sending", last_client_send_id="send-2")
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=sending), patch(
            "app.services.mailbox_drafts.find_sent_gmail_message_by_rfc822_message_id",
            side_effect=MultipleSentGmailMessagesFound("duplicate deliveries"),
        ), patch("app.services.mailbox_drafts.mark_client_draft_sent") as mark_sent:
            response = send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
            )

        self.assertEqual(response.state, "sending")
        self.assertIn("Multiple Sent copies", response.error or "")
        mark_sent.assert_not_called()

    def test_sending_retry_recovers_verified_sent_message_without_resending(self) -> None:
        sending = replace(draft_record(), state="sending", last_client_send_id="send-2")
        sent = replace(
            draft_record(state="sent"),
            last_client_send_id="send-2",
            sent_message_id="sent-message-2",
        )
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=sending), patch(
            "app.services.mailbox_drafts.find_sent_gmail_message_by_rfc822_message_id",
            return_value={"id": "sent-message-2", "threadId": "thread-1", "labelIds": ["SENT"]},
        ), patch("app.services.mailbox_drafts.mark_client_draft_sent", return_value=sent), patch(
            "app.services.mailbox_drafts._import_gmail_message", return_value=sample_message()
        ), patch("app.services.mailbox_drafts._remove_local_messages"), patch(
            "app.services.mailbox_drafts._after_draft_change"
        ), patch("app.services.mailbox_drafts.send_gmail_draft") as send:
            response = send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
            )

        self.assertEqual(response.state, "sent")
        self.assertEqual(response.gmail_message_id, "sent-message-2")
        send.assert_not_called()

    def test_sent_draft_rejects_a_different_send_identity(self) -> None:
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record(state="sent")), patch(
            "app.services.mailbox_drafts.send_gmail_draft"
        ) as send:
            with self.assertRaises(MailDraftIdentityConflict):
                send_saved_draft(
                    self.settings,
                    user_id="user-1",
                    gmail_draft_id="draft-1",
                    request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
                )

        send.assert_not_called()

    def test_unmapped_draft_cannot_be_sent(self) -> None:
        with patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True), patch(
            "app.services.mailbox_drafts.client_draft_lock", return_value=nullcontext()
        ), patch("app.services.mailbox_drafts.get_client_draft", return_value=None), patch(
            "app.services.mailbox_drafts.send_gmail_draft"
        ) as send:
            with self.assertRaises(MailDraftIdentityConflict):
                send_saved_draft(
                    self.settings,
                    user_id="user-1",
                    gmail_draft_id="draft-1",
                    request=SimpleNamespace(client_send_id="send-2", client_draft_id="client-draft-1"),
                )

        send.assert_not_called()

    @patch("app.services.mailbox_drafts.fetch_gmail_attachment")
    @patch("app.services.mailbox_drafts.gmail_attachments_for_message")
    @patch("app.services.mailbox_drafts._record_from_payload", return_value=sample_message())
    @patch("app.services.mailbox_drafts.fetch_gmail_draft", return_value={"message": {"id": "message-1"}})
    def test_selective_retained_attachments_only_downloads_requested_ids(
        self,
        _mock_fetch_draft: Mock,
        _mock_record: Mock,
        mock_attachments: Mock,
        mock_fetch_attachment: Mock,
    ) -> None:
        mock_attachments.return_value = [
            SimpleNamespace(filename="keep.txt", mime_type="text/plain", attachment_id="keep-1"),
            SimpleNamespace(filename="remove.txt", mime_type="text/plain", attachment_id="remove-1"),
        ]
        mock_fetch_attachment.return_value = {"data": "aGVsbG8="}

        retained = _preserved_attachment_inputs(
            self.settings,
            user_id="user-1",
            gmail_draft_id="draft-1",
            retained_attachment_ids=["keep-1"],
        )

        self.assertEqual([item["filename"] for item in retained], ["keep.txt"])
        mock_fetch_attachment.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_id="msg-1",
            attachment_id="keep-1",
        )


class GmailDraftRecoveryIntegrationTests(unittest.TestCase):
    def test_sent_recovery_skips_draft_label_and_returns_verified_sent_message(self) -> None:
        service = Mock()
        messages = service.users.return_value.messages.return_value
        messages.list.return_value.execute.return_value = {
            "messages": [{"id": "draft-copy"}, {"id": "sent-copy"}],
        }
        messages.get.return_value.execute.side_effect = [
            {"id": "draft-copy", "threadId": "thread-1", "labelIds": ["DRAFT"]},
            {"id": "sent-copy", "threadId": "thread-1", "labelIds": ["SENT"]},
        ]

        with patch("app.services.integrations.google.create_gmail_service", return_value=service):
            recovered = find_sent_gmail_message_by_rfc822_message_id(
                SimpleNamespace(),
                user_id="user-1",
                rfc822_message_id="<draft.stable@example.test>",
            )

        self.assertEqual(recovered["id"] if recovered else None, "sent-copy")
        self.assertEqual(
            messages.list.call_args.kwargs["q"],
            "in:sent rfc822msgid:<draft.stable@example.test>",
        )

    def test_sent_recovery_rejects_multiple_verified_messages(self) -> None:
        service = Mock()
        messages = service.users.return_value.messages.return_value
        messages.list.return_value.execute.return_value = {
            "messages": [{"id": "sent-copy-1"}, {"id": "sent-copy-2"}],
        }
        messages.get.return_value.execute.side_effect = [
            {"id": "sent-copy-1", "threadId": "thread-1", "labelIds": ["SENT"]},
            {"id": "sent-copy-2", "threadId": "thread-2", "labelIds": ["SENT"]},
        ]

        with patch("app.services.integrations.google.create_gmail_service", return_value=service):
            with self.assertRaises(MultipleSentGmailMessagesFound):
                find_sent_gmail_message_by_rfc822_message_id(
                    SimpleNamespace(),
                    user_id="user-1",
                    rfc822_message_id="<draft.duplicated@example.test>",
                )


class MailboxDraftAndSearchRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("app.api.routes.mailbox.save_draft")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_create_draft_route_allows_empty_recipient_and_body(self, mock_user: Mock, mock_save: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_save.return_value = MailDraftResponse(client_draft_id="draft-client-1", gmail_draft_id="draft-1", state="saved")

        response = self.client.post(
            "/v1/mailbox/drafts",
            json={"client_draft_id": "draft-client-1", "created_at": "2026-07-13T09:00:00+00:00"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["gmail_draft_id"], "draft-1")

    @patch("app.api.routes.mailbox.save_draft", side_effect=MailDraftIdentityConflict("Draft identity mismatch."))
    @patch("app.api.routes.mailbox.require_current_user")
    def test_create_draft_route_returns_conflict_for_identity_mismatch(
        self,
        mock_user: Mock,
        _mock_save: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")

        response = self.client.post(
            "/v1/mailbox/drafts",
            json={"client_draft_id": "draft-client-1", "created_at": "2026-07-13T09:00:00+00:00"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Draft identity mismatch.")

    @patch(
        "app.api.routes.mailbox.delete_draft_by_id",
        side_effect=MailDraftIdentityConflict("This draft is being sent and cannot be deleted."),
    )
    @patch("app.api.routes.mailbox.require_current_user")
    def test_delete_draft_route_returns_conflict_for_inflight_send(
        self,
        mock_user: Mock,
        _mock_delete: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")

        response = self.client.delete("/v1/mailbox/drafts/draft-1")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "This draft is being sent and cannot be deleted.",
        )

    @patch("app.api.routes.mailbox.save_draft")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_create_draft_route_dispatches_optional_response_context(
        self,
        mock_user: Mock,
        mock_save: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_save.return_value = MailDraftResponse(
            client_draft_id="response-draft-1",
            gmail_draft_id="gmail-draft-1",
            state="saved",
        )

        response = self.client.post(
            "/v1/mailbox/drafts",
            json={
                "client_draft_id": "response-draft-1",
                "response_mode": "reply_all",
                "mailbox_thread_id": "group-1",
                "source_message_id": "message-1",
                "body_text": "Reply body",
                "include_quoted_original": False,
                "include_original_attachments": False,
                "created_at": "2026-07-13T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = mock_save.call_args.kwargs["request"]
        self.assertEqual(payload.response_mode, "reply_all")
        self.assertEqual(payload.mailbox_thread_id, "group-1")
        self.assertEqual(payload.source_message_id, "message-1")
        self.assertFalse(payload.include_quoted_original)
        self.assertFalse(payload.include_original_attachments)

    @patch("app.api.routes.mailbox.send_compose")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_compose_rejects_header_injection_before_send(self, mock_user: Mock, mock_send: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        response = self.client.post(
            "/v1/mailbox/compose",
            json={
                "client_send_id": "send-1",
                "to": ["victim@example.com\r\nBcc: attacker@example.com"],
                "subject": "Hello",
                "body_text": "Body",
                "created_at": "2026-07-13T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 400)
        mock_send.assert_not_called()

    @patch("app.api.routes.mailbox.send_compose")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_compose_rejects_invalid_attachment_base64_before_send(self, mock_user: Mock, mock_send: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        response = self.client.post(
            "/v1/mailbox/compose",
            json={
                "client_send_id": "send-1",
                "to": ["recipient@example.com"],
                "subject": "Hello",
                "body_text": "Body",
                "attachments": [{"filename": "safe.txt", "mime_type": "text/plain", "data_base64": "not-base64!"}],
                "created_at": "2026-07-13T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 400)
        mock_send.assert_not_called()

    @patch("app.api.routes.mailbox._validated_attachments", side_effect=ValueError("Attachments exceed the 18 MB send limit."))
    @patch("app.api.routes.mailbox.send_compose")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_compose_returns_413_when_attachment_limit_is_exceeded(
        self,
        mock_user: Mock,
        mock_send: Mock,
        _mock_validate_attachments: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        response = self.client.post(
            "/v1/mailbox/compose",
            json={
                "client_send_id": "send-1",
                "to": ["recipient@example.com"],
                "subject": "Hello",
                "body_text": "Body",
                "attachments": [{"filename": "safe.txt", "mime_type": "text/plain", "data_base64": "aGVsbG8="}],
                "created_at": "2026-07-13T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 413)
        mock_send.assert_not_called()

    @patch("app.api.routes.mailbox.build_mailbox_response")
    @patch("app.api.routes.mailbox.enqueue_mailbox_search_hydration", return_value="search-key")
    @patch("app.services.gmail_importer.hydrate_gmail_search_results")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_search_returns_local_results_without_waiting_for_gmail(
        self,
        mock_user: Mock,
        mock_hydrate: Mock,
        mock_enqueue_hydration: Mock,
        mock_build: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_build.return_value = MailboxResponse(label="all", total_threads=1, unread_threads=1)

        response = self.client.get("/v1/mailbox/search", params={"q": "contract phrase", "label": "all"})

        self.assertEqual(response.status_code, 200)
        mock_hydrate.assert_not_called()
        self.assertEqual(mock_build.call_args.kwargs["search_query"], "contract phrase")
        mock_enqueue_hydration.assert_called_once()
        self.assertEqual(mock_enqueue_hydration.call_args.kwargs["user_id"], "user-1")
        self.assertEqual(mock_enqueue_hydration.call_args.kwargs["query"], "contract phrase")
        self.assertEqual(mock_enqueue_hydration.call_args.kwargs["label"], "all")
        self.assertEqual(mock_enqueue_hydration.call_args.kwargs["limit"], 100)

    @patch("app.api.routes.mailbox.build_mailbox_response")
    @patch("app.api.routes.mailbox.enqueue_mailbox_search_hydration")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_search_refresh_and_pagination_do_not_enqueue_duplicate_hydration(
        self,
        mock_user: Mock,
        mock_enqueue_hydration: Mock,
        mock_build: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_build.return_value = MailboxResponse(label="all", total_threads=0)

        refresh = self.client.get(
            "/v1/mailbox/search",
            params={"q": "invoice", "label": "all", "hydrate": "false"},
        )
        page = self.client.get(
            "/v1/mailbox/search",
            params={"q": "invoice", "label": "all", "cursor": "next-page"},
        )

        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(page.status_code, 200)
        mock_enqueue_hydration.assert_not_called()

    @patch("app.api.routes.mailbox.build_mailbox_response")
    @patch("app.api.routes.mailbox.enqueue_mailbox_search_hydration", side_effect=RuntimeError("queue unavailable"))
    @patch("app.api.routes.mailbox.require_current_user")
    def test_search_still_returns_local_results_when_hydration_queue_is_unavailable(
        self,
        mock_user: Mock,
        _mock_enqueue_hydration: Mock,
        mock_build: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_build.return_value = MailboxResponse(label="all", total_threads=1)

        response = self.client.get("/v1/mailbox/search", params={"q": "invoice", "label": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_threads"], 1)

    def test_search_rejects_unbounded_query(self) -> None:
        response = self.client.get("/v1/mailbox/search", params={"q": "x" * 201})
        self.assertEqual(response.status_code, 422)


class GmailSearchHydrationTests(unittest.TestCase):
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_message_ids")
    @patch("app.services.gmail_importer.list_messages_by_ids", return_value=[])
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_unhydrated_body_match_from_gmail_is_fetched_full_and_indexed(
        self,
        _mock_can_write: Mock,
        _mock_credentials: Mock,
        mock_build: Mock,
        _mock_existing: Mock,
        mock_hydrate: Mock,
        mock_upsert: Mock,
        mock_rebuild: Mock,
    ) -> None:
        execute = Mock(return_value={"messages": [{"id": "body-match-1"}]})
        messages_api = SimpleNamespace(list=Mock(return_value=SimpleNamespace(execute=execute)))
        users_api = SimpleNamespace(messages=Mock(return_value=messages_api))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=users_api))
        message = sample_message()
        mock_hydrate.return_value = ([message], "20")
        settings = SimpleNamespace(database_path="postgresql://example/db")

        count = hydrate_gmail_search_results(
            settings,
            user_id="user-1",
            query="body-only phrase",
            label="all",
            limit=100,
        )

        self.assertEqual(count, 1)
        messages_api.list.assert_called_once()
        self.assertIn("body-only phrase", messages_api.list.call_args.kwargs["q"])
        mock_hydrate.assert_called_once_with(
            settings,
            user_id="user-1",
            message_ids=["body-match-1"],
            format="full",
        )
        mock_upsert.assert_called_once_with(settings.database_path, [message])
        mock_rebuild.assert_called_once_with(
            settings,
            user_id="user-1",
            message_ids=[message.message_id],
            use_ai=False,
        )

    @patch("app.services.gmail_importer._hydrate_message_ids")
    @patch("app.services.gmail_importer._has_body_for_reader", return_value=True)
    @patch("app.services.gmail_importer.list_messages_by_ids")
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_already_hydrated_gmail_matches_are_not_downloaded_again(
        self,
        _mock_can_write: Mock,
        _mock_credentials: Mock,
        mock_build: Mock,
        mock_existing: Mock,
        _mock_has_body: Mock,
        mock_hydrate: Mock,
    ) -> None:
        message = sample_message()
        execute = Mock(return_value={"messages": [{"id": message.message_id}]})
        messages_api = SimpleNamespace(list=Mock(return_value=SimpleNamespace(execute=execute)))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=SimpleNamespace(messages=Mock(return_value=messages_api))))
        mock_existing.return_value = [message]
        settings = SimpleNamespace(database_path="postgresql://example/db")

        count = hydrate_gmail_search_results(settings, user_id="user-1", query="project", label="all", limit=100)

        self.assertEqual(count, 0)
        mock_hydrate.assert_not_called()

    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_message_ids")
    @patch("app.services.gmail_importer._has_body_for_reader", return_value=True)
    @patch("app.services.gmail_importer.list_messages_by_ids")
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_search_hydration_follows_next_page_for_older_body_only_match(
        self,
        _mock_can_write: Mock,
        _mock_credentials: Mock,
        mock_build: Mock,
        mock_existing: Mock,
        _mock_has_body: Mock,
        mock_hydrate: Mock,
        mock_upsert: Mock,
        mock_rebuild: Mock,
    ) -> None:
        recent_ids = [f"recent-{index}" for index in range(100)]
        older_id = "older-body-only-match"
        execute = Mock(
            side_effect=[
                {"messages": [{"id": message_id} for message_id in recent_ids], "nextPageToken": "older-page"},
                {"messages": [{"id": older_id}]},
            ]
        )
        messages_api = SimpleNamespace(list=Mock(return_value=SimpleNamespace(execute=execute)))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=SimpleNamespace(messages=Mock(return_value=messages_api))))

        def existing_messages(*_args, **kwargs):
            message_ids = kwargs["message_ids"]
            if message_ids == recent_ids:
                return [replace(sample_message(), message_id=message_id) for message_id in recent_ids]
            return []

        older_message = replace(sample_message(), message_id=older_id, gmail_thread_id="older-thread")
        mock_existing.side_effect = existing_messages
        mock_hydrate.return_value = ([older_message], "20")
        settings = SimpleNamespace(database_path="postgresql://example/db")

        count = hydrate_gmail_search_results(
            settings,
            user_id="user-1",
            query="older body phrase",
            label="all",
        )

        self.assertEqual(count, 1)
        self.assertEqual(messages_api.list.call_count, 2)
        self.assertNotIn("pageToken", messages_api.list.call_args_list[0].kwargs)
        self.assertEqual(messages_api.list.call_args_list[1].kwargs["pageToken"], "older-page")
        mock_hydrate.assert_called_once_with(
            settings,
            user_id="user-1",
            message_ids=[older_id],
            format="full",
        )
        mock_upsert.assert_called_once_with(settings.database_path, [older_message])
        mock_rebuild.assert_called_once_with(
            settings,
            user_id="user-1",
            message_ids=[older_id],
            use_ai=False,
        )

    @patch("app.services.gmail_importer._hydrate_message_ids")
    @patch("app.services.gmail_importer._has_body_for_reader", return_value=True)
    @patch("app.services.gmail_importer.list_messages_by_ids")
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_search_hydration_stops_at_production_page_cap(
        self,
        _mock_can_write: Mock,
        _mock_credentials: Mock,
        mock_build: Mock,
        mock_existing: Mock,
        _mock_has_body: Mock,
        mock_hydrate: Mock,
    ) -> None:
        responses = [
            {"messages": [{"id": f"message-{index}"}], "nextPageToken": f"page-{index + 1}"}
            for index in range(GMAIL_SEARCH_MAX_PAGES)
        ]
        execute = Mock(side_effect=responses)
        messages_api = SimpleNamespace(list=Mock(return_value=SimpleNamespace(execute=execute)))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=SimpleNamespace(messages=Mock(return_value=messages_api))))
        mock_existing.side_effect = lambda *_args, **kwargs: [
            replace(sample_message(), message_id=message_id)
            for message_id in kwargs["message_ids"]
        ]
        settings = SimpleNamespace(database_path="postgresql://example/db")

        count = hydrate_gmail_search_results(
            settings,
            user_id="user-1",
            query="many matches",
            label="all",
            max_pages=999,
        )

        self.assertEqual(count, 0)
        self.assertEqual(messages_api.list.call_count, GMAIL_SEARCH_MAX_PAGES)
        self.assertEqual(messages_api.list.call_args_list[-1].kwargs["pageToken"], f"page-{GMAIL_SEARCH_MAX_PAGES - 1}")
        mock_hydrate.assert_not_called()

    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_message_ids")
    @patch("app.services.gmail_importer.list_messages_by_ids", return_value=[])
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_search_hydration_persists_completed_page_before_later_page_failure(
        self,
        _mock_can_write: Mock,
        _mock_credentials: Mock,
        mock_build: Mock,
        _mock_existing: Mock,
        mock_hydrate: Mock,
        mock_upsert: Mock,
        mock_rebuild: Mock,
    ) -> None:
        first_message = replace(sample_message(), message_id="first-page-message")
        execute = Mock(
            side_effect=[
                {"messages": [{"id": first_message.message_id}], "nextPageToken": "failing-page"},
                RuntimeError("temporary Gmail failure"),
            ]
        )
        messages_api = SimpleNamespace(list=Mock(return_value=SimpleNamespace(execute=execute)))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=SimpleNamespace(messages=Mock(return_value=messages_api))))
        mock_hydrate.return_value = ([first_message], "20")
        settings = SimpleNamespace(database_path="postgresql://example/db")

        with self.assertRaisesRegex(RuntimeError, "temporary Gmail failure"):
            hydrate_gmail_search_results(
                settings,
                user_id="user-1",
                query="retry safely",
                label="all",
            )

        self.assertEqual(messages_api.list.call_count, 2)
        mock_upsert.assert_called_once_with(settings.database_path, [first_message])
        mock_rebuild.assert_called_once_with(
            settings,
            user_id="user-1",
            message_ids=[first_message.message_id],
            use_ai=False,
        )


class MailboxSearchBackgroundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql://example/db")

    @patch("app.services.mailbox_search.enqueue_job")
    def test_enqueue_is_persistent_deduplicated_and_carries_bounded_work(self, mock_enqueue: Mock) -> None:
        key = enqueue_mailbox_search_hydration(
            self.settings,
            user_id="user-1",
            query="  invoice  ",
            label="ALL",
            limit=999,
        )

        self.assertEqual(key, mailbox_search_key(query="invoice", label="all"))
        self.assertEqual(mock_enqueue.call_args.kwargs["kind"], "gmail_search_hydrate")
        self.assertEqual(mock_enqueue.call_args.kwargs["queue"], "slow")
        self.assertEqual(mock_enqueue.call_args.kwargs["priority"], GMAIL_SEARCH_JOB_PRIORITY)
        self.assertGreater(GMAIL_SEARCH_JOB_PRIORITY, GMAIL_BACKFILL_JOB_PRIORITY)
        self.assertEqual(mock_enqueue.call_args.kwargs["dedupe_key"], f"gmail-search:user-1:{key}")
        self.assertEqual(mock_enqueue.call_args.kwargs["payload"]["limit"], GMAIL_SEARCH_PAGE_SIZE)
        self.assertEqual(mock_enqueue.call_args.kwargs["payload"]["max_pages"], GMAIL_SEARCH_MAX_PAGES)
        self.assertEqual(mock_enqueue.call_args.kwargs["payload"]["response_limit"], 200)
        self.assertEqual(mock_enqueue.call_args.kwargs["payload"]["query"], "invoice")

    @patch("app.services.mailbox_search.emit_mailbox_event")
    @patch("app.services.mailbox_search.hydrate_gmail_search_results", return_value=2)
    def test_completed_hydration_emits_query_scoped_refresh(self, mock_hydrate: Mock, mock_emit: Mock) -> None:
        key = mailbox_search_key(query="invoice", label="sent")

        count = run_mailbox_search_hydration(
            self.settings,
            user_id="user-1",
            query="invoice",
            label="sent",
            limit=100,
            search_key=key,
        )

        self.assertEqual(count, 2)
        mock_hydrate.assert_called_once()
        self.assertEqual(mock_emit.call_args.kwargs["event_type"], "mailbox-search-hydrated")
        self.assertEqual(mock_emit.call_args.kwargs["mailbox_label"], "sent")
        self.assertEqual(mock_emit.call_args.kwargs["payload"]["search_key"], key)

    @patch("app.services.mailbox_search.emit_mailbox_event")
    @patch("app.services.mailbox_search.hydrate_gmail_search_results", return_value=0)
    def test_completed_hydration_refreshes_even_when_matches_are_already_local(self, _mock_hydrate: Mock, mock_emit: Mock) -> None:
        key = mailbox_search_key(query="invoice", label="all")

        count = run_mailbox_search_hydration(
            self.settings,
            user_id="user-1",
            query="invoice",
            label="all",
            limit=100,
            search_key=key,
            max_pages=GMAIL_SEARCH_MAX_PAGES,
        )

        self.assertEqual(count, 0)
        mock_emit.assert_called_once()
        self.assertEqual(mock_emit.call_args.kwargs["payload"]["hydrated_message_count"], 0)

    @patch("app.services.mailbox_search.emit_mailbox_event")
    @patch("app.services.mailbox_search.hydrate_gmail_search_results")
    def test_committed_page_is_announced_before_later_page_failure(self, mock_hydrate: Mock, mock_emit: Mock) -> None:
        key = mailbox_search_key(query="older receipt", label="all")

        def hydrate_with_later_failure(*_args, **kwargs):
            kwargs["on_page_hydrated"](3)
            raise RuntimeError("later Gmail page failed")

        mock_hydrate.side_effect = hydrate_with_later_failure

        with self.assertRaisesRegex(RuntimeError, "later Gmail page failed"):
            run_mailbox_search_hydration(
                self.settings,
                user_id="user-1",
                query="older receipt",
                label="all",
                limit=100,
                search_key=key,
            )

        mock_emit.assert_called_once()
        self.assertEqual(mock_emit.call_args.kwargs["payload"]["search_key"], key)
        self.assertEqual(mock_emit.call_args.kwargs["payload"]["hydrated_message_count"], 3)

    @patch("app.services.mailbox_search.enqueue_job")
    @patch("app.services.mailbox_search.emit_mailbox_event")
    @patch("app.services.gmail_importer._has_body_for_reader", return_value=True)
    @patch("app.services.gmail_importer.list_messages_by_ids")
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_bounded_continuation_eventually_reaches_sixth_gmail_page(
        self,
        _mock_can_write: Mock,
        _mock_credentials: Mock,
        mock_build: Mock,
        mock_existing: Mock,
        _mock_has_body: Mock,
        mock_emit: Mock,
        mock_enqueue: Mock,
    ) -> None:
        responses = [
            {
                "messages": [{"id": f"message-{page}"}],
                "nextPageToken": f"page-token-{page}",
            }
            for page in range(1, GMAIL_SEARCH_MAX_PAGES + 1)
        ]
        responses.append({"messages": [{"id": "message-6"}]})
        execute = Mock(side_effect=responses)
        messages_api = SimpleNamespace(list=Mock(return_value=SimpleNamespace(execute=execute)))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=SimpleNamespace(messages=Mock(return_value=messages_api))))
        mock_existing.side_effect = lambda *_args, **kwargs: [
            replace(sample_message(), message_id=message_id)
            for message_id in kwargs["message_ids"]
        ]
        key = mailbox_search_key(query="older body match", label="all")

        first_count = run_mailbox_search_hydration(
            self.settings,
            user_id="user-1",
            query="older body match",
            label="all",
            limit=GMAIL_SEARCH_PAGE_SIZE,
            search_key=key,
        )

        self.assertEqual(first_count, 0)
        self.assertEqual(messages_api.list.call_count, GMAIL_SEARCH_MAX_PAGES)
        mock_enqueue.assert_called_once()
        continuation_job = mock_enqueue.call_args.kwargs
        continuation_payload = continuation_job["payload"]
        self.assertEqual(continuation_job["queue"], "slow")
        self.assertEqual(continuation_job["priority"], GMAIL_SEARCH_JOB_PRIORITY)
        self.assertIn(":continuation:", continuation_job["dedupe_key"])
        self.assertEqual(continuation_payload["page_token"], f"page-token-{GMAIL_SEARCH_MAX_PAGES}")
        self.assertEqual(continuation_payload["continuation_index"], 1)
        self.assertEqual(len(continuation_payload["continuation_token_hashes"]), 1)

        _run_job(
            self.settings,
            SimpleNamespace(
                payload_version=1,
                kind="gmail_search_hydrate",
                user_id="user-1",
                payload=continuation_payload,
            ),
        )

        self.assertEqual(messages_api.list.call_count, GMAIL_SEARCH_MAX_PAGES + 1)
        self.assertEqual(
            messages_api.list.call_args_list[-1].kwargs["pageToken"],
            f"page-token-{GMAIL_SEARCH_MAX_PAGES}",
        )
        self.assertEqual(mock_enqueue.call_count, 1)
        self.assertEqual(mock_emit.call_count, GMAIL_SEARCH_MAX_PAGES + 1)

    @patch("app.services.mailbox_search.emit_mailbox_event")
    @patch("app.services.mailbox_search.enqueue_job")
    @patch("app.services.mailbox_search.hydrate_gmail_search_results")
    def test_continuation_rejects_tampering_and_cross_job_token_loop(
        self,
        mock_hydrate: Mock,
        mock_enqueue: Mock,
        _mock_emit: Mock,
    ) -> None:
        key = mailbox_search_key(query="loop safely", label="all")

        def produce_continuation(*_args, **kwargs):
            kwargs["on_continuation"]("next-page-token")
            return 0

        mock_hydrate.side_effect = produce_continuation
        run_mailbox_search_hydration(
            self.settings,
            user_id="user-1",
            query="loop safely",
            label="all",
            limit=100,
            search_key=key,
        )
        payload = mock_enqueue.call_args.kwargs["payload"]

        mock_hydrate.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "invalid continuation key"):
            run_mailbox_search_hydration(
                self.settings,
                user_id="user-1",
                query=payload["query"],
                label=payload["label"],
                limit=payload["limit"],
                search_key=payload["search_key"],
                page_token=payload["page_token"],
                continuation_index=payload["continuation_index"],
                continuation_key="0" * 64,
                continuation_token_hashes=payload["continuation_token_hashes"],
            )
        mock_hydrate.assert_not_called()

        with self.assertRaisesRegex(RuntimeError, "invalid continuation token history"):
            run_mailbox_search_hydration(
                self.settings,
                user_id="user-1",
                query=payload["query"],
                label=payload["label"],
                limit=payload["limit"],
                search_key=payload["search_key"],
                page_token=payload["page_token"],
                continuation_index=payload["continuation_index"],
                continuation_key=payload["continuation_key"],
                continuation_token_hashes=[[]],
            )
        mock_hydrate.assert_not_called()

        mock_enqueue.reset_mock()

        def repeat_continuation(*_args, **kwargs):
            kwargs["on_continuation"](payload["page_token"])
            return 0

        mock_hydrate.side_effect = repeat_continuation
        with self.assertRaisesRegex(RuntimeError, "repeated a continuation page token"):
            run_mailbox_search_hydration(
                self.settings,
                user_id="user-1",
                query=payload["query"],
                label=payload["label"],
                limit=payload["limit"],
                search_key=payload["search_key"],
                page_token=payload["page_token"],
                continuation_index=payload["continuation_index"],
                continuation_key=payload["continuation_key"],
                continuation_token_hashes=payload["continuation_token_hashes"],
            )
        mock_enqueue.assert_not_called()

    @patch("app.services.mailbox_search.emit_mailbox_event")
    @patch("app.services.mailbox_search.enqueue_job")
    @patch("app.services.mailbox_search.hydrate_gmail_search_results")
    def test_continuation_chain_stops_at_safety_cap(
        self,
        mock_hydrate: Mock,
        mock_enqueue: Mock,
        _mock_emit: Mock,
    ) -> None:
        key = mailbox_search_key(query="bounded chain", label="all")
        continuation_payload: dict | None = None

        for continuation_index in range(1, GMAIL_SEARCH_MAX_CONTINUATION_JOBS + 1):
            next_token = f"page-token-{continuation_index}"

            def produce_continuation(*_args, _next_token=next_token, **kwargs):
                kwargs["on_continuation"](_next_token)
                return 0

            mock_hydrate.side_effect = produce_continuation
            call_kwargs = {
                "user_id": "user-1",
                "query": "bounded chain",
                "label": "all",
                "limit": GMAIL_SEARCH_PAGE_SIZE,
                "search_key": key,
            }
            if continuation_payload is not None:
                call_kwargs.update(
                    {
                        "response_limit": continuation_payload["response_limit"],
                        "page_token": continuation_payload["page_token"],
                        "continuation_index": continuation_payload["continuation_index"],
                        "continuation_key": continuation_payload["continuation_key"],
                        "continuation_token_hashes": continuation_payload["continuation_token_hashes"],
                    }
                )
            run_mailbox_search_hydration(self.settings, **call_kwargs)
            continuation_payload = mock_enqueue.call_args.kwargs["payload"]
            self.assertEqual(continuation_payload["continuation_index"], continuation_index)
            mock_enqueue.reset_mock()

        if continuation_payload is None:
            self.fail("continuation chain did not produce a capped payload")

        def overflow_continuation(*_args, **kwargs):
            kwargs["on_continuation"]("page-token-overflow")
            return 0

        mock_hydrate.side_effect = overflow_continuation
        with self.assertRaisesRegex(RuntimeError, "exceeded its continuation safety limit"):
            run_mailbox_search_hydration(
                self.settings,
                user_id="user-1",
                query="bounded chain",
                label="all",
                limit=GMAIL_SEARCH_PAGE_SIZE,
                search_key=key,
                response_limit=continuation_payload["response_limit"],
                page_token=continuation_payload["page_token"],
                continuation_index=continuation_payload["continuation_index"],
                continuation_key=continuation_payload["continuation_key"],
                continuation_token_hashes=continuation_payload["continuation_token_hashes"],
            )
        mock_enqueue.assert_not_called()

    @patch("app.workers.main.run_mailbox_search_hydration")
    def test_worker_dispatches_persisted_search_hydration(self, mock_run: Mock) -> None:
        key = mailbox_search_key(query="invoice", label="all")
        job = SimpleNamespace(
            payload_version=1,
            kind="gmail_search_hydrate",
            user_id="user-1",
            payload={
                "user_id": "user-1",
                "query": "invoice",
                "label": "all",
                "limit": 100,
                "search_key": key,
            },
        )

        _run_job(self.settings, job)

        mock_run.assert_called_once_with(
            self.settings,
            user_id="user-1",
            query="invoice",
            label="all",
            limit=100,
            search_key=key,
            max_pages=GMAIL_SEARCH_MAX_PAGES,
            response_limit=200,
            page_token=None,
            continuation_index=0,
            continuation_key=None,
            continuation_token_hashes=None,
        )


if __name__ == "__main__":
    unittest.main()
