from __future__ import annotations

"""Postgres repository for the generation-scoped AI Inbox projection.

This module never mutates ``gmail_messages``. Gmail remains canonical and the
normal Inbox can operate even when every query in this module fails.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import text

from app.core.config import Settings
from app.core.counterpart_identity import (
    aggregate_counterpart_entities,
    counterpart_aware_headline,
    mailbox_owner_identity,
)
from app.db.repository import get_engine
from app.db.user_mail_guard import user_mail_write_transaction


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _canonical_subgoal_key(goal: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", goal.casefold()).strip()
    return sha256(normalized.encode("utf-8")).hexdigest()


def _derived_matter_status(statuses: Iterable[str], fallback: str) -> str:
    values = list(dict.fromkeys(str(status) for status in statuses))
    if not values:
        return fallback
    for active in ("needs_you", "waiting_on_others", "in_progress"):
        if active in values:
            return active
    if all(value == "completed" for value in values):
        return "completed"
    if all(value == "cancelled" for value in values):
        return "cancelled"
    if all(value == "failed" for value in values):
        return "failed"
    if "completed" in values:
        return "partially_completed"
    if "failed" in values:
        return "failed"
    return "cancelled"


def get_profile(database_url: str, *, user_id: str) -> dict[str, Any]:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text("SELECT * FROM matter_profiles WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).mappings().first()
    if row is None:
        return {
            "user_id": user_id,
            "consented_at": None,
            "enabled": False,
            "grouping_style": "focused",
            "rollout_mode": "disabled",
            "active_generation_id": None,
            "revision": 0,
        }
    return dict(row)


def create_generation(
    connection,
    settings: Settings,
    *,
    user_id: str,
    grouping_style: str,
    status: str,
    source_generation_id: str | None = None,
) -> str:
    generation_id = str(uuid4())
    connection.execute(
        text(
            """
            INSERT INTO matter_generations (
              id, user_id, status, grouping_style, source_generation_id,
              prompt_version, embedding_model, classifier_model, review_model
            ) VALUES (
              :id, :user_id, :status, :grouping_style, :source_generation_id,
              :prompt_version, :embedding_model, :classifier_model, :review_model
            )
            """
        ),
        {
            "id": generation_id,
            "user_id": user_id,
            "status": status,
            "grouping_style": grouping_style,
            "source_generation_id": source_generation_id,
            "prompt_version": settings.ai_inbox_prompt_version,
            "embedding_model": settings.ai_inbox_embedding_model,
            "classifier_model": settings.ai_inbox_classifier_model,
            "review_model": settings.ai_inbox_review_model,
        },
    )
    if source_generation_id:
        # Rebuilds start with user-locked membership copied into new matter IDs.
        # Automatic memberships are deliberately recomputed, while confirmed
        # messages can never be moved by a later model/prompt generation.
        protected_matters = connection.execute(
            text(
                """
                SELECT DISTINCT matters.id
                FROM matters
                JOIN matter_members AS members
                  ON members.user_id = matters.user_id
                 AND members.generation_id = matters.generation_id
                 AND members.matter_id = matters.id
                WHERE matters.user_id = :user_id
                  AND matters.generation_id = :source_generation_id
                  AND members.membership_locked = TRUE
                """
            ),
            {"user_id": user_id, "source_generation_id": source_generation_id},
        ).mappings().all()
        for protected in protected_matters:
            source_matter_id = str(protected["id"])
            copied_matter_id = str(uuid4())
            connection.execute(
                text(
                    """
                    INSERT INTO matters (
                      id, user_id, generation_id, stable_goal, dynamic_title,
                      summary, status, evidence_message_ids, confidence,
                      confidence_state, embedding, classifier_model, review_model,
                      prompt_version, revision, latest_message_at, visible
                    )
                    SELECT
                      :copied_matter_id, user_id, :generation_id, stable_goal,
                      dynamic_title, summary, status,
                      COALESCE(
                        (
                          SELECT jsonb_agg(locked_members.message_id)
                          FROM matter_members AS locked_members
                          WHERE locked_members.user_id = matters.user_id
                            AND locked_members.generation_id = matters.generation_id
                            AND locked_members.matter_id = matters.id
                            AND locked_members.membership_locked = TRUE
                        ),
                        '[]'::jsonb
                      ),
                      confidence, 'confirmed', embedding, classifier_model,
                      review_model, prompt_version, 1, latest_message_at, visible
                    FROM matters
                    WHERE id = :source_matter_id AND user_id = :user_id
                      AND generation_id = :source_generation_id
                    """
                ),
                {
                    "copied_matter_id": copied_matter_id,
                    "generation_id": generation_id,
                    "source_matter_id": source_matter_id,
                    "source_generation_id": source_generation_id,
                    "user_id": user_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO matter_members (
                      user_id, generation_id, matter_id, message_id,
                      membership_source, confidence, provisional,
                      user_confirmed, membership_locked, added_at
                    )
                    SELECT
                      user_id, :generation_id, :copied_matter_id, message_id,
                      'user', confidence, FALSE, TRUE, TRUE, now()
                    FROM matter_members
                    WHERE user_id = :user_id
                      AND generation_id = :source_generation_id
                      AND matter_id = :source_matter_id
                      AND membership_locked = TRUE
                    """
                ),
                {
                    "generation_id": generation_id,
                    "copied_matter_id": copied_matter_id,
                    "user_id": user_id,
                    "source_generation_id": source_generation_id,
                    "source_matter_id": source_matter_id,
                },
            )
    return generation_id


def update_profile(
    settings: Settings,
    *,
    user_id: str,
    consent: bool | None,
    enabled: bool | None,
    grouping_style: str | None,
    rebuild_existing: bool,
) -> tuple[dict[str, Any], str | None]:
    """Update profile and return it plus a generation that needs bootstrapping."""
    engine = get_engine(str(settings.database_path))
    generation_to_build: str | None = None
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        current = connection.execute(
            text("SELECT * FROM matter_profiles WHERE user_id = :user_id FOR UPDATE"),
            {"user_id": user_id},
        ).mappings().first()
        current_consent = bool(current and current["consented_at"])
        current_enabled = bool(current and current["enabled"])
        current_style = str(current["grouping_style"] if current else "focused")
        current_generation = str(current["active_generation_id"]) if current and current["active_generation_id"] else None

        resolved_consent = current_consent if consent is None else consent
        resolved_enabled = current_enabled if enabled is None else enabled
        resolved_style = grouping_style or current_style
        if not resolved_consent:
            resolved_enabled = False
        if resolved_enabled and not resolved_consent:
            raise ValueError("AI processing consent is required before enabling AI Inbox")
        if not settings.ai_inbox_enabled:
            resolved_enabled = False

        style_changed = resolved_style != current_style
        if (
            resolved_enabled
            and current_generation is None
            and current
            and str(current["rollout_mode"]) in {"shadow", "preview"}
        ):
            raise ValueError("The reviewed shadow generation must be promoted before enabling AI Inbox")
        if resolved_enabled and current_generation is None:
            generation_to_build = create_generation(
                connection,
                settings,
                user_id=user_id,
                grouping_style=resolved_style,
                status="active",
            )
            current_generation = generation_to_build
        elif resolved_enabled and style_changed and rebuild_existing:
            generation_to_build = create_generation(
                connection,
                settings,
                user_id=user_id,
                grouping_style=resolved_style,
                status="building",
                source_generation_id=current_generation,
            )
        elif resolved_enabled and style_changed and current_generation:
            connection.execute(
                text(
                    """
                    UPDATE matter_generations
                    SET grouping_style = :grouping_style, updated_at = now()
                    WHERE id = :generation_id AND user_id = :user_id AND status = 'active'
                    """
                ),
                {
                    "grouping_style": resolved_style,
                    "generation_id": current_generation,
                    "user_id": user_id,
                },
            )

        rollout_mode = "live" if resolved_enabled else "disabled"
        row = connection.execute(
            text(
                """
                INSERT INTO matter_profiles (
                  user_id, consented_at, enabled, grouping_style, rollout_mode,
                  active_generation_id, revision, created_at, updated_at
                ) VALUES (
                  :user_id, CASE WHEN :consented THEN now() ELSE NULL END,
                  :enabled, :grouping_style, :rollout_mode, :active_generation_id,
                  1, now(), now()
                )
                ON CONFLICT (user_id) DO UPDATE SET
                  consented_at = CASE
                    WHEN :consented THEN COALESCE(matter_profiles.consented_at, now())
                    ELSE NULL
                  END,
                  enabled = :enabled,
                  grouping_style = :grouping_style,
                  rollout_mode = :rollout_mode,
                  active_generation_id = COALESCE(matter_profiles.active_generation_id, :active_generation_id),
                  revision = matter_profiles.revision + 1,
                  updated_at = now()
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "consented": resolved_consent,
                "enabled": resolved_enabled,
                "grouping_style": resolved_style,
                "rollout_mode": rollout_mode,
                "active_generation_id": current_generation,
            },
        ).mappings().one()
    return dict(row), generation_to_build


def promote_generation(database_url: str, *, user_id: str, generation_id: str) -> None:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        target = connection.execute(
            text(
                "SELECT id FROM matter_generations WHERE id = :id AND user_id = :user_id AND status IN ('building', 'shadow') FOR UPDATE"
            ),
            {"id": generation_id, "user_id": user_id},
        ).scalar_one_or_none()
        if target is None:
            return
        superseded_ids = [
            str(row["id"])
            for row in connection.execute(
                text(
                    """
                    SELECT id FROM matter_generations
                    WHERE user_id = :user_id AND status = 'active' AND id <> :id
                    """
                ),
                {"user_id": user_id, "id": generation_id},
            ).mappings().all()
        ]
        connection.execute(
            text("UPDATE matter_generations SET status = 'superseded', updated_at = now() WHERE user_id = :user_id AND status = 'active'"),
            {"user_id": user_id},
        )
        connection.execute(
            text("UPDATE matter_generations SET status = 'active', completed_at = now(), updated_at = now() WHERE id = :id AND user_id = :user_id"),
            {"id": generation_id, "user_id": user_id},
        )
        if superseded_ids:
            # A promoted projection must not keep spending money on an old
            # generation's queued backfill. Running work is durably cancelled;
            # a provider call already in flight may finish, but cannot be
            # reclaimed and no further stale work will start.
            connection.execute(
                text(
                    """
                    UPDATE background_jobs
                    SET status = 'cancelled', completed_at = now(),
                        lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = now()
                    WHERE user_id = :user_id
                      AND status IN ('queued', 'running')
                      AND kind IN (
                        'ai_message_organize',
                        'ai_generation_bootstrap',
                        'ai_generation_finalize'
                      )
                      AND payload_json::jsonb ->> 'generation_id' = ANY(:generation_ids)
                    """
                ),
                {"user_id": user_id, "generation_ids": superseded_ids},
            )
        connection.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'cancelled', completed_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND status = 'queued'
                  AND kind = 'ai_generation_finalize'
                  AND payload_json::jsonb ->> 'generation_id' = :generation_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        )
        connection.execute(
            text(
                """
                UPDATE matter_profiles
                SET active_generation_id = :id, enabled = TRUE, rollout_mode = 'live',
                    revision = revision + 1, updated_at = now()
                WHERE user_id = :user_id
                """
            ),
            {"id": generation_id, "user_id": user_id},
        )


def get_generation(database_url: str, *, user_id: str, generation_id: str) -> dict[str, Any] | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT * FROM matter_generations
                WHERE id = :generation_id AND user_id = :user_id
                """
            ),
            {"generation_id": generation_id, "user_id": user_id},
        ).mappings().first()
    return dict(row) if row is not None else None


