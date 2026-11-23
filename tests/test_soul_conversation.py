"""Live Soul reactions, draft recovery and body-stable reply obligations."""

import unittest
from threading import Event
from unittest.mock import patch

import test_conversation_integration as fixtures
from game_rules import SOUL_PRIVATE


class SoulConversationTests(unittest.TestCase):
    setUpClass = fixtures.ConversationIntegrationTests.__dict__["setUpClass"]
    setUp = fixtures.ConversationIntegrationTests.setUp
    tearDown = fixtures.ConversationIntegrationTests.tearDown
    wait_for = fixtures.ConversationIntegrationTests.wait_for
    reply = staticmethod(fixtures.ConversationIntegrationTests.reply)
    prepare = fixtures.ConversationIntegrationTests.prepare
    block_proposals = fixtures.ConversationIntegrationTests.block_proposals

    def use_soul(self, target="Dave", activation="soul-response"):
        entry = self.window.inventory.grant("soul")
        target_body = self.window.body_id(target)
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target_body,
                                                      activation_id=activation))
        return entry, target_body

    def test_swap_recovers_orphaned_composer_and_preserves_unsent_draft(self):
        window = self.window
        window.append_speech("Jerry", "Alex, what did you see?", "Alex")
        window.run_conversation_loop()
        self.assertTrue(window.player_reply_required())
        window.interject_input.setText("My unfinished account.")
        with patch.object(window, "run_single_response") as response:
            self.use_soul()
        response.assert_called_once_with("Alex")
        self.assertFalse(window.player_reply_required())
        self.assertTrue(window.pass_button.isEnabled())
        self.assertTrue(window.interject_button.isEnabled())
        self.assertTrue(window.interject_input.isHidden())
        window.interject_conversation()
        self.assertEqual(window.interject_input.text(), "My unfinished account.")

    def test_pre_swap_question_to_npc_does_not_become_human_reply_obligation(self):
        window = self.window
        window.append_speech("Jerry", "Dave, what did you see?", "Dave")
        with patch.object(window, "run_single_response"):
            self.use_soul()
        self.assertEqual(window.player_name, "Dave")
        self.assertFalse(window.player_reply_required())
        self.assertTrue(window.pass_button.isEnabled())

    def test_displaced_npc_responds_first_with_its_own_experience_and_no_selector(self):
        window = self.window
        window.append_speech("Jerry", "An earlier account.")
        self.use_soul()
        self.wait_for(lambda: not window.active_workers)
        self.assertEqual([call.args[0].character for call in self.speech.call_args_list], ["Alex"])
        context = self.speech.call_args.args[0]
        self.assertEqual(context.self_identity.name, "Dave")
        self.assertEqual(context.self_identity.body, "Alex")
        self.assertIn(SOUL_PRIVATE.format(new_body="Alex", original_name="Dave"),
                      [record.text for record in context.experiences])
        self.selector.assert_not_called()
        self.assertEqual(window.public_dialogue[-1].speaker, "Alex")
        self.assertFalse(window.ai_busy)
        self.assertTrue(window.pass_button.isEnabled())
        self.assertTrue(window.interject_button.isEnabled())

    def test_only_new_question_to_current_human_body_requires_reply(self):
        window = self.window
        self.use_soul()
        self.wait_for(lambda: not window.active_workers)
        window.append_speech("Jerry", "Alex, what happened?", "Alex")
        window.run_conversation_loop()
        self.assertFalse(window.player_reply_required())
        self.assertTrue(window.pass_button.isEnabled())
        self.assertTrue(window.conversation_stage.reply_notice.isHidden())
        window.append_speech("Jerry", "Dave, what happened?", "Dave")
        window.run_conversation_loop()
        self.assertTrue(window.player_reply_required())
        self.assertFalse(window.pass_button.isEnabled())
        self.assertTrue(window.interject_button.isEnabled())
        self.assertFalse(window.interject_input.isHidden())
        self.assertFalse(window.conversation_stage.reply_notice.isHidden())
        self.assertIn(window.display_actor(window.player_name), window.conversation_stage.reply_notice.text())

    def test_soul_replay_does_not_repeat_reaction_or_reopen_old_obligation(self):
        entry, target = self.use_soul()
        self.wait_for(lambda: not self.window.active_workers)
        before = tuple(self.window.public_dialogue)
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target,
                                                      activation_id="soul-response"))
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.assertEqual(self.speech.call_count, 1)

    def test_failed_reaction_is_retryable_without_stranding_controls(self):
        self.speech.side_effect = fixtures.game.AIRequestError("Offline failure")
        self.use_soul()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertFalse(self.window.ai_busy)
        self.assertTrue(self.window.pass_button.isEnabled())
        self.assertTrue(self.window.interject_button.isEnabled())
        self.assertIsNotNone(self.window.retry_action)
        first_context = self.speech.call_args.args[0]
        self.speech.side_effect = self.reply
        self.window.retry_ai_request()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertIs(self.speech.call_args.args[0], first_context)
        self.assertEqual(self.window.public_dialogue[-1].speaker, "Alex")
        self.assertTrue(self.window.pass_button.isEnabled())

    def test_muffled_displaced_body_does_not_gain_an_open_floor_response(self):
        window = self.window
        muffle = window.inventory.grant("muffle")
        self.assertTrue(window.use_inventory_item(muffle.instance_id, window.body_id("Dave")))
        window.exchange_player_mind(window.body_id("Dave"))
        self.assertTrue(window.player_is_muffled())
        self.use_soul("Jerry")
        self.assertEqual(window.player_name, "Jerry")
        self.assertTrue(window.item_rules.is_muffled(window.body_id("Dave")))
        self.speech.assert_not_called()
        self.assertFalse(window.ai_busy)
        self.assertTrue(window.pass_button.isEnabled())
        self.assertTrue(window.interject_button.isEnabled())

    def test_changed_context_discards_reaction_and_restores_player_controls(self):
        entered, release = Event(), Event()
        self.releases.append(release)

        def delayed(context):
            entered.set()
            if not release.wait(4):
                raise fixtures.game.AIRequestError("Test response was not released")
            return self.reply(context)

        self.speech.side_effect = delayed
        self.use_soul()
        self.wait_for(entered.is_set)
        self.window.remember("Alex", "experience", "A later experience invalidates the old reply.")
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertFalse(self.window.ai_busy)
        self.assertTrue(self.window.pass_button.isEnabled())
        self.assertTrue(self.window.interject_button.isEnabled())
        self.assertFalse(self.window.public_dialogue)

    def test_pre_swap_preparation_cannot_replace_the_displaced_npcs_first_response(self):
        self.window.append_speech("Jerry", "My earlier account.")
        release, _seen = self.block_proposals()
        self.preparation_paused = True
        self.speech.side_effect = self.reply
        self.use_soul()
        self.wait_for(lambda: self.window.public_dialogue[-1].speaker == "Alex")
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual([line.speaker for line in self.window.public_dialogue], ["Jerry", "Alex"])
        self.selector.assert_not_called()
        self.assertFalse(self.window.ai_busy)
        self.assertTrue(self.window.pass_button.isEnabled())
        self.assertTrue(self.window.interject_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
