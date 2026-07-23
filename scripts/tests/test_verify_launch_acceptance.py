from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_launch_acceptance import (  # noqa: E402
    AcceptanceError,
    REQUIRED_APPROVALS,
    REQUIRED_MANUAL_TESTS,
    validate_manifest,
)


RELEASE_SHA = "0123456789abcdef0123456789abcdef01234567"
DMG_SHA256 = "89abcdef0123456789abcdef0123456789abcdef0123456789abcdef01234567"
RELEASE_CREATED_AT = "2026-07-21T09:00:00+00:00"
VERSION = "1.2.3"
BUILD_NUMBER = "456"
BACKEND_ORIGIN = "https://api.launch.electronicmail.app"
WEB_ORIGIN = "https://www.launch.electronicmail.app"
EVIDENCE_ORIGIN = "https://evidence.electronicmail.app"
NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def evidence_record(url: str) -> dict[str, str]:
    return {"url": url, "sha256": hashlib.sha256(f"proof:{url}".encode("utf-8")).hexdigest()}


def valid_manifest() -> dict[str, object]:
    return {
        "schema_version": 2,
        "product": "Electronic Mail",
        "release": {
            "source_commit": RELEASE_SHA,
            "dmg_sha256": DMG_SHA256,
            "version": VERSION,
            "build_number": BUILD_NUMBER,
            "backend_origin": BACKEND_ORIGIN,
            "web_origin": WEB_ORIGIN,
        },
        "completed_at": "2026-07-21T11:00:00+00:00",
        "manual_tests": [
            {
                "id": test_id,
                "status": "pass",
                "tester": "launch.tester@electronicmail.app",
                "tested_at": "2026-07-21T10:00:00+00:00",
                "test_account": "redacted launch account",
                "environment": "Clean Mac on macOS 15",
                "evidence": [evidence_record(f"https://evidence.electronicmail.app/tests/{test_id}")],
                "notes": "",
            }
            for test_id in sorted(REQUIRED_MANUAL_TESTS)
        ],
        "approvals": [
            {
                "id": approval_id,
                "status": "approved",
                "approver": "launch.approver@electronicmail.app",
                "approved_at": "2026-07-21T10:30:00+00:00",
                "evidence": evidence_record(f"https://evidence.electronicmail.app/approvals/{approval_id}"),
                "notes": "",
            }
            for approval_id in sorted(REQUIRED_APPROVALS)
        ],
    }


class LaunchAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "acceptance.json"

    def write(self, payload: object) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def validate(self) -> None:
        validate_manifest(
            self.path,
            release_sha=RELEASE_SHA,
            dmg_sha256=DMG_SHA256,
            release_created_at=RELEASE_CREATED_AT,
            evidence_origin=EVIDENCE_ORIGIN,
            version=VERSION,
            build_number=BUILD_NUMBER,
            backend_origin=BACKEND_ORIGIN,
            web_origin=WEB_ORIGIN,
            now=NOW,
        )

    def test_complete_build_bound_manifest_passes(self) -> None:
        self.write(valid_manifest())
        self.validate()

    def test_wrong_build_and_nonpassing_test_fail(self) -> None:
        for mutation in ("build", "dmg", "status"):
            with self.subTest(mutation=mutation):
                payload = valid_manifest()
                if mutation == "build":
                    payload["release"]["build_number"] = "455"  # type: ignore[index]
                elif mutation == "dmg":
                    payload["release"]["dmg_sha256"] = "a" * 64  # type: ignore[index]
                else:
                    payload["manual_tests"][0]["status"] = "blocked"  # type: ignore[index]
                self.write(payload)
                with self.assertRaises(AcceptanceError):
                    self.validate()

    def test_every_test_and_approval_is_required_exactly_once(self) -> None:
        for field in ("manual_tests", "approvals"):
            with self.subTest(field=field):
                payload = valid_manifest()
                payload[field] = payload[field][1:]  # type: ignore[index]
                self.write(payload)
                with self.assertRaises(AcceptanceError):
                    self.validate()

                payload = valid_manifest()
                payload[field].append(deepcopy(payload[field][0]))  # type: ignore[union-attr,index]
                self.write(payload)
                with self.assertRaises(AcceptanceError):
                    self.validate()

    def test_placeholder_people_insecure_evidence_and_naive_time_fail(self) -> None:
        mutations = (
            lambda payload: payload["approvals"][0].update({"approver": "TBD"}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"evidence": [evidence_record("https://localhost/test")]}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"tested_at": "2026-07-21T10:00:00"}),  # type: ignore[index,union-attr]
            lambda payload: payload.update({"completed_at": "2026-07-21T09:00:00+00:00"}),
        )
        for mutation in mutations:
            payload = valid_manifest()
            mutation(payload)
            self.write(payload)
            with self.assertRaises(AcceptanceError):
                self.validate()

    def test_evidence_origin_url_accountability_and_freshness_fail_closed(self) -> None:
        mutations = (
            lambda payload: payload["manual_tests"][0].update({"evidence": [evidence_record("https://evidence.electronicmail.app:99999/tests/install")]}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"evidence": [evidence_record("https://evidence.electronicmail.app/\nanything")]}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"evidence": [evidence_record("https://evidence.electronicmail.app/")]}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"evidence": [evidence_record("https://evidence.electronicmail.app/tests/install?alias=1")]}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"evidence": [evidence_record("https://EVIDENCE.ELECTRONICMAIL.APP:443/tests/install")]}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"evidence": [evidence_record("https://evidence.electronicmail.app/tests/../tests/install")]}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][1].update({"evidence": deepcopy(payload["manual_tests"][0]["evidence"])}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"tester": "xx"}),  # type: ignore[index,union-attr]
            lambda payload: payload["manual_tests"][0].update({"tester": "person@electronicmail.app:443"}),  # type: ignore[index,union-attr]
            lambda payload: payload["approvals"][0].update({"approver": "person@electronicmail.app/path"}),  # type: ignore[index,union-attr]
            lambda payload: payload["approvals"][0].update({"approved_at": "1970-01-01T00:00:00+00:00"}),  # type: ignore[index,union-attr]
        )
        for mutation in mutations:
            payload = valid_manifest()
            mutation(payload)
            self.write(payload)
            with self.assertRaises(AcceptanceError):
                self.validate()

    def test_expected_evidence_origin_rejects_loopback_notation(self) -> None:
        self.write(valid_manifest())
        with self.assertRaises(AcceptanceError):
            validate_manifest(
                self.path,
                release_sha=RELEASE_SHA,
                dmg_sha256=DMG_SHA256,
                release_created_at=RELEASE_CREATED_AT,
                evidence_origin="https://0x7f.0.0.1",
                version=VERSION,
                build_number=BUILD_NUMBER,
                backend_origin=BACKEND_ORIGIN,
                web_origin=WEB_ORIGIN,
                now=NOW,
            )

    def test_duplicate_json_fields_and_symlink_fail(self) -> None:
        self.path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaises(AcceptanceError):
            self.validate()

        target = Path(self.temporary.name) / "target.json"
        target.write_text(json.dumps(valid_manifest()), encoding="utf-8")
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(AcceptanceError):
            self.validate()


if __name__ == "__main__":
    unittest.main()
