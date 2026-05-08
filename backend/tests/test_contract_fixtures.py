from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.api.routes.gmail import GmailThreadMutationResponse
from app.schemas.domain import DashboardResponse, GoogleAuthState, ThreadReaderResponse, TraceReplayResponse


FIXTURES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class ContractFixtureTests(unittest.TestCase):
    def test_dashboard_fixture_matches_backend_schema(self) -> None:
        dashboard = DashboardResponse.model_validate(load_fixture("dashboard.json"))

        self.assertTrue(dashboard.auth.connected)
        self.assertEqual(dashboard.feed.now[0].entity_id, "entity-1")
        self.assertEqual(dashboard.feed.now[0].gmail_thread_action, "archive")

    def test_google_auth_state_fixture_matches_backend_schema(self) -> None:
        auth = GoogleAuthState.model_validate(load_fixture("google-auth-state.json"))

        self.assertTrue(auth.available)
        self.assertTrue(auth.connected)

    def test_trace_fixture_matches_backend_schema(self) -> None:
        trace = TraceReplayResponse.model_validate(load_fixture("trace.json"))

        self.assertEqual(trace.entity_id, "entity-1")
        self.assertEqual(trace.items[0].stage, "grouping")

    def test_thread_reader_fixture_matches_backend_schema(self) -> None:
        thread = ThreadReaderResponse.model_validate(load_fixture("thread-reader.json"))

        self.assertEqual(thread.entity_id, "entity-1")
        self.assertEqual(thread.messages[0].source, "gmail")
        self.assertIn("Pay before 5 PM", thread.messages[0].body)

    def test_gmail_mutation_fixtures_match_backend_schema(self) -> None:
        archive = GmailThreadMutationResponse.model_validate(load_fixture("gmail-thread-archive.json"))
        unarchive = GmailThreadMutationResponse.model_validate(load_fixture("gmail-thread-unarchive.json"))

        self.assertEqual(archive.action, "archive")
        self.assertEqual(unarchive.action, "unarchive")


if __name__ == "__main__":
    unittest.main()
