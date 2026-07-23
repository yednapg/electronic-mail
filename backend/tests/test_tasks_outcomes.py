from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from fastapi.testclient import TestClient

from app.api.routes import entities as entity_routes
from app.api.routes import tasks as task_routes
from app.db.mail_groups import EntityOutcomeRecord, GmailMessageRecord, MailGroupRecord, ManualTaskRecord
from app.main import create_app, settings
from app.schemas.domain import DashboardProfile, GoogleAuthState
from app.services.mail_groups import build_dashboard_response


def queue_health() -> SimpleNamespace:
    return SimpleNamespace(
        queue_depth={},
        worker_online=False,
        required_queues_ready=False,
    )


def sample_task(status: str = "open") -> ManualTaskRecord:
    return ManualTaskRecord(
        id="task-1",
        user_id="user-1",
        entity_id="manual-task:task-1",
        title="New to-do",
        notes="Notes",
        section="today",
        due_at=None,
        status=status,
        created_at="2026-05-16T09:30:00+05:30",
        updated_at="2026-05-16T09:30:00+05:30",
    )


def sample_outcome(entity_id: str = "manual-task:task-1") -> EntityOutcomeRecord:
    return EntityOutcomeRecord(
        id="outcome-1",
        user_id="user-1",
        entity_id=entity_id,
        outcome_type="complete",
        snooze_until=None,
        note="Done",
        created_at="2026-05-16T09:31:00+05:30",
    )


def sample_group(
    *,
    id: str,
    title: str,
    summary: str,
    latest_message_at: str,
    priority: int = 20,
    action_needed: bool = False,
    action_type: str = "none",
    timing_band: str = "later",
    group_type: str = "financial_transfer",
) -> MailGroupRecord:
    return MailGroupRecord(
        id=id,
        user_id="user-1",
        group_key=f"gmail-thread:{id}",
        group_type=group_type,
        status="active",
        enrichment_status="ready",
        membership_source="gmail_thread",
        ai_model="test",
        ai_error=None,
        ai_generated_at=latest_message_at,
        ai_title=title,
        ai_summary=summary,
        labels=["INBOX"],
        action_needed=action_needed,
        action_type=action_type,
        priority=priority,
        timing_band=timing_band,
        dashboard_visible=True,
        latest_message_at=latest_message_at,
        latest_message_id=f"msg-{id}",
        generated_from_hash=f"hash-{id}",
        generated_at=latest_message_at,
        created_at=latest_message_at,
        updated_at=latest_message_at,
    )


def sample_message(
    *,
    id: str,
    thread_id: str,
    subject: str,
    sender: str,
    snippet: str,
    internal_date: str,
) -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id=id,
        gmail_thread_id=thread_id,
        history_id="1",
        label_ids=["INBOX"],
        internal_date=internal_date,
        subject=subject,
        sender=sender,
        recipients={},
        headers={},
        snippet=snippet,
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body=snippet,
        extracted_signals={"sender_domain": sender.split("@")[-1].strip(">")},
        body_hash=f"body-{id}",
        created_at=internal_date,
        updated_at=internal_date,
    )


class DashboardManualTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql://example/db")

    @patch("app.services.mail_groups.get_queue_health", return_value=queue_health())
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 0, "pending": 0})
    @patch("app.services.mail_groups.list_messages_for_groups", return_value={})
    @patch("app.services.mail_groups.get_latest_entity_outcomes", return_value={})
    @patch("app.services.mail_groups.list_open_manual_tasks", return_value=[sample_task()])
    @patch("app.services.mail_groups.list_dashboard_mail_groups", return_value=[])
    def test_dashboard_merges_open_manual_tasks(
        self,
        _mock_groups: Mock,
        _mock_tasks: Mock,
        _mock_outcomes: Mock,
        _mock_messages: Mock,
        _mock_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        dashboard = build_dashboard_response(
            self.settings,
            user_id="user-1",
            auth=GoogleAuthState(available=True, connected=True),
            profile=DashboardProfile(email="gaurav@example.com", display_name="Gaurav"),
        )

        self.assertEqual([item.entity_id for item in dashboard.feed.today], ["manual-task:task-1"])
        self.assertEqual(dashboard.feed.today[0].source, "manual")
        self.assertEqual(dashboard.runtime_status["feed_source"], "manual_tasks")

    @patch("app.services.mail_groups.get_queue_health", return_value=queue_health())
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 0, "pending": 0})
    @patch("app.services.mail_groups.list_messages_for_groups", return_value={})
    @patch("app.services.mail_groups.get_latest_entity_outcomes", return_value={"manual-task:task-1": sample_outcome()})
    @patch("app.services.mail_groups.list_open_manual_tasks", return_value=[sample_task()])
    @patch("app.services.mail_groups.list_dashboard_mail_groups", return_value=[])
    def test_dashboard_excludes_completed_manual_tasks(
        self,
        _mock_groups: Mock,
        _mock_tasks: Mock,
        _mock_outcomes: Mock,
        _mock_messages: Mock,
        _mock_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        dashboard = build_dashboard_response(
            self.settings,
            user_id="user-1",
            auth=GoogleAuthState(available=True, connected=True),
        )

        self.assertEqual(dashboard.feed.today, [])
        self.assertEqual(dashboard.runtime_status["feed_source"], "empty")

    @patch("app.services.mail_groups.get_queue_health", return_value=queue_health())
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 4, "pending": 0})
    @patch("app.services.mail_groups.get_latest_entity_outcomes", return_value={})
    @patch("app.services.mail_groups.list_open_manual_tasks", return_value=[])
    @patch("app.services.mail_groups.list_dashboard_mail_groups")
    def test_dashboard_suppresses_fx_transfer_items_after_processed_notice(
        self,
        mock_groups: Mock,
        _mock_tasks: Mock,
        _mock_outcomes: Mock,
        _mock_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        deal = sample_group(
            id="deal",
            title="HDFC FX Retail Deal Confirmation",
            summary="HDFC confirms receipt of an FX booking for USD 100. The user must place a remittance request in NetBanking.",
            latest_message_at="2026-06-02T13:59:27+05:30",
            priority=80,
            action_needed=True,
            action_type="review",
            timing_band="now",
        )
        acknowledgement = sample_group(
            id="ack",
            title="HDFC Bank acknowledged your wire transfer inquiry",
            summary="HDFC auto-acknowledged receipt of your email about the international wire transfer status.",
            latest_message_at="2026-06-03T15:47:41+05:30",
            action_type="track",
            timing_band="later",
        )
        processed = sample_group(
            id="processed",
            title="Outward remittance processed",
            summary="HDFC Bank confirms an outward remittance was processed and includes the SWIFT payment message.",
            latest_message_at="2026-06-03T22:16:29+05:30",
            timing_band="hidden",
            group_type="banking_transaction_notice",
        )
        follow_up = sample_group(
            id="follow-up",
            title="HDFC Bank update on your international wire inquiry",
            summary="HDFC assigned reference number 106400420 and said they will respond by June 10, 2026.",
            latest_message_at="2026-06-04T22:09:14+05:30",
            action_type="none",
            timing_band="later",
            group_type="support_thread",
        )
        support_case = sample_group(
            id="support-case",
            title="HDFC Bank remittance support case",
            summary="HDFC Bank has registered your outward remittance support query as case 106400420 and says their team is working on it.",
            latest_message_at="2026-06-04T22:08:40+05:30",
            action_type="none",
            timing_band="later",
            group_type="support_case",
        )
        mock_groups.return_value = [deal, acknowledgement, processed, follow_up, support_case]
        messages = {
            deal.id: [
                sample_message(
                    id="msg-deal",
                    thread_id="thread-deal",
                    subject="FX Retail Deal Confirmation - CCIL Reference Number 202606029000072",
                    sender="cputrade.swiftmanagement@hdfcbank.bank.in",
                    snippet="FX Retail Trade No:202606029000072",
                    internal_date=deal.latest_message_at or "",
                )
            ],
            acknowledgement.id: [
                sample_message(
                    id="msg-ack",
                    thread_id="thread-ack",
                    subject="<Auto> Fwd: Request for Status of International Wire Sent 24 Hours Ago",
                    sender="grievance.redressal@hdfc.bank.in",
                    snippet="Acknowledged receipt of your international wire transfer status inquiry.",
                    internal_date=acknowledgement.latest_message_at or "",
                )
            ],
            processed.id: [
                sample_message(
                    id="msg-processed",
                    thread_id="thread-processed",
                    subject="Transaction advices for your company GAURAV PANDEY",
                    sender="TradeQualityUnit@hdfcbank.bank.in",
                    snippet="We have processed your Outward remittance. SWIFT payment message is appended.",
                    internal_date=processed.latest_message_at or "",
                )
            ],
            follow_up.id: [
                sample_message(
                    id="msg-follow-up",
                    thread_id="thread-follow-up",
                    subject="Re: Request for Status of International Wire Sent 24 Hours Ago",
                    sender="support@hdfc.bank.in",
                    snippet="Reference number 106400420. We will respond by June 10, 2026.",
                    internal_date=follow_up.latest_message_at or "",
                )
            ],
            support_case.id: [
                sample_message(
                    id="msg-support-case",
                    thread_id="thread-support-case",
                    subject="[Registered] - Service Request 106400420",
                    sender="care@hdfcbank.bank.in",
                    snippet="Your query/concern outward remittance support has been registered under Case Reference No.106400420.",
                    internal_date=support_case.latest_message_at or "",
                )
            ],
        }

        with patch("app.services.mail_groups.list_messages_for_groups", return_value=messages):
            dashboard = build_dashboard_response(
                self.settings,
                user_id="user-1",
                auth=GoogleAuthState(available=True, connected=True),
            )

        visible_titles = [item.title for item in [*dashboard.feed.now, *dashboard.feed.today, *dashboard.feed.worth_knowing]]
        self.assertNotIn("HDFC FX Retail Deal Confirmation", visible_titles)
        self.assertNotIn("HDFC Bank acknowledged your wire transfer inquiry", visible_titles)
        self.assertNotIn("HDFC Bank update on your international wire inquiry", visible_titles)
        self.assertNotIn("HDFC Bank remittance support case", visible_titles)
        self.assertNotIn("Outward remittance processed", visible_titles)

    @patch("app.services.mail_groups.get_queue_health", return_value=queue_health())
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0})
    @patch("app.services.mail_groups.get_latest_entity_outcomes", return_value={})
    @patch("app.services.mail_groups.list_open_manual_tasks", return_value=[])
    @patch("app.services.mail_groups.list_dashboard_mail_groups")
    def test_dashboard_ignores_trash_only_mail_groups(
        self,
        mock_groups: Mock,
        _mock_tasks: Mock,
        _mock_outcomes: Mock,
        _mock_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        trash_group = sample_group(
            id="trash-devin",
            title="Devin email verification",
            summary="Verify the Devin email address.",
            latest_message_at="2026-06-04T12:00:00+00:00",
            priority=80,
            action_needed=True,
            action_type="review",
            timing_band="now",
            group_type="account_security",
        )
        trash_message = replace(
            sample_message(
                id="msg-trash-devin",
                thread_id="thread-trash-devin",
                subject="Devin email verification",
                sender="Devin <verify@devin.ai>",
                snippet="Verify your email address.",
                internal_date=trash_group.latest_message_at or "",
            ),
            label_ids=["TRASH", "CATEGORY_UPDATES"],
        )
        mock_groups.return_value = [trash_group]

        with patch("app.services.mail_groups.list_messages_for_groups", return_value={trash_group.id: [trash_message]}):
            dashboard = build_dashboard_response(
                self.settings,
                user_id="user-1",
                auth=GoogleAuthState(available=True, connected=True),
            )

        self.assertEqual(dashboard.feed.now, [])
        self.assertEqual(dashboard.feed.today, [])
        self.assertEqual(dashboard.feed.worth_knowing, [])
        self.assertEqual(dashboard.runtime_status["feed_source"], "empty")


class TaskOutcomeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            create_app(replace(settings, app_env="local", rate_limit_enabled=False))
        )
        self.task_settings_patch = patch.object(task_routes, "settings", SimpleNamespace(database_path="postgresql://example/db"))
        self.entity_settings_patch = patch.object(entity_routes, "settings", SimpleNamespace(database_path="postgresql://example/db"))
        self.entity_lock_patch = patch.object(
            entity_routes,
            "shared_user_mail_lock",
            return_value=nullcontext(),
        )
        self.task_settings_patch.start()
        self.entity_settings_patch.start()
        self.mock_entity_lock = self.entity_lock_patch.start()
        self.addCleanup(self.task_settings_patch.stop)
        self.addCleanup(self.entity_settings_patch.stop)
        self.addCleanup(self.entity_lock_patch.stop)

    @patch("app.api.routes.tasks.refresh_app_session_snapshot")
    @patch("app.api.routes.tasks.create_manual_task", return_value=sample_task())
    @patch("app.api.routes.tasks.require_current_user")
    def test_create_task_returns_task_and_refreshes_snapshot(
        self,
        mock_user: Mock,
        mock_create: Mock,
        mock_refresh: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")

        response = self.client.post("/v1/tasks", json={"title": " New to-do ", "notes": " Notes ", "section": "today"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entity_id"], "manual-task:task-1")
        mock_create.assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            title="New to-do",
            notes="Notes",
            section="today",
            due_at=None,
        )
        mock_refresh.assert_called_once()

    @patch("app.api.routes.entities.refresh_app_session_snapshot")
    @patch("app.api.routes.entities.append_entity_outcome", return_value=sample_outcome())
    @patch("app.api.routes.entities.update_manual_task", return_value=sample_task(status="done"))
    @patch("app.api.routes.entities.get_manual_task_by_entity_id", return_value=sample_task())
    @patch("app.api.routes.entities.require_current_user")
    def test_manual_completion_marks_task_done_and_writes_outcome(
        self,
        mock_user: Mock,
        _mock_get_task: Mock,
        mock_update: Mock,
        mock_append: Mock,
        mock_refresh: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")

        response = self.client.post("/v1/entities/manual-task:task-1/complete", json={"note": "Done"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcome_type"], "complete")
        mock_update.assert_called_once_with("postgresql://example/db", "task-1", user_id="user-1", status="done")
        mock_append.assert_called_once()
        mock_refresh.assert_called_once()

    @patch("app.api.routes.entities.refresh_app_session_snapshot")
    @patch("app.api.routes.entities.append_entity_outcome", return_value=sample_outcome("group-1"))
    @patch("app.api.routes.entities.archive_gmail_thread_service")
    @patch("app.api.routes.entities.get_mail_group_detail")
    @patch("app.api.routes.entities.get_manual_task_by_entity_id", return_value=None)
    @patch("app.api.routes.entities.require_current_user")
    def test_gmail_completion_archives_distinct_threads_then_writes_outcome(
        self,
        mock_user: Mock,
        _mock_manual: Mock,
        mock_detail: Mock,
        mock_archive: Mock,
        mock_append: Mock,
        mock_refresh: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_detail.return_value = SimpleNamespace(
            messages=[
                SimpleNamespace(gmail_thread_id="thread-2"),
                SimpleNamespace(gmail_thread_id="thread-1"),
                SimpleNamespace(gmail_thread_id="thread-2"),
            ]
        )
        guard_state = {"active": False}

        @contextmanager
        def recording_lock(*_args, **_kwargs):
            guard_state["active"] = True
            try:
                yield
            finally:
                guard_state["active"] = False

        def assert_guarded(*_args, **_kwargs):
            self.assertTrue(guard_state["active"])

        self.mock_entity_lock.side_effect = recording_lock
        mock_archive.side_effect = assert_guarded
        mock_append.side_effect = lambda *_args, **_kwargs: (
            assert_guarded(),
            sample_outcome("group-1"),
        )[1]
        mock_refresh.side_effect = assert_guarded

        response = self.client.post("/v1/entities/group-1/complete", json={})

        self.assertEqual(response.status_code, 200)
        mock_archive.assert_has_calls([
            call(entity_routes.settings, "thread-1", user_id="user-1"),
            call(entity_routes.settings, "thread-2", user_id="user-1"),
        ])
        mock_append.assert_called_once()
        mock_refresh.assert_called_once()
        self.assertFalse(guard_state["active"])

    @patch("app.api.routes.entities.refresh_app_session_snapshot")
    @patch("app.api.routes.entities.append_entity_outcome")
    @patch("app.api.routes.entities.archive_gmail_thread_service", side_effect=RuntimeError("Gmail archive failed"))
    @patch("app.api.routes.entities.get_mail_group_detail")
    @patch("app.api.routes.entities.get_manual_task_by_entity_id", return_value=None)
    @patch("app.api.routes.entities.require_current_user")
    def test_gmail_archive_failure_does_not_write_completion(
        self,
        mock_user: Mock,
        _mock_manual: Mock,
        mock_detail: Mock,
        _mock_archive: Mock,
        mock_append: Mock,
        mock_refresh: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_detail.return_value = SimpleNamespace(messages=[SimpleNamespace(gmail_thread_id="thread-1")])

        response = self.client.post("/v1/entities/group-1/complete", json={})

        self.assertEqual(response.status_code, 400)
        mock_append.assert_not_called()
        mock_refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
