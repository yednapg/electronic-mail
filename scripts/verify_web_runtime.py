#!/usr/bin/env python3
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import re
import sys
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_PAGE_BYTES = 4 * 1024 * 1024


class WebRuntimeError(ValueError):
    pass


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # type: ignore[no-untyped-def]
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, _normalized_text(" ".join(self._text))))
            self._href = None
            self._text = []


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WebRuntimeError(message)


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _page_text(markup: str) -> str:
    parser = _TextParser()
    parser.feed(markup)
    return _normalized_text(" ".join(parser.parts))


def _anchors(markup: str) -> list[tuple[str, str]]:
    parser = _AnchorParser()
    parser.feed(markup)
    return parser.anchors


def _fetch(base_url: str, path: str) -> tuple[int, Mapping[str, str], str]:
    request = Request(f"{base_url}{path}", headers={"User-Agent": "ElectronicMail-CI-Runtime-Verifier/1.0"})
    try:
        response = _NO_REDIRECT_OPENER.open(request, timeout=30)
    except HTTPError as error:
        response = error
    except URLError as error:
        raise WebRuntimeError(f"request {path} failed: {error.reason}") from error
    try:
        body = response.read(MAX_PAGE_BYTES + 1)
        _require(len(body) <= MAX_PAGE_BYTES, f"response {path} exceeds {MAX_PAGE_BYTES} bytes")
        try:
            markup = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WebRuntimeError(f"response {path} is not UTF-8: {error}") from error
        return int(response.status), response.headers, markup
    finally:
        response.close()


def _require_status(status: int, expected: int, path: str) -> None:
    _require(status == expected, f"{path} returned HTTP {status}; expected {expected}")


def _validate_base_url(value: str) -> str:
    _require(value == value.strip() and not any(ord(character) < 33 for character in value), "web URL contains whitespace or control characters")
    parsed = urlsplit(value)
    _require(parsed.scheme in {"http", "https"} and bool(parsed.hostname), "web URL must be an HTTP(S) origin")
    _require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment, "web URL cannot contain credentials, query, or fragment")
    _require(parsed.path in ("", "/"), "web URL must be an origin without a path")
    return value.rstrip("/")


def validate_runtime(
    *,
    web_url: str,
    download_url: str,
    support_email: str,
    status_page_url: str,
    effective_date: str,
    hosting_providers: str,
    data_regions: str,
    backup_retention_days: int,
) -> None:
    base_url = _validate_base_url(web_url)
    pages: dict[str, tuple[Mapping[str, str], str]] = {}
    for path in ("/", "/privacy", "/terms", "/support", "/post-login"):
        status, headers, markup = _fetch(base_url, path)
        _require_status(status, 200, path)
        pages[path] = (headers, markup)

    home_headers, home = pages["/"]
    home_text = _page_text(home)
    _require("native Gmail client for macOS" in home_text, "home page is missing the native macOS product description")
    _require((download_url, "Download for macOS") in _anchors(home), "home page is missing the exact configured macOS download link")
    _require(not re.search(r"Continue with Google|href=[\"']/gmail|href=[\"']/dashboard", home, re.I), "home page exposes the retired web product UI")

    content_security_policy = home_headers.get("Content-Security-Policy", "").lower()
    _require("frame-ancestors 'none'" in content_security_policy, "home response is missing CSP frame-ancestors 'none'")
    _require("font-src 'none'" in content_security_policy, "home response is missing CSP font-src 'none'")
    _require(home_headers.get("X-Content-Type-Options", "").lower() == "nosniff", "home response is missing X-Content-Type-Options: nosniff")
    _require(home_headers.get("X-Frame-Options", "").upper() == "DENY", "home response is missing X-Frame-Options: DENY")
    _require(home_headers.get("Referrer-Policy", "").lower() == "no-referrer", "home response is missing Referrer-Policy: no-referrer")
    _require("camera=()" in home_headers.get("Permissions-Policy", "").lower(), "home response is missing the camera Permissions-Policy restriction")
    _require(home_headers.get("X-Powered-By") is None, "home response exposes X-Powered-By")

    privacy = pages["/privacy"][1]
    privacy_text = _page_text(privacy)
    _require(effective_date in privacy_text, "privacy page is missing the configured effective date")
    _require(hosting_providers in privacy_text, "privacy page is missing the configured hosting-provider disclosure")
    _require(data_regions in privacy_text, "privacy page is missing the configured data-region disclosure")
    retention_text = f"protected backups is scheduled to expire after {backup_retention_days} days"
    _require(retention_text in privacy_text, f"privacy page is missing the configured retention disclosure: {retention_text!r}")

    terms_text = _page_text(pages["/terms"][1])
    _require("Terms of Service" in terms_text, "terms page is missing its Terms of Service heading")
    support = pages["/support"][1]
    support_text = _page_text(support)
    _require(support_email in support_text, "support page is missing the configured support email")
    _require((status_page_url, "Electronic Mail service status page") in _anchors(support), "support page is missing the exact configured status-page link")
    _require("Report a security issue" in support_text, "support page is missing security-reporting guidance")

    all_markup = "\n".join(markup for _headers, markup in pages.values())
    _require(
        not re.search(r"OWNER REVIEW REQUIRED|not approved for public launch|support@example\.com", all_markup, re.I),
        "public pages rendered placeholder or unapproved legal configuration",
    )

    post_login_headers, post_login = pages["/post-login"]
    post_login_text = _page_text(post_login)
    _require("Connection status unavailable" in post_login_text, "OAuth completion fallback is missing its unavailable state")
    _require("Try again" in post_login_text, "OAuth completion fallback is missing its retry action")
    _require("no-store" in post_login_headers.get("Cache-Control", "").lower(), "OAuth completion fallback is missing Cache-Control: no-store")

    for path in ("/gmail", "/dashboard", "/api/dashboard"):
        status, _headers, _markup = _fetch(base_url, path)
        _require_status(status, 404, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exercise the built Electronic Mail native-only public web runtime")
    parser.add_argument("--web-url", required=True)
    parser.add_argument("--download-url", required=True)
    parser.add_argument("--support-email", required=True)
    parser.add_argument("--status-page-url", required=True)
    parser.add_argument("--effective-date", required=True)
    parser.add_argument("--hosting-providers", required=True)
    parser.add_argument("--data-regions", required=True)
    parser.add_argument("--backup-retention-days", required=True, type=int)
    arguments = parser.parse_args(argv)
    try:
        validate_runtime(
            web_url=arguments.web_url,
            download_url=arguments.download_url,
            support_email=arguments.support_email,
            status_page_url=arguments.status_page_url,
            effective_date=arguments.effective_date,
            hosting_providers=arguments.hosting_providers,
            data_regions=arguments.data_regions,
            backup_retention_days=arguments.backup_retention_days,
        )
    except (OSError, WebRuntimeError) as error:
        print(f"web runtime verification failed: {error}", file=sys.stderr)
        return 1
    print("Built web runtime passed native-only, legal, download, OAuth fallback, and security-header verification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
