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
TICKET_RE = re.compile(
    rf"""(?ix)
    \b(?:
      ticket|case|request|service\s+request|case\s+reference|
      (?:unique\s+)?reference(?:\s+number)?(?:\s+for\s+this\s+correspondence)?
    )
    (?:\s*(?:id|number|no\.?|\#))?
    \s*(?:[:\#.-]|\bis\b|\s)\s*
    {STRICT_ID}
    """
)
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
ANCHOR_RE = re.compile(r"""(?is)<a\b[^>]*\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))[^>]*>(.*?)</a>""")
QUOTE_BLOCK_RE = re.compile(r"(?is)<blockquote\b[^>]*>")
QUOTE_BLOCK_CLOSE_RE = re.compile(r"(?is)</blockquote\s*>")
BLOCK_CLOSE_RE = re.compile(r"(?is)</\s*(div|p|tr|table|li|h[1-6]|section|article)\s*>")
BR_RE = re.compile(r"(?is)<\s*br\s*/?\s*>")
EXTERNAL_WARNING_RE = re.compile(r"(?i)\bexternal\s+email\b|be\s+very\s+careful\s+before\s+clicking")
CLASSIFICATION_RE = re.compile(r"(?i)^classification\s*[-:]\s*(.+)$")
QUOTE_START_RE = re.compile(r"(?i)^(?:>+\s*)?(on\s+.{1,180}\bwrote:|from:\s+.+\bsent:\s+.+\bto:\s+.+|from:\s+.+)$")
OUTLOOK_HEADER_RE = re.compile(r"(?i)^(from|sent|to|cc|subject):\s+")
SIGNATURE_START_RE = re.compile(r"(?i)^(best|best regards|regards|thanks|thank you|sincerely|cheers),?$")
FOOTER_START_RE = re.compile(
    r"(?i)^(important communication update|disclaimer:|regd\.?\s+office:|official email domains:|for your security,|.*confidential and intended|.*not responsible for.*viruses)"
)
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
    html_body, text_body = _extract_bodies(
        payload_part,
        message_id=message_id,
        attachment_resolver=inline_attachment_resolver,
    )
    html_is_rich = _is_rich_email_html(html_body) if html_body else False
    sanitized_html = sanitize_email_html(html_body) if html_body and html_is_rich else None
    render_document = html_render_document(
        html_body,
        payload_part=payload_part,
        message_id=message_id,
        inline_attachment_resolver=inline_attachment_resolver,
    )
    extracted_text = html_to_text(html_body) if html_body and not html_is_rich else text_body or html_to_text(html_body or "")
    reader_text = _reader_text_from_plain(extracted_text)
    cleaned_text = clean_ai_text(reader_text) or compact_text(reader_text)
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
        # Reader text is presentation data, so preserve Gmail's paragraph and
        # header boundaries. Search/grouping signals use the compact copy above.
        "text_body": reader_text or None,
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


