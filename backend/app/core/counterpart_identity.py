from __future__ import annotations

"""Privacy-bounded counterpart identity resolution for AI Inbox rows."""

from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses
from collections import Counter
import re
from typing import Any, Iterable, Mapping, Sequence


MAX_COUNTERPART_ENTITIES = 4


@dataclass(frozen=True)
class IdentityCandidate:
    display_name: str
    address: str
    domain: str
    role: str

    def redacted(self) -> dict[str, str]:
        """Return the identity evidence safe to include in an AI request."""
        return {
            "display_name": self.display_name,
            "domain": self.domain,
            "role": self.role,
        }


@dataclass(frozen=True)
class CanonicalIdentityRule:
    label: str
    domains: tuple[str, ...] = ()
    name_phrases: tuple[str, ...] = ()


@dataclass(frozen=True)
class MailboxOwnerIdentity:
    label: str
    addresses: tuple[str, ...]


CANONICAL_IDENTITY_RULES = (
    CanonicalIdentityRule("Domain Harbor", ("domain-harbor.example",), ("domain-harbor",)),
    CanonicalIdentityRule("OpenAI", ("openai.com",), ("openai", "chatgpt")),
    CanonicalIdentityRule("Screener", ("screener.in", "screener.net"), ("screener",)),
    CanonicalIdentityRule(
        "Northstar Bank",
        ("northstar.example", "northstarbank.example", "northstarbank.example"),
        ("northstar", "northstar bank"),
    ),
    CanonicalIdentityRule(
        "HSBC",
        ("bank.example", "bank.example", "bank.example"),
        ("hsbc",),
    ),
    CanonicalIdentityRule(
        "SBI",
        ("bank.example", "bank.example"),
        ("state bank of india", "sbi"),
    ),
    CanonicalIdentityRule(
        "State University",
        ("university.example",),
        ("penn state", "lionpath", "psu enrollment fee"),
    ),
    CanonicalIdentityRule(
        "Linux Foundation",
        ("linuxfoundation.org",),
        ("linux foundation", "lf events"),
    ),
    CanonicalIdentityRule(
        "Charles Schwab",
        ("schwab.com",),
        ("charles schwab", "schwab"),
    ),
    CanonicalIdentityRule(
        "South Park Commons",
        ("southparkcommons.com",),
        ("south park commons",),
    ),
    CanonicalIdentityRule(
        "CCIL",
        ("ccilindia.co.in", "ccilindia.com"),
        ("clearing corporation of india", "ccil"),
    ),
    CanonicalIdentityRule(
        "CVL KRA",
        ("cvlindia.in", "cvlindia.com", "cvlkra.com"),
        ("cvl kra", "cvlkra", "cdsl ventures limited"),
    ),
    CanonicalIdentityRule(
        "CAMS",
        ("camsonline.com", "camskra.com"),
        ("cams", "cams kra", "cams kyc"),
    ),
    CanonicalIdentityRule(
        "Tax Service",
        ("tax.example.gov",),
        ("tax service", "your itr intimation", "income tax"),
    ),
    CanonicalIdentityRule(
        "AI Grants India",
        ("aigrants.in",),
        ("ai grants india", "aigrantsindia"),
    ),
    CanonicalIdentityRule(
        "Amazon Web Services",
        ("amazonaws.com",),
        ("amazon web services", "aws"),
    ),
    CanonicalIdentityRule(
        "Interactive Brokers",
        ("brokerage.example",),
        ("interactive brokers", "ibkr"),
    ),
)

