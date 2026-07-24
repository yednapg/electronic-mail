#!/usr/bin/env python3
"""Stream authenticated encryption for Electronic Mail PostgreSQL dumps."""

from __future__ import annotations

import argparse
import hashlib
import hmac
from importlib.metadata import PackageNotFoundError, version as package_version
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_LOCK = ROOT / "backend/requirements.lock"
MAGIC = b"ELECTRONIC-MAIL-PG-BACKUP\x00\x01"
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOCK_PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[0-9][0-9A-Za-z.!+_-]*)$"
)
LOCK_HASH_PATTERN = re.compile(r"^--hash=sha256:(?P<digest>[0-9a-f]{64})$")


class BackupCryptoError(RuntimeError):
    pass


def _hashed_lock_pins(path: Path) -> dict[str, str]:
    error_message = "backend/requirements.lock must contain one exact cryptography pin"
    logical_requirements: list[str] = []
    continuation: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if continuation:
                raise BackupCryptoError(error_message)
            continue
        continued = line.endswith("\\")
        token_text = line[:-1].rstrip() if continued else line
        if not token_text:
            raise BackupCryptoError(error_message)
        continuation.append(token_text)
        if not continued:
            logical_requirements.append(" ".join(continuation))
            continuation = []
    if continuation:
        raise BackupCryptoError(error_message)

    pins: dict[str, str] = {}
    for requirement in logical_requirements:
        tokens = requirement.split()
        pin_match = LOCK_PIN_PATTERN.fullmatch(tokens[0]) if tokens else None
        if pin_match is None or len(tokens) < 2:
            raise BackupCryptoError(error_message)
        hashes: set[str] = set()
        for token in tokens[1:]:
            hash_match = LOCK_HASH_PATTERN.fullmatch(token)
            if hash_match is None or hash_match.group("digest") in hashes:
                raise BackupCryptoError(error_message)
            hashes.add(hash_match.group("digest"))
        name = re.sub(r"[-_.]+", "-", pin_match.group("name").lower())
        if name in pins:
            raise BackupCryptoError(error_message)
        pins[name] = pin_match.group("version")
    return pins


def _expected_cryptography_version() -> str:
    version = _hashed_lock_pins(REQUIREMENTS_LOCK).get("cryptography")
    if version is None or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise BackupCryptoError("backend/requirements.lock must contain one exact cryptography pin")
    return version


def _verify_runtime() -> None:
    expected = _expected_cryptography_version()
    try:
        actual = package_version("cryptography")
    except PackageNotFoundError as error:
        raise BackupCryptoError("the pinned cryptography package is not installed") from error
    if actual != expected:
        raise BackupCryptoError(
            f"cryptography must match backend/requirements.lock ({expected}); found {actual}"
        )


