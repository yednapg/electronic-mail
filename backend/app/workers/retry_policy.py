from __future__ import annotations

"""Retry-delay policy for transient Gmail worker failures."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import errno
import math
import ssl
from typing import Any, Iterator

from google.auth.exceptions import TransportError
from httplib2 import ServerNotFoundError


GMAIL_RETRY_DELAY_CAP_SECONDS = 60 * 60

_NETWORK_ERRNOS = {
    value
    for name in (
        "ECONNABORTED",
        "ECONNREFUSED",
        "ECONNRESET",
        "EHOSTDOWN",
        "EHOSTUNREACH",
        "ENETDOWN",
        "ENETRESET",
        "ENETUNREACH",
        "EPIPE",
        "ETIMEDOUT",
    )
    if (value := getattr(errno, name, None)) is not None
}


def gmail_retry_delay_seconds(
    job_kind: str,
    exc: BaseException,
    *,
    exponential_delay_seconds: int,
    now: datetime | None = None,
) -> int | None:
    """Return a bounded delay for transient Gmail failures only.

    ``None`` means that the worker should use its unchanged default failure
    behavior. A timeout or network failure has no provider delay to extract,
    so it returns the normal exponential delay. HTTP 429 and 5xx responses may
    raise that delay when Gmail supplies ``Retry-After``.
    """
    if not job_kind.startswith("gmail_"):
        return None

    base_delay = max(0, int(exponential_delay_seconds))
    for candidate in _exception_chain(exc):
        status = _http_status(candidate)
        if status == 429 or (status is not None and 500 <= status <= 599):
            retry_after = _retry_after_seconds(candidate, now=now)
            requested_delay = base_delay if retry_after is None else max(base_delay, retry_after)
            return min(GMAIL_RETRY_DELAY_CAP_SECONDS, requested_delay)
        if _is_network_failure(candidate):
            retry_after = _retry_after_seconds(candidate, now=now)
            requested_delay = base_delay if retry_after is None else max(base_delay, retry_after)
            return min(GMAIL_RETRY_DELAY_CAP_SECONDS, requested_delay)
    return None


def gmail_is_authorization_failure(exc: BaseException) -> bool:
    """Recognize revoked/insufficient Google grants through wrapper errors."""
    return any(_http_status(candidate) in {401, 403} for candidate in _exception_chain(exc))


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _http_status(exc: BaseException) -> int | None:
    response = _response(exc)
    for value in (
        getattr(response, "status", None),
        getattr(response, "status_code", None),
        getattr(exc, "status", None),
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
    ):
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _retry_after_seconds(exc: BaseException, *, now: datetime | None) -> int | None:
    response = _response(exc)
    value = _header(response, "retry-after")
    if value is None:
        value = _header(exc, "retry-after")
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0, int(text, 10))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = (
        retry_at.astimezone(timezone.utc) - current.astimezone(timezone.utc)
    ).total_seconds()
    return max(0, math.ceil(seconds))


def _response(exc: BaseException) -> Any:
    response = getattr(exc, "resp", None)
    if response is None:
        response = getattr(exc, "response", None)
    return response


def _header(response: Any, name: str) -> object | None:
    if response is None:
        return None
    sources = (getattr(response, "headers", None), response)
    for source in sources:
        if source is None:
            continue
        getter = getattr(source, "get", None)
        if callable(getter):
            for key in (name, name.title()):
                value = getter(key)
                if value is not None:
                    return value
        items = getattr(source, "items", None)
        if callable(items):
            for key, value in items():
                if str(key).lower() == name:
                    return value
    return None


def _is_network_failure(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            ServerNotFoundError,
            TransportError,
            ssl.SSLError,
        ),
    ):
        return True
    return isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS
