from __future__ import annotations

"""Static tuning and heuristic data for the mail group pipeline."""

import re

FIRST_BATCH_SIZE = 300
BACKFILL_BATCH_SIZE = 100
FIRST_RUN_HOT_WINDOW_BATCH_SIZE = 500
MAILBOX_REBUILD_LIMIT = 5000
MAIL_GROUP_ENRICH_BATCH_SIZE = 25
MAIL_GROUP_ENRICH_MAX_OUTPUT_TOKENS = 1600
FIRST_RUN_AI_MAX_GROUPS = 80
FIRST_RUN_AI_CANDIDATE_LIMIT = 300
FIRST_RUN_AI_MAX_OUTPUT_TOKENS = 12000
AI_LIFECYCLE_PROJECTION_DAYS = 30
AI_LIFECYCLE_PROJECTION_MESSAGE_LIMIT = 300
AI_LIFECYCLE_PROJECTION_MAX_GROUPS = 48
AI_LIFECYCLE_PROJECTION_MAX_OUTPUT_TOKENS = 12000
AI_LIFECYCLE_MIN_INBOX_CONFIDENCE = 0.94
DASHBOARD_DAYS = 14
RECENT_VISIBLE_DAYS = 30
BODY_WARMUP_GROUP_LIMIT = 24
SMART_FIRST_READY_TARGET_MESSAGES = 300
SMART_HOT_WINDOW_DAYS = 30
SMART_HOT_WINDOW_MESSAGE_CAP = 0
SMART_HOT_WINDOW_THREAD_CAP = 0
SMART_INBOX_DISPLAY_MESSAGE_LIMIT = 5000
SMART_INBOX_DISPLAY_THREAD_LIMIT = 1000
APP_SESSION_MAILBOX_LIMIT = SMART_INBOX_DISPLAY_THREAD_LIMIT
SMART_OFFLINE_WARMUP_THREAD_LIMIT = SMART_FIRST_READY_TARGET_MESSAGES
APP_SESSION_PROJECTION_VERSION = "20260611_smart_inbox_foundation_v13"

