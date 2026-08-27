from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services import contact_avatars


class ContactAvatarTests(unittest.TestCase):
    def test_normalize_contact_email_extracts_and_lowercases_address(self) -> None:
        self.assertEqual(
            contact_avatars.normalize_contact_email("TestUser <contact@example.test>"),
            "contact@example.test",
        )
        self.assertIsNone(contact_avatars.normalize_contact_email("Not an address"))

    def test_missing_optional_scope_keeps_cached_results_without_people_request(self) -> None:
        settings = SimpleNamespace(app_encryption_key="secret", database_path="postgresql://example/db")
        with (
            patch.object(
                contact_avatars,
                "_load_cached",
                return_value=({"saved@example.com": "https://lh3.googleusercontent.com/photo"}, ["missing@example.com"]),
            ),
            patch.object(contact_avatars, "missing_google_scopes", return_value=[contact_avatars.GOOGLE_CONTACTS_READ_SCOPE]),
            patch.object(contact_avatars, "_fetch_saved_contact_index") as fetch_index,
            patch.object(contact_avatars, "create_remote_image_asset_id", return_value="opaque") as create_asset,
        ):
            result = contact_avatars.resolve_contact_avatar_asset_ids(
                settings,
                user_id="user-1",
                sender_values=["Saved <saved@example.com>", "Missing <missing@example.com>"],
            )

        self.assertEqual(result, {"saved@example.com": "opaque"})
        fetch_index.assert_not_called()
        create_asset.assert_called_once()

    def test_people_pagination_indexes_saved_nondefault_photos(self) -> None:
        first = {
            "connections": [
                {
                    "emailAddresses": [{"value": "One@Example.com"}],
                    "photos": [{"url": "https://lh3.googleusercontent.com/one", "default": False}],
                }
            ],
            "nextPageToken": "page-2",
        }
        second = {
            "connections": [
                {
                    "emailAddresses": [{"value": "two@example.com"}],
                    "photos": [{"url": "https://lh3.googleusercontent.com/default", "default": True}],
                }
            ]
        }

        class ExecuteRequest:
            def __init__(self, payload):
                self.payload = payload

            def execute(self):
                return self.payload

        class Connections:
            def __init__(self):
                self.calls = 0

            def list(self, **_kwargs):
                payload = [first, second][self.calls]
                self.calls += 1
                return ExecuteRequest(payload)

        connections = Connections()
        people_service = SimpleNamespace(
            people=lambda: SimpleNamespace(connections=lambda: connections)
        )
        with (
            patch.object(contact_avatars, "create_authorized_credentials", return_value=object()),
            patch.object(contact_avatars, "build_google_service", return_value=people_service),
        ):
            index = contact_avatars._fetch_saved_contact_index(
                SimpleNamespace(database_path="postgresql://example/db"),
                user_id="user-1",
            )

        self.assertEqual(index, {"one@example.com": "https://lh3.googleusercontent.com/one"})
        self.assertEqual(connections.calls, 2)


if __name__ == "__main__":
    unittest.main()
