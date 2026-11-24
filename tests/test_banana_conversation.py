"""Offline gift reactions through the normal conversation request lifecycle."""

import unittest
from threading import Event
from unittest.mock import patch

import test_conversation_integration as fixtures
from game_rules import BANANA_DISPOSITION
from muffled import muffled_sound


class BananaConversationTests(unittest.TestCase):
    setUpClass = fixtures.ConversationIntegrationTests.__dict__["setUpClass"]
    setUp = fixtures.ConversationIntegrationTests.setUp
    tearDown = fixtures.ConversationIntegrationTests.tearDown
    wait_for = fixtures.ConversationIntegrationTests.wait_for
    reply = staticmethod(fixtures.ConversationIntegrationTests.reply)

    def give(self, target="Dave", activation="banana-reaction"):
        entry = self.window.inventory.grant("banana")
        body = self.window.body_id(target)
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, body, activation_id=activation))
        return entry, body

    def test_recipient_reacts_with_gift_memory_closes_inventory_and_returns_control(self):
        window = self.window
        window.append_speech("Jerry", "An earlier account.")
        window.interject_input.setText("My unfinished account.")
        window.open_inventory()
        self.give()
        self.assertIsNone(window.inventory_page)
        self.wait_for(lambda: not window.active_workers)
        self.assertEqual([call.args[0].character for call in self.speech.call_args_list], ["Dave"])
        context = self.speech.call_args.args[0]
        self.assertIn(BANANA_DISPOSITION.format(giver="Alex", day=1),
                      [record.text for record in context.dispositions])
        self.assertIn("Alex gave Dave a banana.", [record.text for record in context.public_events])
        self.selector.assert_not_called()
        self.assertEqual(window.public_dialogue[-1].speaker, "Dave")
        self.assertTrue(window.pass_button.isEnabled())
        self.assertTrue(window.interject_button.isEnabled())
        self.assertEqual(window.interject_input.text(), "My unfinished account.")

    def test_existing_required_answer_survives_an_unaddressed_reaction(self):
        window = self.window
        window.append_speech("Jerry", "Alex, where were you?", "Alex")
        anchor = window.required_reply_anchor
        self.give()
        self.wait_for(lambda: not window.active_workers)
        self.assertEqual(window.required_reply_anchor, anchor)
        self.assertTrue(window.player_reply_required())
        self.assertFalse(window.pass_button.isEnabled())
        self.assertTrue(window.interject_button.isEnabled())
        self.assertFalse(window.interject_input.isHidden())
        self.assertEqual(self.speech.call_count, 1)

    def test_reaction_can_itself_require_an_answer(self):
        self.speech.side_effect = lambda context: fixtures.game.SpeechProposal("Alex", "Alex, why the banana?")
        self.give()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertTrue(self.window.player_reply_required())
        self.assertFalse(self.window.pass_button.isEnabled())

    def test_reaction_neither_charges_nor_resets_direct_reply_streak(self):
        window = self.window
        for _ in range(3):
            window.append_speech("Jerry", "Another answer.", direct_reply=True)
        self.give()
        self.wait_for(lambda: not window.active_workers)
        self.assertEqual(window.direct_replies.count_for(window.body_id("Jerry")), 3)
        self.assertEqual(window.direct_replies.count_for(window.body_id("Dave")), 0)
        self.assertEqual(window.public_dialogue[-1].speaker, "Dave")

    def test_replay_does_not_repeat_gift_or_reaction(self):
        entry, body = self.give()
        self.wait_for(lambda: not self.window.active_workers)
        dialogue, events = tuple(self.window.public_dialogue), tuple(self.window.public_events)
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, body, activation_id="banana-reaction"))
        self.assertEqual(tuple(self.window.public_dialogue), dialogue)
        self.assertEqual(tuple(self.window.public_events), events)
        self.assertEqual(self.speech.call_count, 1)
        self.assertEqual(self.window.items_used, 1)

    def test_capped_recipient_can_react_without_a_fourth_direct_reply(self):
        window = self.window
        for _ in range(3):
            window.append_speech("Dave", "Another answer.", direct_reply=True)
        self.give()
        self.wait_for(lambda: not window.active_workers)
        self.assertEqual(window.public_dialogue[-1].speaker, "Dave")
        self.assertEqual(window.direct_replies.count_for(window.body_id("Dave")), 3)
        self.assertEqual(self.speech.call_count, 1)

    def test_failed_reaction_retries_same_context_with_required_answer_intact(self):
        window = self.window
        window.append_speech("Jerry", "Alex, your account?", "Alex")
        self.speech.side_effect = fixtures.game.AIRequestError("Offline failure")
        self.give()
        self.wait_for(lambda: not window.active_workers)
        self.assertIsNotNone(window.retry_action)
        self.assertTrue(window.player_reply_required())
        self.assertFalse(window.pass_button.isEnabled())
        context = self.speech.call_args.args[0]
        self.speech.side_effect = self.reply
        window.retry_ai_request()
        self.wait_for(lambda: not window.active_workers)
        self.assertIs(self.speech.call_args.args[0], context)
        self.assertEqual(window.public_dialogue[-1].speaker, "Dave")
        self.assertTrue(window.player_reply_required())
        self.assertEqual(window.items_used, 1)

    def test_muffled_recipient_uses_one_tone_request_without_selector(self):
        window = self.window
        muffle = window.inventory.grant("muffle")
        self.assertTrue(window.use_inventory_item(muffle.instance_id, window.body_id("Dave")))
        with patch.object(window.ai_runtime, "muffled_tone", return_value=-1) as tone:
            self.give()
            self.wait_for(lambda: not window.active_workers)
        tone.assert_called_once()
        self.speech.assert_not_called()
        self.selector.assert_not_called()
        self.assertEqual(window.public_dialogue[-1].text, muffled_sound("fallback", -1))
        self.assertIsNone(window.public_dialogue[-1].addressee)
        self.assertTrue(window.pass_button.isEnabled())

    def test_context_change_discards_async_reaction_without_repeating_gift(self):
        entered, release = Event(), Event()
        self.releases.append(release)
        def delayed(context):
            entered.set()
            if not release.wait(4):
                raise fixtures.game.AIRequestError("Response was not released")
            return self.reply(context)
        self.speech.side_effect = delayed
        self.give()
        self.wait_for(entered.is_set)
        self.window.remember("Dave", "experience", "A new experience invalidates the draft.")
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertFalse(self.window.public_dialogue)
        self.assertFalse(self.window.ai_busy)
        self.assertTrue(self.window.pass_button.isEnabled())
        self.assertEqual(self.window.items_used, 1)
        self.assertEqual(len(self.window.characters["Dave"]["dispositions"]), 1)

    def test_recipient_is_current_occupant_after_a_previous_body_exchange(self):
        window = self.window
        window.exchange_player_mind(window.body_id("Dave"))
        self.give("Alex")
        self.wait_for(lambda: not window.active_workers)
        context = self.speech.call_args.args[0]
        self.assertEqual(context.character, "Alex")
        self.assertEqual(context.self_identity.name, "Dave")
        self.assertEqual(context.self_identity.body, "Alex")
        self.assertIn(BANANA_DISPOSITION.format(giver="Dave", day=1),
                      [record.text for record in context.dispositions])

    def test_ordinary_retry_still_cannot_continue_past_a_required_answer(self):
        window = self.window
        self.speech.side_effect = fixtures.game.AIRequestError("Offline failure")
        window.run_single_response("Dave")
        self.wait_for(lambda: not window.active_workers)
        retry = window.retry_action
        self.assertIsNotNone(retry)
        window.append_speech("Jerry", "Alex, your account?", "Alex")
        # Even an obsolete retry callback cannot bypass the ordinary request guard.
        window.retry_action = retry
        window.retry_ai_request()
        self.assertEqual(self.speech.call_count, 1)
        self.assertEqual(window.public_dialogue[-1].speaker, "Jerry")
        self.assertTrue(window.player_reply_required())


if __name__ == "__main__":
    unittest.main()
