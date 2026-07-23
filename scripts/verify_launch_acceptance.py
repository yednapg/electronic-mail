#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit


REQUIRED_MANUAL_TESTS = {
    "install",
    "auth",
    "full_sync",
    "compose",
    "draft",
    "conversation",
    "actions",
    "search",
    "attachments",
    "sync",
    "scale",
    "failure",
    "security",
    "accessibility",
    "update",
}
REQUIRED_APPROVALS = {
    "product_owner",
    "privacy_legal",
    "google_oauth_security",
    "support_on_call",
    "production_operations",
}
RESERVED_HOSTS = {"example.com", "example.net", "example.org", "localhost", "localhost.localdomain"}
RESERVED_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".invalid",
    ".localhost",
    ".local",
    ".test",
)
PLACEHOLDER_PATTERN = re.compile(r"\b(?:owner review|required|replace|placeholder|sample|tbd|todo|unknown)\b", re.I)
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AcceptanceError(ValueError):
    pass


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AcceptanceError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    _require(not missing, f"{label} is missing fields: {', '.join(missing)}")
    _require(not unexpected, f"{label} contains unexpected fields: {', '.join(unexpected)}")


def _meaningful_text(
    value: Any,
    label: str,
    *,
    allow_empty: bool = False,
    minimum_length: int = 2,
) -> str:
    _require(isinstance(value, str), f"{label} must be a string")
    normalized = value.strip()
    if allow_empty and not normalized:
        return ""
    _require(len(normalized) >= minimum_length, f"{label} is too short to be accountable launch evidence")
    _require(not PLACEHOLDER_PATTERN.search(normalized), f"{label} still contains placeholder text")
    return normalized


def _timestamp(value: Any, label: str, now: datetime) -> datetime:
    _require(isinstance(value, str) and value.strip() == value, f"{label} must be an ISO-8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise AcceptanceError(f"{label} must be an ISO-8601 timestamp: {error}") from error
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{label} must include a time-zone offset")
    parsed_utc = parsed.astimezone(timezone.utc)
    _require(parsed_utc <= now + timedelta(minutes=5), f"{label} cannot be in the future")
    return parsed_utc


def _validated_https_url(value: Any, label: str, *, require_path: bool) -> tuple[str, str, int]:
    _require(isinstance(value, str) and value.strip() == value, f"{label} must be an HTTPS URL")
    _require(not any(ord(character) < 33 for character in value), f"{label} cannot contain whitespace or control characters")
    parsed = urlsplit(value)
    _require(parsed.scheme == "https" and bool(parsed.hostname), f"{label} must be an HTTPS URL")
    _require(not parsed.username and not parsed.password, f"{label} cannot contain credentials")
    try:
        port = parsed.port
    except ValueError as error:
        raise AcceptanceError(f"{label} has an invalid port: {error}") from error
    _require(port is None, f"{label} must omit an explicit HTTPS port")
    _require(not parsed.query and not parsed.fragment, f"{label} cannot contain a query or URL fragment")
    if require_path:
        _require(parsed.path not in ("", "/"), f"{label} must identify a specific evidence record")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    _require(
        "." in hostname
        and ":" not in hostname
        and not re.fullmatch(r"[\d.]+", hostname)
        and hostname not in RESERVED_HOSTS
        and not hostname.endswith(RESERVED_SUFFIXES),
        f"{label} must use a public non-reserved hostname",
    )
    _require(
        not any(marker in hostname for marker in ("your-owned-domain", "placeholder", "changeme")),
        f"{label} still uses a placeholder hostname",
    )
    labels = hostname.split(".")
    _require(
        len(hostname) <= 253
        and all(
            len(part) <= 63 and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", part)
            for part in labels
        )
        and bool(re.fullmatch(r"[a-z]{2,63}", labels[-1])),
        f"{label} has an invalid public hostname",
    )
    _require(parsed.netloc == hostname, f"{label} must use a canonical lowercase hostname without a trailing dot")
    if require_path:
        _require(not parsed.path.endswith("/"), f"{label} evidence path must not end with a slash")
        _require(bool(re.fullmatch(r"/[A-Za-z0-9._~/-]+", parsed.path)), f"{label} must use a canonical ASCII evidence path")
        path_parts = parsed.path.split("/")[1:]
        _require(all(part not in ("", ".", "..") for part in path_parts), f"{label} evidence path cannot contain empty or dot segments")
        _require(value == f"https://{hostname}{parsed.path}", f"{label} must be a canonical evidence URL")
    return value, hostname, 443


def _accountable_email(value: Any, label: str) -> str:
    email = _meaningful_text(value, label, minimum_length=6).lower()
    _require(email.count("@") == 1, f"{label} must be an accountable email address")
    local, domain = email.rsplit("@", 1)
    _require(
        bool(re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)) and not local.startswith(".") and not local.endswith(".") and ".." not in local,
        f"{label} has an invalid email local part",
    )
    _require(
        bool(re.fullmatch(r"[a-z0-9.-]+", domain))
        and not domain.startswith(".")
        and not domain.endswith(".")
        and ".." not in domain,
        f"{label} has an invalid email domain",
    )
    _validated_https_url(f"https://{domain}", f"{label} domain", require_path=False)
    return email


