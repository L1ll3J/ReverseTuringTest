"""Offline recall boundaries, retained effects and real item unlock integration."""
import unittest
from pathlib import Path
from unittest.mock import patch

import test_item_integration as integration
from ai_context import CurrentFeeling, FeelingKind, MemoryRecord, SpeechProposal, render_character_context, render_selector_context
from game_rules import NightMemoryView
from love_potion import PAST_INFATUATION, PAST_HANGOVER


class MassMemoryTests(unittest.TestCase):
    setUpClass = integration.ItemIntegrationTests.__dict__["setUpClass"]
    setUp = integration.ItemIntegrationTests.setUp
    tearDown = integration.ItemIntegrationTests.tearDown
    conversation = integration.ItemIntegrationTests.conversation

    def activate(self, item_id, target=None, **kwargs):
        entry = self.window.inventory.grant(item_id)
        self.assertTrue(self.window.use_inventory_item(
            entry.instance_id, self.window.body_id(target) if target else None, **kwargs))
        return entry

    def test_real_item_clears_npc_context_selector_and_old_night_view_preserving_history(self):
        self.conversation()
        window = self.window
        trace = integration.TraceRun(enabled=True, root=Path(self.temp.name) / "traces")
        trace.start({})
        window.trace = trace
        window.remember("Jerry", "experience", "OLD_PRIVATE_SENTINEL")
        window.remember("Jerry", "disposition", "OLD_DISPOSITION_SENTINEL")
        window.add_public_event("OLD_PUBLIC_SENTINEL")
        window.append_speech("Dave", "OLD_DIALOGUE_SENTINEL")
        persona = tuple(window.characters["Jerry"]["profile"])
        history = tuple(window.history_records)
        public_events, dialogue = tuple(window.public_events), tuple(window.public_dialogue)
        old_context = window.build_character_context("Jerry")
        window.night_memory_views = (NightMemoryView(window.body_id("Jerry"), 1,
            (MemoryRecord(1, "experience", "OLD_NIGHT_SENTINEL", "self"),)),)
        entry = window.inventory.grant("mass_discombobulator")
        window.open_inventory()
        page = window.inventory_page
        self.assertTrue(page.use_buttons[entry.instance_id].isEnabled())
        page.use_buttons[entry.instance_id].click()
        self.assertNotIn(entry, window.inventory.entries)
        window.trace_state("after_memory_wipe")
        self.assertTrue(trace.enabled)
        self.assertIn("OLD_PRIVATE_SENTINEL", trace.run_dir.joinpath("events.jsonl").read_text(encoding="utf-8"))
        context = window.build_character_context("Jerry")
        self.assertEqual(context.persona, persona)
        self.assertEqual(context.state, old_context.state)
        self.assertFalse(context.experiences)
        self.assertFalse(context.dispositions)
        self.assertFalse(context.public_events)
        self.assertFalse(context.dialogue)
        self.assertEqual(tuple(window.history_records[:len(history)]), history)
        self.assertEqual(tuple(window.public_events), public_events)
        self.assertEqual(tuple(window.public_dialogue), dialogue)
        self.assertIn("OLD_PRIVATE_SENTINEL", window.characters["Jerry"]["experiences"][0].text)
        self.assertFalse(window.night_memory_views)
        snapshot = window.group_turn_snapshot()
        self.assertFalse(snapshot.public_events)
        self.assertFalse(snapshot.dialogue)
        window.remember("Jerry", "experience", "NEW_PRIVATE_SENTINEL")
        window.add_public_event("NEW_PUBLIC_SENTINEL")
        window.append_speech("Alex", "NEW_DIALOGUE_SENTINEL")
        xml = render_character_context(window.build_character_context("Jerry"))
        selector = render_selector_context(*window.group_turn_snapshot().selector_args, {})
        self.assertIn("NEW_PRIVATE_SENTINEL", xml)
        self.assertIn("NEW_PUBLIC_SENTINEL", selector)
        self.assertIn("NEW_DIALOGUE_SENTINEL", selector)
        self.assertNotIn("OLD_", xml)
        self.assertNotIn("OLD_", selector)
        window.round_number = 2
        self.assertNotIn("OLD_", render_character_context(window.build_character_context("Jerry")))

    def test_completed_love_history_stays_forgotten_active_feeling_and_new_stages_survive(self):
        self.conversation()
        window = self.window
        self.activate("love_potion", "Jerry")
        window.round_number = 2
        window.advance_love_histories(discussion=False)
        window.advance_love_histories(discussion=True)
        before = window.characters["Jerry"]["love_history"]
        self.assertTrue(before.experiences.project().experiences)
        self.activate("mass_discombobulator")
        context = window.build_character_context("Jerry")
        self.assertFalse(context.experiences)
        self.assertEqual(context.current_feelings, (CurrentFeeling(FeelingKind.BITTERNESS, "Alex"),))
        window.round_number = 3
        window.advance_love_histories(discussion=False)
        window.advance_love_histories(discussion=True)
        recalled = window.build_character_context("Jerry").experiences
        self.assertEqual([record.text for record in recalled], [PAST_HANGOVER.format(giver="Alex")])
        self.assertEqual(recalled[0].round_number, 2)
        self.assertEqual(before.experiences.project().experiences[0].text, PAST_INFATUATION.format(giver="Alex"))
        self.activate("love_potion", "Jerry")
        window.round_number = 4
        window.advance_love_histories(discussion=False)
        self.assertEqual(len(window.build_character_context("Jerry").experiences), 2)

    def test_wipe_keeps_mechanics_physical_state_human_reply_and_rejects_prepared_payload(self):
        self.conversation()
        window = self.window
        for item_id, target, kwargs in (
                ("muffle", "Jerry", {}), ("stay_of_execution", "Jerry", {}),
                ("love_potion", "Jerry", {}), ("spare_paper", None, {}),
                ("guardian_angel", None, {}), ("booby_trap", None, {"room_id": "Kitchen"}),
                ("occult_ritual", "Dave", {})):
            self.activate(item_id, target, **kwargs)
        mechanics = (window.item_rules.muffles, window.item_rules.execution_stays,
                     window.item_rules.pending_ballot_bonus, window.item_rules.protection_armed,
                     window.item_rules.traps, window.item_rules.curses, window.body_states())
        window.append_speech("Jerry", "A remembered question?", "Alex")
        anchor = window.required_reply_anchor
        snapshot = window.group_turn_snapshot()
        ticket = window.turn_preparation.reserve(snapshot)
        self.assertTrue(window.turn_preparation.publish(ticket, snapshot, "Dave", SpeechProposal(None, "STALE_PAYLOAD")))
        serial = window.request_serial
        self.activate("mass_discombobulator")
        self.assertGreater(window.request_serial, serial)
        self.assertTrue(ticket.cancelled.is_set())
        self.assertFalse(window.consume_prepared_turn(snapshot))
        self.assertFalse(window.turn_preparation.publish(ticket, snapshot, "Dave", SpeechProposal(None, "STALE_PAYLOAD")))
        self.assertEqual(window.required_reply_anchor, anchor)
        self.assertTrue(window.player_reply_required())
        self.assertIsNone(window.addressed_muffled_npc())
        self.assertFalse(window.can_prepare_next_turn())
        self.assertEqual(mechanics, (window.item_rules.muffles, window.item_rules.execution_stays,
                     window.item_rules.pending_ballot_bonus, window.item_rules.protection_armed,
                     window.item_rules.traps, window.item_rules.curses, window.body_states()))
        self.assertEqual(window.build_character_context("Jerry").current_feelings,
                         (CurrentFeeling(FeelingKind.INFATUATION, "Alex"),))
        self.assertFalse(any(record.text == "STALE_PAYLOAD" for record in window.history_records))

    def test_stale_selector_retry_cannot_start_after_wipe_with_open_floor(self):
        self.conversation()
        window = self.window
        window.append_speech("Dave", "OLD_SELECTOR_SENTINEL")
        snapshot = window.group_turn_snapshot()
        old_retry = lambda: window.handle_multiple_responses(
            {"Dave": SpeechProposal(None, "STALE_PAYLOAD")}, snapshot.selector_args,
            context_guard=lambda: window.group_turn_snapshot() == snapshot)
        window.retry_action = old_retry
        self.activate("mass_discombobulator")
        self.assertFalse(window.player_reply_required())
        self.assertIsNone(window.retry_action)
        with patch.object(window.ai_runtime, "select_reply") as selector:
            old_retry()
            self.assertFalse(window.active_workers)
            selector.assert_not_called()
        self.assertFalse(any(record.text == "STALE_PAYLOAD" for record in window.history_records))

    def test_five_real_distinct_uses_unlock_once_and_acquisitions_do_not_count(self):
        self.conversation()
        window = self.window
        for item_id in ("banana", "muffle", "spare_paper", "love_potion", "room_reservation"):
            window.inventory.grant(item_id)
        self.assertNotIn("mass_discombobulator", window.profile_store.read().unlocked)
        for item_id, target, kwargs in (
                ("banana", "Jerry", {}), ("banana", "Dave", {}),
                ("spare_paper", None, {}), ("love_potion", "Jerry", {}),
                ("divine_revelation", "Dave", {"message": "A message."})):
            self.activate(item_id, target, **kwargs)
        self.assertNotIn("mass_discombobulator", window.profile_store.read().unlocked)
        self.activate("room_reservation", "Jerry", room_id="Kitchen")
        self.assertIn("mass_discombobulator", window.profile_store.read().unlocked)
        self.assertEqual(window.profile_store.read().counters["five_item_types_used"], 1)
        self.activate("muffle", "Dave")
        self.activate("mass_discombobulator")
        self.assertEqual(window.profile_store.read().counters["five_item_types_used"], 1)

    def test_recall_offsets_follow_npc_mind_through_exchange(self):
        self.conversation()
        window = self.window
        window.remember("Jerry", "experience", "BEFORE_EXCHANGE_SENTINEL")
        self.activate("mass_discombobulator")
        jerry_mind = window.mind_id("Jerry")
        window.exchange_player_mind(window.body_id("Jerry"))
        self.assertEqual(window.label_for_mind(jerry_mind), "Alex")
        self.assertFalse(window.build_character_context("Alex").experiences)
        window.remember("Alex", "experience", "AFTER_EXCHANGE_SENTINEL")
        context = window.build_character_context("Alex")
        self.assertEqual([record.text for record in context.experiences], ["AFTER_EXCHANGE_SENTINEL"])
        self.assertIn("BEFORE_EXCHANGE_SENTINEL", window.characters["Alex"]["experiences"][0].text)

    def test_memory_wipe_preserves_original_mind_names_after_exchange_and_copy(self):
        self.conversation()
        window = self.window
        original = window.identity_state
        player_mind = window.player_owner_id
        jerry_mind = window.mind_id("Jerry")
        self.assertEqual(original.mind_name(player_mind), "Alex")
        self.assertEqual(original.mind_name(jerry_mind), "Jerry")
        window.exchange_player_mind(window.body_id("Jerry"))
        window.identity_state = window.identity_state.clone_appearance(window.body_id("Alex"), window.body_id("Dave"))
        registry = window.identity_state
        window.remember("Alex", "experience", "A run recollection that can be forgotten.")
        self.activate("mass_discombobulator")
        self.assertIs(window.identity_state, registry)
        self.assertEqual(window.identity_state.mind_names, original.mind_names)
        self.assertEqual(window.identity_state.mind_name(player_mind), "Alex")
        self.assertEqual(window.identity_state.mind_name(jerry_mind), "Jerry")
        self.assertEqual(window.identity_state.body_for_mind(jerry_mind), window.body_id("Alex"))
        self.assertEqual(window.identity_state.appearance_name(window.body_id("Alex")), "Dave")
        self.assertFalse(window.build_character_context("Alex").experiences)


if __name__ == "__main__":
    unittest.main()
