from __future__ import annotations

"""Heuristic-path tests for the AI decision service."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.ai import CalendarContextInput, EntityInput, FeedEntityContextInput, FeedTimelineEntry
from app.services.ai.decision import decide_entities, describe_calendar_context, judge_feed_entities


class DecisionHeuristicsTest(unittest.TestCase):
    """Verify fallback behavior when no LLM credentials are configured."""

    @patch.dict(os.environ, {}, clear=True)
    def test_decide_entities_returns_reply_decision(self) -> None:
        items = decide_entities(
            [
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

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].primary_action, "reply")
        self.assertEqual(items[0].timing_band, "now")
        self.assertEqual(items[0].importance_level, "high")

    @patch.dict(os.environ, {}, clear=True)
    def test_decide_entities_suppresses_resolved_updates(self) -> None:
        items = decide_entities(
            [
                EntityInput(
                    id="entity-2",
                    source="gmail",
                    subject="Invoice paid",
                    body="Your invoice has been paid and completed.",
                    sender="billing@example.com",
                    participants=["billing@example.com"],
                    timestamp="2026-04-03T08:00:00Z",
                    due_at=None,
                )
            ]
        )

        self.assertEqual(items, [])

    @patch.dict(os.environ, {}, clear=True)
    def test_calendar_context_generates_natural_sentence(self) -> None:
        items = describe_calendar_context(
            [
                CalendarContextInput(
                    id="calendar-1",
                    subject="Meeting with Michael",
                    participants=["Michael"],
                    timing_band="today",
                    day_phrase="today",
                    time_phrase="21:30",
                )
            ]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].why_this_is_here,
            "You have a meeting with Michael today at 21:30.",
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_judge_feed_entities_uses_memory_context_for_reply_state(self) -> None:
        items = judge_feed_entities(
            [
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
                    timeline=[
                        FeedTimelineEntry(
                            id="yc-1",
                            source="gmail",
                            subject="YC Startup School India RSVP confirmed",
                            sender="events@ycombinator.com",
                            timestamp="2026-04-04T08:00:00Z",
                            body_snippet="Your RSVP is confirmed for YC Startup School India.",
                            thread_id="thread-yc",
                        )
                    ],
                )
            ]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].action, "reply")
        self.assertTrue(items[0].suggested_visibility)
        self.assertIn("waiting on your reply", items[0].explanation)

    @patch.dict(os.environ, {}, clear=True)
    def test_judge_feed_entities_hides_resolved_state(self) -> None:
        items = judge_feed_entities(
            [
                FeedEntityContextInput(
                    id="feed-2",
                    source="gmail",
                    current_state="resolved",
                    due_at=None,
                    latest_subject="Invoice paid",
                    latest_sender="billing@example.com",
                    latest_timestamp="2026-04-03T08:00:00Z",
                    participants=["billing@example.com"],
                    sender_domains=["example.com"],
                    record_count=1,
                    reminder_count=0,
                    lifecycle_hints=["paid"],
                    timeline=[],
                )
            ]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].suggested_timing, "hidden")
        self.assertFalse(items[0].suggested_visibility)


if __name__ == "__main__":
    unittest.main()