FREE_MAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "icloud.com",
    "me.com",
    "outlook.com",
    "yahoo.com",
}
RELAY_DOMAINS = {
    "adobesign.com",
    "calendar.luma-mail.com",
    "calendly.com",
    "digio.in",
    "luma-mail.com",
    "pretalx.com",
    "shopifyemail.com",
    "stripe.com",
    "tickets.helpdesk.com",
    "townscript.com",
}
GENERIC_DISPLAY_NAMES = {
    "account",
    "accounts",
    "admin",
    "alert",
    "alerts",
    "care",
    "customer care",
    "customer service",
    "default user",
    "do not reply",
    "help",
    "help desk",
    "mail",
    "mailer",
    "no reply",
    "noreply",
    "notification",
    "notifications",
    "operations",
    "service",
    "support",
    "team",
    "updates",
}
DOMAIN_INFRASTRUCTURE_LABELS = {
    "alerts",
    "bank",
    "calendar",
    "co",
    "com",
    "custcomm",
    "edu",
    "email",
    "gov",
    "in",
    "mail",
    "mailer",
    "net",
    "notification",
    "notifications",
    "org",
    "support",
    "tm",
    "updates",
}
GENERIC_ROLE_SUFFIX_PATTERN = re.compile(
    r"(?i)(?:[\s._-]+(?:support(?:\s+dept\.?|\s+department)?|service\s+help|help\s+desk|"
    r"notifications?|alerts?|customer\s+(?:care|service)|client\s+services?|care|accounts?|"
    r"operations?|team|office|department|developer))+$"
)
GENERIC_ROLE_PREFIX_PATTERN = re.compile(
    r"(?i)^(?:support|notifications?|alerts?|customer\s+(?:care|service)|client\s+services?|"
    r"care|accounts?|operations?|team|office|department)[\s._:-]+"
)
REPORTING_VERB_PATTERN = re.compile(
    r"(?i)^(?:confirms?|confirmed|states?|stated|says?|said|advises?|advised|"
    r"reports?|reported|announces?|announced|informs?|informed|notifies?|notified)"
    r"(?:\s+that)?\s+"
)
PRESERVED_IDENTITY_ACRONYMS = {
    "AI",
    "AWS",
    "CAMS",
    "CCIL",
    "CRIF",
    "CVL",
    "Northstar",
    "HSBC",
    "ITR",
    "KRA",
    "KYC",
    "LLC",
    "LLP",
    "NEFT",
    "NRI",
    "PAN",
    "PSU",
    "RTGS",
    "SBI",
}
LOWERCASE_IDENTITY_CONNECTORS = {"and", "at", "by", "for", "in", "of", "on", "the", "to"}


def identity_candidates_for_message(message: Any) -> list[IdentityCandidate]:
    """Return external From identities for received mail and recipients for sent mail."""
    label_ids = _string_list(getattr(message, "label_ids", []))
    if "SENT" in {value.upper() for value in label_ids}:
        recipients = getattr(message, "recipients", {})
        sender_values = _address_candidates(
            [str(getattr(message, "sender", "") or "")],
            role="owner",
        )
        return _recipient_candidates(
            recipients,
            excluded_addresses={candidate.address for candidate in sender_values},
        )

    sender = str(getattr(message, "sender", "") or "").strip()
    return _address_candidates([sender], role="sender") if sender else []


def redacted_identity_candidates(message: Any) -> list[dict[str, str]]:
    return [candidate.redacted() for candidate in identity_candidates_for_message(message)]


def resolve_counterpart_entities(
    message: Any,
    *,
    suggested_entities: Sequence[str] = (),
    context_text: str = "",
) -> list[str]:
    """Resolve bounded, evidence-backed counterpart labels for one message."""
    candidates = identity_candidates_for_message(message)
    validated = _validated_suggestions(
        suggested_entities,
        candidates=candidates,
        context_text=context_text,
    )
    if validated:
        return [_presentation_label(value) for value in validated[:MAX_COUNTERPART_ENTITIES]]

    resolved: list[str] = []
    for candidate in candidates:
        label = _deterministic_label(candidate, context_text=context_text)
        if label:
            _append_unique(resolved, _presentation_label(label))
    return resolved[:MAX_COUNTERPART_ENTITIES]


