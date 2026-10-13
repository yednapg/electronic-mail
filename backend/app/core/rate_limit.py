from __future__ import annotations

"""Per-process safety limit, with shared buckets for abuse-sensitive routes.

The production edge must still enforce volumetric limits. These buckets stop
one replica from multiplying the allowance of the routes that create sessions,
OAuth rows, or mailbox writes.
"""

from collections import defaultdict, deque
from http.cookies import CookieError, SimpleCookie
import asyncio
import hashlib
import json
from threading import Lock
import time
from typing import Any

from app.db.repository import consume_rate_limit_bucket


class RateLimitMiddleware:
    """Apply conservative fixed-window limits to abuse-sensitive API routes."""

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        trust_proxy_headers: bool = False,
        trusted_proxy_hops: int = 1,
        behind_cloudflare: bool = False,
        database_url: str | None = None,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.trust_proxy_headers = trust_proxy_headers
        self.trusted_proxy_hops = trusted_proxy_hops
        self.behind_cloudflare = behind_cloudflare
        self.database_url = database_url
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._last_cleanup = 0.0

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if not self.enabled or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        rule = _rule_for(str(scope.get("method", "GET")), str(scope.get("path", "")))
        if rule is None:
            await self.app(scope, receive, send)
            return

        rule_name, maximum, window_seconds = rule
        # Public OAuth/handoff endpoints do not authenticate Authorization or
        # Cookie headers. Treating arbitrary attacker-supplied credentials as an
        # identity lets every attempt rotate the header and bypass the limit.
        identity = _request_identity(
            scope,
            use_credentials=rule_name
            not in {
                "oauth",
                "mobile-exchange",
                "mobile-handoff",
                "gmail-pubsub-verification",
            },
            trust_proxy_headers=self.trust_proxy_headers,
            trusted_proxy_hops=self.trusted_proxy_hops,
            behind_cloudflare=self.behind_cloudflare,
        )
        allowed, retry_after = await self._allow(rule_name, identity, maximum, window_seconds)
        if allowed:
            await self.app(scope, receive, send)
            return

        payload = json.dumps({"detail": "Too many requests. Try again shortly."}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"retry-after", str(retry_after).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    async def _allow(self, rule: str, identity: str, maximum: int, window_seconds: int) -> tuple[bool, int]:
        if self.database_url and rule in _PERSISTENT_RULES:
            try:
                return await asyncio.to_thread(
                    consume_rate_limit_bucket,
                    self.database_url,
                    rule=rule,
                    identity=identity,
                    maximum=maximum,
                    window_seconds=window_seconds,
                )
            except Exception:
                # A database outage must not turn the limiter into an open proxy,
                # but it also must not take the API down. Fall back to this process.
                return self._consume(rule, identity, maximum, window_seconds)
        return self._consume(rule, identity, maximum, window_seconds)

    def _consume(self, rule: str, identity: str, maximum: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        key = (rule, identity)
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= maximum:
                retry_after = max(1, int(window_seconds - (now - events[0])) + 1)
                return False, retry_after
            events.append(now)
            if now - self._last_cleanup > 300:
                self._cleanup(cutoff)
                self._last_cleanup = now
        return True, 0

    def _cleanup(self, cutoff: float) -> None:
        stale = [key for key, events in self._events.items() if not events or events[-1] <= cutoff]
        for key in stale:
            self._events.pop(key, None)


_PERSISTENT_RULES = frozenset(
    {
        "oauth",
        "mobile-exchange",
        "mobile-handoff",
        "gmail-pubsub-verification",
        "mailbox-sync",
        "mailbox-write",
    }
)


def _rule_for(method: str, path: str) -> tuple[str, int, int] | None:
    if path in {"/auth/google", "/auth/google/callback"}:
        return ("oauth", 20, 60)
    if path == "/v1/auth/mobile/exchange":
        return ("mobile-exchange", 20, 60)
    if path.startswith("/v1/auth/mobile/handoff/") or path == "/auth/mobile/complete":
        return ("mobile-handoff", 180, 60)
    if method == "POST" and path == "/v1/mailbox/pubsub":
        # This cheap source bucket runs before certificate verification. It is
        # deliberately separate from the verified-delivery quota in the route:
        # rotating attacker-controlled bearer tokens cannot bypass it or spend
        # capacity reserved for authenticated Google deliveries.
        return ("gmail-pubsub-verification", 60, 60)
    if method == "POST" and path in {"/v1/mailbox/sync", "/v1/mailbox/sync-now"}:
        return ("mailbox-sync", 12, 60)
    if method in {"POST", "PUT", "PATCH", "DELETE"} and (
        path == "/v1/mailbox/compose"
        or path == "/v1/mailbox/thread-actions"
        or path == "/v1/mailbox/entity-actions"
        or path == "/v1/matter-decisions"
        or path.startswith("/v1/ai-organization/")
        or "/reply" in path
        or "/draft" in path
    ):
        return ("mailbox-write", 90, 60)
    if method == "GET" and "/attachments/" in path:
        return ("attachment-download", 120, 60)
    if method == "GET" and path == "/v1/mailbox/search":
        return ("mailbox-search", 120, 60)
    if method == "GET" and path == "/v1/ai-inbox/search":
        return ("mailbox-search", 120, 60)
    if method == "GET" and path == "/v1/events/mailbox":
        return ("mailbox-sse", 12, 60)
    return None


def _request_identity(
    scope: dict[str, Any],
    *,
    use_credentials: bool = True,
    trust_proxy_headers: bool = False,
    trusted_proxy_hops: int = 1,
    behind_cloudflare: bool = False,
) -> str:
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    authorization = headers.get(b"authorization", b"")
    cookie = headers.get(b"cookie", b"")
    session_cookie = _session_cookie_value(cookie)
    credential = session_cookie or _bearer_credential(authorization)
    if use_credentials and credential:
        return "credential:" + hashlib.sha256(credential).hexdigest()
    address = _client_address(
        headers,
        scope,
        trust_proxy_headers=trust_proxy_headers,
        trusted_proxy_hops=trusted_proxy_hops,
        behind_cloudflare=behind_cloudflare,
    )
    return "network:" + hashlib.sha256(address.encode("utf-8")).hexdigest()


def _client_address(
    headers: dict[bytes, bytes],
    scope: dict[str, Any],
    *,
    trust_proxy_headers: bool,
    trusted_proxy_hops: int,
    behind_cloudflare: bool,
) -> str:
    """Return the client address a trusted proxy actually observed.

    The leftmost X-Forwarded-For entry is attacker-controlled whenever a proxy
    appends. With one trusted hop, the rightmost entry is the address that hop
    added. Cloudflare's connecting-IP header is used only when this deployment
    is configured to sit behind Cloudflare, because that header is otherwise
    just as spoofable as the forwarded-for list.
    """
    if trust_proxy_headers and behind_cloudflare:
        cloudflare = headers.get(b"cf-connecting-ip", b"").decode("ascii", errors="ignore").split(",", 1)[0].strip()
        if cloudflare:
            return cloudflare
    if trust_proxy_headers and trusted_proxy_hops > 0:
        forwarded = headers.get(b"x-forwarded-for", b"").decode("ascii", errors="ignore")
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            index = max(0, len(parts) - trusted_proxy_hops)
            return parts[index]
    client = scope.get("client") or ("unknown", 0)
    return str(client[0])


def _session_cookie_value(raw_cookie: bytes) -> bytes:
    if not raw_cookie:
        return b""
    parsed = SimpleCookie()
    try:
        parsed.load(raw_cookie.decode("latin-1"))
    except CookieError:
        return b""
    morsel = parsed.get("dp_session")
    return morsel.value.encode("utf-8") if morsel is not None and morsel.value else b""


def _bearer_credential(raw_authorization: bytes) -> bytes:
    scheme, _, token = raw_authorization.partition(b" ")
    if scheme.lower() != b"bearer" or not token:
        return b""
    return token.strip()
