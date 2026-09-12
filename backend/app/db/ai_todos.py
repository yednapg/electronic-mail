from __future__ import annotations

"""Persistence for the independent AI-derived to-do projection."""

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from app.db.repository import get_engine
from app.db.user_mail_guard import user_mail_write_transaction


def pending_todo_matter_ids(
    database_url: str,
    *,
    user_id: str,
    limit: int = 24,
) -> list[dict[str, Any]]:
    """Return active action matters without a projection for their revision."""
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT matters.id, matters.generation_id, matters.revision
                FROM matter_profiles AS profiles
                JOIN matters
                  ON matters.user_id = profiles.user_id
                 AND matters.gmail_account_id = profiles.gmail_account_id
                 AND matters.generation_id = profiles.active_generation_id
                WHERE profiles.user_id = :user_id
                  AND profiles.enabled = TRUE
                  AND matters.visible = TRUE
                  AND matters.status = 'needs_you'
                  AND EXISTS (
                    SELECT 1
                    FROM matter_members AS members
                    JOIN gmail_messages AS messages
                      ON messages.gmail_account_id = members.gmail_account_id
                     AND messages.message_id = members.message_id
                    WHERE members.user_id = matters.user_id
                      AND members.gmail_account_id = matters.gmail_account_id
                      AND members.generation_id = matters.generation_id
                      AND members.matter_id = matters.id
                      AND jsonb_exists(messages.label_ids_json::jsonb, 'INBOX')
                      AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'SPAM')
                      AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'TRASH')
                      AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'DRAFT')
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM ai_todo_items AS projected
                    WHERE projected.user_id = matters.user_id
                      AND projected.gmail_account_id = matters.gmail_account_id
                      AND projected.generation_id = matters.generation_id
                      AND projected.matter_id = matters.id
                      AND projected.source_matter_revision >= matters.revision
                  )
                ORDER BY matters.latest_message_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {
                "user_id": user_id,
                "limit": max(1, min(limit, 100)),
            },
        ).mappings().all()
    return [dict(row) for row in rows]


