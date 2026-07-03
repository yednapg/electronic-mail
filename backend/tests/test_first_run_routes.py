from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import first_run as first_run_routes
from app.schemas.domain import FirstRunImportJobResponse


class FirstRunRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(first_run_routes.router)
        self.client = TestClient(app)

    def test_first_run_latest_is_ready_when_hot_window_ready_before_dashboard(self) -> None:
        state = _import_state(first_dashboard_ready_at=None, hot_window_completed_at="2026-06-20T10:00:45+00:00")
        with (
            patch.object(first_run_routes, "settings", SimpleNamespace(database_path="postgresql://example/db")),
            patch.object(first_run_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(first_run_routes, "get_import_state", return_value=state),
            patch.object(first_run_routes, "get_job", return_value=None),
            patch.object(first_run_routes, "get_app_session_snapshot", return_value=None),
            patch.object(first_run_routes, "hot_window_product_ready", return_value=True),
            patch.object(first_run_routes, "_first_run_counts", return_value=_count_payload()),
        ):
            response = self.client.get("/v1/first-run/import-jobs/latest")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["stage"], "ready")
        self.assertEqual(payload["quality_status"], "ready")
        self.assertIsNone(payload["dashboard_ready_at"])
        self.assertEqual(payload["hot_window_ready_at"], "2026-06-20T10:00:45+00:00")
        self.assertEqual(payload["fetched_count"], 127)
        self.assertEqual(payload["thread_count"], 42)
        self.assertEqual(payload["dashboard_item_count"], 3)

    def test_first_run_latest_waits_when_groups_ready_but_hot_window_is_not_ready(self) -> None:
        state = _import_state(first_dashboard_ready_at=None, hot_window_completed_at=None)
        with (
            patch.object(first_run_routes, "settings", SimpleNamespace(database_path="postgresql://example/db")),
            patch.object(first_run_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(first_run_routes, "get_import_state", return_value=state),
            patch.object(first_run_routes, "get_job", return_value=None),
            patch.object(first_run_routes, "_first_run_counts", return_value=_count_payload()),
        ):
            response = self.client.get("/v1/first-run/import-jobs/latest")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["stage"], "preparing_inbox")
        self.assertEqual(payload["quality_status"], "pending")
        self.assertIsNone(payload["hot_window_ready_at"])

    def test_first_run_latest_waits_when_hot_window_imported_but_titles_are_pending(self) -> None:
        state = _import_state(first_dashboard_ready_at=None, hot_window_completed_at="2026-06-20T10:00:45+00:00")
        with (
            patch.object(first_run_routes, "settings", SimpleNamespace(database_path="postgresql://example/db")),
            patch.object(first_run_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(first_run_routes, "get_import_state", return_value=state),
            patch.object(first_run_routes, "get_job", return_value=None),
            patch.object(first_run_routes, "get_app_session_snapshot", return_value=None),
            patch.object(first_run_routes, "hot_window_product_ready", return_value=False),
            patch.object(first_run_routes, "_first_run_counts", return_value=_count_payload()),
        ):
            response = self.client.get("/v1/first-run/import-jobs/latest")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["stage"], "preparing_inbox")
        self.assertEqual(payload["quality_status"], "pending")
        self.assertIsNone(payload["hot_window_ready_at"])

    def test_first_run_create_wakes_background_work_when_hot_window_is_not_ready(self) -> None:
        state = _import_state(first_dashboard_ready_at=None, hot_window_completed_at=None)
        ensure = Mock()
        enqueue = Mock(return_value="job-new")
        with (
            patch.object(first_run_routes, "settings", SimpleNamespace(database_path="postgresql://example/db")),
            patch.object(first_run_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(first_run_routes, "get_import_state", return_value=state),
            patch.object(first_run_routes, "get_job", return_value=None),
            patch.object(first_run_routes, "enqueue_first_run", enqueue),
            patch.object(first_run_routes, "ensure_background_import_work", ensure),
            patch.object(first_run_routes, "_first_run_counts", return_value=_count_payload()),
        ):
            response = self.client.post("/v1/first-run/import-jobs")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["stage"], "preparing_inbox")
        enqueue.assert_not_called()
        ensure.assert_called_once()

    def test_first_run_create_does_not_enqueue_when_hot_window_ready_before_dashboard(self) -> None:
        state = _import_state(first_dashboard_ready_at=None, hot_window_completed_at="2026-06-20T10:00:45+00:00")
        enqueue = Mock(return_value="job-new")
        with (
            patch.object(first_run_routes, "settings", SimpleNamespace(database_path="postgresql://example/db")),
            patch.object(first_run_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(first_run_routes, "get_import_state", return_value=state),
            patch.object(first_run_routes, "get_job", return_value=None),
            patch.object(first_run_routes, "enqueue_first_run", enqueue),
            patch.object(first_run_routes, "get_app_session_snapshot", return_value=None),
            patch.object(first_run_routes, "hot_window_product_ready", return_value=True),
            patch.object(first_run_routes, "_first_run_counts", return_value=_count_payload()),
        ):
            response = self.client.post("/v1/first-run/import-jobs")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "succeeded")
        enqueue.assert_not_called()

    def test_first_run_latest_waits_when_smart_snapshot_has_partial_rows(self) -> None:
        state = _import_state(first_dashboard_ready_at=None, hot_window_completed_at="2026-06-20T10:00:45+00:00")
        snapshot = SimpleNamespace(
            smart_inbox={
                "total_rows": 2,
                "sections": [
                    {
                        "id": "today",
                        "title": "Today",
                        "rows": [
                            {
                                "id": "smart-row:ready",
                                "row_key": "thread-ready",
                                "row_type": "summarized_thread",
                                "title": "Apple order W123456789 delivered",
                                "source_thread_ids": ["thread-ready"],
                                "source_message_ids": ["msg-ready"],
                                "readiness": "ready",
                            },
                            {
                                "id": "smart-row:partial",
                                "row_key": "thread-partial",
                                "row_type": "summarized_thread",
                                "title": "Northstar transfer pending review",
                                "source_thread_ids": ["thread-partial"],
                                "source_message_ids": ["msg-partial"],
                                "readiness": "partial",
                            },
                        ],
                    }
                ],
                "ready_count": 1,
                "partial_count": 1,
                "failed_count": 0,
            }
        )
        with (
            patch.object(first_run_routes, "settings", SimpleNamespace(database_path="postgresql://example/db")),
            patch.object(first_run_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(first_run_routes, "get_import_state", return_value=state),
            patch.object(first_run_routes, "get_job", return_value=None),
            patch.object(first_run_routes, "get_app_session_snapshot", return_value=snapshot),
            patch.object(first_run_routes, "hot_window_product_ready", return_value=True),
            patch.object(first_run_routes, "_first_run_counts", return_value=_count_payload()),
        ):
            response = self.client.get("/v1/first-run/import-jobs/latest")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["stage"], "preparing_inbox")
        self.assertEqual(payload["quality_status"], "pending")
        self.assertIsNone(payload["hot_window_ready_at"])

    def test_first_run_response_ready_property_is_inbox_first(self) -> None:
        response = FirstRunImportJobResponse(
            id="job-1",
            user_id="user-1",
            status="succeeded",
            stage="ready",
            fetched_count=0,
            thread_count=0,
            dashboard_item_count=0,
            inbox_ready_at="2026-06-20T10:00:00+00:00",
            first_groups_ready_at="2026-06-20T10:00:10+00:00",
            hot_window_ready_at="2026-06-20T10:00:45+00:00",
            dashboard_ready_at=None,
            created_at="2026-06-20T10:00:00+00:00",
            updated_at="2026-06-20T10:00:10+00:00",
        )

        self.assertTrue(response.ready)


def _import_state(*, first_dashboard_ready_at: str | None, hot_window_completed_at: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        first_batch_imported_at="2026-06-20T10:00:00+00:00",
        first_groups_ready_at="2026-06-20T10:00:10+00:00",
        first_dashboard_ready_at=first_dashboard_ready_at,
        last_sync_error=None,
        full_backfill_started_at="2026-06-20T10:00:00+00:00",
        full_backfill_completed_at=None,
        hot_window_completed_at=hot_window_completed_at,
        updated_at="2026-06-20T10:00:10+00:00",
        last_import_started_at="2026-06-20T10:00:00+00:00",
        last_import_completed_at="2026-06-20T10:00:10+00:00",
    )


def _count_payload() -> dict[str, int]:
    return {
        "fetched_count": 127,
        "thread_count": 42,
        "dashboard_item_count": 3,
    }


if __name__ == "__main__":
    unittest.main()
