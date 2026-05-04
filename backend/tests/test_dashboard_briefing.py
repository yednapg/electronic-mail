from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.schemas.domain import DashboardProfile, FeedResponse
from app.services.ai.decision import (
    DASHBOARD_BRIEFING_PROMPT,
    _build_dashboard_briefing_fallback_output,
    _build_dashboard_briefing_input,
    _infer_name_candidates_from_items,
)


class DashboardBriefingTests(unittest.TestCase):
    def test_briefing_prompt_requires_ai_generated_emojis_and_compact_summary(self) -> None:
        self.assertIn("Use relevant emojis inline", DASHBOARD_BRIEFING_PROMPT)
        self.assertIn("Do not summarize individual inbox items", DASHBOARD_BRIEFING_PROMPT)
        self.assertIn("profile_display_name", DASHBOARD_BRIEFING_PROMPT)

    def test_briefing_input_passes_profile_display_name_to_model(self) -> None:
        briefing_input = _build_dashboard_briefing_input(
            FeedResponse(),
            DashboardProfile(email="owner@example.test", display_name="TestUser"),
        )

        self.assertEqual(briefing_input.profile_display_name, "TestUser")

    def test_name_candidates_require_full_names_from_feed_text(self) -> None:
        candidates = _infer_name_candidates_from_items(
            [
                SimpleNamespace(
                    title="Italy visa application for TestUser needs documents.",
                    why_this_is_here="This is still waiting on the other side for Security alert.",
                )
            ]
        )

        self.assertEqual(candidates, ["TestUser"])

    def test_fallback_prefers_profile_display_name_over_email_guess(self) -> None:
        briefing_input = _build_dashboard_briefing_input(
            FeedResponse(),
            DashboardProfile(email="owner@example.test", display_name="TestUser"),
        )

        output = _build_dashboard_briefing_fallback_output(briefing_input)

        self.assertIn("TestUser", output.headline)
        self.assertNotIn("Ramesh", output.headline)


if __name__ == "__main__":
    unittest.main()
