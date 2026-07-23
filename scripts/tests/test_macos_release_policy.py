from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MacOSReleaseToolchainPolicyTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def toolchain_config(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw_line in self.text("scripts/macos-release-toolchain.env").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            self.assertEqual(line.count("="), 1, f"invalid toolchain configuration line: {line}")
            key, value = line.split("=", 1)
            values[key] = value
        return values

    def test_approved_toolchain_is_one_exact_stable_xcode_build(self) -> None:
        self.assertEqual(
            self.toolchain_config(),
            {
                "APPROVED_XCODE_VERSION": "16.4",
                "APPROVED_XCODE_BUILD": "16F6",
                "APPROVED_DEVELOPER_DIR": "/Applications/Xcode_16.4.app/Contents/Developer",
            },
        )

    def test_release_workflow_pins_both_jobs_to_the_approved_toolchain(self) -> None:
        workflow = self.text(".github/workflows/release-macos.yml")
        self.assertEqual(
            workflow.count("DEVELOPER_DIR: /Applications/Xcode_16.4.app/Contents/Developer"),
            2,
        )
        self.assertEqual(workflow.count('EXPECTED_XCODE_VERSION: "16.4"'), 2)
        self.assertEqual(workflow.count("EXPECTED_XCODE_BUILD: 16F6"), 2)
        self.assertEqual(workflow.count("run: bash scripts/verify-macos-toolchain.sh"), 2)
        self.assertEqual(workflow.count('python-version: "3.12.13"'), 2)

    def test_quality_job_uses_the_same_toolchain_and_runs_this_policy(self) -> None:
        workflow = self.text(".github/workflows/quality.yml")
        self.assertIn("DEVELOPER_DIR: /Applications/Xcode_16.4.app/Contents/Developer", workflow)
        self.assertIn('EXPECTED_XCODE_VERSION: "16.4"', workflow)
        self.assertIn("EXPECTED_XCODE_BUILD: 16F6", workflow)
        self.assertIn("python3 -m unittest scripts/tests/test_macos_release_policy.py", workflow)
        self.assertIn("run: bash scripts/verify-macos-toolchain.sh", workflow)

    def test_preflight_and_release_fail_closed_through_shared_verifier(self) -> None:
        for relative in ("scripts/preflight-macos-release.sh", "scripts/release-macos.sh"):
            script = self.text(relative)
            with self.subTest(relative=relative):
                self.assertIn('source "$ROOT_DIR/scripts/macos-release-toolchain.env"', script)
                self.assertIn('bash "$ROOT_DIR/scripts/verify-macos-toolchain.sh"', script)
                self.assertIn("EXPECTED_XCODE_VERSION", script)
                self.assertIn("EXPECTED_XCODE_BUILD", script)

        verifier = self.text("scripts/verify-macos-toolchain.sh")
        self.assertIn('*[Bb]eta*) fail "stable Xcode is required; beta Xcode is forbidden', verifier)
        self.assertIn('[ "$actual_version" = "$APPROVED_XCODE_VERSION" ]', verifier)
        self.assertIn('[ "$actual_build" = "$APPROVED_XCODE_BUILD" ]', verifier)

    def test_release_metadata_and_launch_gate_bind_the_toolchain(self) -> None:
        release = self.text("scripts/release-macos.sh")
        launch = self.text("scripts/verify-launch.sh")
        for field in ('"xcode_version"', '"xcode_build"'):
            with self.subTest(field=field):
                self.assertIn(field, release)
                self.assertIn(field, launch)
        self.assertIn('source "$ROOT_DIR/scripts/macos-release-toolchain.env"', launch)


if __name__ == "__main__":
    unittest.main()
