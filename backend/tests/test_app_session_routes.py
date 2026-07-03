from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import app_session as app_session_routes
from app.schemas.domain import (
    AppSessionResponse,
    AppSessionSyncState,
    AppSessionUser,
    DashboardResponse,
    FeedResponse,
    GoogleAuthState,
    MailboxResponse,
    PostLoginReadinessResponse,
    SmartInboxResponse,
    SmartInboxRow,
    SmartInboxSection,
    SmartReadinessResponse,
    SmartWorkItem,
    SmartWorkQueueResponse,
)


class AppSessionSmartRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(app_session_routes.router)
        self.client = TestClient(app)

    def test_smart_routes_return_app_session_slices(self) -> None:
        session = _app_session_response()
        build_session = Mock(return_value=session)
        with (
            patch.object(app_session_routes, "settings", SimpleNamespace(database_path="postgresql://example/db")),
            patch.object(app_session_routes, "require_current_user", return_value=SimpleNamespace(id="user-1", email="me@example.com")),
            patch.object(app_session_routes, "build_app_session_response", build_session),
        ):
            inbox = self.client.get("/v1/smart-inbox")
            work = self.client.get("/v1/smart-work-queue")
            readiness = self.client.get("/v1/smart-readiness")

        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.json()["total_rows"], 1)
        self.assertEqual(inbox.json()["sections"][0]["rows"][0]["id"], "smart-row:thread-1")
        self.assertEqual(work.status_code, 200)
        self.assertEqual(work.json()["needs_action"][0]["id"], "smart-work:1")
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(readiness.json()["stage"], "snapshot_ready")
        self.assertEqual(build_session.call_count, 3)


def _app_session_response() -> AppSessionResponse:
    return AppSessionResponse(
        user=AppSessionUser(id="user-1", email="me@example.com"),
        readiness=PostLoginReadinessResponse(
            mode="returning",
            stage="ready",
            ready_to_enter=True,
            dashboard_ready=True,
            mailbox_ready=True,
            ready_dashboard_count=1,
            ready_mail_group_count=1,
            full_import_running=False,
            full_import_completed=True,
        ),
        dashboard=DashboardResponse(auth=GoogleAuthState(available=True, connected=True), feed=FeedResponse()),
        mailbox=MailboxResponse(label="inbox", total_threads=1, loaded_threads=1),
        sync=AppSessionSyncState(full_import_completed=True),
        smart_inbox=SmartInboxResponse(
            total_rows=1,
            sections=[
                SmartInboxSection(
                    id="today",
                    title="Today",
                    rows=[
                        SmartInboxRow(
                            id="smart-row:thread-1",
                            row_key="thread-1",
                            row_type="summarized_thread",
                            title="Thread title",
                            summary="Thread summary",
                            primary_sender="Sender <sender@example.com>",
                            latest_message_at="2026-06-11T10:00:00+00:00",
                            latest_message_id="msg-1",
                            source_thread_ids=["thread-1"],
                            source_message_ids=["msg-1"],
                            confidence_tier="strong",
                            confidence=0.82,
                            grouping_reason={"source": "test"},
                            offline_status="partial",
                            readiness="ready",
                            action_type="reply",
                            priority=80,
                        )
                    ],
                )
            ],
            ready_count=1,
            generated_at="2026-06-11T10:00:00+00:00",
        ),
        smart_work_queue=SmartWorkQueueResponse(
            needs_action=[
                SmartWorkItem(
                    id="smart-work:1",
                    kind="needs_action",
                    title="Reply needed",
                    summary="Reply to the thread.",
                    smart_row_id="smart-row:thread-1",
                    source_thread_ids=["thread-1"],
                    source_message_ids=["msg-1"],
                    confidence=0.9,
                )
            ],
            total_open=1,
        ),
        smart_readiness=SmartReadinessResponse(stage="snapshot_ready", ready_rows=1, processed_threads=1, processed_messages=1),
    )


if __name__ == "__main__":
    unittest.main()
