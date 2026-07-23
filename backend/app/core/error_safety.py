from __future__ import annotations

"""Privacy-safe error messages for persistence and public API responses."""


class GoogleCredentialsUnavailable(RuntimeError):
    """The user must reconnect Google before Gmail work can continue."""


def external_error_status(exc: BaseException) -> int | None:
    value = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def safe_google_error(exc: BaseException, *, operation: str = "request") -> str:
    """Describe a Google failure without serializing provider bodies or URLs."""
    if isinstance(exc, GoogleCredentialsUnavailable):
        return "Google authorization expired or was revoked. Please sign in again."
    status = external_error_status(exc)
    if status in {401, 403}:
        return "Google authorization expired or lacks permission. Please sign in again."
    if status == 429:
        return f"Google {operation} is temporarily rate limited."
    if status is not None and status >= 500:
        return f"Google {operation} is temporarily unavailable."
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return f"Google {operation} is temporarily unavailable."
    return f"Google {operation} failed."


def safe_job_error(exc: BaseException) -> str:
    """Persist a useful category, never an exception message or raw payload."""
    status = external_error_status(exc)
    suffix = f" (HTTP {status})" if status is not None else ""
    return f"{type(exc).__name__}: Background task failed{suffix}."
