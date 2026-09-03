from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/install-macos-local-signed.sh"


class MacOSLocalSignedPolicyTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def run_validation(self, **values: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "VERSION",
            "BUILD_NUMBER",
            "BACKEND_URL",
            "CURRENT_YEAR",
            "APPLE_DEVELOPMENT_TEAM",
            "DEVELOPER_ID_APPLICATION",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "APPLE_DEVELOPMENT_TEAM": "ABCDEFGHIJ",
                "DEVELOPER_ID_APPLICATION": "Developer ID Application: Example Person (ABCDEFGHIJ)",
            }
        )
        environment.update(values)
        return subprocess.run(
            ["bash", str(INSTALLER), "--validate-inputs"],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_local_signed_info_preserves_bundle_identity_and_limits_ats_to_localhost(self) -> None:
        path = ROOT / "macos/ElectronicMail/Config/InfoPlists/ElectronicMail-LocalSigned-Info.plist"
        with path.open("rb") as handle:
            info = plistlib.load(handle)

        self.assertEqual(info["CFBundleDisplayName"], "Electronic Mail")
        self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleURLName"], "app.electronicmail.mac")
        self.assertEqual(info["ElectronicMailDistributionChannel"], "local-developer-id")
        self.assertIs(info["ElectronicMailNotarized"], False)
        self.assertEqual(
            info["NSAppTransportSecurity"],
            {
                "NSExceptionDomains": {
                    "localhost": {
                        "NSExceptionAllowsInsecureHTTPLoads": True,
                        "NSIncludesSubdomains": False,
                    }
                }
            },
        )

    def test_inputs_require_exact_local_backend_and_matching_full_identity(self) -> None:
        valid = self.run_validation(VERSION="2.4.1", BUILD_NUMBER="57", CURRENT_YEAR="2026")
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("team ABCDEFGHIJ", valid.stdout)

        for values in (
            {"BACKEND_URL": "http://localhost:3002"},
            {"BACKEND_URL": "https://api.electronicmail.app"},
            {"APPLE_DEVELOPMENT_TEAM": "TOO-SHORT"},
            {"DEVELOPER_ID_APPLICATION": "-"},
            {"DEVELOPER_ID_APPLICATION": "Developer ID Application: Example Person (ZZZZZZZZZZ)"},
            {"BUILD_NUMBER": "0"},
        ):
            with self.subTest(values=values):
                result = self.run_validation(**values)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_installer_is_developer_id_only_and_rolls_back_failed_replacement(self) -> None:
        script = self.text("scripts/install-macos-local-signed.sh")
        verifier = self.text("scripts/verify-macos-release.sh")
        for fragment in (
            'INSTALL_PATH="/Applications/Electronic Mail.app"',
            'security find-identity -v -p codesigning',
            'CODE_SIGN_STYLE=Manual',
            'CODE_SIGNING_REQUIRED=YES',
            'CODE_SIGN_INJECT_BASE_ENTITLEMENTS=YES',
            'SWIFT_ACTIVE_COMPILATION_CONDITIONS=',
            'ARCHS="arm64 x86_64"',
            'INFO_POLICY=local-signed',
            'SIGNING_MODE=developer-id',
            'REQUIRE_ADHOC_SIGNATURE=0',
            'REQUIRE_NOTARIZATION=0',
            'mv "$BACKUP_PATH" "$INSTALL_PATH"',
            'the previous app was restored',
            'local-working-tree',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, script)

        self.assertNotIn("codesign --force --sign -", script)
        self.assertNotIn("ELECTRONIC_MAIL_LOCAL_BETA", script)
        self.assertIn("local-signed", verifier)
        self.assertIn("local-developer-id", verifier)
        self.assertIn("signed app is missing the team-bound application identifier", verifier)

    def test_keychain_migration_is_exact_verified_and_delete_after_copy(self) -> None:
        source = self.text("macos/ElectronicMail/ElectronicMail/Core/SessionTokenStore.swift")
        read_index = source.index("readMigratableGenerationRecord(account: account)")
        write_index = source.index("records.addGenerationRecord(classicData, account: account)", read_index)
        verify_index = source.index("records.readGenerationRecord(account: account) == classicData", write_index)
        delete_index = source.index("records.deleteMigratableGenerationRecord(account: account)", verify_index)
        self.assertLess(read_index, write_index)
        self.assertLess(write_index, verify_index)
        self.assertLess(verify_index, delete_index)
        self.assertIn("Your mailbox data and existing saved sign-in were not removed", source)

    def test_npm_commands_are_registered(self) -> None:
        package = json.loads(self.text("package.json"))
        self.assertEqual(
            package["scripts"]["macos:local-signed:install"],
            "bash scripts/install-macos-local-signed.sh",
        )
        self.assertEqual(
            package["scripts"]["macos:local-signed:validate"],
            "bash scripts/install-macos-local-signed.sh --validate-inputs",
        )
        self.assertIn("macos:local-signed:test", package["scripts"]["verify:all"])


if __name__ == "__main__":
    unittest.main()