def latest_shadow_generation(database_url: str, *, user_id: str) -> dict[str, Any] | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT * FROM matter_generations
                WHERE user_id = :user_id AND status = 'shadow'
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).mappings().first()
    return dict(row) if row is not None else None


def start_shadow_generation(
    settings: Settings,
    *,
    user_id: str,
    grouping_style: str,
) -> str:
    engine = get_engine(str(settings.database_path))
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        profile = connection.execute(
            text("SELECT * FROM matter_profiles WHERE user_id = :user_id FOR UPDATE"),
            {"user_id": user_id},
        ).mappings().first()
        if profile is None or not profile["consented_at"]:
            raise ValueError("OpenAI processing consent is required before creating a shadow generation")
        superseded_ids = [
            str(row["id"])
            for row in connection.execute(
                text(
                    """
                    SELECT id FROM matter_generations
                    WHERE user_id = :user_id AND status = 'shadow'
                    FOR UPDATE
                    """
                ),
                {"user_id": user_id},
            ).mappings().all()
        ]
        connection.execute(
            text(
                """
                UPDATE matter_generations
                SET status = 'superseded', updated_at = now()
                WHERE user_id = :user_id AND status = 'shadow'
                """
            ),
            {"user_id": user_id},
        )
        if superseded_ids:
            connection.execute(
                text(
                    """
                    UPDATE background_jobs
                    SET status = 'cancelled', completed_at = now(),
                        lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = now()
                    WHERE user_id = :user_id
                      AND status IN ('queued', 'running')
                      AND kind IN (
                        'ai_message_organize',
                        'ai_generation_bootstrap',
                        'ai_generation_finalize'
                      )
                      AND payload_json::jsonb ->> 'generation_id' = ANY(:generation_ids)
                    """
                ),
                {"user_id": user_id, "generation_ids": superseded_ids},
            )
        source_generation_id = (
            str(profile["active_generation_id"])
            if profile["active_generation_id"]
            else None
        )
        generation_id = create_generation(
            connection,
            settings,
            user_id=user_id,
            grouping_style=grouping_style,
            status="shadow",
            source_generation_id=source_generation_id,
        )
        connection.execute(
            text(
                """
                UPDATE matter_profiles
                SET grouping_style = :grouping_style, rollout_mode = 'shadow',
                    revision = revision + 1, updated_at = now()
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id, "grouping_style": grouping_style},
        )
    return generation_id


def complete_shadow_generation(database_url: str, *, user_id: str, generation_id: str) -> None:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text(
                """
                UPDATE matter_generations
                SET completed_at = now(), updated_at = now()
                WHERE id = :generation_id AND user_id = :user_id AND status = 'shadow'
                """
            ),
            {"generation_id": generation_id, "user_id": user_id},
        )


def record_shadow_evaluation(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    quality_metrics: dict[str, Any],
) -> dict[str, Any]:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        generation = connection.execute(
            text(
                """
                SELECT id FROM matter_generations
                WHERE id = :generation_id AND user_id = :user_id
                  AND status = 'shadow' AND completed_at IS NOT NULL
                FOR UPDATE
                """
            ),
            {"generation_id": generation_id, "user_id": user_id},
        ).scalar_one_or_none()
        if generation is None:
            raise LookupError("Completed shadow generation not found")
        operational = connection.execute(
            text(
                """
                SELECT
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                    FILTER (WHERE operation = 'classification' AND success = TRUE)
                    / 1000.0 AS classifier_p95_seconds,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                    FILTER (WHERE operation = 'review' AND success = TRUE)
                    / 1000.0 AS review_p95_seconds,
                  CASE WHEN COUNT(DISTINCT message_id) FILTER (WHERE success = TRUE) = 0 THEN NULL
                    ELSE SUM(estimated_cost_usd) FILTER (WHERE success = TRUE)
                      / COUNT(DISTINCT message_id) FILTER (WHERE success = TRUE) * 1000
                  END AS cost_per_1000_messages,
                  COUNT(*) FILTER (WHERE success = FALSE)::INTEGER AS provider_errors
                FROM ai_usage_events
                WHERE user_id = :user_id AND generation_id = :generation_id
                """
            ),
            {"generation_id": generation_id, "user_id": user_id},
        ).mappings().one()
        metrics = {
            **quality_metrics,
            "classifier_p95_seconds": (
                float(operational["classifier_p95_seconds"])
                if operational["classifier_p95_seconds"] is not None
                else None
            ),
            "review_p95_seconds": (
                float(operational["review_p95_seconds"])
                if operational["review_p95_seconds"] is not None
                else None
            ),
            "cost_per_1000_messages": (
                float(operational["cost_per_1000_messages"])
                if operational["cost_per_1000_messages"] is not None
                else None
            ),
            "provider_errors": int(operational["provider_errors"] or 0),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
        connection.execute(
            text(
                """
                UPDATE matter_generations
                SET stats_json = CAST(:metrics AS jsonb), updated_at = now()
                WHERE id = :generation_id AND user_id = :user_id
                """
            ),
            {
                "metrics": json.dumps(metrics),
                "generation_id": generation_id,
                "user_id": user_id,
            },
        )
    return metrics


def active_generation(database_url: str, *, user_id: str) -> dict[str, Any] | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT generations.*
                FROM matter_profiles AS profiles
                JOIN matter_generations AS generations
                  ON generations.id = profiles.active_generation_id
                 AND generations.user_id = profiles.user_id
                WHERE profiles.user_id = :user_id AND profiles.enabled = TRUE
                """
            ),
            {"user_id": user_id},
        ).mappings().first()
    return dict(row) if row is not None else None


