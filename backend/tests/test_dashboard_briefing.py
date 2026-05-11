from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.schemas.domain import DashboardBriefing, DashboardProfile, FeedResponse
from app.services.ai.decision import (
    DASHBOARD_BRIEFING_PROMPT,
    FEED_JUDGMENT_PROMPT,
    SOURCE_RECORD_SUMMARY_PROMPT,
    _build_dashboard_briefing_fallback_output,
    _build_dashboard_briefing_input,
    _infer_name_candidates_from_items,
    generate_dashboard_briefing,
    sanitize_dashboard_briefing,
)


class DashboardBriefingTests(unittest.TestCase):
    def test_briefing_prompt_requires_ai_generated_emojis_and_compact_summary(self) -> None:
        self.assertIn("Use relevant emojis inline", DASHBOARD_BRIEFING_PROMPT)
        self.assertIn("Do not summarize individual inbox items", DASHBOARD_BRIEFING_PROMPT)
        self.assertIn("profile_display_name", DASHBOARD_BRIEFING_PROMPT)
        self.assertIn("If meeting_count is 0", DASHBOARD_BRIEFING_PROMPT)

    def test_feed_prompt_requires_natural_task_copy_without_internal_ids(self) -> None:
        self.assertIn("natural descriptive sentence", FEED_JUDGMENT_PROMPT)
        self.assertIn("not a scraped subject line", FEED_JUDGMENT_PROMPT)
        self.assertIn("thread id", FEED_JUDGMENT_PROMPT)

    def test_source_summary_prompt_allows_human_readable_detail(self) -> None:
        self.assertIn("under 220 characters", SOURCE_RECORD_SUMMARY_PROMPT)
        self.assertIn("natural conversation-style wording", SOURCE_RECORD_SUMMARY_PROMPT)
        self.assertIn('do not write "User says"', SOURCE_RECORD_SUMMARY_PROMPT)

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
        self.assertIn("Your calendar is open for the rest of the day.", output.brief)

    def test_cached_briefing_cannot_call_zero_meeting_day_booked(self) -> None:
        briefing = sanitize_dashboard_briefing(
            DashboardBriefing(
                headline="Good morning.",
                brief=(
                    "You have 🗓️ 0 meetings, ✅ 8 tasks, 💬 0 replies, and 💳 0 payments, "
                    "so 🔓 the rest of the day looks mostly booked."
                ),
            ),
            FeedResponse(),
            DashboardProfile(email="user@example.com", display_name=None),
        )

        self.assertIn("fairly open", briefing.brief)
        self.assertNotIn("mostly booked", briefing.brief)
        self.assertTrue(briefing.brief.startswith("You have 🗓️ 0 meetings"))

    def test_cached_briefing_adds_subject_to_count_first_model_copy(self) -> None:
        briefing = sanitize_dashboard_briefing(
            DashboardBriefing(
                headline="Good morning.",
                brief="🗓️ 0 meetings, ✅ 8 tasks, 💬 0 replies, and 💳 0 payments.",
            ),
            FeedResponse(),
            DashboardProfile(email="user@example.com", display_name=None),
        )

        self.assertEqual(
            briefing.brief,
            "You have 🗓️ 0 meetings, ✅ 8 tasks, 💬 0 replies, and 💳 0 payments.",
        )

    def test_openai_required_without_key_fails_instead_of_using_briefing_fallback(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_REQUIRED": "true"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is required"):
                generate_dashboard_briefing(FeedResponse())


if __name__ == "__main__":
    unittest.main()
