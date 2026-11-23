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
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

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
        self.window.run_action_phase()
        empty = next(room for room, occupants in self.window.room_assignments.items() if not occupants)
        self.window.choose_room(empty)
        self.assertIn(empty, self.window.new_crime_scenes)
        self.window.message_log.clear()
        self.window.show_transcript()
        text = self.window.transcript_page.editor.toPlainText()
        self.assertIn("Dave is carrying a Camera", text)
        self.assertIn(f"The {empty} is empty and is now also a crime scene.", text)
        self.assertNotIn("You must choose another room.", text)

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
