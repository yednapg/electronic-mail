from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.db import mail_groups
from app.services import gmail_importer


class GmailThreadOrderRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql://example/db")

    @patch("app.services.gmail_importer.enqueue_job")
    def test_import_path_queues_deduplicated_order_refresh(self, mock_enqueue: Mock) -> None:
        queued = gmail_importer._refresh_gmail_thread_order_best_effort(
            self.settings,
            user_id="user-1",
            target_history_id="123",
        )

        self.assertTrue(queued)
        mock_enqueue.assert_called_once_with(
            "postgresql://example/db",
            kind="gmail_thread_order_refresh",
            queue="slow",
            user_id="user-1",
            dedupe_key="gmail-thread-order-refresh:user-1:123",
            priority=20,
            payload={"user_id": "user-1", "target_history_id": "123"},
            run_after_seconds=30,
        )

    @patch("app.services.gmail_importer.enqueue_job", side_effect=RuntimeError("database unavailable"))
    def test_import_path_does_not_fail_when_order_refresh_cannot_be_queued(self, _mock_enqueue: Mock) -> None:
        queued = gmail_importer._refresh_gmail_thread_order_best_effort(
            self.settings,
            user_id="user-1",
            target_history_id="123",
        )

        self.assertFalse(queued)

    @patch("app.services.gmail_importer.replace_gmail_thread_orders")
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    def test_refresh_uses_exact_gmail_specs_and_preserves_paginated_order(
        self,
        _mock_credentials: Mock,
        mock_build_service: Mock,
        mock_replace: Mock,
    ) -> None:
        service = Mock()
        mock_build_service.return_value = service
        responses = [
            {"threads": [{"id": "thread-a"}, {"id": "thread-b"}], "nextPageToken": "inbox-page-2"},
            {"threads": [{"id": "thread-b"}, {"id": "thread-c"}]},
            {"threads": [{"id": "thread-sent"}]},
            {"threads": [{"id": "thread-draft"}]},
            {"threads": [{"id": "thread-starred"}]},
            {"threads": [{"id": "thread-spam"}]},
            {"threads": [{"id": "thread-trash"}]},
            {"threads": [{"id": "thread-all"}]},
            {"threads": [{"id": "thread-archive"}]},
        ]
        service.users.return_value.threads.return_value.list.side_effect = [
            SimpleNamespace(execute=Mock(return_value=response)) for response in responses
        ]
        mock_replace.return_value = {"inbox": "generation-1"}

        result = gmail_importer.refresh_gmail_thread_order(self.settings, user_id="user-1")

        self.assertEqual(result, {"inbox": "generation-1"})
        list_calls = service.users.return_value.threads.return_value.list.call_args_list
        self.assertEqual(len(list_calls), 9)
        self.assertEqual(
            list_calls[0].kwargs,
            {"userId": "me", "maxResults": 500, "includeSpamTrash": True, "labelIds": ["INBOX"]},
        )
        self.assertEqual(
            list_calls[1].kwargs,
            {
                "userId": "me",
                "maxResults": 500,
                "includeSpamTrash": True,
                "labelIds": ["INBOX"],
                "pageToken": "inbox-page-2",
            },
        )
        self.assertEqual(list_calls[2].kwargs["labelIds"], ["SENT"])
        self.assertEqual(list_calls[3].kwargs["labelIds"], ["DRAFT"])
        self.assertEqual(list_calls[4].kwargs["labelIds"], ["STARRED"])
        self.assertEqual(list_calls[5].kwargs["labelIds"], ["SPAM"])
        self.assertEqual(list_calls[6].kwargs["labelIds"], ["TRASH"])
        self.assertEqual(list_calls[7].kwargs["q"], "-label:spam -label:trash")
        self.assertEqual(
            list_calls[8].kwargs["q"],
            "-label:inbox -label:sent -label:drafts -label:spam -label:trash",
        )
        ordered = mock_replace.call_args.kwargs["ordered_thread_ids_by_label"]
        self.assertEqual(ordered["inbox"], ["thread-a", "thread-b", "thread-c"])
        self.assertEqual(set(ordered), {label for label, _query in gmail_importer.GMAIL_THREAD_ORDER_SPECS})
        self.assertEqual(mock_replace.call_args.kwargs["user_id"], "user-1")

    @patch("app.services.gmail_importer.replace_gmail_thread_orders")
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    def test_refresh_does_not_publish_partial_labels_when_gmail_listing_fails(
        self,
        _mock_credentials: Mock,
        mock_build_service: Mock,
        mock_replace: Mock,
    ) -> None:
        service = Mock()
        mock_build_service.return_value = service
        service.users.return_value.threads.return_value.list.side_effect = [
            SimpleNamespace(execute=Mock(return_value={"threads": [{"id": "thread-a"}]})),
            RuntimeError("temporary Gmail failure"),
        ]

        with self.assertRaises(RuntimeError):
            gmail_importer.refresh_gmail_thread_order(self.settings, user_id="user-1")

        mock_replace.assert_not_called()


