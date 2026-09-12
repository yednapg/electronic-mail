from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
BETA_SCRIPT = ROOT / "scripts/package-macos-beta.sh"


class MacOSBetaPolicyTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def run_validation(self, **values: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "VERSION",
            "BUILD_NUMBER",
            "BACKEND_URL",
            "CURRENT_YEAR",
            "OUTPUT_DIR",
            "WORK_DIR",
            "DEVELOPER_DIR",
        ):
            environment.pop(name, None)
        environment.update(values)
        return subprocess.run(
            ["bash", str(BETA_SCRIPT), "--validate-inputs"],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_beta_info_plist_has_only_the_exact_localhost_exception(self) -> None:
        with (ROOT / "macos/ElectronicMail/Config/InfoPlists/ElectronicMail-Beta-Info.plist").open("rb") as handle:
            info = plistlib.load(handle)

        self.assertEqual(info["BackendBaseURL"], "$(ELECTRONIC_MAIL_BACKEND_URL)")
        self.assertEqual(info["ElectronicMailSourceCommit"], "$(ELECTRONIC_MAIL_SOURCE_COMMIT)")
        self.assertEqual(info["ElectronicMailDistributionChannel"], "local-testing-beta")
        self.assertIs(info["ElectronicMailNotarized"], False)
        self.assertEqual(info["CFBundleDisplayName"], "Electronic Mail Beta")
        self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleURLName"], "app.electronicmail.mac.beta")
        self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["electronicmail"])
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

    def test_beta_entitlements_add_only_the_local_library_validation_exception(self) -> None:
        with (ROOT / "macos/ElectronicMail/ElectronicMail/Mac/ElectronicMail.entitlements").open("rb") as handle:
            production = plistlib.load(handle)
        with (ROOT / "macos/ElectronicMail/Config/Entitlements/ElectronicMail-Beta.entitlements").open("rb") as handle:
            beta = plistlib.load(handle)

        expected_production = {
            "com.apple.security.app-sandbox": True,
            "com.apple.security.files.user-selected.read-write": True,
            "com.apple.security.network.client": True,
        }
        self.assertEqual(production, expected_production | {
            "com.apple.developer.aps-environment": "$(ELECTRONIC_MAIL_APNS_ENVIRONMENT)"
        })
        self.assertEqual(
            beta,
            expected_production | {"com.apple.security.cs.disable-library-validation": True},
        )

    def test_default_and_canonical_public_inputs_validate_without_building(self) -> None:
        default = self.run_validation()
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertIn("backend http://localhost:3001", default.stdout)

        public = self.run_validation(
            VERSION="2.4.1",
            BUILD_NUMBER="57",
            BACKEND_URL="https://api.electronicmail.app",
            CURRENT_YEAR="2026",
        )
        self.assertEqual(public.returncode, 0, public.stderr)
        self.assertIn("version 2.4.1 (57)", public.stdout)

    def test_noncanonical_or_nonpublic_backend_inputs_fail_closed(self) -> None:
        invalid_values = (
            "http://localhost:3002",
            "http://api.electronicmail.app",
            "https://localhost",
            "https://127.0.0.1",
            "https://10.0.0.2",
            "https://api.electronicmail.app/",
            "https://api.electronicmail.app/path",
            "https://api.electronicmail.app?query=true",
            "https://api.electronicmail.app#fragment",
            "https://user:password@api.electronicmail.app",
            "https://api.electronicmail.app:443",
            "https://API.electronicmail.app",
            "https://api.example.com",
            " https://api.electronicmail.app",
        )
        for backend_url in invalid_values:
            with self.subTest(backend_url=backend_url):
                result = self.run_validation(BACKEND_URL=backend_url)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_invalid_version_build_and_extra_arguments_fail_closed(self) -> None:
        for values in (
            {"VERSION": "1.0-beta"},
            {"VERSION": "1"},
            {"BUILD_NUMBER": "0"},
            {"BUILD_NUMBER": "01"},
            {"CURRENT_YEAR": "26"},
        ):
            with self.subTest(values=values):
                result = self.run_validation(**values)
                self.assertNotEqual(result.returncode, 0, result.stdout)

        extra = subprocess.run(
            ["bash", str(BETA_SCRIPT), "--validate-inputs", "unexpected"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(extra.returncode, 0)

    def test_beta_pipeline_is_release_universal_adhoc_and_self_verifying(self) -> None:
        script = self.text("scripts/package-macos-beta.sh")
        verifier = self.text("scripts/verify-macos-release.sh")
        required_fragments = (
            '-configuration Release',
            'ARCHS="arm64 x86_64"',
            'ONLY_ACTIVE_ARCH=NO',
            'CODE_SIGNING_ALLOWED=NO',
            'PRODUCT_BUNDLE_IDENTIFIER = app.electronicmail.mac.beta;',
            "ELECTRONIC_MAIL_LOCAL_BETA",
            'codesign --force --sign - --options runtime "$FRAMEWORK_PATH"',
            'codesign --force --sign - --options runtime --entitlements "$BETA_ENTITLEMENTS_PATH" "$APP_PATH"',
            '"$APP_PATH/Contents/MacOS/Electronic Mail" --electronic-mail-beta-launch-smoke',
            '"$DMG_MOUNT_POINT/Electronic Mail Beta.app/Contents/MacOS/Electronic Mail" --electronic-mail-beta-launch-smoke',
            'INFO_POLICY=local-beta',
            'REQUIRE_ADHOC_SIGNATURE=1',
            'REQUIRE_NOTARIZATION=0',
            'hdiutil create',
            'hdiutil verify "$DMG_PATH"',
            'hdiutil attach "$DMG_PATH" -nobrowse -readonly',
            'ln -s /Applications "$DMG_ROOT/Applications"',
            'README-BETA.txt',
            'verify_macos_dmg_layout.py',
            'shasum -a 256 -c "$CHECKSUM_NAME"',
            'github-prerelease-testing',
            'gatekeeper_acceptance_claimed',
            '"hardened_runtime": True',
            '"library_validation": False',
            '"performed": False',
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, script)

        self.assertNotIn(
            "\n  PRODUCT_BUNDLE_IDENTIFIER=app.electronicmail.mac.beta \\\n",
            script,
        )
        self.assertIn(
            '[ "$FRAMEWORK_BUNDLE_ID" = "app.electronicmail.core" ]',
            verifier,
        )

        self.assertIn('status --porcelain --untracked-files=all', script)
        self.assertIn('SOURCE_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD)"', script)
        self.assertIn("the local beta source tree must be clean and committed", script)
        self.assertIn('archive --format=tar "$SOURCE_COMMIT" -- macos/ElectronicMail', script)
        self.assertNotIn('ditto "$ROOT_DIR/macos/ElectronicMail"', script)
        self.assertIn("ElectronicMail-Beta-Info.plist", script)
        self.assertIn("ElectronicMail-Beta.entitlements", script)
        self.assertNotIn("DEVELOPER_ID_APPLICATION", script)
        self.assertNotIn("APPLE_DEVELOPMENT_TEAM", script)
        self.assertNotIn("NOTARY_PROFILE", script)
        self.assertNotIn("notarytool", script)
        self.assertNotIn("spctl", script)
        self.assertNotIn("verify-macos-toolchain.sh", script)
        self.assertIn("local beta code must carry an ad-hoc signature", verifier)
        self.assertIn("local beta code must enable Hardened Runtime", verifier)
        self.assertIn('SOURCE_BETA_ENTITLEMENTS=', verifier)
        self.assertIn('"com.apple.security.cs.disable-library-validation": True', verifier)

    def test_beta_build_flag_cannot_leak_into_production_release(self) -> None:
        package_script = self.text("scripts/package-macos-beta.sh")
        production_release = self.text("scripts/release-macos.sh")
        production_preflight = self.text("scripts/preflight-macos-release.sh")
        project = self.text("macos/ElectronicMail/Project.swift")
        generated_project = self.text("macos/ElectronicMail/ElectronicMail.xcodeproj/project.pbxproj")
        mac_app = self.text("macos/ElectronicMail/ElectronicMail/Mac/ElectronicMailApp.swift")

        self.assertIn("ELECTRONIC_MAIL_LOCAL_BETA", package_script)
        for text in (production_release, production_preflight, project, generated_project):
            self.assertNotIn("ELECTRONIC_MAIL_LOCAL_BETA", text)
            self.assertNotIn("ElectronicMail-Beta-Info.plist", text)
            self.assertNotIn("ElectronicMail-Beta.entitlements", text)
            self.assertNotIn("--electronic-mail-beta-launch-smoke", text)

        self.assertIn("#if ELECTRONIC_MAIL_LOCAL_BETA", mac_app)
        self.assertEqual(mac_app.count("--electronic-mail-beta-launch-smoke"), 1)
        self.assertIn("Darwin.exit(EXIT_SUCCESS)", mac_app)
        self.assertEqual(package_script.count("--electronic-mail-beta-launch-smoke"), 2)

        self.assertIn('[ -n "$DEVELOPER_ID_APPLICATION" ] || fail', production_release)
        self.assertIn('[ -n "$NOTARY_PROFILE" ] || fail', production_release)
        self.assertIn('INFO_POLICY="${INFO_POLICY:-production}"', self.text("scripts/verify-macos-release.sh"))
        self.assertGreaterEqual(production_release.count("INFO_POLICY=production"), 4)
        self.assertIn("INFO_POLICY=production", production_preflight)
        self.assertIn("INFO_POLICY=production", self.text("scripts/verify-launch.sh"))
        self.assertIn("SWIFT_ACTIVE_COMPILATION_CONDITIONS=", production_release)
        self.assertIn("SWIFT_ACTIVE_COMPILATION_CONDITIONS=", production_preflight)
        self.assertIn('"com.apple.security.cs.disable-library-validation",', self.text("scripts/verify-macos-release.sh"))

    def test_beta_keychain_fallback_is_compiled_only_for_debug_or_explicit_beta(self) -> None:
        token_store = self.text("macos/ElectronicMail/ElectronicMail/Core/SessionTokenStore.swift")
        recovery_store = self.text("macos/ElectronicMail/ElectronicMail/Core/SignedInShellView.swift")
        condition = "#if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)"
        self.assertGreaterEqual(token_store.count(condition), 4)
        self.assertEqual(recovery_store.count(condition), 2)
        self.assertIn("production Release builds never compile this fallback", token_store)
        self.assertIn("Production Release builds", recovery_store)

    def test_npm_and_handoff_contracts_are_present(self) -> None:
        package = json.loads(self.text("package.json"))
        self.assertEqual(package["scripts"]["release:macos:beta"], "bash scripts/package-macos-beta.sh")
        self.assertEqual(
            package["scripts"]["release:macos:beta:validate"],
            "bash scripts/package-macos-beta.sh --validate-inputs",
        )
        self.assertEqual(
            package["scripts"]["release:macos:beta:test"],
            "python3 -m unittest scripts/tests/test_macos_beta_policy.py scripts/tests/test_macos_distribution_safety.py scripts/tests/test_verify_macos_dmg_layout.py scripts/tests/test_publish_macos_beta.py",
        )
        self.assertIn("npm run release:macos:beta:test", self.text(".github/workflows/quality.yml"))
        handoff = self.text("docs/MACOS_BETA.md")
        for marker in (
            "unnotarized",
            "GitHub prerelease",
            "http://localhost:3001",
            "BACKEND_URL=https://",
            "release:macos:beta",
            "Gatekeeper",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, handoff)


if __name__ == "__main__":
    unittest.main()
