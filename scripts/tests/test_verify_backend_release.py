from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_backend_release import (  # noqa: E402
    BackendReleaseVerificationError,
    validate_backend_origin,
    verify_release_payloads,
)


RELEASE = "0123456789abcdef0123456789abcdef01234567"


def valid_payloads() -> tuple[dict, dict, dict]:
    health = {"status": "ok", "release": RELEASE}
    ready = {
        "status": "ready",
        "environment": "production",
        "database": "postgres",
        "release": RELEASE,
    }
    workers = [
        ("fast", ["critical", "default"]),
        ("reader", ["reader"]),
        ("slow", ["slow"]),
        ("poller", ["gmail_poll"]),
    ]
    ops = {
        "environment": "production",
        "release": RELEASE,
        "queue_depth": {"critical": 1},
        "dead_jobs": 0,
        "stale_running_jobs": 0,
        "oldest_queued_age_seconds": 4,
        "workers": [
            {
                "worker_id": worker_id,
                "queues": queues,
                "release_sha": RELEASE,
                "age_seconds": 5,
                "fresh": True,
                "release_matches_expected": True,
            }
            for worker_id, queues in workers
        ],
        "worker_online": True,
        "worker_releases_match": True,
        "required_queues_ready": True,
    }
    return health, ready, ops


class BackendReleaseVerifierTests(unittest.TestCase):
    def test_accepts_exact_release_database_and_reviewed_worker_roles(self) -> None:
        health, ready, ops = valid_payloads()
        self.assertEqual(
            verify_release_payloads(
                health=health,
                ready=ready,
                ops=ops,
                expected_release_sha=RELEASE,
            ),
            {"fast": 1, "reader": 1, "slow": 1, "poller": 1},
        )

    def test_rejects_missing_or_combined_worker_roles(self) -> None:
        health, ready, ops = valid_payloads()
        ops["workers"] = ops["workers"][:-1]
        with self.assertRaisesRegex(BackendReleaseVerificationError, "missing fresh worker roles: poller"):
            verify_release_payloads(
                health=health, ready=ready, ops=ops, expected_release_sha=RELEASE
            )

        _, _, ops = valid_payloads()
        ops["workers"][0]["queues"] = ["critical", "default", "reader"]
        with self.assertRaisesRegex(BackendReleaseVerificationError, "unreviewed queue role"):
            verify_release_payloads(
                health=health, ready=ready, ops=ops, expected_release_sha=RELEASE
            )

    def test_rejects_mixed_release_or_stale_heartbeat_marked_fresh(self) -> None:
        health, ready, ops = valid_payloads()
        ops["workers"][0]["release_sha"] = "f" * 40
        with self.assertRaisesRegex(BackendReleaseVerificationError, "running release"):
            verify_release_payloads(
                health=health, ready=ready, ops=ops, expected_release_sha=RELEASE
            )

        _, _, ops = valid_payloads()
        ops["workers"][0]["age_seconds"] = 121
        with self.assertRaisesRegex(BackendReleaseVerificationError, "invalid heartbeat age"):
            verify_release_payloads(
                health=health, ready=ready, ops=ops, expected_release_sha=RELEASE
            )

    def test_ignores_old_database_heartbeat_rows(self) -> None:
        health, ready, ops = valid_payloads()
        old = deepcopy(ops["workers"][0])
        old.update({"worker_id": "old-fast", "fresh": False, "release_sha": "f" * 40, "age_seconds": 500})
        ops["workers"].append(old)
        verify_release_payloads(
            health=health, ready=ready, ops=ops, expected_release_sha=RELEASE
        )

    def test_rejects_non_https_or_path_bearing_backend_origins(self) -> None:
        self.assertEqual(validate_backend_origin("https://api.example.com/"), "https://api.example.com")
        for value in (
            "http://api.example.com",
            "https://user:secret@api.example.com",
            "https://api.example.com/v1",
            "https://api.example.com?token=x",
        ):
            with self.subTest(value=value), self.assertRaises(BackendReleaseVerificationError):
                validate_backend_origin(value)

    def test_railway_workers_use_liveness_only_and_external_promotion_gate(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for path in sorted((root / "backend").glob("railway.worker-*.json")):
            config = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("healthcheckPath", config["deploy"], path.name)
        workflow = (root / ".github/workflows/verify-production-backend.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("deployment_status:", workflow)
        self.assertIn("PRODUCTION_OPS_BEARER_TOKEN", workflow)
        self.assertIn("--stable-successes 3", workflow)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertNotIn("ref: ${{ github.sha }}", workflow)
        self.assertNotIn(
            "ref: ${{ github.event_name == 'workflow_dispatch' && inputs.release_sha || github.event.deployment.sha }}",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
