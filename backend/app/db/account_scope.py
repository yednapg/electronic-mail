from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_ACTIVE_GMAIL_ACCOUNT_ID: ContextVar[str | None] = ContextVar(
    "active_gmail_account_id", default=None
)


def active_gmail_account_id() -> str | None:
    return _ACTIVE_GMAIL_ACCOUNT_ID.get()


@contextmanager
def gmail_account_scope(gmail_account_id: str) -> Iterator[None]:
    normalized = gmail_account_id.strip()
    if not normalized:
        raise ValueError("Gmail account ID is required")
    token = _ACTIVE_GMAIL_ACCOUNT_ID.set(normalized)
    try:
        yield
    finally:
        _ACTIVE_GMAIL_ACCOUNT_ID.reset(token)
