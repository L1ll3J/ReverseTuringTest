"""Offline Knife choices through real entry, reward, death and aftermath controls."""
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QCoreApplication, QEvent

import test_item_integration as fixtures
from items import DraftState


class KnifeCleanupTests(unittest.TestCase):
    setUpClass = fixtures.ItemIntegrationTests.__dict__["setUpClass"]
    tearDown = fixtures.ItemIntegrationTests.tearDown
    weapon = fixtures.ItemIntegrationTests.weapon
    night = fixtures.ItemIntegrationTests.night
    settle_offer = fixtures.ItemIntegrationTests.settle_offer
    conversation = fixtures.ItemIntegrationTests.conversation
    give = fixtures.ItemIntegrationTests.give
    arm_optional = fixtures.ItemIntegrationTests.arm_optional
    wait_for_workers = fixtures.ItemIntegrationTests.wait_for_workers

    def setUp(self):
        fixtures.ItemIntegrationTests.setUp(self, keep_weapons=False)

    def start(self, *, normal=True):
        self.window.show_room_allocations = normal
        if self.window.phase == "weapon_select":
            self.night("knife")
        else:
            self.window.selected_weapon = self.weapon("knife")
            self.window.run_action_phase()
            self.window.item_drafts = DraftState(self.window.player_owner_id, ("banana", "muffle", "spare_paper"))

    def first_kill(self):
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertEqual(self.window.knife_stage, "choice")
        self.assertTrue(self.window.knife_used_bloodlust)
        self.assertIsNotNone(self.window.knife_choice_panel)

    def protect(self, name):
        self.conversation()
        original = self.window.body_id(self.window.player_name)
        self.window.exchange_player_mind(self.window.body_id(name))
        self.give("guardian_angel")
        self.window.exchange_player_mind(original)

    def test_knife_starts_passive_without_target_setup_or_blood(self):
        self.start()
        self.assertNotIn("knife", self.window._weapon_windows)
        self.assertTrue(self.window.weapon_configured())
        self.assertFalse(self.window.knife_used_bloodlust)
        self.assertIsNone(self.window.knife_choice_panel)
        text = self.window.selected_weapon["desc"]
        self.assertIn("first kill", text)
        self.assertIn("another room", text)

    def test_choice_wraps_its_hint_without_clipping_at_supported_sizes(self):
        from PyQt5.QtWidgets import QLabel
        from PyQt5.QtTest import QTest
        self.start()
        self.first_kill()
        for width, height in ((1280, 800), (1920, 1080), (2560, 1440)):
            self.window.showNormal()
            self.window.resize(width, height)
            QTest.qWait(10)
            panel = self.window.knife_choice_panel
            hint = panel.findChild(QLabel, "knifeChoiceHint")
            self.assertGreaterEqual(hint.height(), hint.heightForWidth(hint.width()))
            self.assertLessEqual(panel.geometry().bottom(), self.window.centralWidget().height())

    def test_cleanup_removes_visible_blood_but_not_the_kill_or_witness(self):
        self.start()
        self.first_kill()
        self.window.knife_cleanup_button.click()
        self.assertEqual(self.window.knife_stage, "done")
        self.assertFalse(self.window.knife_used_bloodlust)
        self.assertEqual(len(self.window.night_attacks), 1)
        self.assertIsNotNone(self.window.weapon_aftermath_panel)
        self.assertFalse(self.window.weapon_aftermath.nights[self.window.round_number].bloodlust)
        self.window.keep_weapon_button.click()
        self.assertTrue(any("You witnessed Alex kill Dave" in record.text
                            for record in self.window.characters["Jerry"]["experiences"]))
        self.assertFalse(any("covered in blood" in event.text for event in self.window.public_events))

    def test_push_on_enters_different_room_and_kills_only_one_of_two_occupants(self):
        self.start()
        with patch.object(self.window.npc_items, "capture_night", wraps=self.window.npc_items.capture_night) as camera:
            self.first_kill()
            clock = self.window.night_arrivals.clock
            self.window.knife_push_button.click()
            self.assertFalse(self.window.room_buttons["Attic"].isEnabled())
            before = tuple(self.window.rooms_visited)
            self.window.choose_room("Attic")
            self.assertEqual(tuple(self.window.rooms_visited), before)
            self.window.choose_room("Kitchen")
            self.assertEqual(self.window.item_drafts.pending_offer.room_id, "Kitchen")
            self.settle_offer()
            self.assertIsNotNone(self.window._victim_selection_context)
            self.window.kill_victim("Hank")
        self.assertEqual(camera.call_count, 1)
        self.assertEqual([attack["target_label"] for attack in self.window.night_attacks], ["Dave", "Hank"])
        self.assertTrue(self.window.characters["Owen"]["alive"])
        self.assertTrue(self.window.knife_used_bloodlust)
        self.assertEqual(self.window.knife_stage, "done")
        self.assertIn(f"At {clock}, you entered the Kitchen.", [record.text for record in self.window.history_records])
        self.assertIn(f"At {clock}, you saw Alex enter the Kitchen.",
                      [record.text for record in self.window.characters["Owen"]["experiences"]])
        self.window.kill_victim("Owen")
        self.window.finish_primary_kill("Owen", "Kitchen")
        self.assertTrue(self.window.characters["Owen"]["alive"])
        self.assertEqual(len(self.window.night_attacks), 2)

    def test_hard_empty_followup_ends_optional_attack_without_another_room(self):
        self.start(normal=False)
        self.first_kill()
        self.window.knife_push_button.click()
        self.window.choose_room("Cellar")
        self.settle_offer()
        self.assertEqual(self.window.knife_stage, "done")
        self.assertEqual(len(self.window.night_attacks), 1)
        self.assertEqual(self.window.rooms_visited, ["Attic", "Cellar"])
        self.assertTrue(self.window.room_buttons_widget.isHidden())
        self.assertIsNotNone(self.window.weapon_aftermath_panel)
        self.assertTrue(self.window.weapon_aftermath.nights[self.window.round_number].bloodlust)

    def test_entry_trap_kills_before_followup_reward_or_attack(self):
        self.conversation()
        self.arm_optional("booby_trap", room="Cellar")
        self.start(normal=False)
        self.first_kill()
        self.window.knife_push_button.click()
        self.window.choose_room("Cellar")
        self.assertFalse(self.window.player_alive)
        self.assertTrue(self.window.game_over)
        self.assertIsNone(self.window.draft_page)
        self.assertNotIn("Cellar", self.window.item_drafts.seen_rooms)
        self.assertEqual(len(self.window.night_attacks), 1)
        self.assertIsNone(self.window.knife_choice_panel)

    def test_guardian_saves_trap_entry_then_empty_followup_ends_with_original_blood(self):
        self.conversation()
        self.give("guardian_angel")
        self.arm_optional("booby_trap", room="Cellar")
        self.start()
        self.first_kill()
        self.window.knife_push_button.click()
        self.window.choose_room("Cellar")
        self.assertTrue(self.window.player_alive)
        self.assertFalse(self.window.item_rules.protection_armed)
        self.assertIsNotNone(self.window.draft_page)
        self.settle_offer()
        self.assertEqual(len(self.window.night_attacks), 1)
        self.assertTrue(self.window.knife_used_bloodlust)
        self.assertIsNotNone(self.window.weapon_aftermath_panel)

    def test_guardian_prevented_first_kill_offers_end_night_without_blood(self):
        self.protect("Dave")
        self.start()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertTrue(self.window.characters["Dave"]["alive"])
        self.assertFalse(self.window.knife_used_bloodlust)
        self.assertIsNotNone(self.window.knife_choice_panel)
        self.assertEqual(self.window.knife_cleanup_button.text(), "End night")
        self.assertEqual(self.window.night_attacks, [])
        self.window.knife_cleanup_button.click()
        self.assertIsNotNone(self.window.weapon_aftermath_panel)
        self.assertFalse(any("cleaned the blood" in record.text for record in self.window.history_records))

    def test_first_actual_kill_on_followup_has_blood_without_another_cleanup(self):
        self.protect("Dave")
        self.start()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertFalse(self.window.knife_used_bloodlust)
        self.window.knife_push_button.click()
        self.window.choose_room("Kitchen")
        self.settle_offer()
        self.window.kill_victim("Hank")
        self.assertTrue(self.window.characters["Dave"]["alive"])
        self.assertEqual([attack["target_label"] for attack in self.window.night_attacks], ["Hank"])
        self.assertTrue(self.window.knife_used_bloodlust)
        self.assertEqual(self.window.knife_stage, "done")
        self.assertIsNone(self.window.knife_choice_panel)
        self.assertIsNotNone(self.window.weapon_aftermath_panel)

    def test_guardian_prevented_followup_keeps_first_blood_and_consumes_attempt(self):
        self.protect("Hank")
        self.start()
        self.first_kill()
        self.window.knife_push_button.click()
        self.window.choose_room("Kitchen")
        self.settle_offer()
        self.window.kill_victim("Hank")
        self.assertTrue(self.window.characters["Hank"]["alive"])
        self.assertEqual(self.window.knife_stage, "done")
        self.assertTrue(self.window.knife_used_bloodlust)
        self.assertEqual(len(self.window.night_attacks), 1)
        self.window.kill_victim("Owen")
        self.assertTrue(self.window.characters["Owen"]["alive"])

    def test_cleanup_preserves_earned_watch_basic_action_without_knife_recursion(self):
        self.conversation()
        self.give("watch_this_drive", "Dave")
        self.start()
        self.first_kill()
        self.assertFalse(self.window.bonus_action_pending)
        self.window.knife_cleanup_button.click()
        self.assertTrue(self.window.bonus_action_pending)
        self.assertIsNone(self.window.weapon_aftermath_panel)
        self.window.choose_room("Kitchen")
        self.settle_offer()
        self.window.kill_victim("Hank")
        self.assertEqual([attack["weapon_id"] for attack in self.window.night_attacks], ["knife", "bare"])
        self.assertFalse(self.window.knife_used_bloodlust)
        self.assertIsNone(self.window.knife_choice_panel)
        self.assertEqual(self.window.item_rules.bonus_actions, 0)
        self.assertIsNotNone(self.window.weapon_aftermath_panel)

    def test_push_on_finishes_before_watch_bonus_and_ditch(self):
        self.conversation()
        self.give("watch_this_drive", "Dave")
        self.start()
        self.first_kill()
        self.window.knife_push_button.click()
        self.window.choose_room("Kitchen")
        self.settle_offer()
        self.window.kill_victim("Hank")
        self.assertTrue(self.window.bonus_action_pending)
        self.assertIsNone(self.window.weapon_aftermath_panel)
        self.window.choose_room("Attic")
        self.assertIsNone(self.window.draft_page)
        self.window.kill_victim("Jerry")
        self.assertEqual([attack["weapon_id"] for attack in self.window.night_attacks], ["knife", "knife", "bare"])
        self.assertIsNotNone(self.window.weapon_aftermath_panel)
        self.window.ditch_weapon_button.click()
        self.assertEqual(self.window.weapon_aftermath.drop.room, "Attic")
        self.assertTrue(self.window.weapon_aftermath.nights[self.window.round_number].bloodlust)

    def test_new_night_resets_choice_and_blood_without_rewriting_prior_aftermath(self):
        self.start()
        self.first_kill()
        self.window.knife_push_button.click()
        self.window.choose_room("Cellar")
        self.settle_offer()
        prior_day = self.window.round_number
        self.window.keep_weapon_button.click()
        self.window.run_action_phase()
        self.assertEqual(self.window.knife_stage, "ready")
        self.assertIsNone(self.window.knife_first_room)
        self.assertFalse(self.window.knife_used_bloodlust)
        self.assertTrue(self.window.weapon_aftermath.nights[prior_day].bloodlust)

    def test_stale_choice_cannot_change_restarted_run_or_next_stage(self):
        self.start()
        self.first_kill()
        old = (self.window.run_id, self.window.round_number, self.window.body_id(self.window.player_name))
        self.window.knife_push_button.click()
        self.window.choose_knife_followup(*old, False)
        self.assertEqual(self.window.knife_stage, "room")
        self.assertTrue(self.window.knife_used_bloodlust)
        self.window.restart_game()
        self.window.choose_knife_followup(*old, False)
        self.assertEqual(self.window.phase, "start")
        self.assertEqual(self.window.knife_stage, "ready")
        self.assertFalse(self.window.knife_used_bloodlust)
        self.assertIsNone(self.window.knife_choice_panel)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
