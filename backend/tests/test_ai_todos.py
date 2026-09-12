from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.ai_todos import (
    TODO_ELIGIBILITY_INSTRUCTIONS,
    TodoEligibilityDecision,
    TodoEligibilityResult,
    _validated_decisions,
)


SOURCE_ACTIONS = [{
    "source_key": "subgoal:one",
    "source_subgoal_id": "one",
    "goal": "Respond to Priya's proposal",
    "latest_development": "Priya asked for an answer by Friday.",
    "evidence_message_ids": ["message-1"],
}]


def _decision(**overrides: object) -> TodoEligibilityDecision:
    values: dict[str, object] = {
        "source_key": "subgoal:one",
        "source_subgoal_id": "one",
        "display_kind": "todo",
        "title": "Reply to Priya about Friday's proposal",
        "detail": "Priya needs your decision before the Friday meeting.",
        "owner": "user",
        "action_type": "reply",
        "requirement": "required",
        "due_at": "2026-09-04T17:00:00+05:30",
        "due_date_source": "explicit",
        "urgency": "upcoming",
        "confidence": 0.94,
        "evidence_message_ids": ["message-1"],
        "evidence_text": "Please reply with your decision before Friday's meeting.",
        "exclusion_reason": None,
    }
    values.update(overrides)
    return TodoEligibilityDecision.model_validate(values)


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _message(
    message_id: str,
    date: str,
    *,
    sent: bool = False,
    thread_id: str = "thread-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        gmail_thread_id=thread_id,
        internal_date=date,
        updated_at=date,
        label_ids=["SENT"] if sent else ["INBOX"],
    )


def _validate(
    decision: TodoEligibilityDecision,
    *,
    messages: list[SimpleNamespace] | None = None,
) -> dict[str, object]:
    result = TodoEligibilityResult(decisions=[decision])
    return _validated_decisions(
        result,
        source_actions=SOURCE_ACTIONS,
        allowed_message_ids={"message-1"},
        messages=messages or [_message("message-1", "2026-09-02T10:00:00+00:00")],
        now=NOW,
    )[0]


class AITodoEligibilityTests(unittest.TestCase):
    def test_concrete_owned_evidenced_action_becomes_todo(self) -> None:
        projected = _validate(_decision())

        self.assertEqual(projected["display_kind"], "todo")
        self.assertEqual(projected["title"], "Reply to Priya about Friday's proposal")
        self.assertEqual(projected["due_date_source"], "explicit")

    def test_optional_opportunity_cannot_be_forced_into_todo(self) -> None:
        projected = _validate(_decision(requirement="optional", confidence=0.99))

        self.assertEqual(projected["display_kind"], "hidden")
        self.assertIn("strict automatic to-do gates", projected["exclusion_reason"])

    def test_task_without_direct_evidence_is_hidden(self) -> None:
        projected = _validate(
            _decision(evidence_message_ids=["invented-message"], confidence=0.99)
        )

        self.assertEqual(projected["display_kind"], "hidden")
        self.assertEqual(projected["evidence_message_ids"], [])

    def test_task_title_must_match_the_structured_action(self) -> None:
        projected = _validate(_decision(title="Priya's proposal needs attention"))

        self.assertEqual(projected["display_kind"], "hidden")

    def test_inferred_or_naive_due_date_is_removed(self) -> None:
        inferred = _validate(_decision(due_date_source="none"))
        naive = _validate(_decision(due_at="2026-09-04T17:00:00"))

        self.assertIsNone(inferred["due_at"])
        self.assertEqual(inferred["due_date_source"], "none")
        self.assertIsNone(naive["due_at"])

    def test_optional_opportunity_does_not_pollute_worth_knowing(self) -> None:
        projected = _validate(_decision(
            display_kind="worth_knowing",
            title="Developer conference registration closes Friday",
            owner="user",
            action_type="register",
            requirement="optional",
            urgency="upcoming",
            confidence=0.84,
        ))

        self.assertEqual(projected["display_kind"], "hidden")

    def test_material_informational_update_stays_in_worth_knowing(self) -> None:
        projected = _validate(_decision(
            display_kind="worth_knowing",
            title="Your reimbursement was approved",
            owner="other",
            action_type="none",
            requirement="informational",
            due_at=None,
            due_date_source="none",
            urgency="unscheduled",
            confidence=0.91,
        ))

        self.assertEqual(projected["display_kind"], "worth_knowing")
        self.assertEqual(projected["action_type"], "open")

    def test_expired_deadline_is_removed_instead_of_becoming_overdue(self) -> None:
        projected = _validate(_decision(due_at="2026-09-01T17:00:00+00:00"))

        self.assertEqual(projected["display_kind"], "hidden")
        self.assertEqual(projected["exclusion_reason"], "The explicit deadline has passed.")

    def test_later_sent_reply_resolves_the_request(self) -> None:
        projected = _validate(
            _decision(due_at=None, due_date_source="none"),
            messages=[
                _message("message-1", "2026-09-02T10:00:00+00:00"),
                _message("sent-1", "2026-09-02T11:00:00+00:00", sent=True),
            ],
        )

        self.assertEqual(projected["display_kind"], "hidden")
        self.assertIn("answered", projected["exclusion_reason"])

    def test_old_undated_request_expires_from_active_todos(self) -> None:
        projected = _validate(
            _decision(due_at=None, due_date_source="none"),
            messages=[_message("message-1", "2026-08-01T10:00:00+00:00")],
        )

        self.assertEqual(projected["display_kind"], "hidden")
        self.assertIn("too old", projected["exclusion_reason"])

    def test_explicit_future_deadline_keeps_an_older_request_active(self) -> None:
        projected = _validate(
            _decision(due_at="2026-10-01T17:00:00+00:00"),
            messages=[_message("message-1", "2026-08-01T10:00:00+00:00")],
        )

        self.assertEqual(projected["display_kind"], "todo")
        self.assertEqual(projected["urgency"], "upcoming")

    def test_model_cannot_put_an_old_undated_action_in_now(self) -> None:
        projected = _validate(
            _decision(due_at=None, due_date_source="none", urgency="now"),
            messages=[_message("message-1", "2026-09-01T10:00:00+00:00")],
        )

        self.assertEqual(projected["display_kind"], "todo")
        self.assertEqual(projected["urgency"], "unscheduled")

    def test_prompt_defines_tasks_as_actions_not_summaries(self) -> None:
        prompt = TODO_ELIGIBILITY_INSTRUCTIONS.casefold()

        self.assertIn("not to summarize", prompt)
        self.assertIn("mailbox user clearly owns", prompt)
        self.assertIn("direct evidence", prompt)
        self.assertIn("natural everyday english", prompt)


if __name__ == "__main__":
    unittest.main()