def list_ai_inbox(
    database_url: str,
    *,
    user_id: str,
    search_query: str | None = None,
    limit: int = 200,
    generation_id_override: str | None = None,
) -> dict[str, Any]:
    profile = get_profile(database_url, user_id=user_id)
    generation_id = generation_id_override or profile.get("active_generation_id")
    if generation_id_override:
        generation = get_generation(
            database_url,
            user_id=user_id,
            generation_id=generation_id_override,
        )
        if generation is None or str(generation["status"]) not in {"shadow", "building"}:
            return {"profile": profile, "generation_id": None, "matters": [], "organizing": [], "revision": str(profile.get("revision", 0))}
    elif not profile.get("enabled"):
        generation_id = None
    if not generation_id:
        return {"profile": profile, "generation_id": None, "matters": [], "organizing": [], "revision": str(profile.get("revision", 0))}

    params: dict[str, Any] = {
        "user_id": user_id,
        "generation_id": generation_id,
        "limit": max(1, min(limit, 500)),
        "query": f"%{(search_query or '').strip()}%",
        "has_query": bool((search_query or "").strip()),
    }
    with get_engine(database_url).connect() as connection:
        owner_row = connection.execute(
            text("SELECT email, display_name FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        ).mappings().first()
        sent_sender_headers = connection.execute(
            text(
                """
                SELECT sender
                FROM gmail_messages
                WHERE user_id = :user_id
                  AND sender IS NOT NULL
                  AND jsonb_exists(label_ids_json::jsonb, 'SENT')
                GROUP BY sender
                ORDER BY COUNT(*) DESC
                LIMIT 100
                """
            ),
            {"user_id": user_id},
        ).scalars().all()
        owner_identity = mailbox_owner_identity(
            [str(value) for value in sent_sender_headers],
            fallback_display_name=str(owner_row["display_name"] or "") if owner_row else "",
            fallback_address=str(owner_row["email"] or "") if owner_row else "",
        )
        matter_rows = connection.execute(
            text(
                """
                SELECT
                  matters.*,
                  COUNT(members.message_id)::INTEGER AS message_count,
                  BOOL_OR(jsonb_exists(messages.label_ids_json::jsonb, 'UNREAD')) AS unread,
                  BOOL_OR(jsonb_exists(messages.label_ids_json::jsonb, 'STARRED')) AS starred,
                  (
                    SELECT COUNT(*)::INTEGER
                    FROM matter_subgoals AS subgoals
                    WHERE subgoals.user_id = matters.user_id
                      AND subgoals.generation_id = matters.generation_id
                      AND subgoals.matter_id = matters.id
                      AND subgoals.status IN ('needs_you', 'waiting_on_others', 'in_progress')
                  ) AS open_subgoal_count,
                  (
                    SELECT COUNT(*)::INTEGER
                    FROM matter_members AS review_members
                    WHERE review_members.user_id = matters.user_id
                      AND review_members.generation_id = matters.generation_id
                      AND review_members.matter_id = matters.id
                      AND review_members.provisional = TRUE
                  ) AS review_count,
                  ARRAY_REMOVE(ARRAY_AGG(DISTINCT messages.sender), NULL) AS participants,
                  JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                      'sender', messages.sender,
                      'recipients', messages.recipients_json,
                      'label_ids', messages.label_ids_json,
                      'counterpart_entities', COALESCE(
                        semantics.extracted_facts -> 'counterpart_entities',
                        '[]'::jsonb
                      ),
                      'latest_message_at', COALESCE(messages.internal_date, messages.updated_at)
                    )
                  ) AS counterpart_inputs,
                  COALESCE(
                    JSONB_AGG(messages.message_id) FILTER (
                      WHERE :has_query AND (
                        COALESCE(messages.subject, '') ILIKE :query OR
                        COALESCE(messages.sender, '') ILIKE :query OR
                        COALESCE(messages.text_body, '') ILIKE :query OR
                        COALESCE(messages.snippet, '') ILIKE :query OR
                        COALESCE(semantics.extracted_facts -> 'counterpart_entities', '[]'::jsonb)::text ILIKE :query
                      )
                    ),
                    '[]'::jsonb
                  ) AS matching_message_ids
                FROM matters
                JOIN matter_members AS members
                  ON members.user_id = matters.user_id
                 AND members.generation_id = matters.generation_id
                 AND members.matter_id = matters.id
                JOIN gmail_messages AS messages
                  ON messages.user_id = members.user_id AND messages.message_id = members.message_id
                LEFT JOIN message_semantics AS semantics
                  ON semantics.user_id = members.user_id
                 AND semantics.generation_id = members.generation_id
                 AND semantics.message_id = members.message_id
                WHERE matters.user_id = :user_id
                  AND matters.generation_id = :generation_id
                  AND matters.visible = TRUE
                  AND EXISTS (
                    SELECT 1
                    FROM matter_members AS visible_members
                    JOIN gmail_messages AS visible_messages
                      ON visible_messages.user_id = visible_members.user_id
                     AND visible_messages.message_id = visible_members.message_id
                    WHERE visible_members.user_id = matters.user_id
                      AND visible_members.generation_id = matters.generation_id
                      AND visible_members.matter_id = matters.id
                      AND jsonb_exists(visible_messages.label_ids_json::jsonb, 'INBOX')
                      AND NOT jsonb_exists(visible_messages.label_ids_json::jsonb, 'SPAM')
                      AND NOT jsonb_exists(visible_messages.label_ids_json::jsonb, 'TRASH')
                      AND NOT jsonb_exists(visible_messages.label_ids_json::jsonb, 'DRAFT')
                  )
                GROUP BY matters.id
                HAVING NOT :has_query
                   OR matters.dynamic_title ILIKE :query
                   OR matters.summary ILIKE :query
                   OR matters.stable_goal ILIKE :query
                   OR BOOL_OR(
                     COALESCE(messages.subject, '') ILIKE :query OR
                     COALESCE(messages.sender, '') ILIKE :query OR
                     COALESCE(messages.text_body, '') ILIKE :query OR
                     COALESCE(messages.snippet, '') ILIKE :query OR
                     COALESCE(semantics.extracted_facts -> 'counterpart_entities', '[]'::jsonb)::text ILIKE :query
                   )
                ORDER BY matters.latest_message_at DESC NULLS LAST, matters.updated_at DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        organizing_rows = connection.execute(
            text(
                """
                WITH unassigned AS (
                  SELECT messages.*,
                         semantics.processing_state,
                         semantics.error_code,
                         semantics.extracted_facts AS semantic_facts,
                         ROW_NUMBER() OVER (
                           PARTITION BY COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id)
                           ORDER BY COALESCE(messages.internal_date, messages.updated_at) DESC, messages.message_id DESC
                         ) AS row_number,
                         COUNT(*) OVER (
                           PARTITION BY COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id)
                         ) AS thread_message_count
                  FROM gmail_messages AS messages
                  LEFT JOIN matter_members AS members
                    ON members.user_id = messages.user_id
                   AND members.generation_id = :generation_id
                   AND members.message_id = messages.message_id
                  LEFT JOIN message_semantics AS semantics
                    ON semantics.user_id = messages.user_id
                   AND semantics.generation_id = :generation_id
                   AND semantics.message_id = messages.message_id
                  WHERE messages.user_id = :user_id
                    AND members.message_id IS NULL
                    AND jsonb_exists(messages.label_ids_json::jsonb, 'INBOX')
                    AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'SPAM')
                    AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'TRASH')
                    AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'DRAFT')
                    AND (
                      NOT :has_query OR
                      COALESCE(messages.subject, '') ILIKE :query OR
                      COALESCE(messages.sender, '') ILIKE :query OR
                      COALESCE(messages.text_body, '') ILIKE :query OR
                      COALESCE(messages.snippet, '') ILIKE :query OR
                      COALESCE(semantics.extracted_facts -> 'counterpart_entities', '[]'::jsonb)::text ILIKE :query
                    )
                )
                SELECT * FROM unassigned
                WHERE row_number = 1
                ORDER BY COALESCE(internal_date, updated_at) DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()

    matters = []
    for row in matter_rows:
        counterpart_entities = aggregate_counterpart_entities(
            _json(row["counterpart_inputs"], []),
            context_text=" ".join(
                value for value in (str(row["dynamic_title"]), str(row["summary"])) if value
            ),
            owner_identity=owner_identity,
        )
        matters.append(
            {
                "id": str(row["id"]),
                "title": counterpart_aware_headline(
                    str(row["dynamic_title"]),
                    counterpart_entities,
                ),
                "summary": str(row["summary"]),
                "status": str(row["status"]),
                "confidence_state": str(row["confidence_state"]),
                "confidence": float(row["confidence"]),
                "latest_message_at": _iso(row["latest_message_at"]) or _iso(row["updated_at"]),
                "message_count": int(row["message_count"]),
                "unread": bool(row["unread"]),
                "starred": bool(row["starred"]),
                "participants": [str(item) for item in (row["participants"] or [])],
                "counterpart_entities": counterpart_entities,
                "evidence_message_ids": [str(item) for item in _json(row["evidence_message_ids"], [])],
                "revision": int(row["revision"]),
                "open_subgoal_count": int(row["open_subgoal_count"] or 0),
                "review_count": int(row["review_count"] or 0),
                "matching_message_ids": [str(item) for item in _json(row["matching_message_ids"], [])],
            }
        )
    organizing = []
    for row in organizing_rows:
        thread_id = str(row["gmail_thread_id"] or row["message_id"])
        failed = str(row["processing_state"] or "") == "failed"
        semantic_facts = _json(row["semantic_facts"], {})
        counterpart_entities = aggregate_counterpart_entities(
            [
                {
                    "sender": row["sender"],
                    "recipients": row["recipients_json"],
                    "label_ids": row["label_ids_json"],
                    "counterpart_entities": semantic_facts.get("counterpart_entities", []),
                    "latest_message_at": row["internal_date"] or row["updated_at"],
                }
            ],
            context_text=" ".join(
                value for value in (str(row["subject"] or ""), str(row["snippet"] or "")) if value
            ),
            owner_identity=owner_identity,
        )
        organizing.append(
            {
                "id": f"organizing:{thread_id}",
                "gmail_thread_id": thread_id,
                "title": str(row["subject"] or "No subject"),
                "sender": str(row["sender"]) if row["sender"] else None,
                "counterpart_entities": counterpart_entities,
                "snippet": str(row["snippet"]) if row["snippet"] else None,
                "latest_message_at": _iso(row["internal_date"]) or _iso(row["updated_at"]),
                "message_count": int(row["thread_message_count"]),
                "state": "failed" if failed else "organizing",
                "error": "Could not organize this message yet" if failed else None,
                "matching_message_ids": [str(row["message_id"])] if params["has_query"] else [],
            }
        )
    revision = f"{profile.get('revision', 0)}:{generation_id}:{max([item['revision'] for item in matters], default=0)}"
    return {"profile": profile, "generation_id": generation_id, "matters": matters, "organizing": organizing, "revision": revision}


def get_matter(
    database_url: str,
    *,
    user_id: str,
    matter_id: str,
    generation_id: str | None = None,
) -> tuple[dict[str, Any], list[str]] | None:
    with get_engine(database_url).connect() as connection:
        if generation_id is None:
            row = connection.execute(
                text(
                    """
                    SELECT matters.*
                    FROM matters
                    JOIN matter_profiles AS profiles
                      ON profiles.user_id = matters.user_id
                     AND profiles.active_generation_id = matters.generation_id
                    WHERE matters.user_id = :user_id AND matters.id = :matter_id
                    """
                ),
                {"user_id": user_id, "matter_id": matter_id},
            ).mappings().first()
        else:
            row = connection.execute(
                text(
                    """
                    SELECT matters.*
                    FROM matters
                    JOIN matter_generations AS generations
                      ON generations.id = matters.generation_id
                     AND generations.user_id = matters.user_id
                    WHERE matters.user_id = :user_id AND matters.id = :matter_id
                      AND matters.generation_id = :generation_id
                      AND generations.status IN ('shadow', 'building', 'active')
                    """
                ),
                {
                    "user_id": user_id,
                    "matter_id": matter_id,
                    "generation_id": generation_id,
                },
            ).mappings().first()
        if row is None:
            return None
        member_rows = connection.execute(
            text(
                """
                SELECT members.message_id
                FROM matter_members AS members
                JOIN gmail_messages AS messages
                  ON messages.user_id = members.user_id AND messages.message_id = members.message_id
                WHERE members.user_id = :user_id
                  AND members.generation_id = :generation_id
                  AND members.matter_id = :matter_id
                ORDER BY messages.internal_date ASC NULLS LAST, messages.created_at ASC, messages.message_id ASC
                """
            ),
            {"user_id": user_id, "generation_id": row["generation_id"], "matter_id": matter_id},
        ).mappings().all()
    return dict(row), [str(item["message_id"]) for item in member_rows]


def get_matter_context(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    matter_id: str,
    include_subgoals: bool = True,
    include_events: bool = True,
    include_review_proposals: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Return bounded derived context used by matter detail and review UI."""
    with get_engine(database_url).connect() as connection:
        subgoal_rows = []
        if include_subgoals:
            subgoal_rows = connection.execute(
                text(
                    """
                    SELECT id, goal, status, latest_development,
                           evidence_message_ids, revision
                    FROM matter_subgoals
                    WHERE user_id = :user_id AND generation_id = :generation_id
                      AND matter_id = :matter_id
                    ORDER BY created_at, id
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "matter_id": matter_id,
                },
            ).mappings().all()
        event_rows = []
        if include_events:
            event_rows = connection.execute(
                text(
                    """
                    SELECT
                      members.message_id,
                      COALESCE(messages.internal_date, messages.updated_at) AS occurred_at,
                      semantics.extracted_facts -> 'event' AS event
                    FROM matter_members AS members
                    JOIN gmail_messages AS messages
                      ON messages.user_id = members.user_id
                     AND messages.message_id = members.message_id
                    JOIN message_semantics AS semantics
                      ON semantics.user_id = members.user_id
                     AND semantics.generation_id = members.generation_id
                     AND semantics.message_id = members.message_id
                    WHERE members.user_id = :user_id
                      AND members.generation_id = :generation_id
                      AND members.matter_id = :matter_id
                      AND semantics.extracted_facts -> 'event' ->> 'role' IS NOT NULL
                    ORDER BY COALESCE(messages.internal_date, messages.updated_at), members.message_id
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "matter_id": matter_id,
                },
            ).mappings().all()
        proposal_rows = []
        if include_review_proposals:
            proposal_rows = connection.execute(
                text(
                    """
                SELECT
                  decisions.id,
                  members.message_id,
                  messages.subject,
                  messages.sender,
                  COALESCE(messages.internal_date, messages.updated_at) AS occurred_at,
                  decisions.constraints_json,
                  decisions.evidence_message_ids
                FROM matter_members AS members
                JOIN gmail_messages AS messages
                  ON messages.user_id = members.user_id
                 AND messages.message_id = members.message_id
                LEFT JOIN LATERAL (
                  SELECT model_decisions.*
                  FROM matter_decisions AS model_decisions
                  WHERE model_decisions.user_id = members.user_id
                    AND model_decisions.generation_id = members.generation_id
                    AND model_decisions.matter_id = members.matter_id
                    AND model_decisions.decision_type = 'model_assign'
                    AND model_decisions.message_ids ? members.message_id
                  ORDER BY model_decisions.created_at DESC
                  LIMIT 1
                ) AS decisions ON TRUE
                WHERE members.user_id = :user_id
                  AND members.generation_id = :generation_id
                  AND members.matter_id = :matter_id
                  AND members.provisional = TRUE
                ORDER BY COALESCE(messages.internal_date, messages.updated_at), members.message_id
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "matter_id": matter_id,
                },
            ).mappings().all()
    return {
        "subgoals": [dict(row) for row in subgoal_rows],
        "events": [dict(row) for row in event_rows],
        "review_proposals": [dict(row) for row in proposal_rows],
    }


