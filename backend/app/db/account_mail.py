from __future__ import annotations

"""Small account-scoped persistence surface for Gmail writes.

The legacy repositories remain available to primary-only compatibility routes.
Every function here requires both ownership identities and includes the Gmail
account in its lookup and idempotency key.
"""

import json
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import text

from app.db.mail_groups import PendingSendRecord, PendingThreadActionRecord, ClientDraftRecord
from app.db.repository import get_engine
from app.db.user_mail_guard import user_mail_write_transaction


class AccountWriteIdempotencyConflict(RuntimeError):
    """A client id was replayed with a different account-scoped operation."""


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _json_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _send(row) -> PendingSendRecord:
    return PendingSendRecord(
        server_send_id=str(row["server_send_id"]),
        client_send_id=str(row["client_send_id"]),
        user_id=str(row["user_id"]),
        gmail_account_id=str(row["gmail_account_id"]),
        send_type=str(row["send_type"]),
        mailbox_thread_id=str(row["mailbox_thread_id"]) if row["mailbox_thread_id"] is not None else None,
        gmail_thread_id=str(row["gmail_thread_id"]) if row["gmail_thread_id"] is not None else None,
        to=_json_list(row["to_json"]), cc=_json_list(row["cc_json"]), bcc=_json_list(row["bcc_json"]),
        subject=str(row["subject"]), body_text=str(row["body_text"]),
        body_html=str(row["body_html"]) if row["body_html"] is not None else None,
        headers=_json_dict(row["headers_json"]), state=str(row["state"]),
        created_at=_iso(row["created_at"]), queued_at=_iso(row["queued_at"]),
        sent_at=_iso(row["sent_at"]) if row["sent_at"] is not None else None,
        gmail_message_id=str(row["gmail_message_id"]) if row["gmail_message_id"] is not None else None,
        error=str(row["error"]) if row["error"] is not None else None,
        updated_at=_iso(row["updated_at"]),
        attachments=row["attachments_json"] if isinstance(row["attachments_json"], list) else [],
        request_hash=str(row.get("request_hash") or ""),
    )


