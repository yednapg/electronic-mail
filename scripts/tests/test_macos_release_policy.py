from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]

SETUP_PYTHON_V7 = (
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0"
)
SETUP_BUILDX_V4 = (
    "docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c # v4.2.0"
)
BUILD_PUSH_V7 = (
    "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a # v7.3.0"
)


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
        self.assertEqual(workflow.count("runs-on: macos-15-intel"), 2)
        self.assertEqual(
            workflow.count("DEVELOPER_DIR: /Applications/Xcode_16.4.app/Contents/Developer"),
            2,
        )
        self.assertEqual(workflow.count('EXPECTED_XCODE_VERSION: "16.4"'), 2)
        self.assertEqual(workflow.count("EXPECTED_XCODE_BUILD: 16F6"), 2)
        self.assertEqual(workflow.count("run: bash scripts/verify-macos-toolchain.sh"), 2)
        self.assertEqual(workflow.count('python-version: "3.12.10"'), 2)

    def test_quality_job_uses_the_same_toolchain_and_runs_this_policy(self) -> None:
        workflow = self.text(".github/workflows/quality.yml")
        self.assertEqual(workflow.count("runs-on: macos-15-intel"), 1)
        self.assertIn("DEVELOPER_DIR: /Applications/Xcode_16.4.app/Contents/Developer", workflow)
        self.assertIn('EXPECTED_XCODE_VERSION: "16.4"', workflow)
        self.assertIn("EXPECTED_XCODE_BUILD: 16F6", workflow)
        self.assertIn('python-version: "3.12.10"', workflow)
        self.assertIn("python3 -m unittest scripts/tests/test_macos_release_policy.py", workflow)
        self.assertIn("run: bash scripts/verify-macos-toolchain.sh", workflow)

    def test_workflows_pin_node24_action_runtime_releases(self) -> None:
        quality = self.text(".github/workflows/quality.yml")
        release = self.text(".github/workflows/release-macos.yml")

        self.assertEqual(quality.count(SETUP_PYTHON_V7), 3)
        self.assertEqual(release.count(SETUP_PYTHON_V7), 2)
        self.assertEqual(quality.count(SETUP_BUILDX_V4), 2)
        self.assertEqual(quality.count(BUILD_PUSH_V7), 2)

        for retired_pin in (
            "a26af69be951a213d495a4c3e4e4022e16d87065",
            "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f",
            "10e90e3645eae34f1e60eeb005ba3a3d33f178e8",
        ):
            with self.subTest(retired_pin=retired_pin):
                self.assertNotIn(retired_pin, quality)
                self.assertNotIn(retired_pin, release)

    def test_release_requires_successful_quality_gates_from_main_for_the_exact_commit(self) -> None:
        workflow = self.text(".github/workflows/release-macos.yml")
        self.assertIn('--workflow quality.yml \\\n              --branch main \\\n              --commit "$GITHUB_SHA"', workflow)
        self.assertIn("--json conclusion,headBranch,headSha,status", workflow)
        self.assertIn(
            '.headBranch == "main" and .headSha == env.GITHUB_SHA '
            'and .status == "completed" and .conclusion == "success"',
            workflow,
        )

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

    def test_app_and_embedded_framework_require_hardened_runtime(self) -> None:
        project = self.text("macos/ElectronicMail/Project.swift")
        generated_project = self.text(
            "macos/ElectronicMail/ElectronicMail.xcodeproj/project.pbxproj"
        )
        verifier = self.text("scripts/verify-macos-release.sh")

        self.assertGreaterEqual(project.count('"ENABLE_HARDENED_RUNTIME": "YES"'), 2)
        self.assertEqual(generated_project.count("ENABLE_HARDENED_RUNTIME = YES;"), 4)
        self.assertIn('for code_path in "$APP_PATH" "$FRAMEWORK"; do', verifier)
        self.assertIn(
            '[ "$FRAMEWORK_BUNDLE_ID" = "app.electronicmail.core" ]',
            verifier,
        )
        self.assertIn(
            'fail "Hardened Runtime is missing from signed code: $code_path"',
            verifier,
        )


if __name__ == "__main__":
    unittest.main()
