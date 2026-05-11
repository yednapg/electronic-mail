from __future__ import annotations

"""Small deterministic copy polish for user-facing generated text."""

from email.utils import getaddresses
import re
from collections.abc import Iterable

from app.db.models import StoredSourceRecord


USER_PREFIX_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"^User says they(?:'|\u2019)d\b", "You said you'd"),
    (r"^User says they\b", "You said you"),
    (r"^User says their\b", "You said your"),
    (r"^User formally complains\b", "You formally complained"),
    (r"^User complains\b", "You complained"),
    (r"^User requests\b", "You requested"),
    (r"^User requested\b", "You requested"),
    (r"^User corrects\b", "You corrected"),
    (r"^User corrected\b", "You corrected"),
    (r"^User escalates\b", "You escalated"),
    (r"^User escalated\b", "You escalated"),
    (r"^User forwards\b", "You forwarded"),
    (r"^User forwarded\b", "You forwarded"),
    (r"^User replies\b", "You replied"),
    (r"^User replied\b", "You replied"),
    (r"^User reports\b", "You reported"),
    (r"^User reported\b", "You reported"),
    (r"^User sends\b", "You sent"),
    (r"^User sent\b", "You sent"),
    (r"^User shares\b", "You shared"),
    (r"^User shared\b", "You shared"),
    (r"^User tells\b", "You told"),
    (r"^User told\b", "You told"),
    (r"^User asks\b", "You asked"),
    (r"^User asked\b", "You asked"),
    (r"^User notes\b", "You noted"),
    (r"^User noted\b", "You noted"),
    (r"^User confirms\b", "You confirmed"),
    (r"^User confirmed\b", "You confirmed"),
    (r"^User warns\b", "You warned"),
    (r"^User warned\b", "You warned"),
    (r"^User says\b", "You said"),
)

ACCOUNT_OWNER_VERB_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("formally complains", "You formally complained"),
    ("complains", "You complained"),
    ("forwarded", "You forwarded"),
    ("replied", "You replied"),
    ("submitted", "You submitted"),
    ("sent", "You sent"),
    ("asked", "You asked"),
    ("requested", "You requested"),
)


def humanize_account_copy(
    value: str | None,
    *,
    record: StoredSourceRecord | None = None,
    account_emails: Iterable[str] = (),
) -> str | None:
    """Return generated copy from the account owner's point of view."""
    if value is None:
        return None

    text = " ".join(value.split()).strip()
    if not text:
        return None

    text = text.replace("\u043e\u0431\u0435\u0449ing", "promising")
    text = text.replace("\u091c\u093e\u0930\u0940", "issue")
    text = replace_user_prefix(text)
    if record is not None and is_account_owner_record(record, account_emails):
        text = replace_named_account_owner_prefix(text, record)
    text = fix_account_owner_pronouns(text)
    if record is not None and is_account_owner_record(record, account_emails):
        text = replace_sender_name_prefix(text, record)
    return text


def replace_user_prefix(value: str) -> str:
    """Turn model-ish 'User did X' summaries into second-person copy."""
    for pattern, replacement in USER_PREFIX_REPLACEMENTS:
        value = re.sub(pattern, replacement, value, count=1, flags=re.IGNORECASE)
    return value


def replace_named_account_owner_prefix(value: str, record: StoredSourceRecord) -> str:
    """Handle legacy local summaries that used the account owner's sender name."""
    for name in sender_name_candidates(record):
        for verb, replacement in ACCOUNT_OWNER_VERB_REPLACEMENTS:
            pattern = rf"^{re.escape(name)}\s+{verb}\b"
            value = re.sub(pattern, replacement, value, count=1, flags=re.IGNORECASE)
    return value


def fix_account_owner_pronouns(value: str) -> str:
    """Clean up agreement after converting third-person model copy to "you"."""
    if not value.startswith("You "):
        return value

    replacements = (
        (r"\band says\b", "and said"),
        (r"\band asks\b", "and asked"),
        (r"\band warns\b", "and warned"),
        (r"\band requests\b", "and requested"),
        (r"\band shares\b", "and shared"),
        (r", says\b", ", said"),
        (r"\bthey may\b", "you may"),
        (r"\bthey(?:'|\u2019)d\b", "you'd"),
        (r"\bthey are\b", "you are"),
        (r"\bthey\b", "you"),
        (r"\btheir\b", "your"),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value


def replace_sender_name_prefix(value: str, record: StoredSourceRecord) -> str:
    """Replace the account owner's sender name in sent-record summaries."""
    for name in sender_name_candidates(record):
        match = re.match(rf"^{re.escape(name)}\s+", value, flags=re.IGNORECASE)
        if match is None:
            continue

        suffix = value[match.end() :]
        if not suffix:
            return "You"
        return f"You {lowercase_first_letter(suffix)}"

    return value


def sender_name_candidates(record: StoredSourceRecord) -> list[str]:
    sender = record.sender or payload_string(record.raw_payload, "from") or payload_string(record.raw_payload, "sender")
    if sender is None:
        return []

    display_name = sender.split("<", 1)[0].strip().strip('"')
    if not display_name:
        return []

    candidates = [display_name]
    first_name = display_name.split()[0] if display_name.split() else ""
    if first_name and first_name.lower() != display_name.lower():
        candidates.append(first_name)

    return sorted(set(candidates), key=len, reverse=True)


def is_sent_record(record: StoredSourceRecord) -> bool:
    labels = payload_string_list(record.raw_payload, "label_ids") or payload_string_list(record.raw_payload, "labelIds")
    return any(label.upper() == "SENT" for label in labels)


def is_account_owner_record(record: StoredSourceRecord, account_emails: Iterable[str] = ()) -> bool:
    if is_sent_record(record):
        return True

    sender_email = extract_email_address(
        record.sender or payload_string(record.raw_payload, "from") or payload_string(record.raw_payload, "sender")
    )
    if sender_email is None:
        return False

    normalized_account_emails = {email.strip().lower() for email in account_emails if email.strip()}
    return sender_email.lower() in normalized_account_emails


def infer_account_emails_from_records(records: Iterable[StoredSourceRecord]) -> set[str]:
    """Infer local account email addresses from sent records already in backend state."""
    emails: set[str] = set()
    for record in records:
        if not is_sent_record(record):
            continue
        for value in (
            record.sender,
            payload_string(record.raw_payload, "from"),
            payload_string(record.raw_payload, "sender"),
        ):
            emails.update(extract_email_addresses(value))
    return emails


def extract_email_address(value: str | None) -> str | None:
    addresses = extract_email_addresses(value)
    return addresses[0] if addresses else None


def extract_email_addresses(value: str | None) -> list[str]:
    if value is None:
        return []

    parsed = [email.strip().lower() for _name, email in getaddresses([value]) if email.strip()]
    if parsed:
        return parsed

    return [match.group(0).strip().lower() for match in re.finditer(r"\b[^@\s<>]+@[^@\s<>]+\b", value)]


def payload_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def payload_string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def lowercase_first_letter(value: str) -> str:
    if not value:
        return value
    return f"{value[0].lower()}{value[1:]}"