def build_thread_message_reader(
    *,
    html_render_document: str | None,
    html_body: str | None,
    text_body: str | None,
    snippet: str | None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the clean reader payload used by native clients.

    Raw HTML stays available through the existing fields; this payload is only
    the product-reader projection of a message body.
    """

    html_source = html_render_document or html_body
    source_text = _reader_text_from_html(html_source) if html_source else _reader_text_from_plain(text_body or snippet or "")
    parts = _split_reader_text(source_text)
    if _looks_like_mailing_list_digest(source_text, headers or {}):
        # A list digest embeds RFC-style From/Subject/To headers for each
        # article. Those are message content, not a quoted reply boundary.
        # Keep the complete digest readable in the primary conversation body.
        parts = {
            "primary_text": _normalize_reader_text(source_text).strip(),
            "markers": parts["markers"],
            "signature_text": "",
            "quoted_text": "",
            "footer_text": "",
        }
    primary_text = parts["primary_text"] or _reader_text_from_plain(text_body or snippet or "")
    primary_text = primary_text or compact_text(snippet)
    html_is_rich = _is_rich_email_html(html_source or "") if html_source else False
    quote_detected = bool(parts["quoted_text"])

    return {
        "primary_text": primary_text or "Loading email...",
        "render_mode": "rich_html" if html_is_rich else "plain_conversation",
        "markers": parts["markers"],
        "signature_text": parts["signature_text"] or None,
        "quoted_text": parts["quoted_text"] or None,
        "footer_text": parts["footer_text"] or None,
        "original_html_available": bool((html_render_document or "").strip() or (html_body or "").strip()),
        "html_is_rich": html_is_rich,
        "quote_detected": quote_detected,
    }


def html_body_for_reader(value: str | None) -> str | None:
    """Return sanitized HTML when it is available for the reader."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    return candidate


def html_render_document_for_reader(value: str | None) -> str | None:
    """Return a preserved HTML document when Gmail supplied HTML."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    return candidate


def html_render_document(
    value: str | None,
    *,
    payload_part: dict[str, Any],
    message_id: str,
    inline_attachment_resolver: InlineAttachmentResolver | None = None,
) -> str | None:
    if not value or not value.strip():
        return None
    cleaned = sanitize_email_render_document(value.strip())
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
        if _is_attachment_part(node):
            return False
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


def gmail_payload_has_unresolved_text_body(payload: dict[str, Any]) -> bool:
    """Return whether Gmail stored a text MIME body behind an unresolved attachment ID."""
    part = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload

    def walk(node: dict[str, Any]) -> bool:
        if _is_attachment_part(node):
            return False
        mime_type = str(node.get("mimeType") or "").lower()
        body = node.get("body") if isinstance(node.get("body"), dict) else {}
        data = body.get("data") if isinstance(body, dict) else None
        attachment_id = body.get("attachmentId") if isinstance(body, dict) else None
        if (
            mime_type in {"text/html", "text/plain"}
            and isinstance(attachment_id, str)
            and attachment_id
            and not (isinstance(data, str) and data.strip())
        ):
            return True
        for child in node.get("parts", []) if isinstance(node.get("parts"), list) else []:
            if isinstance(child, dict) and walk(child):
                return True
        return False

    return walk(part) if isinstance(part, dict) else False


def mark_full_gmail_payload_body_fetch_status(parsed: dict[str, Any]) -> dict[str, Any]:
    """Mark a parsed Gmail full response terminal unless every body alternative is unresolved."""
    has_resolved_body = any(
        isinstance(parsed.get(field), str) and bool(str(parsed[field]).strip())
        for field in ("text_body", "html_body_sanitized", "html_render_document")
    )
    unresolved_body = gmail_payload_has_unresolved_text_body(parsed.get("raw_payload") or {})
    # Only the full Gmail representation proves that an empty attachment list
    # is complete. Metadata responses intentionally leave this false so they
    # cannot erase descriptors learned by an earlier body hydration.
    parsed["attachment_descriptors_ready"] = True
    parsed["body_fetch_status"] = "missing" if unresolved_body and not has_resolved_body else "fetched"
    if parsed["body_fetch_status"] == "fetched":
        parsed["raw_payload"] = normalized_gmail_payload_projection(parsed.get("raw_payload") or {})
    return parsed


def normalized_gmail_payload_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain MIME structure and attachment descriptors without body bytes.

    Reader text, sanitized HTML, CID assets, and headers are persisted in their
    normalized columns. Keeping Gmail's base64 body data again in raw JSON
    doubles storage and leaves an unnecessary second rendering source.
    """

    def project_part(part: dict[str, Any]) -> dict[str, Any]:
        projected: dict[str, Any] = {}
        for key in ("partId", "mimeType", "filename"):
            value = part.get(key)
            if value is not None:
                projected[key] = value
        headers = part.get("headers")
        if isinstance(headers, list):
            projected["headers"] = [
                {"name": item.get("name"), "value": item.get("value")}
                for item in headers
                if isinstance(item, dict) and item.get("name") is not None
            ]
        body = part.get("body")
        if isinstance(body, dict):
            descriptor = {
                key: body[key]
                for key in ("attachmentId", "size")
                if body.get(key) is not None
            }
            if descriptor:
                projected["body"] = descriptor
        children = part.get("parts")
        if isinstance(children, list):
            projected["parts"] = [
                project_part(child)
                for child in children
                if isinstance(child, dict)
            ]
        return projected

    projected_payload = {
        key: payload[key]
        for key in ("id", "threadId", "historyId", "labelIds", "internalDate", "snippet")
        if payload.get(key) is not None
    }
    root = payload.get("payload")
    if isinstance(root, dict):
        projected_payload["payload"] = project_part(root)
    return projected_payload


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


def _reader_text_from_html(value: str | None) -> str:
    if not value:
        return ""
    readable = HIDDEN_HTML_BLOCK_RE.sub(" ", HTML_COMMENT_RE.sub(" ", SCRIPT_STYLE_RE.sub(" ", value)))
    readable = QUOTE_BLOCK_RE.sub("\n\n__QUOTE_START__\n", readable)
    readable = QUOTE_BLOCK_CLOSE_RE.sub("\n__QUOTE_END__\n\n", readable)
    readable = ANCHOR_RE.sub(_reader_anchor_text, readable)
    readable = BR_RE.sub("\n", readable)
    readable = BLOCK_CLOSE_RE.sub("\n\n", readable)
    readable = TAG_RE.sub(" ", readable)
    return _normalize_reader_text(html.unescape(readable))


def _reader_anchor_text(match: re.Match[str]) -> str:
    href = next((group for group in match.groups()[:3] if group), "")
    label = compact_text(TAG_RE.sub(" ", match.group(4)))
    if not label:
        return href
    normalized_href = href.removeprefix("mailto:")
    if normalized_href.lower() == label.lower() or href.lower().startswith("mailto:"):
        return label
    if href.lower().startswith(("http://", "https://")):
        return f"{label} ({href})"
    return label


def _reader_text_from_plain(value: str | None) -> str:
    return _normalize_reader_text(value or "")


def _normalize_reader_text(value: str) -> str:
    value = ZERO_WIDTH_RE.sub("", html.unescape(value))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in value.split("\n")]
    output: list[str] = []
    pending_blank = False
    for line in lines:
        if not line:
            pending_blank = bool(output)
            continue
        if pending_blank and output[-1] != "":
            output.append("")
        output.append(line)
        pending_blank = False
    return "\n".join(output).strip()


