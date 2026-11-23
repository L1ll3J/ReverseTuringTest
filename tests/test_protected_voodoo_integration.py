"""Live Voodoo protection, canonical witnesses and corpse reporting."""

import unittest

import test_runtime_regressions as runtime
from game_rules import ItemUseContext


class ProtectedVoodooIntegrationTests(unittest.TestCase):
    setUpClass = runtime.RuntimeRegressionTests.__dict__["setUpClass"]
    tearDown = runtime.RuntimeRegressionTests.tearDown

    def setUp(self):
        runtime.RuntimeRegressionTests.setUp(self)
        self.window.round_number = 1
        self.window.phase = "action"
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons
                                           if weapon["id"] == "voodoo")
        self.window.ensure_identity_state()
        self.window.room_assignments = {"Kitchen": ["Dave", "Hank"], "Attic": ["Jerry"]}
        self.window.rooms_visited = ["Kitchen"]
        self.window.new_crime_scenes = []

    def arm(self, handle):
        entry = self.window.inventory.grant("guardian_angel")
        self.window.item_rules.use_item(entry.instance_id, ItemUseContext(
            1, "conversation", False, self.window.player_owner_id,
            self.window.body_id(handle), self.window.body_states()))

    def write_memories(self):
        self.window.update_witness_memories(None)
        return self.window.characters["Hank"]["experiences"][-1].text

    def test_target_guardian_copies_appearance_without_exchanging_or_recording_a_death(self):
        self.arm("Dave")
        old = self.window.identity_state
        target_data = self.window.characters["Dave"]
        self.window.append_speech("Dave", "Before the attack.")
        old_record = self.window.history_records[-1]
        revision = self.window.conversation_revision
        self.assertFalse(self.window.commit_attack("Dave", "Kitchen"))
        self.assertEqual(self.window.identity_state.occupants, old.occupants)
        self.assertIs(self.window.characters["Dave"], target_data)
        self.assertEqual(self.window.player_name, "Alex")
        self.assertEqual(self.window.perceived_actor("Alex"), "Dave")
        self.assertEqual(self.window.perceived_actor("Dave"), "Dave")
        self.assertTrue(self.window.player_alive)
        self.assertTrue(self.window.characters["Dave"]["alive"])
        self.assertFalse(self.window.item_rules.guardian_protects(self.window.body_id("Dave")))
        self.assertGreater(self.window.conversation_revision, revision)
        self.assertEqual(self.window.victims_last_night, [])
        self.assertEqual(self.window.new_crime_scenes, [])
        self.assertEqual(self.window.profile.counters.get("player_kill", 0), 0)
        self.assertEqual(self.write_memories(),
                         "You saw Alex stab Dave. Dave survived. Afterwards, Alex also looked exactly like Dave.")
        self.assertFalse(any("body was found" in event.text for event in self.window.public_events))
        self.assertEqual((old_record.speaker, old_record.text), ("Dave", "Before the attack."))
        self.window.phase = "conversation"
        references = {self.window.display_actor(name) for name in ("Alex", "Dave")}
        self.assertEqual(references, {"Dave #1", "Dave #2"})
        self.assertFalse(any("#" in record.text for record in target_data["experiences"]))

    def test_attacker_guardian_preserves_old_body_after_real_exchange(self):
        self.arm("Alex")
        original_target_mind = self.window.mind_id("Dave")
        self.assertFalse(self.window.commit_attack("Dave", "Kitchen"))
        self.assertEqual(self.window.player_name, "Dave")
        self.assertEqual(self.window.mind_id("Alex"), original_target_mind)
        self.assertTrue(self.window.characters["Alex"]["alive"])
        self.assertTrue(self.window.player_alive)
        self.assertEqual(self.window.victims_last_night, [])
        self.assertEqual(self.window.new_crime_scenes, [])
        self.assertEqual(self.window.profile.counters.get("player_kill", 0), 0)
        self.assertEqual(self.write_memories(), "You (Hank) were in the Kitchen. You saw Alex stab Dave.")
        self.assertFalse(any("body was found" in event.text for event in self.window.public_events))
        self.assertFalse(any("corpse" in record.text for record in self.window.history_records))

    def test_both_guardians_consume_only_the_target_guardian(self):
        self.arm("Alex")
        self.arm("Dave")
        self.assertFalse(self.window.commit_attack("Dave", "Kitchen"))
        self.assertTrue(self.window.item_rules.guardian_protects(self.window.body_id("Alex")))
        self.assertFalse(self.window.item_rules.guardian_protects(self.window.body_id("Dave")))
        self.assertEqual(self.window.player_name, "Alex")

    def test_unprotected_possession_still_records_the_old_body_as_the_only_corpse(self):
        self.assertTrue(self.window.commit_attack("Dave", "Kitchen"))
        self.assertEqual(self.window.player_name, "Dave")
        self.assertFalse(self.window.characters["Alex"]["alive"])
        self.assertEqual(self.window.victims_last_night, ["Alex"])
        self.assertEqual(self.window.profile.counters["player_kill"], 1)
        self.assertEqual(self.write_memories(),
                         "You (Hank) were in the Kitchen. You saw Alex stab Dave. Afterwards, Alex's body was lying dead there, and Dave was alive.")
        bodies = [event.text for event in self.window.public_events if "body was found" in event.text]
        self.assertEqual(bodies, ["In the morning the Detective reveals Alex's body was found in the Kitchen."])
        self.window.update_witness_memories(None)
        self.assertEqual(len([event for event in self.window.public_events if "body was found" in event.text]), 1)

    def test_pre_attack_names_remain_literal_after_later_copy_changes(self):
        self.arm("Dave")
        self.window.identity_state = self.window.identity_state.clone_appearance(
            self.window.body_id("Alex"), self.window.body_id("Jerry"))
        self.assertFalse(self.window.commit_attack("Dave", "Kitchen"))
        self.window.identity_state = self.window.identity_state.clone_appearance(
            self.window.body_id("Alex"), self.window.body_id("Hank"))
        self.assertEqual(self.write_memories(),
                         "You saw Jerry stab Dave. Dave survived. Afterwards, Jerry also looked exactly like Dave.")


if __name__ == "__main__":
    unittest.main()
