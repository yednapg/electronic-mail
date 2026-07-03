from __future__ import annotations

from dataclasses import replace

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    PendingThreadActionRecord,
    delete_gmail_messages,
    get_mail_group_detail,
    get_pending_thread_action,
    get_smart_inbox_row,
    list_messages_by_ids,
    list_messages_for_gmail_thread,
    mark_pending_thread_action_applied,
    mark_pending_thread_action_applying,
    mark_pending_thread_action_failed,
    prune_empty_mail_groups,
    upsert_gmail_messages,
    upsert_pending_thread_action,
)
from app.schemas.domain import QueuedThreadActionRequest, QueuedThreadActionResponse
from app.services.integrations.google import (
    archive_gmail_thread,
    delete_gmail_message_forever,
    delete_gmail_thread_forever,
    mark_gmail_message_read,
    mark_gmail_thread_read,
    move_gmail_message_to_trash,
    move_gmail_thread_to_trash,
    unarchive_gmail_thread,
)
from app.services.mailbox_events import MAILBOX_CHANGED, emit_mailbox_event
from app.services.mail_groups import enqueue_projection_refresh, rebuild_touched_mail_groups

SMART_ROW_READER_PREFIX = "smart-row:"


def enqueue_thread_action(settings: Settings, *, user_id: str, request: QueuedThreadActionRequest) -> QueuedThreadActionResponse:
    database_url = str(settings.database_path)
    record = upsert_pending_thread_action(
        database_url,
        user_id=user_id,
        client_action_id=request.client_action_id,
        mailbox_thread_id=request.mailbox_thread_id,
        target_message_id=getattr(request, "target_message_id", None),
        action=request.action,
        created_at=request.created_at,
    )
    if record.action != "delete_forever":
        _apply_local_action(
            settings,
            user_id=user_id,
            mailbox_thread_id=record.mailbox_thread_id,
            action=record.action,
            target_message_id=record.target_message_id,
        )
    enqueue_job(
        database_url,
        kind="gmail_thread_action",
        queue="default",
        user_id=user_id,
        dedupe_key=f"gmail-thread-action:{user_id}:{record.server_action_id}",
        priority=70,
        payload={"user_id": user_id, "server_action_id": record.server_action_id},
    )
    enqueue_projection_refresh(settings, user_id=user_id, priority=10)
    emit_mailbox_event(
        settings,
        user_id=user_id,
        event_type=MAILBOX_CHANGED,
        mailbox_label="all",
        payload={"source": "thread_action", "action": record.action, "mailbox_thread_id": record.mailbox_thread_id},
    )
    return _response_from_record(record)