def write_todo_projection(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    matter_id: str,
    matter_revision: int,
    decisions: list[dict[str, Any]],
    model: str,
    prompt_version: str,
) -> None:
    """Upsert one complete matter projection while preserving user choices."""
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        matter = connection.execute(
            text(
                """
                SELECT gmail_account_id
                FROM matters
                WHERE user_id = :user_id AND generation_id = :generation_id
                  AND id = :matter_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "matter_id": matter_id,
            },
        ).mappings().first()
        if matter is None:
            raise LookupError("Matter disappeared during to-do extraction")
        gmail_account_id = str(matter["gmail_account_id"])
        projected_keys: list[str] = []
        for decision in decisions:
            source_key = str(decision["source_key"])
            projected_keys.append(source_key)
            connection.execute(
                text(
                    """
                    INSERT INTO ai_todo_items (
                      id, user_id, gmail_account_id, generation_id, matter_id,
                      source_key, source_subgoal_id, display_kind, title, detail,
                      owner, action_type, requirement, due_at, due_date_source,
                      urgency, confidence, evidence_message_ids, evidence_text,
                      exclusion_reason, source_matter_revision, classifier_model,
                      prompt_version
                    ) VALUES (
                      :id, :user_id, :gmail_account_id, :generation_id, :matter_id,
                      :source_key, :source_subgoal_id, :display_kind, :title, :detail,
                      :owner, :action_type, :requirement,
                      CAST(:due_at AS TIMESTAMPTZ), :due_date_source,
                      :urgency, :confidence, CAST(:evidence_message_ids AS JSONB),
                      :evidence_text, :exclusion_reason, :source_matter_revision,
                      :classifier_model, :prompt_version
                    )
                    ON CONFLICT (gmail_account_id, generation_id, matter_id, source_key)
                    DO UPDATE SET
                      source_subgoal_id = EXCLUDED.source_subgoal_id,
                      display_kind = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.display_kind ELSE EXCLUDED.display_kind END,
                      title = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.title ELSE EXCLUDED.title END,
                      detail = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.detail ELSE EXCLUDED.detail END,
                      owner = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.owner ELSE EXCLUDED.owner END,
                      action_type = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.action_type ELSE EXCLUDED.action_type END,
                      requirement = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.requirement ELSE EXCLUDED.requirement END,
                      due_at = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.due_at ELSE EXCLUDED.due_at END,
                      due_date_source = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.due_date_source ELSE EXCLUDED.due_date_source END,
                      urgency = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.urgency ELSE EXCLUDED.urgency END,
                      confidence = CASE WHEN ai_todo_items.user_modified
                        THEN ai_todo_items.confidence ELSE EXCLUDED.confidence END,
                      evidence_message_ids = EXCLUDED.evidence_message_ids,
                      evidence_text = EXCLUDED.evidence_text,
                      exclusion_reason = EXCLUDED.exclusion_reason,
                      source_matter_revision = EXCLUDED.source_matter_revision,
                      classifier_model = EXCLUDED.classifier_model,
                      prompt_version = EXCLUDED.prompt_version,
                      revision = ai_todo_items.revision + 1,
                      updated_at = now()
                    """
                ),
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "gmail_account_id": gmail_account_id,
                    "generation_id": generation_id,
                    "matter_id": matter_id,
                    "source_key": source_key,
                    "source_subgoal_id": decision.get("source_subgoal_id"),
                    "display_kind": decision["display_kind"],
                    "title": decision.get("title"),
                    "detail": decision.get("detail"),
                    "owner": decision["owner"],
                    "action_type": decision["action_type"],
                    "requirement": decision["requirement"],
                    "due_at": decision.get("due_at"),
                    "due_date_source": decision["due_date_source"],
                    "urgency": decision["urgency"],
                    "confidence": decision["confidence"],
                    "evidence_message_ids": json.dumps(decision["evidence_message_ids"]),
                    "evidence_text": decision.get("evidence_text"),
                    "exclusion_reason": decision.get("exclusion_reason"),
                    "source_matter_revision": matter_revision,
                    "classifier_model": model,
                    "prompt_version": prompt_version,
                },
            )

        connection.execute(
            text(
                """
                UPDATE ai_todo_items
                SET display_kind = 'hidden',
                    exclusion_reason = 'The source action is no longer active.',
                    source_matter_revision = :source_matter_revision,
                    revision = revision + 1,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND gmail_account_id = :gmail_account_id
                  AND generation_id = :generation_id
                  AND matter_id = :matter_id
                  AND user_modified = FALSE
                  AND NOT (source_key = ANY(CAST(:source_keys AS TEXT[])))
                """
            ),
            {
                "user_id": user_id,
                "gmail_account_id": gmail_account_id,
                "generation_id": generation_id,
                "matter_id": matter_id,
                "source_matter_revision": matter_revision,
                "source_keys": projected_keys or ["__none__"],
            },
        )


def list_ai_todos(
    database_url: str,
    *,
    user_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT items.*,
                       matters.latest_message_at,
                       COALESCE(
                         NULLIF(semantics.extracted_facts -> 'counterpart_entities' ->> 0, ''),
                         NULLIF(messages.sender, ''),
                         'AI Inbox'
                       ) AS source_label
                FROM ai_todo_items AS items
                JOIN matter_profiles AS profiles
                  ON profiles.user_id = items.user_id
                 AND profiles.gmail_account_id = items.gmail_account_id
                 AND profiles.active_generation_id = items.generation_id
                 AND profiles.enabled = TRUE
                JOIN matters
                  ON matters.id = items.matter_id
                 AND matters.gmail_account_id = items.gmail_account_id
                 AND matters.generation_id = items.generation_id
                LEFT JOIN LATERAL (
                  SELECT members.message_id
                  FROM matter_members AS members
                  WHERE members.user_id = items.user_id
                    AND members.gmail_account_id = items.gmail_account_id
                    AND members.generation_id = items.generation_id
                    AND members.matter_id = items.matter_id
                  ORDER BY members.added_at DESC
                  LIMIT 1
                ) latest_member ON TRUE
                LEFT JOIN gmail_messages AS messages
                  ON messages.gmail_account_id = items.gmail_account_id
                 AND messages.message_id = latest_member.message_id
                LEFT JOIN message_semantics AS semantics
                  ON semantics.gmail_account_id = items.gmail_account_id
                 AND semantics.generation_id = items.generation_id
                 AND semantics.message_id = latest_member.message_id
                LEFT JOIN LATERAL (
                  SELECT MAX(evidence.internal_date) FILTER (
                           WHERE NOT jsonb_exists(evidence.label_ids_json::jsonb, 'SENT')
                         ) AS latest_evidence_at
                  FROM jsonb_array_elements_text(items.evidence_message_ids) AS evidence_id(value)
                  JOIN gmail_messages AS evidence
                    ON evidence.gmail_account_id = items.gmail_account_id
                   AND evidence.message_id = evidence_id.value
                ) lifecycle ON TRUE
                WHERE items.user_id = :user_id
                  AND items.display_kind <> 'hidden'
                  AND (
                    items.status = 'open'
                    OR (items.status = 'snoozed' AND items.snoozed_until <= now())
                  )
                  AND matters.visible = TRUE
                  AND (
                    items.display_kind = 'todo'
                    OR items.requirement IN ('informational', 'waiting')
                  )
                  AND (
                    (items.due_at IS NOT NULL AND items.due_at >= now())
                    OR (
                      items.due_at IS NULL
                      AND lifecycle.latest_evidence_at >= now() - INTERVAL '14 days'
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(items.evidence_message_ids) AS evidence_id(value)
                    JOIN gmail_messages AS evidence
                      ON evidence.gmail_account_id = items.gmail_account_id
                     AND evidence.message_id = evidence_id.value
                    JOIN gmail_messages AS sent
                      ON sent.gmail_account_id = evidence.gmail_account_id
                     AND COALESCE(NULLIF(sent.gmail_thread_id, ''), sent.message_id)
                       = COALESCE(NULLIF(evidence.gmail_thread_id, ''), evidence.message_id)
                     AND jsonb_exists(sent.label_ids_json::jsonb, 'SENT')
                     AND sent.internal_date > evidence.internal_date
                    WHERE NOT jsonb_exists(evidence.label_ids_json::jsonb, 'SENT')
                  )
                ORDER BY
                  CASE items.display_kind WHEN 'todo' THEN 0 ELSE 1 END,
                  CASE items.urgency
                    WHEN 'now' THEN 0 WHEN 'today' THEN 1
                    WHEN 'upcoming' THEN 2 ELSE 3
                  END,
                  items.due_at ASC NULLS LAST,
                  items.confidence DESC,
                  matters.latest_message_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": max(1, min(limit, 200))},
        ).mappings().all()
    return {"items": [_row_to_dict(row) for row in rows]}


def update_ai_todo_status(
    database_url: str,
    *,
    user_id: str,
    todo_id: str,
    status: str,
    snoozed_until: str | None = None,
) -> dict[str, Any] | None:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE ai_todo_items
                SET status = :status,
                    snoozed_until = CASE WHEN :status = 'snoozed'
                      THEN CAST(:snoozed_until AS TIMESTAMPTZ) ELSE NULL END,
                    completed_at = CASE WHEN :status = 'completed' THEN now() ELSE NULL END,
                    user_modified = TRUE,
                    revision = revision + 1,
                    updated_at = now()
                WHERE id = :id AND user_id = :user_id
                RETURNING *
                """
            ),
            {
                "id": todo_id,
                "user_id": user_id,
                "status": status,
                "snoozed_until": snoozed_until,
            },
        ).mappings().first()
    return _row_to_dict(row) if row is not None else None


def _row_to_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in (
        "due_at",
        "snoozed_until",
        "latest_message_at",
        "completed_at",
        "created_at",
        "updated_at",
    ):
        value = result.get(key)
        if value is not None and hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    evidence = result.get("evidence_message_ids")
    if isinstance(evidence, str):
        result["evidence_message_ids"] = json.loads(evidence)
    return result
