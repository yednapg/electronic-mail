from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "verify-reproducibility.py"
SPEC = importlib.util.spec_from_file_location("verify_reproducibility", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class ReproducibilityVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original_root = verifier.ROOT
        verifier.ROOT = self.root
        (self.root / "web").mkdir()
        (self.root / "packages/types").mkdir(parents=True)
        (self.root / "backend").mkdir()
        (self.root / ".github/workflows").mkdir(parents=True)
        self.write_json("package.json", {"devDependencies": {"typescript": "5.9.3"}})
        self.write_json("web/package.json", {"dependencies": {"react": "19.2.7"}})
        self.write_json("packages/types/package.json", {"name": "types"})
        self.write_json(
            "package-lock.json",
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"devDependencies": {"typescript": "5.9.3"}},
                    "web": {"dependencies": {"react": "19.2.7"}},
                    "packages/types": {"name": "types"},
                    "node_modules/react": {"version": "19.2.7", "integrity": "sha512-test"},
                    "node_modules/typescript": {"version": "5.9.3", "integrity": "sha512-test"},
                },
            },
        )
        (self.root / ".nvmrc").write_text("22.22.0\n", encoding="utf-8")
        (self.root / ".npmrc").write_text("engine-strict=true\n", encoding="utf-8")
        (self.root / ".python-version").write_text("3.12.13\n", encoding="utf-8")
        (self.root / "backend/requirements.lock").write_text("fastapi==0.139.0\n", encoding="utf-8")
        pinned = "sha256:" + "a" * 64
        dockerfile = f"# syntax=docker/dockerfile:1.7@{pinned}\nFROM example.invalid/runtime:1@{pinned}\nRUN pip install --no-deps -r requirements.lock\n"
        (self.root / "Dockerfile.web").write_text(dockerfile, encoding="utf-8")
        (self.root / "backend/Dockerfile").write_text(dockerfile, encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts/bootstrap.sh").write_text(
            "pip install --no-deps -r backend/requirements.lock\n",
            encoding="utf-8",
        )
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"steps:\n  - uses: actions/checkout@{action}\n  - node-version: 22.22.0\n  - run: pip install --no-deps -r backend/requirements.lock\n  - run: pip install --no-deps -r backend/requirements.lock\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        verifier.ROOT = self.original_root
        self.temporary.cleanup()

    def write_json(self, relative: str, payload: dict) -> None:
        (self.root / relative).write_text(json.dumps(payload), encoding="utf-8")

    def test_valid_fixture_passes_every_policy(self) -> None:
        verifier.verify_node_manifests()
        verifier.verify_python_lock()
        verifier.verify_container_inputs()
        verifier.verify_actions()

    def test_latest_direct_dependency_is_rejected(self) -> None:
        self.write_json("web/package.json", {"dependencies": {"react": "latest"}})
        lock = json.loads((self.root / "package-lock.json").read_text(encoding="utf-8"))
        lock["packages"]["web"]["dependencies"]["react"] = "latest"
        self.write_json("package-lock.json", lock)
        with self.assertRaisesRegex(verifier.ReproducibilityError, "exact version"):
            verifier.verify_node_manifests()

    def test_manifest_lock_drift_is_rejected(self) -> None:
        self.write_json("web/package.json", {"dependencies": {"react": "19.2.6"}})
        with self.assertRaisesRegex(verifier.ReproducibilityError, "does not exactly match"):
            verifier.verify_node_manifests()

    def test_mutable_override_is_rejected(self) -> None:
        self.write_json(
            "package.json",
            {"devDependencies": {"typescript": "5.9.3"}, "overrides": {"postcss": "^8.5.10"}},
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "overrides.postcss must use an exact version"):
            verifier.verify_node_manifests()

    def test_python_runtime_install_must_disable_dependency_resolution(self) -> None:
        (self.root / "scripts/bootstrap.sh").write_text(
            "pip install -r backend/requirements.lock\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "must install requirements.lock with --no-deps"):
            verifier.verify_python_lock()

    def test_mutable_container_base_is_rejected(self) -> None:
        (self.root / "backend/Dockerfile").write_text(
            "# syntax=docker/dockerfile:1.7@sha256:" + "a" * 64 + "\nFROM python:3.12.13-slim\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "mutable base image"):
            verifier.verify_container_inputs()

    def test_action_tag_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps:\n  - uses: actions/checkout@v6\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "not commit-pinned"):
            verifier.verify_actions()

    def test_mutable_workflow_service_image_is_rejected(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:17\n    steps:\n      - uses: actions/checkout@{action}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "service/container image is mutable"):
            verifier.verify_actions()


if __name__ == "__main__":
    unittest.main()