def aggregate_counterpart_entities(
    records: Iterable[Mapping[str, Any]],
    *,
    context_text: str = "",
    owner_identity: MailboxOwnerIdentity | None = None,
) -> list[str]:
    """Rank the actual correspondence parties across a matter.

    The list shown beside an AI Inbox row is a sender/recipient list, not an
    entity summary.  For received mail only the ``From`` identity is relevant;
    for sent mail the external ``To``/``Cc``/``Bcc`` identities are relevant.
    This deliberately excludes people who merely shared a large incoming
    recipient list and entities mentioned only in the message body.  The
    mailbox owner is included only for a genuinely two-way matter containing
    both sent and received mail.
    """
    counts: dict[str, int] = {}
    header_counts: dict[str, int] = {}
    latest: dict[str, float] = {}
    canonical: dict[str, str] = {}
    first_seen: dict[str, int] = {}
    owner_sent_message = False
    owner_received_message = False
    for record in records:
        suggested_entities = _string_list(record.get("counterpart_entities"))
        message = _RecordMessage(record)
        if owner_identity is not None:
            if "SENT" in {value.upper() for value in message.label_ids}:
                owner_sent_message = True
            else:
                owner_received_message = True
        entities: list[str] = []
        header_keys: set[str] = set()
        for candidate in identity_candidates_for_message(message):
            if owner_identity is not None and candidate.address in owner_identity.addresses:
                continue
            label = _header_participant_label(candidate, context_text=context_text)
            domain_presentation = _domain_backed_presentation(
                suggested_entities,
                candidate=candidate,
            )
            if domain_presentation:
                label = domain_presentation
            label_key = _identity_key(label)
            if label_key:
                header_keys.add(label_key)
            if not label:
                continue
            if owner_identity is not None and _same_person_label(label, owner_identity.label):
                continue
            _append_unique(entities, label)
        timestamp = _timestamp(record.get("latest_message_at"))
        seen_for_message: set[str] = set()
        for entity in entities:
            if owner_identity is not None and _same_person_label(
                entity,
                owner_identity.label,
            ):
                continue
            key = _identity_key(entity)
            if not key or key in seen_for_message:
                continue
            seen_for_message.add(key)
            first_seen.setdefault(key, len(first_seen))
            existing_label = canonical.get(key)
            if existing_label is None or _presentation_quality(entity) > _presentation_quality(
                existing_label
            ):
                canonical[key] = entity
            counts[key] = counts.get(key, 0) + 1
            if key in header_keys:
                header_counts[key] = header_counts.get(key, 0) + 1
            latest[key] = max(latest.get(key, 0.0), timestamp)

    ordered = sorted(
        canonical,
        key=lambda key: (
            -counts[key],
            -latest[key],
            -header_counts.get(key, 0),
            first_seen[key],
        ),
    )
    owner_label = (
        _presentation_label(owner_identity.label)
        if (
            owner_identity is not None
            and owner_identity.label
            and owner_sent_message
            and owner_received_message
        )
        else ""
    )
    external_limit = MAX_COUNTERPART_ENTITIES - (1 if owner_label else 0)
    result = [
        _presentation_label(canonical[key])
        for key in ordered[:external_limit]
    ]
    if owner_label:
        _append_unique(result, owner_label)
    return result[:MAX_COUNTERPART_ENTITIES]


def mailbox_owner_identity(
    sender_headers: Iterable[str],
    *,
    fallback_display_name: str = "",
    fallback_address: str = "",
) -> MailboxOwnerIdentity:
    """Infer the mailbox owner's stable display name from sent-message headers."""
    names: Counter[str] = Counter()
    addresses: set[str] = set()
    for display_name, address in getaddresses([str(value) for value in sender_headers]):
        normalized_address = address.strip().casefold()
        if normalized_address:
            addresses.add(normalized_address)
        cleaned_name = _clean_display_name(display_name)
        if cleaned_name and not _is_generic_display_name(cleaned_name):
            names[cleaned_name] += 1

    normalized_fallback_address = fallback_address.strip().casefold()
    if normalized_fallback_address:
        addresses.add(normalized_fallback_address)
    fallback_name = _clean_display_name(fallback_display_name)
    if fallback_name:
        names[fallback_name] += 1

    label = ""
    if names:
        label = max(
            names,
            key=lambda value: (
                len(_words(value).split()),
                names[value],
                len(value),
                value.casefold(),
            ),
        )
    if not label and normalized_fallback_address:
        local_part = normalized_fallback_address.split("@", 1)[0]
        label = " ".join(part.capitalize() for part in re.split(r"[._-]+", local_part) if part)
    return MailboxOwnerIdentity(
        label=_presentation_label(label),
        addresses=tuple(sorted(addresses)),
    )