def get_grouping_explanation(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    matter_id: str,
    message_id: str,
) -> dict[str, Any] | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                  members.message_id,
                  semantics.extracted_facts -> 'event' AS event,
                  decisions.constraints_json,
                  decisions.evidence_message_ids
                FROM matter_members AS members
                JOIN message_semantics AS semantics
                  ON semantics.user_id = members.user_id
                 AND semantics.generation_id = members.generation_id
                 AND semantics.message_id = members.message_id
                LEFT JOIN LATERAL (
                  SELECT model_decisions.*
                  FROM matter_decisions AS model_decisions
                  WHERE model_decisions.user_id = members.user_id
                    AND model_decisions.generation_id = members.generation_id
                    AND model_decisions.matter_id = members.matter_id
                    AND model_decisions.decision_type = 'model_assign'
                    AND model_decisions.message_ids ? members.message_id
                  ORDER BY model_decisions.created_at DESC
                  LIMIT 1
                ) AS decisions ON TRUE
                WHERE members.user_id = :user_id
                  AND members.generation_id = :generation_id
                  AND members.matter_id = :matter_id
                  AND members.message_id = :message_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "matter_id": matter_id,
                "message_id": message_id,
            },
        ).mappings().first()
    return dict(row) if row is not None else None


def begin_message_processing(
    settings: Settings,
    *,
    user_id: str,
    generation_id: str,
    message_id: str,
    content_revision: int,
    normalized_sha256: str,
) -> bool:
    engine = get_engine(str(settings.database_path))
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO message_semantics (
                  user_id, message_id, generation_id, content_revision,
                  normalized_sha256, processing_state, embedding_model, prompt_version
                ) VALUES (
                  :user_id, :message_id, :generation_id, :content_revision,
                  :normalized_sha256, 'processing', :embedding_model, :prompt_version
                )
                ON CONFLICT (user_id, message_id, generation_id) DO UPDATE SET
                  content_revision = EXCLUDED.content_revision,
                  normalized_sha256 = EXCLUDED.normalized_sha256,
                  processing_state = CASE
                    WHEN message_semantics.content_revision = EXCLUDED.content_revision
                     AND message_semantics.normalized_sha256 = EXCLUDED.normalized_sha256
                     AND message_semantics.processing_state = 'ready'
                    THEN 'ready'
                    ELSE 'processing'
                  END,
                  error_code = NULL,
                  updated_at = now()
                RETURNING processing_state
                """
            ),
            {
                "user_id": user_id,
                "message_id": message_id,
                "generation_id": generation_id,
                "content_revision": content_revision,
                "normalized_sha256": normalized_sha256,
                "embedding_model": settings.ai_inbox_embedding_model,
                "prompt_version": settings.ai_inbox_prompt_version,
            },
        ).mappings().one()
    return str(row["processing_state"]) != "ready"


def complete_message_semantics(
    settings: Settings,
    *,
    user_id: str,
    generation_id: str,
    message_id: str,
    reference_tokens: list[str],
    facts: dict[str, Any],
    embedding: list[float],
) -> None:
    engine = get_engine(str(settings.database_path))
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text(
                """
                UPDATE message_semantics
                SET reference_tokens = CAST(:reference_tokens AS jsonb),
                    extracted_facts = CAST(:facts AS jsonb),
                    embedding = CAST(:embedding AS vector),
                    processing_state = 'ready', error_code = NULL,
                    processed_at = now(), updated_at = now()
                WHERE user_id = :user_id AND generation_id = :generation_id AND message_id = :message_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "message_id": message_id,
                "reference_tokens": json.dumps(reference_tokens),
                "facts": json.dumps(facts),
                "embedding": "[" + ",".join(f"{value:.9g}" for value in embedding) + "]",
            },
        )


def update_message_counterpart_entities(
    settings: Settings,
    *,
    user_id: str,
    generation_id: str,
    message_id: str,
    counterpart_entities: list[str],
) -> None:
    """Attach validated display identities without changing semantic readiness."""
    engine = get_engine(str(settings.database_path))
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text(
                """
                UPDATE message_semantics
                SET extracted_facts = jsonb_set(
                      COALESCE(extracted_facts, '{}'::jsonb),
                      '{counterpart_entities}',
                      CAST(:counterpart_entities AS jsonb),
                      TRUE
                    ),
                    updated_at = now()
                WHERE user_id = :user_id
                  AND generation_id = :generation_id
                  AND message_id = :message_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "message_id": message_id,
                "counterpart_entities": json.dumps(counterpart_entities),
            },
        )


def upsert_attachment_extraction(
    database_url: str,
    *,
    user_id: str,
    message_id: str,
    attachment_id: str,
    content_revision: int,
    filename: str | None,
    mime_type: str | None,
    source_bytes: int,
    extracted_text: str | None,
    extracted_sha256: str | None,
    status: str,
    truncated: bool,
    error_code: str | None,
) -> None:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO attachment_text_extractions (
                  user_id, message_id, attachment_id, content_revision, filename,
                  mime_type, source_bytes, extracted_text, extracted_sha256,
                  status, truncated, error_code
                ) VALUES (
                  :user_id, :message_id, :attachment_id, :content_revision, :filename,
                  :mime_type, :source_bytes, :extracted_text, :extracted_sha256,
                  :status, :truncated, :error_code
                )
                ON CONFLICT (user_id, message_id, attachment_id) DO UPDATE SET
                  content_revision = EXCLUDED.content_revision,
                  filename = EXCLUDED.filename,
                  mime_type = EXCLUDED.mime_type,
                  source_bytes = EXCLUDED.source_bytes,
                  extracted_text = EXCLUDED.extracted_text,
                  extracted_sha256 = EXCLUDED.extracted_sha256,
                  status = EXCLUDED.status,
                  truncated = EXCLUDED.truncated,
                  error_code = EXCLUDED.error_code,
                  updated_at = now()
                """
            ),
            {
                "user_id": user_id, "message_id": message_id, "attachment_id": attachment_id,
                "content_revision": content_revision, "filename": filename, "mime_type": mime_type,
                "source_bytes": max(0, source_bytes), "extracted_text": extracted_text,
                "extracted_sha256": extracted_sha256, "status": status, "truncated": truncated,
                "error_code": error_code[:80] if error_code else None,
            },
        )


