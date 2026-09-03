from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/install-macos-local-self-signed.sh"


class MacOSLocalSelfSignedPolicyTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def run_validation(self, **values: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "VERSION",
            "BUILD_NUMBER",
            "BACKEND_URL",
            "CURRENT_YEAR",
            "LOCAL_CODE_SIGN_IDENTITY",
        ):
            environment.pop(name, None)
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

    def test_info_preserves_production_identity_and_marks_local_channel(self) -> None:
        path = ROOT / "macos/ElectronicMail/Config/InfoPlists/ElectronicMail-LocalSelfSigned-Info.plist"
        with path.open("rb") as handle:
            info = plistlib.load(handle)

        self.assertEqual(info["CFBundleDisplayName"], "Electronic Mail")
        self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleURLName"], "app.electronicmail.mac")
        self.assertEqual(info["ElectronicMailDistributionChannel"], "local-self-signed")
        self.assertEqual(
            info["ElectronicMailLocalSigningIdentitySHA1"],
            "$(ELECTRONIC_MAIL_LOCAL_SIGNING_IDENTITY_SHA1)",
        )
        self.assertIs(info["ElectronicMailNotarized"], False)

    def test_inputs_default_to_named_local_identity_and_reject_distribution_inputs(self) -> None:
        valid = self.run_validation(VERSION="2.4.1", BUILD_NUMBER="57", CURRENT_YEAR="2026")
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("identity Electronic Mail Local Signing", valid.stdout)

        for values in (
            {"BACKEND_URL": "http://localhost:3002"},
            {"BACKEND_URL": "https://api.electronicmail.app"},
            {"LOCAL_CODE_SIGN_IDENTITY": "-"},
            {"LOCAL_CODE_SIGN_IDENTITY": "Developer ID Application: Example Person (ABCDEFGHIJ)"},
            {"BUILD_NUMBER": "0"},
        ):
            with self.subTest(values=values):
                result = self.run_validation(**values)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_installer_uses_stable_identity_classic_keychain_and_safe_replacement(self) -> None:
        script = self.text("scripts/install-macos-local-self-signed.sh")
        for fragment in (
            'INSTALL_PATH="/Applications/Electronic Mail.app"',
            "security find-identity -v -p codesigning",
            "Electronic Mail Local Signing",
            "ELECTRONIC_MAIL_LOCAL_BETA",
            "ElectronicMail-LocalSelfSigned.entitlements",
            'codesign --force --sign "$IDENTITY_SHA1"',
            "local-self-signed",
            "ElectronicMailLocalSigningIdentitySHA1",
            'mv "$BACKUP_PATH" "$INSTALL_PATH"',
            "the previous app was restored",
            "local-working-tree",
            "designated requirement changed",
            "sed -n '/^designated =>/p'",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, script)

        self.assertNotIn("codesign --force --sign -", script)
        self.assertNotIn("DEVELOPMENT_TEAM", script)
        self.assertNotIn("kSecUseDataProtectionKeychain", script)

        entitlements_path = (
            ROOT
            / "macos/ElectronicMail/Config/Entitlements/ElectronicMail-LocalSelfSigned.entitlements"
        )
        with entitlements_path.open("rb") as handle:
            entitlements = plistlib.load(handle)
        self.assertEqual(
            entitlements,
            {"com.apple.security.cs.disable-library-validation": True},
        )
        self.assertNotIn("com.apple.security.app-sandbox", entitlements)

    def test_local_policy_is_classic_non_synchronizing_keychain(self) -> None:
        source = self.text("macos/ElectronicMail/ElectronicMail/Core/SessionTokenStore.swift")
        self.assertIn("#if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)", source)
        self.assertIn("kSecAttrSynchronizable as String: false", source)
        local_policy_start = source.index("static var classicMacLocalTestingPolicyAttributes")
        local_policy_end = source.index("static var classicMacFallbackEnabled", local_policy_start)
        self.assertNotIn("kSecUseDataProtectionKeychain", source[local_policy_start:local_policy_end])

    def test_npm_commands_are_registered(self) -> None:
        package = json.loads(self.text("package.json"))
        self.assertEqual(
            package["scripts"]["macos:local-self-signed:install"],
            "bash scripts/install-macos-local-self-signed.sh",
        )
        self.assertEqual(
            package["scripts"]["macos:local-self-signed:validate"],
            "bash scripts/install-macos-local-self-signed.sh --validate-inputs",
        )
        self.assertEqual(
            package["scripts"]["macos:local-self-signed:test"],
            "python3 -m unittest scripts/tests/test_macos_local_self_signed_policy.py",
        )
        self.assertIn("macos:local-self-signed:test", package["scripts"]["verify:all"])


if __name__ == "__main__":
    unittest.main()