def counterpart_aware_headline(title: str, counterpart_entities: Sequence[str]) -> str:
    """Remove a redundant primary counterpart prefix from a one-party headline.

    Multi-party titles are intentionally preserved because names may be needed
    to explain which organization performed each action.
    """
    headline = " ".join(str(title or "").split()).strip()
    entities: list[str] = []
    for raw_entity in counterpart_entities:
        entity = _clean_display_name(str(raw_entity or ""))
        if entity:
            _append_unique(entities, entity)
    if len(entities) != 1 or not headline:
        return headline

    primary = entities[0]
    if primary == "Northstar Bank":
        primary_pattern = r"Northstar(?:\s+Bank)?"
    else:
        suffix = r"(?:\s+Bank)?" if primary in {"Northstar", "HSBC", "SBI"} else ""
        primary_pattern = re.escape(primary) + suffix
    prefix = re.compile(
        rf"(?i)^{primary_pattern}(?=\s|[-—:·|]|$)\s*(?:[-—:·|]\s*)?"
    )
    remainder = prefix.sub("", headline, count=1).strip()
    if not remainder or remainder == headline:
        return headline

    # Once the sender is rendered in its own column, introductory reporting
    # verbs add no meaning: `Northstar · Confirms X` is clearer as `Northstar · X`.
    action = REPORTING_VERB_PATTERN.sub("", remainder, count=1).strip()
    if action:
        remainder = action
    return _sentence_case(remainder)


class _RecordMessage:
    def __init__(self, record: Mapping[str, Any]) -> None:
        self.sender = record.get("sender")
        self.recipients = _json_value(record.get("recipients"), {})
        self.label_ids = _string_list(_json_value(record.get("label_ids"), []))


def _recipient_candidates(
    recipients: Any,
    *,
    excluded_addresses: set[str],
) -> list[IdentityCandidate]:
    if not isinstance(recipients, Mapping):
        return []
    values: list[str] = []
    for field in ("to", "cc", "bcc"):
        values.extend(_header_values(recipients.get(field)))
    return [
        candidate
        for candidate in _address_candidates(values, role="recipient")
        if candidate.address not in excluded_addresses
    ]


def _all_header_candidates(message: Any) -> list[IdentityCandidate]:
    candidates = _address_candidates(
        [str(getattr(message, "sender", "") or "")],
        role="sender",
    )
    recipients = getattr(message, "recipients", {})
    if isinstance(recipients, Mapping):
        for field in ("to", "cc", "bcc"):
            candidates.extend(
                _address_candidates(_header_values(recipients.get(field)), role="recipient")
            )
    return candidates


def _header_participant_label(
    candidate: IdentityCandidate,
    *,
    context_text: str,
) -> str:
    rule = _matching_rule(candidate.display_name, candidate.domain)
    if rule is not None:
        return rule.label

    # This helper now receives only directional counterparts. A bare address
    # is therefore still a real participant: retain a free-mail address as-is
    # or use conservative domain inference for an institutional mailbox.
    display_name = _strip_relay_attribution(candidate.display_name)
    if display_name and not _is_generic_display_name(display_name):
        compact = _strip_generic_role(display_name)
        if compact and not _is_generic_display_name(compact):
            return compact
    return _deterministic_label(candidate, context_text=context_text)


def _domain_backed_presentation(
    suggestions: Sequence[str],
    *,
    candidate: IdentityCandidate,
) -> str:
    """Use semantic output only to format a header-proven domain identity.

    A bare custom-domain sender such as ``support@example-retailer.example``
    deterministically identifies ``example-retailer`` but does not reveal its
    word boundaries or acronym casing.  A suggestion such as ``RBI Retail
    Direct`` may supply that presentation because both normalize to exactly
    the same identity key.  Names merely mentioned in the message body cannot
    pass this equality check.
    """
    display_name = _strip_relay_attribution(candidate.display_name)
    if display_name and not _is_generic_display_name(display_name):
        return ""
    domain_label = _registrable_label(candidate.domain)
    domain_key = _identity_key(domain_label or "")
    if not domain_key:
        return ""

    matches = [
        _presentation_label(value)
        for value in suggestions
        if _identity_key(value) == domain_key
    ]
    if not matches:
        return ""
    return max(matches, key=_presentation_quality)


def _same_person_label(value: str, owner_label: str) -> bool:
    value_key = _identity_key(value)
    owner_key = _identity_key(owner_label)
    if not value_key or not owner_key:
        return False
    if value_key == owner_key:
        return True
    value_words = _words(value).split()
    owner_words = _words(owner_label).split()
    return len(value_words) == 1 and value_words[0] in owner_words


