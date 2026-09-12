from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from app.api.routes import ai_inbox as ai_inbox_routes
from app.schemas.ai_inbox import MatterEntityActionRequest
from app.services import ai_inbox_actions


class AIInboxGroupActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql:///unused")

    def test_move_to_trash_applies_to_every_unique_group_message_then_hides_group(self) -> None:
        with (
            patch.object(ai_inbox_actions, "_apply_message_action") as apply_message,
            patch.object(ai_inbox_actions, "hide_matter") as hide_matter,
            patch.object(ai_inbox_actions, "enqueue_job"),
            patch.object(ai_inbox_actions, "emit_mailbox_event"),
        ):
            ai_inbox_actions.run_matter_action(
                self.settings,
                user_id="user-1",
                matter_id="matter-1",
                action="move_trash",
                message_ids=["message-1", "message-2", "message-1"],
            )

        self.assertEqual(
            apply_message.call_args_list,
            [
                call(
                    self.settings,
                    user_id="user-1",
                    message_id="message-1",
                    action="move_trash",
                ),
                call(
                    self.settings,
                    user_id="user-1",
                    message_id="message-2",
                    action="move_trash",
                ),
            ],
        )
        hide_matter.assert_called_once_with(
            "postgresql:///unused",
            user_id="user-1",
            matter_id="matter-1",
        )

    def test_group_remains_visible_if_any_message_mutation_fails(self) -> None:
        with (
            patch.object(
                ai_inbox_actions,
                "_apply_message_action",
                side_effect=[None, RuntimeError("provider failed")],
            ),
            patch.object(ai_inbox_actions, "hide_matter") as hide_matter,
            patch.object(ai_inbox_actions, "enqueue_job"),
            patch.object(ai_inbox_actions, "emit_mailbox_event"),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                ai_inbox_actions.run_matter_action(
                    self.settings,
                    user_id="user-1",
                    matter_id="matter-1",
                    action="move_trash",
                    message_ids=["message-1", "message-2"],
                )

        hide_matter.assert_not_called()

    def test_non_removing_group_action_does_not_hide_group(self) -> None:
        with (
            patch.object(ai_inbox_actions, "_apply_message_action"),
            patch.object(ai_inbox_actions, "hide_matter") as hide_matter,
            patch.object(ai_inbox_actions, "enqueue_job"),
            patch.object(ai_inbox_actions, "emit_mailbox_event"),
        ):
            ai_inbox_actions.run_matter_action(
                self.settings,
                user_id="user-1",
                matter_id="matter-1",
                action="mark_read",
                message_ids=["message-1", "message-2"],
            )

        hide_matter.assert_not_called()


class AIInboxGroupActionRouteTests(unittest.TestCase):
    def test_confirmed_main_trash_request_snapshots_the_complete_group(self) -> None:
        snapshot = {
            "matter": {"revision": 7},
            "message_ids": ["message-1", "message-2", "message-3"],
            "thread_ids": ["thread-1", "thread-2"],
        }
        payload = MatterEntityActionRequest(
            client_action_id="client-action-1",
            matter_id="matter-1",
            action="move_trash",
            expected_revision=7,
            confirm_multi_thread_trash=True,
        )
        with (
            patch.object(
                ai_inbox_routes,
                "require_current_user",
                return_value=SimpleNamespace(id="user-1"),
            ),
            patch.object(
                ai_inbox_routes,
                "active_member_snapshot",
                return_value=snapshot,
            ),
            patch.object(ai_inbox_routes, "enqueue_matter_action") as enqueue,
        ):
            response = ai_inbox_routes.matter_entity_action(SimpleNamespace(), payload)

        enqueue.assert_called_once_with(
            ai_inbox_routes.settings,
            user_id="user-1",
            client_action_id="client-action-1",
            matter_id="matter-1",
            action="move_trash",
            message_ids=["message-1", "message-2", "message-3"],
        )
        self.assertEqual(response.target_message_ids, snapshot["message_ids"])
        self.assertEqual(response.affected_thread_count, 2)
        self.assertEqual(response.state, "queued")

    def test_main_trash_request_resolves_the_group_shown_in_local_shadow_preview(self) -> None:
        snapshot = {
            "matter": {"revision": 4},
            "message_ids": ["message-1", "message-2"],
            "thread_ids": ["thread-1", "thread-2"],
        }
        payload = MatterEntityActionRequest(
            client_action_id="client-action-shadow",
            matter_id="matter-shadow",
            action="move_trash",
            expected_revision=4,
            confirm_multi_thread_trash=True,
        )
        with (
            patch.object(
                ai_inbox_routes,
                "require_current_user",
                return_value=SimpleNamespace(id="user-1"),
            ),
            patch.object(
                ai_inbox_routes,
                "active_member_snapshot",
                side_effect=[None, snapshot],
            ) as active_snapshot,
            patch.object(
                ai_inbox_routes,
                "_local_shadow_generation_id",
                return_value="generation-shadow",
            ),
            patch.object(ai_inbox_routes, "enqueue_matter_action") as enqueue,
        ):
            response = ai_inbox_routes.matter_entity_action(SimpleNamespace(), payload)

        self.assertEqual(active_snapshot.call_args_list[1].kwargs["generation_id"], "generation-shadow")
        enqueue.assert_called_once_with(
            ai_inbox_routes.settings,
            user_id="user-1",
            client_action_id="client-action-shadow",
            matter_id="matter-shadow",
            action="move_trash",
            message_ids=["message-1", "message-2"],
        )
        self.assertEqual(response.target_message_ids, snapshot["message_ids"])

if __name__ == "__main__":
    unittest.main()