def create_or_get_send(
    database_url: str, *, user_id: str, gmail_account_id: str,
    client_send_id: str, send_type: str, mailbox_thread_id: str | None,
    gmail_thread_id: str | None, to: list[str], cc: list[str], bcc: list[str],
    subject: str, body_text: str, body_html: str | None, headers: dict,
    attachments: list[dict[str, str]], created_at: str,
) -> PendingSendRecord:
    canonical_request = json.dumps({
        "send_type": send_type, "mailbox_thread_id": mailbox_thread_id,
        "gmail_thread_id": gmail_thread_id, "to": to, "cc": cc, "bcc": bcc,
        "subject": subject, "body_text": body_text, "body_html": body_html,
        "headers": headers, "attachments": attachments,
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    request_hash = sha256(canonical_request.encode("utf-8")).hexdigest()
    with user_mail_write_transaction(
        get_engine(database_url), user_id=user_id, gmail_account_id=gmail_account_id
    ) as connection:
        row = connection.execute(text("""
            INSERT INTO gmail_pending_sends (
              server_send_id, client_send_id, user_id, gmail_account_id,
              send_type, mailbox_thread_id, gmail_thread_id, to_json, cc_json,
              bcc_json, subject, body_text, body_html, headers_json,
              attachments_json, request_hash, state, created_at, queued_at, updated_at
            ) VALUES (
              :server_send_id, :client_send_id, :user_id, :gmail_account_id,
              :send_type, :mailbox_thread_id, :gmail_thread_id, :to_json, :cc_json,
              :bcc_json, :subject, :body_text, :body_html, :headers_json,
              CAST(:attachments_json AS JSONB), :request_hash, 'queued', :created_at, now(), now()
            )
            ON CONFLICT (gmail_account_id, client_send_id) DO NOTHING
            RETURNING *
        """), {
            "server_send_id": str(uuid4()), "client_send_id": client_send_id,
            "user_id": user_id, "gmail_account_id": gmail_account_id,
            "send_type": send_type, "mailbox_thread_id": mailbox_thread_id,
            "gmail_thread_id": gmail_thread_id, "to_json": json.dumps(to),
            "cc_json": json.dumps(cc), "bcc_json": json.dumps(bcc),
            "subject": subject, "body_text": body_text, "body_html": body_html,
            "headers_json": json.dumps(headers), "attachments_json": json.dumps(attachments),
            "request_hash": request_hash,
            "created_at": created_at,
        }).mappings().first()
        if row is None:
            row = connection.execute(text("""
                SELECT * FROM gmail_pending_sends
                WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
                  AND client_send_id=:client_send_id
            """), {"user_id": user_id, "gmail_account_id": gmail_account_id,
                     "client_send_id": client_send_id}).mappings().first()
    if row is None:
        raise RuntimeError("Failed to persist account-scoped send")
    record = _send(row)
    if record.request_hash and record.request_hash != request_hash:
        raise AccountWriteIdempotencyConflict(
            "This send identifier was already used for different content in this Gmail account"
        )
    return record


def get_send(database_url: str, *, user_id: str, gmail_account_id: str, server_send_id: str) -> PendingSendRecord | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(text("""
            SELECT * FROM gmail_pending_sends
            WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
              AND server_send_id=:server_send_id
        """), locals()).mappings().first()
    return _send(row) if row is not None else None


def list_sends(database_url: str, *, user_id: str, gmail_account_id: str, limit: int) -> list[PendingSendRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(text("""
            SELECT * FROM gmail_pending_sends
            WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
              AND state IN ('queued','sending','failed')
            ORDER BY updated_at DESC LIMIT :limit
        """), {"user_id": user_id, "gmail_account_id": gmail_account_id,
                 "limit": max(1, min(limit, 200))}).mappings().all()
    return [_send(row) for row in rows]


def update_send(
    database_url: str, *, user_id: str, gmail_account_id: str,
    server_send_id: str, state: str, error: str | None = None,
    gmail_message_id: str | None = None, gmail_thread_id: str | None = None,
) -> PendingSendRecord | None:
    with user_mail_write_transaction(
        get_engine(database_url), user_id=user_id, gmail_account_id=gmail_account_id
    ) as connection:
        row = connection.execute(text("""
            UPDATE gmail_pending_sends SET state=:state, error=:error,
              gmail_message_id=COALESCE(:gmail_message_id,gmail_message_id),
              gmail_thread_id=COALESCE(:gmail_thread_id,gmail_thread_id),
              sent_at=CASE WHEN :state='sent' THEN now() ELSE sent_at END,
              updated_at=now()
            WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
              AND server_send_id=:server_send_id
            RETURNING *
        """), locals()).mappings().first()
    return _send(row) if row is not None else None


def create_or_get_action(
    database_url: str, *, user_id: str, gmail_account_id: str,
    client_action_id: str, mailbox_thread_id: str, target_message_id: str | None,
    action: str, created_at: str,
) -> PendingThreadActionRecord:
    with user_mail_write_transaction(
        get_engine(database_url), user_id=user_id, gmail_account_id=gmail_account_id
    ) as connection:
        row = connection.execute(text("""
            INSERT INTO gmail_pending_thread_actions (
              server_action_id, client_action_id, user_id, gmail_account_id,
              mailbox_thread_id, target_message_id, action, state, created_at,
              queued_at, previous_labels_json, updated_at
            ) VALUES (
              :server_action_id,:client_action_id,:user_id,:gmail_account_id,
              :mailbox_thread_id,:target_message_id,:action,'queued',:created_at,
              now(),'{}'::jsonb,now()
            ) ON CONFLICT (gmail_account_id, client_action_id) DO NOTHING
            RETURNING *
        """), {**locals(), "server_action_id": str(uuid4())}).mappings().first()
        if row is None:
            row = connection.execute(text("""
                SELECT * FROM gmail_pending_thread_actions
                WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
                  AND client_action_id=:client_action_id
            """), locals()).mappings().first()
    if row is None:
        raise RuntimeError("Failed to persist account-scoped action")
    record = _action(row)
    if (
        record.mailbox_thread_id != mailbox_thread_id
        or record.target_message_id != target_message_id
        or record.action != action
    ):
        raise AccountWriteIdempotencyConflict(
            "This action identifier was already used for a different Gmail operation"
        )
    return record


def _action(row) -> PendingThreadActionRecord:
    return PendingThreadActionRecord(
        server_action_id=str(row["server_action_id"]), client_action_id=str(row["client_action_id"]),
        user_id=str(row["user_id"]), gmail_account_id=str(row["gmail_account_id"]),
        mailbox_thread_id=str(row["mailbox_thread_id"]),
        target_message_id=str(row["target_message_id"]) if row["target_message_id"] is not None else None,
        action=str(row["action"]), state=str(row["state"]), created_at=_iso(row["created_at"]),
        queued_at=_iso(row["queued_at"]), applied_at=_iso(row["applied_at"]) if row["applied_at"] is not None else None,
        error=str(row["error"]) if row["error"] is not None else None,
        updated_at=_iso(row["updated_at"]), previous_labels=_json_dict(row["previous_labels_json"]),
    )


def get_action(database_url: str, *, user_id: str, gmail_account_id: str, server_action_id: str) -> PendingThreadActionRecord | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(text("""
          SELECT * FROM gmail_pending_thread_actions
          WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
            AND server_action_id=:server_action_id
        """), locals()).mappings().first()
    return _action(row) if row is not None else None


def update_action(database_url: str, *, user_id: str, gmail_account_id: str,
                  server_action_id: str, state: str, error: str | None = None) -> PendingThreadActionRecord | None:
    with user_mail_write_transaction(
        get_engine(database_url), user_id=user_id, gmail_account_id=gmail_account_id
    ) as connection:
        row = connection.execute(text("""
          UPDATE gmail_pending_thread_actions SET state=:state,error=:error,
            applied_at=CASE WHEN :state='applied' THEN now() ELSE applied_at END,
            updated_at=now()
          WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
            AND server_action_id=:server_action_id RETURNING *
        """), locals()).mappings().first()
    return _action(row) if row is not None else None


def _draft(row) -> ClientDraftRecord:
    return ClientDraftRecord(
        user_id=str(row["user_id"]), gmail_account_id=str(row["gmail_account_id"]),
        client_draft_id=str(row["client_draft_id"]),
        gmail_draft_id=str(row["gmail_draft_id"]) if row["gmail_draft_id"] is not None else None,
        gmail_message_id=str(row["gmail_message_id"]) if row["gmail_message_id"] is not None else None,
        gmail_thread_id=str(row["gmail_thread_id"]) if row["gmail_thread_id"] is not None else None,
        content_hash=str(row["content_hash"] or ""), state=str(row["state"]),
        last_client_send_id=str(row["last_client_send_id"]) if row["last_client_send_id"] is not None else None,
        sent_message_id=str(row["sent_message_id"]) if row["sent_message_id"] is not None else None,
        error=str(row["error"]) if row["error"] is not None else None,
        created_at=_iso(row["created_at"]), saved_at=_iso(row["saved_at"]) if row["saved_at"] is not None else None,
        updated_at=_iso(row["updated_at"]),
    )


def get_draft(database_url: str, *, user_id: str, gmail_account_id: str,
              client_draft_id: str | None = None, gmail_draft_id: str | None = None) -> ClientDraftRecord | None:
    if not client_draft_id and not gmail_draft_id:
        return None
    column = "client_draft_id" if client_draft_id else "gmail_draft_id"
    value = client_draft_id or gmail_draft_id
    with get_engine(database_url).connect() as connection:
        row = connection.execute(text(f"""
          SELECT * FROM gmail_client_drafts
          WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
            AND {column}=:value
        """), {"user_id": user_id, "gmail_account_id": gmail_account_id,
                 "value": value}).mappings().first()
    return _draft(row) if row is not None else None


def upsert_draft(database_url: str, *, user_id: str, gmail_account_id: str,
                 client_draft_id: str, gmail_draft_id: str | None,
                 gmail_message_id: str | None, gmail_thread_id: str | None,
                 content_hash: str, state: str, created_at: str,
                 error: str | None = None) -> ClientDraftRecord:
    with user_mail_write_transaction(
        get_engine(database_url), user_id=user_id, gmail_account_id=gmail_account_id
    ) as connection:
        row = connection.execute(text("""
          INSERT INTO gmail_client_drafts (
            user_id,gmail_account_id,client_draft_id,gmail_draft_id,
            gmail_message_id,gmail_thread_id,content_hash,state,error,
            created_at,saved_at,updated_at
          ) VALUES (
            :user_id,:gmail_account_id,:client_draft_id,:gmail_draft_id,
            :gmail_message_id,:gmail_thread_id,:content_hash,:state,:error,
            :created_at,CASE WHEN :state='saved' THEN now() ELSE NULL END,now()
          ) ON CONFLICT (gmail_account_id,client_draft_id) DO UPDATE SET
            gmail_draft_id=COALESCE(EXCLUDED.gmail_draft_id,gmail_client_drafts.gmail_draft_id),
            gmail_message_id=COALESCE(EXCLUDED.gmail_message_id,gmail_client_drafts.gmail_message_id),
            gmail_thread_id=COALESCE(EXCLUDED.gmail_thread_id,gmail_client_drafts.gmail_thread_id),
            content_hash=EXCLUDED.content_hash,state=EXCLUDED.state,error=EXCLUDED.error,
            saved_at=CASE WHEN EXCLUDED.state='saved' THEN now() ELSE gmail_client_drafts.saved_at END,
            updated_at=now() RETURNING *
        """), locals()).mappings().first()
    if row is None:
        raise RuntimeError("Failed to persist account-scoped draft")
    return _draft(row)


def update_draft_state(database_url: str, *, user_id: str, gmail_account_id: str,
                       client_draft_id: str, state: str,
                       client_send_id: str | None = None,
                       gmail_message_id: str | None = None,
                       gmail_thread_id: str | None = None,
                       error: str | None = None) -> ClientDraftRecord | None:
    with user_mail_write_transaction(
        get_engine(database_url), user_id=user_id, gmail_account_id=gmail_account_id
    ) as connection:
        row = connection.execute(text("""
          UPDATE gmail_client_drafts SET state=:state,error=:error,
            last_client_send_id=COALESCE(:client_send_id,last_client_send_id),
            sent_message_id=COALESCE(:gmail_message_id,sent_message_id),
            gmail_thread_id=COALESCE(:gmail_thread_id,gmail_thread_id),updated_at=now()
          WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
            AND client_draft_id=:client_draft_id RETURNING *
        """), locals()).mappings().first()
    return _draft(row) if row is not None else None
