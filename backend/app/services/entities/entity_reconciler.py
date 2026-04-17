from __future__ import annotations

"""Entity-to-entity reconciliation for complaint chains that span multiple threads."""

from dataclasses import dataclass

from app.db.models import LoadedEntity
from app.db.repository import append_trace_record, list_all_loaded_entities, merge_entities
from app.schemas.ai import EntityGroupingRequest
from app.services.ai.decision import resolve_entity_group
from app.services.entities.entity_resolver import (
    build_entity_summary,
    extract_named_markers,
    extract_issue_markers,
    extract_reference_ids,
    extract_message_reference_ids,
    get_sender_organization,
    get_support_confidence,
    payload_string,
)

LOW_SIGNAL_ORGANIZATIONS = {
    "gmail",
    "googlemail",
    "rameshpandey",
    "rbi",
}


@dataclass
class EntityMergeProfile:
    """Compact per-entity view used during reconciliation."""

    loaded: LoadedEntity
    latest_subject: str
    summary: str
    reference_ids: set[str]
    issue_markers: set[str]
    named_markers: set[str]
    sender_organizations: set[str]
    latest_timestamp: str


def reconcile_entities(database_path: str) -> list[str]:
    """Merge entities that later evidence proves belong to one real-world task."""
    changed_entity_ids: set[str] = set()

    while True:
        profiles = [build_merge_profile(entity) for entity in list_all_loaded_entities(database_path)]
        merge_plan = find_ai_merge(profiles)

        if merge_plan is None:
            break

        target, source, confidence, method, details = merge_plan
        merge_entities(database_path, target.loaded.entity.id, source.loaded.entity.id)
        append_trace_record(
            database_path,
            stage="grouping",
            user_id="local-user",
            entity_id=target.loaded.entity.id,
            trace_id=target.loaded.entity.id,
            input={
                "source_entity_id": source.loaded.entity.id,
                "target_entity_id": target.loaded.entity.id,
                **details,
            },
            output={
                "entity_id": target.loaded.entity.id,
                "confidence": confidence,
                "method": method,
            },
        )
        changed_entity_ids.add(target.loaded.entity.id)

    return sorted(changed_entity_ids)


def build_merge_profile(entity: LoadedEntity) -> EntityMergeProfile:
    """Summarize an entity into reusable merge signals."""
    summary = build_entity_summary(entity)
    latest_subject = entity.members[-1].subject or payload_string(entity.members[-1].raw_payload, "subject") if entity.members else ""
    latest_timestamp = entity.members[-1].timestamp if entity.members else entity.entity.updated_at
    reference_ids: set[str] = set()
    issue_markers: set[str] = set()
    named_markers: set[str] = set()
    sender_organizations: set[str] = set()

    for record in entity.members:
        subject = record.subject or payload_string(record.raw_payload, "subject")
        body = payload_string(record.raw_payload, "body")
        reference_ids.update(extract_message_reference_ids(subject or "", body))
        issue_markers.update(extract_issue_markers(subject or "", body))
        organization = get_sender_organization(record.sender or "")
        if organization:
            sender_organizations.add(organization)
        named_markers.update(extract_named_markers(subject or "", body, organization))

    reference_ids.update(extract_reference_ids(summary))
    issue_markers.update(extract_issue_markers(summary, ""))
    named_markers.update(extract_named_markers(summary, ""))

    return EntityMergeProfile(
        loaded=entity,
        latest_subject=latest_subject or "Untitled",
        summary=summary,
        reference_ids=reference_ids,
        issue_markers=issue_markers,
        named_markers=named_markers,
        sender_organizations=sender_organizations,
        latest_timestamp=latest_timestamp,
    )
