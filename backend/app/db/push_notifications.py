from __future__ import annotations

"""Push tokens are encrypted and bound to the session that registered them."""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import text

from app.db.account_scope import active_gmail_account_id
from app.db.repository import get_engine
from app.db.user_mail_guard import user_mail_write_transaction
from app.services.token_crypto import encrypt_json


def register_device(settings, *, device_id: str, user_id: str, session_id: str, registration) -> None:
    token_hash = hashlib.sha256(registration.token.encode()).hexdigest()
    values = registration.model_dump(exclude={"token"}) | {
        "id": device_id, "user_id": user_id, "session_id": session_id,
        "token_hash": token_hash, "token_encrypted": encrypt_json(settings, {"token": registration.token}),
    }
    with get_engine(settings.database_path).begin() as connection:
        # Serialize token rotation/reinstallation. The same APNs address must
        # never retain a second registration belonging to an older session.
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:token_hash, 0))"), values)
        connection.execute(text("""
            DELETE FROM push_devices WHERE platform=:platform AND environment=:environment
              AND token_hash=:token_hash AND id<>:id
        """), values)
        connection.execute(text("""
            INSERT INTO push_devices (id, user_id, session_id, platform, environment,
                token_hash, token_encrypted, enabled, sound_enabled, preview_enabled)
            VALUES (:id, :user_id, :session_id, :platform, :environment,
                :token_hash, :token_encrypted, :enabled, :sound_enabled, :preview_enabled)
            ON CONFLICT (id) DO UPDATE SET user_id=EXCLUDED.user_id,
                session_id=EXCLUDED.session_id, platform=EXCLUDED.platform,
                environment=EXCLUDED.environment, token_hash=EXCLUDED.token_hash,
                token_encrypted=EXCLUDED.token_encrypted, enabled=EXCLUDED.enabled,
                sound_enabled=EXCLUDED.sound_enabled, preview_enabled=EXCLUDED.preview_enabled,
                updated_at=now()
        """), values)


def delete_device(database_url: str, *, device_id: str, user_id: str, session_id: str) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(text("DELETE FROM push_devices WHERE id=:id AND user_id=:user_id AND session_id=:session_id"),
                           {"id": device_id, "user_id": user_id, "session_id": session_id})


def queue_new_mail(settings, *, user_id: str, messages: list) -> None:
    from app.services.push_notifications import PushConfiguration, eligible_new_mail
    if not PushConfiguration.load().configured:
        return
    candidates = [message for message in messages if eligible_new_mail(message)]
    if not candidates:
        return
    account_id = active_gmail_account_id() or candidates[0].gmail_account_id or user_id
    with user_mail_write_transaction(get_engine(settings.database_path), user_id=user_id, gmail_account_id=account_id) as connection:
        devices = connection.execute(text("""
            SELECT d.id, d.session_id FROM push_devices d
            JOIN app_sessions s ON s.id=d.session_id AND s.user_id=d.user_id
            WHERE d.user_id=:user_id AND d.enabled AND s.revoked_at IS NULL
              AND s.expires_at>now()
        """), {"user_id": user_id}).mappings().all()
        for message in candidates:
            for device in devices:
                delivery_id = str(uuid4())
                values = {"id": delivery_id, "user_id": user_id, "gmail_account_id": account_id,
                          "device_id": device["id"], "session_id": device["session_id"], "message_id": message.message_id}
                inserted = connection.execute(text("""
                    INSERT INTO push_deliveries (id, user_id, gmail_account_id, device_id, session_id, message_id)
                    VALUES (:id, :user_id, :gmail_account_id, :device_id, :session_id, :message_id)
                    ON CONFLICT (gmail_account_id, message_id, device_id) DO NOTHING RETURNING id
                """), values).scalar_one_or_none()
                if inserted is None:
                    continue
                # Queue and outbox record commit together before the Gmail
                # history cursor advances, so retries cannot lose new mail.
                connection.execute(text("""
                    INSERT INTO background_jobs (id, kind, queue, status, user_id, gmail_account_id,
                        dedupe_key, priority, payload_version, payload_json, max_attempts, run_after, created_at, updated_at)
                    VALUES (:job_id, 'push_notification', 'default', 'queued', :user_id, :gmail_account_id,
                        :id, 50, 1, :payload_json, 5, now(), now(), now())
                """), values | {"job_id": str(uuid4()), "payload_json": json.dumps({"user_id": user_id, "delivery_id": delivery_id})})


def load_delivery(database_url: str, *, delivery_id: str, user_id: str) -> dict | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(text("""
            SELECT p.*, d.token_encrypted, d.platform, d.environment, d.sound_enabled, d.preview_enabled,
                m.gmail_thread_id, m.sender, m.subject, m.snippet
            FROM push_deliveries p
            JOIN push_devices d ON d.id=p.device_id AND d.user_id=p.user_id AND d.session_id=p.session_id
            JOIN app_sessions s ON s.id=p.session_id AND s.user_id=p.user_id
            JOIN users u ON u.id=p.user_id
            JOIN gmail_accounts a ON a.id=p.gmail_account_id AND a.user_id=p.user_id
            JOIN gmail_messages m ON m.gmail_account_id=p.gmail_account_id
                AND m.user_id=p.user_id AND m.message_id=p.message_id
            WHERE p.id=:id AND p.user_id=:user_id AND p.status='pending'
                AND d.enabled AND s.revoked_at IS NULL AND s.expires_at>now() AND u.access_enabled
                AND u.google_disconnected_at IS NULL AND u.google_data_delete_requested_at IS NULL AND u.google_data_deleted_at IS NULL
                AND a.state='ready' AND a.google_disconnected_at IS NULL AND a.google_data_deleted_at IS NULL
                AND a.google_data_delete_requested_at IS NULL
                AND p.created_at>now()-interval '15 minutes'
                AND m.label_ids_json::jsonb @> '["INBOX", "UNREAD"]'::jsonb
                AND NOT m.label_ids_json::jsonb ?| ARRAY['SENT', 'DRAFT', 'SPAM', 'TRASH']
        """), {"id": delivery_id, "user_id": user_id}).mappings().first()
    if row is None:
        finish_delivery(database_url, delivery_id=delivery_id, user_id=user_id, status="skipped")
        return None
    return dict(row)


def finish_delivery(database_url: str, *, delivery_id: str, user_id: str, status: str) -> None:
    with get_engine(database_url).begin() as connection:
        row = connection.execute(text("""
            UPDATE push_deliveries SET status=:status, completed_at=now()
            WHERE id=:id AND user_id=:user_id AND status='pending'
            RETURNING device_id, session_id, created_at
        """), {"id": delivery_id, "user_id": user_id, "status": status}).mappings().first()
        if row is not None and status == "invalid_token":
            # Do not disable a token that the app refreshed during delivery.
            connection.execute(text("""
                UPDATE push_devices SET enabled=FALSE WHERE id=:id AND session_id=:session_id
                    AND updated_at<=:created_at
            """), {"id": row["device_id"], "session_id": row["session_id"], "created_at": row["created_at"]})
