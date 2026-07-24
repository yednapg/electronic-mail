#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from urllib.parse import unquote, urlsplit


class PublicURLPolicyError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicURLPolicyError(message)


def validate_public_status_url(value: str) -> str:
    """Return one canonical, public HTTPS status URL or fail closed."""
    _require(
        value == value.strip()
        and "\\" not in value
        and not any(ord(character) < 33 or ord(character) == 127 for character in value),
        "status page link contains whitespace, control characters, or a backslash",
    )
    url = urlsplit(value)
    _require(
        url.scheme == "https"
        and bool(url.hostname)
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment,
        "status page link must be a public HTTPS URL without credentials, query, or fragment",
    )
    try:
        port = url.port
    except ValueError as error:
        raise PublicURLPolicyError(f"status page link has an invalid port: {error}") from error
    _require(port is None, "status page link must omit an explicit port")

    hostname = url.hostname or ""
    _require(
        hostname == hostname.lower()
        and not hostname.endswith(".")
        and url.netloc == hostname,
        "status page link hostname must be canonical lowercase DNS",
    )
    reserved = (
        ".example",
        ".example.com",
        ".example.net",
        ".example.org",
        ".invalid",
        ".localhost",
        ".local",
        ".test",
    )
    labels = hostname.split(".")
    _require(
        "." in hostname
        and len(hostname) <= 253
        and re.fullmatch(r"[\d.]+", hostname) is None
        and hostname not in {"example.com", "example.net", "example.org", "localhost"}
        and not hostname.endswith(reserved)
        and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ),
        "status page link must use a public non-reserved DNS hostname",
    )

    _require(
        re.search(r"%(?![0-9A-Fa-f]{2})", url.path) is None,
        "status page link path contains an invalid percent escape",
    )
    try:
        decoded_path = unquote(url.path, errors="strict")
    except UnicodeDecodeError as error:
        raise PublicURLPolicyError(f"status page link path is not valid UTF-8: {error}") from error
    decoded_parts = decoded_path.split("/")[1:]
    _require(
        "\\" not in decoded_path
        and "//" not in url.path
        and "//" not in decoded_path
        and not any(ord(character) < 32 or ord(character) == 127 for character in decoded_path)
        and not any(part in (".", "..") for part in decoded_parts),
        "status page link path must be canonical",
    )
    _require(
        value == f"https://{hostname}{url.path}",
        "status page link must be a canonical HTTPS URL",
    )
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one public Electronic Mail status URL")
    parser.add_argument("--url", required=True)
    arguments = parser.parse_args(argv)
    try:
        print(validate_public_status_url(arguments.url))
    except PublicURLPolicyError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
