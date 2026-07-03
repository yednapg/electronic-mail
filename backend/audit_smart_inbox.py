from __future__ import annotations

"""Run a local quality audit over the rendered smart inbox."""

import argparse
import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import text

from app.core.config import load_settings
from app.db.mail_groups import get_app_session_snapshot, get_import_state
from app.db.repository import get_engine
from app.schemas.domain import SmartInboxResponse
from app.services.mail_group_config import SMART_HOT_WINDOW_DAYS
from app.services.mail_groups import build_group_detail_response
from app.services.smart_inbox_quality import audit_smart_inbox_response_quality


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit smart-inbox grouping/title quality for a local user.")
    parser.add_argument("--user-id", default=None, help="User id to audit. Defaults to the most recently updated user.")
    parser.add_argument("--label", default="inbox", choices=["inbox", "all", "sent"], help="Mailbox label to render.")
    parser.add_argument("--limit", type=int, default=150, help="Mailbox row limit.")
    parser.add_argument("--reader-sample-limit", type=int, default=8, help="Visible rows to open without summaries to verify lazy summary behavior.")
    parser.add_argument("--skip-reader-sample", action="store_true", help="Skip reader default-summary sampling.")
    parser.add_argument("--show-text", action="store_true", help="Print row titles/senders instead of redacted hashes.")
    args = parser.parse_args()

    settings = load_settings()
    database_url = str(settings.database_path)
    user_id = args.user_id or _latest_user_id(database_url)
    if not user_id:
        print(json.dumps({"status": "no_user", "message": "No local users found. Sign in first."}, indent=2))
        return

    snapshot = get_app_session_snapshot(database_url, user_id=user_id)
    smart_inbox = SmartInboxResponse.model_validate(snapshot.smart_inbox if snapshot is not None else {})
    report = audit_smart_inbox_response_quality(smart_inbox)
    state = get_import_state(database_url, user_id=user_id)
    contract = build_contract_audit_payload(
        settings,
        user_id=user_id,
        smart_inbox=smart_inbox,
        quality_blocking_issue_count=report.blocking_issue_count,
        state=state,
        reader_sample_limit=max(0, args.reader_sample_limit),
        sample_reader_defaults=not args.skip_reader_sample,
    )
    payload: dict[str, Any] = {
        "status": "pass" if contract["passed"] else "fail",
        "user_id": user_id,
        "label": args.label,
        "contract": _contract_payload(contract, show_text=args.show_text),
        "total_rows": report.total_rows,
        "grouped_rows": report.grouped_rows,
        "ai_ready_rows": report.ai_ready_rows,
        "blocking_issue_count": report.blocking_issue_count,
        "warning_count": report.warning_count,
        "issues": [_issue_payload(issue, show_text=args.show_text) for issue in report.issues],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_contract_audit_payload(
    settings,
    *,
    user_id: str,
    smart_inbox=None,
    mailbox=None,
    quality_blocking_issue_count: int,
    state,
    reader_sample_limit: int = 8,
    sample_reader_defaults: bool = True,
) -> dict[str, Any]:
    """Evaluate the concrete first-run Inbox contract for one rendered mailbox."""
    if smart_inbox is not None:
        rows = [row for section in smart_inbox.sections for row in section.rows]
    elif mailbox is not None:
        rows = [row for section in mailbox.sections for row in section.rows]
    else:
        rows = []
    hot_window_completed_at = (
        getattr(state, "hot_window_completed_at", None)
        or getattr(state, "full_backfill_completed_at", None)
        if state is not None
        else None
    )
    import_state = {
        "has_state": state is not None,
        "first_batch_imported_at": getattr(state, "first_batch_imported_at", None) if state is not None else None,
        "first_groups_ready_at": getattr(state, "first_groups_ready_at", None) if state is not None else None,
        "hot_window_completed_at": hot_window_completed_at,
        "full_backfill_completed_at": getattr(state, "full_backfill_completed_at", None) if state is not None else None,
        "last_sync_error": getattr(state, "last_sync_error", None) if state is not None else None,
    }
    issues: list[dict[str, Any]] = []
    if state is None:
        issues.append(_contract_issue("first_run_state_missing", "blocker", "No gmail_import_state row exists for this user."))
    else:
        if not import_state["first_batch_imported_at"]:
            issues.append(_contract_issue("first_batch_missing", "blocker", "Initial Gmail batch has not completed."))
        if not import_state["hot_window_completed_at"]:
            issues.append(_contract_issue("hot_window_missing", "blocker", f"The {SMART_HOT_WINDOW_DAYS}-day import window has not completed."))
        if not import_state["first_groups_ready_at"]:
            issues.append(_contract_issue("first_groups_missing", "blocker", "AI/title grouping has not finished for first entry."))
        if import_state["last_sync_error"]:
            issues.append(_contract_issue("last_sync_error", "blocker", "Gmail sync has a recorded error.", {"error": import_state["last_sync_error"]}))
    if not rows:
        issues.append(_contract_issue("empty_inbox", "blocker", "Rendered Inbox has no visible rows."))
    if quality_blocking_issue_count:
        issues.append(
            _contract_issue(
                "quality_blockers",
                "blocker",
                "Rendered rows have title/grouping quality blockers.",
                {"count": quality_blocking_issue_count},
            )
        )

    reader_default_summary = (
        _audit_reader_default_summary_contract(
            settings,
            user_id=user_id,
            rows=rows,
            limit=reader_sample_limit,
        )
        if sample_reader_defaults
        else {"sampled_rows": 0, "unexpected_summary_count": 0, "missing_detail_count": 0, "error_count": 0, "issues": [], "skipped": True}
    )
    issues.extend(reader_default_summary["issues"])
    blocking_issue_count = sum(1 for issue in issues if issue["severity"] == "blocker")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    return {
        "passed": blocking_issue_count == 0,
        "hot_window_days": SMART_HOT_WINDOW_DAYS,
        "visible_rows": len(rows),
        "grouped_rows": sum(1 for row in rows if _row_message_count(row) > 1),
        "ai_ready_rows": sum(1 for row in rows if _row_is_ready(row)),
        "import_state": import_state,
        "reader_default_summary": {key: value for key, value in reader_default_summary.items() if key != "issues"},
        "blocking_issue_count": blocking_issue_count,
        "warning_count": warning_count,
        "issues": issues,
    }


def _audit_reader_default_summary_contract(settings, *, user_id: str, rows: list[Any], limit: int) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    sampled = rows[:limit]
    for row in sampled:
        reader_id = _reader_id_for_row(row)
        try:
            detail = build_group_detail_response(settings, user_id=user_id, group_id=reader_id, include_summary=False)
        except Exception as exc:
            issues.append(
                _contract_issue(
                    "reader_default_summary_error",
                    "blocker",
                    "Could not open a visible row without requesting a summary.",
                    {"row_id": reader_id, "title": row.title, "error": f"{type(exc).__name__}: {str(exc)[:300]}"},
                )
            )
            continue
        if detail is None:
            issues.append(
                _contract_issue(
                    "reader_detail_missing",
                    "blocker",
                    "Visible row has no reader detail response.",
                    {"row_id": reader_id, "title": row.title},
                )
            )
            continue
        if _compact(detail.summary):
            issues.append(
                _contract_issue(
                    "reader_default_summary_present",
                    "blocker",
                    "Reader returned a summary without include_summary=true.",
                    {"row_id": reader_id, "title": row.title},
                )
            )
    return {
        "sampled_rows": len(sampled),
        "unexpected_summary_count": sum(1 for issue in issues if issue["code"] == "reader_default_summary_present"),
        "missing_detail_count": sum(1 for issue in issues if issue["code"] == "reader_detail_missing"),
        "error_count": sum(1 for issue in issues if issue["code"] == "reader_default_summary_error"),
        "issues": issues,
        "skipped": False,
    }


def _reader_id_for_row(row: Any) -> str:
    reader_thread_id = getattr(row, "reader_thread_id", None)
    if isinstance(reader_thread_id, str) and reader_thread_id.strip():
        return reader_thread_id
    thread_id = getattr(row, "thread_id", None)
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id
    source_thread_ids = getattr(row, "source_thread_ids", None)
    if isinstance(source_thread_ids, list) and len(source_thread_ids) == 1 and str(source_thread_ids[0]).strip():
        return str(source_thread_ids[0])
    return str(getattr(row, "id", "") or "")


def _row_message_count(row: Any) -> int:
    message_count = getattr(row, "message_count", None)
    if isinstance(message_count, int):
        return message_count
    source_message_ids = getattr(row, "source_message_ids", None)
    source_thread_ids = getattr(row, "source_thread_ids", None)
    message_total = len(source_message_ids) if isinstance(source_message_ids, list) else 0
    thread_total = len(source_thread_ids) if isinstance(source_thread_ids, list) else 0
    return max(1, message_total, thread_total)


def _row_is_ready(row: Any) -> bool:
    presentation_status = getattr(row, "presentation_status", None)
    if isinstance(presentation_status, str):
        return presentation_status == "ai_ready"
    return getattr(row, "readiness", None) == "ready"


def _contract_issue(code: str, severity: str, reason: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "reason": reason, "metadata": metadata or {}}


def _latest_user_id(database_url: str) -> str | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT id FROM users ORDER BY updated_at DESC LIMIT 1")).fetchone()
    return str(row.id) if row is not None else None


def _issue_payload(issue, *, show_text: bool) -> dict[str, Any]:
    payload = asdict(issue)
    if show_text:
        return payload
    payload["title"] = _redacted_text(payload.get("title"))
    payload["sender"] = _redacted_text(payload.get("sender"))
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and "child_titles" in metadata:
        metadata["child_titles"] = [_redacted_text(value) for value in metadata["child_titles"]]
    return payload


def _contract_payload(contract: dict[str, Any], *, show_text: bool) -> dict[str, Any]:
    if show_text:
        return contract
    payload = json.loads(json.dumps(contract, default=str))
    for issue in payload.get("issues", []):
        metadata = issue.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if "title" in metadata:
            metadata["title"] = _redacted_text(metadata.get("title"))
        if "error" in metadata:
            metadata["error"] = _redacted_text(metadata.get("error"))
    return payload


def _redacted_text(value: str | None) -> str | None:
    if not value:
        return value
    return f"<text:{len(value)} chars>"


def _compact(value: str | None) -> str | None:
    if value is None:
        return None
    compacted = " ".join(value.split())
    return compacted or None


if __name__ == "__main__":
    main()
