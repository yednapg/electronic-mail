from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from app.db.models import LoadedEntity, StoredEntity, StoredEntityAiSuggestion, StoredEntityState
from app.db.models import StoredSourceRecord
from app.db.repository import initialize_database
from app.schemas.ai import FeedEntityJudgmentOutput
from app.schemas.domain import AttentionItem, PipelineEntity, PipelineOutput
from app.services.ai.decision import classify_entity_state
from app.services.entities.derive_entity_state import derive_state
from app.services.feed.build_feed import build_feed
from app.services.feed.memory_pipeline import normalize_judgment, to_pipeline_output


def make_record(
    *,
    record_id: str,
    source: str,
    subject: str,
    body: str = "",
    timestamp: str = "2026-04-17T10:00:00+00:00",
    due_at: str | None = None,
) -> StoredSourceRecord:
    payload: dict[str, object] = {"subject": subject, "body": body}
    if due_at is not None:
        payload["due_at"] = due_at

    return StoredSourceRecord(
        id=record_id,
        source=source,
        thread_id="thread-1",
        subject=subject,
        sender="sender@example.com",
        timestamp=timestamp,
        raw_payload=payload,
        created_at=timestamp,
    )


def make_output(
    *,
    entity_id: str,
    current_state: str,
    importance_level: str,
    created_at: str,
    timing_band: str = "today",
) -> PipelineOutput:
    pipeline_entity = PipelineEntity(
        id=entity_id,
        user_id="local-user",
        source="gmail",
        current_state=current_state,
        due_at=None,
        importance=current_state == "waiting",
        lifecycle_state="active" if current_state != "done" else "resolved",
        created_at=created_at,
        updated_at=created_at,
        group_id=entity_id,
    )
    attention_item = AttentionItem(
        id=entity_id,
        entity_id=entity_id,
        user_id="local-user",
        need_type="decision" if current_state == "open" else "awareness",
        action_type="external" if current_state == "open" else "none",
        effort_level="deep" if current_state == "open" else "quick",
        timing_band=timing_band,  # type: ignore[arg-type]
        action_confidence="high",
        primary_action="open" if current_state == "open" else "none",
        fallback_action="open",
        title=entity_id,
        why_this_is_here="test",
        importance_level=importance_level,  # type: ignore[arg-type]
        lifecycle_state="active" if current_state != "done" else "resolved",
        current_state=current_state,
        source="gmail",
        trace_id=entity_id,
        created_at=created_at,
    )
    return PipelineOutput(entity=pipeline_entity, attention_item=attention_item, suppressed=False, suppression_reason=None)


