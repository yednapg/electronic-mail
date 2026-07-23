from __future__ import annotations

"""Fast local mailbox search with durable Gmail-authority hydration."""

from hashlib import sha256

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.services.gmail_importer import hydrate_gmail_search_results
from app.services.mailbox_events import MAILBOX_SEARCH_HYDRATED, emit_mailbox_event


def mailbox_search_key(*, query: str, label: str) -> str:
    """Return the cross-platform identity for one exact normalized search."""
    normalized_query = query.strip()[:200]
    normalized_label = label.strip().lower() or "all"
    material = f"{normalized_label}\0{normalized_query}".encode("utf-8")
    return sha256(material).hexdigest()


def enqueue_mailbox_search_hydration(
    settings: Settings,
    *,
    user_id: str,
    query: str,
    label: str,
    limit: int,
) -> str | None:
    """Persist one deduplicated authority refresh without delaying local results."""
    normalized_query = query.strip()[:200]
    if not normalized_query:
        return None
    normalized_label = label.strip().lower() or "all"
    search_key = mailbox_search_key(query=normalized_query, label=normalized_label)
    enqueue_job(
        str(settings.database_path),
        kind="gmail_search_hydrate",
        queue="reader",
        user_id=user_id,
        dedupe_key=f"gmail-search:{user_id}:{search_key}",
        priority=60,
        max_attempts=2,
        payload={
            "user_id": user_id,
            "query": normalized_query,
            "label": normalized_label,
            "limit": max(1, min(int(limit), 200)),
            "search_key": search_key,
        },
    )
    return search_key


def run_mailbox_search_hydration(
    settings: Settings,
    *,
    user_id: str,
    query: str,
    label: str,
    limit: int,
    search_key: str,
) -> int:
    """Hydrate missing authoritative matches and notify only the originating search."""
    expected_key = mailbox_search_key(query=query, label=label)
    if not search_key or search_key != expected_key:
        raise RuntimeError("gmail_search_hydrate has an invalid search key")
    hydrated_count = hydrate_gmail_search_results(
        settings,
        user_id=user_id,
        query=query,
        label=label,
        limit=max(1, min(int(limit), 200)),
    )
    if hydrated_count > 0:
        emit_mailbox_event(
            settings,
            user_id=user_id,
            event_type=MAILBOX_SEARCH_HYDRATED,
            mailbox_label=label,
            payload={
                "source": "gmail_search_hydration",
                "search_key": search_key,
                "hydrated_message_count": hydrated_count,
            },
        )
    return hydrated_count