def find_ai_merge(
    profiles: list[EntityMergeProfile],
) -> tuple[EntityMergeProfile, EntityMergeProfile, float, str, dict[str, object]] | None:
    """Ask the grouping model to merge semantically identical entities."""
    by_id = {profile.loaded.entity.id: profile for profile in profiles}
    ordered = sorted(profiles, key=lambda profile: profile.loaded.entity.created_at, reverse=True)

    for source in ordered:
        candidate_profiles = []
        source_summary_fingerprint = fingerprint_text(source.summary)
        source_meaningful_organizations = get_meaningful_organizations(source)

        for candidate in profiles:
            if candidate.loaded.entity.id == source.loaded.entity.id:
                continue

            support_confidence = get_support_confidence(source.summary, candidate.summary)
            shared_reference_ids = sorted(source.reference_ids & candidate.reference_ids)
            shared_issue_markers = sorted(source.issue_markers & candidate.issue_markers)
            shared_named_markers = sorted(source.named_markers & candidate.named_markers)
            candidate_meaningful_organizations = get_meaningful_organizations(candidate)
            shared_sender_organizations = sorted(source_meaningful_organizations & candidate_meaningful_organizations)
            both_low_signal_only = not source_meaningful_organizations and not candidate_meaningful_organizations
            source_brand_bridge = any(
                organization in candidate.named_markers for organization in source_meaningful_organizations
            )
            candidate_brand_bridge = any(
                organization in source.named_markers for organization in candidate_meaningful_organizations
            )
            source_mentions_candidate = any(
                organization in source_summary_fingerprint
                for organization in candidate_meaningful_organizations
            )
            candidate_mentions_source = any(
                organization in fingerprint_text(candidate.summary)
                for organization in source_meaningful_organizations
            )

            if (
                not shared_reference_ids
                and not shared_named_markers
                and support_confidence < 0.62
                and len(shared_issue_markers) < 2
                and not shared_sender_organizations
                and not source_mentions_candidate
                and not candidate_mentions_source
            ):
                continue
            if (
                source.reference_ids
                and candidate.reference_ids
                and not shared_reference_ids
                and not shared_named_markers
                and support_confidence < 0.78
                and len(shared_issue_markers) < 3
            ):
                continue
            if (
                not shared_sender_organizations
                and not shared_reference_ids
                and not shared_named_markers
                and not source_mentions_candidate
                and not candidate_mentions_source
                and len(shared_issue_markers) < 2
            ):
                continue
            if both_low_signal_only and not shared_reference_ids and not shared_named_markers:
                continue
            if (
                source_meaningful_organizations
                and not shared_sender_organizations
                and not source_brand_bridge
                and not shared_reference_ids
            ):
                continue
            if (
                candidate_meaningful_organizations
                and not shared_sender_organizations
                and not candidate_brand_bridge
                and not shared_reference_ids
            ):
                continue

            candidate_profiles.append(
                (
                    candidate,
                    shared_reference_ids,
                    shared_named_markers,
                    support_confidence,
                    shared_issue_markers,
                    shared_sender_organizations,
                )
            )

        if not candidate_profiles:
            continue

        candidate_profiles.sort(
            key=lambda item: (
                len(item[1]),
                len(item[2]),
                item[3],
                len(item[4]),
                len(item[5]),
                item[0].latest_timestamp,
            ),
            reverse=True,
        )
        request = EntityGroupingRequest(
            subject=source.latest_subject,
            snippet=source.summary,
            reference_ids=sorted(source.reference_ids),
            named_markers=sorted(source.named_markers),
            candidates=[
                {
                    "entity_id": candidate.loaded.entity.id,
                    "canonical_key": candidate.loaded.entity.canonical_key,
                    "latest_subject": candidate.latest_subject,
                    "latest_sender": candidate.loaded.members[-1].sender if candidate.loaded.members else None,
                    "current_state": candidate.loaded.state.current_state if candidate.loaded.state is not None else None,
                    "summary": candidate.summary,
                    "reference_ids": sorted(candidate.reference_ids),
                    "named_markers": sorted(candidate.named_markers),
                }
                for candidate, _shared_reference_ids, _shared_named_markers, _support_confidence, _shared_issue_markers, _shared_sender_organizations in candidate_profiles[:5]
            ],
        )
        resolution = resolve_entity_group(request)

        if resolution.entity_id is None or resolution.confidence <= 0.92:
            continue

        target = by_id.get(resolution.entity_id)

        if target is None:
            continue

        if target.loaded.entity.id == source.loaded.entity.id:
            continue

        ordered_target, ordered_source = choose_merge_direction(target, source)
        return (
            ordered_target,
            ordered_source,
            float(resolution.confidence),
            "entity_ai_merge",
            {
                "shared_reference_ids": sorted(source.reference_ids & target.reference_ids),
                "source_summary": source.summary[:280],
                "target_summary": target.summary[:280],
            },
        )

    return None


def choose_merge_direction(
    left: EntityMergeProfile,
    right: EntityMergeProfile,
) -> tuple[EntityMergeProfile, EntityMergeProfile]:
    """Prefer the older entity as the surviving container for merged threads."""
    if left.loaded.entity.created_at < right.loaded.entity.created_at:
        return left, right
    if right.loaded.entity.created_at < left.loaded.entity.created_at:
        return right, left

    if len(left.loaded.members) >= len(right.loaded.members):
        return left, right

    return right, left


def get_meaningful_organizations(profile: EntityMergeProfile) -> set[str]:
    """Drop low-signal sender organizations before semantic merge checks."""
    return {
        organization
        for organization in profile.sender_organizations
        if organization not in LOW_SIGNAL_ORGANIZATIONS
    }


def fingerprint_text(value: str) -> str:
    """Normalize text so organization mentions compare across spaces and punctuation."""
    return "".join(character.lower() for character in value if character.isalnum())
