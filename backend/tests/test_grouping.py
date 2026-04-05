from __future__ import annotations

"""Tests for entity grouping confidence and null-match behavior."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.ai import EntityGroupingCandidateInput, EntityGroupingRequest
from app.services.ai.decision import resolve_entity_group


class GroupingTest(unittest.TestCase):
    """Cover the heuristic grouping path used without an LLM."""

    @patch.dict(os.environ, {}, clear=True)
    def test_grouping_heuristic_attaches_related_entity(self) -> None:
        response = resolve_entity_group(
            EntityGroupingRequest(
                subject="YC Startup School India RSVP update",
                snippet="Please confirm your YC Startup School India RSVP.",
                candidates=[
                    EntityGroupingCandidateInput(
                        entity_id="entity-1",
                        canonical_key="yc::ycombinator.com",
                        latest_subject="YC Startup School India registration",
                        latest_sender="events@ycombinator.com",
                        current_state="awaiting_reply",
                        summary="YC Startup School India lifecycle",
                    )
                ],
            )
        )

        self.assertEqual(response.entity_id, "entity-1")
        self.assertGreater(response.confidence, 0.8)

    @patch.dict(os.environ, {}, clear=True)
    def test_grouping_heuristic_returns_null_when_uncertain(self) -> None:
        response = resolve_entity_group(
            EntityGroupingRequest(
                subject="Completely unrelated message",
                snippet="This has nothing to do with the candidate.",
                candidates=[
                    EntityGroupingCandidateInput(
                        entity_id="entity-1",
                        canonical_key="yc::ycombinator.com",
                        latest_subject="YC Startup School India registration",
                        latest_sender="events@ycombinator.com",
                        current_state="awaiting_reply",
                        summary="YC Startup School India lifecycle",
                    )
                ],
            )
        )

        self.assertIsNone(response.entity_id)
        self.assertLess(response.confidence, 0.8)


if __name__ == "__main__":
    unittest.main()
