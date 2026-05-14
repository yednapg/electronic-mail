from __future__ import annotations

"""Gmail MIME extraction, HTML sanitization, and grouping signal extraction."""

from base64 import urlsafe_b64decode
from email.utils import getaddresses
import hashlib
import html
import re
from typing import Any

SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style|iframe|object|embed|form|input|button|textarea|select)[^>]*>.*?</\1>")
UNSAFE_TAG_RE = re.compile(r"(?is)</?(script|style|iframe|object|embed|form|input|button|textarea|select)[^>]*>")
EVENT_ATTR_RE = re.compile(r"\s+on[a-zA-Z]+\s*=\s*(['\"]).*?\1", re.DOTALL)
JS_HREF_RE = re.compile(r"(?i)(href|src)\s*=\s*(['\"])\s*javascript:[^'\"]*\2")
TAG_RE = re.compile(r"(?is)<[^>]+>")
SPACE_RE = re.compile(r"\s+")
SUBJECT_PREFIX_RE = re.compile(r"(?i)^\s*(re|fwd?|fw):\s*")
STRICT_ID = r"(?=[A-Z0-9-]*\d)([A-Z0-9][A-Z0-9-]{4,})"
ORDER_RE = re.compile(rf"(?i)\b(?:order|shipment)(?:\s*(?:id|number|no\.?|#))?\s*(?:[:#-]|\s)\s*{STRICT_ID}")
TICKET_RE = re.compile(rf"(?i)\b(?:ticket|case|request)(?:\s*(?:id|number|no\.?|#))?\s*(?:[:#-]|\s)\s*{STRICT_ID}")
TRACKING_RE = re.compile(rf"(?i)\b(?:tracking|awb)(?:\s*(?:id|number|no\.?|#))?\s*(?:[:#-]|\s)\s*{STRICT_ID}")
INVOICE_RE = re.compile(rf"(?i)\b(?:invoice|receipt|bill)(?:\s*(?:id|number|no\.?|#))?\s*(?:[:#-]|\s)\s*{STRICT_ID}")
BOOKING_RE = re.compile(rf"(?i)\b(?:booking|pnr|reservation)(?:\s*(?:id|number|no\.?|#))?\s*(?:[:#-]|\s)\s*{STRICT_ID}")
APPLICATION_RE = re.compile(rf"(?i)\b(?:application|applicant|submission)(?:\s*(?:id|number|no\.?|#))?\s*(?:[:#-]|\s)\s*{STRICT_ID}")
URL_DOMAIN_RE = re.compile(r"https?://([^/\s]+)", re.IGNORECASE)
ZERO_WIDTH_RE = re.compile(r"[\u034f\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
URL_LIST_RE = re.compile(r"(?:https?://\S+\s*){3,}", re.I)
FOOTER_RE = re.compile(r"(?is)(unsubscribe|manage preferences|privacy policy|view in browser|update your preferences).{0,600}")


def parse_gmail_message(payload: dict[str, Any], *, user_id: str) -> dict[str, Any]:
    message_id = str(payload.get("id") or "")
    gmail_thread_id = str(payload.get("threadId") or "") or None
    headers = _headers(payload.get("payload") if isinstance(payload.get("payload"), dict) else {})
    label_ids = [str(label) for label in payload.get("labelIds", []) if label]
    html_body, text_body = _extract_bodies(payload.get("payload") if isinstance(payload.get("payload"), dict) else {})
    sanitized_html = sanitize_email_html(html_body) if html_body else None
    fallback_text = text_body or html_to_text(html_body or "") or str(payload.get("snippet") or "")
    cleaned_text = clean_ai_text(fallback_text) or compact_text(fallback_text)
    subject = headers.get("subject")
    sender = headers.get("from")
    recipients = {
        "to": headers.get("to"),
        "cc": headers.get("cc"),
        "bcc": headers.get("bcc"),
    }
    body_hash = hashlib.sha256(f"{subject or ''}\n{fallback_text}\n{html_body or ''}".encode("utf-8", errors="ignore")).hexdigest()
    return {
        "user_id": user_id,
        "message_id": message_id,
        "gmail_thread_id": gmail_thread_id,
        "history_id": str(payload.get("historyId")) if payload.get("historyId") is not None else None,
        "label_ids": label_ids,
        "internal_date": _internal_date_iso(payload.get("internalDate")),
        "subject": subject,
        "sender": sender,
        "recipients": recipients,
        "headers": headers,
        "snippet": str(payload.get("snippet") or "") or None,
        "raw_payload": payload,
        "html_body_sanitized": sanitized_html,
        "text_body": cleaned_text,
        "extracted_signals": extract_signals(subject=subject, sender=sender, text=cleaned_text, headers=headers),
        "body_hash": body_hash,
    }


def sanitize_email_html(value: str) -> str:
    cleaned = SCRIPT_STYLE_RE.sub("", value)
    cleaned = UNSAFE_TAG_RE.sub("", cleaned)
    cleaned = EVENT_ATTR_RE.sub("", cleaned)
    cleaned = JS_HREF_RE.sub(r"\1=\2#\2", cleaned)
    return cleaned.strip()


def html_to_text(value: str) -> str:
    without_tags = TAG_RE.sub(" ", value)
    return compact_text(html.unescape(without_tags))


def compact_text(value: str | None) -> str:
    if not value:
        return ""
    value = ZERO_WIDTH_RE.sub("", html.unescape(value))
    value = URL_LIST_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


def clean_ai_text(value: str | None, *, max_chars: int = 8000) -> str:
    if not value:
        return ""
    value = ZERO_WIDTH_RE.sub("", html.unescape(value))
    value = URL_LIST_RE.sub(" ", value)
    value = FOOTER_RE.sub(" ", value)
    value = re.sub(r"(?im)^>.*$", " ", value)
    value = re.sub(r"(?is)\bon .{0,120}wrote:\s.*$", " ", value)
    return SPACE_RE.sub(" ", value).strip()[:max_chars]


def normalize_subject(value: str | None) -> str:
    subject = compact_text(value).lower()
    while True:
        next_subject = SUBJECT_PREFIX_RE.sub("", subject).strip()
        if next_subject == subject:
            return next_subject
        subject = next_subject


def sender_domain(value: str | None) -> str:
    if not value:
        return "unknown"
    addresses = getaddresses([value])
    email_address = addresses[0][1] if addresses else value
    if "@" not in email_address:
        return email_address.lower().strip() or "unknown"
    return email_address.rsplit("@", 1)[1].lower().strip() or "unknown"


def extract_signals(*, subject: str | None, sender: str | None, text: str, headers: dict[str, str]) -> dict[str, Any]:
    haystack = "\n".join([subject or "", sender or "", text or ""])
    signals: dict[str, Any] = {
        "normalized_subject": normalize_subject(subject),
        "sender_domain": sender_domain(sender),
        "list_id": headers.get("list-id"),
        "in_reply_to": headers.get("in-reply-to"),
        "references": headers.get("references"),
        "domains": sorted(set(URL_DOMAIN_RE.findall(haystack)))[:8],
    }
    for key, pattern in {
        "order_id": ORDER_RE,
        "ticket_id": TICKET_RE,
        "tracking_id": TRACKING_RE,
        "invoice_id": INVOICE_RE,
        "booking_id": BOOKING_RE,
        "application_id": APPLICATION_RE,
    }.items():
        match = pattern.search(haystack)
        if match:
            signals[key] = match.group(1).strip().lower()
    return signals


def _headers(part: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for header in part.get("headers", []) if isinstance(part.get("headers"), list) else []:
        name = str(header.get("name") or "").strip().lower()
        value = str(header.get("value") or "").strip()
        if name and value:
            headers[name] = value
    return headers


def _extract_bodies(part: dict[str, Any]) -> tuple[str | None, str | None]:
    html_parts: list[str] = []
    text_parts: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        mime_type = str(node.get("mimeType") or "").lower()
        body = node.get("body") if isinstance(node.get("body"), dict) else {}
        data = body.get("data") if isinstance(body, dict) else None
        decoded = _decode_body(data) if isinstance(data, str) else ""
        if decoded:
            if mime_type == "text/html":
                html_parts.append(decoded)
            elif mime_type == "text/plain":
                text_parts.append(decoded)
        for child in node.get("parts", []) if isinstance(node.get("parts"), list) else []:
            if isinstance(child, dict):
                walk(child)

    walk(part)
    return ("\n".join(html_parts).strip() or None, "\n".join(text_parts).strip() or None)


def _decode_body(value: str) -> str:
    try:
        padding = "=" * (-len(value) % 4)
        return urlsafe_b64decode(f"{value}{padding}".encode("utf-8")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _internal_date_iso(value: object) -> str | None:
    if value is None:
        return None
    try:
        milliseconds = int(str(value))
    except ValueError:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()
