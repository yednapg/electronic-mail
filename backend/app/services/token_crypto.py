from __future__ import annotations

"""Shared encryption helpers for persisted user secrets."""

import base64
import hashlib
import json

from cryptography.fernet import Fernet

from app.core.config import Settings


def encrypt_json(settings: Settings, payload: dict[str, object]) -> str:
    """Encrypt a JSON payload using app encryption key material."""
    return _fernet(settings).encrypt(json.dumps(payload, ensure_ascii=True).encode("utf-8")).decode("utf-8")


def decrypt_json(settings: Settings, encrypted: str) -> dict[str, object]:
    """Decrypt a JSON payload."""
    decoded = _fernet(settings).decrypt(encrypted.encode("utf-8")).decode("utf-8")
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("Encrypted payload is not a JSON object")
    return payload


def _fernet(settings: Settings) -> Fernet:
    key = settings.app_encryption_key.strip()
    try:
        return Fernet(key.encode("utf-8"))
    except Exception:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))
