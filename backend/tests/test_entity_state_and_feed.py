from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.db.models import LoadedEntity, StoredEntity, StoredEntityAiSuggestion, StoredEntityState
from app.db.models import StoredSourceRecord
from app.db.repository import (
    DEFAULT_USER_ID,
    attach_record_to_entity,
    create_entity,
    initialize_database,
    list_loaded_entities,
    upsert_feed_projection,
    upsert_entity_state,
    upsert_source_records,
)
from app.schemas.ai import FeedEntityJudgmentOutput
from app.schemas.domain import AttentionItem, PipelineEntity, PipelineOutput
from app.services.ai.decision import (
    OPENAI_TIMEOUT_SECONDS,
    _create_json_response,
    _create_json_response_content,
    classify_entity_state,
)
from app.services.entities.derive_entity_state import derive_state
from app.services.feed.build_feed import build_feed
from app.services.feed.memory_pipeline import (
    AI_JUDGMENT_MODEL,
    FEED_PROJECTION_VERSION,
    build_feed_from_projection_cache,
    get_usable_suggestion,
    normalize_judgment,
    refresh_ai_suggestions_for_entities,
    to_pipeline_output,
    versioned_feed_projection_payload,
)


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
    def test_responses_api_helper_uses_private_medium_reasoning_defaults(self) -> None:
        calls: list[dict[str, object]] = []
        client = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(output_text='{"ok":true}')
            )
        )

        with patch.dict(os.environ, {"OPENAI_REASONING_EFFORT": ""}, clear=False):
            response = _create_json_response(
                client,
                model="gpt-5.4-mini",
                instructions="Return JSON.",
                input_text='{"hello":"world"}',
            )

        self.assertEqual(response.output_text, '{"ok":true}')
        self.assertEqual(calls[0]["reasoning"], {"effort": "medium"})
        self.assertEqual(calls[0]["text"], {"format": {"type": "json_object"}, "verbosity": "low"})
        self.assertEqual(calls[0]["max_output_tokens"], 1800)
        self.assertFalse(calls[0]["store"])
        self.assertEqual(calls[0]["timeout"], OPENAI_TIMEOUT_SECONDS)

    @patch("app.services.ai.decision._create_json_response")
    def test_json_response_content_retries_high_effort_after_malformed_json(self, mock_response) -> None:
        mock_response.side_effect = [
            SimpleNamespace(output_text="{not-json"),
            SimpleNamespace(output_text='{"ok":true}'),
        ]

        with patch.dict(os.environ, {"OPENAI_REASONING_EFFORT": ""}, clear=False):
            content = _create_json_response_content(
                SimpleNamespace(),
                model="gpt-5.4-mini",
                instructions="Return JSON.",
                input_text='{"hello":"world"}',
                label="test",
                payload={"hello": "world"},
                fallback_content='{"ok":false}',
            )

        self.assertEqual(content, '{"ok":true}')
        self.assertEqual(
            [call.kwargs["reasoning_effort"] for call in mock_response.call_args_list],
            ["medium", "high"],
        )

    def test_classify_entity_state_heuristic_fallbacks(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_REQUIRED": ""}, clear=False):
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

    @patch("app.services.ai.decision.OpenAI")
    @patch("app.services.ai.decision._create_json_response")
    def test_classify_entity_state_llm_path_uses_compact_timeline(
        self,
        mock_response,
        _mock_openai,
    ) -> None:
        mock_response.return_value = SimpleNamespace(output_text='{"current_state":"waiting"}')

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_REQUIRED": "true",
                "OPENAI_REASONING_EFFORT": "",
                "OPENAI_ENTITY_STATE": "true",
            },
            clear=False,
        ):
            state = classify_entity_state(
                [
                    make_record(
                        record_id="state-llm-1",
                        source="gmail",
                        subject="HSBC acknowledged your corrected account number",
                        body="We have taken your query up for review and will respond by email.",
                        timestamp="2026-04-17T10:00:00+00:00",
                    )
                ]
            )

        self.assertEqual(state, "waiting")
        self.assertEqual(mock_response.call_args.kwargs["reasoning_effort"], "medium")
        self.assertEqual(mock_response.call_args.kwargs["verbosity"], "low")
        message = mock_response.call_args.kwargs["input_text"]
        self.assertIn('"timeline"', message)
        self.assertIn("HSBC acknowledged", message)

    def test_derive_state_uses_fallback_classifier_and_due_dates(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_REQUIRED": ""}, clear=False):
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

    def test_build_feed_backfills_backend_owned_detail_copy_without_internal_ids(self) -> None:
        feed = build_feed(
            [
                make_output(
                    entity_id="waiting-thread",
                    current_state="waiting",
                    importance_level="medium",
                    created_at="2026-04-17T11:00:00+00:00",
                )
            ]
        )

        item = feed.today[0]

        self.assertIsNotNone(item.detail)
        assert item.detail is not None
        self.assertEqual(item.detail.body, ["test"])
        self.assertEqual(item.detail.action_label, "No action needed right now")
        self.assertEqual(item.detail.source_label, "Gmail")
        self.assertNotIn("thread-1", item.detail.model_dump_json())
        self.assertNotIn("trace", item.detail.model_dump_json())

    def test_feed_projection_cache_requires_current_copy_version(self) -> None:
        output = make_output(
            entity_id="versioned-projection",
            current_state="open",
            importance_level="medium",
            created_at="2026-04-17T11:00:00+00:00",
        )

        with tempfile.TemporaryDirectory() as directory:
            database_path = f"{directory}/app.db"
            initialize_database(database_path)
            upsert_feed_projection(
                database_path,
                user_id=DEFAULT_USER_ID,
                entity_id="versioned-projection",
                pipeline_output=output.model_dump(),
            )

            self.assertIsNone(build_feed_from_projection_cache(database_path))

            payload = versioned_feed_projection_payload(output)
            self.assertEqual(payload["projection_version"], FEED_PROJECTION_VERSION)
            upsert_feed_projection(
                database_path,
                user_id=DEFAULT_USER_ID,
                entity_id="versioned-projection",
                pipeline_output=payload,
            )

            feed = build_feed_from_projection_cache(database_path)

        self.assertIsNotNone(feed)
        assert feed is not None
        self.assertEqual(feed.today[0].entity_id, "versioned-projection")

    def test_hidden_ai_judgment_suppresses_done_item(self) -> None:
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
                title="Your parcel was delivered.",
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
                    subject="Your parcel has been delivered",
                    body="Delivery complete.",
                    timestamp="2026-04-17T10:00:00+00:00",
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            initialize_database(db_file.name)
            output = to_pipeline_output(db_file.name, loaded_entity, "2026-04-17T12:00:00+00:00")

        self.assertTrue(output.suppressed)
        self.assertIsNone(output.attention_item)

        feed = build_feed([output])

        self.assertEqual(feed.now, [])
        self.assertEqual(feed.today, [])
        self.assertEqual(feed.worth_knowing, [])

    def test_itr_intimation_surfaces_as_worth_knowing_not_a_todo(self) -> None:
        loaded_entity = LoadedEntity(
            entity=StoredEntity(
                id="entity-itr",
                canonical_key="gmail-thread:thread-itr",
                created_at="2026-05-12T09:00:00+00:00",
                updated_at="2026-05-12T10:00:00+00:00",
            ),
            state=StoredEntityState(
                id="state-itr",
                entity_id="entity-itr",
                current_state="done",
                due_at=None,
                updated_at="2026-05-12T10:00:00+00:00",
            ),
            ai_suggestion=None,
            members=[
                make_record(
                    record_id="record-itr",
                    source="gmail",
                    subject="Your tax notice",
                    body="Your income tax return has been processed successfully.",
                    timestamp="2026-05-12T10:00:00+00:00",
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            initialize_database(db_file.name)
            output = to_pipeline_output(db_file.name, loaded_entity, "2026-05-13T12:00:00+00:00")

        self.assertFalse(output.suppressed)
        self.assertIsNotNone(output.attention_item)
        assert output.attention_item is not None
        self.assertEqual(output.attention_item.timing_band, "later")
        self.assertEqual(output.attention_item.need_type, "awareness")
        self.assertEqual(output.attention_item.primary_action, "none")
        self.assertEqual(output.attention_item.title, "Your ITR intimation was processed")

        feed = build_feed([output])

        self.assertEqual(feed.now, [])
        self.assertEqual(feed.today, [])
        self.assertEqual([item.entity_id for item in feed.worth_knowing], ["entity-itr"])

    def test_recent_bill_email_becomes_concrete_pay_action_with_link(self) -> None:
        loaded_entity = LoadedEntity(
            entity=StoredEntity(
                id="entity-bill",
                canonical_key="gmail-thread:thread-bill",
                created_at="2026-05-13T09:00:00+00:00",
                updated_at="2026-05-13T09:30:00+00:00",
            ),
            state=StoredEntityState(
                id="state-bill",
                entity_id="entity-bill",
                current_state="open",
                due_at=None,
                updated_at="2026-05-13T09:30:00+00:00",
            ),
            ai_suggestion=None,
            members=[
                make_record(
                    record_id="record-bill",
                    source="gmail",
                    subject="Credit card bill due today",
                    body="Please pay your card bill at https://bank.example/pay before 5 PM to avoid late fees.",
                    timestamp="2026-05-13T09:30:00+00:00",
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            initialize_database(db_file.name)
            output = to_pipeline_output(db_file.name, loaded_entity, "2026-05-13T12:00:00+00:00")

        self.assertFalse(output.suppressed)
        self.assertIsNotNone(output.attention_item)
        assert output.attention_item is not None
        self.assertEqual(output.attention_item.primary_action, "pay")
        self.assertEqual(output.attention_item.timing_band, "now")
        self.assertIsNotNone(output.attention_item.detail)
        assert output.attention_item.detail is not None
        self.assertEqual(output.attention_item.detail.action_label, "Pay bill")
        self.assertEqual(output.attention_item.detail.action_url, "https://bank.example/pay")

    def test_old_gmail_action_email_is_not_converted_to_todo(self) -> None:
        loaded_entity = LoadedEntity(
            entity=StoredEntity(
                id="entity-old-bill",
                canonical_key="gmail-thread:thread-old-bill",
                created_at="2026-01-01T09:00:00+00:00",
                updated_at="2026-01-01T09:30:00+00:00",
            ),
            state=StoredEntityState(
                id="state-old-bill",
                entity_id="entity-old-bill",
                current_state="open",
                due_at=None,
                updated_at="2026-01-01T09:30:00+00:00",
            ),
            ai_suggestion=None,
            members=[
                make_record(
                    record_id="record-old-bill",
                    source="gmail",
                    subject="Credit card bill due today",
                    body="Please pay your card bill at https://bank.example/pay before 5 PM to avoid late fees.",
                    timestamp="2026-01-01T09:30:00+00:00",
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            initialize_database(db_file.name)
            output = to_pipeline_output(db_file.name, loaded_entity, "2026-05-13T12:00:00+00:00")

        self.assertTrue(output.suppressed)
        self.assertEqual(output.suppression_reason, "not_actionable_or_worth_knowing")
        self.assertIsNone(output.attention_item)

    def test_visible_done_item_is_suppressed_by_backend_state(self) -> None:
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

        self.assertTrue(output.suppressed)
        self.assertIsNone(output.attention_item)

        feed = build_feed([output])

        self.assertEqual(feed.now, [])
        self.assertEqual(feed.today, [])
        self.assertEqual(feed.worth_knowing, [])

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
        self.assertEqual(normalized["model"], AI_JUDGMENT_MODEL)

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

    def test_refresh_ai_suggestions_loads_only_requested_entities(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            initialize_database(db_file.name)
            target_record = make_record(
                record_id="record-target",
                source="gmail",
                subject="Please review the release",
                body="Can you review this before launch?",
            )
            untouched_record = make_record(
                record_id="record-untouched",
                source="gmail",
                subject="FYI only",
                body="No action needed.",
                timestamp="2026-04-17T11:00:00+00:00",
            )
            upsert_source_records(db_file.name, [target_record, untouched_record])
            target = create_entity(db_file.name, "gmail-thread:target")
            untouched = create_entity(db_file.name, "gmail-thread:untouched")
            attach_record_to_entity(db_file.name, target.id, target_record.id)
            attach_record_to_entity(db_file.name, untouched.id, untouched_record.id)
            upsert_entity_state(db_file.name, target.id, "open", None)
            upsert_entity_state(db_file.name, untouched.id, "open", None)

            with (
                patch(
                    "app.services.feed.memory_pipeline.list_all_loaded_entities",
                    side_effect=AssertionError("refresh should not scan every loaded entity"),
                ) as mock_all_loaded,
                patch(
                    "app.services.feed.memory_pipeline.judge_feed_entities",
                    return_value=[
                        FeedEntityJudgmentOutput(
                            id=target.id,
                            title="Review the release before launch.",
                            explanation="The email asks for review before launch.",
                            action="review",
                            suggested_timing="today",
                            suggested_priority=80,
                            suggested_visibility=True,
                        )
                    ],
                ) as mock_judge,
            ):
                refresh_ai_suggestions_for_entities(db_file.name, [target.id])

            mock_all_loaded.assert_not_called()
            mock_judge.assert_called_once()
            self.assertEqual([context.id for context in mock_judge.call_args.args[0]], [target.id])

            loaded = {entity.entity.id: entity for entity in list_loaded_entities(db_file.name, [target.id, untouched.id])}
            self.assertIsNotNone(loaded[target.id].ai_suggestion)
            self.assertIsNone(loaded[untouched.id].ai_suggestion)

    def test_old_ai_judgment_cache_is_ignored_after_copy_prompt_version_change(self) -> None:
        loaded_entity = LoadedEntity(
            entity=StoredEntity(
                id="entity-old-copy",
                canonical_key="gmail-thread:thread-old-copy",
                created_at="2026-04-17T09:00:00+00:00",
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            state=StoredEntityState(
                id="state-old-copy",
                entity_id="entity-old-copy",
                current_state="open",
                due_at=None,
                updated_at="2026-04-17T10:00:00+00:00",
            ),
            ai_suggestion=StoredEntityAiSuggestion(
                id="ai-old-copy",
                entity_id="entity-old-copy",
                title="Short scraped title",
                explanation="Old brief.",
                action="open",
                suggested_timing="today",
                suggested_priority=50,
                suggested_visibility=True,
                model="ai-judgment",
                generated_at="2026-04-17T10:00:00+00:00",
                generated_from_updated_at="2026-04-17T10:00:00+00:00",
            ),
            members=[
                make_record(
                    record_id="record-old-copy",
                    source="gmail",
                    subject="Please review this",
                    body="Can you review this before launch?",
                    timestamp="2026-04-17T10:00:00+00:00",
                )
            ],
        )

        self.assertIsNone(get_usable_suggestion(loaded_entity))


if __name__ == "__main__":
    unittest.main()
