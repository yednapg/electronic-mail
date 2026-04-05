from __future__ import annotations

"""Tests for feed projection invariants and candidate filtering."""

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.domain import AttentionItem, PipelineEntity, PipelineOutput
from app.services.feed.build_feed import build_feed


class BuildFeedTest(unittest.TestCase):
    """Keep hidden, suppressed, and resolved items out of the final feed."""

    def test_hidden_and_suppressed_items_are_excluded(self) -> None:
        feed = build_feed(
            [
                create_output("visible", timing_band="today"),
                create_output("hidden", timing_band="hidden"),
                create_output("suppressed", timing_band="today", suppressed=True),
                create_output("resolved", timing_band="today", lifecycle_state="resolved"),
            ]
        )

        self.assertEqual([item.entity_id for item in feed.today], ["visible"])


def create_output(
    entity_id: str,
    *,
    timing_band: str,
    suppressed: bool = False,
    lifecycle_state: str = "active",
) -> PipelineOutput:
    entity = PipelineEntity(
        id=entity_id,
        thread_id=entity_id,
        user_id="user-1",
        current_state="awaiting_reply",
        due_at=None,
        importance=True,
        lifecycle_state=lifecycle_state,
        created_at="2026-04-01T00:00:00+00:00",
        updated_at="2026-04-01T00:00:00+00:00",
    )
    attention_item = AttentionItem(
        id=entity_id,
        entity_id=entity_id,
        user_id="user-1",
        need_type="decision",
        action_type="inline",
        effort_level="quick",
        timing_band=timing_band,
        action_confidence="high",
        primary_action="reply",
        fallback_action="open",
        title="Reply about contract",
        why_this_is_here="Still waiting on your response.",
        trace_id=entity_id,
        created_at="2026-04-01T00:00:00+00:00",
    )
    return PipelineOutput(
        entity=entity,
        attention_item=attention_item,
        suppressed=suppressed,
        suppression_reason="suppressed" if suppressed else None,
    )


if __name__ == "__main__":
    unittest.main()
