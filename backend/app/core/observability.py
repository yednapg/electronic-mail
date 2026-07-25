from __future__ import annotations

"""Minimal structured logging and request correlation for hosted runtimes."""

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import re
import sys
import time
from typing import Any
from uuid import uuid4

from app.core.config import Settings


request_id_context: ContextVar[str] = ContextVar("request_id", default="")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_NO_STORE_AUTH_PATHS = {
    "/auth/google",
    "/auth/google/callback",
    "/auth/mobile/complete",
    "/v1/auth/mobile/exchange",
}
_PRIVATE_CONTENT_CACHE_PATHS = (
    re.compile(r"^/v1/mailbox/messages/[^/]+/attachments/[^/]+$"),
    re.compile(r"^/v1/mailbox/remote-images/[^/]+$"),
)


def _must_not_store_response(path: str) -> bool:
    # Mailbox, session, and legacy native API responses can contain email
    # metadata, bodies, attachments, or bearer-session state. They must never
    # enter browser, proxy, or URLSession caches.
    return (
        path.startswith("/v1/")
        or path.startswith("/gmail/")
        or path in _NO_STORE_AUTH_PATHS
        or path.startswith("/v1/auth/mobile/handoff/")
    )


def _allows_private_content_cache(path: str) -> bool:
    """Allow authenticated immutable bytes to use their route cache policy."""
    return any(pattern.fullmatch(path) is not None for pattern in _PRIVATE_CONTENT_CACHE_PATHS)


class JSONLogFormatter(logging.Formatter):
    """Emit one machine-readable object per line without request payloads."""

    def __init__(self, *, release_sha: str) -> None:
        super().__init__()
        self.release_sha = release_sha

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "release": self.release_sha,
        }
        request_id = request_id_context.get()
        if request_id:
            payload["request_id"] = request_id
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            # Exception messages can embed provider response bodies, OAuth URLs,
            # or database DSNs. Keep the operational category without serializing
            # the message or traceback into the hosted log stream.
            exception_type = record.exc_info[0]
            payload["exception_type"] = exception_type.__name__ if exception_type is not None else "Exception"
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_observability(settings: Settings) -> None:
    """Configure stdout JSON logs once for API and platform collectors."""
    root = logging.getLogger()
    level = getattr(logging, settings.log_level, logging.INFO)
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONLogFormatter(release_sha=settings.release_sha))
    root.addHandler(handler)
    # These libraries log complete URLs at INFO, which can contain OAuth codes,
    # state, or native handoff identifiers. RequestObservabilityMiddleware emits
    # the intentionally path-only request record used in production instead.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class RequestObservabilityMiddleware:
    """Pure ASGI middleware so streaming mailbox responses remain streaming."""

    def __init__(self, app: Any, *, production: bool = False) -> None:
        self.app = app
        self.production = production
        self.logger = logging.getLogger("electronic_mail.http")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid4())
        token = request_id_context.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_headers(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                response_headers = list(message.get("headers", []))
                path = str(scope.get("path") or "")
                if _must_not_store_response(path) and not (
                    status_code in {200, 304} and _allows_private_content_cache(path)
                ):
                    # Apply this at the ASGI boundary so validation errors and
                    # exception responses cannot accidentally become cacheable.
                    response_headers = [
                        (key, value)
                        for key, value in response_headers
                        if key.lower() not in {b"cache-control", b"pragma"}
                    ]
                    response_headers.extend(
                        [
                            (b"cache-control", b"no-store, private"),
                            (b"pragma", b"no-cache"),
                        ]
                    )
                response_headers.extend(
                    [
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-frame-options", b"DENY"),
                    ]
                )
                if self.production:
                    response_headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            self.logger.exception(
                "request.failed",
                extra={
                    "event_fields": {
                        "event": "request.failed",
                        "method": scope.get("method", ""),
                        "path": _request_log_path(scope),
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            self.logger.info(
                "request.complete",
                extra={
                    "event_fields": {
                        "event": "request.complete",
                        "method": scope.get("method", ""),
                        "path": _request_log_path(scope),
                        "status": status_code,
                        "duration_ms": duration_ms,
                    }
                },
            )
            request_id_context.reset(token)


def _request_log_path(scope: dict[str, Any]) -> str:
    """Log the route template, not identifiers embedded in the request path."""
    route_path = getattr(scope.get("route"), "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return "<unmatched>"