def _split_reader_text(value: str) -> dict[str, Any]:
    lines = [line.strip() for line in _normalize_reader_text(value).split("\n")]
    primary: list[str] = []
    signature: list[str] = []
    quoted: list[str] = []
    footer: list[str] = []
    markers: list[dict[str, str]] = []
    mode = "primary"
    outlook_header_run = 0

    for line in lines:
        if not line:
            _append_blank_for_mode(mode, primary, signature, quoted, footer)
            continue
        if line == "__QUOTE_START__":
            mode = "quoted"
            continue
        if line == "__QUOTE_END__":
            mode = "primary"
            continue

        marker = _reader_marker(line)
        if marker:
            _append_marker(markers, marker)
            continue

        if FOOTER_START_RE.search(line):
            mode = "footer"

        if mode != "footer" and (_is_quote_start(line) or _starts_outlook_header_quote(line, outlook_header_run)):
            mode = "quoted"

        if mode == "primary" and primary and SIGNATURE_START_RE.match(line):
            mode = "signature"

        if mode == "primary":
            primary.append(line)
        elif mode == "signature":
            signature.append(line)
        elif mode == "quoted":
            quoted.append(line)
        elif mode == "footer":
            footer.append(line)

        outlook_header_run = outlook_header_run + 1 if OUTLOOK_HEADER_RE.match(line) else 0

    return {
        "primary_text": _join_reader_lines(primary),
        "markers": markers,
        "signature_text": _join_reader_lines(signature),
        "quoted_text": _join_reader_lines(quoted),
        "footer_text": _join_reader_lines(footer),
    }