def _evidence_record(
    value: Any,
    label: str,
    *,
    evidence_hostname: str,
    evidence_port: int,
    evidence_urls: set[str],
    evidence_hashes: set[str],
) -> tuple[str, str]:
    _require(isinstance(value, dict), f"{label} must be an object")
    _require_exact_keys(value, {"url", "sha256"}, label)
    normalized_url, hostname, port = _validated_https_url(value["url"], f"{label}.url", require_path=True)
    _require((hostname, port) == (evidence_hostname, evidence_port), f"{label}.url must use the approved evidence origin")
    expected_hash = value["sha256"]
    _require(isinstance(expected_hash, str) and bool(SHA256_PATTERN.fullmatch(expected_hash)), f"{label}.sha256 must be 64 lowercase hexadecimal characters")
    _require(normalized_url not in evidence_urls, f"launch evidence URL is reused: {normalized_url}")
    _require(expected_hash not in evidence_hashes, f"launch evidence content hash is reused: {expected_hash}")
    evidence_urls.add(normalized_url)
    evidence_hashes.add(expected_hash)
    return normalized_url, expected_hash


def _load_manifest(path: Path) -> dict[str, Any]:
    _require(path.is_file(), "LAUNCH_ACCEPTANCE_PATH must point to a file")
    _require(not path.is_symlink(), "LAUNCH_ACCEPTANCE_PATH must not be a symbolic link")
    _require(path.stat().st_size <= 1024 * 1024, "launch acceptance manifest cannot exceed 1 MiB")
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle, object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"launch acceptance manifest is invalid: {error}") from error
    _require(isinstance(payload, dict), "launch acceptance manifest must be a JSON object")
    return payload


