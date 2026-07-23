from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.schemas.domain import MailDraftSaveRequest
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mailbox_actions import run_pending_thread_action
from app.services.mailbox_drafts import delete_draft_by_id, save_draft, send_saved_draft
from app.services.mailbox_sends import _perform_send
from backend.tests.test_mailbox_drafts_and_search import draft_record
from backend.tests.test_mailbox_sends import pending_send, sample_message
from backend.tests.test_mailbox_thread_actions import pending_action


class _ProviderLockProbe:
    """Deterministic proof that a mocked provider effect occurs in lock scope."""

    def __init__(self) -> None:
        self.active = False
        self.entries = 0

    @contextmanager
    def lock(self, _database_url: str, *, user_id: str):
        if self.active:
            raise AssertionError("provider lock unexpectedly re-entered")
        if user_id != "user-1":
            raise AssertionError(f"unexpected user lock: {user_id}")
        self.entries += 1
        self.active = True
        try:
            yield
        finally:
            self.active = False

    def assert_active(self) -> None:
        if not self.active:
            raise AssertionError("provider effect escaped the shared user-mail lock")


class _DraftLockProbe:
    """Record the exact cross-replica draft identity used by an operation."""

    def __init__(self) -> None:
        self.active = False
        self.keys: list[str] = []
        self.gmail_keys: list[str | None] = []

    @contextmanager
    def lock(
        self,
        _database_url: str,
        *,
        user_id: str,
        client_draft_id: str,
        gmail_draft_id: str | None = None,
    ):
        if self.active:
            raise AssertionError("draft lock unexpectedly re-entered")
        if user_id != "user-1":
            raise AssertionError(f"unexpected draft-lock user: {user_id}")
        self.keys.append(client_draft_id)
        self.gmail_keys.append(gmail_draft_id)
        self.active = True
        try:
            yield
        finally:
            self.active = False

    def assert_active(self) -> None:
        if not self.active:
            raise AssertionError("draft provider effect escaped its serialization lock")