def fail_message_semantics(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    message_id: str,
    error_code: str,
) -> None:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text(
                """
                UPDATE message_semantics
                SET processing_state = 'failed', error_code = :error_code, updated_at = now()
                WHERE user_id = :user_id AND generation_id = :generation_id AND message_id = :message_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id, "message_id": message_id, "error_code": error_code[:80]},
        )


def candidate_matters(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    message_id: str,
    gmail_thread_id: str | None,
    reference_tokens: list[str],
    entity_tokens: list[str],
    embedding: list[float],
    lexical_query: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    vector = "[" + ",".join(f"{value:.9g}" for value in embedding) + "]"
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                WITH ranked AS (
                  SELECT
                    matters.*,
                    1 - (matters.embedding <=> CAST(:embedding AS vector)) AS vector_similarity,
                    CASE
                      WHEN btrim(:lexical_query) = '' THEN 0
                      ELSE ts_rank_cd(
                        to_tsvector('simple', matters.stable_goal || ' ' || matters.dynamic_title || ' ' || matters.summary),
                        plainto_tsquery('simple', left(:lexical_query, 4000))
                      )
                    END AS lexical_score,
                    GREATEST(
                      0,
                      EXTRACT(EPOCH FROM (now() - COALESCE(matters.latest_message_at, matters.created_at))) / 86400.0
                    ) AS days_since_latest,
                    EXISTS (
                      SELECT 1 FROM matter_members AS same_thread_members
                      JOIN gmail_messages AS same_thread_messages
                        ON same_thread_messages.user_id = same_thread_members.user_id
                       AND same_thread_messages.message_id = same_thread_members.message_id
                      WHERE same_thread_members.user_id = matters.user_id
                        AND same_thread_members.generation_id = matters.generation_id
                        AND same_thread_members.matter_id = matters.id
                        AND CAST(:gmail_thread_id AS text) IS NOT NULL
                        AND same_thread_messages.gmail_thread_id = CAST(:gmail_thread_id AS text)
                    ) AS same_gmail_thread,
                    EXISTS (
                      SELECT 1 FROM matter_members AS reference_members
                      JOIN message_semantics AS reference_semantics
                        ON reference_semantics.user_id = reference_members.user_id
                       AND reference_semantics.generation_id = reference_members.generation_id
                       AND reference_semantics.message_id = reference_members.message_id
                      WHERE reference_members.user_id = matters.user_id
                        AND reference_members.generation_id = matters.generation_id
                        AND reference_members.matter_id = matters.id
                        AND reference_semantics.reference_tokens ?| CAST(:reference_tokens AS text[])
                    ) AS exact_reference
                    , EXISTS (
                      SELECT 1 FROM matter_members AS entity_members
                      JOIN message_semantics AS entity_semantics
                        ON entity_semantics.user_id = entity_members.user_id
                       AND entity_semantics.generation_id = entity_members.generation_id
                       AND entity_semantics.message_id = entity_members.message_id
                      WHERE entity_members.user_id = matters.user_id
                        AND entity_members.generation_id = matters.generation_id
                        AND entity_members.matter_id = matters.id
                        AND COALESCE(
                          entity_semantics.extracted_facts -> 'entity_tokens',
                          '[]'::jsonb
                        ) ?| CAST(:entity_tokens AS text[])
                    ) AS exact_entity,
                    COALESCE(
                      (
                        SELECT jsonb_agg(
                          jsonb_build_object(
                            'id', subgoals.id,
                            'goal', subgoals.goal,
                            'status', subgoals.status,
                            'latest_development', subgoals.latest_development,
                            'evidence_message_ids', subgoals.evidence_message_ids
                          ) ORDER BY subgoals.created_at, subgoals.id
                        )
                        FROM matter_subgoals AS subgoals
                        WHERE subgoals.user_id = matters.user_id
                          AND subgoals.generation_id = matters.generation_id
                          AND subgoals.matter_id = matters.id
                      ),
                      '[]'::jsonb
                    ) AS subgoals,
                    COALESCE(
                      (
                        SELECT jsonb_agg(
                          jsonb_build_object(
                            'message_id', bounded_events.message_id,
                            'occurred_at', bounded_events.occurred_at,
                            'role', bounded_events.role,
                            'purpose', bounded_events.purpose,
                            'state_change', bounded_events.state_change
                          ) ORDER BY bounded_events.occurred_at, bounded_events.message_id
                        )
                        FROM (
                          SELECT * FROM (
                            SELECT
                              event_members.message_id,
                              COALESCE(event_messages.internal_date, event_messages.updated_at) AS occurred_at,
                              event_semantics.extracted_facts -> 'event' ->> 'role' AS role,
                              event_semantics.extracted_facts -> 'event' ->> 'purpose' AS purpose,
                              event_semantics.extracted_facts -> 'event' ->> 'state_change' AS state_change,
                              ROW_NUMBER() OVER (
                                ORDER BY COALESCE(event_messages.internal_date, event_messages.updated_at), event_members.message_id
                              ) AS oldest_rank,
                              ROW_NUMBER() OVER (
                                ORDER BY COALESCE(event_messages.internal_date, event_messages.updated_at) DESC, event_members.message_id DESC
                              ) AS latest_rank
                            FROM matter_members AS event_members
                            JOIN gmail_messages AS event_messages
                              ON event_messages.user_id = event_members.user_id
                             AND event_messages.message_id = event_members.message_id
                            JOIN message_semantics AS event_semantics
                              ON event_semantics.user_id = event_members.user_id
                             AND event_semantics.generation_id = event_members.generation_id
                             AND event_semantics.message_id = event_members.message_id
                            WHERE event_members.user_id = matters.user_id
                              AND event_members.generation_id = matters.generation_id
                              AND event_members.matter_id = matters.id
                              AND event_semantics.extracted_facts -> 'event' ->> 'role' IS NOT NULL
                          ) AS ranked_events
                          WHERE ranked_events.oldest_rank = 1 OR ranked_events.latest_rank <= 3
                        ) AS bounded_events
                      ),
                      '[]'::jsonb
                    ) AS event_history
                  FROM matters
                  WHERE matters.user_id = :user_id
                    AND matters.generation_id = :generation_id
                    AND matters.visible = TRUE
                    AND matters.embedding IS NOT NULL
                )
                SELECT * FROM ranked
                ORDER BY
                  exact_reference DESC,
                  same_gmail_thread DESC,
                  exact_entity DESC,
                  (
                    0.64 * vector_similarity
                    + 0.16 * LEAST(lexical_score, 1.0)
                    + 0.12 * CASE WHEN exact_entity THEN 1 ELSE 0 END
                    + 0.08 * GREATEST(0, 1 - LEAST(days_since_latest, 365) / 365.0)
                  ) DESC
                LIMIT :limit
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "message_id": message_id,
                "gmail_thread_id": gmail_thread_id,
                "reference_tokens": reference_tokens or ["__none__"],
                "entity_tokens": entity_tokens or ["__none__"],
                "embedding": vector,
                "lexical_query": lexical_query,
                "limit": max(1, min(limit, 20)),
            },
        ).mappings().all()
    return [dict(row) for row in rows]


def matter_reconciliation_candidates(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Return a bounded set of plausible duplicate matters for independent review."""
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                WITH matter_stats AS (
                  SELECT
                    matters.*,
                    COUNT(DISTINCT members.message_id)::INTEGER AS member_count,
                    BOOL_OR(members.membership_locked) AS has_locked_members,
                    ARRAY_AGG(DISTINCT members.message_id) AS message_ids,
                    ARRAY_REMOVE(
                      ARRAY_AGG(DISTINCT semantics.extracted_facts ->> 'sender_domain')
                        FILTER (WHERE semantics.extracted_facts ->> 'direction' = 'received'),
                      NULL
                    ) AS received_domains,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT reference_token.value), NULL) AS reference_tokens
                  FROM matters
                  JOIN matter_members AS members
                    ON members.user_id = matters.user_id
                   AND members.generation_id = matters.generation_id
                   AND members.matter_id = matters.id
                  LEFT JOIN message_semantics AS semantics
                    ON semantics.user_id = members.user_id
                   AND semantics.generation_id = members.generation_id
                   AND semantics.message_id = members.message_id
                  LEFT JOIN LATERAL jsonb_array_elements_text(
                    COALESCE(semantics.reference_tokens, '[]'::jsonb)
                  ) AS reference_token(value) ON TRUE
                  WHERE matters.user_id = :user_id
                    AND matters.generation_id = :generation_id
                    AND matters.visible = TRUE
                    AND matters.embedding IS NOT NULL
                  GROUP BY matters.id
                ), pair_features AS (
                  SELECT
                    left_matter.id AS left_id,
                    left_matter.stable_goal AS left_goal,
                    left_matter.dynamic_title AS left_title,
                    left_matter.summary AS left_summary,
                    left_matter.status AS left_status,
                    left_matter.confidence_state AS left_confidence_state,
                    left_matter.revision AS left_revision,
                    left_matter.member_count AS left_member_count,
                    left_matter.has_locked_members AS left_has_locked_members,
                    left_matter.latest_message_at AS left_latest_message_at,
                    right_matter.id AS right_id,
                    right_matter.stable_goal AS right_goal,
                    right_matter.dynamic_title AS right_title,
                    right_matter.summary AS right_summary,
                    right_matter.status AS right_status,
                    right_matter.confidence_state AS right_confidence_state,
                    right_matter.revision AS right_revision,
                    right_matter.member_count AS right_member_count,
                    right_matter.has_locked_members AS right_has_locked_members,
                    right_matter.latest_message_at AS right_latest_message_at,
                    1 - (left_matter.embedding <=> right_matter.embedding) AS vector_similarity,
                    COALESCE(left_matter.reference_tokens && right_matter.reference_tokens, FALSE) AS shared_reference,
                    COALESCE(left_matter.received_domains && right_matter.received_domains, FALSE) AS shared_received_domain,
                    left_matter.message_ids AS left_message_ids,
                    right_matter.message_ids AS right_message_ids
                  FROM matter_stats AS left_matter
                  JOIN matter_stats AS right_matter ON left_matter.id < right_matter.id
                  WHERE NOT (
                    left_matter.has_locked_members AND right_matter.has_locked_members
                  )
                )
                SELECT *
                FROM pair_features
                WHERE (
                    shared_reference
                    OR vector_similarity >= 0.90
                    OR (shared_received_domain AND vector_similarity >= 0.72)
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM matter_decisions AS decisions
                    WHERE decisions.user_id = :user_id
                      AND decisions.generation_id = :generation_id
                      AND decisions.decision_type = 'separate'
                      AND decisions.outcome = 'accepted'
                      AND (
                        (
                          decisions.message_ids ?| pair_features.left_message_ids
                          AND COALESCE(decisions.constraints_json -> 'other_message_ids', '[]'::jsonb)
                            ?| pair_features.right_message_ids
                        ) OR (
                          decisions.message_ids ?| pair_features.right_message_ids
                          AND COALESCE(decisions.constraints_json -> 'other_message_ids', '[]'::jsonb)
                            ?| pair_features.left_message_ids
                        )
                      )
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM matter_decisions AS reviews
                    WHERE reviews.user_id = :user_id
                      AND reviews.generation_id = :generation_id
                      AND reviews.decision_type = 'model_review'
                      AND reviews.outcome = 'rejected'
                      AND reviews.matter_id = pair_features.left_id
                      AND reviews.target_matter_id = pair_features.right_id
                      AND reviews.expected_revision = pair_features.left_revision
                      AND COALESCE((reviews.constraints_json ->> 'target_revision')::BIGINT, -1)
                        = pair_features.right_revision
                  )
                ORDER BY vector_similarity DESC, shared_reference DESC,
                         (left_has_locked_members OR right_has_locked_members) DESC,
                         (left_member_count + right_member_count) DESC
                LIMIT :limit
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "limit": max(1, min(limit, 100)),
            },
        ).mappings().all()
    return [dict(row) for row in rows]


def record_reconciliation_rejection(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    left_matter_id: str,
    right_matter_id: str,
    left_revision: int,
    right_revision: int,
    model: str,
    confidence: float,
    decision_basis: dict[str, Any] | None = None,
) -> None:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO matter_decisions (
                  id, user_id, generation_id, decision_type, matter_id,
                  target_matter_id, message_ids, constraints_json, outcome,
                  expected_revision, model, confidence
                ) VALUES (
                  :id, :user_id, :generation_id, 'model_review', :left_matter_id,
                  :right_matter_id, '[]'::jsonb, CAST(:constraints AS jsonb),
                  'rejected', :left_revision, :model, :confidence
                )
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "generation_id": generation_id,
                "left_matter_id": left_matter_id,
                "right_matter_id": right_matter_id,
                "constraints": json.dumps(
                    {
                        "target_revision": right_revision,
                        "decision_basis": decision_basis or {},
                    }
                ),
                "left_revision": left_revision,
                "model": model,
                "confidence": max(0.0, min(float(confidence), 1.0)),
            },
        )