class EntityStateAndFeedTests(unittest.TestCase):
    def test_classify_entity_state_heuristic_fallbacks(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            self.assertEqual(
                classify_entity_state(
                    [
                        make_record(
                            record_id="open-1",
                            source="gmail",
                            subject="Please review the proposal",
                            body="We still need your reply.",
                        )
                    ]
                ),
                "open",
            )
            self.assertEqual(
                classify_entity_state(
                    [
                        make_record(
                            record_id="waiting-1",
                            source="gmail",
                            subject="We received your request",
                            body="We will respond shortly.",
                        )
                    ]
                ),
                "waiting",
            )
            self.assertEqual(
                classify_entity_state(
                    [
                        make_record(
                            record_id="done-1",
                            source="gmail",
                            subject="Your order has been delivered",
                            body="Delivery complete.",
                        )
                    ]
                ),
                "done",
            )

    def test_derive_state_uses_fallback_classifier_and_due_dates(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            state, due_at = derive_state(
                [
                    make_record(
                        record_id="state-1",
                        source="gmail",
                        subject="Please review the proposal",
                        body="Reply by Monday.",
                        due_at="2026-04-20T12:00:00+00:00",
                    )
                ]
            )

        self.assertEqual(state, "open")
        self.assertEqual(due_at, "2026-04-20T12:00:00+00:00")

    def test_build_feed_orders_by_state_importance_then_recency(self) -> None:
        outputs = [
            make_output(
                entity_id="open-older",
                current_state="open",
                importance_level="high",
                created_at="2026-04-17T09:00:00+00:00",
            ),
            make_output(
                entity_id="open-newer",
                current_state="open",
                importance_level="high",
                created_at="2026-04-17T11:00:00+00:00",
            ),
            make_output(
                entity_id="open-low",
                current_state="open",
                importance_level="low",
                created_at="2026-04-17T12:00:00+00:00",
            ),
            make_output(
                entity_id="waiting-high",
                current_state="waiting",
                importance_level="high",
                created_at="2026-04-17T13:00:00+00:00",
            ),
        ]

        feed = build_feed(outputs)

        self.assertEqual(
            [item.entity_id for item in feed.today],
            ["open-newer", "open-older", "open-low", "waiting-high"],
        )
        self.assertEqual(feed.now, [])
        self.assertEqual(feed.worth_knowing, [])

    def test_hidden_ai_judgment_surfaces_done_item_in_worth_knowing(self) -> None:
        loaded_entity = LoadedEntity(
            entity=StoredEntity(
                id="entity-hidden",
                canonical_key="gmail-thread:thread-1",
                created_at="2026-04-17T09:00:00+00:00",
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            state=StoredEntityState(
                id="state-1",
                entity_id="entity-hidden",
                current_state="done",
                due_at=None,
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            ai_suggestion=StoredEntityAiSuggestion(
                id="ai-1",
                entity_id="entity-hidden",
                title="Your bank request was completed.",
                explanation="This is worth keeping around as context.",
                action="none",
                suggested_timing="hidden",
                suggested_priority=10,
                suggested_visibility=False,
                model="ai-judgment",
                generated_at="2026-04-17T10:00:00+00:00",
                generated_from_updated_at="2026-04-17T10:00:00+00:00",
            ),
            members=[
                make_record(
                    record_id="record-1",
                    source="gmail",
                    subject="Your request has been completed",
                    body="Processed successfully.",
                    timestamp="2026-04-17T10:00:00+00:00",
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            initialize_database(db_file.name)
            output = to_pipeline_output(db_file.name, loaded_entity, "2026-04-17T12:00:00+00:00")

        self.assertFalse(output.suppressed)
        self.assertIsNotNone(output.attention_item)

        feed = build_feed([output])

        self.assertEqual(feed.now, [])
        self.assertEqual(feed.today, [])
        self.assertEqual([item.entity_id for item in feed.worth_knowing], ["entity-hidden"])

    def test_visible_done_item_is_not_suppressed(self) -> None:
        loaded_entity = LoadedEntity(
            entity=StoredEntity(
                id="entity-done-visible",
                canonical_key="gmail-thread:thread-2",
                created_at="2026-04-17T09:00:00+00:00",
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            state=StoredEntityState(
                id="state-2",
                entity_id="entity-done-visible",
                current_state="done",
                due_at=None,
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            ai_suggestion=StoredEntityAiSuggestion(
                id="ai-2",
                entity_id="entity-done-visible",
                title="The provider finished your request.",
                explanation="This should still stay visible in the feed as completed context.",
                action="none",
                suggested_timing="today",
                suggested_priority=25,
                suggested_visibility=True,
                model="ai-judgment",
                generated_at="2026-04-17T10:00:00+00:00",
                generated_from_updated_at="2026-04-17T10:00:00+00:00",
            ),
            members=[
                make_record(
                    record_id="record-2",
                    source="gmail",
                    subject="The request is complete",
                    body="All done.",
                    timestamp="2026-04-17T10:00:00+00:00",
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            initialize_database(db_file.name)
            output = to_pipeline_output(db_file.name, loaded_entity, "2026-04-17T12:00:00+00:00")

        self.assertFalse(output.suppressed)
        self.assertIsNotNone(output.attention_item)
        assert output.attention_item is not None
        self.assertEqual(output.attention_item.timing_band, "later")

        feed = build_feed([output])

        self.assertEqual(feed.now, [])
        self.assertEqual(feed.today, [])
        self.assertEqual([item.entity_id for item in feed.worth_knowing], ["entity-done-visible"])

    def test_normalize_judgment_enriches_title_with_missing_artifact_context(self) -> None:
        entity = LoadedEntity(
            entity=StoredEntity(
                id="entity-groww",
                canonical_key="gmail-thread:thread-groww",
                created_at="2026-04-17T09:00:00+00:00",
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            state=StoredEntityState(
                id="state-groww",
                entity_id="entity-groww",
                current_state="done",
                due_at=None,
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            ai_suggestion=None,
            members=[
                make_record(
                    record_id="record-groww",
                    source="gmail",
                    subject="Demat Account successfully closed",
                    body="We have attached your client master report.",
                    timestamp="2026-04-17T10:00:00+00:00",
                )
            ],
        )

        normalized = normalize_judgment(
            entity,
            FeedEntityJudgmentOutput(
                id="entity-groww",
                title="Groww confirmed your demat account has been closed.",
                explanation="Groww has already completed the account closure and sent the client master report, so this is just a finished record of the closure.",
                action="none",
                suggested_timing="later",
                suggested_priority=10,
                suggested_visibility=True,
            ),
        )

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(
            normalized["title"],
            "Groww confirmed your demat account has been closed and sent the client master report.",
        )

    def test_normalize_judgment_keeps_title_when_explanation_adds_no_high_value_clause(self) -> None:
        entity = LoadedEntity(
            entity=StoredEntity(
                id="entity-waiting",
                canonical_key="gmail-thread:thread-waiting",
                created_at="2026-04-17T09:00:00+00:00",
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            state=StoredEntityState(
                id="state-waiting",
                entity_id="entity-waiting",
                current_state="waiting",
                due_at=None,
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            ai_suggestion=None,
            members=[
                make_record(
                    record_id="record-waiting",
                    source="gmail",
                    subject="Request acknowledged",
                    body="We will get back to you soon.",
                    timestamp="2026-04-17T10:00:00+00:00",
                )
            ],
        )

        normalized = normalize_judgment(
            entity,
            FeedEntityJudgmentOutput(
                id="entity-waiting",
                title="The bank acknowledged your request.",
                explanation="The bank has acknowledged the request, so you are waiting for their response.",
                action="none",
                suggested_timing="today",
                suggested_priority=55,
                suggested_visibility=True,
            ),
        )

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["title"], "The bank acknowledged your request.")


if __name__ == "__main__":
    unittest.main()