def _read_key(path: Path) -> bytes:
    try:
        metadata = path.stat()
    except OSError as error:
        raise BackupCryptoError(f"cannot read encryption key file: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise BackupCryptoError("encryption key path must be a regular file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BackupCryptoError("encryption key file permissions must be 0600 or stricter")
    try:
        value = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        raise BackupCryptoError(f"cannot read encryption key file: {error}") from error
    if value.endswith("\n"):
        value = value[:-1]
    if KEY_PATTERN.fullmatch(value) is None:
        raise BackupCryptoError("BACKUP_ENCRYPTION_KEY must be exactly 64 lowercase hexadecimal characters")
    return bytes.fromhex(value)


def _read_environment_key() -> bytes:
    value = os.environ.pop("BACKUP_ENCRYPTION_KEY", "")
    if KEY_PATTERN.fullmatch(value) is None:
        raise BackupCryptoError("BACKUP_ENCRYPTION_KEY must be exactly 64 lowercase hexadecimal characters")
    return bytes.fromhex(value)


def _exclusive_output(path: Path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, "wb")


def _open_regular_input(path: Path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BackupCryptoError(f"cannot open encrypted backup: {error}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackupCryptoError("input must be a regular, non-symbolic-link file")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _validate_paths(input_path: Path, output_path: Path) -> None:
    if not input_path.is_file() or input_path.is_symlink():
        raise BackupCryptoError("input must be a regular, non-symbolic-link file")
    if input_path.resolve() == output_path.resolve():
        raise BackupCryptoError("input and output paths must differ")
    if output_path.exists() or output_path.is_symlink():
        raise BackupCryptoError("output already exists")


def _encrypt_stream(source, output_path: Path, key: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    nonce = os.urandom(NONCE_BYTES)
    header = MAGIC + nonce
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    try:
        with _exclusive_output(output_path) as destination:
            destination.write(header)
            while chunk := source.read(CHUNK_BYTES):
                destination.write(encryptor.update(chunk))
            destination.write(encryptor.finalize())
            destination.write(encryptor.tag)
            destination.flush()
            os.fsync(destination.fileno())
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def encrypt(input_path: Path, output_path: Path, key: bytes) -> None:
    _validate_paths(input_path, output_path)
    with input_path.open("rb") as source:
        _encrypt_stream(source, output_path, key)


def encrypt_stdin(output_path: Path, key: bytes) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise BackupCryptoError("output already exists")
    _encrypt_stream(sys.stdin.buffer, output_path, key)


def _encrypted_layout(source, *, size: int) -> tuple[bytes, bytes, bytes, int]:
    minimum_size = len(MAGIC) + NONCE_BYTES + TAG_BYTES
    if size < minimum_size:
        raise BackupCryptoError("encrypted backup is truncated")
    source.seek(0)
    magic = source.read(len(MAGIC))
    if magic != MAGIC:
        raise BackupCryptoError("encrypted backup has an unsupported or invalid format")
    nonce = source.read(NONCE_BYTES)
    header = magic + nonce
    ciphertext_bytes = size - len(header) - TAG_BYTES
    source.seek(size - TAG_BYTES)
    tag = source.read(TAG_BYTES)
    return header, nonce, tag, ciphertext_bytes


def _decrypt_pass(source, *, header: bytes, nonce: bytes, tag: bytes, ciphertext_bytes: int, key: bytes, destination) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(header)
    source.seek(len(header))
    remaining = ciphertext_bytes
    try:
        while remaining:
            chunk = source.read(min(CHUNK_BYTES, remaining))
            if not chunk:
                raise BackupCryptoError("encrypted backup is truncated")
            remaining -= len(chunk)
            plaintext = decryptor.update(chunk)
            if destination is not None:
                destination.write(plaintext)
        final_plaintext = decryptor.finalize()
        if destination is not None:
            destination.write(final_plaintext)
    except InvalidTag as error:
        raise BackupCryptoError("encrypted backup authentication failed") from error


def decrypt(input_path: Path, output_path: Path, key: bytes) -> None:
    _validate_paths(input_path, output_path)
    size = input_path.stat().st_size
    with input_path.open("rb") as source:
        header, nonce, tag, ciphertext_bytes = _encrypted_layout(source, size=size)
        try:
            with _exclusive_output(output_path) as destination:
                _decrypt_pass(
                    source,
                    header=header,
                    nonce=nonce,
                    tag=tag,
                    ciphertext_bytes=ciphertext_bytes,
                    key=key,
                    destination=destination,
                )
                destination.flush()
                os.fsync(destination.fileno())
        except Exception:
            output_path.unlink(missing_ok=True)
            raise


def decrypt_stdout(input_path: Path, key: bytes, expected_sha256: str | None) -> None:
    if expected_sha256 is not None and SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise BackupCryptoError("Backup checksum verification failed")
    with _open_regular_input(input_path) as source, tempfile.TemporaryFile(mode="w+b") as snapshot:
        size = 0
        digest = hashlib.sha256()
        while chunk := source.read(CHUNK_BYTES):
            snapshot.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        snapshot.flush()
        if expected_sha256 is not None and not hmac.compare_digest(
            digest.hexdigest(), expected_sha256
        ):
            raise BackupCryptoError("Backup checksum verification failed")
        header, nonce, tag, ciphertext_bytes = _encrypted_layout(snapshot, size=size)
        # Authenticate the entire archive before pg_restore receives any byte.
        _decrypt_pass(
            snapshot,
            header=header,
            nonce=nonce,
            tag=tag,
            ciphertext_bytes=ciphertext_bytes,
            key=key,
            destination=None,
        )
        _decrypt_pass(
            snapshot,
            header=header,
            nonce=nonce,
            tag=tag,
            ciphertext_bytes=ciphertext_bytes,
            key=key,
            destination=sys.stdout.buffer,
        )
        sys.stdout.buffer.flush()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate-key", "encrypt", "decrypt", "encrypt-stdin", "decrypt-stdout"):
        child = subparsers.add_parser(command)
        key_source = child.add_mutually_exclusive_group(required=True)
        key_source.add_argument("--key-file", type=Path)
        key_source.add_argument("--key-env", action="store_true")
        if command in {"encrypt", "decrypt"}:
            child.add_argument("--input", required=True, type=Path)
            child.add_argument("--output", required=True, type=Path)
        elif command == "encrypt-stdin":
            child.add_argument("--output", required=True, type=Path)
        elif command == "decrypt-stdout":
            child.add_argument("--input", required=True, type=Path)
            child.add_argument("--expected-sha256")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        _verify_runtime()
        key = _read_environment_key() if arguments.key_env else _read_key(arguments.key_file)
        if arguments.command == "encrypt":
            encrypt(arguments.input, arguments.output, key)
        elif arguments.command == "decrypt":
            decrypt(arguments.input, arguments.output, key)
        elif arguments.command == "encrypt-stdin":
            encrypt_stdin(arguments.output, key)
        elif arguments.command == "decrypt-stdout":
            decrypt_stdout(arguments.input, key, arguments.expected_sha256)
    except (BackupCryptoError, ImportError, OSError, ValueError) as error:
        print(f"backup encryption error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
