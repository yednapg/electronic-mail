from __future__ import annotations

"""Gmail MIME extraction, HTML sanitization, and grouping signal extraction."""

from base64 import b64encode, urlsafe_b64decode
from email.utils import getaddresses
import hashlib
import html
import re
from typing import Any, Callable

SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style|iframe|object|embed|form|input|button|textarea|select)[^>]*>.*?</\1>")
UNSAFE_TAG_RE = re.compile(r"(?is)</?(script|style|iframe|object|embed|form|input|button|textarea|select)[^>]*>")
HTML_COMMENT_RE = re.compile(r"(?is)<!--.*?-->")
HIDDEN_HTML_BLOCK_RE = re.compile(
    r"(?is)<([a-z0-9]+)\b(?=[^>]*\bstyle\s*=\s*['\"][^'\"]*(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|color\s*:\s*transparent|font-size\s*:\s*0))[^>]*>.*?</\s*\1\s*>"
)
RENDER_UNSAFE_BLOCK_RE = re.compile(r"(?is)<(script|iframe|object|embed|form|input|button|textarea|select)[^>]*>.*?</\1>")
RENDER_UNSAFE_TAG_RE = re.compile(r"(?is)</?(script|iframe|object|embed|form|input|button|textarea|select)[^>]*>")
META_REFRESH_RE = re.compile(r"(?is)<meta\b(?=[^>]*http-equiv\s*=\s*(['\"]?)refresh\1)[^>]*>")
EVENT_ATTR_RE = re.compile(r"\s+on[a-zA-Z]+\s*=\s*(['\"]).*?\1", re.DOTALL)
JS_HREF_RE = re.compile(r"(?i)(href|src)\s*=\s*(['\"])\s*javascript:[^'\"]*\2")
CID_SRC_RE = re.compile(r"(?i)\b(src|background)\s*=\s*(['\"])\s*cid:([^'\"]+)\2")
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
IMG_HTML_TAG_RE = re.compile(r"(?is)<\s*img\b[^>]*>")
PICTURE_HTML_TAG_RE = re.compile(r"(?is)<\s*(picture|source)\b")
TABLE_HTML_TAG_RE = re.compile(r"(?is)<\s*(table|tbody|thead|tfoot|tr|td|th)\b")
LAYOUT_HTML_TAG_RE = re.compile(r"(?is)<\s*(center|font|hr)\b")
STYLE_ATTR_RE = re.compile(r"(?is)\sstyle\s*=")
CLASS_ATTR_RE = re.compile(r"(?is)\sclass\s*=")
HTML_DOCUMENT_RE = re.compile(r"(?is)<\s*(?:!doctype\s+html|html)\b")
HTML_ATTR_RE = re.compile(r"""(?is)\b([a-z0-9_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""")
CSS_DIMENSION_RE = re.compile(r"(?is)\b(width|height)\s*:\s*([0-9.]+)\s*px")
TRACKING_IMAGE_RE = re.compile(r"(?is)(/wf/open|[?&]open=|/open[?/]?|/track|tracking|pixel|beacon)")
InlineAttachmentResolver = Callable[[str, str], str | None]


def parse_gmail_message(
    payload: dict[str, Any],
    *,
    user_id: str,
    inline_attachment_resolver: InlineAttachmentResolver | None = None,
) -> dict[str, Any]:
    message_id = str(payload.get("id") or "")
    gmail_thread_id = str(payload.get("threadId") or "") or None
    payload_part = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    headers = _headers(payload_part)
    label_ids = [str(label) for label in payload.get("labelIds", []) if label]
    html_body, text_body = _extract_bodies(payload_part)
    html_is_rich = _is_rich_email_html(html_body) if html_body else False
    sanitized_html = sanitize_email_html(html_body) if html_body and html_is_rich else None
    render_document = html_render_document(
        html_body,
        payload_part=payload_part,
        message_id=message_id,
        inline_attachment_resolver=inline_attachment_resolver,
    )
    extracted_text = html_to_text(html_body) if html_body and not html_is_rich else text_body or html_to_text(html_body or "")
    cleaned_text = clean_ai_text(extracted_text) or compact_text(extracted_text)
    snippet_text = compact_text(str(payload.get("snippet") or ""))
    signal_text = cleaned_text or snippet_text
    subject = headers.get("subject")
    sender = headers.get("from")
    recipients = {
        "to": headers.get("to"),
        "cc": headers.get("cc"),
        "bcc": headers.get("bcc"),
    }
    body_hash = hashlib.sha256(f"{subject or ''}\n{signal_text}\n{html_body or ''}".encode("utf-8", errors="ignore")).hexdigest()
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
        "html_render_document": render_document,
        "text_body": cleaned_text or None,
        "extracted_signals": extract_signals(subject=subject, sender=sender, text=signal_text, headers=headers),
        "body_hash": body_hash,
    }


