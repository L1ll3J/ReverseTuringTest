"""Injected identical appearances exercise real controls without live model calls."""
from dataclasses import replace
import random
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication, QComboBox, QPushButton
import test_love_potion_integration as fixtures
from npc_items import NPCItemState
from night_arrivals import NightArrivals
from test_item_integration import ArrivalFixtureRng, keep_completed_night_weapon
import main as game
from presentation_aliases import PresentationAliases


class AppearanceRoutingTests(unittest.TestCase):
    setUpClass = fixtures.LovePotionIntegrationTests.__dict__["setUpClass"]
    def setUp(self):
        fixtures.LovePotionIntegrationTests.setUp(self)
        self.stack.enter_context(patch.object(self.window, "finish_night", side_effect=
            keep_completed_night_weapon(self.window, self.window.finish_night)))

    def start_planned_night(self, assignments, initial_count=1):
        placements = {self.window.body_id(name): room for room, names in assignments.items() for name in names}
        with patch.object(game, "assign_guest_rooms", return_value=placements), patch.object(
                game, "NightArrivals", side_effect=lambda night, planned: NightArrivals(
                    night, planned, rng=ArrivalFixtureRng(), initial_count=min(initial_count, len(planned)))):
            self.window.run_action_phase()
    tearDown = fixtures.LovePotionIntegrationTests.tearDown

    def clone(self, actor, target="Debra"):
        self.window.identity_state = self.window.identity_state.clone_appearance(
            self.window.body_id(actor), self.window.body_id(target))
        self.window.invalidate_prepared_turn("test_appearance", changed=True)
        self.window.refresh_recipient_choices()

    def test_alias_controls_keep_distinct_body_targets_and_canonical_item_memories(self):
        self.clone("Alex")
        self.clone("Dave")
        body_ids = {name: self.window.body_id(name) for name in ("Alex", "Dave", "Debra")}
        labels = {name: self.window.display_label(body) for name, body in body_ids.items()}
        self.assertEqual(len(set(labels.values())), 3)
        self.assertTrue(all(label.startswith("Debra #") for label in labels.values()))
        self.window.refresh_recipient_choices()
        selector = self.window.recipient_selector
        for name in ("Dave", "Debra"):
            index = selector.findData(body_ids[name])
            self.assertEqual(selector.itemText(index), labels[name])
        entry = self.window.inventory.grant("banana")
        self.window.open_inventory()
        box = self.window.inventory_page.target_boxes[entry.instance_id]
        box.setCurrentIndex(box.findData(body_ids["Dave"]))
        self.window.inventory_page.use_buttons[entry.instance_id].click()
        self.assertIn("Debra gave you", self.window.characters["Dave"]["dispositions"][-1].text)
        self.assertFalse(self.window.characters["Debra"]["dispositions"])
        self.assertEqual(self.window.public_events[-1].text, "Debra gave Debra a banana.")
        self.assertEqual({body.label for body in self.window.body_states() if body.body_id in body_ids.values()},
                         {"Alex", "Dave", "Debra"})
        self.window.phase = "action"
        self.assertEqual(self.window.display_label(body_ids["Dave"]), "Debra")

    def test_speech_captures_alias_for_human_and_canonical_perception_for_npc(self):
        self.window.append_speech("Alex", "Before the change.")
        old_record = self.window.history_records[-1]
        old_recall = self.window.recalled_dialogue()[-1]
        self.clone("Alex")
        self.clone("Dave")
        self.window.append_speech("Dave", "After the change.", "Alex")
        record = self.window.history_records[-1]
        self.assertEqual(record.speaker_id, self.window.body_id("Dave"))
        self.assertTrue(record.speaker.startswith("Debra #"))
        self.assertTrue(self.window.speaker_display_format(record).isAnchor())
        self.assertEqual(self.window.public_dialogue[-1].speaker, "Dave")
        self.assertEqual(self.window.public_dialogue[-1].addressee, "Alex")
        self.assertTrue(self.window.player_reply_required())
        recalled = self.window.recalled_dialogue()[-1]
        self.assertEqual((recalled.speaker, recalled.addressee), ("Debra", "Debra"))
        self.assertEqual(self.window.group_turn_snapshot().dialogue[-1], recalled)
        self.clone("Dave", "Hank")
        self.assertIs(self.window.recalled_dialogue()[-1], recalled)
        self.assertEqual(record.speaker.split(" #")[0], "Debra")
        self.assertEqual((old_record.speaker, old_recall.speaker), ("Alex", "Alex"))

    def test_voting_buttons_preserve_target_even_after_aliases_or_phase_change(self):
        self.clone("Dave")
        self.window.presentation_aliases = PresentationAliases(random.Random(7))
        conversation_labels = {self.window.body_id(name): self.window.display_actor(name)
                               for name in ("Dave", "Debra")}
        with patch.object(self.window, "start_vote_requests"):
            self.window.run_voting_phase()
        self.assertEqual({body: self.window.display_label(body) for body in conversation_labels}, conversation_labels)
        buttons = {button.property("target_body_id"): button
                   for button in self.window.vote_buttons_widget.findChildren(QPushButton)
                   if button.property("target_body_id") is not None}
        dave = self.window.body_id("Dave")
        debra = self.window.body_id("Debra")
        self.assertNotEqual(buttons[dave].text(), buttons[debra].text())
        self.assertTrue(buttons[dave].text().startswith("Vote for Debra #"))
        self.window.presentation_aliases = PresentationAliases(random.Random(2))
        with patch.object(self.window, "start_vote_requests"), patch.object(self.window, "tally_votes"):
            buttons[dave].click()
        self.assertEqual(self.window.votes["Alex"], "Dave")
        self.window.votes = {}
        self.window.phase = "conversation"
        buttons[debra].click()
        self.assertEqual(self.window.votes, {})

    def test_captured_photo_preserves_duplicate_faces_and_private_body_provenance(self):
        camera = NPCItemState({"Dave": "camera"})
        rooms = {"Kitchen": ("Dave", "Debra")}
        original = camera.capture_night(1, rooms, ("Dave", "Debra"), body_ids=self.window.body_ids)[0]
        description = original.public_description()
        self.clone("Dave")
        names = {name: self.window.perceived_actor(name) for name in self.window.body_ids}
        later = camera.capture_night(2, rooms, ("Dave", "Debra"), appearance_names=names,
                                     body_ids=self.window.body_ids)[0]
        self.assertEqual(later.owner, "Debra")
        self.assertEqual(later.occupants, ("Debra", "Debra"))
        self.assertEqual(later.owner_body_id, self.window.body_id("Dave"))
        self.assertEqual(set(later.occupant_body_ids), {self.window.body_id("Dave"), self.window.body_id("Debra")})
        self.assertNotIn("#", later.public_description())
        self.assertEqual(original.public_description(), description)
        self.assertIn("Dave", description)
        self.clone("Dave", "Hank")
        self.assertEqual(later.occupants, ("Debra", "Debra"))
        self.assertIs(camera.capture_night(2, rooms, ("Dave", "Debra"))[0], later)

    def test_existing_room_memory_template_uses_canonical_appearance_names(self):
        self.clone("Dave")
        self.window.room_assignments = {"Kitchen": ["Dave", "Debra"]}
        self.window.rooms_visited = []
        self.window.night_attacks = []
        self.window._night_memories_written = False
        self.window.update_witness_memories(None)
        memory = self.window.characters["Dave"]["experiences"][-1]
        self.assertEqual(memory.text, "You (Debra) spent the night in the Kitchen with Debra.")
        self.assertNotIn("#", memory.text)
        self.clone("Dave", "Hank")
        self.assertEqual(memory.text, "You (Debra) spent the night in the Kitchen with Debra.")

    def test_memory_wipe_cutoff_keeps_only_later_canonical_captures(self):
        self.window.append_speech("Dave", "Before.")
        self.window.clear_npc_recollections()
        self.clone("Dave")
        self.window.append_speech("Dave", "After.")
        self.assertEqual([(line.speaker, line.text) for line in self.window.recalled_dialogue()], [("Debra", "After.")])
        self.assertEqual(len(self.window.public_dialogue), 2)

    def test_action_editors_use_identical_canonical_text_and_distinct_body_data(self):
        self.clone("Dave")
        self.window.phase = "action"
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "blackmagic")
        self.window.open_blackmagic_dialog()
        editor = self.window._weapon_windows["blackmagic"]
        control, target = editor.findChildren(QComboBox)
        control.setCurrentIndex(control.findData(self.window.body_id("Dave")))
        target.setCurrentIndex(target.findData(self.window.body_id("Debra")))
        self.assertEqual((control.currentText(), target.currentText()), ("Debra", "Debra"))
        next(button for button in editor.findChildren(QPushButton) if button.text() == "Save").click()
        self.assertEqual((self.window.black_magic_controller, self.window.black_magic_target), ("Dave", "Debra"))
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "knife")
        self.window.open_knife_dialog()
        editor = self.window._weapon_windows["knife"]
        victim = editor.findChild(QComboBox)
        victim.setCurrentIndex(victim.findData(self.window.body_id("Dave")))
        self.assertEqual(victim.currentText(), "Debra")
        next(button for button in editor.findChildren(QPushButton) if button.text() == "Confirm").click()
        self.assertEqual(self.window.knife_extra_target, "Dave")
        self.assertNotIn("#", self.window.history_records[-1].text)

    def test_love_giver_is_captured_canonically_and_never_renamed(self):
        self.clone("Alex")
        entry = self.window.inventory.grant("love_potion")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        history = self.window.characters["Dave"]["love_history"]
        self.assertEqual(history.doses[0].giver, "Debra")
        self.clone("Alex", "Hank")
        self.window.round_number = 2
        self.window.advance_love_histories(discussion=False)
        self.window.advance_love_histories(discussion=True)
        self.assertEqual(self.window.characters["Dave"]["love_history"].doses[0].giver, "Debra")

    def test_attack_observation_captures_name_before_later_appearance_change(self):
        self.clone("Alex")
        self.clone("Dave")
        self.window.phase = "action"
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.room_assignments = {"Kitchen": ["Dave", "Hank"]}
        self.window.rooms_visited = ["Kitchen"]
        self.window.new_crime_scenes = []
        self.window._night_memories_written = False
        self.assertTrue(self.window.commit_attack("Hank", "Kitchen"))
        self.clone("Alex", "Jerry")
        self.window.update_witness_memories(None)
        memory = self.window.characters["Dave"]["experiences"][-1].text
        self.assertIn("You (Debra)", memory)
        self.assertIn("You witnessed Debra kill Hank", memory)
        self.assertNotIn("Alex", memory)
        self.assertNotIn("#", memory)

    def test_local_identical_victims_have_stable_aliases_and_body_id_callbacks(self):
        self.clone("Dave")
        self.window.phase = "action"
        self.window.rooms_visited = ["Kitchen"]
        self.window.room_assignments = {"Kitchen": ["Dave", "Debra"]}
        self.window.display_victim_selection(["Dave", "Debra"])
        buttons = {button.property("target_body_id"): button
                   for button in self.window.victim_buttons_widget.findChildren(QPushButton)}
        labels = {body: button.text() for body, button in buttons.items()}
        self.assertEqual(set(labels.values()), {"Kill Debra #1", "Kill Debra #2"})
        self.window.display_victim_selection(["Debra", "Dave"])
        reopened = {button.property("target_body_id"): button.text()
                    for button in self.window.victim_buttons_widget.findChildren(QPushButton)}
        self.assertEqual(reopened, labels)
        with patch.object(self.window, "finish_primary_kill") as kill:
            buttons[self.window.body_id("Dave")].click()
            kill.assert_called_once_with("Dave", "Kitchen")
        self.window.round_number += 1
        with patch.object(self.window, "finish_primary_kill") as kill:
            buttons[self.window.body_id("Debra")].click()
            kill.assert_not_called()
        self.window._night_memories_written = False
        self.window.rooms_visited = []
        self.window.night_attacks = []
        self.window.update_witness_memories(None)
        self.assertNotIn("#", self.window.characters["Dave"]["experiences"][-1].text)
        photo = NPCItemState({"Dave": "camera"}).capture_night(
            1, {"Kitchen": ["Dave", "Debra"]}, ("Dave", "Debra"),
            appearance_names={"Dave": "Debra", "Debra": "Debra"})[0]
        self.assertNotIn("#", photo.public_description())

    def test_multi_occupant_love_captures_perceived_player_in_vouches_and_memories(self):
        self.clone("Alex")
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "love")
        self.start_planned_night({
            "Kitchen": ["Dave", "Jerry"], "Attic": ["Hank"],
            "Library": ["Debra"], "Basement": ["Owen"], "Cellar": [],
        })
        self.window.finish_room_choice("Kitchen")
        for observer, companion in (("Dave", "Jerry"), ("Jerry", "Dave")):
            disposition = self.window.characters[observer]["dispositions"][-1]
            memory = self.window.characters[observer]["experiences"][-1]
            self.assertIn(f"You VOUCH for {companion} and Debra.", disposition.text)
            self.assertIn(f"You and {companion} and Debra shared", memory.text)
            for record in (disposition, memory):
                self.assertNotIn("Alex", record.text)
                self.assertNotIn("#", record.text)
        captured = self.window.characters["Dave"]["dispositions"][-1]
        self.clone("Alex", "Hank")
        self.assertIn("You VOUCH for Jerry and Debra.", captured.text)

    def test_hard_room_hover_and_accessibility_match_local_victim_aliases(self):
        self.clone("Dave")
        self.window.show_room_allocations = False
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.start_planned_night({"Kitchen": ["Dave", "Debra"], "Attic": ["Jerry"],
                                  "Library": ["Hank"], "Basement": ["Owen"], "Cellar": []})
        self.assertTrue(all(not observation.known_names for observation in self.window.house_view.observations.values()))
        self.window.finish_room_choice("Kitchen")
        observation = self.window.house_view.observations["kitchen"]
        labels = tuple(button.text().removeprefix("Kill ")
                       for button in self.window.victim_buttons_widget.findChildren(QPushButton))
        self.assertEqual(observation.known_names, labels)
        self.assertEqual(set(labels), {"Debra #1", "Debra #2"})
        button = self.window.house_view.buttons["kitchen"]
        QApplication.sendEvent(button, QEvent(QEvent.Enter))
        detail = self.window.house_view.info_body.text()
        self.assertEqual(button.accessibleDescription(), detail)
        for label in labels:
            self.assertIn(label, detail)
        self.assertNotIn("Dave", detail)
        self.assertTrue(all(not item.known_names for room, item in self.window.house_view.observations.items()
                            if room != "kitchen"))
        self.clone("Dave", "Hank")
        self.window.update_room_button_states()
        self.assertEqual(self.window.house_view.observations["kitchen"].known_names, labels)
        self.assertEqual(button.accessibleDescription(), detail)

    def test_normal_initial_identical_occupants_have_distinct_observed_labels(self):
        self.clone("Dave")
        for name in ("Dave", "Debra"):
            entry = self.window.inventory.grant("room_reservation")
            self.assertTrue(self.window.use_inventory_item(
                entry.instance_id, self.window.body_id(name), room_id="Kitchen"))
        self.window.show_room_allocations = True
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.rooms_visited = ["Kitchen"]
        self.start_planned_night({"Kitchen": ["Dave", "Debra"], "Attic": ["Jerry"],
                                  "Library": ["Hank"], "Basement": ["Owen"], "Cellar": []}, initial_count=2)
        observation = self.window.house_view.observations["kitchen"]
        self.assertEqual(set(observation.known_names), {"Debra #1", "Debra #2"})
        self.assertFalse(observation.unknown_remainder)
        self.assertNotIn("Alex", self.window.house_view.buttons["kitchen"].accessibleDescription())

    def test_generated_explosion_and_kill_history_capture_names_without_rewriting_prior_speech(self):
        self.window.append_speech("Dave", "An earlier statement.")
        prior = self.window.history_records[-1]
        self.clone("Dave")
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "c4")
        self.start_planned_night({"Kitchen": ["Dave", "Debra"], "Attic": [],
                                  "Library": ["Jerry"], "Basement": ["Hank"], "Cellar": ["Owen"]})
        self.window.c4_target_room = "Kitchen"
        self.window.apply_weapon_gating()
        start = len(self.window.history_records)
        self.window.finish_room_choice("Attic")
        generated = [record.text for record in self.window.history_records[start:]
                     if "C4 detonates" in record.text or record.text.startswith("You killed ")]
        self.assertEqual(len(generated), 3)
        self.assertTrue(any("It kills: Debra, Debra." in line for line in generated))
        self.assertTrue(all("Dave" not in line and "#" not in line for line in generated))
        self.assertEqual((prior.speaker, prior.text), ("Dave", "An earlier statement."))
        self.clone("Dave", "Hank")
        self.assertTrue(all("Hank" not in line for line in generated))

    def test_possession_history_captures_perceived_target_and_discarded_body(self):
        self.clone("Alex", "Hank")
        self.clone("Dave")
        self.window.phase = "action"
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "voodoo")
        self.window.room_assignments = {"Kitchen": ["Dave"]}
        self.window.new_crime_scenes = []
        start = len(self.window.history_records)
        self.assertTrue(self.window.commit_attack("Dave", "Kitchen"))
        generated = [record.text for record in self.window.history_records[start:]]
        self.assertIn("You possessed the body of Debra in the Kitchen and left behind Hank as a corpse.", generated)
        self.assertIn("You killed Hank in the Kitchen.", generated)
        self.assertTrue(all("Alex" not in line and "Dave" not in line for line in generated))
