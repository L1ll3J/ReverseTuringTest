"""Real inventory, private ownership and next-night Soul achievement checks."""

from dataclasses import replace
from xml.etree import ElementTree as ET
import unittest
from unittest.mock import patch

import test_runtime_regressions as runtime
from ai_context import MemoryRecord, render_character_context
from game_rules import SOUL_PRIVATE


class SoulIntegrationTests(unittest.TestCase):
    setUpClass = runtime.RuntimeRegressionTests.__dict__["setUpClass"]
    tearDown = runtime.RuntimeRegressionTests.tearDown

    def setUp(self):
        runtime.RuntimeRegressionTests.setUp(self)
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.ensure_identity_state()
        self.patches.enter_context(patch.object(self.window, "run_single_response"))

    def transfer(self, target="Dave", activation="transfer"):
        entry = self.window.inventory.grant("soul")
        old_body = self.window.body_id(self.window.player_name)
        target_body = self.window.body_id(target)
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target_body, activation_id=activation))
        return entry, old_body, target_body

    def prepare_next_night(self, day=2):
        self.window.round_number = day
        self.window.phase = "action"
        self.window.room_assignments = {room: [] for room in self.window.rooms}
        self.window.room_assignments["Attic"] = [name for name, data in self.window.characters.items() if data["alive"]]
        self.window.new_crime_scenes = []

    def test_live_transfer_keeps_inventory_and_private_personality_but_leaves_weapon_and_guardian(self):
        displaced = self.window.characters["Dave"]
        remembered = MemoryRecord(1, "experience", "An earlier experience.", "self")
        displaced["experiences"].append(remembered)
        banana = self.window.inventory.grant("banana")
        guardian = self.window.inventory.grant("guardian_angel")
        self.assertTrue(self.window.use_inventory_item(guardian.instance_id))
        knife = next(weapon for weapon in self.window.weapons if weapon["id"] == "knife")
        self.window.selected_weapon = knife
        entry, old_body, new_body = self.transfer()
        self.assertEqual(self.window.player_name, "Dave")
        self.assertIs(self.window.characters["Alex"], displaced)
        self.assertIs(displaced["experiences"][0], remembered)
        self.assertEqual(displaced["experiences"][-1].text,
                         SOUL_PRIVATE.format(new_body="Alex", original_name="Dave"))
        self.assertEqual(self.window.inventory.entries, (banana,))
        self.assertEqual(self.window.body_weapons[old_body], knife)
        self.assertEqual(self.window.selected_weapon["id"], "bare")
        self.assertTrue(self.window.item_rules.guardian_protects(old_body))
        self.assertFalse(self.window.item_rules.guardian_protects(new_body))
        self.assertFalse(self.window.resolve_death("Alex", "old-body-guardian").killed)
        self.assertTrue(self.window.resolve_death("Dave", "new-body-unprotected").killed)

    def test_self_identity_survives_a_wipe_without_renaming_history(self):
        self.window.append_speech("Dave", "Before the exchange.")
        earlier = tuple(self.window.history_records)
        self.transfer()
        context = self.window.build_character_context("Alex")
        identity = ET.fromstring(render_character_context(context)).find("self")
        self.assertEqual(identity.attrib, {"name": "Dave", "body": "Alex", "reference": "Alex"})
        self.assertEqual(tuple(self.window.history_records[:len(earlier)]), earlier)
        self.window.clear_npc_recollections()
        context = self.window.build_character_context("Alex")
        self.assertEqual(context.self_identity.name, "Dave")
        self.assertFalse(context.experiences)
        self.assertFalse(context.dialogue)

    def test_same_activation_replays_after_body_change_without_second_transfer_or_memory(self):
        entry, old_body, target_body = self.transfer()
        identity = self.window.identity_state
        memories = tuple(self.window.characters["Alex"]["experiences"])
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target_body, activation_id="transfer"))
        self.assertEqual(self.window.identity_state, identity)
        self.assertEqual(tuple(self.window.characters["Alex"]["experiences"]), memories)
        self.assertEqual(self.window.items_used, 1)
        self.assertEqual(len(self.window.soul_former_bodies), 1)
        self.assertFalse(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Hank"),
                                                      activation_id="transfer"))
        self.assertEqual(self.window.identity_state, identity)
        self.window.exchange_player_mind(self.window.body_id("Hank"))
        later = self.window.identity_state
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target_body, activation_id="transfer"))
        self.assertEqual(self.window.identity_state, later)

    def test_recovered_engine_result_can_be_the_first_world_application(self):
        entry = self.window.inventory.grant("soul")
        target = self.window.body_id("Dave")
        self.window.item_rules.use_item(entry.instance_id, self.window.item_use_context(), target,
                                        activation_id="lost-result")
        self.assertEqual(self.window.player_name, "Alex")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target, activation_id="lost-result"))
        self.assertEqual(self.window.player_name, "Dave")
        self.assertEqual(len(self.window.characters["Alex"]["experiences"]), 1)

    def test_missing_identity_rejects_before_consuming_soul(self):
        self.window.identity_state = replace(self.window.identity_state, mind_names=())
        entry = self.window.inventory.grant("soul")
        self.assertFalse(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertIn(entry, self.window.inventory.entries)
        self.assertEqual(self.window.player_name, "Alex")

    def test_personal_kill_of_former_body_the_following_night_unlocks_voodoo(self):
        self.transfer()
        self.assertNotIn("voodoo", self.window.profile.unlocked)
        self.prepare_next_night()
        self.assertTrue(self.window.commit_attack("Alex", "Attic"))
        self.assertIn("voodoo", self.window.profile.unlocked)
        self.assertEqual(self.window.profile.counters["former_body_killed_following_night"], 1)
        self.assertFalse(self.window.commit_attack("Alex", "Attic"))
        self.assertEqual(self.window.profile.counters["former_body_killed_following_night"], 1)

    def test_same_appearance_is_not_the_former_body(self):
        _entry, old_body, _target = self.transfer()
        self.window.identity_state = self.window.identity_state.clone_appearance(self.window.body_id("Jerry"), old_body)
        self.prepare_next_night()
        self.assertTrue(self.window.commit_attack("Jerry", "Attic"))
        self.assertNotIn("voodoo", self.window.profile.unlocked)
        self.assertTrue(self.window.commit_attack("Alex", "Attic"))
        self.assertIn("voodoo", self.window.profile.unlocked)

    def test_later_night_does_not_meet_the_following_night_condition(self):
        self.transfer()
        self.prepare_next_night(day=3)
        self.assertTrue(self.window.commit_attack("Alex", "Attic"))
        self.assertNotIn("voodoo", self.window.profile.unlocked)

    def test_controlled_npc_kills_do_not_meet_the_personal_condition(self):
        self.transfer()
        self.prepare_next_night()
        self.assertTrue(self.window.commit_attack("Alex", "Attic", attacker="Hank", weapon_id="blackmagic"))
        self.assertNotIn("voodoo", self.window.profile.unlocked)

    def test_guardian_prevention_does_not_complete_the_former_body_kill(self):
        guardian = self.window.inventory.grant("guardian_angel")
        self.assertTrue(self.window.use_inventory_item(guardian.instance_id))
        self.transfer()
        self.prepare_next_night()
        self.assertFalse(self.window.commit_attack("Alex", "Attic"))
        self.assertNotIn("voodoo", self.window.profile.unlocked)
        self.assertTrue(self.window.commit_attack("Alex", "Attic"))
        self.assertIn("voodoo", self.window.profile.unlocked)


if __name__ == "__main__":
    unittest.main()