def _looks_like_mailing_list_digest(value: str, headers: dict[str, str]) -> bool:
    """Distinguish embedded digest articles from reply/forward history."""
    if not str(headers.get("list-id") or "").strip():
        return False
    normalized = _normalize_reader_text(value)
    return bool(
        re.search(r"(?im)^today(?:'|’)?s topics:\s*$", normalized)
        and re.search(r"(?im)^message:\s*\d+\s*$", normalized)
        and re.search(r"(?im)^subject:\s*\S", normalized)
    )


def _append_blank_for_mode(mode: str, primary: list[str], signature: list[str], quoted: list[str], footer: list[str]) -> None:
    target = {"primary": primary, "signature": signature, "quoted": quoted, "footer": footer}.get(mode, primary)
    if target and target[-1] != "":
        target.append("")


def _reader_marker(line: str) -> dict[str, str] | None:
    classification = CLASSIFICATION_RE.match(line)
    if classification:
        label = classification.group(1).strip() or "Classification"
        return {"kind": "classification", "label": label, "text": line}
    if EXTERNAL_WARNING_RE.search(line):
        return {"kind": "external_warning", "label": "External", "text": line}
    return None


def _append_marker(markers: list[dict[str, str]], marker: dict[str, str]) -> None:
    key = (marker["kind"], marker["label"].lower(), marker["text"].lower())
    existing = {(item["kind"], item["label"].lower(), item["text"].lower()) for item in markers}
    if key not in existing:
        markers.append(marker)


def _is_quote_start(line: str) -> bool:
    return bool(QUOTE_START_RE.match(line) or (line.startswith(">") and len(line) > 1))


def _starts_outlook_header_quote(line: str, previous_header_count: int) -> bool:
    return previous_header_count >= 1 and bool(OUTLOOK_HEADER_RE.match(line))


def _join_reader_lines(lines: list[str]) -> str:
    output: list[str] = []
    for line in lines:
        if not line:
            if output and output[-1] != "":
                output.append("")
            continue
        output.append(line)
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output).strip()


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
    if (
        table_element_count == 1
        and table_tag_count >= 4
        and style_count + class_count >= 4
        and len(value) > max(900, text_length * 2)
    ):
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


def _is_attachment_part(part: dict[str, Any]) -> bool:
    mime_type = str(part.get("mimeType") or "").strip().lower()
    filename = str(part.get("filename") or "").strip()
    disposition = _headers(part).get("content-disposition", "").lower()
    return mime_type == "message/rfc822" or bool(filename) or disposition.startswith("attachment")


def _extract_bodies(
    part: dict[str, Any],
    *,
    message_id: str,
    attachment_resolver: InlineAttachmentResolver | None,
) -> tuple[str | None, str | None]:
    html_parts: list[str] = []
    text_parts: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        if _is_attachment_part(node):
            return
        mime_type = str(node.get("mimeType") or "").lower()
        body = node.get("body") if isinstance(node.get("body"), dict) else {}
        data = body.get("data") if isinstance(body, dict) else None
        attachment_id = body.get("attachmentId") if isinstance(body, dict) else None
        if (
            mime_type in {"text/html", "text/plain"}
            and not (isinstance(data, str) and data.strip())
            and isinstance(attachment_id, str)
            and attachment_id
            and attachment_resolver is not None
        ):
            data = attachment_resolver(message_id, attachment_id)
            if isinstance(data, str) and data:
                body["data"] = data
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
        if str(node.get("mimeType") or "").strip().lower() == "message/rfc822":
            return
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
