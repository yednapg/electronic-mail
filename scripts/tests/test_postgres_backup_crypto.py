from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "postgres-backup-crypto.py"
SPEC = importlib.util.spec_from_file_location("postgres_backup_crypto", SCRIPT)
assert SPEC and SPEC.loader
backup_crypto = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup_crypto
SPEC.loader.exec_module(backup_crypto)


def lock_record(name: str, version: str, digest: str = "a" * 64) -> str:
    return f"{name}=={version} \\\n    --hash=sha256:{digest}\n"


class BackupCryptoLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.lock = Path(self.temporary.name) / "requirements.lock"
        self.original_lock = backup_crypto.REQUIREMENTS_LOCK
        backup_crypto.REQUIREMENTS_LOCK = self.lock

    def tearDown(self) -> None:
        backup_crypto.REQUIREMENTS_LOCK = self.original_lock
        self.temporary.cleanup()

    def test_repository_multiline_hashed_lock_has_reviewed_pin(self) -> None:
        backup_crypto.REQUIREMENTS_LOCK = SCRIPT.parent.parent / "backend/requirements.lock"
        self.assertEqual(backup_crypto._expected_cryptography_version(), "48.0.1")

    def test_multiline_hashed_pin_is_parsed_without_continuation_marker(self) -> None:
        self.lock.write_text(
            "# reviewed lock\n"
            + lock_record("example", "1.2.3", "b" * 64)
            + lock_record("Cryptography", "48.0.1"),
            encoding="utf-8",
        )
        self.assertEqual(backup_crypto._expected_cryptography_version(), "48.0.1")

    def test_lock_rejects_missing_malformed_duplicate_or_unfinished_hash_records(self) -> None:
        malformed_locks = (
            "cryptography==48.0.1\n",
            "cryptography==48.0.1 --hash=sha256:abcd\n",
            lock_record("cryptography", "48.0.1") + lock_record("Cryptography", "48.0.1", "b" * 64),
            "cryptography==48.0.1 \\\n",
        )
        for contents in malformed_locks:
            with self.subTest(contents=contents):
                self.lock.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(
                    backup_crypto.BackupCryptoError,
                    "must contain one exact cryptography pin",
                ):
                    backup_crypto._expected_cryptography_version()

    def test_runtime_still_must_match_the_exact_locked_version(self) -> None:
        self.lock.write_text(lock_record("cryptography", "48.0.1"), encoding="utf-8")
        with (
            patch.object(backup_crypto, "package_version", return_value="49.0.0"),
            self.assertRaisesRegex(
                backup_crypto.BackupCryptoError,
                r"cryptography must match backend/requirements\.lock \(48\.0\.1\); found 49\.0\.0",
            ),
        ):
            backup_crypto._verify_runtime()


if __name__ == "__main__":
    unittest.main()
