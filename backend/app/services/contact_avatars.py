from __future__ import annotations

"""Optional saved-Google-Contact avatar resolution and bounded caching."""

from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
import logging
from typing import Iterable

from sqlalchemy import text

from app.core.config import Settings
from app.db.repository import get_engine
from app.services.integrations.google import (
    GOOGLE_CONTACTS_READ_SCOPE,
    build_google_service,
    create_authorized_credentials,
    missing_google_scopes,
)
from app.services.remote_images import RemoteImageBlocked, create_remote_image_asset_id


logger = logging.getLogger(__name__)
POSITIVE_CACHE_TTL = timedelta(hours=24)
NEGATIVE_CACHE_TTL = timedelta(hours=6)


def normalize_contact_email(value: str | None) -> str | None:
    address = parseaddr(value or "")[1].strip().lower()
    if not address or "@" not in address or len(address) > 320:
        return None
    return address


def resolve_contact_avatar_asset_ids(
    settings: Settings,
    *,
    user_id: str,
    sender_values: Iterable[str | None],
) -> dict[str, str]:
    """Return opaque avatar asset IDs keyed by normalized sender email."""
    emails = sorted({email for value in sender_values if (email := normalize_contact_email(value))})
    if not emails or not getattr(settings, "app_encryption_key", None):
        return {}

    cached, stale = _load_cached(settings, user_id=user_id, emails=emails)
    if stale and not missing_google_scopes(
        settings,
        user_id=user_id,
        required_scopes=[GOOGLE_CONTACTS_READ_SCOPE],
    ):
        try:
            contact_index = _fetch_saved_contact_index(settings, user_id=user_id)
            _store_resolutions(
                settings,
                user_id=user_id,
                resolutions={email: contact_index.get(email) for email in stale},
            )
            cached, _ = _load_cached(settings, user_id=user_id, emails=emails)
        except Exception:
            # Contact photos are deliberately best-effort. Mail reading and
            # sending must never fail because People API is unavailable.
            logger.info("Google Contact photos unavailable for user_id=%s", user_id, exc_info=True)

    assets: dict[str, str] = {}
    for email, source_url in cached.items():
        if not source_url:
            continue
        try:
            assets[email] = create_remote_image_asset_id(
                settings,
                user_id=user_id,
                message_id=f"contact-avatar:{email}",
                source_url=source_url,
            )
        except RemoteImageBlocked:
            continue
    return assets


def contact_avatar_asset_is_owned(
    settings: Settings,
    *,
    user_id: str,
    normalized_email: str,
    source_url: str,
) -> bool:
    with get_engine(str(settings.database_path)).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT 1
                FROM google_contact_avatar_cache
                WHERE user_id = :user_id
                  AND normalized_email = :email
                  AND source_url = :source_url
                """
            ),
            {"user_id": user_id, "email": normalized_email, "source_url": source_url},
        ).fetchone()
    return row is not None


def _load_cached(
    settings: Settings,
    *,
    user_id: str,
    emails: list[str],
) -> tuple[dict[str, str | None], list[str]]:
    cached: dict[str, str | None] = {}
    with get_engine(str(settings.database_path)).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT normalized_email, source_url
                FROM google_contact_avatar_cache
                WHERE user_id = :user_id
                  AND normalized_email = ANY(:emails)
                  AND expires_at > now()
                """
            ),
            {"user_id": user_id, "emails": emails},
        ).fetchall()
    for row in rows:
        cached[str(row[0])] = str(row[1]) if row[1] else None
    return cached, [email for email in emails if email not in cached]


def _store_resolutions(
    settings: Settings,
    *,
    user_id: str,
    resolutions: dict[str, str | None],
) -> None:
    now = datetime.now(timezone.utc)
    with get_engine(str(settings.database_path)).begin() as connection:
        for email, source_url in resolutions.items():
            expires_at = now + (POSITIVE_CACHE_TTL if source_url else NEGATIVE_CACHE_TTL)
            connection.execute(
                text(
                    """
                    INSERT INTO google_contact_avatar_cache (
                      user_id, normalized_email, source_url, expires_at, updated_at
                    ) VALUES (
                      :user_id, :email, :source_url, :expires_at, :updated_at
                    )
                    ON CONFLICT (user_id, normalized_email) DO UPDATE SET
                      source_url = EXCLUDED.source_url,
                      expires_at = EXCLUDED.expires_at,
                      updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "user_id": user_id,
                    "email": email,
                    "source_url": source_url,
                    "expires_at": expires_at,
                    "updated_at": now,
                },
            )


def _fetch_saved_contact_index(settings: Settings, *, user_id: str) -> dict[str, str]:
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        return {}
    people = build_google_service("people", "v1", credentials)
    index: dict[str, str] = {}
    page_token: str | None = None
    while True:
        response = (
            people.people()
            .connections()
            .list(
                resourceName="people/me",
                personFields="emailAddresses,photos",
                pageSize=1000,
                pageToken=page_token,
            )
            .execute()
        )
        for person in response.get("connections", []):
            photos = person.get("photos") or []
            source_url = next(
                (
                    str(photo.get("url"))
                    for photo in photos
                    if photo.get("url") and photo.get("default") is not True
                ),
                None,
            )
            if not source_url:
                continue
            for item in person.get("emailAddresses") or []:
                email = normalize_contact_email(str(item.get("value") or ""))
                if email:
                    index.setdefault(email, source_url)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return index
