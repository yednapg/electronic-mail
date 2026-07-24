from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MacOSDistributionSafetyTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def run_release_validation(self, backend_url: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "VERSION",
            "BUILD_NUMBER",
            "BACKEND_URL",
            "CURRENT_YEAR",
            "DEVELOPER_ID_APPLICATION",
            "APPLE_DEVELOPMENT_TEAM",
            "NOTARY_PROFILE",
            "SKIP_NOTARIZATION",
        ):
            environment.pop(name, None)
        environment["BACKEND_URL"] = backend_url
        return subprocess.run(
            ["bash", str(ROOT / "scripts/release-macos.sh"), "--validate-inputs"],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_production_backend_origin_must_be_canonical(self) -> None:
        for value in (
            "https://api.electronicmail.app",
            "https://8.8.8.8",
            "https://[2606:4700:4700::1111]",
        ):
            with self.subTest(valid=value):
                valid = self.run_release_validation(value)
                self.assertEqual(valid.returncode, 0, valid.stderr)

        for value in (
            "https://api.electronicmail.app/",
            "https://API.electronicmail.app",
            "https://api.electronicmail.app.",
            "https://api.electronicmail.app:443",
            "https://api.electronicmail.app/path",
            "https://api.electronicmail.app?channel=production",
            "https://user@api.electronicmail.app",
            "https://127.0.0.1",
            "https://api.example.com",
            "https://bad_label.electronicmail.app",
            "https://-api.electronicmail.app",
            "https://api-.electronicmail.app",
            "https://api..electronicmail.app",
            "https://électronicmail.app",
            "https://999.999.999.999",
            "https://8.8.08.8",
            "https://[2606:4700:4700:0:0:0:0:1111]",
            f"https://{'a' * 64}.electronicmail.app",
            f"https://{'a.' * 126}aa",
        ):
            with self.subTest(value=value):
                result = self.run_release_validation(value)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_beta_install_identity_is_separate_but_oauth_scheme_is_shared(self) -> None:
        with (ROOT / "macos/ElectronicMail/Config/InfoPlists/ElectronicMail-Beta-Info.plist").open("rb") as handle:
            beta_info = plistlib.load(handle)
        with (ROOT / "macos/ElectronicMail/Config/InfoPlists/ElectronicMail-Release-Info.plist").open("rb") as handle:
            production_info = plistlib.load(handle)

        self.assertEqual(beta_info["CFBundleIdentifier"], "$(PRODUCT_BUNDLE_IDENTIFIER)")
        self.assertEqual(beta_info["CFBundleURLTypes"][0]["CFBundleURLName"], "app.electronicmail.mac.beta")
        self.assertEqual(production_info["CFBundleURLTypes"][0]["CFBundleURLName"], "app.electronicmail.mac")
        self.assertEqual(beta_info["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["electronicmail"])
        self.assertEqual(production_info["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["electronicmail"])

        beta_script = self.text("scripts/package-macos-beta.sh")
        self.assertIn("PRODUCT_BUNDLE_IDENTIFIER = app.electronicmail.mac.beta;", beta_script)
        self.assertIn('DMG_APP_PATH="$DMG_ROOT/Electronic Mail Beta.app"', beta_script)
        self.assertIn('"bundle_identifier": "app.electronicmail.mac.beta"', beta_script)
        self.assertIn('"coinstallation_supported_during_sign_in": False', beta_script)
        self.assertIn("OAUTH COINSTALLATION LIMITATION", beta_script)
        self.assertIn("remove every production ElectronicMail.app", beta_script)
        self.assertIn("**OAuth co-installation limitation:**", beta_script)
        self.assertIn("Remove every production `ElectronicMail.app` copy", beta_script)
        self.assertNotIn('DMG_APP_PATH="$DMG_ROOT/ElectronicMail.app"', beta_script)

        beta_guide = self.text("docs/MACOS_BETA.md")
        self.assertIn("does not isolate OAuth scheme routing", beta_guide)
        self.assertIn("remove every production `ElectronicMail.app` copy", beta_guide)

        verifier = self.text("scripts/verify-macos-release.sh")
        self.assertIn("EXPECTED_BUNDLE_ID=app.electronicmail.mac", verifier)
        self.assertIn("EXPECTED_BUNDLE_ID=app.electronicmail.mac.beta", verifier)

    def test_every_dmg_path_uses_the_exact_layout_verifier(self) -> None:
        for relative in (
            "scripts/release-macos.sh",
            "scripts/package-macos-beta.sh",
            "scripts/verify-launch.sh",
        ):
            with self.subTest(relative=relative):
                self.assertIn("verify_macos_dmg_layout.py", self.text(relative))

        launch = self.text("scripts/verify-launch.sh")
        self.assertIn('fail "DMG secure signing timestamp is missing"', launch)

    def test_public_launch_checks_the_rendered_status_service(self) -> None:
        verifier = self.text("scripts/verify-public-pages.sh")
        self.assertIn("Electronic Mail service status page", verifier)
        self.assertIn("public status page is unavailable over HTTPS", verifier)
        self.assertIn("--proto '=https' --proto-redir '=https'", verifier)


if __name__ == "__main__":
    unittest.main()