class GmailThreadOrderRepositoryTests(unittest.TestCase):
    def test_generation_replacement_publishes_new_order_then_keeps_one_previous_generation(self) -> None:
        connection = _RecordingConnection(previous_by_label={"inbox": "generation-old"})
        engine = _RecordingEngine(connection)

        with patch.object(mail_groups, "get_engine", return_value=engine):
            generations = mail_groups.replace_gmail_thread_orders(
                "postgresql://example/db",
                user_id="user-1",
                ordered_thread_ids_by_label={
                    "inbox": ["thread-b", "thread-a", "thread-b"],
                    "sent": [],
                },
            )

        self.assertEqual(engine.begin_calls, 1)
        self.assertEqual(set(generations), {"inbox", "sent"})
        entry_call = next(
            (params for sql, params in connection.calls if "INSERT INTO gmail_thread_order_entries" in sql),
            None,
        )
        self.assertIsInstance(entry_call, list)
        self.assertEqual([row["gmail_thread_id"] for row in entry_call], ["thread-b", "thread-a"])
        self.assertEqual([row["position"] for row in entry_call], [0, 1])
        state_calls = [params for sql, params in connection.calls if "INSERT INTO gmail_thread_order_state" in sql]
        inbox_state = next(params for params in state_calls if params["label"] == "inbox")
        sent_state = next(params for params in state_calls if params["label"] == "sent")
        self.assertEqual(inbox_state["previous_generation_id"], "generation-old")
        self.assertIsNone(sent_state["previous_generation_id"])
        delete_calls = [params for sql, params in connection.calls if "DELETE FROM gmail_thread_order_entries" in sql]
        inbox_delete = next(params for params in delete_calls if params["label"] == "inbox")
        self.assertEqual(
            inbox_delete["keep_generations"],
            [generations["inbox"], "generation-old"],
        )
        for _sql, params in connection.calls:
            values = params if isinstance(params, list) else [params]
            for values_row in values:
                if "user_id" in values_row:
                    self.assertEqual(values_row["user_id"], "user-1")


class _ScalarResult:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class _RecordingConnection:
    def __init__(self, *, previous_by_label: dict[str, str]) -> None:
        self.previous_by_label = previous_by_label
        self.calls: list[tuple[str, dict | list[dict]]] = []

    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if "SELECT EXISTS" in sql:
            return _ScalarResult(True)
        if "SELECT active_generation_id" in sql:
            return _ScalarResult(self.previous_by_label.get(params["label"]))
        return _ScalarResult()


class _RecordingEngine:
    def __init__(self, connection: _RecordingConnection) -> None:
        self.connection = connection
        self.begin_calls = 0

    def begin(self):
        self.begin_calls += 1
        return nullcontext(self.connection)


if __name__ == "__main__":
    unittest.main()
