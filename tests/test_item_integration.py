"""Offline integration checks for real room offers, item effects and progression."""

import contextlib
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from time import monotonic
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QPushButton, QTextEdit

import main as game
from content import CONTENT_CATALOGUE
from dev_trace import TraceRun
from game_rules import MUFFLED_SPEECH, MUFFLE_EXPIRED, BANANA_DISPOSITION
from items import DraftState, eligible_item_pool
from night_arrivals import NightArrivals
from progression import ProfileError, ProfileStore, ProgressEvent


class ArrivalFixtureRng:
    """Preserve planned guest order with repeatable, plausible clock increments."""
    def shuffle(self, values):
        pass

    def randint(self, lower, upper):
        return lower


def keep_completed_night_weapon(window, finish_night):
    """Make the old fixtures' retention choice through the real aftermath control."""
    def finish_and_keep(*args, **kwargs):
        result = finish_night(*args, **kwargs)
        panel = getattr(window, "weapon_aftermath_panel", None)
        if panel is not None and not panel.isHidden():
            window.keep_weapon_button.click()
        return result
    return finish_and_keep


class ItemIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["item-integration"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32" and cls.app.platformName() == "offscreen":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self, *, keep_weapons=True):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.path = Path(self.temp.name) / "profile.json"
        self.stack.enter_context(patch.dict(os.environ, {
            "ALIBAI_PROFILE_PATH": str(self.path), "ALIBAI_DEV_MODE": "0",
            "ALIBAI_SETTINGS_PATH": str(Path(self.temp.name) / "preferences.ini"),
        }))
        self.stack.enter_context(patch.object(game, "QMediaPlayer"))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.speech = self.stack.enter_context(patch.object(game.AIRuntime, "speech", return_value=game.SpeechProposal(None, "My account.")))
        self.stack.enter_context(patch.object(game.AIRuntime, "select_reply", side_effect=lambda state, events, dialogue, proposals: next(iter(proposals))))
        self.stack.enter_context(patch.object(game.AIRuntime, "vote", return_value="NONE"))
        self.stack.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        self.window = game.GameWindow()
        self.window.player_name_input.setText("Alex")
        self.window.start_game()
        self.window.rooms_visited = []
        self.stack.enter_context(patch.object(self.window, "run_conversation_loop"))
        if keep_weapons:
            self.stack.enter_context(patch.object(self.window, "finish_night", side_effect=
                keep_completed_night_weapon(self.window, self.window.finish_night)))
        self.planned_rooms = {
            "Attic": ["Dave", "Jerry"], "Kitchen": ["Hank", "Owen"],
            "Library": ["Debra"], "Basement": [], "Cellar": [],
        }
        assign_rooms = game.assign_guest_rooms

        def planned_assignments(body_ids, rooms, reservations=None, **kwargs):
            preferred = dict(reservations or {})
            for room, names in self.planned_rooms.items():
                if room not in rooms:
                    continue
                for name in names:
                    body_id = self.window.body_id(name)
                    if (body_id in body_ids and body_id not in preferred
                            and sum(value == room for value in preferred.values()) < 2):
                        preferred[body_id] = room
            return assign_rooms(body_ids, rooms, preferred, **kwargs)

        self.stack.enter_context(patch.object(game, "assign_guest_rooms", side_effect=planned_assignments))
        self.stack.enter_context(patch.object(game, "NightArrivals", side_effect=lambda night, placements:
            NightArrivals(night, placements, rng=ArrivalFixtureRng(), initial_count=min(1, len(placements)))))


    def tearDown(self):
        for worker in list(self.window.active_workers):
            worker.wait(3000)
        self.app.processEvents()
        self.window.overlay_host.close_page()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def weapon(self, item_id="bare"):
        return next(weapon for weapon in self.window.weapons if weapon["id"] == item_id)

    def night(self, weapon_id="bare", assignments=None):
        if assignments is not None:
            self.planned_rooms = assignments
        self.window.select_weapon_and_start(self.weapon())
        self.window.selected_weapon = self.weapon(weapon_id)
        # Item choices are fixed while arrivals stay on the real production path.
        self.window.item_drafts = DraftState(self.window.player_owner_id, ("banana", "muffle", "spare_paper"))

    def settle_offer(self, item_id=None):
        offer = self.window.item_drafts.pending_offer
        self.assertIsNotNone(offer)
        if item_id is None:
            self.window.draft_page.leave_button.click()
        else:
            self.window.draft_page.take_buttons[offer.options.index(item_id)].click()

    def conversation(self):
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.update_inventory_button()

    def give(self, item_id, target=None):
        entry = self.window.inventory.grant(item_id)
        target_id = self.window.body_id(target) if target else None
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target_id))
        if item_id == "banana":
            self.wait_for_workers()
        return entry

    def vote(self, ballots):
        self.window.phase = "conversation"
        if self.window.player_reply_required():
            self.window.interject_input.setText("My account.")
            self.window.send_interjection()
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.window.player_vote(ballots[self.window.player_name])
        for voter, target in ballots.items():
            if voter != self.window.player_name:
                self.window.collect_ai_vote(voter, target)

    def wait_for_workers(self):
        deadline = monotonic() + 3
        while self.window.active_workers and monotonic() < deadline:
            QTest.qWait(5)
        self.assertFalse(self.window.active_workers)

    def test_offer_inventory_and_unlock_goals_resolve_readable_body_fonts(self):
        for weapon in self.window.weapons:
            if weapon["id"] == "bare":
                continue
            self.window.inspect_weapon(weapon)
            details = self.window.weapon_details
            self.assertEqual(details.description.text(), weapon["desc"])
            self.assertEqual(details.goal.text(), "Unlock: " + game.UNLOCK_GOALS[weapon["id"]])
            for label in (details.description, details.goal):
                self.assertNotEqual(QFontInfo(label.font()).family(), self.window.pixel_font_family)
        self.night()
        self.window.choose_room("Basement")
        self.app.processEvents()
        page = self.window.draft_page
        labels = page.findChildren(QLabel)
        descriptions = [label for label in labels if label.objectName().startswith("inlineItemDescription_")]
        names = [label for label in labels if label.objectName().startswith("inlineItemName_")]
        self.assertEqual(len(descriptions), 3)
        self.assertEqual(len(names), 3)
        self.assertTrue(all(QFontInfo(label.font()).family() != self.window.pixel_font_family
                            for label in descriptions))
        self.assertTrue(all(QFontInfo(label.font()).family() == self.window.pixel_font_family for label in names))
        self.assertTrue(all(QFontInfo(button.font()).family() == self.window.pixel_font_family
                            for button in page.take_buttons.values()))
        self.assertTrue(all(QFontInfo(button.font()).family() != self.window.pixel_font_family
                            for button in page.reroll_buttons.values()))
        for label in labels:
            if label.wordWrap():
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        self.settle_offer("banana")
        self.conversation()
        self.window.open_inventory()
        self.app.processEvents()
        descriptions = [label for label in self.window.inventory_page.findChildren(QLabel)
                        if label.objectName().startswith("inventoryDescription_")]
        self.assertTrue(descriptions)
        self.assertTrue(all(QFontInfo(label.font()).family() != self.window.pixel_font_family
                            for label in descriptions))

    def test_reservation_inventory_room_selection_actual_assignment_and_causal_unlock(self):
        self.conversation()
        self.window.old_crime_scenes = ["Attic"]
        entry = self.window.inventory.grant("room_reservation")
        self.window.open_inventory()
        page = self.window.inventory_page
        target = page.target_boxes[entry.instance_id]
        room = page.room_boxes[entry.instance_id]
        self.assertEqual(room.findData("Attic"), -1)
        target.setCurrentIndex(target.findData(self.window.body_id("Jerry")))
        room.setCurrentIndex(room.findData("Kitchen"))
        page.use_buttons[entry.instance_id].click()
        self.assertNotIn(entry, self.window.inventory.entries)
        self.assertEqual(self.window.item_rules.reservations[0].room_id, "Kitchen")
        self.window.selected_weapon = self.weapon()
        self.window.show_room_allocations = False
        self.window.run_action_phase()
        self.assertIn("Jerry", self.window.room_assignments["Kitchen"])
        self.assertTrue(all(len(occupants) <= 2 for occupants in self.window.room_assignments.values()))
        self.assertFalse(self.window.room_assignments["Attic"])
        self.assertTrue(all(not observation.known_names for observation in self.window.house_view.observations.values()))
        self.assertTrue(self.window.commit_attack("Jerry", "Kitchen"))
        self.assertIn("occult_ritual", self.window.profile_store.read().unlocked)
        self.assertEqual(self.window.profile_store.read().counters["reserved_room_kill"], 1)
        self.assertFalse(self.window.commit_attack("Jerry", "Kitchen"))
        self.assertEqual(self.window.profile_store.read().counters["reserved_room_kill"], 1)

    def test_reservation_follows_actual_exchanged_mind_and_unlocks_on_its_corpse(self):
        self.conversation()
        dave_body = self.window.body_id("Dave")
        original_body = self.window.body_id("Alex")
        dave_mind = self.window.mind_id("Dave")
        entry = self.window.inventory.grant("room_reservation")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, dave_body, room_id="Kitchen"))
        self.window.exchange_player_mind(dave_body)
        self.window.selected_weapon = self.weapon()
        self.window.run_action_phase()
        self.assertEqual(self.window.mind_id("Alex"), dave_mind)
        self.assertIn("Alex", self.window.room_assignments["Kitchen"])
        self.assertNotIn("Dave", self.window.characters)
        self.assertTrue(self.window.commit_attack("Alex", "Kitchen"))
        attack = self.window.night_attacks[-1]
        self.assertEqual(attack["corpse_body_id"], original_body)
        self.assertEqual(attack["target_body_id"], original_body)
        self.assertEqual(self.window.profile_store.read().counters["reserved_room_kill"], 1)
        self.assertIn("occult_ritual", self.window.profile_store.read().unlocked)

    def test_muffle_and_due_curse_remain_on_body_after_actual_exchange(self):
        self.conversation()
        self.give("muffle", "Dave")
        self.arm_optional("occult_ritual", target="Dave")
        target_body = self.window.body_id("Dave")
        old_body = self.window.body_id("Alex")
        dave_data = self.window.characters["Dave"]
        self.window.exchange_player_mind(target_body)
        self.assertTrue(self.window.item_rules.is_muffled(target_body))
        self.assertFalse(self.window.item_rules.is_muffled(old_body))
        self.assertIs(self.window.characters["Alex"], dave_data)
        self.window.phase = "action"
        self.window.round_number = 3
        self.assertTrue(self.window.resolve_action_curses())
        self.assertFalse(self.window.player_alive)
        self.assertTrue(self.window.characters["Alex"]["alive"])
        record, = self.window.item_deaths.values()
        self.assertEqual(record.body_id, target_body)
        self.assertEqual(record.cause, game.DeathCause.CURSE)
        self.assertEqual(self.window.run_player_kills, 0)
        self.assertEqual(self.window.night_attacks, [])

    def test_reserved_mind_voodoo_corpse_unlock_and_original_attack_identity(self):
        self.conversation()
        target_body = self.window.body_id("Dave")
        original_body = self.window.body_id("Alex")
        target_mind = self.window.mind_id("Dave")
        entry = self.window.inventory.grant("room_reservation")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target_body, room_id="Kitchen"))
        self.window.selected_weapon = self.weapon("voodoo")
        self.window.run_action_phase()
        self.assertTrue(self.window.commit_attack("Dave", "Kitchen"))
        attack, = self.window.night_attacks
        self.assertEqual(attack["attacker_body_id"], original_body)
        self.assertEqual(attack["target_body_id"], target_body)
        self.assertEqual(attack["corpse_body_id"], original_body)
        self.assertEqual(self.window.identity_state.mind_for_body(original_body), target_mind)
        self.assertFalse(self.window.characters["Alex"]["alive"])
        self.assertEqual(self.window.victims_last_night, ["Alex"])
        self.assertEqual(self.window.run_player_kills, 1)
        self.assertEqual(self.window.profile_store.read().counters["reserved_room_kill"], 1)

    def test_actual_night_assignment_trap_records_reserved_corpse_once(self):
        self.conversation()
        target_body = self.window.body_id("Dave")
        entry = self.window.inventory.grant("room_reservation")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target_body, room_id="Attic"))
        self.arm_optional("booby_trap", room="Attic")
        self.window.selected_weapon = self.weapon()
        self.window.run_action_phase()
        self.assertFalse(self.window.characters["Dave"]["alive"])
        record, = (record for record in self.window.item_deaths.values() if record.body_id == target_body)
        self.assertEqual((record.room_id, record.day, record.cause), ("Attic", 2, game.DeathCause.TRAP))
        self.assertEqual(self.window.profile_store.read().counters["reserved_room_kill"], 1)
        self.assertEqual(self.window.night_attacks, [])
        self.window.finish_night()
        self.assertFalse(any("no murder occurred" in event.text for event in self.window.public_events))
        self.assertEqual(sum("Dave's body was found in the Attic" in event.text
                             for event in self.window.public_events), 1)
        self.assertIn("Attic", self.window.old_crime_scenes)
        self.assertNotIn("Attic", self.window.item_use_context().available_rooms)
        living = next(name for name, data in self.window.characters.items() if data["alive"])
        reservation = self.window.inventory.grant("room_reservation")
        self.assertFalse(self.window.use_inventory_item(
            reservation.instance_id, self.window.body_id(living), room_id="Attic"))
        self.assertIn(reservation, self.window.inventory.entries)
        self.window.run_action_phase()
        self.assertEqual(self.window.room_assignments["Attic"], [])
        self.assertNotIn("Attic", self.window.night_rooms)
        self.assertFalse(self.window.room_buttons["Attic"].isEnabled())

    def test_prevented_trap_without_corpse_does_not_close_its_room(self):
        self.conversation()
        self.give("guardian_angel")
        self.arm_optional("booby_trap", room="Attic")
        self.planned_rooms = {
            "Attic": [], "Kitchen": ["Dave", "Jerry"], "Library": ["Hank", "Owen"],
            "Basement": ["Debra"], "Cellar": [],
        }
        self.window.selected_weapon = self.weapon()
        with patch.object(game.random, "choice", side_effect=lambda choices: choices[-1]):
            self.window.run_action_phase()
        self.assertEqual(self.window.room_assignments["Attic"], [])
        self.assertFalse(self.window.resolve_room_entry_items(
            "shielded-player-entry", "Attic", {self.window.body_id("Alex")}))
        self.assertTrue(self.window.player_alive)
        self.assertFalse(self.window.item_rules.protection_armed)
        self.assertFalse(self.window.item_deaths)
        self.window.finish_night()
        self.assertNotIn("Attic", self.window.old_crime_scenes)
        self.assertIn("Attic", self.window.item_use_context().available_rooms)
        self.window.run_action_phase()
        self.assertIn("Attic", self.window.night_rooms)
        self.assertTrue(self.window.room_buttons["Attic"].isEnabled())

    def test_how_to_play_covers_current_items_progression_and_evidence(self):
        guide = game.HowToPlayPage(self.window)
        text = guide.findChild(QTextEdit).toPlainText()
        for feature in ("three items", "Inventory", "Muffle", "Spare Paper", "Guardian Angel", "next run", "photograph"):
            self.assertIn(feature, text)
        self.assertNotIn("hallucinat", text.lower())
        guide.close()

    def test_production_catalogue_uses_approved_unlocks_and_bare_is_initial_weapon(self):
        self.assertEqual(self.window.profile_store.available_weapons(), ("bare",))
        self.assertEqual(self.window.profile_store.item_pool(), eligible_item_pool())
        goals = {content.content_id: content.unlock_rule for content in CONTENT_CATALOGUE}
        self.assertEqual(goals["knife"], game.CONTENT_CATALOGUE[1].unlock_rule)
        self.window.select_weapon_and_start(self.weapon("knife"))
        self.assertEqual(self.window.phase, "weapon_select")
        self.assertIsNone(self.window.selected_weapon)

    def test_new_run_includes_ready_rewards_without_changing_earned_unlocks_or_preferences(self):
        store = self.window.profile_store
        for sequence, event_type in enumerate(("three_kills_one_run", "survived_spare_paper_vote",
                                                "marked_kill_completed"), 1):
            store.record(ProgressEvent("prior-run", sequence, event_type))
        store.set_item_enabled("booby_trap", False)
        earned = store.read()
        self.assertIn("revolver", earned.unlocked)
        self.assertNotIn("revolver", earned.disabled_items)
        next_window = game.GameWindow()
        try:
            next_window.player_name_input.setText("Alex")
            next_window.start_game()
            next_window.select_weapon_and_start(next(weapon for weapon in next_window.weapons
                                                     if weapon["id"] == "bare"))
            self.assertIn("revolver", next_window.run_item_pool)
            self.assertNotIn("booby_trap", next_window.run_item_pool)
            self.assertIn("presidential_decree", next_window.run_item_pool)
            self.assertEqual(next_window.item_drafts.eligible_item_ids, next_window.run_item_pool)
            with patch.object(next_window.item_drafts._rng, "random", return_value=.99):
                next_window.choose_room("Attic")
            offer = next_window.item_drafts.pending_offer
            self.assertIsNotNone(offer)
            self.assertIn("presidential_decree", offer.options)
            self.assertIn("revolver", offer.options)
            self.assertEqual(store.read(), earned)
        finally:
            next_window.close()
            next_window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_room_offer_blocks_action_until_resolved_and_stale_clicks_cannot_reenter(self):
        self.night()
        windows = set(self.app.topLevelWidgets())
        self.window.choose_room("Library")
        offer = self.window.item_drafts.pending_offer
        page = self.window.draft_page
        self.assertFalse(page.isWindow())
        self.assertNotIsInstance(page, QDialog)
        self.assertEqual(set(self.app.topLevelWidgets()), windows)
        self.assertIs(page.parentWidget(), self.window.centralWidget())
        self.assertEqual(self.window.layout.indexOf(page) + 1,
                         self.window.layout.indexOf(self.window.room_buttons_widget))
        self.assertTrue(self.window.room_buttons_widget.isHidden())
        self.assertTrue(self.window.weapon_action_panel.isHidden())
        self.assertTrue(self.window.characters["Debra"]["alive"])
        self.assertEqual(len(set(offer.options)), 3)
        self.window.apply_weapon_gating()
        self.window.set_room_buttons_enabled(True)
        self.window.position_weapon_panel()
        self.assertTrue(all(not button.isEnabled() for button in self.window.room_buttons.values()))
        self.assertTrue(self.window.weapon_action_panel.isHidden())
        self.window.choose_room("Attic")
        self.window.finish_room_choice("Attic")
        for configure in (self.window.open_pen_editor, self.window.open_blackmagic_dialog,
                          self.window.open_c4_dialog):
            configure()
        self.assertTrue(all(surface is None for surface in self.window._weapon_windows.values()))
        self.assertEqual(self.window.rooms_visited, [])
        self.assertEqual(self.window.item_drafts.pending_offer, offer)
        self.settle_offer("banana")
        self.assertFalse(self.window.characters["Debra"]["alive"])
        self.assertEqual(len(self.window.inventory.entries), 1)
        self.window.choose_item_offer(offer.offer_id, 0)
        self.assertEqual(len(self.window.inventory.entries), 1)

    def test_leave_button_skips_permanently_and_continues_accepted_action_once(self):
        self.night()
        self.window.choose_room("Basement")
        page = self.window.draft_page
        page.leave_button.click()
        page.leave_button.click()
        page.take_buttons[0].click()
        self.assertIsNone(self.window.item_drafts.pending_offer)
        self.assertIsNone(self.window.draft_page)
        self.assertEqual(self.window.rooms_visited, ["Basement"])
        self.assertIsNone(self.window.item_drafts.enter_room("Basement", self.window.inventory.snapshot()))
        self.assertFalse(self.window.inventory.entries)
        self.assertFalse(self.window.room_buttons_widget.isHidden())
        self.assertFalse(self.window.weapon_action_panel.isHidden())
        self.assertFalse(self.window.room_buttons["Basement"].isEnabled())
        self.assertTrue(self.window.room_buttons["Cellar"].isEnabled())
        self.assertEqual(sum(record.text == "You left the items behind."
                             for record in self.window.history_records), 1)

    def test_preexisting_weapon_editors_cannot_save_after_room_action_is_accepted(self):
        editors = (("pen", self.window.open_pen_editor),
                   ("blackmagic", self.window.open_blackmagic_dialog),
                   ("c4", self.window.open_c4_dialog))

        def configuration():
            return (self.window.pending_pen_note_text, self.window.black_magic_controller,
                    self.window.black_magic_target, self.window.knife_stage,
                    self.window.knife_used_bloodlust, self.window.c4_target_room)

        for weapon_id, open_editor in editors:
            with self.subTest(weapon=weapon_id):
                self.window.show_weapon_select_screen()
                self.night(weapon_id)
                if weapon_id == "pen":
                    self.window.pending_pen_note_text = "Accepted note"
                elif weapon_id == "blackmagic":
                    self.window.black_magic_controller, self.window.black_magic_target = "Dave", "Hank"
                elif weapon_id == "c4":
                    self.window.c4_target_room = "Kitchen"
                self.window.apply_weapon_gating()
                open_editor()
                editor = self.window._weapon_windows[weapon_id]
                if weapon_id == "pen":
                    editor.findChild(QTextEdit).setPlainText("Unsaved replacement")
                else:
                    boxes = editor.findChildren(QComboBox)
                    boxes[-1].setCurrentText("Jerry" if weapon_id == "blackmagic" else "Cellar")
                save = next(button for button in editor.findChildren(QPushButton) if button.text() == "Save")
                before = configuration()
                QTest.mouseClick(self.window.room_buttons["Basement"], Qt.LeftButton)
                offer = self.window.item_drafts.pending_offer
                self.assertIsNotNone(offer)
                self.assertIsNotNone(self.window.pending_room_action)
                history = tuple(self.window.history_records)
                save.click()
                self.assertEqual(configuration(), before)
                self.assertEqual(tuple(self.window.history_records), history)
                self.assertIs(self.window.item_drafts.pending_offer, offer)
                self.assertTrue(editor.isHidden())
                self.assertIsNone(self.window._weapon_windows[weapon_id])
                self.assertTrue(self.window.weapon_action_panel.isHidden())
                self.assertTrue(all(not button.isEnabled() for button in self.window.room_buttons.values()))
                self.window.build_weapon_action_panel()
                self.assertTrue(self.window.weapon_action_panel.isHidden())
                self.settle_offer()
                resolved = configuration()
                history = tuple(self.window.history_records)
                save.click()
                self.assertEqual(configuration(), resolved)
                self.assertEqual(tuple(self.window.history_records), history)
                self.assertEqual(self.window.rooms_visited, ["Basement"])
                self.assertEqual(self.window.inventory.entries, ())
                self.window.show_weapon_select_screen()
                self.night(weapon_id)
                open_editor()
                replacement = self.window._weapon_windows[weapon_id]
                self.assertIsNot(replacement, editor)
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertTrue(sip.isdeleted(editor))
                self.assertIs(self.window._weapon_windows[weapon_id], replacement)
                replacement.close()

    def test_weapon_editor_saves_still_configure_current_unaccepted_action(self):
        editors = (("pen", self.window.open_pen_editor),
                   ("blackmagic", self.window.open_blackmagic_dialog),
                   ("c4", self.window.open_c4_dialog))
        for weapon_id, open_editor in editors:
            with self.subTest(weapon=weapon_id):
                self.window.show_weapon_select_screen()
                self.night(weapon_id)
                self.window.apply_weapon_gating()
                open_editor()
                editor = self.window._weapon_windows[weapon_id]
                if weapon_id == "pen":
                    editor.findChild(QTextEdit).setPlainText("Current note")
                else:
                    boxes = editor.findChildren(QComboBox)
                    if weapon_id == "blackmagic":
                        boxes[0].setCurrentText("Dave")
                    boxes[-1].setCurrentText("Kitchen" if weapon_id == "c4" else "Hank")
                save = next(button for button in editor.findChildren(QPushButton) if button.text() == "Save")
                QTest.mouseClick(save, Qt.LeftButton)
                self.assertTrue(editor.isHidden())
                self.assertTrue(self.window.room_buttons["Basement"].isEnabled())
                if weapon_id == "pen":
                    self.assertEqual(self.window.pending_pen_note_text, "Current note")
                elif weapon_id == "blackmagic":
                    self.assertEqual((self.window.black_magic_controller, self.window.black_magic_target),
                                     ("Dave", "Hank"))
                else:
                    self.assertEqual(self.window.c4_target_room, "Kitchen")

    def test_room_acceptance_retires_editor_when_room_has_no_new_reward(self):
        self.night("pen")
        self.window.pending_pen_note_text = "Accepted note"
        self.window.apply_weapon_gating()
        previous = self.window.item_drafts.enter_room("Basement", self.window.inventory.snapshot())
        self.window.item_drafts.skip(previous.offer_id)
        self.window.open_pen_editor()
        editor = self.window._weapon_windows["pen"]
        editor.findChild(QTextEdit).setPlainText("Stale replacement")
        save = next(button for button in editor.findChildren(QPushButton) if button.text() == "Save")
        QTest.mouseClick(self.window.room_buttons["Basement"], Qt.LeftButton)
        self.assertIsNone(self.window.draft_page)
        self.assertIsNone(self.window.pending_room_action)
        self.assertEqual(self.window.rooms_visited, ["Basement"])
        self.assertTrue(editor.isHidden())
        save.click()
        self.assertEqual(self.window.pending_pen_note_text, "Accepted note")

    def test_offer_reroll_invalidates_old_buttons_and_only_spends_with_alternative(self):
        self.night()
        self.window.choose_room("Basement")
        offer = self.window.item_drafts.pending_offer
        self.assertFalse(self.window.draft_page.reroll_buttons[0].isEnabled())
        self.window.draft_page.reroll_buttons[0].click()
        self.assertEqual(self.window.item_drafts.rerolls_remaining, 1)
        self.assertEqual(self.window.item_drafts.pending_offer, offer)
        self.settle_offer()
        self.window.item_drafts = DraftState("player", tuple(game.ITEM_CATALOGUE))
        continuation = Mock()
        self.window.offer_room_item("Cellar", continuation)
        old = self.window.item_drafts.pending_offer
        old_page = self.window.draft_page
        old_page.reroll_buttons[1].click()
        updated = self.window.item_drafts.pending_offer
        self.assertIsNot(self.window.draft_page, old_page)
        self.assertTrue(old_page.isHidden())
        self.assertNotEqual(updated.offer_id, old.offer_id)
        self.assertEqual(self.window.item_drafts.rerolls_remaining, 0)
        old_page.take_buttons[1].click()
        old_page.leave_button.click()
        old_page.reroll_buttons[1].click()
        self.assertIs(self.window.item_drafts.pending_offer, updated)
        continuation.assert_not_called()
        self.assertFalse(self.window.inventory.entries)
        self.settle_offer()
        continuation.assert_called_once_with()

    def test_help_transcript_pause_and_escape_preserve_pending_inline_offer(self):
        self.night()
        self.window.choose_room("Basement")
        page = self.window.draft_page
        offer = self.window.item_drafts.pending_offer
        self.window.show_transcript()
        transcript = self.window.overlay_host.current_page
        self.assertIs(transcript, self.window.transcript_page)
        self.window.show_how_to_play()
        self.window.open_inventory()
        self.assertIs(self.window.overlay_host.current_page, transcript)
        QTest.keyClick(self.window.overlay_host, Qt.Key_Escape)
        self.window.show_how_to_play()
        self.assertIs(self.window.overlay_host.current_page, self.window.help_page)
        self.window.overlay_host.close_page()
        page.scroll.ensureWidgetVisible(page.help_button)
        QTest.mouseClick(page.help_button, Qt.LeftButton)
        self.assertTrue(page.help_label.isVisible())
        QTest.keyClick(page.help_button, Qt.Key_Escape)
        self.assertEqual(self.window.overlay_host.current_title, "Menu")
        self.assertEqual(sum(dialog.isVisible() for dialog in self.window.findChildren(QDialog)), 1)
        QTest.keyClick(self.window.overlay_host, Qt.Key_Escape)
        self.assertIsNone(self.window.overlay_host.current_page)
        self.assertIs(self.window.draft_page, page)
        self.assertIs(self.window.item_drafts.pending_offer, offer)
        self.assertEqual(self.window.rooms_visited, [])
        self.assertEqual(self.window.inventory.entries, ())
        self.assertFalse(any(record.text == "You left the items behind."
                             for record in self.window.history_records))

    def test_stale_phase_callbacks_cannot_mutate_offer_or_resume_room(self):
        self.night()
        continuation = Mock()
        self.window.offer_room_item("Basement", continuation)
        offer = self.window.item_drafts.pending_offer
        page = self.window.draft_page
        history = tuple(self.window.history_records)
        self.window.phase = "conversation"
        page.take_buttons[0].click()
        page.leave_button.click()
        self.window.reroll_item_offer(offer.offer_id, 1)
        self.assertIs(self.window.item_drafts.pending_offer, offer)
        self.assertEqual(self.window.inventory.entries, ())
        self.assertEqual(tuple(self.window.history_records), history)
        continuation.assert_not_called()

    def test_phase_transitions_cancel_obsolete_page_without_loot_or_continuation(self):
        for transition in (self.window.run_conversation_phase, self.window.run_action_phase,
                           self.window.show_weapon_select_screen,
                           lambda: self.window.end_game_screen(0)):
            with self.subTest(transition=transition):
                self.window.show_weapon_select_screen()
                self.night()
                continuation = Mock()
                self.window.offer_room_item("Basement", continuation)
                page = self.window.draft_page
                offer = self.window.item_drafts.pending_offer
                transition()
                self.assertIsNone(self.window.pending_room_action)
                self.assertIsNone(self.window.item_drafts.pending_offer)
                self.assertIsNone(self.window.draft_page)
                self.assertTrue(page.isHidden())
                self.window.choose_item_offer(offer.offer_id, 0)
                self.window.skip_item_offer(offer.offer_id)
                self.window.reroll_item_offer(offer.offer_id, 1)
                self.assertEqual(self.window.inventory.entries, ())
                continuation.assert_not_called()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertTrue(sip.isdeleted(page))

    def test_close_cancels_inline_offer_and_old_callbacks_without_accepting_action(self):
        self.night()
        continuation = Mock()
        self.window.offer_room_item("Basement", continuation)
        page = self.window.draft_page
        offer = self.window.item_drafts.pending_offer
        self.window.close()
        self.assertIsNone(self.window.draft_page)
        self.assertIsNone(self.window.pending_room_action)
        self.assertIsNone(self.window.item_drafts.pending_offer)
        self.window.choose_item_offer(offer.offer_id, 0)
        self.window.skip_item_offer(offer.offer_id)
        self.window.reroll_item_offer(offer.offer_id, 0)
        self.assertEqual(self.window.inventory.entries, ())
        continuation.assert_not_called()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(page))

    def test_laptop_offer_scrolls_below_corner_controls_and_leave_works_by_keyboard(self):
        self.window.showNormal()
        self.night()
        self.window.transcript_button.show()
        self.window.choose_room("Basement")
        page = self.window.draft_page
        self.app.processEvents()
        viewport = page.scroll.viewport()
        top = viewport.mapTo(self.window, QPoint(0, 0)).y()
        self.assertGreater(top, self.window.transcript_button.geometry().bottom())
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        self.app.setActiveWindow(self.window)
        page.take_buttons[0].setFocus()
        QTest.keyClick(page.take_buttons[0], Qt.Key_Tab)
        self.assertTrue(page.take_buttons[1].hasFocus())
        page.scroll.ensureWidgetVisible(page.leave_button)
        page.leave_button.setFocus()
        self.app.processEvents()
        position = page.leave_button.mapTo(viewport, QPoint(0, 0))
        self.assertGreaterEqual(position.y(), 0)
        self.assertLessEqual(position.y() + page.leave_button.height(), viewport.height())
        QTest.keyClick(page.leave_button, Qt.Key_Space)
        self.assertIsNone(self.window.draft_page)
        self.assertEqual(self.window.rooms_visited, ["Basement"])

    def test_restart_retires_old_offer_in_same_window_and_rejects_late_actions(self):
        self.night()
        continuation = Mock()
        self.window.offer_room_item("Basement", continuation)
        page = self.window.draft_page
        drafts = self.window.item_drafts
        offer = drafts.pending_offer
        native_id = int(self.window.winId())
        with patch.object(game.GameWindow, "__init__", side_effect=AssertionError("Unexpected window replacement")):
            self.window.restart_game()
        self.assertEqual(int(self.window.winId()), native_id)
        self.assertEqual(self.window.phase, "start")
        self.assertIsNone(self.window.draft_page)
        self.assertIsNone(self.window.pending_room_action)
        self.assertIsNone(self.window.item_drafts)
        self.assertIsNone(drafts.pending_offer)
        self.window.choose_item_offer(offer.offer_id, 0)
        self.window.skip_item_offer(offer.offer_id)
        self.window.reroll_item_offer(offer.offer_id, 0)
        self.assertEqual(self.window.inventory.entries, ())
        continuation.assert_not_called()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(page))

    def test_real_room_offer_excludes_owned_types_and_exhaustion_continues_action(self):
        self.night()
        self.window.inventory.grant("banana")
        self.window.inventory.grant("muffle")
        continuation = Mock()
        self.window.offer_room_item("Basement", continuation)
        offer = self.window.item_drafts.pending_offer
        self.assertEqual(offer.options, ("spare_paper",))
        buttons = list(self.window.draft_page.reroll_buttons.values())
        self.assertTrue(buttons)
        self.assertTrue(all(not button.isEnabled() for button in buttons))
        self.window.choose_item_offer(offer.offer_id, 0)
        continuation.assert_called_once_with()
        exhausted = Mock()
        self.window.offer_room_item("Cellar", exhausted)
        exhausted.assert_called_once_with()
        self.assertIsNone(self.window.pending_room_action)
        self.assertIsNone(self.window.item_drafts.pending_offer)
        self.assertIsNone(self.window.draft_page)

    def test_inventory_preserves_composing_draft_when_used(self):
        self.night()
        self.window.choose_room("Library")
        self.settle_offer("banana")
        self.window.interject_input.setText("A half-written alibi")
        entry = self.window.inventory.entries[0]
        self.window.open_inventory()
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertEqual(self.window.interject_input.text(), "A half-written alibi")
        self.assertEqual(self.window.items_used, 1)
        self.assertIn("Alex gave Dave a banana.", [record.text for record in self.window.public_events])
        self.assertEqual(self.window.characters["Dave"]["dispositions"][-1].text,
                         BANANA_DISPOSITION.format(giver="Alex", day=1))

    def test_item_use_rejects_busy_dead_targets_and_invalidates_failed_retry(self):
        self.conversation()
        entry = self.window.inventory.grant("banana")
        self.window.ai_busy = True
        self.assertFalse(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertEqual(len(self.window.inventory.entries), 1)
        self.window.ai_busy = False
        self.window.characters["Dave"]["alive"] = False
        self.assertFalse(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.window.characters["Dave"]["alive"] = True
        retry = Mock()
        self.window.retry_action = retry
        serial = self.window.request_serial
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertGreater(self.window.request_serial, serial)
        self.assertIsNone(self.window.retry_action)
        self.window.retry_ai_request()
        retry.assert_not_called()

    def test_muffle_excluded_from_generation_selection_and_stale_commits(self):
        self.conversation()
        self.give("muffle", "Dave")
        seen = []
        with patch.object(self.window.ai_runtime, "select_reply", side_effect=lambda state, events, dialogue, proposals:
                          seen.append(dict(proposals)) or next(iter(proposals))):
            self.window.run_multiple_responses()
            self.wait_for_workers()
        self.assertNotIn("Dave", [call.args[0].character for call in self.speech.call_args_list])
        self.assertNotIn("Dave", seen[0])
        before = tuple(self.window.public_dialogue)
        self.window.handle_best_response(game.SpeechProposal("Alex", "Forbidden articulate text"), "Dave")
        self.window.handle_single_response("Dave", game.SpeechProposal("Alex", "Forbidden articulate text"))
        self.assertEqual(tuple(self.window.public_dialogue), before)
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.assertIn("Dave", self.window.expected_voters)

    def test_muffle_expires_next_night_with_memory(self):
        self.conversation()
        self.give("muffle", "Dave")
        self.window.run_action_phase()
        # The first real night starts at round 2 from this round-1 decision point.
        self.assertEqual(self.window.round_number, 2)
        self.assertIsNone(self.window.item_rules.speech_override(self.window.body_id("Dave")))
        memories = self.window.characters["Dave"]["experiences"]
        self.assertEqual(memories[-2].text, MUFFLE_EXPIRED)
        event = next(event for event in self.window.night_arrivals.initial
                     if event.body_id == self.window.body_id("Dave"))
        self.assertEqual(memories[-1].text, f"At {event.time}, you entered the {event.room}.")

    def test_spare_paper_stacks_weights_freeze_and_resolve_once(self):
        self.conversation()
        self.give("spare_paper")
        self.give("spare_paper")
        ballots = {"Alex": "Dave", "Dave": "Alex", "Jerry": "Alex", "Owen": "Alex", "Hank": "Alex", "Debra": "NONE"}
        self.vote(ballots)
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertTrue(self.window.player_alive)
        self.assertEqual(self.window.item_rules.pending_ballot_bonus, 0)
        records = [record.text for record in self.window.public_events]
        self.assertEqual(records.count("Alex's vote for Dave counted as 5 votes."), 1)
        count = self.window.profile.counters["survived_vote"]
        self.window.tally_votes()
        self.assertEqual(self.window.profile.counters["survived_vote"], count)
        self.assertEqual(self.window.innocents_executed, 1)

    def test_guardian_prevents_execution_without_false_death_or_innocence_then_consumes(self):
        self.conversation()
        self.give("guardian_angel")
        duplicate = self.window.inventory.grant("guardian_angel")
        self.assertFalse(self.window.use_inventory_item(duplicate.instance_id))
        self.assertIn(duplicate, self.window.inventory.entries)
        ballots = {name: "Alex" for name in self.window.characters}
        ballots["Alex"] = "NONE"
        self.vote(ballots)
        self.assertTrue(self.window.player_alive)
        self.assertFalse(self.window.game_over)
        self.assertFalse(self.window.item_rules.protection_armed)
        self.assertEqual(self.window.innocents_executed, 0)
        self.assertEqual(self.window.profile.counters["survived_vote"], 1)
        self.assertIn("Against all expectation, Alex survives.", [event.text for event in self.window.public_events])
        self.vote(ballots)
        self.assertFalse(self.window.player_alive)
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.profile.counters["survived_vote"], 1)

    def test_knife_attacks_two_actual_rooms_and_rewards_second_physical_entry(self):
        self.night("knife")
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertTrue(self.window.characters["Hank"]["alive"])
        self.window.knife_push_button.click()
        self.window.choose_room("Kitchen")
        self.assertEqual(self.window.item_drafts.pending_offer.room_id, "Kitchen")
        self.settle_offer("muffle")
        self.window.kill_victim("Hank")
        self.assertFalse(self.window.characters["Hank"]["alive"])
        self.assertEqual([(attack["target_label"], attack["room"]) for attack in self.window.night_attacks],
                         [("Dave", "Attic"), ("Hank", "Kitchen")])
        for witness, victim, room in (("Jerry", "Dave", "Attic"), ("Owen", "Hank", "Kitchen")):
            event = next(event for event in self.window.night_arrivals.events
                         if event.body_id == self.window.body_id(witness))
            memories = self.window.characters[witness]["experiences"]
            expected = [f"At {event.time}, you entered the {room}.",
                        f"At {event.time}, {victim} was already in the {room} when you entered."]
            if witness == "Jerry":
                expected.append(f"At {event.time}, Alex was already in the {room} when you entered.")
            if witness == "Owen":
                expected.append(f"At {self.window.night_arrivals.clock}, you saw Alex enter the {room}.")
            self.assertEqual(len(memories), len(expected) + 1)
            self.assertEqual([record.text for record in memories[:-1]], expected)
            self.assertIn(f"Alex kill {victim} in the {room}", memories[-1].text)
        self.assertEqual(self.window.profile.counters["player_kill"], 2)
        self.assertEqual(self.window.item_drafts.seen_rooms, {"Attic", "Kitchen"})
        self.assertEqual(len(self.window.inventory.entries), 1)

    def test_knife_does_not_kill_primary_twice(self):
        self.night("knife")
        self.window.choose_room("Library")
        self.settle_offer()
        self.window.knife_cleanup_button.click()
        self.assertEqual(len(self.window.night_attacks), 1)
        self.assertEqual(self.window.profile.counters["player_kill"], 1)

    def test_remote_blast_does_not_offer_reward_for_target_room(self):
        self.night("c4")
        self.window.c4_target_room = "Kitchen"
        self.window.choose_room("Basement")
        self.settle_offer()
        self.assertEqual(self.window.item_drafts.seen_rooms, {"Basement"})
        self.assertEqual({attack["target_label"] for attack in self.window.night_attacks}, {"Hank", "Owen"})
        self.assertFalse(self.window.characters["Hank"]["alive"])
        self.assertFalse(self.window.characters["Owen"]["alive"])

    def test_voodoo_preserves_body_affinity_and_history_with_former_body_guardian(self):
        self.conversation()
        self.give("banana", "Jerry")
        self.give("guardian_angel")
        original_player_id = self.window.body_id("Alex")
        target_id = self.window.body_id("Dave")
        self.window.append_speech("Alex", "My old statement")
        old_statement = self.window.public_dialogue[-1]
        self.window.phase = "weapon_select"
        self.night("voodoo")
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertEqual(self.window.player_name, "Dave")
        self.assertEqual(self.window.body_id(self.window.player_name), target_id)
        self.assertTrue(self.window.characters["Alex"]["alive"])
        self.assertFalse(self.window.item_rules.protection_armed)
        self.assertEqual(self.window.item_rules.affinities[0].subject_body_id, original_player_id)
        self.assertIn(old_statement, self.window.public_dialogue)
        self.assertEqual(old_statement.speaker, "Alex")
        record = self.window.characters["Jerry"]["experiences"][-1].text
        self.assertEqual(record, "You (Jerry) were in the Attic. You saw Alex stab Dave.")
        self.assertTrue(self.window.resolve_death("Dave", "test-new-body").killed)
        self.assertFalse(self.window.player_alive)
        self.assertFalse(self.window.item_rules.protection_armed)

    def test_unlocks_saved_from_actions_only_and_frozen_until_next_run(self):
        self.night()
        original_pool = self.window.run_item_pool
        self.window.append_speech("Dave", "I killed three people and survived a vote.")
        self.assertFalse(self.path.exists())
        self.window.choose_room("Library")
        self.settle_offer()
        self.assertIn("knife", self.window.profile.unlocked)
        self.assertNotIn("knife", self.window.run_weapons)
        self.assertEqual(self.window.run_item_pool, original_pool)
        self.assertEqual(self.window.profile_store.available_weapons(), ("bare", "knife"))

    def test_save_failure_preserves_order_and_retries_same_sequences_without_double_progress(self):
        with patch.object(self.window.profile_store, "record", side_effect=ProfileError("Disk unavailable")):
            self.window.queue_progress("player_kill")
            self.window.queue_progress("banana_given")
        pending = tuple(self.window.pending_progress)
        self.assertEqual([event.sequence for event in pending], [1, 2])
        self.assertTrue(self.window.progress_retry_button.isVisible())
        self.window.retry_progress()
        profile = self.window.profile_store.read()
        self.assertEqual(dict(profile.counters), {"player_kill": 1, "banana_given": 1})
        self.assertEqual(profile.last_sequences[self.window.run_id], 2)
        self.assertFalse(self.window.pending_progress)
        self.window.retry_progress()
        self.assertEqual(dict(self.window.profile_store.read().counters), dict(profile.counters))

    def test_three_actual_kills_unlock_c4_once_and_save_retry_does_not_recount(self):
        self.night()
        self.assertTrue(self.window.commit_attack("Dave", "Attic"))
        self.assertFalse(self.window.commit_attack("Dave", "Attic"))
        self.assertTrue(self.window.commit_attack("Jerry", "Attic"))
        self.assertEqual(self.window.run_player_kills, 2)
        self.assertNotIn("c4", self.window.profile.unlocked)
        with patch.object(self.window.profile_store, "record", side_effect=ProfileError("Disk unavailable")):
            self.assertTrue(self.window.commit_attack("Hank", "Kitchen"))
        self.assertEqual([event.event_type for event in self.window.pending_progress],
                         ["player_kill", "three_kills_one_run"])
        self.window.retry_progress()
        self.window.retry_progress()
        self.assertEqual(self.window.run_player_kills, 3)
        self.assertEqual(self.window.profile.counters["player_kill"], 3)
        self.assertEqual(self.window.profile.counters["three_kills_one_run"], 1)
        self.assertIn("c4", self.window.profile.unlocked)
        self.assertNotIn("c4", self.window.run_weapons)

    def test_execution_and_prevented_death_do_not_count_towards_c4(self):
        self.conversation()
        self.give("guardian_angel")
        self.assertTrue(self.window.resolve_death("Alex", "prevented").prevented)
        self.assertTrue(self.window.resolve_death("Dave", "execution").killed)
        self.assertEqual(self.window.run_player_kills, 0)
        self.assertNotIn("three_kills_one_run", self.window.profile.counters)

    def test_corrupt_profile_is_never_overwritten_or_silently_reset(self):
        self.path.write_text("not a profile", encoding="utf-8")
        self.assertFalse(self.window.load_profile())
        self.window.queue_progress("player_kill")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "not a profile")
        self.assertEqual(self.window.pending_progress[0].sequence, 1)
        self.assertIn("invalid", self.window.progress_notice.text())
        self.assertEqual(self.window.profile_store.path, self.path)

    def test_witness_unlock_uses_living_body_ids_not_dialogue_claims(self):
        self.night()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertIn(self.window.body_id("Jerry"), self.window.witness_body_ids)
        ballots = {name: "NONE" for name, data in self.window.characters.items() if data["alive"]}
        ballots["Alex"] = "NONE"
        self.vote(ballots)
        self.assertIn("stopwatch", self.window.profile.unlocked)
        self.assertEqual(self.window.profile.counters["survived_witnessed_vote"], 1)

    def prepare_exchanged_witness(self):
        self.night()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        original_player_body = self.window.body_id("Alex")
        witness_body = self.window.body_id("Jerry")
        witness_data = self.window.characters["Jerry"]
        memories = tuple(witness_data["experiences"])
        self.assertEqual(self.window.witness_body_ids, {witness_body})
        self.window.exchange_player_mind(witness_body)
        self.assertIs(self.window.characters["Alex"], witness_data)
        self.assertEqual(tuple(witness_data["experiences"]), memories)
        self.assertEqual(self.window.witness_body_ids, {original_player_body})
        self.assertNotIn(self.window.body_id(self.window.player_name), self.window.witness_body_ids)

    def test_living_witness_mind_still_unlocks_stopwatch_after_exchange(self):
        self.prepare_exchanged_witness()
        ballots = {name: "NONE" for name, data in self.window.characters.items() if data["alive"]}
        ballots[self.window.player_name] = "NONE"
        self.vote(ballots)
        self.assertIn("stopwatch", self.window.profile.unlocked)
        self.assertEqual(self.window.profile.counters["survived_witnessed_vote"], 1)

    def test_exchanged_witness_must_still_be_alive_to_unlock_stopwatch(self):
        self.prepare_exchanged_witness()
        self.window.resolve_death("Alex", "exchanged-witness-death")
        ballots = {name: "NONE" for name, data in self.window.characters.items() if data["alive"]}
        ballots[self.window.player_name] = "NONE"
        self.vote(ballots)
        self.assertNotIn("stopwatch", self.window.profile.unlocked)
        self.assertNotIn("survived_witnessed_vote", self.window.profile.counters)

    def test_unused_inventory_is_retained_across_multiple_nights(self):
        self.night()
        self.window.choose_room("Library")
        self.settle_offer("muffle")
        entry = self.window.inventory.entries[0]
        self.window.run_action_phase()
        self.assertIn(entry, self.window.inventory.entries)
        self.assertEqual(self.window.round_number, 2)
        self.assertIsNone(self.window.item_drafts.enter_room("Library", self.window.inventory.snapshot()))

    def test_dead_witness_does_not_unlock_stopwatch(self):
        self.night()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertIn(self.window.body_id("Jerry"), self.window.witness_body_ids)
        self.window.resolve_death("Jerry", "test-witness-death")
        ballots = {name: "NONE" for name, data in self.window.characters.items() if data["alive"]}
        ballots["Alex"] = "NONE"
        self.vote(ballots)
        self.assertNotIn("stopwatch", self.window.profile.unlocked)
        self.assertNotIn("survived_witnessed_vote", self.window.profile.counters)

    def test_delayed_death_trace_distinguishes_replays_and_snapshots_actual_corpses(self):
        trace = TraceRun(enabled=True, root=Path(self.temp.name) / "traces")
        self.window.trace = trace
        trace.start({"gameplay_run_id": self.window.run_id})
        self.conversation()
        self.arm_optional("booby_trap", room="Attic")
        self.window.phase = "action"
        self.window.round_number = 2
        body = next(body for body in self.window.body_states() if body.label == "Dave")
        resolution = self.window.item_rules.resolve_entry("traced-entry", "Attic", (body,), 2)
        self.assertTrue(self.window.apply_delayed_item_resolution(resolution, room_id="Attic"))
        self.assertFalse(self.window.apply_delayed_item_resolution(resolution, room_id="Attic"))
        replay = self.window.item_rules.resolve_entry("traced-entry", "Attic", (body,), 2)
        self.assertFalse(self.window.apply_delayed_item_resolution(replay, room_id="Attic"))
        self.window.publish_item_bodies()
        self.window.publish_item_bodies()
        self.window.trace_state("item-death-proof")
        records = [json.loads(line) for line in (trace.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        decisions = [record["payload"] for record in records if record["kind"] == "item_death_resolved"]
        decision, = resolution.decisions
        self.assertEqual([entry["applied"] for entry in decisions], [True, False, False])
        self.assertEqual([entry["is_new"] for entry in decisions], [True, True, False])
        for entry in decisions:
            self.assertEqual((entry["attempt_id"], entry["target_body_id"], entry["cause"], entry["outcome"]),
                             (decision.attempt_id, body.body_id, "trap", "killed"))
        state = [record["payload"]["state"] for record in records if record["kind"] == "state_snapshot"][-1]
        self.assertFalse(state["characters"]["Dave"]["alive"])
        self.assertEqual(state["player_items"]["attacks"], [])
        corpse, = state["player_items"]["item_deaths"].values()
        self.assertEqual((corpse["body_id"], corpse["room_id"], corpse["cause"]), (body.body_id, "Attic", "trap"))
        self.assertEqual(state["player_items"]["published_item_deaths"], [decision.attempt_id])
        self.assertEqual(self.window.run_player_kills, 1)
        self.assertTrue(trace.enabled)

    def test_item_effects_and_frozen_vote_weights_have_serializable_developer_snapshots(self):
        trace = TraceRun(enabled=True, root=Path(self.temp.name) / "traces")
        self.window.trace = trace
        self.window.ai_runtime.trace = trace
        trace.start({"gameplay_run_id": self.window.run_id})
        self.conversation()
        self.give("muffle", "Dave")
        self.give("spare_paper")
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.assertTrue(trace.enabled)
        records = [json.loads(line) for line in (trace.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        state = [record["payload"]["state"] for record in records if record["kind"] == "state_snapshot"][-1]
        self.assertEqual(state["player_items"]["vote_weights"][self.window.body_id("Alex")], 3)
        self.assertEqual(state["player_items"]["muffles"][0]["target_body_id"], self.window.body_id("Dave"))
        self.assertEqual(state["player_items"]["run_id"], self.window.run_id)

    def test_using_any_item_blocks_the_bare_hands_no_item_victory_unlock(self):
        self.conversation()
        self.give("banana", "Jerry")
        self.window.phase = "weapon_select"
        self.night()
        for name in ("Hank", "Owen", "Debra"):
            self.window.characters[name]["alive"] = False
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertTrue(self.window.game_over)
        self.assertNotIn("voodoo", self.window.profile.unlocked)
        self.assertNotIn("bare_hands_no_item_victory", self.window.profile.counters)

    def use_card(self, item_id, target=None, message=None):
        entry = self.window.inventory.grant(item_id)
        self.window.open_inventory()
        page = self.window.inventory_page
        if target is not None:
            box = page.target_boxes[entry.instance_id]
            index = box.findData(self.window.body_id(target))
            self.assertGreaterEqual(index, 0)
            box.setCurrentIndex(index)
        if message is not None:
            page.message_boxes[entry.instance_id].setPlainText(message)
        page.use_buttons[entry.instance_id].click()
        if item_id == "banana":
            self.wait_for_workers()
            self.window.overlay_host.close_page()
        return entry

    def watch_night(self, weapon_id="bare", marked="Dave"):
        self.night(weapon_id)
        self.conversation()
        self.use_card("watch_this_drive", marked)
        self.window.overlay_host.close_page()
        self.window.run_action_phase()

    def test_projected_voice_preserves_required_answer_and_direct_cap(self):
        self.conversation()
        self.window.append_speech("Dave", "An answer.", direct_reply=True)
        self.window.append_speech("Dave", "Another answer.", direct_reply=True)
        self.window.append_speech("Detective", "Alex, where were you?", "Alex")
        self.use_card("throw_voice", "Hank", "Dave, Alex was nowhere near me.")
        self.assertEqual(self.window.public_dialogue[-1].speaker, "Hank")
        self.assertIsNone(self.window.public_dialogue[-1].addressee)
        self.assertEqual(self.window.direct_replies.count_for(self.window.body_id("Dave")), 2)
        self.assertTrue(self.window.player_reply_required())
        self.assertFalse(self.window.pass_button.isEnabled())
        self.assertFalse(self.window.can_call_vote())
        private = self.window.characters["Hank"]["experiences"][-1].text
        self.assertEqual(private, 'You heard your own voice say: "Dave, Alex was nowhere near me." You did not say those words.')
        self.window.append_speech("Alex", "Here is my account.")
        self.assertFalse(self.window.player_reply_required())

    def test_dark_whisper_dead_target_and_private_revelation_use_actual_controls(self):
        self.conversation()
        self.window.characters["Dave"]["alive"] = False
        entry = self.use_card("dark_whisper", "Dave", "A voice from the darkness.")
        self.assertNotIn(entry, self.window.inventory.entries)
        self.assertEqual(self.window.public_events[-1].text,
            'Everyone hears the unmistakeable voice of the dead Dave, loudly and clearly they speak:\n\n"A voice from the darkness."')
        before = tuple(self.window.public_events)
        self.use_card("divine_revelation", "Hank", "<truth> & certainty")
        self.assertEqual(tuple(self.window.public_events), before)
        self.assertEqual(self.window.characters["Hank"]["experiences"][-1].text,
            'You hear a voice inside your mind that seems to come from a higher power: "<truth> & certainty"')
        for name in ("Jerry", "Owen", "Debra"):
            self.assertFalse(any("<truth>" in record.text for record in self.window.characters[name]["experiences"]))

    def test_invalid_message_is_not_consumed_and_error_stays_in_inventory(self):
        self.conversation()
        entry = self.use_card("divine_revelation", "Hank", "   ")
        page = self.window.inventory_page
        self.assertIn(entry, self.window.inventory.entries)
        self.assertFalse(page.feedback_label.isHidden())
        page.message_boxes[entry.instance_id].setPlainText("x" * 2001)
        page.use_buttons[entry.instance_id].click()
        self.assertIn(entry, self.window.inventory.entries)
        self.assertEqual(self.window.items_used, 0)

    def test_lucky_match_and_deep_pockets_keep_all_three_picks_inline(self):
        self.night()
        self.conversation()
        self.window.item_drafts = DraftState("player", eligible_item_pool())
        self.use_card("lucky_match")
        self.use_card("deep_pockets")
        self.assertEqual(self.window.item_drafts.rerolls_remaining, 4)
        self.window.overlay_host.close_page()
        self.window.run_action_phase()
        self.window.choose_room("Attic")
        first = self.window.draft_page
        self.assertEqual(self.window.item_drafts.pending_offer.picks_remaining, 3)
        first.take_buttons[0].click()
        self.assertEqual(len(self.window.inventory.entries), 1)
        self.assertEqual(self.window.item_drafts.pending_offer.picks_remaining, 2)
        self.assertIsNotNone(self.window.pending_room_action)
        self.assertTrue(self.window.characters["Dave"]["alive"])
        first.take_buttons[1].click()
        self.assertEqual(len(self.window.inventory.entries), 1)
        self.window.draft_page.take_buttons[0].click()
        self.assertEqual(len(self.window.inventory.entries), 2)
        self.assertEqual(self.window.item_drafts.pending_offer.picks_remaining, 1)
        self.assertIsNotNone(self.window.pending_room_action)
        self.window.draft_page.take_buttons[0].click()
        self.assertEqual(len(self.window.inventory.entries), 3)
        self.assertIsNone(self.window.pending_room_action)
        self.assertIsNone(self.window.draft_page)

    def test_mail_ballot_is_selected_in_vote_ui_and_skips_reserved_ai(self):
        self.conversation()
        self.use_card("mail_in_ballot", "Dave")
        self.window.overlay_host.close_page()
        with patch.object(self.window.ai_runtime, "vote", return_value="NONE") as vote:
            self.window.run_voting_phase()
            self.assertIsNone(self.window.vote_snapshot)
            old_button = next(button for button in self.window.vote_buttons_widget.findChildren(QPushButton)
                              if button.text() == "Vote for Hank")
            old_button.click()
            self.assertEqual(self.window.ballot_actor, "Dave")
            old_button.click()
            self.assertNotIn("Dave", self.window.votes)
            buttons = self.window.vote_buttons_widget.findChildren(QPushButton)
            self.assertNotIn("Vote for Dave", [button.text() for button in buttons])
            next(button for button in buttons if button.text() == "Vote for Alex").click()
            self.wait_for_workers()
            self.assertEqual(self.window.votes["Dave"], "Alex")
            self.assertNotIn("Dave", [call.args[0].character for call in vote.call_args_list])
            self.assertEqual(self.window.vote_snapshot.proxy_ballots[self.window.body_id("Dave")], self.window.body_id("Alex"))
            self.assertTrue(self.window.voting_resolved)

    def enable_decree_for_test(self):
        catalogue = dict(game.ITEM_CATALOGUE)
        catalogue["presidential_decree"] = replace(catalogue["presidential_decree"], default=True)
        return patch.object(game, "ITEM_CATALOGUE", catalogue)

    def test_decree_is_available_in_production_inventory_and_callback(self):
        self.conversation()
        entry = self.window.inventory.grant("presidential_decree")
        self.window.open_inventory()
        self.assertTrue(self.window.inventory_page.use_buttons[entry.instance_id].isEnabled())
        self.assertTrue(self.window.use_inventory_item(entry.instance_id))
        self.assertNotIn(entry, self.window.inventory.entries)
        self.assertTrue(self.window.item_rules.skip_next_vote)

    def test_decree_skips_without_ballots_or_achievements_and_preserves_next_actual_vote(self):
        self.conversation()
        self.give("spare_paper")
        self.give("stay_of_execution", "Alex")
        self.give("mail_in_ballot", "Dave")
        with self.enable_decree_for_test():
            self.give("presidential_decree")
        self.window.minimum_to_call_vote = 0
        self.window.witness_body_ids.add(self.window.body_id("Jerry"))
        public_before = tuple(self.window.public_events)
        counters_before = dict(self.window.profile.counters)
        with patch.object(self.window, "display_vote_buttons", side_effect=AssertionError("Skipped vote")), patch.object(
                self.window, "start_ai_request", side_effect=AssertionError("Skipped vote")), patch.object(
                self.window, "resolve_death", side_effect=AssertionError("Skipped vote")):
            self.window.call_vote()
        self.assertTrue(self.window.voting_resolved)
        self.assertTrue(self.window.vote_snapshot.skipped)
        self.assertFalse(self.window.awaiting_player_vote)
        self.assertEqual(self.window.votes, {})
        self.assertEqual(self.window.expected_voters, set())
        self.assertEqual(tuple(self.window.public_events), public_before)
        self.assertEqual(dict(self.window.profile.counters), counters_before)
        self.assertFalse(self.window.item_rules.skip_next_vote)
        self.assertEqual(dict(self.window.item_rules.proxy_voters), {})
        self.assertEqual(self.window.item_rules.pending_ballot_bonus, 2)
        self.assertEqual(self.window.item_rules.execution_stays, (self.window.body_id("Alex"),))
        self.assertGreaterEqual(self.window.layout.indexOf(self.window.continue_button), 0)
        vote_id = self.window.vote_id
        with patch.object(self.window, "add_continue_button") as continuation:
            self.window.player_vote("Dave", voter="Alex", vote_id=vote_id)
            self.window.collect_ai_vote("Jerry", "Alex")
            self.window.tally_votes()
            self.window.start_vote_requests()
            self.window.run_voting_phase()
            self.window.call_vote()
        continuation.assert_not_called()
        self.assertEqual(self.window.vote_id, vote_id)
        self.assertEqual(self.window.votes, {})
        with patch.object(self.window, "run_action_phase") as next_night:
            self.window.continue_button.click()
        next_night.assert_called_once_with()
        self.window.round_number = 2
        self.window.item_rules.begin_night(2, self.window.body_states())
        ballots = {name: "Alex" for name in self.window.characters}
        ballots["Alex"] = "Dave"
        self.vote(ballots)
        self.assertFalse(self.window.vote_snapshot.skipped)
        self.assertEqual(self.window.vote_snapshot.bonus, 2)
        self.assertEqual(self.window.vote_snapshot.proxy_ballots, {})
        self.assertIn("Alex's vote for Dave counted as 3 votes.",
                      [event.text for event in self.window.public_events])
        self.assertTrue(self.window.player_alive)
        self.assertEqual(self.window.innocents_executed, 0)
        self.assertEqual(self.window.item_rules.pending_ballot_bonus, 0)
        self.assertEqual(self.window.item_rules.execution_stays, ())
        self.assertEqual(self.window.profile.counters["survived_vote"], 1)
        self.assertEqual(self.window.profile.counters["survived_spare_paper_vote"], 1)
        self.assertEqual(self.window.profile.counters["survived_witnessed_vote"], 1)

    def test_decree_waits_for_an_eligible_vote_boundary(self):
        self.conversation()
        with self.enable_decree_for_test():
            self.give("presidential_decree")
        self.window.minimum_to_call_vote = 0
        self.window.append_speech("Detective", "Alex, answer.", "Alex")
        self.window.call_vote()
        self.window.run_voting_phase()
        self.assertEqual(self.window.phase, "conversation")
        self.assertTrue(self.window.item_rules.skip_next_vote)
        self.window.append_speech("Alex", "My account.")
        self.window.ai_busy = True
        self.window.call_vote()
        self.window.run_voting_phase()
        self.assertEqual(self.window.phase, "conversation")
        self.assertTrue(self.window.item_rules.skip_next_vote)
        self.window.ai_busy = False
        self.window.minimum_to_call_vote = 100
        self.window.call_vote()
        self.assertEqual(self.window.phase, "conversation")
        self.window.minimum_to_call_vote = 0
        with patch.object(self.window, "start_ai_request", side_effect=AssertionError("Skipped vote")):
            self.window.call_vote()
        self.assertTrue(self.window.voting_resolved)
        self.assertFalse(self.window.item_rules.skip_next_vote)

    def test_player_ballot_callback_before_first_vote_is_ignored(self):
        history = tuple(self.window.history_records)
        self.window.player_vote("Dave")
        self.window.collect_player_vote("Dave")
        self.assertEqual(tuple(self.window.history_records), history)
        self.assertEqual(self.window.phase, "weapon_select")

    def test_stay_protects_chosen_body_without_false_execution_or_innocence(self):
        self.conversation()
        self.use_card("stay_of_execution", "Alex")
        self.window.overlay_host.close_page()
        ballots = {name: "Alex" for name in self.window.characters}
        ballots["Alex"] = "Dave"
        self.vote(ballots)
        self.assertTrue(self.window.player_alive)
        self.assertFalse(self.window.game_over)
        self.assertEqual(self.window.innocents_executed, 0)
        self.assertIn("Against all expectation, Alex survives.", [event.text for event in self.window.public_events])

    def test_pocket_gavel_honors_required_reply_then_opens_normal_vote(self):
        self.conversation()
        self.window.append_speech("Detective", "Alex, answer.", "Alex")
        entry = self.use_card("pocket_gavel")
        self.assertIn(entry, self.window.inventory.entries)
        self.assertEqual(self.window.phase, "conversation")
        self.window.append_speech("Alex", "My account.")
        self.window.inventory_page.use_buttons[entry.instance_id].click()
        self.wait_for_workers()
        self.assertEqual(self.window.phase, "voting")
        self.assertIsNone(self.window.overlay_host.current_page)
        self.assertLess(len(self.window.current_conversation), self.window.minimum_to_call_vote)

    def test_listening_glass_reveals_only_authoritative_night_record_to_player(self):
        self.night()
        self.window.remember("Jerry", "experience", "Earlier private record.")
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        view = next(view for view in self.window.night_memory_views if view.body_id == self.window.body_id("Jerry"))
        self.assertFalse(any(record.text == "Earlier private record." for record in view.experiences))
        self.window.remember("Jerry", "experience", "Later private revelation.")
        before = tuple(self.window.public_events)
        self.use_card("listening_glass", "Jerry")
        self.assertEqual(tuple(self.window.public_events), before)
        self.assertIn(view.experiences[-1].text, self.window.history_records[-2].text)
        self.assertNotIn("Later private revelation.", self.window.history_records[-2].text)
        self.assertIn(view.experiences[-1].text, self.window.inventory_page.feedback_label.text())
        self.assertFalse(self.window.inventory_page.feedback_label.isHidden())

    def test_forged_note_binds_next_actual_victim_and_is_announced_once(self):
        self.night()
        self.conversation()
        self.use_card("forged_note", message="Trust the banana.")
        self.window.overlay_host.close_page()
        self.window.run_action_phase()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        text = "The Detective reveals that a note was found by the body, it reads: Trust the banana. - Signed: Dave"
        self.assertEqual([event.text for event in self.window.public_events].count(text), 1)
        self.assertIsNone(self.window.item_rules.prepared_note)
        self.assertEqual(self.window.morning_item_notes, [])

    def test_known_equipment_refreshes_after_kill_before_watch_bonus(self):
        self.window.npc_items = game.NPCItemState({"Dave": "camera"})
        self.watch_night()
        self.assertEqual(self.window.npc_equipment_label.text(), "")
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.phase, "action")
        self.assertTrue(self.window.bonus_action_pending)
        self.assertTrue(self.window.room_buttons_widget.isVisible())
        self.assertEqual(self.window.npc_equipment_label.text(), "")
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.assertEqual(dict(self.window.npc_items.equipment), {"Dave": "camera"})
        self.assertFalse(any(record.text == "Known equipment: Dave is carrying a Camera."
                             for record in self.window.history_records))

    def test_known_equipment_refresh_after_conversation_death_stays_hidden(self):
        self.window.npc_items = game.NPCItemState({"Hank": "camera"})
        self.night()
        self.window.run_conversation_phase()
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.assertTrue(self.window.apply_item_events(self.resolved_item_events()))
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.npc_equipment_label.text(), "")
        self.assertTrue(self.window.npc_equipment_label.isHidden())

    def test_known_equipment_excludes_possessed_player_identity(self):
        self.window.npc_items = game.NPCItemState({"Dave": "camera"})
        self.night("voodoo")
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertEqual(self.window.player_name, "Dave")
        self.assertTrue(self.window.player_alive)
        self.assertNotIn("Dave", self.window.characters)
        self.assertEqual(self.window.npc_equipment_label.text(), "")
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.assertEqual(dict(self.window.npc_items.equipment), {"Dave": "camera"})

    def test_known_equipment_is_logically_hidden_at_game_over(self):
        self.window.npc_items = game.NPCItemState({"Hank": "camera"})
        self.night()
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.window.end_game_screen(0)
        self.assertTrue(self.window.npc_equipment_label.isHidden())

    def test_watch_bonus_new_room_gets_first_reward_and_basic_attack(self):
        self.watch_night("stopwatch")
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertTrue(self.window.bonus_action_pending)
        self.assertEqual(self.window.phase, "action")
        self.assertFalse(self.window._night_memories_written)
        self.window.choose_room("Kitchen")
        self.assertIsNotNone(self.window.draft_page)
        self.window.draft_page.take_buttons[0].click()
        self.window.kill_victim("Hank")
        self.assertEqual([attack["weapon_id"] for attack in self.window.night_attacks], ["stopwatch", "bare"])
        self.assertEqual(self.window.item_rules.bonus_actions, 0)
        self.assertEqual(self.window.phase, "conversation")
        self.assertTrue(any("You witnessed Alex kill Hank" in record.text for record in self.window.characters["Owen"]["experiences"]))
        self.assertFalse(self.window.characters["Owen"]["dispositions"])
        self.assertIn("booby_trap", self.window.profile.unlocked)

    def test_watch_bonus_revisited_room_has_no_second_reward(self):
        self.watch_night()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.window.choose_room("Attic")
        self.assertIsNone(self.window.draft_page)
        self.assertEqual(self.window.item_rules.bonus_actions, 0)
        self.assertFalse(self.window.characters["Jerry"]["alive"])
        self.assertEqual(self.window.phase, "conversation")

    def test_watch_after_c4_finishes_extra_attack_before_morning(self):
        self.watch_night("c4", "Hank")
        self.window.c4_target_room = "Kitchen"
        self.window.apply_weapon_gating()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.assertTrue(self.window.bonus_action_pending)
        self.assertFalse(self.window._night_memories_written)
        self.window.choose_room("Library")
        self.settle_offer()
        self.assertEqual([attack["weapon_id"] for attack in self.window.night_attacks], ["c4", "c4", "bare"])
        self.assertEqual(self.window.run_player_kills, 3)
        self.assertIn("revolver", self.window.profile.unlocked)
        self.assertIn("c4", self.window.profile.unlocked)

    def test_item_event_replay_does_not_duplicate_private_memory_or_usage(self):
        self.conversation()
        entry = self.window.inventory.grant("divine_revelation")
        events = self.window.item_rules.use_item(entry.instance_id, self.window.item_use_context(),
            self.window.body_id("Dave"), message="A recorded voice.", activation_id="activation")
        self.window.apply_item_events(events)
        self.window.apply_item_events(events)
        self.assertEqual(self.window.items_used, 1)
        self.assertEqual(len(self.window.characters["Dave"]["experiences"]), 1)

    def test_unlocks_follow_actual_weighted_vote_intervention_and_run_victory(self):
        self.conversation()
        self.use_card("banana", "Dave")
        self.use_card("muffle", "Hank")
        self.use_card("spare_paper")
        self.use_card("guardian_angel")
        self.window.overlay_host.close_page()
        ballots = {name: "Alex" for name in self.window.characters}
        ballots["Alex"] = "NONE"
        self.vote(ballots)
        self.assertTrue(self.window.player_alive)
        self.assertTrue(self.window.guardian_intervened_this_run)
        self.assertIn("presidential_decree", self.window.profile.unlocked)
        self.assertIn("lucky_coin", self.window.profile_store.item_pool())
        self.assertNotIn("spin_the_dagger", self.window.profile.unlocked)
        self.window.run_action_phase()
        self.assertTrue(self.window.prepare_room_entry("Attic"))
        for name in ("Dave", "Jerry", "Hank", "Owen"):
            room = next(room for room, occupants in self.window.room_assignments.items() if name in occupants)
            self.assertTrue(self.window.commit_attack(name, room))
        self.window.finish_night()
        self.assertTrue(self.window.game_over)
        self.assertNotIn("guardian_angel", self.window.profile.unlocked)
        self.assertIn("spin_the_dagger", self.window.profile.unlocked)
        self.assertEqual(self.window.profile.counters["three_item_types_victory"], 1)
        self.window.end_game_screen(5)
        self.assertNotIn("coin_survived_run_alive", self.window.profile.counters)

    def test_survived_coin_unlocks_guardian_only_after_actual_run_victory_once(self):
        self.conversation()
        coin = self.window.inventory.grant("lucky_coin")
        target = next(body for body in self.window.body_states() if body.label == "Dave")
        with patch.object(self.window.item_rules._rng, "choice", return_value=target):
            self.assertTrue(self.window.use_inventory_item(coin.instance_id, target.body_id, activation_id="won-coin"))
            self.assertTrue(self.window.use_inventory_item(coin.instance_id, target.body_id, activation_id="won-coin"))
        self.assertTrue(self.window.coin_survived_this_run)
        self.assertNotIn("guardian_angel", self.window.profile.unlocked)
        self.assertIn(coin, self.window.inventory.entries)
        for name, data in self.window.characters.items():
            data["alive"] = False
        self.window.end_game_screen(5)
        self.assertIn("guardian_angel", self.window.profile.unlocked)
        self.assertEqual(self.window.profile.counters["coin_survived_run_alive"], 1)
        self.window.end_game_screen(5)
        self.assertEqual(self.window.profile.counters["coin_survived_run_alive"], 1)

    def test_survived_coin_does_not_unlock_guardian_if_player_later_dies(self):
        self.conversation()
        coin = self.window.inventory.grant("lucky_coin")
        target = next(body for body in self.window.body_states() if body.label == "Dave")
        player = next(body for body in self.window.body_states() if body.label == "Alex")
        with patch.object(self.window.item_rules._rng, "choice", return_value=target):
            self.assertTrue(self.window.use_inventory_item(coin.instance_id, target.body_id))
        self.assertTrue(self.window.coin_survived_this_run)
        with patch.object(self.window.item_rules._rng, "choice", return_value=player):
            self.assertTrue(self.window.use_inventory_item(coin.instance_id, self.window.body_id("Hank")))
        self.assertTrue(self.window.game_over)
        self.assertFalse(self.window.player_alive)
        self.assertNotIn("guardian_angel", self.window.profile.unlocked)
        self.assertNotIn("coin_survived_run_alive", self.window.profile.counters)

    def test_watch_controlled_attack_keeps_controller_and_bonus_separate(self):
        self.watch_night("blackmagic", "Hank")
        self.window.black_magic_controller, self.window.black_magic_target = "Dave", "Hank"
        self.window.apply_weapon_gating()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.assertTrue(self.window.bonus_action_pending)
        self.window.choose_room("Library")
        self.settle_offer()
        self.assertEqual([attack["weapon_id"] for attack in self.window.night_attacks], ["blackmagic", "bare"])
        self.assertEqual([attack["attacker_label"] for attack in self.window.night_attacks], ["Dave", "Alex"])
        self.assertEqual(self.window.item_rules.bonus_actions, 0)

    def test_watch_basic_bonus_does_not_transfer_pen_signature_to_bonus_victim(self):
        self.watch_night("pen")
        self.window.pending_pen_note_text = "A single pen note."
        self.window.apply_weapon_gating()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.window.choose_room("Kitchen")
        self.settle_offer()
        with patch.object(game.random, "random", return_value=1.0):
            self.window.kill_victim("Hank")
        notes = [event.text for event in self.window.public_events if "A single pen note." in event.text]
        self.assertEqual(notes, ["The Detective reveals that a note was found by the body, it reads: A single pen note. - Signed: Dave"])

    def resolved_item_events(self, item_id="revolver", target="Dave", activation_id="death-item"):
        entry = self.window.inventory.grant(item_id)
        return self.window.item_rules.use_item(
            entry.instance_id, self.window.item_use_context(),
            self.window.body_id(target) if target else None, activation_id=activation_id)

    def test_death_item_batch_reconciles_once_without_night_attack_side_effects(self):
        self.conversation()
        self.window.new_crime_scenes = []
        events = self.resolved_item_events()
        with patch.object(self.window.item_rules, "attempt_death", side_effect=AssertionError("Already resolved")), patch.object(
                self.window, "commit_attack", side_effect=AssertionError("Conversation death")), patch.object(
                self.window, "finish_night", side_effect=AssertionError("Conversation death")):
            self.assertTrue(self.window.apply_item_events(events))
            self.assertFalse(self.window.apply_item_events(events))
            self.assertFalse(self.window.apply_resolved_death(events[0].death_decision))
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.run_player_kills, 1)
        self.assertEqual(self.window.items_used, 1)
        self.assertEqual(self.window.night_attacks, [])
        self.assertEqual(self.window.victims_last_night, [])
        self.assertEqual(self.window.new_crime_scenes, [])
        self.assertEqual(self.window.characters["Jerry"]["experiences"], [])

    def test_cached_death_decision_can_be_first_world_application(self):
        self.conversation()
        target = next(body for body in self.window.body_states() if body.label == "Dave")
        original = self.window.item_rules.attempt_death("cached-first", target, 1)
        cached = self.window.item_rules.attempt_death("cached-first", target, 1)
        self.assertFalse(cached.is_new)
        self.assertFalse(cached.killed)
        self.assertTrue(self.window.apply_resolved_death(cached))
        self.assertFalse(self.window.apply_resolved_death(original))
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.run_player_kills, 0)

    def test_cached_prevention_recovers_lost_first_application_events_once(self):
        self.conversation()
        self.give("guardian_angel")
        target = next(body for body in self.window.body_states() if body.label == "Alex")
        original = self.window.item_rules.attempt_death("cached-shield", target, 1, self.window.player_owner_id)
        cached = self.window.item_rules.attempt_death("cached-shield", target, 1, self.window.player_owner_id)
        self.assertFalse(cached.is_new)
        self.assertEqual(cached.events, original.events)
        self.assertFalse(self.window.guardian_intervened_this_run)
        self.assertFalse(self.window.item_rules.protection_armed)
        self.assertFalse(any("Against all expectation" in event.text for event in self.window.public_events))
        self.give("guardian_angel")
        self.assertFalse(self.window.apply_resolved_death(cached))
        self.assertTrue(self.window.guardian_intervened_this_run)
        self.assertTrue(self.window.item_rules.protection_armed)
        self.assertEqual(sum("Against all expectation" in event.text for event in self.window.public_events), 1)
        self.assertTrue(set(event.event_id for event in original.events).issubset(self.window.applied_item_events))
        self.assertFalse(self.window.apply_resolved_death(cached))
        self.assertFalse(self.window.apply_resolved_death(original))
        self.assertTrue(self.window.player_alive)
        self.assertTrue(self.window.item_rules.protection_armed)
        self.assertEqual(sum("Against all expectation" in event.text for event in self.window.public_events), 1)

    def test_cached_kill_recovers_note_and_bonus_progress_after_first_result_was_lost(self):
        self.conversation()
        note = self.window.inventory.grant("forged_note")
        self.assertTrue(self.window.use_inventory_item(note.instance_id, message="A captured note."))
        self.give("watch_this_drive", "Dave")
        self.window.round_number = 2
        self.window.phase = "action"
        target = next(body for body in self.window.body_states() if body.label == "Dave")
        original = self.window.item_rules.attempt_death(
            "cached-consequences", target, 2, caused_by_owner_id=self.window.player_owner_id, phase="action")
        self.assertEqual([event.event_type for event in original.events],
                         ["prepared_note_bound", "extra_action_earned"])
        self.assertTrue(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.morning_item_notes, [])
        self.assertNotIn("marked_kill_completed", self.window.profile.counters)
        self.assertEqual(self.window.item_rules.bonus_actions, 1)
        cached = self.window.item_rules.attempt_death(
            "cached-consequences", target, 2, caused_by_owner_id=self.window.player_owner_id, phase="action")
        self.assertFalse(cached.is_new)
        self.assertFalse(cached.killed)
        self.assertEqual(cached.events, original.events)
        self.assertTrue(self.window.apply_resolved_death(cached))
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.morning_item_notes,
                         [(original.events[0].event_id, "Dave", "A captured note.")])
        self.assertEqual(self.window.profile.counters["marked_kill_completed"], 1)
        self.assertEqual(self.window.item_rules.bonus_actions, 1)
        self.assertIsNone(self.window.item_rules.prepared_note)
        self.assertIsNone(self.window.item_rules.marked_target)
        self.assertFalse(self.window.apply_resolved_death(cached))
        self.assertFalse(self.window.apply_resolved_death(original))
        self.assertEqual(len(self.window.morning_item_notes), 1)
        self.assertEqual(self.window.profile.counters["marked_kill_completed"], 1)
        self.assertEqual(self.window.item_rules.bonus_actions, 1)

    def test_shared_adapter_preserves_already_dead_and_delayed_causes(self):
        self.conversation()
        for cause, label in ((game.DeathCause.TRAP, "Dave"), (game.DeathCause.CURSE, "Jerry")):
            target = next(body for body in self.window.body_states() if body.label == label)
            decision = self.window.item_rules.attempt_death(cause.value, target, 1, cause=cause)
            self.assertTrue(self.window.apply_resolved_death(decision))
            self.assertFalse(self.window.apply_resolved_death(decision))
            already_dead = self.window.item_rules.attempt_death(cause.value + "-again", target, 1, cause=cause)
            self.assertEqual(already_dead.outcome, "already_dead")
            self.assertFalse(self.window.apply_resolved_death(already_dead))
            self.assertFalse(self.window.characters[label]["alive"])
        self.assertEqual(self.window.run_player_kills, 0)
        self.assertEqual(self.window.innocents_executed, 0)

    def test_prevented_gamble_applies_guardian_once_then_next_attempt_kills(self):
        self.conversation()
        self.give("guardian_angel")
        actor = next(body for body in self.window.body_states() if body.label == "Alex")
        with patch.object(self.window.item_rules._rng, "choice", return_value=actor):
            events = self.resolved_item_events("lucky_coin")
        self.assertTrue(events[0].death_decision.prevented)
        self.assertFalse(self.window.apply_item_events(events))
        self.assertFalse(self.window.apply_item_events(events))
        self.assertTrue(self.window.player_alive)
        self.assertTrue(self.window.guardian_intervened_this_run)
        self.assertFalse(self.window.item_rules.protection_armed)
        self.assertEqual(sum("Against all expectation" in event.text for event in self.window.public_events), 1)
        self.assertTrue(self.window.resolve_death("Alex", "next-attempt").killed)
        self.assertFalse(self.window.player_alive)
        self.assertEqual(self.window.run_player_kills, 0)

    def test_conversation_death_binds_note_once_without_watch_bonus(self):
        self.conversation()
        for item_id, target, message in (("forged_note", None, "A prepared note."),
                                         ("watch_this_drive", "Dave", None)):
            entry = self.window.inventory.grant(item_id)
            events = self.window.item_rules.use_item(
                entry.instance_id, self.window.item_use_context(),
                self.window.body_id(target) if target else None, message=message)
            self.window.apply_item_events(events)
        self.window.round_number = 2
        events = self.resolved_item_events()
        self.assertTrue(self.window.apply_item_events(events))
        self.assertFalse(self.window.apply_item_events(events))
        self.assertEqual(len(self.window.morning_item_notes), 1)
        self.assertEqual(self.window.morning_item_notes[0][1:], ("Dave", "A prepared note."))
        self.assertEqual(self.window.item_rules.bonus_actions, 0)
        self.assertNotIn("marked_kill_completed", self.window.profile.counters)

    def test_death_batch_save_retry_preserves_kill_and_usage_receipts(self):
        self.conversation()
        events = self.resolved_item_events()
        with patch.object(self.window.profile_store, "record", side_effect=ProfileError("Disk unavailable")):
            self.window.apply_item_events(events)
            self.window.apply_item_events(events)
        self.assertEqual(self.window.run_player_kills, 1)
        self.assertEqual([event.event_type for event in self.window.pending_progress], ["player_kill"])
        self.window.retry_progress()
        self.window.apply_item_events(events)
        self.window.retry_progress()
        self.assertEqual(self.window.profile.counters["player_kill"], 1)
        self.assertEqual(self.window.items_used, 1)

    def test_unknown_death_target_does_not_consume_application_receipt(self):
        self.conversation()
        events = self.resolved_item_events()
        decision = events[0].death_decision
        invalid = replace(events[0], death_decision=replace(decision, target_body_id="missing"))
        with self.assertRaises(game.ItemRuleError):
            self.window.apply_item_events((invalid,))
        self.assertNotIn(invalid.event_id, self.window.applied_item_events)
        self.assertNotIn(decision.attempt_id, self.window.applied_death_attempts)
        self.assertTrue(self.window.apply_item_events(events))

    def test_player_item_death_ends_game_without_execution_story_or_restored_input(self):
        self.conversation()
        entry = self.window.inventory.grant("lucky_coin")
        actor = next(body for body in self.window.body_states() if body.label == "Alex")
        catalogue = dict(game.ITEM_CATALOGUE)
        catalogue["lucky_coin"] = replace(catalogue["lucky_coin"], default=True)
        self.window.interject_input.show()
        with patch.object(game, "ITEM_CATALOGUE", catalogue), patch.object(
                self.window.item_rules._rng, "choice", return_value=actor), patch.object(
                self.window, "restore_conversation_decision") as restore:
            self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        restore.assert_not_called()
        self.assertFalse(self.window.player_alive)
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.phase, "game_over")
        self.assertTrue(self.window.interject_input.isHidden())
        self.assertFalse(self.window.inventory_button.isEnabled())
        self.assertIn("You died.", self.window.output_area.toPlainText())
        self.assertNotIn("hanged for your crimes", self.window.output_area.toPlainText())
        self.assertEqual(self.window.items_used, 1)
        self.assertEqual(self.window.run_player_kills, 0)

    def test_terminal_item_usage_is_counted_before_victory_achievement(self):
        self.conversation()
        self.give("banana", "Dave")
        self.give("muffle", "Jerry")
        for name in ("Hank", "Owen", "Debra"):
            self.window.characters[name]["alive"] = False
        entry = self.window.inventory.grant("revolver")
        catalogue = dict(game.ITEM_CATALOGUE)
        catalogue["revolver"] = replace(catalogue["revolver"], default=True)
        with patch.object(game, "ITEM_CATALOGUE", catalogue), patch.object(
                game, "OPTIONAL_ITEM_NARRATION_PENDING", frozenset()):
            self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.items_used, 3)
        self.assertEqual(self.window.used_item_types, {"banana", "muffle", "revolver"})
        self.assertEqual(self.window.profile.counters["three_item_types_victory"], 1)
        self.assertEqual(self.window.profile.counters["player_kill"], 1)

    def test_gamble_items_reconcile_the_engine_selected_npc(self):
        self.conversation()
        for item_id, label in (("lucky_coin", "Dave"), ("spin_the_dagger", "Hank")):
            with self.subTest(item_id=item_id):
                loser = next(body for body in self.window.body_states() if body.label == label)
                with patch.object(self.window.item_rules._rng, "choice", return_value=loser):
                    events = self.resolved_item_events(
                        item_id, target="Dave" if item_id == "lucky_coin" else None, activation_id=item_id)
                self.assertTrue(self.window.apply_item_events(events))
                self.assertFalse(self.window.apply_item_events(events))
                self.assertFalse(self.window.characters[label]["alive"])
                self.assertTrue(self.window.player_alive)
        self.assertEqual(self.window.run_player_kills, 2)
        self.assertEqual(self.window.items_used, 2)

    def test_revolver_is_enabled_and_publishes_a_shot_before_guardian_survival(self):
        self.conversation()
        self.give("guardian_angel")
        entry = self.window.inventory.grant("revolver")
        target = self.window.body_id(self.window.player_name)
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target, activation_id="protected-shot"))
        self.assertTrue(self.window.player_alive)
        texts = [event.text for event in self.window.public_events]
        shot = "Alex fires a revolver at Alex."
        survival = "Against all expectation, Alex survives."
        self.assertLess(texts.index(shot), texts.index(survival))
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target, activation_id="protected-shot"))
        texts = [event.text for event in self.window.public_events]
        self.assertEqual(texts.count(shot), 1)
        self.assertEqual(texts.count(survival), 1)
        self.assertEqual(self.window.items_used, 2)

    def arm_optional(self, item_id, target=None, room=None):
        entry = self.window.inventory.grant(item_id)
        events = self.window.item_rules.use_item(
            entry.instance_id, self.window.item_use_context(),
            self.window.body_id(target) if target else None, room_id=room)
        self.window.apply_item_events(events)
        return events

    def test_cached_consumable_activation_recovers_original_item_then_rejects_mismatches(self):
        self.conversation()
        for item_id in ("lucky_match", "deep_pockets"):
            with self.subTest(item=item_id):
                self.window.item_drafts = DraftState(self.window.player_owner_id, eligible_item_pool())
                entry = self.window.inventory.grant(item_id)
                self.window.item_rules.use_item(entry.instance_id, self.window.item_use_context(),
                    drafts=self.window.item_drafts, activation_id=item_id)
                before = (self.window.item_drafts.rerolls_remaining, self.window.item_drafts.extra_pick_armed)
                self.assertNotIn(entry, self.window.inventory.entries)
                self.assertTrue(self.window.use_inventory_item(entry.instance_id, activation_id=item_id))
                self.assertTrue(self.window.use_inventory_item(entry.instance_id, activation_id=item_id))
                self.assertEqual((self.window.item_drafts.rerolls_remaining,
                                  self.window.item_drafts.extra_pick_armed), before)
                self.assertFalse(self.window.use_inventory_item(entry.instance_id, activation_id="unknown"))
                self.assertFalse(self.window.use_inventory_item(entry.instance_id,
                    self.window.body_id("Dave"), activation_id=item_id))
        self.assertEqual(self.window.items_used, 2)

    def test_cached_gamble_activation_recovers_guardian_and_kill_without_rerolling(self):
        self.conversation()
        self.give("guardian_angel")
        coin = self.window.inventory.grant("lucky_coin")
        player = next(body for body in self.window.body_states() if body.label == "Alex")
        target = next(body for body in self.window.body_states() if body.label == "Dave")
        with patch.object(self.window.item_rules._rng, "choice", return_value=player) as roll:
            original = self.window.item_rules.use_item(coin.instance_id, self.window.item_use_context(),
                                                       target.body_id, activation_id="lost-coin")
            self.assertTrue(original[0].death_decision.prevented)
            self.assertFalse(self.window.guardian_intervened_this_run)
            self.assertTrue(self.window.use_inventory_item(coin.instance_id, target.body_id, activation_id="lost-coin"))
            self.assertTrue(self.window.use_inventory_item(coin.instance_id, target.body_id, activation_id="lost-coin"))
            self.assertEqual(roll.call_count, 1)
        self.assertTrue(self.window.guardian_intervened_this_run)
        self.assertTrue(self.window.player_alive)
        self.assertFalse(self.window.item_rules.protection_armed)
        self.assertEqual(sum("Against all expectation" in record.text for record in self.window.public_events), 1)
        dagger = self.window.inventory.grant("spin_the_dagger")
        with patch.object(self.window.item_rules._rng, "choice", return_value=target) as roll:
            self.window.item_rules.use_item(dagger.instance_id, self.window.item_use_context(), activation_id="lost-spin")
            self.assertTrue(self.window.characters["Dave"]["alive"])
            self.assertTrue(self.window.use_inventory_item(dagger.instance_id, activation_id="lost-spin"))
            self.assertTrue(self.window.use_inventory_item(dagger.instance_id, activation_id="lost-spin"))
            self.assertEqual(roll.call_count, 1)
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.run_player_kills, 1)
        self.assertEqual(self.window.items_used, 3)

    def test_cached_revolver_activation_recovers_one_shot_and_one_death(self):
        self.conversation()
        target = self.window.body_id("Dave")
        entry = self.window.inventory.grant("revolver")
        self.window.item_rules.use_item(entry.instance_id, self.window.item_use_context(), target,
                                       activation_id="lost-shot")
        self.assertTrue(self.window.characters["Dave"]["alive"])
        for _ in range(2):
            self.assertTrue(self.window.use_inventory_item(entry.instance_id, target, activation_id="lost-shot"))
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.items_used, 1)
        self.assertEqual(self.window.run_player_kills, 1)
        self.assertEqual(sum(event.text == "Alex fires a revolver at Dave." for event in self.window.public_events), 1)

    def test_lost_first_entry_result_recovers_guardian_note_bonus_and_death_once(self):
        self.conversation()
        self.give("guardian_angel")
        note = self.window.inventory.grant("forged_note")
        self.assertTrue(self.window.use_inventory_item(note.instance_id, message="Lost trap note."))
        self.give("watch_this_drive", "Dave")
        self.arm_optional("booby_trap", room="Attic")
        self.window.phase = "action"
        self.window.round_number = 2
        entrants = tuple(body for body in self.window.body_states() if body.label in {"Alex", "Dave"})
        requested = {body.body_id for body in entrants}
        owners = {self.window.body_id("Alex"): self.window.player_owner_id}
        original = self.window.item_rules.resolve_entry("lost-entry", "Attic", entrants, 2, owners=owners)
        self.assertEqual({decision.outcome for decision in original.decisions}, {"prevented", "killed"})
        self.assertFalse(self.window.item_rules.traps)
        self.assertTrue(self.window.characters["Dave"]["alive"])
        self.assertFalse(self.window.guardian_intervened_this_run)
        self.assertEqual(self.window.morning_item_notes, [])
        self.window.resolve_room_entry_items("lost-entry", "Attic", requested)
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertTrue(self.window.player_alive)
        self.assertTrue(self.window.guardian_intervened_this_run)
        self.assertEqual(len(self.window.morning_item_notes), 1)
        self.assertEqual(self.window.morning_item_notes[0][1:], ("Dave", "Lost trap note."))
        self.assertEqual(self.window.item_rules.bonus_actions, 1)
        self.assertEqual(self.window.profile.counters["marked_kill_completed"], 1)
        self.assertEqual(self.window.run_player_kills, 1)
        before = (tuple(self.window.public_events), tuple(self.window.morning_item_notes),
                  dict(self.window.profile.counters), frozenset(self.window.applied_item_events))
        self.window.resolve_room_entry_items("lost-entry", "Attic", requested)
        self.assertEqual((tuple(self.window.public_events), tuple(self.window.morning_item_notes),
                          dict(self.window.profile.counters), frozenset(self.window.applied_item_events)), before)
        self.assertEqual(self.window.item_rules.bonus_actions, 1)
        self.assertEqual(self.window.run_player_kills, 1)

    def test_fresh_dead_or_unknown_room_entry_is_rejected_without_spending_trap(self):
        self.conversation()
        self.arm_optional("booby_trap", room="Attic")
        self.window.phase = "action"
        self.window.round_number = 2
        self.window.characters["Dave"]["alive"] = False
        for requested in ({"unknown"}, {self.window.body_id("Dave")}):
            with self.subTest(requested=requested), self.assertRaises(game.ItemRuleError):
                self.window.resolve_room_entry_items("fresh-entry", "Attic", requested)
            self.assertEqual(len(self.window.item_rules.traps), 1)
        self.assertFalse(self.window.resolve_room_entry_items("empty-entry", "Attic", set()))
        self.assertEqual(len(self.window.item_rules.traps), 1)
        self.window.resolve_room_entry_items("fresh-entry", "Attic", {self.window.body_id("Jerry")})
        self.assertFalse(self.window.characters["Jerry"]["alive"])
        self.assertFalse(self.window.item_rules.traps)

    def test_lost_first_curse_boundary_recovers_nested_consequences_once(self):
        self.conversation()
        self.give("guardian_angel")
        self.arm_optional("occult_ritual", target="Dave")
        self.arm_optional("occult_ritual", target="Alex")
        note = self.window.inventory.grant("forged_note")
        self.assertTrue(self.window.use_inventory_item(note.instance_id, message="Lost curse note."))
        self.window.round_number = 2
        self.give("watch_this_drive", "Dave")
        self.window.round_number = 3
        self.window.phase = "action"
        phase_id = f"{self.window.run_id}:action:3"
        bodies = self.window.body_states()
        owners = {self.window.body_id("Alex"): self.window.player_owner_id}
        original = self.window.item_rules.resolve_due_curses(phase_id, 3, bodies, owners=owners)
        self.assertFalse(self.window.item_rules.curses)
        self.assertTrue(self.window.characters["Dave"]["alive"])
        self.assertFalse(self.window.guardian_intervened_this_run)
        cached = self.window.item_rules.resolve_due_curses(phase_id, 3, bodies, owners=owners)
        self.assertFalse(cached.is_new)
        self.assertTrue(all(not decision.is_new for decision in cached.decisions))
        self.assertTrue(all(not event.death_decision.is_new for event in cached.events if event.death_decision is not None))
        self.assertEqual([event.event_id for event in cached.events], [event.event_id for event in original.events])
        self.assertEqual(tuple(decision.events for decision in cached.decisions),
                         tuple(decision.events for decision in original.decisions))
        self.window.resolve_action_curses()
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertTrue(self.window.player_alive)
        self.assertTrue(self.window.guardian_intervened_this_run)
        self.assertEqual(self.window.morning_item_notes[0][1:], ("Dave", "Lost curse note."))
        self.assertEqual(self.window.profile.counters["marked_kill_completed"], 1)
        self.assertEqual(self.window.item_rules.bonus_actions, 1)
        before = (tuple(self.window.public_events), tuple(self.window.morning_item_notes),
                  dict(self.window.profile.counters), frozenset(self.window.applied_item_events))
        self.window.resolve_action_curses()
        self.assertEqual((tuple(self.window.public_events), tuple(self.window.morning_item_notes),
                          dict(self.window.profile.counters), frozenset(self.window.applied_item_events)), before)
        self.assertEqual(self.window.run_player_kills, 1)

    def test_cached_vote_payload_recovers_weighted_and_skipped_events_once(self):
        self.conversation()
        self.give("spare_paper")
        bodies = self.window.body_states()
        player = self.window.body_id("Alex")
        self.window.item_rules.open_vote("lost-weighted", bodies, player, day=1)
        ballots = {body.body_id: "NONE" for body in bodies}
        ballots[player] = self.window.body_id("Dave")
        original = self.window.item_rules.resolve_vote("lost-weighted", ballots)
        text = original.events[0].public_text
        self.assertFalse(any(record.text == text for record in self.window.public_events))
        self.give("spare_paper")
        cached = self.window.item_rules.resolve_vote("lost-weighted", ballots)
        self.assertFalse(cached.is_new)
        self.assertEqual(cached.events, original.events)
        self.window.apply_item_events(cached.events)
        self.window.apply_item_events(cached.events)
        self.assertEqual(sum(record.text == text for record in self.window.public_events), 1)
        self.assertEqual(self.window.item_rules.pending_ballot_bonus, 2)
        self.arm_optional("presidential_decree")
        self.window.item_rules.open_vote("lost-skipped", bodies, player, day=1)
        skipped = self.window.item_rules.resolve_vote("lost-skipped", {})
        self.arm_optional("presidential_decree")
        cached_skip = self.window.item_rules.resolve_vote("lost-skipped", {})
        self.assertFalse(cached_skip.is_new)
        self.assertEqual(cached_skip.events, skipped.events)
        self.window.apply_item_events(cached_skip.events)
        self.window.apply_item_events(cached_skip.events)
        self.assertTrue(self.window.item_rules.skip_next_vote)
        self.assertEqual(sum(record.text == "Presidential Decree: the next vote was skipped."
                             for record in self.window.history_records), 1)

    def test_assigned_group_trap_kills_once_without_attack_witnesses(self):
        self.conversation()
        self.arm_optional("booby_trap", room="Attic")
        self.window.phase = "action"
        self.window.round_number = 2
        entrants = {self.window.body_id(name) for name in ("Dave", "Jerry")}
        self.window.resolve_room_entry_items("assigned-group", "Attic", entrants)
        self.window.resolve_room_entry_items("assigned-group", "Attic", entrants)
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertFalse(self.window.characters["Jerry"]["alive"])
        self.assertEqual(self.window.run_player_kills, 2)
        self.assertEqual(len(self.window.item_deaths), 2)
        self.assertEqual(self.window.night_attacks, [])
        self.assertEqual(self.window.victims_last_night, [])
        self.assertEqual(self.window.characters["Hank"]["experiences"], [])
        self.window.publish_item_bodies()
        self.window.publish_item_bodies()
        self.assertEqual(sum("body was found in the Attic" in event.text
                             for event in self.window.public_events), 2)

    def test_trap_binds_prepared_note_and_marked_bonus_once(self):
        self.conversation()
        self.arm_optional("booby_trap", room="Attic")
        self.give("watch_this_drive", "Dave")
        entry = self.window.inventory.grant("forged_note")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, message="Prepared words."))
        self.window.phase = "action"
        self.window.round_number = 2
        entrants = {self.window.body_id("Dave")}
        self.window.resolve_room_entry_items("assigned", "Attic", entrants)
        self.window.resolve_room_entry_items("assigned", "Attic", entrants)
        self.assertEqual(self.window.item_rules.bonus_actions, 1)
        self.assertEqual(self.window.profile.counters["marked_kill_completed"], 1)
        self.assertEqual(len(self.window.morning_item_notes), 1)
        self.assertEqual(self.window.morning_item_notes[0][1:], ("Dave", "Prepared words."))
        self.assertEqual(self.window.run_player_kills, 1)

    def test_empty_room_player_entry_trap_uses_guardian_once(self):
        self.conversation()
        self.give("guardian_angel")
        self.arm_optional("booby_trap", room="Attic")
        self.window.phase = "action"
        self.window.round_number = 2
        self.window.resolve_room_entry_items("empty-assignment", "Attic", set())
        self.assertEqual(len(self.window.item_rules.traps), 1)
        self.window.resolve_room_entry_items("player-entry", "Attic", {self.window.body_id("Alex")})
        self.assertTrue(self.window.player_alive)
        self.assertEqual(len(self.window.item_rules.traps), 0)
        self.assertTrue(self.window.guardian_intervened_this_run)
        self.window.resolve_room_entry_items("player-entry", "Attic", {self.window.body_id("Alex")})
        self.assertEqual(sum("Against all expectation" in event.text
                             for event in self.window.public_events), 1)
        self.assertEqual(self.window.run_player_kills, 0)

    def test_empty_room_player_trap_death_ends_game(self):
        self.conversation()
        self.arm_optional("booby_trap", room="Attic")
        self.window.phase = "action"
        self.window.round_number = 2
        self.assertTrue(self.window.resolve_room_entry_items(
            "player-entry", "Attic", {self.window.body_id("Alex")}))
        self.assertTrue(self.window.game_over)
        self.assertFalse(self.window.player_alive)
        self.assertEqual(self.window.run_player_kills, 0)
        self.assertNotIn("hanged for your crimes", self.window.output_area.toPlainText())

    def test_curse_due_boundary_preserves_unknown_location_and_actual_kill(self):
        self.conversation()
        self.arm_optional("occult_ritual", target="Dave")
        self.window.phase = "action"
        self.window.round_number = 2
        self.window.resolve_action_curses()
        self.assertTrue(self.window.characters["Dave"]["alive"])
        self.window.round_number = 3
        self.window.resolve_action_curses()
        self.window.resolve_action_curses()
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.run_player_kills, 1)
        record, = self.window.item_deaths.values()
        self.assertIsNone(record.room_id)
        self.assertEqual(record.cause, game.DeathCause.CURSE)
        self.window.new_crime_scenes = []
        self.window.run_conversation_phase()
        self.assertFalse(any("no murder occurred" in event.text for event in self.window.public_events))
        self.assertFalse(any("body was found" in event.text for event in self.window.public_events))
        self.assertEqual(self.window.night_attacks, [])

    def test_action_phase_resolves_due_curse_before_assigning_living_bodies(self):
        self.conversation()
        self.arm_optional("occult_ritual", target="Dave")
        self.window.round_number = 2
        self.window.selected_weapon = self.weapon()
        self.window.run_action_phase()
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertFalse(any("Dave" in names for names in self.window.room_assignments.values()))
        self.assertEqual(self.window.run_player_kills, 1)

    def test_all_registered_items_have_approved_runtime_narration(self):
        self.assertFalse(game.OPTIONAL_ITEM_NARRATION_PENDING)

    def test_enabled_gambles_update_canonical_context_without_new_npc_prose(self):
        self.conversation()
        for item_id, name in (("lucky_coin", "Dave"), ("spin_the_dagger", "Jerry")):
            loser = next(body for body in self.window.body_states() if body.label == name)
            entry = self.window.inventory.grant(item_id)
            before = list(self.window.public_events)
            with patch.object(self.window.item_rules._rng, "choice", return_value=loser):
                self.assertTrue(self.window.use_inventory_item(
                    entry.instance_id, self.window.body_id(name) if item_id == "lucky_coin" else None))
            self.assertIn(name, self.window.public_state().dead)
            self.assertEqual(self.window.public_events, before)
            self.assertTrue(any(name in record.text and "killed" in record.text
                                for record in self.window.history_records))
        self.assertEqual(self.window.run_player_kills, 2)

    def test_enabled_decree_skips_without_vote_progress_or_false_public_abstention(self):
        self.conversation()
        self.give("spare_paper")
        self.give("presidential_decree")
        before = list(self.window.public_events)
        self.vote({name: "NONE" for name in (*self.window.characters, "Alex")})
        self.assertEqual(self.window.item_rules.pending_ballot_bonus, 2)
        self.assertEqual(self.window.public_events, before)
        self.assertNotIn("survived_vote", self.window.profile.counters)
        self.assertTrue(any("vote was skipped" in record.text for record in self.window.history_records))

    def test_final_attack_publishes_camera_before_awarding_bare_no_items_victory(self):
        self.night()
        for name in ("Hank", "Owen", "Debra"):
            self.window.characters[name]["alive"] = False
        self.window.npc_items = game.NPCItemState({"Dave": "camera"})
        self.assertNotIn(1, self.window.npc_items._photos_by_night)
        self.window.choose_room("Attic")
        photo, = self.window.npc_items._photos_by_night[1]
        self.assertEqual(set(photo.occupants), {"Alex", "Dave", "Jerry"})
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.profile.counters["bare_hands_no_item_victory"], 1)
        self.assertTrue(any(record.source == "Photograph" for record in self.window.public_events))


if __name__ == "__main__":
    unittest.main()
