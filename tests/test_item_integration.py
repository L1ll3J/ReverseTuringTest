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
from progression import ProfileError, ProfileStore, ProgressEvent


class ItemIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["item-integration"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32" and cls.app.platformName() == "offscreen":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
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
        self.stack.enter_context(patch.object(self.window, "run_conversation_loop"))

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
        self.window.select_weapon_and_start(self.weapon())
        self.window.selected_weapon = self.weapon(weapon_id)
        # Keep effect-specific room offers deterministic; production pool is checked separately.
        self.window.item_drafts = DraftState(self.window.player_owner_id, ("banana", "muffle", "spare_paper"))
        self.window.room_assignments = assignments or {
            "Attic": ["Dave", "Jerry"], "Kitchen": ["Hank", "Owen"],
            "Library": ["Debra"], "Basement": [], "Cellar": [],
        }

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
        goals = [label for label in self.window.weapon_picker_widget.findChildren(QLabel)
                 if "\n\nLocked:" in label.text()]
        self.assertTrue(goals)
        self.assertTrue(all(QFontInfo(label.font()).family() != self.window.pixel_font_family for label in goals))
        for weapon in self.window.weapons:
            if weapon["id"] != "bare":
                self.assertTrue(any(label.text().startswith(weapon["desc"]) for label in goals))
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
                            for button in page.findChildren(QPushButton)))
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
                          self.window.open_knife_dialog, self.window.open_c4_dialog):
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
        editors = (("knife", self.window.open_knife_dialog),
                   ("pen", self.window.open_pen_editor),
                   ("blackmagic", self.window.open_blackmagic_dialog),
                   ("c4", self.window.open_c4_dialog))

        def configuration():
            return (self.window.pending_pen_note_text, self.window.black_magic_controller,
                    self.window.black_magic_target, self.window.knife_extra_target,
                    self.window.knife_used_bloodlust, self.window.c4_target_room)

        for weapon_id, open_editor in editors:
            with self.subTest(weapon=weapon_id):
                self.window.show_weapon_select_screen()
                for character in self.window.characters.values():
                    character["alive"] = True
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
                    boxes[-1].setCurrentText("Hank" if weapon_id == "knife"
                                             else "Jerry" if weapon_id == "blackmagic" else "Cellar")
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
        editors = (("knife", self.window.open_knife_dialog),
                   ("pen", self.window.open_pen_editor),
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
                elif weapon_id == "knife":
                    self.assertEqual(self.window.knife_extra_target, "Hank")
                    self.assertTrue(self.window.knife_used_bloodlust)
                else:
                    self.assertEqual(self.window.c4_target_room, "Kitchen")

    def test_room_acceptance_retires_editor_when_room_has_no_new_reward(self):
        self.night("knife")
        previous = self.window.item_drafts.enter_room("Basement", self.window.inventory.snapshot())
        self.window.item_drafts.skip(previous.offer_id)
        self.window.open_knife_dialog()
        editor = self.window._weapon_windows["knife"]
        editor.findChild(QComboBox).setCurrentText("Hank")
        save = next(button for button in editor.findChildren(QPushButton) if button.text() == "Save")
        QTest.mouseClick(self.window.room_buttons["Basement"], Qt.LeftButton)
        self.assertIsNone(self.window.draft_page)
        self.assertIsNone(self.window.pending_room_action)
        self.assertEqual(self.window.rooms_visited, ["Basement"])
        self.assertTrue(editor.isHidden())
        save.click()
        self.assertIsNone(self.window.knife_extra_target)
        self.assertFalse(self.window.knife_used_bloodlust)

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
        self.assertGreater(page.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
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

    def test_restart_retires_old_offer_before_new_window_and_rejects_late_actions(self):
        self.night()
        continuation = Mock()
        self.window.offer_room_item("Basement", continuation)
        page = self.window.draft_page
        offer = self.window.item_drafts.pending_offer
        had_main_window = hasattr(self.app, "_main_window")
        previous_main_window = getattr(self.app, "_main_window", None)
        previous_quit = self.app.quitOnLastWindowClosed()
        original_init = game.GameWindow.__init__
        created = []

        def record_init(window):
            original_init(window)
            created.append(window)

        try:
            with patch.object(game.GameWindow, "__init__", new=record_init):
                self.window.restart_game()
                self.assertIsNone(self.window.draft_page)
                self.assertIsNone(self.window.pending_room_action)
                self.assertIsNone(self.window.item_drafts.pending_offer)
                self.window.choose_item_offer(offer.offer_id, 0)
                self.window.skip_item_offer(offer.offer_id)
                self.window.reroll_item_offer(offer.offer_id, 0)
                deadline = monotonic() + 3
                while not created and monotonic() < deadline:
                    QTest.qWait(5)
            self.assertEqual(len(created), 1)
            self.assertIs(self.app._main_window, created[0])
            self.assertEqual(created[0].phase, "start")
            self.assertIsNone(created[0].draft_page)
            self.assertEqual(created[0].inventory.entries, ())
            self.assertEqual(self.window.inventory.entries, ())
            continuation.assert_not_called()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertTrue(sip.isdeleted(page))
            QTest.qWait(60)
        finally:
            self.app.setQuitOnLastWindowClosed(False)
            for replacement in created:
                replacement.close()
                replacement.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            if had_main_window:
                self.app._main_window = previous_main_window
            elif hasattr(self.app, "_main_window"):
                del self.app._main_window
            self.app.setQuitOnLastWindowClosed(previous_quit)

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
        self.assertEqual(self.window.characters["Dave"]["experiences"][-1].text, MUFFLE_EXPIRED)

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
        self.window.knife_extra_target = "Hank"
        self.window.knife_used_bloodlust = True
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertTrue(self.window.characters["Hank"]["alive"])
        self.assertEqual(self.window.item_drafts.pending_offer.room_id, "Kitchen")
        self.settle_offer("muffle")
        self.assertFalse(self.window.characters["Hank"]["alive"])
        self.assertEqual([(attack["target_label"], attack["room"]) for attack in self.window.night_attacks],
                         [("Dave", "Attic"), ("Hank", "Kitchen")])
        self.assertEqual(len(self.window.characters["Jerry"]["experiences"]), 1)
        self.assertIn("Alex kill Dave in the Attic", self.window.characters["Jerry"]["experiences"][0].text)
        self.assertEqual(len(self.window.characters["Owen"]["experiences"]), 1)
        self.assertIn("Alex kill Hank in the Kitchen", self.window.characters["Owen"]["experiences"][0].text)
        self.assertEqual(self.window.profile.counters["player_kill"], 2)
        self.assertEqual(self.window.item_drafts.seen_rooms, {"Attic", "Kitchen"})
        self.assertEqual(len(self.window.inventory.entries), 1)

    def test_knife_does_not_kill_primary_twice(self):
        self.night("knife")
        self.window.knife_extra_target = "Debra"
        self.window.choose_room("Library")
        self.settle_offer()
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

    def test_voodoo_preserves_body_affinity_history_guardian_and_truthful_witness(self):
        self.conversation()
        self.give("banana", "Jerry")
        self.give("guardian_angel")
        original_player_id = self.window.body_id("Alex")
        target_id = self.window.body_id("Dave")
        self.window.append_speech("Alex", "My old statement")
        self.window.phase = "weapon_select"
        self.night("voodoo")
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertEqual(self.window.player_name, "Dave")
        self.assertEqual(self.window.body_id(self.window.player_name), target_id)
        self.assertFalse(self.window.characters["Alex"]["alive"])
        self.assertTrue(self.window.item_rules.protection_armed)
        self.assertEqual(self.window.item_rules.affinities[0].subject_body_id, original_player_id)
        self.assertEqual(self.window.public_dialogue[0].speaker, "Alex")
        record = self.window.characters["Jerry"]["experiences"][-1].text
        self.assertEqual(record, "You (Jerry) were in the Attic. You saw Alex stab Dave. Afterwards, Alex's body was lying dead there, and Dave was alive.")
        self.assertTrue(self.window.resolve_death("Dave", "test-new-body").prevented)
        self.assertTrue(self.window.player_alive)
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
        return entry

    def watch_night(self, weapon_id="bare", marked="Dave"):
        self.night(weapon_id)
        self.conversation()
        self.use_card("watch_this_drive", marked)
        self.window.overlay_host.close_page()
        self.window.run_action_phase()
        self.window.room_assignments = {
            "Attic": ["Dave", "Jerry"], "Kitchen": ["Hank", "Owen"],
            "Library": ["Debra"], "Basement": [], "Cellar": [],
        }

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
        self.window.room_assignments["Attic"] = ["Dave", "Jerry"]
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

    def test_decree_remains_blocked_in_production_inventory_and_callback(self):
        self.conversation()
        entry = self.window.inventory.grant("presidential_decree")
        self.window.open_inventory()
        with patch.object(self.window.item_rules, "use_item", side_effect=AssertionError("Unavailable item")):
            self.assertFalse(self.window.inventory_page.use_buttons[entry.instance_id].isEnabled())
            self.assertFalse(self.window.use_inventory_item(entry.instance_id))
        self.assertIn(entry, self.window.inventory.entries)
        self.assertFalse(self.window.item_rules.skip_next_vote)

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
        self.window.room_assignments = {"Attic": ["Dave", "Jerry"], "Kitchen": ["Hank", "Owen"],
                                        "Library": ["Debra"], "Basement": [], "Cellar": []}
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
        self.assertEqual(self.window.npc_equipment_label.text(), "Known equipment\nDave: Camera")
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
        self.assertTrue(any(record.text == "Known equipment: Dave is carrying a Camera."
                            for record in self.window.history_records))

    def test_known_equipment_refresh_after_conversation_death_stays_hidden(self):
        self.window.npc_items = game.NPCItemState({"Hank": "camera"})
        self.night()
        self.window.run_conversation_phase()
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.assertTrue(self.window.apply_item_events(self.resolved_item_events()))
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertEqual(self.window.npc_equipment_label.text(), "Known equipment\nHank: Camera")
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
        self.assertFalse(self.window.npc_equipment_label.isHidden())
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
        self.assertNotIn("lucky_coin", self.window.profile.unlocked)
        self.assertNotIn("spin_the_dagger", self.window.profile.unlocked)
        self.window.run_action_phase()
        for name in ("Dave", "Jerry", "Hank", "Owen"):
            room = next(room for room, occupants in self.window.room_assignments.items() if name in occupants)
            self.assertTrue(self.window.commit_attack(name, room))
        self.window.finish_night()
        self.assertTrue(self.window.game_over)
        self.assertIn("lucky_coin", self.window.profile.unlocked)
        self.assertIn("spin_the_dagger", self.window.profile.unlocked)
        self.assertEqual(self.window.profile.counters["three_item_types_victory"], 1)
        self.window.end_game_screen(5)
        self.assertEqual(self.window.profile.counters["guardian_intervention_run_survived"], 1)

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

    def test_cached_prevention_then_original_applies_nested_events_once(self):
        self.conversation()
        self.give("guardian_angel")
        target = next(body for body in self.window.body_states() if body.label == "Alex")
        original = self.window.item_rules.attempt_death("cached-shield", target, 1, self.window.player_owner_id)
        cached = self.window.item_rules.attempt_death("cached-shield", target, 1, self.window.player_owner_id)
        self.assertFalse(self.window.apply_resolved_death(cached))
        self.assertFalse(self.window.apply_resolved_death(original))
        self.assertFalse(self.window.apply_resolved_death(original))
        self.assertTrue(self.window.player_alive)
        self.assertTrue(self.window.guardian_intervened_this_run)
        self.assertEqual(sum("Against all expectation" in event.text for event in self.window.public_events), 1)

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
        with patch.object(game, "ITEM_CATALOGUE", catalogue):
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

    def test_all_death_items_stay_unavailable_before_rule_execution(self):
        self.conversation()
        entries = [self.window.inventory.grant(item_id)
                   for item_id in ("revolver", "lucky_coin", "spin_the_dagger")]
        self.window.open_inventory()
        with patch.object(self.window.item_rules, "use_item", side_effect=AssertionError("Unavailable item")):
            for entry in entries:
                with self.subTest(item_id=entry.item_id):
                    self.assertFalse(self.window.inventory_page.use_buttons[entry.instance_id].isEnabled())
                    self.assertFalse(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
                    self.assertIn(entry, self.window.inventory.entries)
        self.assertTrue(all(body.alive for body in self.window.body_states()))
        self.assertEqual(self.window.items_used, 0)

    def test_optional_unfinished_use_is_explicitly_blocked_without_consuming(self):
        self.conversation()
        entry = self.use_card("revolver", "Dave")
        self.assertIn(entry, self.window.inventory.entries)
        self.assertFalse(self.window.inventory_page.use_buttons[entry.instance_id].isEnabled())
        self.assertFalse(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertTrue(self.window.characters["Dave"]["alive"])

    def test_final_attack_publishes_camera_before_awarding_bare_no_items_victory(self):
        self.night()
        for name in ("Hank", "Owen", "Debra"):
            self.window.characters[name]["alive"] = False
        self.window.npc_items = game.NPCItemState({"Dave": "camera"})
        self.window.npc_items.capture_night(1, self.window.room_assignments, ("Dave", "Jerry"))
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.profile.counters["bare_hands_no_item_victory"], 1)
        self.assertTrue(any(record.source == "Photograph" for record in self.window.public_events))


if __name__ == "__main__":
    unittest.main()
