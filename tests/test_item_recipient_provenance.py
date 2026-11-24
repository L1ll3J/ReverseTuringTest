"""Personal item observations retain their experiencer across deferred application."""
from dataclasses import replace
import unittest

import test_item_integration as integration
from game_rules import ItemRuleError, MUFFLE_EXPIRED, MUFFLE_PRIVATE, THROW_VOICE_PRIVATE


class ItemRecipientProvenanceTests(unittest.TestCase):
    setUpClass = integration.ItemIntegrationTests.__dict__["setUpClass"]
    setUp = integration.ItemIntegrationTests.setUp
    tearDown = integration.ItemIntegrationTests.tearDown
    conversation = integration.ItemIntegrationTests.conversation
    wait_for_workers = integration.ItemIntegrationTests.wait_for_workers

    def pending(self, item_id, message=None):
        self.conversation()
        window = self.window
        target = window.body_id("Dave")
        mind = window.mind_id("Dave")
        data = window.mind_data[mind]
        entry = window.inventory.grant(item_id)
        events = window.item_rules.use_item(entry.instance_id, window.item_use_context(), target,
                                            message=message, activation_id="pending")
        self.assertEqual(events[0].recipient_mind_id, mind)
        self.assertEqual(events[0].target_name, window.perceived_name(target))
        return entry, events, mind, data

    def cycle(self, change_voice_face=False):
        window = self.window
        window.exchange_player_mind(window.body_id("Dave"))
        if change_voice_face:
            window.identity_state = window.identity_state.clone_appearance(window.body_id("Dave"), window.body_id("Hank"))
        window.exchange_player_mind(window.body_id("Hank"))
        window.exchange_player_mind(window.body_id("Alex"))

    def recover(self, entry, message=None):
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave"),
                                                       message=message, activation_id="pending"))
        self.wait_for_workers()

    def test_love_recovery_follows_original_recipient_mind_after_body_cycle(self):
        entry, events, mind, data = self.pending("love_potion")
        self.cycle()
        self.assertEqual(self.window.label_for_mind(mind), "Hank")
        self.recover(entry)
        self.assertIs(self.window.characters["Hank"], data)
        self.assertEqual(data["love_history"].doses[0].giver, "Alex")
        self.assertNotIn("love_history", self.window.characters["Dave"])
        self.recover(entry)
        self.assertEqual(len(data["love_history"].doses), 1)
        self.assertEqual(self.window.items_used, 1)

    def test_banana_disposition_follows_mind_but_public_event_keeps_original_body_name(self):
        entry, events, mind, data = self.pending("banana")
        self.cycle()
        self.recover(entry)
        self.assertEqual([record.text for record in data["dispositions"]], [events[0].disposition_text])
        self.assertFalse(self.window.characters["Dave"]["dispositions"])
        self.assertIn("Alex gave Dave a banana.", [record.text for record in self.window.public_events])
        self.recover(entry)
        self.assertEqual(len(data["dispositions"]), 1)
        self.assertEqual(self.window.profile.counters["banana_given"], 1)

    def test_divine_observation_follows_the_original_experiencer(self):
        message = "A private message."
        entry, events, mind, data = self.pending("divine_revelation", message)
        self.cycle()
        self.recover(entry, message)
        self.assertEqual([record.text for record in data["experiences"]], [events[0].private_text])
        self.assertFalse(self.window.characters["Dave"]["experiences"])
        self.recover(entry, message)
        self.assertEqual(len(data["experiences"]), 1)

    def test_muffle_stays_physical_and_expiry_observation_follows_actual_boundary_experiencer(self):
        entry, events, mind, original_data = self.pending("muffle")
        target = self.window.body_id("Dave")
        self.cycle()
        self.recover(entry)
        self.assertEqual(original_data["experiences"][-1].text, MUFFLE_PRIVATE)
        self.assertTrue(self.window.item_rules.is_muffled(target))
        self.assertFalse(self.window.item_rules.is_muffled(self.window.body_id("Hank")))
        expiry_mind = self.window.mind_id("Dave")
        expiry_data = self.window.mind_data[expiry_mind]
        self.window.round_number = 2
        expired = self.window.item_rules.begin_night(2, self.window.body_states())
        self.assertEqual(expired[0].recipient_mind_id, expiry_mind)
        self.assertNotEqual(expiry_mind, mind)
        self.cycle()
        self.window.apply_item_events(expired)
        self.assertFalse(self.window.item_rules.is_muffled(target))
        self.assertEqual(expiry_data["experiences"][-1].text, MUFFLE_EXPIRED)
        self.assertFalse(any(record.text == MUFFLE_EXPIRED for record in original_data["experiences"]))
        self.window.apply_item_events(expired)
        self.assertEqual(sum(record.text == MUFFLE_EXPIRED for record in expiry_data["experiences"]), 1)

    def test_muffle_expiry_can_be_archived_for_the_player_mind(self):
        entry, events, mind, data = self.pending("muffle")
        self.window.apply_item_events(events)
        self.window.exchange_player_mind(self.window.body_id("Dave"))
        self.window.round_number = 2
        expired = self.window.item_rules.begin_night(2, self.window.body_states())
        self.assertEqual(expired[0].recipient_mind_id, self.window.player_owner_id)
        self.window.apply_item_events(expired)
        archive = self.window.mind_data[self.window.player_owner_id]
        self.assertEqual(archive["experiences"][-1].text, MUFFLE_EXPIRED)
        self.assertFalse(any(record.text == MUFFLE_EXPIRED for record in data["experiences"]))

    def test_projected_voice_private_victim_is_mind_bound_and_public_speaker_is_captured_body(self):
        window = self.window
        window.identity_state = window.identity_state.clone_appearance(window.body_id("Dave"), window.body_id("Debra"))
        message = "I heard a noise."
        entry, events, mind, data = self.pending("throw_voice", message)
        self.assertEqual(events[0].target_name, "Debra")
        self.cycle(change_voice_face=True)
        self.assertEqual(window.perceived_name(window.body_id("Dave")), "Hank")
        self.recover(entry, message)
        self.assertEqual(data["experiences"][-1].text, THROW_VOICE_PRIVATE.format(message=message))
        self.assertFalse(window.characters["Dave"]["experiences"])
        self.assertEqual(window.public_dialogue[-1].speaker, "Dave")
        self.assertEqual(window.recalled_dialogue()[-1].speaker, "Debra")
        record = next(record for record in reversed(window.history_records) if record.kind == "speech")
        self.assertEqual((record.speaker, record.speaker_id), ("Debra", window.body_id("Dave")))
        self.assertNotIn("#", window.recalled_dialogue()[-1].speaker)
        count = len(window.public_dialogue)
        self.recover(entry, message)
        self.assertEqual(len(window.public_dialogue), count)
        self.assertEqual(len(data["experiences"]), 1)
        window.identity_state = window.identity_state.clone_appearance(window.body_id("Dave"), window.body_id("Jerry"))
        self.assertEqual(record.speaker, "Debra")

    def test_immediate_projected_voice_keeps_current_ui_alias_but_canonical_npc_name(self):
        window = self.window
        window.identity_state = window.identity_state.clone_appearance(window.body_id("Dave"), window.body_id("Debra"))
        entry, events, mind, data = self.pending("throw_voice", "A voice.")
        expected = window.display_actor("Dave")
        window.apply_item_events(events)
        record = next(record for record in reversed(window.history_records) if record.kind == "speech")
        self.assertEqual(record.speaker, expected)
        self.assertIn("#", record.speaker)
        self.assertEqual(window.recalled_dialogue()[-1].speaker, "Debra")

    def test_dead_recipient_keeps_its_personal_observation_in_its_archive(self):
        entry, events, mind, data = self.pending("divine_revelation", "Before death.")
        self.cycle()
        self.window.resolve_death("Hank", "recipient-died")
        self.assertFalse(data["alive"])
        self.recover(entry, "Before death.")
        self.assertEqual(data["experiences"][-1].text, events[0].private_text)
        self.assertFalse(self.window.characters["Dave"]["experiences"])
        self.recover(entry, "Before death.")
        self.assertEqual(len(data["experiences"]), 1)

    def test_unknown_captured_mind_rejects_before_receipt_and_legacy_uses_body_fallback(self):
        entry, events, mind, data = self.pending("divine_revelation", "A captured observation.")
        self.cycle()
        invalid = replace(events[0], recipient_mind_id="unknown-mind")
        with self.assertRaises(ItemRuleError):
            self.window.apply_item_events((invalid,))
        self.assertNotIn(invalid.event_id, self.window.applied_item_events)
        self.assertFalse(data["experiences"])
        self.assertFalse(self.window.characters["Dave"]["experiences"])
        legacy = replace(events[0], recipient_mind_id=None, event_id=events[0].event_id + ":legacy")
        self.window.apply_item_events((legacy,))
        self.assertFalse(data["experiences"])
        self.assertEqual(self.window.characters["Dave"]["experiences"][-1].text, events[0].private_text)
        self.window.apply_item_events((events[0],))
        with self.assertRaises(ItemRuleError):
            self.window.apply_item_events((invalid,))
        self.assertEqual(len(data["experiences"]), 1)