def run_pending_thread_action(settings: Settings, *, user_id: str, server_action_id: str) -> QueuedThreadActionResponse | None:
    database_url = str(settings.database_path)
    record = get_pending_thread_action(database_url, user_id=user_id, server_action_id=server_action_id)
    if record is None:
        return None
    if record.state == "applied":
        return _response_from_record(record)
    mark_pending_thread_action_applying(database_url, user_id=user_id, server_action_id=server_action_id)
    try:
        messages, thread_ids = _resolve_action_messages(
            database_url,
            user_id=user_id,
            mailbox_thread_id=record.mailbox_thread_id,
            target_message_id=record.target_message_id,
        )
        if not record.target_message_id and not thread_ids and not messages:
            raise RuntimeError("Mail group not found")
        if record.target_message_id and not messages:
            raise RuntimeError("Gmail message not found")
        if record.target_message_id and record.action in {"mark_read", "move_trash", "delete_forever"}:
            _apply_remote_message_action(settings, user_id=user_id, gmail_message_id=record.target_message_id, action=record.action)
        else:
            if not thread_ids:
                raise RuntimeError("No Gmail thread IDs found for mailbox thread")
            for thread_id in thread_ids:
                _apply_remote_action(settings, user_id=user_id, gmail_thread_id=thread_id, action=record.action)
        if record.action == "delete_forever":
            affected_group_ids = delete_gmail_messages(database_url, user_id=user_id, message_ids=[message.message_id for message in messages])
            prune_empty_mail_groups(database_url, user_id=user_id, group_ids=affected_group_ids)
        applied = mark_pending_thread_action_applied(database_url, user_id=user_id, server_action_id=server_action_id)
        enqueue_projection_refresh(settings, user_id=user_id, priority=10)
        emit_mailbox_event(
            settings,
            user_id=user_id,
            event_type=MAILBOX_CHANGED,
            mailbox_label="all",
            payload={"source": "thread_action_applied", "action": record.action, "mailbox_thread_id": record.mailbox_thread_id},
        )
        return _response_from_record(applied or record)
    except Exception as exc:
        mark_pending_thread_action_failed(
            database_url,
            user_id=user_id,
            server_action_id=server_action_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


def _apply_local_action(settings: Settings, *, user_id: str, mailbox_thread_id: str, action: str, target_message_id: str | None = None) -> None:
    database_url = str(settings.database_path)
    messages, _thread_ids = _resolve_action_messages(
        database_url,
        user_id=user_id,
        mailbox_thread_id=mailbox_thread_id,
        target_message_id=target_message_id,
    )
    if not messages:
        return
    changed = []
    for message in messages:
        next_labels = _labels_after_action(message.label_ids, action)
        if next_labels != message.label_ids:
            changed.append(replace(message, label_ids=next_labels))
    if not changed:
        return
    upsert_gmail_messages(database_url, changed)
    rebuild_touched_mail_groups(settings, user_id=user_id, message_ids=[message.message_id for message in changed], use_ai=False)


def _resolve_action_messages(
    database_url: str,
    *,
    user_id: str,
    mailbox_thread_id: str,
    target_message_id: str | None,
):
    if target_message_id:
        messages = list_messages_by_ids(database_url, user_id=user_id, message_ids=[target_message_id])
        return messages, sorted({message.gmail_thread_id for message in messages if message.gmail_thread_id})

    if mailbox_thread_id.startswith(SMART_ROW_READER_PREFIX):
        smart_row = get_smart_inbox_row(database_url, user_id=user_id, row_id=mailbox_thread_id[len(SMART_ROW_READER_PREFIX) :])
        if smart_row is None:
            return [], []
        messages_by_id = {
            message.message_id: message
            for message in list_messages_by_ids(database_url, user_id=user_id, message_ids=smart_row.source_message_ids)
        }
        for thread_id in smart_row.source_thread_ids:
            for message in list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=thread_id):
                messages_by_id.setdefault(message.message_id, message)
        messages = list(messages_by_id.values())
        return messages, sorted({message.gmail_thread_id for message in messages if message.gmail_thread_id})

    messages = list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=mailbox_thread_id)
    if messages:
        return messages, sorted({message.gmail_thread_id for message in messages if message.gmail_thread_id})

    detail = get_mail_group_detail(database_url, user_id=user_id, group_id=mailbox_thread_id)
    if detail is None:
        return [], []
    messages = detail.messages
    return messages, sorted({message.gmail_thread_id for message in messages if message.gmail_thread_id})


def _labels_after_action(labels: list[str], action: str) -> list[str]:
    next_labels = list(dict.fromkeys(labels))
    upper_to_original = {label.upper(): label for label in next_labels}
    if action == "archive":
        next_labels = [label for label in next_labels if label.upper() != "INBOX"]
    elif action == "unarchive":
        if "INBOX" not in upper_to_original:
            next_labels.append("INBOX")
    elif action == "mark_read":
        next_labels = [label for label in next_labels if label.upper() != "UNREAD"]
    elif action == "move_trash":
        next_labels = [label for label in next_labels if label.upper() not in {"INBOX", "SPAM"}]
        if "TRASH" not in {label.upper() for label in next_labels}:
            next_labels.append("TRASH")
    return next_labels


def _apply_remote_action(settings: Settings, *, user_id: str, gmail_thread_id: str, action: str) -> None:
    if action == "archive":
        archive_gmail_thread(settings, gmail_thread_id, user_id=user_id)
    elif action == "unarchive":
        unarchive_gmail_thread(settings, gmail_thread_id, user_id=user_id)
    elif action == "mark_read":
        mark_gmail_thread_read(settings, gmail_thread_id, user_id=user_id)
    elif action == "move_trash":
        move_gmail_thread_to_trash(settings, gmail_thread_id, user_id=user_id)
    elif action == "delete_forever":
        delete_gmail_thread_forever(settings, gmail_thread_id, user_id=user_id)
    else:
        raise RuntimeError(f"Unsupported Gmail thread action {action}")


def _apply_remote_message_action(settings: Settings, *, user_id: str, gmail_message_id: str, action: str) -> None:
    if action == "mark_read":
        mark_gmail_message_read(settings, gmail_message_id, user_id=user_id)
    elif action == "move_trash":
        move_gmail_message_to_trash(settings, gmail_message_id, user_id=user_id)
    elif action == "delete_forever":
        delete_gmail_message_forever(settings, gmail_message_id, user_id=user_id)
    else:
        raise RuntimeError(f"Unsupported Gmail message action {action}")


def _response_from_record(record: PendingThreadActionRecord) -> QueuedThreadActionResponse:
    return QueuedThreadActionResponse(
        client_action_id=record.client_action_id,
        server_action_id=record.server_action_id,
        mailbox_thread_id=record.mailbox_thread_id,
        target_message_id=record.target_message_id,
        action=record.action,
        state=record.state,
        queued_at=record.queued_at,
        applied_at=record.applied_at,
        error=record.error,
    )
