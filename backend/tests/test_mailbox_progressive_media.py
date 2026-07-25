from __future__ import annotations

from base64 import urlsafe_b64encode
from types import SimpleNamespace
import socket
import struct
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.routes import mailbox as mailbox_routes
from app.db.mail_groups import GmailThreadSnapshotStats
from app.main import app
from app.schemas.domain import ThreadMessage, ThreadReaderResponse
from app.services import remote_images


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_encryption_key="test-encryption-key-with-at-least-32-characters",
        database_path="postgresql://example/db",
    )


def _thread(thread_id: str, *, body_complete: bool) -> ThreadReaderResponse:
    return ThreadReaderResponse(
        entity_id=thread_id,
        user_id="user-1",
        source="gmail",
        gmail_thread_id=thread_id,
        subject="Subject",
        title="Subject",
        total_messages=1,
        limit=100,
        offset=0,
        has_more=False,
        messages=[
            ThreadMessage(
                id=f"message-{thread_id}",
                source="gmail",
                thread_id=thread_id,
                body="Complete" if body_complete else "Loading",
                body_complete=body_complete,
                received_at="2026-07-25T00:00:00+00:00",
            )
        ],
    )


def _snapshot_stats(
    thread_id: str,
    *,
    message_count: int = 1,
    stored_bytes: int = 256,
    incomplete_body_count: int = 0,
) -> GmailThreadSnapshotStats:
    return GmailThreadSnapshotStats(
        gmail_thread_id=thread_id,
        message_count=message_count,
        stored_bytes=stored_bytes,
        incomplete_body_count=incomplete_body_count,
    )


class RemoteImageAssetTests(unittest.TestCase):
    def test_asset_id_is_opaque_and_bound_to_authenticated_user(self) -> None:
        settings = _settings()
        source_url = "https://images.example-mail.com/banner.png?recipient=secret@example.com"
        asset_id = remote_images.create_remote_image_asset_id(
            settings,
            user_id="user-1",
            message_id="message-1",
            source_url=source_url,
        )

        self.assertNotIn("example-mail", asset_id)
        self.assertEqual(
            remote_images.decode_remote_image_asset_id(settings, user_id="user-1", asset_id=asset_id).source_url,
            source_url,
        )
        with self.assertRaises(remote_images.RemoteImageTokenError):
            remote_images.decode_remote_image_asset_id(settings, user_id="user-2", asset_id=asset_id)

    def test_asset_id_is_stable_for_the_same_message_asset(self) -> None:
        settings = _settings()
        kwargs = {
            "user_id": "user-1",
            "message_id": "message-1",
            "source_url": "https://images.example-mail.com/banner.png?campaign=weekly",
        }

        first = remote_images.create_remote_image_asset_id(settings, **kwargs)
        second = remote_images.create_remote_image_asset_id(settings, **kwargs)

        self.assertEqual(first, second)
        self.assertNotEqual(
            first,
            remote_images.create_remote_image_asset_id(
                settings,
                user_id="user-1",
                message_id="message-2",
                source_url=kwargs["source_url"],
            ),
        )

    def test_html_rewrite_uses_only_custom_scheme_and_removes_srcset_bypass(self) -> None:
        rewritten = remote_images.rewrite_external_image_sources(
            _settings(),
            user_id="user-1",
            message_id="message-1",
            document=(
                '<img src="https://cdn.example-mail.com/image.png" '
                'srcset="https://cdn.example-mail.com/image@2x.png 2x">'
                '<div style="background:url(https://cdn.example-mail.com/background.jpg)"></div>'
            ),
        )

        self.assertIsNotNone(rewritten)
        self.assertNotIn("srcset", rewritten or "")
        self.assertNotIn("https://cdn.example-mail.com", rewritten or "")
        self.assertEqual((rewritten or "").count("electronicmail-image://asset/"), 2)

    def test_private_and_non_https_sources_are_blocked(self) -> None:
        with self.assertRaises(remote_images.RemoteImageBlocked):
            remote_images.create_remote_image_asset_id(
                _settings(),
                user_id="user-1",
                message_id="message-1",
                source_url="http://public.example/image.png",
            )
        with patch.object(
            remote_images.socket,
            "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ):
            with self.assertRaises(remote_images.RemoteImageBlocked):
                remote_images._public_addresses("redirect.example", 443)

    def test_control_characters_cannot_inject_remote_request_headers(self) -> None:
        with self.assertRaises(remote_images.RemoteImageBlocked):
            remote_images.create_remote_image_asset_id(
                _settings(),
                user_id="user-1",
                message_id="message-1",
                source_url="https://images.example/image.png\r\nCookie: stolen=yes",
            )

    def test_one_pixel_png_is_recognized_as_tracking_image(self) -> None:
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1, 1)
        self.assertTrue(remote_images._is_tracking_pixel(png_header, mime_type="image/png"))

    def test_tracking_markup_is_suppressed_before_an_asset_is_created(self) -> None:
        rewritten = remote_images.rewrite_external_image_sources(
            _settings(),
            user_id="user-1",
            message_id="message-1",
            document=(
                '<img width="1" height="2" '
                'src="https://cdn.example-mail.com/innocent-looking.gif">'
                '<img style="width: 1px; height: 1px" '
                'src="https://cdn.example-mail.com/also-innocent.png">'
            ),
        )

        self.assertEqual((rewritten or "").count("about:blank"), 2)
        self.assertNotIn(remote_images.REMOTE_IMAGE_SCHEME, rewritten or "")

    def test_recognized_tracking_url_is_rejected_before_network_access(self) -> None:
        tracker_url = "https://images.example-mail.com/campaign/open.gif?recipient=abc"
        with patch.object(remote_images, "_open_public_https") as open_image:
            with self.assertRaises(remote_images.RemoteImageBlocked):
                remote_images.fetch_remote_image(tracker_url)

        open_image.assert_not_called()

    def test_recognized_tracking_url_never_gets_an_asset_id(self) -> None:
        with self.assertRaises(remote_images.RemoteImageBlocked):
            remote_images.create_remote_image_asset_id(
                _settings(),
                user_id="user-1",
                message_id="message-1",
                source_url="https://images.example-mail.com/beacon/pixel.png",
            )


class MailboxProgressiveMediaRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_batch_thread_endpoint_separates_complete_pending_and_missing(self) -> None:
        def detail(_settings, *, group_id: str, **_kwargs):
            if group_id == "complete":
                return _thread(group_id, body_complete=True)
            if group_id == "pending":
                return _thread(group_id, body_complete=False)
            return None

        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(
                mailbox_routes,
                "get_gmail_thread_snapshot_stats",
                return_value={
                    "complete": _snapshot_stats("complete"),
                    "pending": _snapshot_stats("pending", incomplete_body_count=1),
                },
            ),
            patch.object(mailbox_routes, "build_group_detail_response", side_effect=detail) as build_detail,
        ):
            response = self.client.post(
                "/v1/mailbox/threads/batch",
                json={"thread_ids": ["complete", "pending", "missing", "complete"]},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([thread["gmail_thread_id"] for thread in payload["threads"]], ["complete"])
        self.assertEqual(payload["pending_thread_ids"], ["pending"])
        self.assertEqual(payload["missing_thread_ids"], ["missing"])
        self.assertTrue(build_detail.call_args_list)
        self.assertTrue(
            all(call.kwargs["promote_body_fetch"] is False for call in build_detail.call_args_list)
        )
        self.assertTrue(
            all(
                call.kwargs["maximum_snapshot_messages"] == mailbox_routes.MAX_BATCH_THREAD_MESSAGES
                for call in build_detail.call_args_list
            )
        )

    def test_batch_thread_endpoint_keeps_huge_thread_pending_without_loading_it(self) -> None:
        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(
                mailbox_routes,
                "get_gmail_thread_snapshot_stats",
                return_value={
                    "huge": _snapshot_stats(
                        "huge",
                        message_count=mailbox_routes.MAX_BATCH_THREAD_MESSAGES + 1,
                    )
                },
            ),
            patch.object(mailbox_routes, "build_group_detail_response") as build_detail,
        ):
            response = self.client.post(
                "/v1/mailbox/threads/batch",
                json={"thread_ids": ["huge"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["threads"], [])
        self.assertEqual(response.json()["pending_thread_ids"], ["huge"])
        self.assertEqual(response.json()["missing_thread_ids"], [])
        build_detail.assert_not_called()

    def test_batch_thread_endpoint_never_returns_partial_aggregate(self) -> None:
        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(
                mailbox_routes,
                "get_gmail_thread_snapshot_stats",
                return_value={
                    "first": _snapshot_stats("first", stored_bytes=50),
                    "second": _snapshot_stats("second", stored_bytes=50),
                },
            ),
            patch.object(
                mailbox_routes,
                "build_group_detail_response",
                side_effect=[_thread("first", body_complete=True), _thread("second", body_complete=True)],
            ),
            patch.object(mailbox_routes, "_serialized_model_bytes", side_effect=[2_000, 3_500]),
            patch.object(mailbox_routes, "MAX_BATCH_THREAD_RESPONSE_BYTES", 4_000),
            patch.object(mailbox_routes, "MAX_BATCH_RESPONSE_BYTES", 5_000),
        ):
            response = self.client.post(
                "/v1/mailbox/threads/batch",
                json={"thread_ids": ["first", "second"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [thread["gmail_thread_id"] for thread in response.json()["threads"]],
            ["first"],
        )
        self.assertEqual(response.json()["pending_thread_ids"], ["second"])

    def test_batch_thread_endpoint_rejects_more_than_twenty_ids(self) -> None:
        with patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")):
            response = self.client.post(
                "/v1/mailbox/threads/batch",
                json={"thread_ids": [f"thread-{index}" for index in range(21)]},
            )
        self.assertEqual(response.status_code, 422)

    def test_attachment_response_has_private_immutable_cache_contract(self) -> None:
        attachment = SimpleNamespace(filename="report.pdf", mime_type="application/pdf", attachment_id="attachment-1")
        with (
            patch.object(mailbox_routes, "settings", _settings()),
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(mailbox_routes, "list_messages_by_ids", return_value=[SimpleNamespace(message_id="message-1")]),
            patch.object(mailbox_routes, "gmail_attachments_for_message", return_value=[attachment]),
            patch.object(
                mailbox_routes,
                "fetch_gmail_attachment",
                return_value={"data": urlsafe_b64encode(b"pdf-bytes").decode("ascii")},
            ),
        ):
            response = self.client.get("/v1/mailbox/messages/message-1/attachments/attachment-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-length"], str(len(b"pdf-bytes")))
        self.assertEqual(response.headers["cache-control"], "private, max-age=31536000, immutable")
        self.assertTrue(response.headers["etag"].startswith('"'))
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

        with (
            patch.object(mailbox_routes, "settings", _settings()),
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(mailbox_routes, "list_messages_by_ids", return_value=[SimpleNamespace(message_id="message-1")]),
            patch.object(mailbox_routes, "gmail_attachments_for_message", return_value=[attachment]),
            patch.object(
                mailbox_routes,
                "fetch_gmail_attachment",
                return_value={"data": urlsafe_b64encode(b"pdf-bytes").decode("ascii")},
            ),
        ):
            cached = self.client.get(
                "/v1/mailbox/messages/message-1/attachments/attachment-1",
                headers={"If-None-Match": response.headers["etag"]},
            )

        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.headers["etag"], response.headers["etag"])
        self.assertEqual(cached.headers["cache-control"], "private, max-age=31536000, immutable")

    def test_attachment_provider_404_is_terminal_not_a_gateway_error(self) -> None:
        attachment = SimpleNamespace(filename="gone.pdf", mime_type="application/pdf", attachment_id="attachment-1")
        provider_error = RuntimeError("provider body must not be exposed")
        provider_error.resp = SimpleNamespace(status=404)
        with (
            patch.object(mailbox_routes, "settings", _settings()),
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(mailbox_routes, "list_messages_by_ids", return_value=[SimpleNamespace(message_id="message-1")]),
            patch.object(mailbox_routes, "gmail_attachments_for_message", return_value=[attachment]),
            patch.object(mailbox_routes, "fetch_gmail_attachment", side_effect=provider_error),
        ):
            response = self.client.get("/v1/mailbox/messages/message-1/attachments/attachment-1")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Attachment no longer exists")
        self.assertNotIn("provider body", response.text)

    def test_remote_image_requires_message_ownership(self) -> None:
        route_settings = _settings()
        asset_id = remote_images.create_remote_image_asset_id(
            route_settings,
            user_id="user-1",
            message_id="message-1",
            source_url="https://mail-images.example.com/image.png",
        )
        with (
            patch.object(mailbox_routes, "settings", route_settings),
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(mailbox_routes, "list_messages_by_ids", return_value=[]),
            patch.object(mailbox_routes, "fetch_remote_image") as fetch_image,
        ):
            response = self.client.get(f"/v1/mailbox/remote-images/{asset_id}")

        self.assertEqual(response.status_code, 404)
        fetch_image.assert_not_called()


if __name__ == "__main__":
    unittest.main()
