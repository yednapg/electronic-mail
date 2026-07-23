from __future__ import annotations

"""Fast local mailbox search with durable Gmail-authority hydration."""

from hashlib import sha256
import hmac
import re
from typing import Any

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.services.gmail_importer import (
    GMAIL_BACKFILL_JOB_PRIORITY,
    GMAIL_SEARCH_MAX_PAGES,
    GMAIL_SEARCH_PAGE_SIZE,
    hydrate_gmail_search_results,
)
from app.services.mailbox_events import MAILBOX_SEARCH_HYDRATED, emit_mailbox_event


GMAIL_SEARCH_JOB_PRIORITY = GMAIL_BACKFILL_JOB_PRIORITY + 10
GMAIL_SEARCH_MAX_CONTINUATION_JOBS = 100
GMAIL_SEARCH_MAX_PAGE_TOKEN_LENGTH = 2048
_SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")


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
    _enqueue_mailbox_search_job(
        settings,
        user_id=user_id,
        query=normalized_query,
        label=normalized_label,
        response_limit=max(1, min(int(limit), 200)),
        search_key=search_key,
    )
    return search_key


def _enqueue_mailbox_search_job(
    settings: Settings,
    *,
    user_id: str,
    query: str,
    label: str,
    response_limit: int,
    search_key: str,
    page_token: str = "",
    continuation_index: int = 0,
    continuation_key: str = "",
    continuation_token_hashes: tuple[str, ...] = (),
) -> None:
    payload: dict[str, Any] = {
        "user_id": user_id,
        "query": query,
        "label": label,
        # Authority hydration is intentionally independent of the UI page
        # size. Each slow job walks one bounded Gmail continuation segment.
        "limit": GMAIL_SEARCH_PAGE_SIZE,
        "max_pages": GMAIL_SEARCH_MAX_PAGES,
        "response_limit": max(1, min(int(response_limit), 200)),
        "search_key": search_key,
    }
    dedupe_key = f"gmail-search:{user_id}:{search_key}"
    if page_token:
        payload.update(
            {
                "page_token": page_token,
                "continuation_index": continuation_index,
                "continuation_key": continuation_key,
                "continuation_token_hashes": list(continuation_token_hashes),
            }
        )
        dedupe_key = f"{dedupe_key}:continuation:{continuation_key}"
    enqueue_job(
        str(settings.database_path),
        kind="gmail_search_hydrate",
        # Search authority hydration can download hundreds of full messages.
        # Keep it off the latency-sensitive reader queue used to open threads.
        queue="slow",
        user_id=user_id,
        dedupe_key=dedupe_key,
        # A backfill continuation yields after every bounded page. Search must
        # win the next slow-worker claim so first-run backfill cannot starve an
        # interactive body-only lookup for hours.
        priority=GMAIL_SEARCH_JOB_PRIORITY,
        max_attempts=2,
        payload=payload,
    )


def run_mailbox_search_hydration(
    settings: Settings,
    *,
    user_id: str,
    query: str,
    label: str,
    limit: int,
    search_key: str,
    max_pages: int = GMAIL_SEARCH_MAX_PAGES,
    response_limit: int = 200,
    page_token: object = None,
    continuation_index: object = 0,
    continuation_key: object = "",
    continuation_token_hashes: object = None,
) -> int:
    """Hydrate missing authoritative matches and notify only the originating search."""
    expected_key = mailbox_search_key(query=query, label=label)
    if not search_key or search_key != expected_key:
        raise RuntimeError("gmail_search_hydrate has an invalid search key")
    validated_page_token, validated_continuation_index, validated_token_hashes = _validated_continuation(
        user_id=user_id,
        search_key=search_key,
        page_token=page_token,
        continuation_index=continuation_index,
        continuation_key=continuation_key,
        continuation_token_hashes=continuation_token_hashes,
    )
    page_notification_count = 0

    def notify_page(page_hydrated_count: int) -> None:
        nonlocal page_notification_count
        page_notification_count += 1
        emit_mailbox_event(
            settings,
            user_id=user_id,
            event_type=MAILBOX_SEARCH_HYDRATED,
            mailbox_label=label,
            payload={
                "source": "gmail_search_hydration",
                "search_key": search_key,
                "hydrated_message_count": max(0, int(page_hydrated_count)),
            },
        )

    def enqueue_continuation(next_page_token: str) -> None:
        next_page_token = _validated_page_token(next_page_token)
        next_continuation_index = validated_continuation_index + 1
        if next_continuation_index > GMAIL_SEARCH_MAX_CONTINUATION_JOBS:
            raise RuntimeError("gmail_search_hydrate exceeded its continuation safety limit")
        next_token_hash = _page_token_hash(next_page_token)
        if next_token_hash in validated_token_hashes:
            raise RuntimeError("gmail_search_hydrate repeated a continuation page token")
        next_token_hashes = (*validated_token_hashes, next_token_hash)
        next_continuation_key = _continuation_key(
            user_id=user_id,
            search_key=search_key,
            page_token=next_page_token,
            continuation_index=next_continuation_index,
            continuation_token_hashes=next_token_hashes,
        )
        _enqueue_mailbox_search_job(
            settings,
            user_id=user_id,
            query=query,
            label=label,
            response_limit=response_limit,
            search_key=search_key,
            page_token=next_page_token,
            continuation_index=next_continuation_index,
            continuation_key=next_continuation_key,
            continuation_token_hashes=next_token_hashes,
        )

    hydrated_count = hydrate_gmail_search_results(
        settings,
        user_id=user_id,
        query=query,
        label=label,
        limit=max(1, min(int(limit), GMAIL_SEARCH_PAGE_SIZE)),
        max_pages=max(1, min(int(max_pages), GMAIL_SEARCH_MAX_PAGES)),
        page_token=validated_page_token or None,
        on_page_hydrated=notify_page,
        on_continuation=enqueue_continuation,
    )
    # Retain compatibility with a test double or alternate importer that does
    # not invoke the progress hook. Production calls notify once per page.
    if page_notification_count == 0:
        notify_page(hydrated_count)
    return hydrated_count


