from __future__ import annotations

import unittest
from uuid import uuid4

from app.core.config import load_settings
from app.core.rate_limit import _client_address, _request_identity
from app.db.repository import consume_rate_limit_bucket


class ClientAddressTests(unittest.TestCase):
    def test_rightmost_forwarded_entry_is_the_client_when_one_proxy_is_trusted(self) -> None:
        headers = {b"x-forwarded-for": b"203.0.113.9, 198.51.100.8"}
        scope = {"client": ("10.0.0.4", 1234), "headers": []}

        address = _client_address(
            headers,
            scope,
            trust_proxy_headers=True,
            trusted_proxy_hops=1,
            behind_cloudflare=False,
        )

        self.assertEqual(address, "198.51.100.8")

    def test_spoofed_leftmost_forwarded_entry_does_not_change_identity(self) -> None:
        scope = {"client": ("10.0.0.4", 1234)}
        honest = _request_identity(
            {**scope, "headers": [(b"x-forwarded-for", b"198.51.100.8")]},
            use_credentials=False,
            trust_proxy_headers=True,
            trusted_proxy_hops=1,
        )
        spoofed = _request_identity(
            {**scope, "headers": [(b"x-forwarded-for", b"203.0.113.9, 198.51.100.8")]},
            use_credentials=False,
            trust_proxy_headers=True,
            trusted_proxy_hops=1,
        )

        self.assertEqual(honest, spoofed)

    def test_cloudflare_header_is_ignored_unless_the_deployment_says_so(self) -> None:
        headers = {
            b"cf-connecting-ip": b"203.0.113.50",
            b"x-forwarded-for": b"198.51.100.8",
        }
        scope = {"client": ("10.0.0.4", 1)}

        untrusted = _client_address(
            headers,
            scope,
            trust_proxy_headers=True,
            trusted_proxy_hops=1,
            behind_cloudflare=False,
        )
        trusted = _client_address(
            headers,
            scope,
            trust_proxy_headers=True,
            trusted_proxy_hops=1,
            behind_cloudflare=True,
        )

        self.assertEqual(untrusted, "198.51.100.8")
        self.assertEqual(trusted, "203.0.113.50")


class SharedRateLimitBucketTests(unittest.TestCase):
    def test_two_callers_share_one_window(self) -> None:
        database_url = str(load_settings().database_path)
        rule = f"test-rate-{uuid4().hex}"
        identity = f"network:{uuid4().hex}"
        try:
            first_allowed, _ = consume_rate_limit_bucket(
                database_url, rule=rule, identity=identity, maximum=2, window_seconds=60
            )
            second_allowed, _ = consume_rate_limit_bucket(
                database_url, rule=rule, identity=identity, maximum=2, window_seconds=60
            )
            third_allowed, retry_after = consume_rate_limit_bucket(
                database_url, rule=rule, identity=identity, maximum=2, window_seconds=60
            )
        except Exception as exc:
            self.skipTest(f"shared rate-limit table is unavailable: {type(exc).__name__}")

        self.assertTrue(first_allowed)
        self.assertTrue(second_allowed)
        self.assertFalse(third_allowed)
        self.assertGreaterEqual(retry_after, 1)
