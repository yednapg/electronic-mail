from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from fastapi.testclient import TestClient

from app.api.routes import entities as entity_routes
from app.api.routes import tasks as task_routes
from app.db.mail_groups import EntityOutcomeRecord, ManualTaskRecord
from app.main import app
from app.schemas.domain import DashboardProfile, GoogleAuthState
from app.services.mail_groups import build_dashboard_response


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


class DashboardManualTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql://example/db")

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
    ) -> None:
        dashboard = build_dashboard_response(
            self.settings,
            user_id="user-1",
            auth=GoogleAuthState(available=True, connected=True),
            profile=DashboardProfile(email="demo@example.test", display_name="TestUser"),
        )

        self.assertEqual([item.entity_id for item in dashboard.feed.today], ["manual-task:task-1"])
        self.assertEqual(dashboard.feed.today[0].source, "manual")
        self.assertEqual(dashboard.runtime_status["feed_source"], "manual_tasks")

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
    ) -> None:
        dashboard = build_dashboard_response(
            self.settings,
            user_id="user-1",
            auth=GoogleAuthState(available=True, connected=True),
        )

        self.assertEqual(dashboard.feed.today, [])
        self.assertEqual(dashboard.runtime_status["feed_source"], "empty")


class TaskOutcomeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.task_settings_patch = patch.object(task_routes, "settings", SimpleNamespace(database_path="postgresql://example/db"))
        self.entity_settings_patch = patch.object(entity_routes, "settings", SimpleNamespace(database_path="postgresql://example/db"))
        self.task_settings_patch.start()
        self.entity_settings_patch.start()
        self.addCleanup(self.task_settings_patch.stop)
        self.addCleanup(self.entity_settings_patch.stop)

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

        response = self.client.post("/v1/entities/group-1/complete", json={})

        self.assertEqual(response.status_code, 200)
        mock_archive.assert_has_calls([
            call(entity_routes.settings, "thread-1", user_id="user-1"),
            call(entity_routes.settings, "thread-2", user_id="user-1"),
        ])
        mock_append.assert_called_once()
        mock_refresh.assert_called_once()

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