def _validated_continuation(
    *,
    user_id: str,
    search_key: str,
    page_token: object,
    continuation_index: object,
    continuation_key: object,
    continuation_token_hashes: object,
) -> tuple[str, int, tuple[str, ...]]:
    if page_token is None or page_token == "":
        if (
            not (
                continuation_index is None
                or (isinstance(continuation_index, int) and not isinstance(continuation_index, bool) and continuation_index == 0)
            )
            or continuation_key not in (None, "")
            or continuation_token_hashes not in (None, (), [])
        ):
            raise RuntimeError("gmail_search_hydrate has invalid initial continuation state")
        return "", 0, ()
    if not isinstance(page_token, str):
        raise RuntimeError("gmail_search_hydrate has an invalid continuation page token")
    validated_page_token = _validated_page_token(page_token)
    if isinstance(continuation_index, bool) or not isinstance(continuation_index, int):
        raise RuntimeError("gmail_search_hydrate has an invalid continuation index")
    if continuation_index < 1 or continuation_index > GMAIL_SEARCH_MAX_CONTINUATION_JOBS:
        raise RuntimeError("gmail_search_hydrate has an invalid continuation index")
    if not isinstance(continuation_token_hashes, (list, tuple)):
        raise RuntimeError("gmail_search_hydrate has invalid continuation token history")
    token_hashes = tuple(continuation_token_hashes)
    if (
        len(token_hashes) != continuation_index
        or any(not isinstance(item, str) or _SHA256_HEX_PATTERN.fullmatch(item) is None for item in token_hashes)
        or len(set(token_hashes)) != len(token_hashes)
        or token_hashes[-1] != _page_token_hash(validated_page_token)
    ):
        raise RuntimeError("gmail_search_hydrate has invalid continuation token history")
    if not isinstance(continuation_key, str) or _SHA256_HEX_PATTERN.fullmatch(continuation_key) is None:
        raise RuntimeError("gmail_search_hydrate has an invalid continuation key")
    expected_key = _continuation_key(
        user_id=user_id,
        search_key=search_key,
        page_token=validated_page_token,
        continuation_index=continuation_index,
        continuation_token_hashes=token_hashes,
    )
    if not hmac.compare_digest(continuation_key, expected_key):
        raise RuntimeError("gmail_search_hydrate has an invalid continuation key")
    return validated_page_token, continuation_index, token_hashes


def _validated_page_token(value: str) -> str:
    if (
        not value
        or len(value) > GMAIL_SEARCH_MAX_PAGE_TOKEN_LENGTH
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RuntimeError("gmail_search_hydrate has an invalid continuation page token")
    return value


def _page_token_hash(page_token: str) -> str:
    return sha256(page_token.encode("utf-8")).hexdigest()


def _continuation_key(
    *,
    user_id: str,
    search_key: str,
    page_token: str,
    continuation_index: int,
    continuation_token_hashes: tuple[str, ...],
) -> str:
    material = "\0".join(
        [user_id, search_key, str(continuation_index), page_token, *continuation_token_hashes]
    ).encode("utf-8")
    return sha256(material).hexdigest()
