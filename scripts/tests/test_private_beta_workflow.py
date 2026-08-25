from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/private_beta_env.py"
SPEC = importlib.util.spec_from_file_location("private_beta_env", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
private_beta_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(private_beta_env)


class PrivateBetaEnvironmentTests(unittest.TestCase):
    def create_backend(self, root: Path, *, include_oauth: bool = True) -> Path:
        backend = root / "backend"
        backend.mkdir()
        values = [
            "APP_ENV=local",
            "DATABASE_URL=postgresql:///electronic_mail",
            "APP_SESSION_SECRET=replace-me",
            "APP_ENCRYPTION_KEY=replace-me",
            "REGISTRATION_MODE=allowlist",
            "ALLOWED_EMAILS=",
            "GMAIL_SYNC_SCOPE=recent",
            "GOOGLE_REDIRECT_URI=https://old.example/auth/google/callback",
            "MOBILE_REDIRECT_URI=old-app://callback",
            "AI_GROUPING_ENABLED=true",
            "OPENAI_REQUIRED=true",
            "OPENAI_DEBUG_LOGS=true",
            "OPENAI_API_KEY=must-be-removed",
        ]
        if include_oauth:
            values.extend(
                (
                    "GOOGLE_CLIENT_ID=beta-client.apps.googleusercontent.com",
                    "GOOGLE_CLIENT_SECRET=beta-client-secret",
                )
            )
        (backend / ".env").write_text("\n".join(values) + "\n", encoding="utf-8")
        return backend

    def test_prepare_makes_a_valid_safe_local_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.create_backend(Path(directory))
            changed = private_beta_env.prepare_environment(backend, email="Tester@Gmail.com")
            values = private_beta_env.resolved_environment(backend)

            self.assertIn("APP_SESSION_SECRET", changed)
            self.assertIn("APP_ENCRYPTION_KEY", changed)
            self.assertEqual(values["ALLOWED_EMAILS"], "tester@gmail.com")
            self.assertEqual(values["GMAIL_SYNC_SCOPE"], "full")
            self.assertEqual(values["GOOGLE_REDIRECT_URI"], private_beta_env.EXPECTED_REDIRECT_URI)
            self.assertEqual(values["MOBILE_REDIRECT_URI"], private_beta_env.EXPECTED_MOBILE_REDIRECT_URI)
            self.assertGreaterEqual(len(values["APP_SESSION_SECRET"]), 32)
            self.assertGreaterEqual(len(values["APP_ENCRYPTION_KEY"]), 32)
            self.assertNotEqual(values["APP_SESSION_SECRET"], values["APP_ENCRYPTION_KEY"])
            self.assertEqual(values["OPENAI_API_KEY"], "")
            self.assertEqual(private_beta_env.environment_errors(backend), [])
            self.assertEqual((backend / ".env.local").stat().st_mode & 0o777, 0o600)

    def test_prepare_preserves_valid_existing_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.create_backend(Path(directory))
            existing = "a" * 64
            (backend / ".env.local").write_text(
                f"APP_SESSION_SECRET={existing}\nAPP_ENCRYPTION_KEY={'b' * 64}\n",
                encoding="utf-8",
            )
            changed = private_beta_env.prepare_environment(backend, email="tester@gmail.com")
            values = private_beta_env.resolved_environment(backend)

            self.assertNotIn("APP_SESSION_SECRET", changed)
            self.assertNotIn("APP_ENCRYPTION_KEY", changed)
            self.assertEqual(values["APP_SESSION_SECRET"], existing)

    def test_check_reports_missing_oauth_and_allowlist_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.create_backend(Path(directory), include_oauth=False)
            private_beta_env.prepare_environment(backend)
            errors = private_beta_env.environment_errors(backend)

            self.assertIn("ALLOWED_EMAILS must contain the Gmail test account", errors)
            self.assertIn("GOOGLE_CLIENT_ID is missing", errors)
            self.assertIn("GOOGLE_CLIENT_SECRET is missing", errors)

    def test_invalid_email_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.create_backend(Path(directory))
            with self.assertRaisesRegex(ValueError, "valid email"):
                private_beta_env.prepare_environment(backend, email="not-an-email")

    def test_imports_valid_web_client_without_losing_local_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.create_backend(root, include_oauth=False)
            private_beta_env.prepare_environment(backend, email="tester@gmail.com")
            credentials = root / "client.json"
            credentials.write_text(
                json.dumps(
                    {
                        "web": {
                            "project_id": "private-test",
                            "client_id": "new-client.apps.googleusercontent.com",
                            "client_secret": "new-client-secret",
                            "redirect_uris": [private_beta_env.EXPECTED_REDIRECT_URI],
                        }
                    }
                ),
                encoding="utf-8",
            )

            project_id = private_beta_env.import_google_client_credentials(credentials, backend)
            values = private_beta_env.resolved_environment(backend)

            self.assertEqual(project_id, "private-test")
            self.assertEqual(values["GOOGLE_CLIENT_ID"], "new-client.apps.googleusercontent.com")
            self.assertEqual(values["GOOGLE_CLIENT_SECRET"], "new-client-secret")
            self.assertEqual(values["ALLOWED_EMAILS"], "tester@gmail.com")
            self.assertEqual((backend / ".env.local").stat().st_mode & 0o777, 0o600)

    def test_rejects_google_client_with_wrong_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.create_backend(root, include_oauth=False)
            credentials = root / "client.json"
            credentials.write_text(
                json.dumps(
                    {
                        "web": {
                            "client_id": "new-client.apps.googleusercontent.com",
                            "client_secret": "new-client-secret",
                            "redirect_uris": ["https://wrong.example/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "localhost:3001"):
                private_beta_env.import_google_client_credentials(credentials, backend)


class PrivateBetaContractTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_package_exposes_the_three_beta_commands(self) -> None:
        package = self.text("package.json")
        self.assertIn('"beta:setup": "bash scripts/setup-private-beta.sh"', package)
        self.assertIn('"beta:check": "bash scripts/check-private-beta.sh"', package)
        self.assertIn('"beta:start": "bash scripts/start-private-beta.sh"', package)

    def test_guide_pins_the_oauth_contract_and_secret_boundary(self) -> None:
        guide = self.text("docs/PRIVATE_BETA.md")
        for fragment in (
            "http://localhost:3001/auth/google/callback",
            "https://mail.google.com/",
            "External",
            "Testing",
            "Web application",
            "Each tester must use a separate Google Cloud project",
            "Never paste these values into GitHub",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, guide)

    def test_start_fails_closed_through_the_checker(self) -> None:
        start = self.text("scripts/start-private-beta.sh")
        self.assertIn("activate_private_beta_toolchain check", start)
        self.assertIn('bash "$ROOT_DIR/scripts/check-private-beta.sh"', start)
        self.assertIn('exec bash "$ROOT_DIR/scripts/dev-macos.sh"', start)

    def test_setup_activates_the_pinned_toolchain_before_bootstrap(self) -> None:
        setup = self.text("scripts/setup-private-beta.sh")
        toolchain = self.text("scripts/private-beta-toolchain.sh")
        self.assertLess(
            setup.index("activate_private_beta_toolchain install"),
            setup.index('bash "$ROOT_DIR/scripts/bootstrap.sh"'),
        )
        self.assertIn('nvm install "$required_node"', toolchain)
        self.assertIn('uv python install "$required_python"', toolchain)


if __name__ == "__main__":
    unittest.main()
