from __future__ import annotations

"""Exact-message Gmail mutations initiated from AI Inbox matters."""

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.services.integrations.google import (
    delete_gmail_message_forever,
    mark_gmail_message_not_spam,
    mark_gmail_message_read,
    mark_gmail_message_spam,
    mark_gmail_message_unread,
    modify_gmail_message_labels,
    move_gmail_message_to_trash,
    restore_gmail_message_from_trash,
    star_gmail_message,
    unstar_gmail_message,
)
from app.services.mailbox_events import AI_INBOX_CHANGED, MAILBOX_CHANGED, emit_mailbox_event


def enqueue_matter_action(
    settings: Settings,
    *,
    user_id: str,
    client_action_id: str,
    matter_id: str,
    action: str,
    message_ids: list[str],
) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="ai_matter_action",
        queue="critical",
        user_id=user_id,
        dedupe_key=f"ai-matter-action:{user_id}:{client_action_id}",
        priority=95,
        max_attempts=5,
        payload={
            "user_id": user_id,
            "client_action_id": client_action_id,
            "matter_id": matter_id,
            "action": action,
            # This immutable snapshot is the central offline-safety guarantee.
            "message_ids": list(dict.fromkeys(message_ids)),
        },
    )
    return job.id


def run_matter_action(
    settings: Settings,
    *,
    user_id: str,
    matter_id: str,
    action: str,
    message_ids: list[str],
) -> None:
    for message_id in list(dict.fromkeys(message_ids)):
        _apply_message_action(settings, user_id=user_id, message_id=message_id, action=action)
    enqueue_job(
        str(settings.database_path),
        kind="gmail_delta_sync",
        queue="critical",
        user_id=user_id,
        dedupe_key=f"gmail-delta-after-ai-action:{user_id}",
        priority=90,
        payload={"user_id": user_id, "batch_size": 100},
    )
    emit_mailbox_event(
        settings,
        user_id=user_id,
        event_type=MAILBOX_CHANGED,
        mailbox_label="all",
        payload={"source": "ai_matter_action", "matter_id": matter_id, "action": action},
    )
    emit_mailbox_event(
        settings,
        user_id=user_id,
        event_type=AI_INBOX_CHANGED,
        payload={"source": "ai_matter_action", "matter_id": matter_id, "action": action},
    )


def _apply_message_action(settings: Settings, *, user_id: str, message_id: str, action: str) -> None:
    if action == "archive":
        modify_gmail_message_labels(settings, message_id, user_id=user_id, remove_label_ids=["INBOX"])
    elif action == "unarchive":
        modify_gmail_message_labels(settings, message_id, user_id=user_id, add_label_ids=["INBOX"])
    elif action == "mark_read":
        mark_gmail_message_read(settings, message_id, user_id=user_id)
    elif action == "mark_unread":
        mark_gmail_message_unread(settings, message_id, user_id=user_id)
    elif action == "move_trash":
        move_gmail_message_to_trash(settings, message_id, user_id=user_id)
    elif action == "restore_trash":
        restore_gmail_message_from_trash(settings, message_id, user_id=user_id)
    elif action == "mark_spam":
        mark_gmail_message_spam(settings, message_id, user_id=user_id)
    elif action == "not_spam":
        mark_gmail_message_not_spam(settings, message_id, user_id=user_id)
    elif action == "star":
        star_gmail_message(settings, message_id, user_id=user_id)
    elif action == "unstar":
        unstar_gmail_message(settings, message_id, user_id=user_id)
    elif action == "delete_forever":
        delete_gmail_message_forever(settings, message_id, user_id=user_id)
    else:
        raise ValueError(f"Unsupported matter action {action}")