GENERIC_SENDER_SLUGS = {
    "alert",
    "alerts",
    "defaultuser",
    "info",
    "mail",
    "no-reply",
    "noreply",
    "notification",
    "notifications",
    "support",
    "team",
}
DOMAIN_OWNER_SUFFIX_WORDS = (
    "company",
    "intelligence",
    "direct",
    "retail",
    "india",
    "apply",
    "state",
    "path",
    "bank",
    "aws",
    "cc",
)
DOMAIN_PLATFORM_LABELS = {"myliaison"}
DOMAIN_SERVICE_LABELS = GENERIC_SENDER_SLUGS | {
    "billing",
    "costalerts",
    "custcomm",
    "email",
    "newsletter",
    "newsletters",
    "updates",
}
DOMAIN_TITLE_SUFFIX_WORDS = set(DOMAIN_OWNER_SUFFIX_WORDS) | DOMAIN_SERVICE_LABELS | {"app", "cloud", "hq"}
MAILBOX_REFERENCE_FAMILY_BY_SIGNAL = {
    "application_id": "application",
    "booking_id": "logistics",
    "dispute_id": "support_case",
    "invoice_id": "billing",
    "order_id": "logistics",
    "repair_id": "support_case",
    "ticket_id": "support_case",
    "trade_id": "financial_transfer",
    "tracking_id": "logistics",
}
MAILBOX_TASK_REFERENCE_SIGNALS = (
    "order_id",
    "trade_id",
    "ticket_id",
    "dispute_id",
    "repair_id",
    "tracking_id",
    "invoice_id",
    "booking_id",
    "application_id",
)
MAILBOX_EXACT_ABSORPTION_SIGNALS = ("trade_id", "ticket_id", "repair_id", "booking_id", "tracking_id", "order_id")
SMART_SUPPORT_REFERENCE_RE = re.compile(
    r"(?i)\b(?:service\s+request|support\s+case|case|ticket|grievance|complaint|reference(?:\s+number)?|request(?:\s+number)?)"
    r"\s*(?:number|no\.?|id|#|:|-)?\s*(?P<value>[A-Z]{0,6}[-/]?\d[A-Z0-9-]{5,}|\d{7,})\b"
)
SMART_TRADE_REFERENCE_RE = re.compile(
    r"(?i)\b(?:(?:fx[-\s]?retail\s+)?trade(?:\s+confirmation)?|(?:fx[-\s]?retail\s+)?deal|ccil\s+reference)"
    r"\s*(?:number|no\.?|id|#|for|:|-)?\s*(?P<value>[A-Z]{0,8}[-/]?\d[A-Z0-9-]{5,}|\d{7,})\b"
)
SMART_BOOKING_REFERENCE_RE = re.compile(r"(?i)\bbooking\b.{0,80}\b([A-Z]{2,8}[-/]?\d[A-Z0-9-]{4,})\b")
SMART_REPAIR_REFERENCE_RE = re.compile(
    r"(?i)\brepair\s*(?:number|no\.?|id|#)?\s*(?:[:#.-]|\s)\s*([A-Z]{1,8}[-/]?\d[A-Z0-9-]{5,}|\d{7,})\b"
)
DOMAIN_LABEL_WORDS = {
    "amazonaws": ["aws"],
    "calstateapply": ["cal", "state", "apply"],
    "cccmypath": ["ccc", "mypath"],
    "github": ["github"],
    "hackclub": ["hack", "club"],
    "interactivebrokers": ["interactive", "brokers"],
}
DOMAIN_WORD_TITLES = {
    "aws": "AWS",
    "api": "API",
    "cal": "Cal",
    "ccc": "CCC",
    "ccil": "CCIL",
    "club": "Club",
    "fx": "FX",
    "github": "GitHub",
    "hack": "Hack",
    "northstar": "Northstar",
    "hsbc": "HSBC",
    "id": "ID",
    "mypath": "MyPath",
    "neo": "Neo",
    "nse": "NSE",
    "psu": "PSU",
    "rbi": "RBI",
    "sbi": "SBI",
    "simplypay": "SimplyPay",
}
SERVICE_CHANNEL_WORDS = {
    "alert",
    "alerts",
    "care",
    "card",
    "cards",
    "cc",
    "credit",
    "credits",
    "fyi",
    "grievance",
    "redressal",
    "response",
    "service",
    "services",
    "support",
}
TITLE_PHRASE_STOP_STARTS = {
    "action",
    "alert",
    "confirmation",
    "confirming",
    "dear",
    "decision",
    "finish",
    "getting",
    "important",
    "limited",
    "madam",
    "miss",
    "mr",
    "mrs",
    "ms",
    "new",
    "notice",
    "reminder",
    "update",
    "updates",
    "view",
    "welcome",
    "your",
}
MAILBOX_DISPLAY_CLUSTER_WORKFLOW_TYPES = {
    "financial_transfer",
    "support_case",
    "billing",
    "logistics",
    "account_security",
    "application",
    "newsletter",
    "marketing",
}
MAILBOX_DISPLAY_CLUSTER_PREFIX = "mailbox-cluster:"
LOCALIZATION_PLACEHOLDER_RE = re.compile(r"(?i)\btranslation missing:\s*[a-z0-9_.-]+(?:\s+base)?\s*")
TOPIC_STOP_WORDS = {
    "account",
    "application",
    "created",
    "complete",
    "congratulations",
    "from",
    "received",
    "reference",
    "request",
    "save",
    "successfully",
    "the",
    "this",
    "to",
    "update",
    "welcome",
    "your",
}
SMART_WORKFLOW_TOKEN_STOP_WORDS = TOPIC_STOP_WORDS | {
    "bank",
    "customer",
    "dear",
    "email",
    "example",
    "info",
    "kindly",
    "limited",
    "mail",
    "message",
    "notification",
    "please",
    "provider",
    "sir",
    "status",
    "support",
    "team",
}
SMART_WORKFLOW_ACCOUNT_TOKENS = {
    "access",
    "account",
    "approval",
    "approved",
    "created",
    "document",
    "esign",
    "kyc",
    "login",
    "processed",
    "registration",
    "registered",
    "signed",
    "signature",
    "terms",
    "virtual",
}
SMART_WORKFLOW_APPLICATION_TOKENS = {
    "accepted",
    "acceptance",
    "admission",
    "admissions",
    "admitted",
    "aid",
    "application",
    "cost",
    "costs",
    "estimate",
    "estimated",
    "international",
    "lionpath",
    "mypennstate",
    "reconsideration",
    "student",
    "students",
}
SMART_WORKFLOW_LIMIT_TOKENS = {
    "amount",
    "funded",
    "funding",
    "limit",
    "limits",
    "markup",
    "rejected",
    "request",
    "trading",
}
SMART_WORKFLOW_REMITTANCE_TOKENS = {
    "mt103",
    "mt110",
    "outward",
    "remit",
    "remittance",
    "swift",
    "wire",
}
SMART_WORKFLOW_PLATFORM_STOP_WORDS = GENERIC_SENDER_SLUGS | {
    "contact",
    "default",
    "email",
    "example",
    "family",
    "message",
    "provider",
}
MAILBOX_TOPIC_TITLE_GENERIC_WORDS = SMART_WORKFLOW_TOKEN_STOP_WORDS | TITLE_PHRASE_STOP_STARTS | {
    "activity",
    "activities",
    "digest",
    "generic",
    "mailbox",
    "notice",
    "summary",
}
MAILBOX_BUNDLE_TOPIC_STOP_WORDS = MAILBOX_TOPIC_TITLE_GENERIC_WORDS | {
    "and",
    "announced",
    "announc",
    "announcement",
    "approval",
    "approved",
    "approv",
    "client",
    "complet",
    "everyday",
    "emerging",
    "emerg",
    "finish",
    "for",
    "free",
    "get",
    "granted",
    "grant",
    "invitation",
    "invite",
    "invited",
    "life",
    "let",
    "lets",
    "market",
    "markets",
    "more",
    "move",
    "moves",
    "job",
    "jobs",
    "notification",
    "opportunity",
    "opportunities",
    "posting",
    "power",
    "profile",
    "promotion",
    "promotional",
    "promotions",
    "role",
    "set",
    "setup",
    "setting",
    "settings",
    "sett",
    "these",
    "update",
    "updates",
    "using",
    "with",
    "work",
    "you",
}
MAILBOX_BUNDLE_FALLBACK_KEYWORD_STOP_WORDS = {
    "and",
    "email",
    "from",
    "inbox",
    "mail",
    "now",
    "the",
    "with",
    "your",
}
MAILBOX_BUNDLE_TITLE_MAX_CHARS = 96
MAILBOX_PROVIDER_STREAM_MAX_SPAN_DAYS = 45
SMART_WORKFLOW_MAX_SPAN_DAYS = 45
