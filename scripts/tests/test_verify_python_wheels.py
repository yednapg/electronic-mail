from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "verify_python_wheels.py"
SPEC = importlib.util.spec_from_file_location("verify_python_wheels", SCRIPT)
assert SPEC and SPEC.loader
wheel_policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wheel_policy
SPEC.loader.exec_module(wheel_policy)

REFRESH_SCRIPT = SCRIPT.parent / "refresh-python-lock-hashes.py"
REFRESH_SPEC = importlib.util.spec_from_file_location("refresh_python_lock_hashes", REFRESH_SCRIPT)
assert REFRESH_SPEC and REFRESH_SPEC.loader
lock_refresh = importlib.util.module_from_spec(REFRESH_SPEC)
sys.modules[REFRESH_SPEC.name] = lock_refresh
REFRESH_SPEC.loader.exec_module(lock_refresh)


class PythonWheelPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lock = self.root / "requirements.lock"
        self.lock.write_text(
            "example-one==1.2.3 \\\n"
            "    --hash=sha256:" + "a" * 64 + "\n"
            "example-two==2.3.4 \\\n"
            "    --hash=sha256:" + "b" * 64 + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_download_uses_exact_macos_intel_python_target_and_hash_policy(self) -> None:
        observed: list[str] = []

        def successful_download(command, **_kwargs):
            observed.extend(command)
            destination = Path(command[command.index("--dest") + 1])
            (destination / "example_one-1.2.3-py3-none-any.whl").touch()
            (destination / "example_two-2.3.4-py3-none-any.whl").touch()
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        count = wheel_policy.verify_lock_for_target(
            self.lock,
            wheel_policy.MACOS_INTEL_CPYTHON_312,
            runner=successful_download,
        )

        self.assertEqual(count, 2)
        for required in (
            "download",
            "--no-deps",
            "--require-hashes",
            "--only-binary=:all:",
            "macosx_15_0_x86_64",
            "3.12",
            "cp",
        ):
            self.assertIn(required, observed)

    def test_missing_target_wheel_fails_closed_with_pip_error(self) -> None:
        def failed_download(_command, **_kwargs):
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="No matching distribution found for example-one==1.2.3",
            )

        with self.assertRaisesRegex(
            wheel_policy.WheelPolicyError,
            r"(?s)missing a hash-approved wheel.*No matching distribution",
        ):
            wheel_policy.verify_lock_for_target(
                self.lock,
                wheel_policy.MACOS_INTEL_CPYTHON_312,
                runner=failed_download,
            )

    def test_download_must_produce_exactly_one_wheel_per_pin(self) -> None:
        def incomplete_download(command, **_kwargs):
            destination = Path(command[command.index("--dest") + 1])
            (destination / "example_one-1.2.3-py3-none-any.whl").touch()
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(wheel_policy.WheelPolicyError, "downloaded 1 wheels.*expected exactly 2"):
            wheel_policy.verify_lock_for_target(
                self.lock,
                wheel_policy.MACOS_INTEL_CPYTHON_312,
                runner=incomplete_download,
            )

    def test_malformed_or_duplicate_lock_pin_is_rejected_before_network(self) -> None:
        fixtures = (
            "example-one>=1.2.3\n",
            "example-one==1.2.3\nexample_one==1.2.3\n",
        )
        for contents in fixtures:
            with self.subTest(contents=contents):
                self.lock.write_text(contents, encoding="utf-8")
                with self.assertRaises(wheel_policy.WheelPolicyError):
                    wheel_policy.verify_lock_for_target(
                        self.lock,
                        wheel_policy.MACOS_INTEL_CPYTHON_312,
                    )

    def test_lock_refresh_is_atomic_when_required_target_has_no_wheel(self) -> None:
        original = self.lock.read_text(encoding="utf-8")
        with (
            patch.object(lock_refresh, "_wheel_hashes", return_value=["c" * 64]),
            patch.object(
                lock_refresh,
                "verify_lock_for_target",
                side_effect=wheel_policy.WheelPolicyError("missing target wheel"),
            ),
            self.assertRaisesRegex(lock_refresh.LockRefreshError, "refusing to replace"),
        ):
            lock_refresh.refresh(self.lock)

        self.assertEqual(self.lock.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.root.glob(".requirements.lock.*")), [])


if __name__ == "__main__":
    unittest.main()
