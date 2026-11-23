"""Actual post-action controls and private weapon observations without network calls."""
import unittest
from unittest.mock import Mock, patch

import test_item_integration as fixtures
import test_night_arrivals_integration as arrival_fixtures
from weapon_aftermath import OBJECT_DESCRIPTIONS, OBJECT_NAMES, PUBLIC_FINDING, PRIVATE_FINDING


class WeaponAftermathIntegrationTests(unittest.TestCase):
    setUpClass = fixtures.ItemIntegrationTests.__dict__["setUpClass"]
    tearDown = fixtures.ItemIntegrationTests.tearDown
    weapon = fixtures.ItemIntegrationTests.weapon

    def setUp(self):
        fixtures.ItemIntegrationTests.setUp(self, keep_weapons=False)

    def settled_action(self, weapon="knife"):
        w = self.window
        w.phase = "action"
        w.round_number = 1
        w.selected_weapon = self.weapon(weapon)
        w.rooms_visited = ["Attic"]
        w.room_assignments = {room: [] for room in w.rooms}
        w.room_assignments["Attic"] = ["Dave", "Jerry"]
        w.room_assignments["Kitchen"] = ["Hank", "Owen"]
        w.new_crime_scenes = []
        w.weapon_aftermath.visit("entry", 1, "Attic")
        w.pending_pen_note_text = "A forged last note." if weapon == "pen" else None
        self.assertTrue(w.commit_attack("Dave", "Attic", weapon_id=weapon))
        return w

    def test_ditch_uses_real_buttons_once_and_detective_finds_exact_snapshot(self):
        w = self.settled_action()
        w.finish_night()
        self.assertEqual(w.phase, "action")
        self.assertIsNotNone(w.weapon_aftermath_panel)
        old = w.ditch_weapon_button
        old.click()
        self.assertEqual(w.phase, "conversation")
        self.assertEqual(w.selected_weapon["id"], "bare")
        expected = PUBLIC_FINDING.format(room="Attic", object_name=OBJECT_NAMES["knife"], description=OBJECT_DESCRIPTIONS["knife"])
        self.assertEqual([event.text for event in w.public_events].count(expected), 1)
        w.publish_dropped_weapon()
        self.assertEqual([event.text for event in w.public_events].count(expected), 1)
        w.choose_weapon_aftermath(1, w.body_id(w.player_name), "knife", True)
        self.assertEqual(w.weapon_aftermath.drop.room, "Attic")

    def test_keep_retains_weapon_and_ditch_opportunity(self):
        w = self.settled_action("stopwatch")
        w.finish_night()
        w.keep_weapon_button.click()
        self.assertEqual(w.phase, "conversation")
        self.assertEqual(w.selected_weapon["id"], "stopwatch")
        self.assertIsNone(w.weapon_aftermath.drop)
        self.assertTrue(w.weapon_aftermath.can_ditch("stopwatch"))
        self.assertTrue(any("VOUCH" in record.text for record in w.characters["Jerry"]["dispositions"]))

    def test_pen_note_and_signature_survive_ditch_without_rerolling(self):
        w = self.settled_action("pen")
        with patch("main.random.random", return_value=1.0) as roll:
            w.finish_night()
            frozen = w.weapon_aftermath.nights[1]
            w.ditch_weapon_button.click()
            self.assertEqual(roll.call_count, 1)
        self.assertEqual((frozen.pen_note, frozen.pen_signature), ("A forged last note.", "Dave"))
        self.assertIn("The Detective reveals that a note was found by the body, it reads: A forged last note. - Signed: Dave",
                      [event.text for event in w.public_events])
        self.assertEqual(w.selected_weapon["id"], "bare")

    def test_knife_bloodlust_survives_ditch_and_later_physical_entry_sets_location(self):
        w = self.settled_action()
        with patch.object(w, "finish_night"):
            w.finish_knife_attack("Hank", "Kitchen")
        w.finish_night()
        w.ditch_weapon_button.click()
        self.assertEqual(w.weapon_aftermath.drop.room, "Kitchen")
        self.assertTrue(w.weapon_aftermath.nights[1].bloodlust)
        self.assertTrue(any("covered in blood" in event.text for event in w.public_events))

    def test_remote_attack_does_not_move_dump_from_committed_player_room(self):
        arrival_fixtures.NightArrivalIntegrationTests.open_night(self)
        w = self.window
        w.selected_weapon = self.weapon("c4")
        w.c4_target_room = "Kitchen"
        w.choose_room("Library")
        self.assertIsNotNone(w.draft_page)
        w.draft_page.leave_button.click()
        self.assertEqual(w.weapon_aftermath.current_rooms[1], "Library")
        self.assertEqual([attack["room"] for attack in w.night_attacks], ["Kitchen", "Kitchen"])
        self.assertIsNotNone(w.weapon_aftermath_panel)
        w.ditch_weapon_button.click()
        self.assertEqual(w.weapon_aftermath.drop.room, "Library")

    def test_soul_displaced_npc_finds_only_object_after_two_committed_turns(self):
        w = self.window
        w.phase = "conversation"; w.round_number = 1
        w.selected_weapon = self.weapon("knife")
        w.ensure_identity_state()
        displaced = w.characters["Dave"]
        with patch.object(w.weapon_aftermath.rng, "randint", return_value=2):
            w.exchange_player_mind(w.body_id("Dave"))
        expected = PRIVATE_FINDING.format(object_name=OBJECT_NAMES["knife"], description=OBJECT_DESCRIPTIONS["knife"])
        w.append_speech("Detective", "A question.")
        w.append_speech("Jerry", "An apparent voice.", origin="throw_voice")
        self.assertNotIn(expected, [record.text for record in displaced["experiences"]])
        w.append_speech("Jerry", "One committed reply.")
        self.assertNotIn(expected, [record.text for record in displaced["experiences"]])
        w.append_speech("Hank", "Two committed replies.")
        self.assertEqual([record.text for record in displaced["experiences"]].count(expected), 1)
        self.assertNotIn("kill twice", expected)
        self.assertNotIn(expected, [event.text for event in w.public_events])
        w.process_pocket_discoveries()
        self.assertEqual([record.text for record in displaced["experiences"]].count(expected), 1)

    def test_player_reclaiming_object_cancels_old_pocket_memory(self):
        w = self.window
        w.phase = "conversation"; w.round_number = 1
        w.selected_weapon = self.weapon("knife")
        w.ensure_identity_state()
        old = w.body_id("Alex")
        displaced = w.characters["Dave"]
        with patch.object(w.weapon_aftermath.rng, "randint", return_value=2):
            w.exchange_player_mind(w.body_id("Dave"))
            w.exchange_player_mind(old)
        w.append_speech("Jerry", "First.")
        w.append_speech("Hank", "Second.")
        self.assertFalse(any("reach into your pocket" in record.text for record in displaced["experiences"]))
        self.assertEqual(w.selected_weapon["id"], "knife")

    def test_voodoo_follows_soul_without_dump_or_discovery(self):
        w = self.window
        w.phase = "conversation"; w.round_number = 1
        w.selected_weapon = self.weapon("voodoo")
        w.ensure_identity_state()
        old = w.body_id("Alex")
        w.exchange_player_mind(w.body_id("Dave"))
        self.assertEqual(w.selected_weapon["id"], "voodoo")
        self.assertNotIn(old, w.body_weapons)
        self.assertFalse(w.weapon_aftermath.can_ditch("voodoo"))
        w.append_speech("Jerry", "One."); w.append_speech("Hank", "Two.")
        self.assertFalse(any("reach into your pocket" in record.text for record in w.characters["Alex"]["experiences"]))