def _address_candidates(values: Sequence[str], *, role: str) -> list[IdentityCandidate]:
    candidates: list[IdentityCandidate] = []
    seen: set[str] = set()
    for display_name, address in getaddresses(values):
        normalized_address = address.strip().casefold()
        if not normalized_address or normalized_address in seen:
            continue
        seen.add(normalized_address)
        domain = normalized_address.rsplit("@", 1)[1] if "@" in normalized_address else ""
        candidates.append(
            IdentityCandidate(
                display_name=_clean_display_name(display_name),
                address=normalized_address,
                domain=domain,
                role=role,
            )
        )
    return candidates


def _deterministic_label(candidate: IdentityCandidate, *, context_text: str) -> str:
    rule = _matching_rule(candidate.display_name, candidate.domain)
    if rule is not None:
        return rule.label

    display_name = _strip_relay_attribution(candidate.display_name)
    context_rule = _matching_rule(display_name, "", context_text=context_text)
    if context_rule is not None and (
        _is_relay_domain(candidate.domain)
        or _contains_phrase(display_name, context_rule.name_phrases)
    ):
        return context_rule.label

    domain_label = _domain_organization_label(candidate.domain)
    if display_name and not _is_generic_display_name(display_name):
        compact = _strip_generic_role(display_name)
        if compact and not _is_generic_display_name(compact):
            if domain_label and _identity_key(domain_label) in _identity_key(compact):
                return compact
            # Unknown named people remain people rather than being replaced by
            # a speculative organization inferred from their custom domain.
            return compact

    if domain_label:
        return domain_label
    return candidate.address if candidate.domain in FREE_MAIL_DOMAINS else display_name


def _validated_suggestions(
    suggestions: Sequence[str],
    *,
    candidates: Sequence[IdentityCandidate],
    context_text: str,
) -> list[str]:
    validated: list[str] = []
    normalized_context = _identity_key(context_text)
    for raw_value in suggestions:
        value = _clean_display_name(str(raw_value or ""))
        if not value:
            continue
        represented_rule = _candidate_rule_for_suggestion(value, candidates)
        if represented_rule is not None:
            _append_unique(validated, represented_rule.label)
            continue
        rule = _rule_for_label_or_name(value)
        if rule is not None:
            supported = any(
                _domain_matches(candidate.domain, rule.domains)
                or _contains_phrase(candidate.display_name, rule.name_phrases)
                for candidate in candidates
            ) or _contains_phrase(context_text, rule.name_phrases)
            if supported:
                _append_unique(validated, rule.label)
            continue

        value_key = _identity_key(value)
        if len(value_key) < 3:
            continue
        supported = value_key in normalized_context or any(
            value_key in _identity_key(candidate.display_name)
            or value_key == _identity_key(_domain_organization_label(candidate.domain) or "")
            or value_key == _identity_key(_registrable_label(candidate.domain) or "")
            for candidate in candidates
        )
        if supported:
            _append_unique(validated, value)
    return validated


def _candidate_rule_for_suggestion(
    value: str,
    candidates: Sequence[IdentityCandidate],
) -> CanonicalIdentityRule | None:
    """Collapse a suggested department/mailbox back to its verified domain."""
    value_key = _identity_key(value)
    if not value_key:
        return None
    for candidate in candidates:
        rule = _matching_rule(candidate.display_name, candidate.domain)
        if rule is None:
            continue
        evidence_keys = {
            _identity_key(candidate.display_name),
            _identity_key(candidate.address),
        }
        if any(
            evidence_key
            and (value_key in evidence_key or evidence_key in value_key)
            for evidence_key in evidence_keys
        ):
            return rule
    return None


def _matching_rule(
    display_name: str,
    domain: str,
    *,
    context_text: str = "",
) -> CanonicalIdentityRule | None:
    for rule in CANONICAL_IDENTITY_RULES:
        if _domain_matches(domain, rule.domains):
            return rule
        if _contains_phrase(display_name, rule.name_phrases):
            return rule
        if context_text and _contains_phrase(context_text, rule.name_phrases):
            return rule
    return None


def _rule_for_label_or_name(value: str) -> CanonicalIdentityRule | None:
    key = _identity_key(value)
    for rule in CANONICAL_IDENTITY_RULES:
        if key == _identity_key(rule.label) or _contains_phrase(value, rule.name_phrases):
            return rule
    return None


