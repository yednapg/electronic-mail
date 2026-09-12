from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import text

from app.api.routes.push_notifications import DeviceRegistration
from app.core.config import load_settings
from app.db import push_notifications as db
from app.db.account_scope import gmail_account_scope
from app.db.mail_groups import GmailMessageRecord, upsert_gmail_messages
from app.db.repository import create_app_session, get_engine, revoke_app_session, upsert_user, upsert_google_oauth_token
from app.main import create_app
from app.services.auth import hash_token
from app.services.push_notifications import APNsProvider, PushConfiguration, deliver_push_notification, eligible_new_mail, notification_payload
from app.services.token_crypto import decrypt_json


def delivery(**overrides):
    return dict(id=str(uuid4()), user_id="user", gmail_account_id="account", gmail_thread_id="thread",
                message_id="message", platform="ios", environment="sandbox", sound_enabled=True,
                preview_enabled=False, sender="Private sender", subject="Private subject", snippet="Private snippet") | overrides


class PushNotificationTests(unittest.TestCase):
    def test_history_distinguishes_new_mail_from_label_changes_and_deletions(self):
        from app.services.gmail_importer import _list_history_delta
        service = Mock()
        service.users.return_value.history.return_value.list.return_value.execute.return_value = {
            "historyId": "105",
            "history": [{"id": "104", "messagesAdded": [{"message": {"id": "new"}}, {"message": {"id": "deleted"}}],
                         "labelsAdded": [{"message": {"id": "existing"}}], "messagesDeleted": [{"message": {"id": "deleted"}}]}],
        }
        with patch("app.services.gmail_importer.create_authorized_credentials", return_value=object()), patch("app.services.gmail_importer.build_google_service", return_value=service):
            result = _list_history_delta(load_settings(), user_id="user", start_history_id="100", page_size=100)
        self.assertEqual(result["added_message_ids"], ["new"])
        self.assertEqual(result["message_ids"], ["new", "existing"])

    def test_only_recent_unread_inbox_messages_are_eligible(self):
        now = datetime.now(timezone.utc)
        message = SimpleNamespace(label_ids=["INBOX", "UNREAD"], internal_date=now.isoformat(), gmail_thread_id="thread")
        self.assertTrue(eligible_new_mail(message, now=now))
        for labels in (["INBOX"], ["UNREAD"], ["INBOX", "UNREAD", "SENT"], ["INBOX", "UNREAD", "SPAM"], ["INBOX", "UNREAD", "TRASH"], ["INBOX", "UNREAD", "DRAFT"]):
            self.assertFalse(eligible_new_mail(SimpleNamespace(**(vars(message) | {"label_ids": labels})), now=now))
        for date in ((now-timedelta(hours=1)).isoformat(), None, "bad", "2026-09-08T10:00:00"):
            self.assertFalse(eligible_new_mail(SimpleNamespace(**(vars(message) | {"internal_date": date})), now=now))

    def test_private_payload_has_routing_but_no_email_content(self):
        payload = notification_payload(delivery())
        self.assertNotIn("Private", json.dumps(payload))
        self.assertEqual(payload["gmail_account_id"], "account")
        self.assertEqual(payload["thread_id"], "thread")
        self.assertEqual(payload["message_id"], "message")

    def test_preview_is_opt_in_and_payload_is_bounded(self):
        payload = notification_payload(delivery(preview_enabled=True, sound_enabled=False, snippet="🙂"*10000))
        self.assertEqual(payload["aps"]["alert"]["title"], "Private sender")
        self.assertNotIn("sound", payload["aps"])
        self.assertLess(len(json.dumps(payload, ensure_ascii=False).encode()), 4096)

    def test_registration_rejects_invalid_token_platform_and_environment(self):
        for values in ({"token": "https://example.com"}, {"platform": "web"}, {"environment": "arbitrary-host"}):
            with self.assertRaises(ValueError):
                DeviceRegistration.model_validate({"token": "ab"*32, "platform": "ios", "environment": "sandbox"} | values)

    def test_apns_uses_correct_topic_environment_and_stable_delivery_id(self):
        client = Mock()
        client.post.return_value = httpx.Response(200)
        provider = APNsProvider(PushConfiguration(), client=client, clock=lambda: 1000)
        provider.authorization = Mock(return_value="signed-token")
        item = delivery(platform="macos", environment="production")
        self.assertEqual(provider.send("ab"*32, item), "sent")
        args, kwargs = client.post.call_args
        self.assertTrue(args[0].startswith("https://api.push.apple.com/3/device/"))
        self.assertEqual(kwargs["headers"]["apns-topic"], "app.electronicmail.mac")
        self.assertEqual(kwargs["headers"]["apns-id"], item["id"])
        self.assertEqual(kwargs["headers"]["apns-push-type"], "alert")
        self.assertEqual(len(kwargs["headers"]["apns-collapse-id"]), 64)
        provider.send("ab"*32, delivery())
        self.assertTrue(client.post.call_args.args[0].startswith("https://api.sandbox.push.apple.com/"))

    def test_invalid_tokens_are_retired_and_transient_failures_retry(self):
        for code, reason in ((410, "Unregistered"), (400, "BadDeviceToken"), (400, "DeviceTokenNotForTopic")):
            provider = APNsProvider(PushConfiguration(), client=Mock())
            provider.authorization = Mock(return_value="jwt")
            provider.client.post.return_value = httpx.Response(code, json={"reason": reason})
            self.assertEqual(provider.send("ab"*32, delivery()), "invalid_token")
        for code in (429, 500, 503, 403):
            provider.client.post.return_value = httpx.Response(code, json={"reason": "Error"})
            with self.assertRaises(RuntimeError):
                provider.send("ab"*32, delivery())

    def test_transport_errors_do_not_expose_token(self):
        provider = APNsProvider(PushConfiguration(), client=Mock())
        provider.authorization = Mock(return_value="jwt")
        provider.client.post.side_effect = httpx.ConnectError("https://api.push.apple.com/3/device/secret-token")
        with self.assertRaisesRegex(RuntimeError, "^APNs transport unavailable$"):
            provider.send("secret-token", delivery())

    def test_provider_jwt_is_es256_and_cached_for_less_than_an_hour(self):
        key = ec.generate_private_key(ec.SECP256R1())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"key.p8"
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            now = [10000]
            provider = APNsProvider(PushConfiguration(team_id="TEAM", key_id="KEY", key_path=str(path)), client=Mock(), clock=lambda: now[0])
            jwt = provider.authorization()
            header, claims, signature = jwt.split(".")
            decode = lambda value: base64.urlsafe_b64decode(value + "="*((-len(value)) % 4))
            self.assertEqual(json.loads(decode(header)), {"alg": "ES256", "kid": "KEY"})
            self.assertEqual(json.loads(decode(claims)), {"iss": "TEAM", "iat": 10000})
            raw = decode(signature)
            key.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:])),
                                    f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
            now[0] += 2999
            self.assertEqual(provider.authorization(), jwt)
            now[0] += 1
            self.assertNotEqual(provider.authorization(), jwt)

    def test_api_requires_authentication(self):
        client = TestClient(create_app(load_settings()))
        self.assertEqual(client.get("/v1/push/status").status_code, 401)
        self.assertEqual(client.put(f"/v1/push/devices/{uuid4()}", json={"token": "ab"*32, "platform": "ios", "environment": "sandbox"}).status_code, 401)
        self.assertEqual(client.delete(f"/v1/push/devices/{uuid4()}").status_code, 401)