def validate_manifest(
    path: Path,
    *,
    release_sha: str,
    dmg_sha256: str,
    release_created_at: str,
    evidence_origin: str,
    version: str,
    build_number: str,
    backend_origin: str,
    web_origin: str,
    now: datetime | None = None,
) -> list[tuple[str, str, str]]:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _require(bool(SHA_PATTERN.fullmatch(release_sha)), "expected release SHA must be a full lowercase commit")
    _require(bool(re.fullmatch(r"[0-9a-f]{64}", dmg_sha256)), "expected DMG SHA-256 must be 64 lowercase hexadecimal characters")
    release_time = _timestamp(release_created_at, "release metadata created_at_utc", current_time)
    freshness_cutoff = current_time - timedelta(days=14)
    _require(release_time >= freshness_cutoff, "the exact release DMG is older than 14 days")
    normalized_evidence_origin = evidence_origin.rstrip("/")
    _origin_value, evidence_hostname, evidence_port = _validated_https_url(
        normalized_evidence_origin,
        "expected evidence origin",
        require_path=False,
    )
    evidence_origin_parts = urlsplit(normalized_evidence_origin)
    _require(
        evidence_origin_parts.path in ("", "/") and not evidence_origin_parts.query,
        "expected evidence origin must not contain a path or query",
    )
    manifest = _load_manifest(path)
    _require_exact_keys(
        manifest,
        {"schema_version", "product", "release", "completed_at", "manual_tests", "approvals"},
        "manifest",
    )
    _require(manifest["schema_version"] == 2, "manifest schema_version must be 2")
    _require(manifest["product"] == "Electronic Mail", "manifest product must be Electronic Mail")

    release = manifest["release"]
    _require(isinstance(release, dict), "manifest release must be an object")
    _require_exact_keys(
        release,
        {"source_commit", "dmg_sha256", "version", "build_number", "backend_origin", "web_origin"},
        "manifest release",
    )
    expected_release = {
        "source_commit": release_sha,
        "dmg_sha256": dmg_sha256,
        "version": version,
        "build_number": build_number,
        "backend_origin": backend_origin,
        "web_origin": web_origin,
    }
    for field, expected in expected_release.items():
        _require(release[field] == expected, f"manifest release {field} mismatch: expected {expected!r}")

    manual_tests = manifest["manual_tests"]
    _require(isinstance(manual_tests, list), "manual_tests must be an array")
    manual_by_id: dict[str, dict[str, Any]] = {}
    evidence_times: list[datetime] = []
    evidence_urls: set[str] = set()
    evidence_hashes: set[str] = set()
    evidence_records: list[tuple[str, str, str]] = []
    for index, item in enumerate(manual_tests):
        label = f"manual_tests[{index}]"
        _require(isinstance(item, dict), f"{label} must be an object")
        _require_exact_keys(
            item,
            {"id", "status", "tester", "tested_at", "test_account", "environment", "evidence", "notes"},
            label,
        )
        test_id = item["id"]
        _require(isinstance(test_id, str) and test_id in REQUIRED_MANUAL_TESTS, f"{label}.id is not a required launch test")
        _require(test_id not in manual_by_id, f"manual_tests contains duplicate id {test_id!r}")
        _require(item["status"] == "pass", f"manual test {test_id!r} must have status 'pass'")
        _accountable_email(item["tester"], f"manual test {test_id!r} tester")
        evidence_times.append(_timestamp(item["tested_at"], f"manual test {test_id!r} tested_at", current_time))
        _meaningful_text(item["test_account"], f"manual test {test_id!r} test_account", minimum_length=5)
        environment = _meaningful_text(item["environment"], f"manual test {test_id!r} environment", minimum_length=10)
        _require(bool(re.search(r"\bmacOS\s+\d{2}(?:\.\d+)*\b", environment)), f"manual test {test_id!r} environment must identify the macOS version")
        _meaningful_text(item["notes"], f"manual test {test_id!r} notes", allow_empty=True)
        evidence = item["evidence"]
        _require(isinstance(evidence, list) and evidence, f"manual test {test_id!r} evidence must be a non-empty array")
        for evidence_index, record in enumerate(evidence):
            normalized_url, expected_hash = _evidence_record(
                record,
                f"manual test {test_id!r} evidence[{evidence_index}]",
                evidence_hostname=evidence_hostname,
                evidence_port=evidence_port,
                evidence_urls=evidence_urls,
                evidence_hashes=evidence_hashes,
            )
            evidence_records.append((f"manual-{test_id}-{evidence_index + 1}", normalized_url, expected_hash))
        manual_by_id[test_id] = item
    _require(set(manual_by_id) == REQUIRED_MANUAL_TESTS, f"manual_tests must contain exactly: {', '.join(sorted(REQUIRED_MANUAL_TESTS))}")

    approvals = manifest["approvals"]
    _require(isinstance(approvals, list), "approvals must be an array")
    approvals_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(approvals):
        label = f"approvals[{index}]"
        _require(isinstance(item, dict), f"{label} must be an object")
        _require_exact_keys(item, {"id", "status", "approver", "approved_at", "evidence", "notes"}, label)
        approval_id = item["id"]
        _require(isinstance(approval_id, str) and approval_id in REQUIRED_APPROVALS, f"{label}.id is not a required approval")
        _require(approval_id not in approvals_by_id, f"approvals contains duplicate id {approval_id!r}")
        _require(item["status"] == "approved", f"approval {approval_id!r} must have status 'approved'")
        _accountable_email(item["approver"], f"approval {approval_id!r} approver")
        evidence_times.append(_timestamp(item["approved_at"], f"approval {approval_id!r} approved_at", current_time))
        normalized_url, expected_hash = _evidence_record(
            item["evidence"],
            f"approval {approval_id!r} evidence",
            evidence_hostname=evidence_hostname,
            evidence_port=evidence_port,
            evidence_urls=evidence_urls,
            evidence_hashes=evidence_hashes,
        )
        evidence_records.append((f"approval-{approval_id}", normalized_url, expected_hash))
        _meaningful_text(item["notes"], f"approval {approval_id!r} notes", allow_empty=True)
        approvals_by_id[approval_id] = item
    _require(set(approvals_by_id) == REQUIRED_APPROVALS, f"approvals must contain exactly: {', '.join(sorted(REQUIRED_APPROVALS))}")

    completed_at = _timestamp(manifest["completed_at"], "manifest completed_at", current_time)
    _require(all(timestamp >= release_time for timestamp in evidence_times), "tests and approvals must not predate the exact release DMG")
    _require(all(timestamp >= freshness_cutoff for timestamp in evidence_times), "test or approval evidence is older than 14 days")
    _require(all(completed_at >= timestamp for timestamp in evidence_times), "manifest completed_at must not precede a test or approval")
    _require(completed_at >= release_time, "manifest completed_at must not predate the exact release DMG")
    _require(completed_at >= freshness_cutoff, "launch acceptance evidence is older than 14 days")
    return evidence_records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an exact-DMG-bound Electronic Mail launch acceptance manifest")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--dmg-sha256", required=True)
    parser.add_argument("--release-created-at", required=True)
    parser.add_argument("--evidence-origin", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-number", required=True)
    parser.add_argument("--backend-origin", required=True)
    parser.add_argument("--web-origin", required=True)
    parser.add_argument("--evidence-index", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        evidence_records = validate_manifest(
            arguments.manifest,
            release_sha=arguments.release_sha,
            dmg_sha256=arguments.dmg_sha256,
            release_created_at=arguments.release_created_at,
            evidence_origin=arguments.evidence_origin,
            version=arguments.version,
            build_number=arguments.build_number,
            backend_origin=arguments.backend_origin,
            web_origin=arguments.web_origin,
        )
        _require(arguments.evidence_index.parent.is_dir(), "evidence index parent directory is missing")
        _require(not arguments.evidence_index.exists(), "evidence index output must not already exist")
        with arguments.evidence_index.open("x", encoding="utf-8") as handle:
            for record_id, url, expected_hash in evidence_records:
                handle.write(f"{record_id}\t{url}\t{expected_hash}\n")
    except (AcceptanceError, OSError) as error:
        print(f"launch acceptance verification failed: {error}", file=sys.stderr)
        return 1
    print("Launch acceptance manifest passed: the exact DMG, all manual tests, and accountable approvals match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