def merge_reconciled_matters(
    settings: Settings,
    *,
    user_id: str,
    generation_id: str,
    source_matter_id: str,
    target_matter_id: str,
    stable_goal: str,
    title: str,
    summary: str,
    status: str,
    evidence_message_ids: list[str],
    confidence: float,
    decision_basis: dict[str, Any],
) -> bool:
    """Merge one automatic matter into another without moving locked members."""
    engine = get_engine(str(settings.database_path))
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        matters = connection.execute(
            text(
                """
                SELECT id, visible
                FROM matters
                WHERE user_id = :user_id AND generation_id = :generation_id
                  AND id = ANY(:matter_ids)
                ORDER BY id
                FOR UPDATE
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "matter_ids": [source_matter_id, target_matter_id],
            },
        ).mappings().all()
        if len(matters) != 2 or not all(bool(item["visible"]) for item in matters):
            return False
        source_ids = [
            str(row["message_id"])
            for row in connection.execute(
                text(
                    """
                    SELECT message_id
                    FROM matter_members
                    WHERE user_id = :user_id AND generation_id = :generation_id
                      AND matter_id = :matter_id
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "matter_id": source_matter_id,
                },
            ).mappings().all()
        ]
        target_ids = [
            str(row["message_id"])
            for row in connection.execute(
                text(
                    """
                    SELECT message_id
                    FROM matter_members
                    WHERE user_id = :user_id AND generation_id = :generation_id
                      AND matter_id = :matter_id
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "matter_id": target_matter_id,
                },
            ).mappings().all()
        ]
        if not source_ids or not target_ids:
            return False
        source_locked = connection.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM matter_members
                  WHERE user_id = :user_id AND generation_id = :generation_id
                    AND matter_id = :matter_id AND membership_locked = TRUE
                )
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "matter_id": source_matter_id,
            },
        ).scalar_one()
        if bool(source_locked):
            return False
        prohibited = connection.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM matter_decisions AS decisions
                  WHERE decisions.user_id = :user_id
                    AND decisions.generation_id = :generation_id
                    AND decisions.decision_type = 'separate'
                    AND decisions.outcome = 'accepted'
                    AND (
                      (
                        decisions.message_ids ?| CAST(:source_ids AS text[])
                        AND COALESCE(decisions.constraints_json -> 'other_message_ids', '[]'::jsonb)
                          ?| CAST(:target_ids AS text[])
                      ) OR (
                        decisions.message_ids ?| CAST(:target_ids AS text[])
                        AND COALESCE(decisions.constraints_json -> 'other_message_ids', '[]'::jsonb)
                          ?| CAST(:source_ids AS text[])
                      )
                    )
                )
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "source_ids": source_ids,
                "target_ids": target_ids,
            },
        ).scalar_one()
        if bool(prohibited):
            return False
        connection.execute(
            text(
                """
                UPDATE matter_members
                SET matter_id = :target_matter_id
                WHERE user_id = :user_id AND generation_id = :generation_id
                  AND matter_id = :source_matter_id
                  AND membership_locked = FALSE
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "source_matter_id": source_matter_id,
                "target_matter_id": target_matter_id,
            },
        )
        source_subgoals = connection.execute(
            text(
                """
                SELECT * FROM matter_subgoals
                WHERE user_id = :user_id AND generation_id = :generation_id
                  AND matter_id = :source_matter_id
                ORDER BY created_at, id
                FOR UPDATE
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "source_matter_id": source_matter_id,
            },
        ).mappings().all()
        for subgoal in source_subgoals:
            existing_subgoal = connection.execute(
                text(
                    """
                    SELECT * FROM matter_subgoals
                    WHERE user_id = :user_id AND generation_id = :generation_id
                      AND matter_id = :target_matter_id
                      AND canonical_key = :canonical_key
                    FOR UPDATE
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "target_matter_id": target_matter_id,
                    "canonical_key": subgoal["canonical_key"],
                },
            ).mappings().first()
            if existing_subgoal is None:
                connection.execute(
                    text(
                        """
                        UPDATE matter_subgoals SET matter_id = :target_matter_id, updated_at = now()
                        WHERE id = :id AND user_id = :user_id
                        """
                    ),
                    {
                        "target_matter_id": target_matter_id,
                        "id": subgoal["id"],
                        "user_id": user_id,
                    },
                )
                continue
            source_is_newer = subgoal["updated_at"] >= existing_subgoal["updated_at"]
            combined_status = str(
                subgoal["status"] if source_is_newer else existing_subgoal["status"]
            )
            combined_development = str(
                subgoal["latest_development"]
                if source_is_newer
                else existing_subgoal["latest_development"]
            )
            connection.execute(
                text(
                    """
                    UPDATE matter_subgoals
                    SET status = :status,
                        latest_development = :latest_development,
                        evidence_message_ids = (
                          SELECT COALESCE(jsonb_agg(DISTINCT evidence_id), '[]'::jsonb)
                          FROM jsonb_array_elements_text(
                            matter_subgoals.evidence_message_ids || CAST(:evidence AS jsonb)
                          ) AS evidence(evidence_id)
                        ),
                        revision = revision + 1,
                        updated_at = now()
                    WHERE id = :id AND user_id = :user_id
                    """
                ),
                {
                    "status": combined_status,
                    "latest_development": combined_development[:1000],
                    "evidence": json.dumps(_json(subgoal["evidence_message_ids"], [])),
                    "id": existing_subgoal["id"],
                    "user_id": user_id,
                },
            )
            connection.execute(
                text("DELETE FROM matter_subgoals WHERE id = :id AND user_id = :user_id"),
                {"id": subgoal["id"], "user_id": user_id},
            )
        merged_status = _derived_matter_status(
            (
                str(row["status"])
                for row in connection.execute(
                    text(
                        """
                        SELECT status FROM matter_subgoals
                        WHERE user_id = :user_id AND generation_id = :generation_id
                          AND matter_id = :target_matter_id
                        """
                    ),
                    {
                        "user_id": user_id,
                        "generation_id": generation_id,
                        "target_matter_id": target_matter_id,
                    },
                ).mappings().all()
            ),
            status,
        )
        connection.execute(
            text(
                """
                UPDATE matters
                SET stable_goal = :stable_goal,
                    dynamic_title = :title,
                    summary = :summary,
                    status = :status,
                    evidence_message_ids = CAST(:evidence AS jsonb),
                    confidence = :confidence,
                    confidence_state = CASE
                      WHEN EXISTS (
                        SELECT 1 FROM matter_members
                        WHERE user_id = :user_id AND generation_id = :generation_id
                          AND matter_id = :target_matter_id AND membership_locked = TRUE
                      ) THEN 'confirmed'
                      ELSE 'automatic'
                    END,
                    embedding = (
                      SELECT AVG(semantics.embedding)
                      FROM matter_members AS members
                      JOIN message_semantics AS semantics
                        ON semantics.user_id = members.user_id
                       AND semantics.generation_id = members.generation_id
                       AND semantics.message_id = members.message_id
                      WHERE members.user_id = :user_id
                        AND members.generation_id = :generation_id
                        AND members.matter_id = :target_matter_id
                        AND semantics.embedding IS NOT NULL
                    ),
                    review_model = :review_model,
                    prompt_version = :prompt_version,
                    latest_message_at = (
                      SELECT MAX(COALESCE(messages.internal_date, messages.updated_at))
                      FROM matter_members AS members
                      JOIN gmail_messages AS messages
                        ON messages.user_id = members.user_id
                       AND messages.message_id = members.message_id
                      WHERE members.user_id = :user_id
                        AND members.generation_id = :generation_id
                        AND members.matter_id = :target_matter_id
                    ),
                    revision = revision + 1,
                    updated_at = now()
                WHERE id = :target_matter_id AND user_id = :user_id
                  AND generation_id = :generation_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "target_matter_id": target_matter_id,
                "stable_goal": stable_goal[:1000],
                "title": title[:300],
                "summary": summary[:3000],
                "status": merged_status,
                "evidence": json.dumps(list(dict.fromkeys(evidence_message_ids))[:20]),
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "review_model": settings.ai_inbox_review_model,
                "prompt_version": settings.ai_inbox_prompt_version,
            },
        )
        connection.execute(
            text(
                """
                UPDATE matters
                SET visible = FALSE, revision = revision + 1, updated_at = now()
                WHERE id = :source_matter_id AND user_id = :user_id
                  AND generation_id = :generation_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "source_matter_id": source_matter_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO matter_decisions (
                  id, user_id, generation_id, decision_type, matter_id,
                  target_matter_id, message_ids, constraints_json, outcome, model, confidence,
                  evidence_message_ids
                ) VALUES (
                  :id, :user_id, :generation_id, 'model_review', :source_matter_id,
                  :target_matter_id, CAST(:message_ids AS jsonb), CAST(:constraints AS jsonb), 'accepted',
                  :model, :confidence, CAST(:evidence AS jsonb)
                )
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "generation_id": generation_id,
                "source_matter_id": source_matter_id,
                "target_matter_id": target_matter_id,
                "message_ids": json.dumps(source_ids),
                "constraints": json.dumps(decision_basis),
                "model": settings.ai_inbox_review_model,
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "evidence": json.dumps(list(dict.fromkeys(evidence_message_ids))[:20]),
            },
        )
        connection.execute(
            text(
                """
                UPDATE matter_profiles
                SET revision = revision + 1, updated_at = now()
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )
    return True


def separated_matter_ids_for_message(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    message_id: str,
) -> set[str]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                WITH prohibited_pairs AS (
                  SELECT selected_id, other_id
                  FROM matter_decisions AS decisions
                  CROSS JOIN LATERAL jsonb_array_elements_text(decisions.message_ids) AS selected(selected_id)
                  CROSS JOIN LATERAL jsonb_array_elements_text(
                    COALESCE(decisions.constraints_json -> 'other_message_ids', '[]'::jsonb)
                  ) AS other(other_id)
                  WHERE decisions.user_id = :user_id
                    AND decisions.decision_type = 'separate'
                    AND decisions.outcome = 'accepted'
                ), prohibited_partners AS (
                  SELECT other_id AS message_id FROM prohibited_pairs WHERE selected_id = :message_id
                  UNION
                  SELECT selected_id AS message_id FROM prohibited_pairs WHERE other_id = :message_id
                )
                SELECT DISTINCT members.matter_id
                FROM matter_members AS members
                JOIN prohibited_partners AS partners ON partners.message_id = members.message_id
                WHERE members.user_id = :user_id AND members.generation_id = :generation_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id, "message_id": message_id},
        ).mappings().all()
    return {str(row["matter_id"]) for row in rows}


def apply_matter_assignment(
    settings: Settings,
    *,
    user_id: str,
    generation_id: str,
    message_id: str,
    selected_matter_id: str | None,
    stable_goal: str,
    title: str,
    summary: str,
    status: str,
    subgoals: list[dict[str, Any]],
    event: dict[str, Any],
    decision_basis: dict[str, Any],
    evidence_message_ids: list[str],
    confidence: float,
    provisional: bool,
    membership_source: str,
    embedding: list[float],
    review_model: str | None,
) -> str:
    engine = get_engine(str(settings.database_path))
    matter_id = selected_matter_id or str(uuid4())
    vector = "[" + ",".join(f"{value:.9g}" for value in embedding) + "]"
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        locked = connection.execute(
            text(
                """
                SELECT matter_id FROM matter_members
                WHERE user_id = :user_id AND generation_id = :generation_id
                  AND message_id = :message_id AND membership_locked = TRUE
                """
            ),
            {"user_id": user_id, "generation_id": generation_id, "message_id": message_id},
        ).scalar_one_or_none()
        if locked is not None:
            # Preserve the user's locked membership while still rebuilding the
            # message event, subgoal state, headline, and explanation for the
            # new prompt generation. Returning here would leave confirmed
            # matters permanently outside the event-chain model.
            matter_id = str(locked)
            selected_matter_id = matter_id
            provisional = False
            membership_source = "user"
        connection.execute(
            text(
                """
                INSERT INTO matters (
                  id, user_id, generation_id, stable_goal, dynamic_title, summary,
                  status, evidence_message_ids, confidence, confidence_state,
                  embedding, classifier_model, review_model, prompt_version,
                  latest_message_at
                )
                SELECT
                  :matter_id, :user_id, :generation_id, :stable_goal, :title, :summary,
                  :status, CAST(:evidence AS jsonb), :confidence, :confidence_state,
                  CAST(:embedding AS vector), :classifier_model, :review_model,
                  :prompt_version, COALESCE(messages.internal_date, messages.updated_at)
                FROM gmail_messages AS messages
                WHERE messages.user_id = :user_id AND messages.message_id = :message_id
                ON CONFLICT (id) DO UPDATE SET
                  stable_goal = matters.stable_goal,
                  dynamic_title = CASE WHEN EXCLUDED.confidence_state = 'provisional' THEN matters.dynamic_title ELSE EXCLUDED.dynamic_title END,
                  summary = CASE WHEN EXCLUDED.confidence_state = 'provisional' THEN matters.summary ELSE EXCLUDED.summary END,
                  status = CASE WHEN EXCLUDED.confidence_state = 'provisional' THEN matters.status ELSE EXCLUDED.status END,
                  evidence_message_ids = CASE
                    WHEN EXCLUDED.confidence_state = 'provisional' THEN matters.evidence_message_ids
                    ELSE (
                      SELECT COALESCE(jsonb_agg(DISTINCT evidence_id), '[]'::jsonb)
                      FROM jsonb_array_elements_text(
                        matters.evidence_message_ids || EXCLUDED.evidence_message_ids
                      ) AS evidence(evidence_id)
                    )
                  END,
                  confidence = CASE WHEN EXCLUDED.confidence_state = 'provisional' THEN matters.confidence ELSE EXCLUDED.confidence END,
                  confidence_state = CASE
                    WHEN EXCLUDED.confidence_state = 'provisional' THEN 'provisional'
                    WHEN matters.confidence_state = 'confirmed' THEN 'confirmed'
                    ELSE EXCLUDED.confidence_state
                  END,
                  embedding = matters.embedding,
                  classifier_model = CASE WHEN EXCLUDED.confidence_state = 'provisional' THEN matters.classifier_model ELSE EXCLUDED.classifier_model END,
                  review_model = CASE WHEN EXCLUDED.confidence_state = 'provisional' THEN matters.review_model ELSE EXCLUDED.review_model END,
                  prompt_version = CASE WHEN EXCLUDED.confidence_state = 'provisional' THEN matters.prompt_version ELSE EXCLUDED.prompt_version END,
                  latest_message_at = GREATEST(matters.latest_message_at, EXCLUDED.latest_message_at),
                  revision = matters.revision + 1,
                  updated_at = now()
                """
            ),
            {
                "matter_id": matter_id,
                "user_id": user_id,
                "generation_id": generation_id,
                "message_id": message_id,
                "stable_goal": stable_goal[:1000],
                "title": title[:300],
                "summary": summary[:3000],
                "status": status,
                "evidence": json.dumps(list(dict.fromkeys([*evidence_message_ids, message_id]))),
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "confidence_state": "provisional" if provisional else "automatic",
                "embedding": vector,
                "classifier_model": settings.ai_inbox_classifier_model,
                "review_model": review_model,
                "prompt_version": settings.ai_inbox_prompt_version,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO matter_members (
                  user_id, generation_id, matter_id, message_id,
                  membership_source, confidence, provisional
                ) VALUES (
                  :user_id, :generation_id, :matter_id, :message_id,
                  :membership_source, :confidence, :provisional
                )
                ON CONFLICT (user_id, generation_id, message_id) DO UPDATE SET
                  matter_id = CASE WHEN matter_members.membership_locked THEN matter_members.matter_id ELSE EXCLUDED.matter_id END,
                  membership_source = CASE WHEN matter_members.membership_locked THEN matter_members.membership_source ELSE EXCLUDED.membership_source END,
                  confidence = CASE WHEN matter_members.membership_locked THEN matter_members.confidence ELSE EXCLUDED.confidence END,
                  provisional = CASE WHEN matter_members.membership_locked THEN matter_members.provisional ELSE EXCLUDED.provisional END
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "matter_id": matter_id,
                "message_id": message_id,
                "membership_source": membership_source,
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "provisional": provisional,
            },
        )
        if not provisional:
            existing_subgoals = {
                str(row["id"]): dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT * FROM matter_subgoals
                        WHERE user_id = :user_id AND generation_id = :generation_id
                          AND matter_id = :matter_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "user_id": user_id,
                        "generation_id": generation_id,
                        "matter_id": matter_id,
                    },
                ).mappings().all()
            }
            existing_by_key = {
                str(row["canonical_key"]): row for row in existing_subgoals.values()
            }
            processed_keys: set[str] = set()
            allowed_evidence_ids = {
                str(row["message_id"])
                for row in connection.execute(
                    text(
                        """
                        SELECT message_id FROM matter_members
                        WHERE user_id = :user_id AND generation_id = :generation_id
                          AND matter_id = :matter_id
                        """
                    ),
                    {
                        "user_id": user_id,
                        "generation_id": generation_id,
                        "matter_id": matter_id,
                    },
                ).mappings().all()
            }
            for subgoal in subgoals[:12]:
                goal = str(subgoal.get("goal") or "").strip()
                latest_development = str(subgoal.get("latest_development") or "").strip()
                subgoal_status = str(subgoal.get("status") or "")
                if not goal or not latest_development or subgoal_status not in {
                    "needs_you",
                    "waiting_on_others",
                    "in_progress",
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    continue
                canonical_key = _canonical_subgoal_key(goal)
                if canonical_key in processed_keys:
                    continue
                processed_keys.add(canonical_key)
                requested_id = str(subgoal.get("existing_subgoal_id") or "")
                existing = existing_subgoals.get(requested_id) or existing_by_key.get(canonical_key)
                subgoal_id = str(existing["id"]) if existing else str(uuid4())
                valid_evidence = [
                    str(value)
                    for value in dict.fromkeys(subgoal.get("evidence_message_ids") or [])
                    if str(value) in allowed_evidence_ids
                ]
                if message_id not in valid_evidence:
                    valid_evidence.append(message_id)
                connection.execute(
                    text(
                        """
                        INSERT INTO matter_subgoals (
                          id, user_id, generation_id, matter_id, canonical_key,
                          goal, status, latest_development, evidence_message_ids
                        ) VALUES (
                          :id, :user_id, :generation_id, :matter_id, :canonical_key,
                          :goal, :status, :latest_development, CAST(:evidence AS jsonb)
                        )
                        ON CONFLICT (id) DO UPDATE SET
                          goal = EXCLUDED.goal,
                          status = EXCLUDED.status,
                          latest_development = EXCLUDED.latest_development,
                          evidence_message_ids = (
                            SELECT COALESCE(jsonb_agg(DISTINCT evidence_id), '[]'::jsonb)
                            FROM jsonb_array_elements_text(
                              matter_subgoals.evidence_message_ids || EXCLUDED.evidence_message_ids
                            ) AS evidence(evidence_id)
                          ),
                          revision = matter_subgoals.revision + 1,
                          updated_at = now()
                        """
                    ),
                    {
                        "id": subgoal_id,
                        "user_id": user_id,
                        "generation_id": generation_id,
                        "matter_id": matter_id,
                        "canonical_key": canonical_key,
                        "goal": goal[:600],
                        "status": subgoal_status,
                        "latest_development": latest_development[:1000],
                        "evidence": json.dumps(valid_evidence[:20]),
                    },
                )
            derived_status = _derived_matter_status(
                (
                    str(row["status"])
                    for row in connection.execute(
                        text(
                            """
                            SELECT status FROM matter_subgoals
                            WHERE user_id = :user_id AND generation_id = :generation_id
                              AND matter_id = :matter_id
                            """
                        ),
                        {
                            "user_id": user_id,
                            "generation_id": generation_id,
                            "matter_id": matter_id,
                        },
                    ).mappings().all()
                ),
                status,
            )
            connection.execute(
                text(
                    """
                    UPDATE matters SET status = :status, updated_at = now()
                    WHERE id = :matter_id AND user_id = :user_id
                      AND generation_id = :generation_id
                    """
                ),
                {
                    "status": derived_status,
                    "matter_id": matter_id,
                    "user_id": user_id,
                    "generation_id": generation_id,
                },
            )
        # Keep retrieval representative of the whole matter. Freezing the
        # first message's vector causes later workflow stages (request -> offer
        # -> completion) to drift away from the candidate they belong to.
        connection.execute(
            text(
                """
                UPDATE matters
                SET embedding = (
                      SELECT AVG(semantics.embedding)
                      FROM matter_members AS members
                      JOIN message_semantics AS semantics
                        ON semantics.user_id = members.user_id
                       AND semantics.generation_id = members.generation_id
                       AND semantics.message_id = members.message_id
                      WHERE members.user_id = :user_id
                        AND members.generation_id = :generation_id
                        AND members.matter_id = :matter_id
                        AND semantics.embedding IS NOT NULL
                    ),
                    updated_at = now()
                WHERE id = :matter_id AND user_id = :user_id
                  AND generation_id = :generation_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "matter_id": matter_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO matter_decisions (
                  id, user_id, generation_id, decision_type, matter_id, message_ids,
                  constraints_json, outcome, model, confidence, evidence_message_ids
                ) VALUES (
                  :id, :user_id, :generation_id, 'model_assign', :matter_id,
                  CAST(:message_ids AS jsonb), CAST(:constraints AS jsonb),
                  :outcome, :model, :confidence,
                  CAST(:evidence AS jsonb)
                )
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "generation_id": generation_id,
                "matter_id": matter_id,
                "message_ids": json.dumps([message_id]),
                "constraints": json.dumps(
                    {
                        "schema_version": "grouping-decision-v1",
                        "event": event,
                        "decision_basis": decision_basis,
                        "selected_matter_id": selected_matter_id,
                        "review_model": review_model,
                    }
                ),
                "outcome": "provisional" if provisional else "accepted",
                "model": settings.ai_inbox_classifier_model,
                "confidence": confidence,
                "evidence": json.dumps(evidence_message_ids),
            },
        )
    return matter_id


def record_usage(
    database_url: str,
    *,
    user_id: str,
    generation_id: str | None,
    message_id: str | None,
    operation: str,
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    estimated_cost_usd: float,
    success: bool,
    error_code: str | None = None,
) -> None:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO ai_usage_events (
                  id, user_id, generation_id, message_id, operation, model,
                  input_tokens, cached_input_tokens, output_tokens, latency_ms,
                  estimated_cost_usd, success, error_code
                ) VALUES (
                  :id, :user_id, :generation_id, :message_id, :operation, :model,
                  :input_tokens, :cached_input_tokens, :output_tokens, :latency_ms,
                  :estimated_cost_usd, :success, :error_code
                )
                """
            ),
            {
                "id": str(uuid4()), "user_id": user_id, "generation_id": generation_id,
                "message_id": message_id, "operation": operation, "model": model,
                "input_tokens": max(0, input_tokens), "cached_input_tokens": max(0, cached_input_tokens),
                "output_tokens": max(0, output_tokens), "latency_ms": max(0, latency_ms),
                "estimated_cost_usd": max(0.0, estimated_cost_usd), "success": success,
                "error_code": error_code[:80] if error_code else None,
            },
        )


def monthly_usage(database_url: str, *, user_id: str | None = None) -> float:
    sql = "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM ai_usage_events WHERE created_at >= date_trunc('month', now())"
    params: dict[str, Any] = {}
    if user_id is not None:
        sql += " AND user_id = :user_id"
        params["user_id"] = user_id
    with get_engine(database_url).connect() as connection:
        return float(connection.execute(text(sql), params).scalar_one() or 0)


def generation_progress(database_url: str, *, user_id: str) -> dict[str, Any]:
    profile = get_profile(database_url, user_id=user_id)
    generation_id = profile.get("active_generation_id")
    if not profile.get("enabled") or not generation_id:
        return {"generation_id": None, "phase": "disabled", "recent_total": 0, "recent_processed": 0, "history_total": 0, "history_processed": 0, "last_error": None}
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE COALESCE(messages.internal_date, messages.created_at) >= now() - interval '30 days')::INTEGER AS recent_total,
                  COUNT(*) FILTER (WHERE COALESCE(messages.internal_date, messages.created_at) >= now() - interval '30 days' AND semantics.processing_state IN ('ready', 'skipped'))::INTEGER AS recent_processed,
                  COUNT(*) FILTER (WHERE COALESCE(messages.internal_date, messages.created_at) < now() - interval '30 days')::INTEGER AS history_total,
                  COUNT(*) FILTER (WHERE COALESCE(messages.internal_date, messages.created_at) < now() - interval '30 days' AND semantics.processing_state IN ('ready', 'skipped'))::INTEGER AS history_processed,
                  MAX(semantics.error_code) FILTER (WHERE semantics.processing_state = 'failed') AS last_error
                FROM gmail_messages AS messages
                LEFT JOIN message_semantics AS semantics
                  ON semantics.user_id = messages.user_id
                 AND semantics.message_id = messages.message_id
                 AND semantics.generation_id = :generation_id
                WHERE messages.user_id = :user_id
                  AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'SPAM')
                  AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'TRASH')
                  AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'DRAFT')
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).mappings().one()
    values = {key: int(row[key] or 0) for key in ("recent_total", "recent_processed", "history_total", "history_processed")}
    if row["last_error"] and values["recent_processed"] == 0:
        phase = "failed"
    elif values["recent_processed"] < values["recent_total"]:
        phase = "organizing_recent"
    elif values["history_processed"] < values["history_total"]:
        phase = "organizing_history"
    else:
        phase = "ready"
    return {"generation_id": generation_id, "phase": phase, **values, "last_error": str(row["last_error"]) if row["last_error"] else None}


def list_generation_message_ids(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    recent: bool,
    limit: int,
    offset: int,
    all_history: bool = False,
) -> list[tuple[str, int]]:
    comparison = ">=" if recent else "<"
    date_filter = (
        ""
        if all_history
        else f"AND COALESCE(messages.internal_date, messages.created_at) {comparison} now() - interval '30 days'"
    )
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT messages.message_id, messages.content_revision
                FROM gmail_messages AS messages
                LEFT JOIN message_semantics AS semantics
                  ON semantics.user_id = messages.user_id
                 AND semantics.message_id = messages.message_id
                 AND semantics.generation_id = :generation_id
                WHERE messages.user_id = :user_id
                  {date_filter}
                  AND COALESCE(messages.body_fetch_status, 'missing') IN ('fetched', 'unavailable')
                  AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'SPAM')
                  AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'TRASH')
                  AND NOT jsonb_exists(messages.label_ids_json::jsonb, 'DRAFT')
                  AND (semantics.message_id IS NULL OR semantics.content_revision <> messages.content_revision OR semantics.processing_state = 'failed')
                ORDER BY COALESCE(messages.internal_date, messages.created_at) ASC, messages.message_id
                LIMIT :limit OFFSET :offset
                """
            ),
            {"user_id": user_id, "generation_id": generation_id, "limit": limit, "offset": offset},
        ).mappings().all()
    return [(str(row["message_id"]), int(row["content_revision"] or 1)) for row in rows]


