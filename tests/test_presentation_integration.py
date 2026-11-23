"""Real game presentation, live history and shared overlay lifecycle checks."""

import contextlib
import os
from pathlib import Path
import socket
import sys
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QColor, QFont, QFontInfo, QPalette
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QPushButton

import main as game


class PresentationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["presentation-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            "ALIBAI_SETTINGS_PATH": str(Path(directory.name) / "settings.ini"),
            "ALIBAI_PROFILE_PATH": str(Path(directory.name) / "profile.json"),
            "ALIBAI_DEV_MODE": "0",
        }))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.stack.enter_context(patch.object(game, "QMediaPlayer"))
        self.stack.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        self.stack.enter_context(patch.object(game.GameWindow, "run_conversation_loop"))
        self.window = game.GameWindow()
        self.app._main_window = self.window
        self.windows = [self.window]
        self.window.showNormal()
        self.window.resize(1000, 700)
        self.window.player_name = "Alex"
        self.window.characters = {name: {"alive": True, "profile": [], "experiences": [], "dispositions": []}
                                  for name in ("Dave", "Hank", "Jerry")}
        self.window.round_number = 1
        self.window.rooms_visited = []
        self.window.phase = "conversation"
        self.window.menu_widget.hide()
        self.window.message_log.show()
        self.app.processEvents()

    def tearDown(self):
        if any(getattr(self.app, "_main_window", None) is window for window in self.windows):
            del self.app._main_window
        for window in self.windows:
            for worker in list(window.active_workers):
                worker.wait(3000)
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def test_open_transcript_updates_live_with_literal_structured_speech(self):
        self.window.append_speech("Dave", "Jerry's <b>claim</b> & a question.")
        self.window.show_transcript()
        page = self.window.transcript_page
        self.assertEqual(page.editor.toPlainText(), "Dave\nJerry's <b>claim</b> & a question.")
        self.window.append_speech("Hank", "Another account.")
        self.window.add_public_event("A new discovery.")
        self.assertEqual(page.records, tuple(self.window.history_records))
        self.assertEqual([record.kind for record in page.records], ["speech", "speech", "event"])
        self.assertEqual(page.records[0].speaker_id, self.window.body_id("Dave"))
        body_font = page.editor.document().find("Jerry").charFormat().font()
        self.assertNotEqual(QFontInfo(body_font).family(), self.window.pixel_font_family)
        self.assertIn("Another account.", page.editor.toPlainText())

    def test_history_does_not_receive_private_context_drafts_status_or_unselected_proposals(self):
        self.window.remember("Dave", "experience", "Private memory sentinel")
        self.window.interject_input.setText("Unsent draft sentinel")
        self.window.append_output_text("Temporary status sentinel")
        replies = lambda context: game.SpeechProposal(None, "Selected reply" if context.character == "Dave"
                                                       else "Discarded proposal sentinel")
        with patch.object(self.window.ai_runtime, "speech", side_effect=replies), patch.object(
                self.window.ai_runtime, "select_reply", return_value="Dave"):
            self.window.run_multiple_responses()
            deadline = monotonic() + 3
            while self.window.active_workers and monotonic() < deadline:
                QTest.qWait(5)
        self.assertFalse(self.window.active_workers)
        self.window.show_transcript()
        text = self.window.transcript_page.editor.toPlainText()
        self.assertIn("Selected reply", text)
        for sentinel in ("Private memory sentinel", "Unsent draft sentinel", "Temporary status sentinel", "Discarded proposal sentinel"):
            self.assertNotIn(sentinel, text)
        self.window.record_history("Private player action sentinel")
        self.assertNotIn("Private player action sentinel", repr(self.window.build_character_context("Dave")))

    def test_scrolled_selection_and_unread_state_survive_new_dialogue(self):
        for index in range(35):
            self.window.append_speech("Dave", f"Account {index}: " + "A witness statement. " * 9)
        editor = self.window.output_area
        cursor = editor.document().find("Account 4:")
        editor.setTextCursor(cursor)
        editor.verticalScrollBar().setValue(0)
        before = (cursor.anchor(), cursor.position(), cursor.selectedText())
        self.window.append_speech("Hank", "A new line below.")
        self.app.processEvents()
        after = editor.textCursor()
        self.assertEqual((after.anchor(), after.position(), after.selectedText()), before)
        self.assertEqual(editor.verticalScrollBar().value(), 0)
        self.assertEqual(self.window.message_log.unread_count, 1)
        self.window.message_log.latest_button.click()
        self.assertEqual(editor.verticalScrollBar().value(), editor.verticalScrollBar().maximum())
        self.assertEqual(editor.textCursor().selectedText(), before[2])

    def test_phase_clear_and_public_morning_replay_preserve_single_history_record(self):
        self.window.add_public_event("A genuine discovery.", display=False)
        record = self.window.history_records[-1]
        self.window.new_crime_scenes = []
        self.window.victims_last_night = ["Earlier victim"]
        for _ in range(2):
            self.window.run_conversation_phase()
            self.assertEqual(self.window.output_area.toPlainText().count("A genuine discovery."), 1)
            self.assertEqual(sum(item.record_id == record.record_id for item in self.window.history_records), 1)
        self.window.message_log.clear()
        self.window.show_transcript()
        self.assertIn("A genuine discovery.", self.window.transcript_page.editor.toPlainText())

    def test_inventory_use_replaces_one_page_and_preserves_composing_selection(self):
        entry = self.window.inventory.grant("banana")
        self.window.interject_input.setText("My unfinished alibi")
        self.window.interject_input.setSelection(3, 10)
        self.window.interject_input.show()
        self.window.interject_input.setFocus()
        self.app.processEvents()
        before = (self.window.interject_input.text(), self.window.interject_input.selectedText())
        self.window.open_inventory()
        old_page = self.window.inventory_page
        old_page.target_boxes[entry.instance_id].setCurrentIndex(1)
        QTest.mouseClick(old_page.use_buttons[entry.instance_id], Qt.LeftButton)
        self.assertIsNot(self.window.inventory_page, old_page)
        self.assertIs(self.window.overlay_host.current_page, self.window.inventory_page)
        self.assertEqual(self.window.inventory.entries, ())
        self.assertEqual(self.window.public_events[-1].text, "Alex gave Hank a banana.")
        self.assertEqual(self.window.interject_input.text(), before[0])
        self.window.show_transcript()
        self.assertEqual(self.window.overlay_host.current_title, "Inventory")
        self.window.overlay_host.close_page()
        self.app.processEvents()
        self.assertEqual(self.window.interject_input.selectedText(), before[1])

    def test_nightly_equipment_and_empty_room_result_survive_clearing_the_live_view(self):
        self.window.npc_items = game.NPCItemState({"Dave": "camera"})
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        with patch.object(game.random, "choice", side_effect=lambda rooms: rooms[0]):
            self.window.run_action_phase()
        empty = "Cellar"
        self.assertNotIn(empty, self.window.night_arrivals.destinations.values())
        self.window.choose_room(empty)
        self.assertIn(empty, self.window.new_crime_scenes)
        self.window.message_log.clear()
        self.window.show_transcript()
        text = self.window.transcript_page.editor.toPlainText()
        self.assertNotIn("Dave is carrying a Camera", text)
        self.assertIn(f"The {empty} is empty and is now also a crime scene.", text)
        self.assertNotIn("You must choose another room.", text)

    def test_camera_equipment_stays_private_from_weapon_selection_through_action(self):
        self.window.player_name_input.setText("Alex")
        with patch.object(game.NPCItemState, "allocate", return_value=game.NPCItemState({"Dave": "camera"})):
            self.window.phase = "start"
            self.window.start_game()
        expected = "Known equipment: Dave is carrying a Camera."
        public_equipment = [event for event in self.window.public_events if event.source == "Visible equipment"]
        self.assertEqual([(event.round_number, event.text) for event in public_equipment], [])
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.assertEqual(self.window.layout.indexOf(self.window.npc_equipment_label), -1)
        self.assertIs(self.window.npc_equipment_label.parentWidget(), self.window.house_sidebar_content)
        self.window.show_room_allocations = False
        bare = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.select_weapon_and_start(bare)
        self.app.processEvents()
        label = self.window.npc_equipment_label
        self.assertTrue(label.isHidden())
        self.assertEqual(label.text(), "")
        self.assertEqual(label.font().pixelSize(), 18)
        self.assertTrue(all(not record.known_names and record.unknown_remainder
                            for record in self.window.house_view.observations.values()))
        self.assertNotIn("Known equipment", self.window.output_area.toPlainText())
        self.assertEqual([record.text for record in self.window.history_records if record.text == expected],
                         [])
        self.assertEqual([event.text for event in self.window.public_events if event.source == "Visible equipment"],
                         [])

    def test_dead_and_absent_equipment_never_adds_disclosure_history(self):
        self.window.npc_items = game.NPCItemState({"Dave": "camera"})
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.run_action_phase()
        self.assertEqual(self.window.npc_equipment_label.text(), "")
        self.window.characters["Dave"]["alive"] = False
        self.window.run_action_phase()
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.assertEqual(self.window.npc_equipment_label.text(), "")
        empty_equipment = "Known equipment: No special equipment is visible."
        self.assertEqual([record.text for record in self.window.history_records
                          if record.round_number == self.window.round_number
                          and record.text.startswith("Known equipment:")], [])
        self.assertNotIn(empty_equipment, self.window.output_area.toPlainText())
        self.window.npc_items = game.NPCItemState()
        self.window.run_action_phase()
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.assertEqual([record.text for record in self.window.history_records
                          if record.round_number == self.window.round_number
                          and record.text.startswith("Known equipment:")], [])

    def test_equipment_stays_hidden_across_reward_discussion_and_action(self):
        from items import DraftState
        self.window.npc_items = game.NPCItemState({"Dave": "camera"})
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.item_drafts = DraftState(self.window.player_owner_id, ("banana", "muffle", "spare_paper"))
        with patch.object(game.random, "choice", side_effect=lambda rooms: rooms[0]):
            self.window.run_action_phase()
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        empty = "Cellar"
        self.assertNotIn(empty, self.window.night_arrivals.destinations.values())
        self.window.choose_room(empty)
        self.assertFalse(self.window.npc_equipment_label.isVisible())
        self.window.draft_page.leave_button.click()
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        self.assertIn("empty", self.window.output_area.toPlainText())
        self.window.victims_last_night = ["Earlier victim"]
        self.window.run_conversation_phase()
        self.assertFalse(self.window.npc_equipment_label.isVisible())
        self.window.run_action_phase()
        self.assertTrue(self.window.npc_equipment_label.isHidden())

    def test_action_feedback_has_black_background_and_expanded_phases_restore_style(self):
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.run_action_phase()
        self.app.processEvents()
        editor = self.window.output_area
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.viewport().palette().color(QPalette.Base), QColor("black"))
        self.assertEqual(self.window.message_log.maximumHeight(), 128)
        self.window.append_output_text("A weapon effect.\nAn action error.")
        self.assertIn("An action error.", editor.toPlainText())
        self.assertEqual(editor.document().find("An action error.").charFormat().font().pixelSize(), 18)
        self.window.victims_last_night = ["Earlier victim"]
        self.window.run_conversation_phase()
        self.app.processEvents()
        self.assertEqual(self.window.message_log.maximumHeight(), 16777215)
        self.assertEqual(editor.viewport().palette().color(QPalette.Base), QColor("black"))
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.assertEqual(self.window.message_log.maximumHeight(), 16777215)
        self.assertEqual(editor.viewport().palette().color(QPalette.Base), QColor("black"))

    def test_game_over_directly_from_action_restores_normal_output_background(self):
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.run_action_phase()
        editor = self.window.output_area
        self.window.end_game_screen(4)
        self.app.processEvents()
        self.assertEqual(self.window.message_log.maximumHeight(), 16777215)
        self.assertEqual(editor.viewport().palette().color(QPalette.Base), QColor("black"))
        self.assertTrue(editor.isVisible())

    def test_controlled_attacker_is_retained_in_private_player_history(self):
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "blackmagic")
        self.window.run_action_phase()
        self.window.room_assignments = {"Attic": ["Dave"], "Kitchen": ["Hank"],
                                        "Library": ["Jerry"], "Basement": [], "Cellar": []}
        self.window.black_magic_controller, self.window.black_magic_target = "Dave", "Hank"
        self.window.apply_weapon_gating()
        self.window.choose_room("Basement")
        self.assertFalse(self.window.characters["Hank"]["alive"])
        self.window.message_log.clear()
        self.window.show_transcript()
        text = self.window.transcript_page.editor.toPlainText()
        self.assertIn("Dave (under your control) kills Hank in the Kitchen.", text)
        self.assertFalse(any("(under your control)" in event.text
                             for event in self.window.build_character_context("Jerry").public_events))

    def test_shared_help_and_transcript_never_stack_and_closed_pages_disconnect(self):
        self.window.show_how_to_play()
        page = self.window.help_page
        self.window.show_transcript()
        self.assertIs(self.window.overlay_host.current_page, page)
        self.window.overlay_host.close_page()
        self.window.show_transcript()
        old = self.window.transcript_page
        self.window.open_escape_menu()
        self.assertIsNone(self.window.overlay_host.current_page)
        self.assertIsNone(self.window._esc_overlay)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(old))
        self.window.append_speech("Hank", "After the old page closed.")
        self.window.show_transcript()
        self.assertIn("After the old page closed.", self.window.transcript_page.editor.toPlainText())

    def test_stage_phase_lifecycle_and_body_target_clicks(self):
        self.window.refresh_conversation_stage()
        stage = self.window.conversation_stage
        self.assertTrue(stage.isVisible())
        ids = {p.body_id for p in stage.participants}
        self.assertEqual(ids, {self.window.body_id(name) for name in self.window.characters})
        self.window.append_speech("Hank", "A visible account.")
        self.assertEqual(stage.active_speaker, self.window.body_id("Hank"))
        self.window.characters["Dave"]["alive"] = False
        self.window.refresh_conversation_stage()
        self.assertNotIn(self.window.body_id("Dave"), {p.body_id for p in stage.participants})
        body_id = self.window.body_id("Jerry")
        QTest.mouseClick(stage._buttons[body_id], Qt.LeftButton)
        self.assertEqual(self.window.recipient_selector.currentData(), body_id)
        self.assertTrue(self.window.interject_input.isVisible())
        self.window.selected_weapon = next(w for w in self.window.weapons if w["id"] == "bare")
        self.window.run_action_phase()
        self.assertTrue(stage.isHidden())
        with patch.object(game.random, "choice", return_value="Hank"):
            self.window.run_conversation_phase()
        self.assertTrue(stage.isVisible())
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.assertEqual(self.window.phase, "voting")
        self.assertTrue(stage.isHidden())

    def test_microphone_preferences_and_pause_settings_use_one_overlay_without_recording(self):
        recorder = self.window.dictation_recorder
        self.window.preferences.save_player_name("Remembered player")
        with patch.object(recorder, "input_devices", return_value=[("usb-input", "USB microphone")]), patch.object(
                recorder, "start", side_effect=AssertionError("Settings must not open audio")):
            self.window.open_escape_menu()
            host = self.window.overlay_host
            menu = host.current_page
            self.assertIsNone(menu.findChild(QPushButton, "pauseModels"))
            menu.findChild(QPushButton, "pauseMicrophone").click()
            selector = host.current_page.findChild(QComboBox, "microphoneDeviceSelector")
            self.assertEqual(selector.currentData(), "")
            selector.setCurrentIndex(selector.findData("usb-input"))
            selector.activated[int].emit(selector.currentIndex())
            self.assertEqual(recorder.device_name, "usb-input")
            self.assertEqual(self.window.preferences.microphone_name(), "usb-input")
            self.assertEqual(self.window.preferences.last_player_name(), "Remembered player")
            self.window.show_microphone_settings()
            selector = host.current_page.findChild(QComboBox, "microphoneDeviceSelector")
            self.assertEqual(selector.currentData(), "usb-input")
            selector.setCurrentIndex(selector.findData(""))
            selector.activated[int].emit(selector.currentIndex())
            self.assertEqual(recorder.device_name, "")
            self.assertEqual(self.window.preferences.microphone_name(), "")
            self.assertEqual(len([w for w in self.app.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()]), 1)
            current = host.current_page
            self.window.show_model_configuration()
            self.assertIs(host.current_page, current)
            host.current_page.findChild(QPushButton, "settingsBack").click()
            self.assertEqual(host.current_title, "Menu")
            host.current_page.findChild(QPushButton, "pauseResume").click()
            self.assertIsNone(host.current_page)
            self.assertFalse(recorder.is_recording)

    def test_restart_cancels_old_overlay_and_starts_with_empty_history(self):
        self.window.append_speech("Dave", "Previous run.")
        self.window.show_transcript()
        old_host = self.window.overlay_host
        self.window.restart_game()
        deadline = monotonic() + 3
        while getattr(self.app, "_main_window", self.window) is self.window and monotonic() < deadline:
            QTest.qWait(5)
        replacement = self.app._main_window
        self.assertIsNot(replacement, self.window)
        self.windows.append(replacement)
        self.assertTrue(old_host.owner_cancelled)
        self.assertEqual(replacement.history_records, [])
        self.assertIsNone(replacement.overlay_host.current_page)
        self.window.history_record_added.emit(self.window.history_records[0])
        self.assertEqual(replacement.history_records, [])


if __name__ == "__main__":
    unittest.main()