def sanitize_email_html(value: str) -> str:
    cleaned = SCRIPT_STYLE_RE.sub("", value)
    cleaned = UNSAFE_TAG_RE.sub("", cleaned)
    cleaned = EVENT_ATTR_RE.sub("", cleaned)
    cleaned = JS_HREF_RE.sub(r"\1=\2#\2", cleaned)
    return cleaned.strip()


def sanitize_email_render_document(value: str) -> str:
    cleaned = RENDER_UNSAFE_BLOCK_RE.sub("", value)
    cleaned = RENDER_UNSAFE_TAG_RE.sub("", cleaned)
    cleaned = META_REFRESH_RE.sub("", cleaned)
    cleaned = EVENT_ATTR_RE.sub("", cleaned)
    cleaned = JS_HREF_RE.sub(r"\1=\2#\2", cleaned)
    return cleaned.strip()


def html_to_text(value: str) -> str:
    readable = HIDDEN_HTML_BLOCK_RE.sub(" ", HTML_COMMENT_RE.sub(" ", SCRIPT_STYLE_RE.sub(" ", value)))
    without_tags = TAG_RE.sub(" ", readable)
    return compact_text(html.unescape(without_tags))


def html_body_for_reader(value: str | None) -> str | None:
    """Return HTML only when it has email layout worth rendering as HTML."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if _is_rich_email_html(candidate):
        return candidate
    return None


def html_render_document_for_reader(value: str | None) -> str | None:
    """Return a preserved HTML document only when it has email layout worth rendering."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if _is_rich_email_html(candidate):
        return candidate
    return None


def html_render_document(
    value: str | None,
    *,
    payload_part: dict[str, Any],
    message_id: str,
    inline_attachment_resolver: InlineAttachmentResolver | None = None,
) -> str | None:
    if not value or not _is_rich_email_html(value):
        return None
    cleaned = sanitize_email_render_document(value)
    if not cleaned:
        return None
    inline_images = _inline_image_data_urls(
        payload_part,
        message_id=message_id,
        inline_attachment_resolver=inline_attachment_resolver,
    )
    if inline_images:
        cleaned = _rewrite_cid_sources(cleaned, inline_images)
    return cleaned


def has_persisted_renderable_body(
    *,
    text_body: str | None,
    html_body: str | None,
    html_render_document: str | None = None,
    raw_payload: dict[str, Any],
) -> bool:
    if html_render_document or html_body:
        return True
    if not text_body:
        return False
    return gmail_payload_has_renderable_body(raw_payload)


def gmail_payload_has_renderable_body(payload: dict[str, Any]) -> bool:
    part = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload

    def walk(node: dict[str, Any]) -> bool:
        mime_type = str(node.get("mimeType") or "").lower()
        body = node.get("body") if isinstance(node.get("body"), dict) else {}
        data = body.get("data") if isinstance(body, dict) else None
        if mime_type in {"text/html", "text/plain"} and isinstance(data, str) and data.strip():
            return True
        for child in node.get("parts", []) if isinstance(node.get("parts"), list) else []:
            if isinstance(child, dict) and walk(child):
                return True
        return False

    return walk(part) if isinstance(part, dict) else False


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


