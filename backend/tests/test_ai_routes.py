from __future__ import annotations

"""Route-level tests for the in-process AI and system endpoints."""

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.routes.ai import calendar_context, decide, judge_feed_items
from app.api.routes.system import health, root
from app.schemas.ai import (
    CalendarContextInput,
    CalendarContextRequest,
    DecideRequest,
    EntityInput,
    FeedEntityContextInput,
    FeedEntityJudgmentRequest,
)


class MainEndpointsTest(unittest.TestCase):
    """Exercise the thin route wrappers rather than the HTTP stack."""

    def test_health_route_returns_ok(self) -> None:
        self.assertEqual(health(), {"status": "ok"})

    def test_root_route_exposes_service_metadata(self) -> None:
        payload = root()

        self.assertEqual(payload["service"], "Decision Pipeline Backend")
        self.assertEqual(payload["health"], "/health")
        self.assertEqual(payload["decide"], "/decide")

    def test_decide_route_wraps_decision_outputs(self) -> None:
        response = decide(
            DecideRequest(
                entities=[
                    EntityInput(
                        id="entity-1",
                        source="gmail",
                        subject="Please reply about the contract",
                        body="Please reply before tomorrow.",
                        sender="legal@example.com",
                        participants=["legal@example.com"],
                        timestamp="2026-04-03T08:00:00Z",
                        due_at="2026-04-04T08:00:00Z",
                    )
                ]
            )
        )

        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].primary_action, "reply")

    def test_calendar_context_route_wraps_calendar_copy(self) -> None:
        response = calendar_context(
            CalendarContextRequest(
                items=[
                    CalendarContextInput(
                        id="calendar-1",
                        subject="Meeting with Steve",
                        participants=["Steve"],
                        timing_band="today",
                        day_phrase="today",
                        time_phrase="14:30",
                    )
                ]
            )
        )

        self.assertEqual(len(response.items), 1)
        self.assertEqual(
            response.items[0].why_this_is_here,
            "You have a meeting with Steve today at 14:30.",
        )

    def test_judge_feed_items_route_wraps_judgment_outputs(self) -> None:
        response = judge_feed_items(
            FeedEntityJudgmentRequest(
                entities=[
                    FeedEntityContextInput(
                        id="feed-1",
                        source="gmail",
                        current_state="awaiting_reply",
                        due_at="2026-04-04T08:00:00Z",
                        latest_subject="YC Startup School India",
                        latest_sender="events@ycombinator.com",
                        latest_timestamp="2026-04-03T08:00:00Z",
                        participants=["events@ycombinator.com"],
                        sender_domains=["ycombinator.com"],
                        record_count=4,
                        reminder_count=0,
                        lifecycle_hints=["registered", "accepted", "rsvp"],
                        timeline=[],
                    )
                ]
            )
        )

        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].action, "reply")
        self.assertTrue(response.items[0].suggested_visibility)


if __name__ == "__main__":
    unittest.main()