TEST_DATABASE = os.getenv("PUSH_TEST_DATABASE_URL", "")
TEST_TARGET = urlparse(TEST_DATABASE)


@unittest.skipUnless(TEST_TARGET.hostname in {"127.0.0.1", "localhost", "::1"} and TEST_TARGET.path.startswith("/push_test_"), "requires an isolated migrated push_test_ database")
class PushNotificationPostgresTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(load_settings(), database_url=TEST_DATABASE, database_path=TEST_DATABASE)
        self.user = upsert_user(TEST_DATABASE, email=f"{uuid4()}@example.test", google_sub=str(uuid4()))
        self.account_id = self.user.id
        self.token = str(uuid4())
        self.session = create_app_session(TEST_DATABASE, user_id=self.user.id, token_hash=hash_token(self.settings, self.token),
                                          platform="ios", expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
        upsert_google_oauth_token(TEST_DATABASE, user_id=self.user.id, token_json_encrypted="fixture")
        with get_engine(TEST_DATABASE).begin() as connection:
            connection.execute(text("UPDATE gmail_accounts SET state='ready' WHERE id=:id"), {"id": self.user.id})
        self.device_id = str(uuid4())
        self.registration = DeviceRegistration(token="ab"*32, platform="ios", environment="sandbox")
        self.register()
        self.configuration = patch("app.services.push_notifications.PushConfiguration.load", return_value=PushConfiguration(True, "TEAM", "KEY", "/fixture/key.p8"))
        self.configuration.start()
        self.addCleanup(self.configuration.stop)

    def tearDown(self):
        with get_engine(TEST_DATABASE).begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": self.user.id})

    def register(self):
        db.register_device(self.settings, device_id=self.device_id, user_id=self.user.id,
                           session_id=self.session.id, registration=self.registration)

    def queue(self, message_id="message"):
        now = datetime.now(timezone.utc).isoformat()
        message = GmailMessageRecord(user_id=self.user.id, message_id=message_id, gmail_thread_id="thread", history_id="100",
            label_ids=["INBOX", "UNREAD"], internal_date=now, subject="subject", sender="sender", recipients={}, headers={},
            snippet="snippet", raw_payload={}, html_body_sanitized=None, html_render_document=None, text_body=None,
            extracted_signals={}, body_hash="hash", created_at=now, updated_at=now, gmail_account_id=self.account_id)
        with gmail_account_scope(self.account_id):
            upsert_gmail_messages(TEST_DATABASE, [message])
            db.queue_new_mail(self.settings, user_id=self.user.id, messages=[message])
            with get_engine(TEST_DATABASE).connect() as connection:
                row = connection.execute(text("SELECT id FROM push_deliveries WHERE user_id=:user_id AND gmail_account_id=:account_id AND message_id=:message_id"),
                                         {"user_id": self.user.id, "account_id": self.account_id, "message_id": message_id}).first()
        return row[0] if row else None

    def load(self, delivery_id):
        with gmail_account_scope(self.account_id):
            return db.load_delivery(TEST_DATABASE, delivery_id=delivery_id, user_id=self.user.id)

    def test_token_is_encrypted_and_job_is_deduplicated(self):
        delivery_id = self.queue()
        self.assertEqual(self.queue(), delivery_id)
        row = self.load(delivery_id)
        self.assertEqual(decrypt_json(self.settings, row["token_encrypted"])["token"], self.registration.token)
        self.assertNotIn(self.registration.token, row["token_encrypted"])
        with get_engine(TEST_DATABASE).connect() as connection:
            count = connection.execute(text("SELECT count(*) FROM background_jobs WHERE user_id=:id AND kind='push_notification'"), {"id": self.user.id}).scalar_one()
        self.assertEqual(count, 1)

    def test_worker_sends_once_and_marks_delivery_complete(self):
        delivery_id = self.queue()
        provider = Mock(config=PushConfiguration.load())
        provider.send.return_value = "sent"
        with patch("app.services.push_notifications._provider", provider):
            deliver_push_notification(self.settings, delivery_id=delivery_id, user_id=self.user.id)
            deliver_push_notification(self.settings, delivery_id=delivery_id, user_id=self.user.id)
        provider.send.assert_called_once()
        self.assertEqual(provider.send.call_args.args[0], self.registration.token)
        with get_engine(TEST_DATABASE).connect() as connection:
            self.assertEqual(connection.execute(text("SELECT status FROM push_deliveries WHERE id=:id"), {"id": delivery_id}).scalar_one(), "sent")

    def test_worker_invalid_token_disables_device(self):
        delivery_id = self.queue()
        provider = Mock(config=PushConfiguration.load())
        provider.send.return_value = "invalid_token"
        with patch("app.services.push_notifications._provider", provider):
            deliver_push_notification(self.settings, delivery_id=delivery_id, user_id=self.user.id)
        with get_engine(TEST_DATABASE).connect() as connection:
            self.assertFalse(connection.execute(text("SELECT enabled FROM push_devices WHERE id=:id"), {"id": self.device_id}).scalar_one())

    def test_revoked_session_cannot_receive_queued_mail(self):
        delivery_id = self.queue()
        revoke_app_session(TEST_DATABASE, token_hash=hash_token(self.settings, self.token))
        self.assertIsNone(self.load(delivery_id))

    def test_read_message_is_skipped_before_delivery(self):
        delivery_id = self.queue()
        with get_engine(TEST_DATABASE).begin() as connection:
            connection.execute(text("UPDATE gmail_messages SET label_ids_json='[\"INBOX\"]' WHERE user_id=:id"), {"id": self.user.id})
        self.assertIsNone(self.load(delivery_id))

    def test_preference_changes_apply_to_queued_delivery(self):
        delivery_id = self.queue()
        self.registration.preview_enabled = True
        self.registration.sound_enabled = False
        self.register()
        self.assertTrue(self.load(delivery_id)["preview_enabled"])
        self.assertFalse(self.load(delivery_id)["sound_enabled"])

    def test_disabled_device_cannot_receive_queued_mail(self):
        delivery_id = self.queue()
        self.registration.enabled = False
        self.register()
        self.assertIsNone(self.load(delivery_id))

    def test_delivery_cannot_be_loaded_by_another_user(self):
        delivery_id = self.queue()
        self.assertIsNone(db.load_delivery(TEST_DATABASE, delivery_id=delivery_id, user_id="another-user"))
        self.assertIsNotNone(self.load(delivery_id))

    def test_reinstalled_device_replaces_old_token_registration(self):
        self.queue()
        self.device_id = str(uuid4())
        self.register()
        with get_engine(TEST_DATABASE).connect() as connection:
            self.assertEqual(connection.execute(text("SELECT count(*) FROM push_devices WHERE user_id=:id"), {"id": self.user.id}).scalar_one(), 1)

    def test_delete_registration_is_bound_to_its_session(self):
        delivery_id = self.queue()
        db.delete_device(TEST_DATABASE, device_id=self.device_id, user_id=self.user.id, session_id="different-session")
        self.assertIsNotNone(self.load(delivery_id))
        db.delete_device(TEST_DATABASE, device_id=self.device_id, user_id=self.user.id, session_id=self.session.id)
        self.assertIsNone(self.load(delivery_id))

    def test_api_registers_only_for_authenticated_session(self):
        client = TestClient(create_app(self.settings))
        client.headers["Authorization"] = f"Bearer {self.token}"
        response = client.put(f"/v1/push/devices/{self.device_id}", json=self.registration.model_dump())
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(client.get("/v1/push/status").json(), {"available": True})
        self.assertEqual(client.delete(f"/v1/push/devices/{self.device_id}").status_code, 204)

    def test_second_account_has_separate_deliveries(self):
        first_id = self.queue()
        self.account_id = str(uuid4())
        with get_engine(TEST_DATABASE).begin() as connection:
            connection.execute(text("INSERT INTO gmail_accounts (id, user_id, email, google_sub, state) VALUES (:id, :user_id, :email, :id, 'ready')"),
                               {"id": self.account_id, "user_id": self.user.id, "email": f"{uuid4()}@example.test"})
        upsert_google_oauth_token(TEST_DATABASE, user_id=self.user.id, gmail_account_id=self.account_id, token_json_encrypted="fixture")
        second_id = self.queue()
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.load(second_id)["gmail_account_id"], self.account_id)


if __name__ == "__main__":
    unittest.main()
