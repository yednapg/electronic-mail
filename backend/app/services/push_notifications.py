from __future__ import annotations

"""APNs delivery for new mail. Payloads and provider errors never enter logs."""

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
import httpx


@dataclass(frozen=True)
class PushConfiguration:
    enabled: bool = False
    team_id: str = ""
    key_id: str = ""
    key_path: str = ""
    mac_topic: str = "app.electronicmail.mac"
    ios_topic: str = "app.electronicmail.ios"

    @classmethod
    def load(cls) -> PushConfiguration:
        return cls(
            enabled=os.getenv("PUSH_NOTIFICATIONS_ENABLED", "false").lower() == "true",
            team_id=os.getenv("APNS_TEAM_ID", ""),
            key_id=os.getenv("APNS_KEY_ID", ""),
            key_path=os.getenv("APNS_PRIVATE_KEY_PATH", ""),
            mac_topic=os.getenv("APNS_MAC_TOPIC", "app.electronicmail.mac"),
            ios_topic=os.getenv("APNS_IOS_TOPIC", "app.electronicmail.ios"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.team_id and self.key_id and self.key_path)

    def readiness_errors(self) -> list[str]:
        if not self.enabled:
            return []
        if not self.configured:
            return ["APNS_TEAM_ID, APNS_KEY_ID and APNS_PRIVATE_KEY_PATH are required when PUSH_NOTIFICATIONS_ENABLED=true"]
        try:
            key = serialization.load_pem_private_key(Path(self.key_path).read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
                return ["APNS_PRIVATE_KEY_PATH must contain an ES256 Apple push key"]
        except (OSError, ValueError, TypeError):
            return ["APNS_PRIVATE_KEY_PATH must be a readable Apple push private key"]
        return []


def eligible_new_mail(message, *, now: datetime | None = None) -> bool:
    labels = set(message.label_ids)
    if not {"INBOX", "UNREAD"}.issubset(labels) or labels & {"SENT", "DRAFT", "SPAM", "TRASH"}:
        return False
    try:
        received = datetime.fromisoformat(message.internal_date.replace("Z", "+00:00"))
        if received.tzinfo is None:
            return False
        age = ((now or datetime.now(timezone.utc)) - received).total_seconds()
        return -60 <= age <= 900 and bool(message.gmail_thread_id)
    except (AttributeError, TypeError, ValueError):
        return False


def notification_payload(delivery: dict) -> dict:
    alert = {"title": "New email", "body": "Open Electronic Mail to read it."}
    if delivery["preview_enabled"]:
        alert = {
            "title": str(delivery.get("sender") or "New email")[:160],
            "subtitle": str(delivery.get("subject") or "(No subject)")[:200],
            "body": str(delivery.get("snippet") or "")[:400],
        }
    aps: dict = {"alert": alert, "thread-id": delivery["gmail_account_id"]}
    if delivery["sound_enabled"]:
        aps["sound"] = "default"
    return {
        "aps": aps,
        "notification_id": delivery["id"],
        "user_id": delivery["user_id"],
        "gmail_account_id": delivery["gmail_account_id"],
        "thread_id": delivery["gmail_thread_id"],
        "message_id": delivery["message_id"],
    }


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class APNsProvider:
    def __init__(self, config: PushConfiguration, *, client=None, clock=time.time):
        self.config = config
        self.client = client or httpx.Client(http2=True, timeout=10, trust_env=False)
        self.clock = clock
        self._jwt: str | None = None
        self._issued_at = 0

    def authorization(self) -> str:
        now = int(self.clock())
        if self._jwt is not None and 0 <= now - self._issued_at < 3000:
            return self._jwt
        key = serialization.load_pem_private_key(Path(self.config.key_path).read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("APNs requires an ES256 private key")
        header = _base64url(json.dumps({"alg": "ES256", "kid": self.config.key_id}, separators=(",", ":")).encode())
        claims = _base64url(json.dumps({"iss": self.config.team_id, "iat": now}, separators=(",", ":")).encode())
        unsigned = f"{header}.{claims}"
        r, s = decode_dss_signature(key.sign(unsigned.encode(), ec.ECDSA(hashes.SHA256())))
        self._jwt = f"{unsigned}.{_base64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        self._issued_at = now
        return self._jwt

    def send(self, token: str, delivery: dict) -> str:
        host = "api.sandbox.push.apple.com" if delivery["environment"] == "sandbox" else "api.push.apple.com"
        topic = self.config.mac_topic if delivery["platform"] == "macos" else self.config.ios_topic
        collapse = hashlib.sha256(f"{delivery['gmail_account_id']}:{delivery['message_id']}".encode()).hexdigest()
        try:
            response = self.client.post(
                f"https://{host}/3/device/{token}",
                headers={
                    "authorization": f"bearer {self.authorization()}",
                    "apns-topic": topic,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-id": delivery["id"],
                    "apns-collapse-id": collapse,
                    "apns-expiration": str(int(self.clock()) + 900),
                },
                json=notification_payload(delivery),
            )
        except httpx.HTTPError:
            raise RuntimeError("APNs transport unavailable") from None
        if response.status_code == 200:
            return "sent"
        try:
            reason = response.json().get("reason")
        except (ValueError, AttributeError):
            reason = None
        if response.status_code == 410 or reason in {"BadDeviceToken", "DeviceTokenNotForTopic"}:
            return "invalid_token"
        if response.status_code in {429, 500, 503}:
            raise RuntimeError("APNs temporarily unavailable")
        if reason == "ExpiredProviderToken":
            self._jwt = None
            raise RuntimeError("APNs authorization needs renewal")
        # Authentication/configuration errors must be visible as failed jobs,
        # without leaking the token-bearing provider request URL.
        raise RuntimeError(f"APNs rejected notification (HTTP {response.status_code})")


_provider: APNsProvider | None = None


def deliver_push_notification(settings, *, delivery_id: str, user_id: str) -> None:
    from app.db.push_notifications import load_delivery, finish_delivery
    from app.db.user_mail_guard import shared_gmail_account_mail_lock
    from app.services.token_crypto import decrypt_json

    config = PushConfiguration.load()
    if not config.configured:
        finish_delivery(settings.database_path, delivery_id=delivery_id, user_id=user_id, status="skipped")
        return
    delivery = load_delivery(settings.database_path, delivery_id=delivery_id, user_id=user_id)
    if delivery is None:
        return
    global _provider
    if _provider is None or _provider.config != config:
        if _provider is not None:
            _provider.client.close()
        _provider = APNsProvider(config)
    with shared_gmail_account_mail_lock(settings.database_path, user_id=user_id, gmail_account_id=delivery["gmail_account_id"]):
        # Account deletion/disconnection drains provider work. Recheck after
        # acquiring that lock so an older loaded row cannot outlive a purge.
        delivery = load_delivery(settings.database_path, delivery_id=delivery_id, user_id=user_id)
        if delivery is None:
            return
        token = str(decrypt_json(settings, delivery["token_encrypted"])["token"])
        result = _provider.send(token, delivery)
        finish_delivery(settings.database_path, delivery_id=delivery_id, user_id=user_id, status=result)
