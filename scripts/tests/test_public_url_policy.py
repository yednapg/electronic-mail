from __future__ import annotations

import unittest

from scripts.public_url_policy import PublicURLPolicyError, validate_public_status_url


class PublicStatusURLPolicyTests(unittest.TestCase):
    def test_accepts_canonical_public_status_urls(self) -> None:
        for value in (
            "https://status.electronicmail.app",
            "https://status.electronicmail.app/",
            "https://status.electronicmail.app/incidents/current",
        ):
            with self.subTest(value=value):
                self.assertEqual(validate_public_status_url(value), value)

    def test_rejects_noncanonical_or_ambiguous_paths(self) -> None:
        for value in (
            "https://status.electronicmail.app//current",
            "https://status.electronicmail.app/a\\b",
            "https://status.electronicmail.app/a%5cb",
            "https://status.electronicmail.app/%2e%2e/current",
            "https://status.electronicmail.app/a/%2E./current",
            "https://status.electronicmail.app/a%2f%2fcurrent",
            "https://status.electronicmail.app/%0acurrent",
            "https://status.electronicmail.app/%zz",
        ):
            with self.subTest(value=value):
                with self.assertRaises(PublicURLPolicyError):
                    validate_public_status_url(value)

    def test_rejects_nonpublic_or_noncanonical_authorities(self) -> None:
        for value in (
            "http://status.electronicmail.app",
            "https://STATUS.electronicmail.app",
            "https://status.electronicmail.app:443",
            "https://status.example.com",
            "https://status_service.electronicmail.app",
            "https://127.0.0.1/status",
        ):
            with self.subTest(value=value):
                with self.assertRaises(PublicURLPolicyError):
                    validate_public_status_url(value)


if __name__ == "__main__":
    unittest.main()