def count_generation_jobs(database_url: str, *, user_id: str, generation_id: str) -> int:
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT COUNT(*) FROM background_jobs
                WHERE user_id = :user_id AND kind = 'ai_message_organize'
                  AND status IN ('queued', 'running')
                  AND payload_json::jsonb ->> 'generation_id' = :generation_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).scalar_one()
    return int(value)


def active_member_snapshot(database_url: str, *, user_id: str, matter_id: str) -> dict[str, Any] | None:
    result = get_matter(database_url, user_id=user_id, matter_id=matter_id)
    if result is None:
        return None
    matter, message_ids = result
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT message_id, COALESCE(NULLIF(gmail_thread_id, ''), message_id) AS gmail_thread_id
                FROM gmail_messages
                WHERE user_id = :user_id AND message_id = ANY(:message_ids)
                """
            ),
            {"user_id": user_id, "message_ids": message_ids},
        ).mappings().all()
    return {"matter": matter, "message_ids": message_ids, "thread_ids": list(dict.fromkeys(str(row["gmail_thread_id"]) for row in rows))}


def apply_user_decision(
    database_url: str,
    *,
    user_id: str,
    client_decision_id: str,
    decision: str,
    matter_id: str,
    target_matter_id: str | None,
    message_ids: list[str],
    expected_revision: int,
) -> dict[str, Any]:
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        existing = connection.execute(
            text("SELECT * FROM matter_decisions WHERE user_id = :user_id AND idempotency_key = :key"),
            {"user_id": user_id, "key": client_decision_id},
        ).mappings().first()
        if existing is not None:
            return {"decision_id": str(existing["id"]), "state": str(existing["outcome"]), "matter_ids": [matter_id] + ([target_matter_id] if target_matter_id else [])}
        matter = connection.execute(
            text("SELECT * FROM matters WHERE user_id = :user_id AND id = :matter_id FOR UPDATE"),
            {"user_id": user_id, "matter_id": matter_id},
        ).mappings().first()
        if matter is None:
            raise LookupError("Matter not found")
        if int(matter["revision"]) != expected_revision:
            return {"decision_id": "", "state": "conflict", "matter_ids": [matter_id]}
        generation_id = str(matter["generation_id"])
        selected_ids = list(dict.fromkeys(message_ids))
        decision_constraints: dict[str, Any] = {"never_recombine": False}
        if decision == "confirm":
            if not selected_ids:
                selected_ids = [
                    str(row["message_id"])
                    for row in connection.execute(
                        text(
                            """
                            SELECT message_id FROM matter_members
                            WHERE user_id = :user_id AND generation_id = :generation_id
                              AND matter_id = :matter_id AND provisional = TRUE
                            """
                        ),
                        {
                            "user_id": user_id,
                            "generation_id": generation_id,
                            "matter_id": matter_id,
                        },
                    ).mappings().all()
                ]
            if not selected_ids:
                raise ValueError("Select at least one proposed message to confirm")
            connection.execute(
                text(
                    """
                    UPDATE matter_members SET provisional = FALSE, user_confirmed = TRUE, membership_locked = TRUE, membership_source = 'user'
                    WHERE user_id = :user_id AND generation_id = :generation_id AND matter_id = :matter_id
                      AND message_id = ANY(:message_ids)
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "matter_id": matter_id,
                    "message_ids": selected_ids,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE matters SET
                      confidence_state = CASE
                        WHEN EXISTS (
                          SELECT 1 FROM matter_members
                          WHERE user_id = :user_id AND generation_id = :generation_id
                            AND matter_id = :matter_id AND provisional = TRUE
                        ) THEN 'provisional'
                        ELSE 'confirmed'
                      END,
                      revision = revision + 1,
                      updated_at = now()
                    WHERE id = :matter_id AND user_id = :user_id
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "matter_id": matter_id,
                },
            )
        elif decision == "separate":
            if not selected_ids:
                selected_ids = [str(row["message_id"]) for row in connection.execute(text("SELECT message_id FROM matter_members WHERE user_id = :user_id AND generation_id = :generation_id AND matter_id = :matter_id AND provisional = TRUE"), {"user_id": user_id, "generation_id": generation_id, "matter_id": matter_id}).mappings().all()]
            if not selected_ids:
                raise ValueError("Select at least one message to separate")
            other_message_ids = [
                str(row["message_id"])
                for row in connection.execute(
                    text(
                        """
                        SELECT message_id FROM matter_members
                        WHERE user_id = :user_id AND generation_id = :generation_id
                          AND matter_id = :matter_id
                          AND NOT (message_id = ANY(:message_ids))
                        """
                    ),
                    {
                        "user_id": user_id,
                        "generation_id": generation_id,
                        "matter_id": matter_id,
                        "message_ids": selected_ids,
                    },
                ).mappings().all()
            ]
            decision_constraints = {
                "never_recombine": True,
                "other_message_ids": other_message_ids,
            }
            # This is an explicit user correction, so it is allowed to move a
            # previously confirmed member. The automatic assignment path still
            # refuses to move any locked member.
            connection.execute(text("DELETE FROM matter_members WHERE user_id = :user_id AND generation_id = :generation_id AND matter_id = :matter_id AND message_id = ANY(:message_ids)"), {"user_id": user_id, "generation_id": generation_id, "matter_id": matter_id, "message_ids": selected_ids})
            connection.execute(text("UPDATE message_semantics SET processing_state = 'failed', error_code = 'user_separated', updated_at = now() WHERE user_id = :user_id AND generation_id = :generation_id AND message_id = ANY(:message_ids)"), {"user_id": user_id, "generation_id": generation_id, "message_ids": selected_ids})
            connection.execute(
                text(
                    """
                    UPDATE matters
                    SET confidence_state = CASE
                          WHEN EXISTS (
                            SELECT 1 FROM matter_members
                            WHERE matter_members.user_id = :user_id
                              AND matter_members.generation_id = :generation_id
                              AND matter_members.matter_id = :matter_id
                              AND matter_members.membership_locked = TRUE
                          ) THEN 'confirmed'
                          ELSE 'automatic'
                        END,
                        visible = EXISTS (
                          SELECT 1 FROM matter_members
                          WHERE matter_members.user_id = :user_id
                            AND matter_members.generation_id = :generation_id
                            AND matter_members.matter_id = :matter_id
                        ),
                        revision = revision + 1,
                        updated_at = now()
                    WHERE id = :matter_id AND user_id = :user_id AND generation_id = :generation_id
                    """
                ),
                {"user_id": user_id, "generation_id": generation_id, "matter_id": matter_id},
            )
        elif decision in {"merge", "move"}:
            if not target_matter_id:
                raise ValueError("A target matter is required")
            target = connection.execute(text("SELECT id FROM matters WHERE user_id = :user_id AND generation_id = :generation_id AND id = :target_id FOR UPDATE"), {"user_id": user_id, "generation_id": generation_id, "target_id": target_matter_id}).scalar_one_or_none()
            if target is None:
                raise LookupError("Target matter not found")
            if not selected_ids or decision == "merge":
                selected_ids = [str(row["message_id"]) for row in connection.execute(text("SELECT message_id FROM matter_members WHERE user_id = :user_id AND generation_id = :generation_id AND matter_id = :matter_id"), {"user_id": user_id, "generation_id": generation_id, "matter_id": matter_id}).mappings().all()]
            connection.execute(text("UPDATE matter_members SET matter_id = :target_id, membership_source = 'user', provisional = FALSE, user_confirmed = TRUE, membership_locked = TRUE WHERE user_id = :user_id AND generation_id = :generation_id AND matter_id = :matter_id AND message_id = ANY(:message_ids)"), {"user_id": user_id, "generation_id": generation_id, "matter_id": matter_id, "target_id": target_matter_id, "message_ids": selected_ids})
            connection.execute(text("UPDATE matters SET confidence_state = 'confirmed', revision = revision + 1, updated_at = now() WHERE id = :target_id"), {"target_id": target_matter_id})
            connection.execute(text("UPDATE matters SET visible = EXISTS (SELECT 1 FROM matter_members WHERE matter_id = :matter_id), revision = revision + 1, updated_at = now() WHERE id = :matter_id"), {"matter_id": matter_id})
        decision_id = str(uuid4())
        connection.execute(
            text(
                """
                INSERT INTO matter_decisions (
                  id, user_id, generation_id, decision_type, matter_id, target_matter_id,
                  message_ids, constraints_json, outcome, expected_revision, idempotency_key
                ) VALUES (
                  :id, :user_id, :generation_id, :decision, :matter_id, :target_matter_id,
                  CAST(:message_ids AS jsonb), CAST(:constraints AS jsonb), 'accepted',
                  :expected_revision, :idempotency_key
                )
                """
            ),
            {"id": decision_id, "user_id": user_id, "generation_id": generation_id, "decision": decision, "matter_id": matter_id, "target_matter_id": target_matter_id, "message_ids": json.dumps(selected_ids), "constraints": json.dumps(decision_constraints), "expected_revision": expected_revision, "idempotency_key": client_decision_id},
        )
        profile_revision = connection.execute(text("UPDATE matter_profiles SET revision = revision + 1, updated_at = now() WHERE user_id = :user_id RETURNING revision"), {"user_id": user_id}).scalar_one()
    return {"decision_id": decision_id, "state": "applied", "matter_ids": [matter_id] + ([target_matter_id] if target_matter_id else []), "message_ids": selected_ids, "revision": str(profile_revision)}


def delete_ai_data(database_url: str, *, user_id: str) -> None:
    """Delete only OpenAI-derived state; canonical Gmail data is untouched."""
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        # Generation cascade removes matters, members, semantics, decisions,
        # and usage. Attachment extraction is message-scoped, so remove it
        # explicitly before the profile/generation roots.
        connection.execute(
            text("DELETE FROM attachment_text_extractions WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        connection.execute(
            text("DELETE FROM matter_profiles WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        connection.execute(
            text("DELETE FROM matter_generations WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