def _is_rich_email_html(value: str) -> bool:
    if PICTURE_HTML_TAG_RE.search(value) or _has_substantive_image(value):
        return True
    table_element_count = len(re.findall(r"<\s*table\b", value, flags=re.IGNORECASE))
    table_tag_count = len(TABLE_HTML_TAG_RE.findall(value))
    layout_tag_count = len(LAYOUT_HTML_TAG_RE.findall(value))
    style_count = len(STYLE_ATTR_RE.findall(value))
    class_count = len(CLASS_ATTR_RE.findall(value))
    text_length = len(html_to_text(value))
    source_is_document_sized = len(value) > max(700, text_length * 2)
    if table_element_count >= 2 and table_tag_count >= 4 and source_is_document_sized:
        return True
    if table_element_count >= 2 and table_tag_count >= 2 and (style_count >= 1 or class_count >= 1) and len(value) > max(500, text_length * 2):
        return True
    if layout_tag_count >= 2 and (style_count >= 1 or class_count >= 1) and source_is_document_sized:
        return True
    return False


def _has_substantive_image(value: str) -> bool:
    for tag in IMG_HTML_TAG_RE.findall(value):
        attrs = _html_attrs(tag)
        style = attrs.get("style", "")
        if _is_hidden_image(style):
            continue
        dimensions = [
            _numeric_css_size(attrs.get("width")),
            _numeric_css_size(attrs.get("height")),
            *(_numeric_css_size(match.group(2)) for match in CSS_DIMENSION_RE.finditer(style)),
        ]
        if any(size is not None and size <= 2 for size in dimensions):
            continue
        if any(size is not None and size >= 24 for size in dimensions):
            return True
        src = attrs.get("src", "")
        if src and TRACKING_IMAGE_RE.search(src):
            continue
        if src:
            return True
    return False


def _html_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in HTML_ATTR_RE.finditer(tag):
        attrs[match.group(1).lower()] = next((group for group in match.groups()[1:] if group is not None), "")
    return attrs


def _is_hidden_image(style: str) -> bool:
    normalized = style.replace(" ", "").lower()
    return "display:none" in normalized or "visibility:hidden" in normalized or "opacity:0" in normalized


def _numeric_css_size(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"([0-9.]+)", value)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


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
    html_body = _choose_best_html_part(html_parts)
    return (html_body, "\n".join(text_parts).strip() or None)


def _choose_best_html_part(parts: list[str]) -> str | None:
    candidates = [part.strip() for part in parts if part.strip()]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    full_documents = [part for part in candidates if HTML_DOCUMENT_RE.search(part)]
    pool = full_documents or candidates
    return max(pool, key=len)


def _inline_image_data_urls(
    part: dict[str, Any],
    *,
    message_id: str,
    inline_attachment_resolver: InlineAttachmentResolver | None,
) -> dict[str, str]:
    images: dict[str, str] = {}

    def walk(node: dict[str, Any]) -> None:
        headers = _headers(node)
        content_id = _normalize_content_id(headers.get("content-id"))
        mime_type = str(node.get("mimeType") or "").lower()
        if content_id and mime_type.startswith("image/"):
            body = node.get("body") if isinstance(node.get("body"), dict) else {}
            data = body.get("data") if isinstance(body, dict) else None
            attachment_id = body.get("attachmentId") if isinstance(body, dict) else None
            if not isinstance(data, str) and isinstance(attachment_id, str) and inline_attachment_resolver:
                data = inline_attachment_resolver(message_id, attachment_id)
            if isinstance(data, str) and data:
                data_url = _data_url_from_gmail_data(mime_type, data)
                if data_url:
                    images[content_id] = data_url
        for child in node.get("parts", []) if isinstance(node.get("parts"), list) else []:
            if isinstance(child, dict):
                walk(child)

    walk(part)
    return images


def _rewrite_cid_sources(value: str, inline_images: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        attribute, quote, cid_value = match.group(1), match.group(2), match.group(3)
        data_url = inline_images.get(_normalize_content_id(cid_value))
        if not data_url:
            return match.group(0)
        return f"{attribute}={quote}{data_url}{quote}"

    return CID_SRC_RE.sub(replace, value)


def _normalize_content_id(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip("<>").lower()


def _data_url_from_gmail_data(mime_type: str, value: str) -> str | None:
    decoded = _decode_body_bytes(value)
    if not decoded:
        return None
    encoded = b64encode(decoded).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _decode_body(value: str) -> str:
    data = _decode_body_bytes(value)
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _decode_body_bytes(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return urlsafe_b64decode(f"{value}{padding}".encode("utf-8"))
    except Exception:
        return b""


def _internal_date_iso(value: object) -> str | None:
    if value is None:
        return None
    try:
        milliseconds = int(str(value))
    except ValueError:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()