class MailProviderMutationGuardTests(unittest.TestCase):
    settings = SimpleNamespace(
        database_path="postgresql://example/db",
        backend_origin="http://127.0.0.1:3001",
        gmail_pubsub_topic="projects/example/topics/gmail",
        gmail_watch_renewal_hours=24,
    )

    def test_thread_action_provider_effect_and_completion_are_guarded(self) -> None:
        probe = _ProviderLockProbe()
        record = pending_action()
        applied = replace(record, state="applied", applied_at="2026-05-21T09:01:00+00:00")

        def archive(*_args, **_kwargs):
            probe.assert_active()
            return {"id": "thread-1"}

        def mark_applied(*_args, **_kwargs):
            probe.assert_active()
            return applied

        with (
            patch("app.services.mailbox_actions.shared_user_mail_lock", new=probe.lock),
            patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record),
            patch("app.services.mailbox_actions.mark_pending_thread_action_applying"),
            patch(
                "app.services.mailbox_actions._resolve_action_messages",
                return_value=([sample_message()], ["thread-1"]),
            ),
            patch("app.services.mailbox_actions.archive_gmail_thread", side_effect=archive),
            patch("app.services.mailbox_actions.mark_pending_thread_action_applied", side_effect=mark_applied),
            patch("app.services.mailbox_actions.enqueue_projection_refresh"),
            patch("app.services.mailbox_actions.emit_mailbox_event"),
        ):
            response = run_pending_thread_action(
                self.settings,
                user_id="user-1",
                server_action_id=record.server_action_id,
            )

        self.assertEqual(response.state, "applied")
        self.assertEqual(probe.entries, 1)
        self.assertFalse(probe.active)

    def test_raw_send_provider_effect_and_durable_finalize_are_guarded(self) -> None:
        probe = _ProviderLockProbe()
        queued = pending_send()
        sent = replace(
            queued,
            state="sent",
            gmail_message_id="gmail-message-1",
            gmail_thread_id="gmail-thread-1",
        )

        def deliver(*_args, **_kwargs):
            probe.assert_active()
            return {"id": "gmail-message-1", "threadId": "gmail-thread-1"}

        def finalize(*_args, **_kwargs):
            probe.assert_active()
            return sent

        with (
            patch("app.services.mailbox_sends.shared_user_mail_lock", new=probe.lock),
            patch("app.services.mailbox_sends.get_pending_send", return_value=queued),
            patch("app.services.mailbox_sends.claim_pending_send", return_value=queued),
            patch("app.services.mailbox_sends._enqueue_send_job"),
            patch("app.services.mailbox_sends._raw_message", return_value="encoded-message"),
            patch("app.services.mailbox_sends.send_gmail_raw_message", side_effect=deliver),
            patch("app.services.mailbox_sends._finalize_sent_message", side_effect=finalize),
        ):
            result = _perform_send(self.settings, user_id="user-1", record=queued)

        self.assertEqual(result.state, "sent")
        self.assertEqual(probe.entries, 1)
        self.assertFalse(probe.active)

    def test_draft_create_delete_and_send_provider_effects_are_guarded(self) -> None:
        operations = (self._save_draft, self._delete_draft, self._send_draft)
        for operation in operations:
            with self.subTest(operation=operation.__name__):
                probe = _ProviderLockProbe()
                operation(probe)
                self.assertEqual(probe.entries, 1)
                self.assertFalse(probe.active)

    def test_draft_delete_and_save_share_the_serialization_identity(self) -> None:
        save_key = self._save_draft(_ProviderLockProbe())
        delete_key = self._delete_draft(_ProviderLockProbe())

        self.assertEqual((save_key, delete_key), ("client-draft-1", "client-draft-1"))

    def test_draft_delete_and_send_share_the_serialization_identity(self) -> None:
        send_key = self._send_draft(_ProviderLockProbe())
        delete_key = self._delete_draft(_ProviderLockProbe())

        self.assertEqual((send_key, delete_key), ("client-draft-1", "client-draft-1"))

    def test_adopt_update_delete_and_send_share_provider_draft_identity(self) -> None:
        provider_ids: list[str | None] = []

        @contextmanager
        def record_lock(
            _database_url: str,
            *,
            user_id: str,
            client_draft_id: str,
            gmail_draft_id: str | None = None,
        ):
            self.assertEqual(user_id, "user-1")
            provider_ids.append(gmail_draft_id)
            yield

        adoption = MailDraftSaveRequest(
            client_draft_id="adopted-client-id",
            gmail_draft_id="draft-1",
            subject="Adopted",
            body_text="Draft body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )
        with (
            patch("app.services.mailbox_drafts.shared_user_mail_lock", return_value=nullcontext()),
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", new=record_lock),
            patch("app.services.mailbox_drafts.get_client_draft", return_value=None),
            patch("app.services.mailbox_drafts._gmail_draft_message", return_value=sample_message()),
            patch(
                "app.services.mailbox_drafts.update_gmail_draft",
                return_value={"id": "draft-1", "message": {"id": "message-1", "threadId": "thread-1"}},
            ),
            patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message()),
            patch("app.services.mailbox_drafts.upsert_client_draft", return_value=draft_record()),
            patch("app.services.mailbox_drafts._after_draft_change"),
        ):
            save_draft(self.settings, user_id="user-1", request=adoption)

        with (
            patch("app.services.mailbox_drafts.shared_user_mail_lock", return_value=nullcontext()),
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", new=record_lock),
            patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record()),
            patch("app.services.mailbox_drafts._delete_draft_by_id_locked", return_value=True),
        ):
            delete_draft_by_id(self.settings, user_id="user-1", gmail_draft_id="draft-1")

        with (
            patch("app.services.mailbox_drafts.shared_user_mail_lock", return_value=nullcontext()),
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", new=record_lock),
            patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record(state="sent")),
        ):
            send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(client_send_id="send-1", client_draft_id="client-draft-1"),
            )

        self.assertEqual(provider_ids, ["draft-1", "draft-1", "draft-1"])

    def test_unmapped_draft_delete_uses_deterministic_gmail_identity(self) -> None:
        draft_probe = _DraftLockProbe()
        mapping_reads = 0

        def get_mapping(*_args, **_kwargs):
            nonlocal mapping_reads
            mapping_reads += 1
            return None

        with (
            patch("app.services.mailbox_drafts.shared_user_mail_lock", return_value=nullcontext()),
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.get_client_draft", side_effect=get_mapping),
            patch("app.services.mailbox_drafts.client_draft_lock", new=draft_probe.lock),
            patch("app.services.mailbox_drafts._delete_draft_by_id_locked", return_value=True) as delete_locked,
        ):
            deleted = delete_draft_by_id(
                self.settings,
                user_id="user-1",
                gmail_draft_id="provider-draft-1",
            )

        self.assertTrue(deleted)
        self.assertEqual(draft_probe.keys, ["gmail:provider-draft-1"])
        self.assertEqual(draft_probe.gmail_keys, ["provider-draft-1"])
        self.assertEqual(mapping_reads, 2)
        self.assertIsNone(delete_locked.call_args.kwargs["mapping"])

    def test_delete_rereads_mapping_after_provider_lock_wait(self) -> None:
        draft_probe = _DraftLockProbe()
        mappings = iter([None, draft_record()])

        with (
            patch("app.services.mailbox_drafts.shared_user_mail_lock", return_value=nullcontext()),
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.get_client_draft", side_effect=lambda *_args, **_kwargs: next(mappings)),
            patch("app.services.mailbox_drafts.client_draft_lock", new=draft_probe.lock),
            patch("app.services.mailbox_drafts._delete_draft_by_id_locked", return_value=True) as delete_locked,
        ):
            deleted = delete_draft_by_id(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
            )

        self.assertTrue(deleted)
        self.assertEqual(draft_probe.keys, ["gmail:draft-1"])
        self.assertEqual(delete_locked.call_args.kwargs["mapping"], draft_record())

    def test_watch_start_state_write_and_renewal_enqueue_are_guarded(self) -> None:
        probe = _ProviderLockProbe()

        def start_watch(*_args, **_kwargs):
            probe.assert_active()
            return {"historyId": "456", "expiration": "1770000000000"}

        def mark_started(*_args, **_kwargs):
            probe.assert_active()

        def enqueue(*_args, **_kwargs):
            probe.assert_active()

        with (
            patch("app.services.gmail_watch.shared_user_mail_lock", new=probe.lock),
            patch("app.services.gmail_watch.get_import_state", return_value=None),
            patch("app.services.gmail_watch.start_gmail_watch", side_effect=start_watch),
            patch("app.services.gmail_watch.mark_gmail_watch_started", side_effect=mark_started),
            patch("app.services.gmail_watch.enqueue_job", side_effect=enqueue),
        ):
            result = ensure_gmail_watch(self.settings, user_id="user-1")

        self.assertEqual(result.status, "started")
        self.assertEqual(probe.entries, 1)
        self.assertFalse(probe.active)

    def _save_draft(self, probe: _ProviderLockProbe) -> str:
        draft_probe = _DraftLockProbe()
        request = MailDraftSaveRequest(
            client_draft_id="client-draft-1",
            to=["recipient@example.com"],
            subject="Launch",
            body_text="Draft body",
            attachments=[],
            created_at="2026-07-13T09:00:00+00:00",
        )

        def create(*_args, **_kwargs):
            probe.assert_active()
            draft_probe.assert_active()
            return {
                "id": "draft-1",
                "message": {"id": "message-1", "threadId": "thread-1"},
            }

        with (
            patch("app.services.mailbox_drafts.shared_user_mail_lock", new=probe.lock),
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", new=draft_probe.lock),
            patch("app.services.mailbox_drafts.get_client_draft", return_value=None),
            patch("app.services.mailbox_drafts.find_gmail_draft_by_rfc822_message_id", return_value=None),
            patch("app.services.mailbox_drafts.create_gmail_draft", side_effect=create),
            patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message()),
            patch("app.services.mailbox_drafts.upsert_client_draft", return_value=draft_record()),
            patch("app.services.mailbox_drafts._after_draft_change"),
        ):
            response = save_draft(self.settings, user_id="user-1", request=request)
        self.assertEqual(response.state, "saved")
        self.assertEqual(len(draft_probe.keys), 1)
        return draft_probe.keys[0]

    def _delete_draft(self, probe: _ProviderLockProbe) -> str:
        draft_probe = _DraftLockProbe()
        mapping_reads = 0

        def get_mapping(*_args, **_kwargs):
            nonlocal mapping_reads
            mapping_reads += 1
            return draft_record()

        def delete(*_args, **_kwargs):
            probe.assert_active()
            draft_probe.assert_active()

        with (
            patch("app.services.mailbox_drafts.shared_user_mail_lock", new=probe.lock),
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.get_client_draft", side_effect=get_mapping),
            patch("app.services.mailbox_drafts.client_draft_lock", new=draft_probe.lock),
            patch("app.services.mailbox_drafts.delete_gmail_draft", side_effect=delete),
            patch("app.services.mailbox_drafts._remove_local_messages"),
            patch("app.services.mailbox_drafts.mark_client_draft_deleted"),
            patch("app.services.mailbox_drafts._after_draft_change"),
        ):
            deleted = delete_draft_by_id(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
            )
        self.assertTrue(deleted)
        self.assertEqual(mapping_reads, 2)
        self.assertEqual(len(draft_probe.keys), 1)
        return draft_probe.keys[0]

    def _send_draft(self, probe: _ProviderLockProbe) -> str:
        draft_probe = _DraftLockProbe()

        def send(*_args, **_kwargs):
            probe.assert_active()
            draft_probe.assert_active()
            return {"id": "sent-message-1", "threadId": "thread-1"}

        with (
            patch("app.services.mailbox_drafts.shared_user_mail_lock", new=probe.lock),
            patch("app.services.mailbox_drafts._can_manage_drafts", return_value=True),
            patch("app.services.mailbox_drafts.client_draft_lock", new=draft_probe.lock),
            patch("app.services.mailbox_drafts.get_client_draft", return_value=draft_record()),
            patch(
                "app.services.mailbox_drafts.mark_client_draft_sending",
                return_value=replace(draft_record(), state="sending", last_client_send_id="send-2"),
            ),
            patch("app.services.mailbox_drafts.send_gmail_draft", side_effect=send),
            patch("app.services.mailbox_drafts._import_gmail_message", return_value=sample_message()),
            patch("app.services.mailbox_drafts._remove_local_messages"),
            patch(
                "app.services.mailbox_drafts.mark_client_draft_sent",
                return_value=replace(
                    draft_record(state="sent"),
                    last_client_send_id="send-2",
                    sent_message_id="sent-message-1",
                ),
            ),
            patch("app.services.mailbox_drafts._after_draft_change"),
        ):
            response = send_saved_draft(
                self.settings,
                user_id="user-1",
                gmail_draft_id="draft-1",
                request=SimpleNamespace(
                    client_send_id="send-2",
                    client_draft_id="client-draft-1",
                ),
            )
        self.assertEqual(response.state, "sent")
        self.assertEqual(len(draft_probe.keys), 1)
        return draft_probe.keys[0]


if __name__ == "__main__":
    unittest.main()