def _domain_matches(domain: str, suffixes: Sequence[str]) -> bool:
    normalized = domain.casefold().strip(".")
    return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes)


def _is_relay_domain(domain: str) -> bool:
    return _domain_matches(domain, tuple(RELAY_DOMAINS))


def _domain_organization_label(domain: str) -> str | None:
    if not domain or domain in FREE_MAIL_DOMAINS or _is_relay_domain(domain):
        return None
    label = _registrable_label(domain)
    if not label:
        return None
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", label) if part)


def _registrable_label(domain: str) -> str | None:
    labels = [label for label in domain.casefold().strip(".").split(".") if label]
    for label in reversed(labels):
        if label not in DOMAIN_INFRASTRUCTURE_LABELS:
            return label
    return None


def _clean_display_name(value: str) -> str:
    cleaned = value.strip().strip('"').strip("'").strip()
    cleaned = cleaned.replace("\\\"", '"')
    return " ".join(cleaned.split())


def _presentation_label(value: str) -> str:
    """Use proper-name casing without lowercasing real acronyms or brands."""
    cleaned = _clean_display_name(value)
    if not cleaned or "@" in cleaned:
        return cleaned

    for rule in CANONICAL_IDENTITY_RULES:
        if _identity_key(cleaned) == _identity_key(rule.label):
            return rule.label

    letters = [character for character in cleaned if character.isalpha()]
    if not letters or not (all(character.isupper() for character in letters) or all(character.islower() for character in letters)):
        return cleaned

    word_index = 0

    def replace_word(match: re.Match[str]) -> str:
        nonlocal word_index
        raw_word = match.group(0)
        upper_word = raw_word.upper()
        lower_word = raw_word.lower()
        if upper_word in PRESERVED_IDENTITY_ACRONYMS:
            rendered = upper_word
        elif word_index > 0 and lower_word in LOWERCASE_IDENTITY_CONNECTORS:
            rendered = lower_word
        else:
            rendered = lower_word.capitalize()
        word_index += 1
        return rendered

    return re.sub(r"[A-Za-z]+", replace_word, cleaned)


def _presentation_quality(value: str) -> tuple[int, int, int]:
    """Prefer explicit word boundaries and acronym casing for one identity."""
    words = re.findall(r"[A-Za-z0-9]+", _clean_display_name(value))
    return (
        len(words),
        sum(1 for word in words if len(word) > 1 and word.isupper()),
        len(value),
    )


def _strip_relay_attribution(value: str) -> str:
    cleaned = re.sub(r"(?i)\s*\(via\s+[^)]+\)\s*$", "", value).strip()
    cleaned = re.sub(r"(?i)\s+via\s+.+$", "", cleaned).strip()
    return cleaned


def _strip_generic_role(value: str) -> str:
    cleaned = _strip_relay_attribution(value)
    cleaned = GENERIC_ROLE_PREFIX_PATTERN.sub("", cleaned)
    cleaned = GENERIC_ROLE_SUFFIX_PATTERN.sub("", cleaned)
    return cleaned.strip(" ._-|")


def _is_generic_display_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return normalized in GENERIC_DISPLAY_NAMES or not normalized


def _contains_phrase(value: str, phrases: Sequence[str]) -> bool:
    normalized = f" {_words(value)} "
    return any(f" {_words(phrase)} " in normalized for phrase in phrases if phrase)


def _words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _identity_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _append_unique(values: list[str], value: str) -> None:
    key = _identity_key(value)
    if key and all(_identity_key(existing) != key for existing in values):
        values.append(value)


def _sentence_case(value: str) -> str:
    for index, character in enumerate(value):
        if character.isalpha():
            if any(prefix.isalnum() for prefix in value[:index]):
                return value
            return value[:index] + character.upper() + value[index + 1 :]
    return value


def _header_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        import json

        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _timestamp(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


__all__ = [
    "MAX_COUNTERPART_ENTITIES",
    "MailboxOwnerIdentity",
    "aggregate_counterpart_entities",
    "counterpart_aware_headline",
    "identity_candidates_for_message",
    "mailbox_owner_identity",
    "redacted_identity_candidates",
    "resolve_counterpart_entities",
]
