"""Offline regression checks using the real Qt widgets and worker signals.

Run with: python -m unittest discover -s tests -v
"""

import contextlib
import itertools
import os
from pathlib import Path
import socket
import sys
import tempfile
from threading import Barrier, Event
from time import monotonic
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QScrollArea, QTextEdit, QLabel

with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
    import main as game
    import ai_runtime

from progression import ProfileError, ProgressEvent


def character(alive=True):
    return {"alive": alive, "profile": [], "experiences": [], "dispositions": []}


class RuntimeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["runtime-regressions"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32" and cls.app.platformName() == "offscreen":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.profile_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.profile_directory.cleanup)
        self.patches.enter_context(patch.dict(os.environ, {
            "ALIBAI_DEV_MODE": "0", "ALIBAI_DEV_VOTE_MINIMUM": "",
            "ALIBAI_PROFILE_PATH": str(Path(self.profile_directory.name) / "profile.json"),
            "ALIBAI_SETTINGS_PATH": str(Path(self.profile_directory.name) / "preferences.ini"),
        }))
        self.patches.enter_context(patch.object(
            socket.socket, "connect", side_effect=AssertionError("Network access in offline test")
        ))
        self.openai_constructor = self.patches.enter_context(patch.object(
            ai_runtime, "OpenAI", side_effect=AssertionError("Unexpected API client creation")
        ))
        self.patches.enter_context(patch.object(game, "QMediaPlayer"))
        self.speech = self.patches.enter_context(patch.object(
            game.AIRuntime, "speech", return_value=game.SpeechProposal(None, "A reply.")
        ))
        self.vote = self.patches.enter_context(patch.object(game.AIRuntime, "vote", return_value="NONE"))
        self.selector = self.patches.enter_context(patch.object(
            game.AIRuntime, "select_reply", side_effect=lambda state, events, dialogue, proposals: next(iter(proposals))
        ))
        self.patches.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        self.window = game.GameWindow()
        self.app.processEvents()
        self.window.player_name = "Alex"
        self.window.characters = {
            "Jerry": character(), "Dave": character(), "Hank": character(),
            "Debra": character(False),
        }

    def tearDown(self):
        for worker in self.window.active_workers:
            self.assertFalse(worker.isRunning(), "Test left a running worker")
        self.window.close_escape_menu()
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def begin_vote(self):
        self.window.phase = "conversation"
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.assertEqual(self.window.expected_voters, {"Alex", "Jerry", "Dave", "Hank"})

    def wait_for(self, condition, message="The queued operation did not complete"):
        deadline = monotonic() + 3
        while not condition() and monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(condition(), message)

    def prepare_vote_call(self):
        self.window.phase = "conversation"
        for index in range(20):
            self.window.append_speech("Dave", f"Account {index + 1}.")

    def test_title_screen_opens_without_api_key_or_client(self):
        self.assertEqual(self.window.phase, "start")
        self.assertTrue(self.window.start_button.isVisible())
        self.openai_constructor.assert_not_called()

    def test_small_menu_tabs_preserve_play_name_and_difficulty(self):
        self.window.player_name_input.setText("A name in progress")
        self.window.difficulty_button.click()
        mode = self.window.show_room_allocations
        mode_text = self.window.difficulty_button.text()
        self.assertIs(self.window.menu_stack.currentWidget(), self.window.play_page)
        self.window.items_tab_button.click()
        self.assertEqual(self.window.phase, "start")
        self.assertIs(self.window.menu_stack.currentWidget(), self.window.item_catalogue)
        self.assertTrue(self.window.item_catalogue.isVisible())
        self.assertFalse(self.window.player_name_input.isVisible())
        self.assertTrue(self.window.items_tab_button.isChecked())
        self.assertTrue(self.window.play_tab_button.isVisible())
        self.window.play_tab_button.click()
        self.assertIs(self.window.menu_stack.currentWidget(), self.window.play_page)
        self.assertTrue(self.window.start_button.isVisible())
        self.assertEqual(self.window.player_name_input.text(), "A name in progress")
        self.assertEqual(self.window.show_room_allocations, mode)
        self.assertEqual(self.window.difficulty_button.text(), mode_text)
        self.assertFalse(self.window.profile_store.path.exists())
        self.openai_constructor.assert_not_called()

    def test_menu_corner_controls_stay_at_current_window_edge_without_covering_tabs(self):
        self.window.setMinimumSize(1, 1)
        self.window.setMaximumSize(16777215, 16777215)
        self.window.showNormal()
        for width in (1280, 1000):
            self.window.resize(width, 800)
            self.app.processEvents()
            self.assertEqual(self.window.how_to_play_button.geometry().right(), self.window.width() - 11)
            self.assertGreaterEqual(self.window.how_to_play_button.width(), self.window.how_to_play_button.sizeHint().width())
            for tab in (self.window.play_tab_button, self.window.items_tab_button):
                rectangle = tab.rect().translated(tab.mapTo(self.window, tab.rect().topLeft()))
                self.assertFalse(rectangle.intersects(self.window.how_to_play_button.geometry()))

    def test_browsing_item_catalogue_preserves_existing_profile_data(self):
        self.window.profile_store.record(ProgressEvent("earlier", 1, "survived_vote"))
        original = self.window.profile_store.path.read_bytes()
        with patch.object(self.window.profile_store, "set_item_enabled") as toggle:
            for _ in range(2):
                self.window.items_tab_button.click()
                self.assertIn("guardian_angel", self.window.item_catalogue.checkboxes)
                self.window.play_tab_button.click()
        toggle.assert_not_called()
        self.assertEqual(self.window.profile_store.path.read_bytes(), original)

    def test_item_menu_pool_toggle_persists_then_freezes_at_run_start(self):
        self.window.profile_store.record(ProgressEvent("earlier", 1, "survived_vote"))
        self.window.items_tab_button.click()
        self.window.item_catalogue.checkboxes["guardian_angel"].setChecked(False)
        self.assertIn("guardian_angel", self.window.profile_store.read().disabled_items)
        self.window.play_tab_button.click()
        self.window.player_name_input.setText("Alex")
        self.window.start_button.click()
        self.assertEqual(self.window.phase, "weapon_select")
        self.assertFalse(self.window.menu_widget.isVisible())
        self.assertFalse(self.window.play_tab_button.isVisible())
        self.assertFalse(self.window.item_catalogue.isVisible())
        goals = [label.text() for label in self.window.weapon_picker_widget.findChildren(QLabel)
                 if "\n\nLocked:" in label.text()]
        knife = next(weapon for weapon in self.window.weapons if weapon["id"] == "knife")
        self.assertIn(knife["desc"] + "\n\nLocked: Kill your first victim.", goals)
        bare = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.select_weapon_and_start(bare)
        self.assertEqual(self.window.phase, "action")
        self.assertNotIn("guardian_angel", self.window.run_item_pool)
        self.window.profile_store.set_item_enabled("guardian_angel", True)
        self.assertNotIn("guardian_angel", self.window.item_drafts.eligible_item_ids)
        with self.assertRaises(ProfileError):
            self.window.set_menu_item_enabled("guardian_angel", False)
        self.assertNotIn("guardian_angel", self.window.profile_store.read().disabled_items)
        self.window.items_tab_button.click()
        self.assertFalse(self.window.menu_widget.isVisible())
        self.assertEqual(self.window.phase, "action")

    def test_item_menu_save_failure_preserves_checkbox_and_shows_error_on_items_page(self):
        self.window.profile_store.record(ProgressEvent("earlier", 1, "survived_vote"))
        self.window.items_tab_button.click()
        original = self.window.profile_store.path.read_bytes()
        with patch.object(self.window.profile_store, "set_item_enabled", side_effect=ProfileError("Disk is unavailable.")):
            self.window.item_catalogue.checkboxes["guardian_angel"].setChecked(False)
        self.assertTrue(self.window.item_catalogue.checkboxes["guardian_angel"].isChecked())
        self.assertIs(self.window.menu_stack.currentWidget(), self.window.item_catalogue)
        self.assertTrue(self.window.progress_notice.isVisible())
        self.assertIn("Disk is unavailable", self.window.progress_notice.text())
        self.assertTrue(self.window.item_catalogue.error_label.isVisible())
        self.assertEqual(self.window.profile_store.path.read_bytes(), original)
        self.window.item_catalogue.checkboxes["guardian_angel"].setChecked(False)
        self.assertFalse(self.window.item_catalogue.checkboxes["guardian_angel"].isChecked())
        self.assertTrue(self.window.progress_retry_button.isHidden())
        self.assertTrue(self.window.item_catalogue.error_label.isHidden())

    def test_items_page_load_error_preserves_corrupt_file_and_retry_refreshes_same_page(self):
        self.window.profile_store.record(ProgressEvent("earlier", 1, "survived_vote"))
        original = self.window.profile_store.path.read_bytes()
        self.window.profile_store.path.write_bytes(b"broken profile")
        self.window.items_tab_button.click()
        self.assertTrue(self.window.progress_notice.isVisible())
        self.assertIn("invalid", self.window.progress_notice.text())
        self.assertIs(self.window.menu_stack.currentWidget(), self.window.item_catalogue)
        self.assertFalse(self.window.item_catalogue.checkboxes)
        self.assertEqual(self.window.profile_store.path.read_bytes(), b"broken profile")
        self.window.progress_retry_button.click()
        self.assertTrue(self.window.progress_retry_button.isVisible())
        self.assertIn("invalid", self.window.progress_notice.text())
        self.window.profile_store.path.write_bytes(original)
        self.window.progress_retry_button.click()
        self.assertTrue(self.window.progress_retry_button.isHidden())
        self.assertIs(self.window.menu_stack.currentWidget(), self.window.item_catalogue)
        self.assertIn("guardian_angel", self.window.item_catalogue.checkboxes)
        self.assertEqual(self.window.profile_store.path.read_bytes(), original)

    def test_blank_name_error_remains_visible_on_start_screen(self):
        self.window.player_name_input.setText("   ")
        self.window.start_button.click()
        self.assertEqual(self.window.phase, "start")
        self.assertTrue(self.window.player_name_label.isVisible())
        self.assertIn("Please enter your name", self.window.player_name_label.text())
        self.assertTrue(self.window.how_to_play_button.isVisible())

    def test_development_observability_does_not_shorten_voting_without_explicit_override(self):
        self.assertEqual(self.window.minimum_to_call_vote, 20)
        cases = (
            ({"ALIBAI_DEV_MODE": "0", "ALIBAI_DEV_VOTE_MINIMUM": "2"}, 20),
            ({"ALIBAI_DEV_MODE": "1", "ALIBAI_DEV_VOTE_MINIMUM": ""}, 20),
            ({"ALIBAI_DEV_MODE": "1", "ALIBAI_DEV_VOTE_MINIMUM": "2"}, 2),
            ({"ALIBAI_DEV_MODE": "1", "ALIBAI_DEV_VOTE_MINIMUM": "1"}, 20),
            ({"ALIBAI_DEV_MODE": "1", "ALIBAI_DEV_VOTE_MINIMUM": "invalid"}, 20),
        )
        for configuration, expected in cases:
            with self.subTest(configuration=configuration), patch.dict(os.environ, configuration):
                self.assertEqual(game.vote_conversation_minimum(), expected)
        with tempfile.TemporaryDirectory() as trace_directory, patch.dict(os.environ, {
            "ALIBAI_DEV_MODE": "1", "ALIBAI_DEV_VOTE_MINIMUM": "2",
            "ALIBAI_TRACE_DIR": trace_directory,
        }):
            development_window = game.GameWindow()
            try:
                self.assertEqual(development_window.minimum_to_call_vote, 2)
            finally:
                development_window.close()
                development_window.deleteLater()

    def test_vote_eligibility_is_enforced_at_the_twentieth_message(self):
        self.window.phase = "conversation"
        self.window.append_speech("Detective", "Where were you last night?")
        with patch.object(self.window, "run_voting_phase") as vote:
            for index in range(18):
                self.window.append_speech("Dave", f"Account {index + 1}.")
            self.window.run_conversation_loop()
            self.assertEqual(len(self.window.current_conversation), 19)
            self.assertFalse(self.window.can_call_vote())
            self.assertFalse(self.window.vote_button.isEnabled())
            self.window.call_vote()
            vote.assert_not_called()
            self.window.append_speech("Dave", "One more account.")
            self.window.run_conversation_loop()
            self.assertTrue(self.window.can_call_vote())
            self.assertTrue(self.window.vote_button.isEnabled())
            self.window.ai_busy = True
            self.window.call_vote()
            vote.assert_not_called()
            self.window.ai_busy = False
            self.window.call_vote()
            vote.assert_called_once()

    def test_request_progress_is_one_quiet_indicator_across_stages(self):
        self.window.phase = "conversation"
        for status in ("The group is responding...", "The conversation is continuing..."):
            with self.subTest(status=status):
                entered, release = Event(), Event()

                def operation():
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError("Activity probe was not released")
                    return "complete"

                try:
                    self.window.start_ai_request(operation, Mock(), status)
                    self.wait_for(entered.is_set)
                    self.assertEqual(self.window.secondary_output_area.text(), "...")
                    self.assertNotIn(status, self.window.output_area.toPlainText())
                    release.set()
                    self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
                    self.assertEqual(self.window.secondary_output_area.text(), "")
                finally:
                    release.set()
                    for worker in list(self.window.active_workers):
                        worker.wait(3000)
                    self.app.processEvents()

    def test_structured_npc_address_requires_player_reply_without_fabricating_speech(self):
        self.window.phase = "conversation"
        for speech in ("I spent the night alone in the attic.", "Where were you?"):
            with self.subTest(speech=speech), patch.object(
                self.window, "run_multiple_responses"
            ) as group, patch.object(self.window, "run_single_response") as single:
                self.window.append_speech("Dave", speech, "Alex")
                before = tuple(self.window.public_dialogue)
                self.window.run_conversation_loop()
                self.assertFalse(self.window.pass_button.isEnabled())
                self.assertTrue(self.window.interject_button.isEnabled())
                self.assertTrue(self.window.interject_input.isVisible())
                self.assertTrue(self.window.say_button.isVisible())
                self.assertEqual(self.window.secondary_output_area.text(), "")
                group.assert_not_called()
                single.assert_not_called()
                self.window.pass_button.click()
                group.assert_not_called()
                single.assert_not_called()
                self.assertEqual(tuple(self.window.public_dialogue), before)
                self.assertFalse(any(line.speaker == "Alex" for line in self.window.public_dialogue))

    def test_pending_reply_blocks_all_conversation_advance_paths_until_actual_reply(self):
        self.prepare_vote_call()
        self.window.append_speech("Hank", "Where were you last night?", "Alex")
        self.window.run_conversation_loop()
        before = tuple(self.window.public_dialogue)
        self.assertTrue(self.window.player_reply_required())
        self.assertFalse(self.window.can_call_vote())
        self.assertFalse(self.window.vote_button.isEnabled())
        self.window.pass_button.setEnabled(True)
        retry = Mock()
        self.window.retry_action = retry
        with patch.object(self.window, "start_ai_request") as request:
            self.window.pass_button.click()
            self.window.call_vote()
            self.window.run_voting_phase()
            self.window.run_multiple_responses()
            self.window.run_single_response("Dave")
            self.window.retry_ai_request()
        request.assert_not_called()
        retry.assert_not_called()
        self.assertEqual(self.window.phase, "conversation")
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.window.interject_input.setText("   ")
        self.window.send_interjection()
        self.assertTrue(self.window.player_reply_required())
        self.window.interject_input.setText("I was upstairs.")
        self.window.interject_conversation()
        self.assertEqual(self.window.interject_input.text(), "I was upstairs.")
        with patch.object(self.window, "run_multiple_responses") as group:
            self.window.send_interjection()
        group.assert_called_once()
        self.assertFalse(self.window.player_reply_required())
        self.assertTrue(self.window.can_call_vote())
        self.assertEqual(self.window.public_dialogue[-1].text, "I was upstairs.")

    def test_reply_requirement_uses_current_round_metadata_not_name_mentions(self):
        self.window.phase = "conversation"
        self.window.append_speech("Dave", "Alex, where were you?", None)
        self.assertFalse(self.window.player_reply_required())
        self.window.append_speech("Dave", "What happened?", "Alex")
        self.assertTrue(self.window.player_reply_required())
        self.window.round_number += 1
        self.window.run_conversation_loop()
        self.assertFalse(self.window.player_reply_required())
        self.assertTrue(self.window.pass_button.isEnabled())
        self.assertFalse(self.window.interject_input.isVisible())

    def test_pending_reply_does_not_start_a_retry_or_new_ai_job(self):
        self.window.phase = "conversation"
        entered = Mock(side_effect=game.AIRequestError("Temporary failure"))
        self.window.start_ai_request(entered, Mock(), "Waiting")
        self.wait_for(lambda: not self.window.active_workers)
        self.assertIsNotNone(self.window.retry_action)
        self.window.append_speech("Dave", "And your account?", "Alex")
        self.window.run_conversation_loop()
        self.assertIsNone(self.window.retry_action)
        self.assertFalse(self.window.retry_button.isVisible())
        self.assertFalse(self.window.pass_button.isEnabled())
        new_operation = Mock()
        self.window.start_ai_request(new_operation, Mock(), "Waiting")
        self.window.retry_ai_request()
        new_operation.assert_not_called()
        self.assertEqual(entered.call_count, 1)
        self.assertEqual(self.window.active_workers, [])

    def test_public_output_preserves_literal_punctuation_markup_and_newlines(self):
        self.window.phase = "conversation"
        literal = "Jerry's <b>note</b> & a literal &#x27;\nA second line."
        self.window.add_public_event(literal)
        self.assertEqual(self.window.output_area.toPlainText(), literal)
        self.assertEqual(self.window.public_events[-1].text, literal)
        self.assertEqual(self.window.history_records[-1].text, literal)

    def test_name_preference_uses_literal_field_not_current_body(self):
        literal = "  JoSH O'Connor \u03a9  "
        self.window.player_name_input.setText(literal)
        self.window.player_name_input.editingFinished.emit()
        self.window.player_name = "Dave"
        self.window.close()
        replacement = game.GameWindow()
        try:
            self.assertEqual(replacement.player_name_input.text(), literal)
            self.assertEqual(replacement.phase, "start")
        finally:
            replacement.close()
            replacement.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_name_save_failure_does_not_block_start_or_reset_preferences(self):
        self.window.player_name_input.setText("Josh")
        with patch.object(self.window.preferences, "save_player_name", return_value=False):
            self.window.start_game()
        self.assertEqual(self.window.phase, "weapon_select")
        self.assertIn("could not be remembered", self.window.player_name_input.toolTip())

    def test_npc_addressee_never_automatically_hands_the_floor_to_that_npc(self):
        self.window.phase = "conversation"
        self.window.append_speech("Dave", "Jerry, where were you?", "Jerry")
        with patch.object(self.window, "run_multiple_responses") as group, patch.object(
            self.window, "run_single_response"
        ) as single:
            self.window.run_conversation_loop()
            group.assert_not_called()
            single.assert_not_called()
            self.assertTrue(self.window.pass_button.isEnabled())
            self.assertTrue(self.window.interject_button.isEnabled())
            self.window.pass_button.click()
            group.assert_called_once()
            single.assert_not_called()

    def test_player_submission_starts_group_selection_with_only_user_supplied_words(self):
        self.window.phase = "conversation"
        self.window.interject_input.setText("Those are my words.")
        with patch.object(self.window, "run_multiple_responses") as group, patch.object(
            self.window, "run_single_response"
        ) as single:
            self.window.send_interjection()
            group.assert_called_once()
            single.assert_not_called()
        self.assertEqual(tuple(self.window.public_dialogue),
                         (game.DialogueLine(0, "Alex", "Those are my words."),))

    def test_opening_detective_invitation_requires_reply_and_uses_group_for_npcs(self):
        self.window.phase = "conversation"
        with patch.object(self.window, "run_multiple_responses") as group, patch.object(
            self.window, "run_single_response"
        ) as single:
            self.window.append_speech("Detective", "Where were you last night?", "Alex")
            self.window.run_conversation_loop()
            group.assert_not_called()
            single.assert_not_called()
            self.assertFalse(self.window.pass_button.isEnabled())
            self.assertTrue(self.window.interject_input.isVisible())
            self.window.append_speech("Detective", "Where were you last night?", "Jerry")
            self.window.run_conversation_loop()
            group.assert_called_once()
            single.assert_not_called()

    def test_dialogue_and_input_use_readable_body_font_with_pixel_speaker_names(self):
        self.window.append_speech("Dave", "A readable <b>literal</b> account.")
        document = self.window.output_area.document()
        body_font = game.readable_body_font()
        self.assertEqual(document.defaultFont().family(), body_font.family())
        self.assertEqual(document.defaultFont().pixelSize(), 18)
        self.assertEqual(self.window.interject_input.font().family(), body_font.family())
        self.assertEqual(self.window.interject_input.font().pixelSize(), 18)
        speaker_format = document.find("Dave").charFormat()
        self.assertEqual(speaker_format.fontFamily(), self.window.pixel_font_family)
        self.assertEqual(speaker_format.font().pixelSize(), 12)
        body_format = document.find("A readable").charFormat()
        resolved_body = QFontInfo(body_format.font().resolve(document.defaultFont())).family()
        resolved_speaker = QFontInfo(speaker_format.font().resolve(document.defaultFont())).family()
        self.assertNotEqual(resolved_body, resolved_speaker)
        self.assertNotEqual(QFontInfo(self.window.interject_input.font()).family(), resolved_speaker)
        self.assertIn("A readable <b>literal</b> account.", self.window.output_area.toPlainText())
        self.assertEqual(self.window.conversation_buttons_layout.count(), 3)
        self.assertEqual([self.window.conversation_buttons_layout.itemAt(i).widget().text()
                          for i in range(3)], ["Pass", "Interject", "Call Vote"])

    def test_help_and_transcript_are_readable_and_fit_the_available_screen(self):
        self.window.append_speech("Dave", "A <b>literal</b> account.")
        for open_page in (self.window.show_how_to_play, self.window.show_transcript):
            open_page()
            self.app.processEvents()
            host = self.window.overlay_host
            self.assertTrue(host.screen().availableGeometry().contains(host.geometry()))
            body = host.current_page.findChild(QTextEdit)
            self.assertEqual(body.document().defaultFont().family(), game.readable_body_font().family())
            self.assertEqual(body.document().defaultFont().pixelSize(), 18)
            self.assertEqual(host.title_label.font().family(), self.window.pixel_font_family)
            if host.current_title == "How to Play":
                self.assertEqual(body.textCursor().position(), 0)
                self.assertNotIn("hallucinate", body.toPlainText())
                self.assertNotIn("capitalize", body.toPlainText())
                self.assertNotIn("\n\n\n", body.toPlainText())
            else:
                self.assertEqual(body.toPlainText(), "Dave\nA <b>literal</b> account.")
            host.close_page()

    def test_escape_menu_timer_runs_and_menu_closes(self):
        QTest.keyClick(self.window, Qt.Key_Escape)
        QTest.qWait(40)
        self.assertTrue(self.window._esc_overlay.isVisible())
        QTest.keyClick(self.window, Qt.Key_Escape)
        self.assertTrue(self.window._esc_overlay is None or self.window._esc_overlay.isHidden())

    def test_weapon_picker_scrolls_to_every_card_at_laptop_size(self):
        self.window.showNormal()
        self.window.setFixedSize(1366, 768)
        self.window.player_name_input.setText("Alex")
        self.window.start_button.click()
        self.app.processEvents()
        scroll = self.window.weapon_scroll
        self.assertIsInstance(scroll, QScrollArea)
        self.assertTrue(scroll.isVisible())
        self.assertLessEqual(scroll.width(), self.window.width())
        self.assertEqual(self.window.weapon_grid.count(), len(self.window.weapons))
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        last_card = self.window.weapon_grid.itemAt(len(self.window.weapons) - 1).widget()
        scroll.ensureWidgetVisible(last_card)
        self.app.processEvents()
        self.assertTrue(scroll.viewport().rect().intersects(
            last_card.rect().translated(last_card.mapTo(scroll.viewport(), last_card.rect().topLeft()))
        ))

    def test_source_resources_load_outside_working_directory(self):
        original_cwd = os.getcwd()
        expected = game.load_system_prompt()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                self.assertEqual(game.load_system_prompt(), expected)
                self.assertTrue(Path(game.resource_path("icons/audio.png")).is_file())
                self.assertTrue(Path(game.resource_path("House_images/Scaled/Attic.png")).is_file())
            finally:
                os.chdir(original_cwd)

    def test_voting_waits_for_separate_player_in_every_arrival_order(self):
        for order in itertools.permutations(("Alex", "Jerry", "Dave", "Hank")):
            with self.subTest(order=order), patch.object(self.window, "add_continue_button") as advance:
                self.begin_vote()
                for index, voter in enumerate(order):
                    if voter == "Alex":
                        self.window.player_vote("NONE")
                    else:
                        self.window.collect_ai_vote(voter, "NONE")
                    self.assertEqual(self.window.voting_resolved, index == len(order) - 1)
                    self.assertEqual(advance.call_count, int(index == len(order) - 1))
                self.window.tally_votes()
                self.window.collect_ai_vote("Jerry", "Alex")
                self.window.player_vote("Jerry")
                advance.assert_called_once()
                self.assertEqual(self.window.votes["Alex"], "NONE")
                self.assertFalse(self.window.awaiting_player_vote)

    def test_voting_buttons_stay_enabled_while_ai_votes_are_pending(self):
        self.prepare_vote_call()
        with patch.object(game.AIWorker, "start"):
            self.window.call_vote()
        self.assertTrue(self.window.isEnabled())
        self.assertTrue(self.window.vote_buttons_widget.isEnabled())
        for worker in list(self.window.active_workers):
            self.window.release_ai_worker(worker)

    def test_missing_ballots_produce_no_partial_results(self):
        self.begin_vote()
        self.window.collect_ai_vote("Jerry", "Dave")
        self.window.tally_votes()
        self.assertFalse(self.window.voting_resolved)
        self.assertNotIn("Jerry voted", self.window.output_area.toPlainText())

    def test_invalid_ai_ballots_skip_instead_of_targeting_dead_or_self(self):
        for vote in ("Debra", "Jerry", "Unknown", "", None):
            with self.subTest(vote=vote):
                self.assertEqual(game.validate_vote("Jerry", vote, self.window.characters, "Alex"), "NONE")
                self.window.item_rules = game.PlayerItemRules(self.window.player_owner_id, self.window.inventory)
                self.begin_vote()
                self.window.collect_ai_vote("Jerry", vote)
                self.assertEqual(self.window.votes["Jerry"], "NONE")
        self.assertEqual(game.validate_vote("Jerry", "Alex", self.window.characters, "Alex"), "Alex")
        self.assertEqual(game.validate_vote("Jerry", "Dave", self.window.characters, "Alex"), "Dave")

    def test_duplicate_or_dead_voters_cannot_overwrite_ballots(self):
        self.begin_vote()
        self.window.collect_ai_vote("Jerry", "Dave")
        self.window.collect_ai_vote("Jerry", "Alex")
        self.window.collect_ai_vote("Debra", "Alex")
        self.window.collect_ai_vote("Alex", "Jerry")
        self.assertEqual(self.window.votes, {"Jerry": "Dave"})

    def test_complete_vote_can_execute_an_npc_once(self):
        self.begin_vote()
        with patch.object(self.window, "add_continue_button") as advance:
            self.window.player_vote("Dave")
            self.window.collect_ai_vote("Jerry", "Dave")
            self.window.collect_ai_vote("Hank", "Dave")
            self.assertTrue(self.window.characters["Dave"]["alive"])
            self.window.collect_ai_vote("Dave", "Jerry")
            self.window.tally_votes()
            self.assertFalse(self.window.characters["Dave"]["alive"])
            advance.assert_called_once()

    def test_player_loss_sets_terminal_state_and_rejects_further_actions(self):
        self.begin_vote()
        self.window.player_vote("Jerry")
        for npc in ("Jerry", "Dave", "Hank"):
            self.window.collect_ai_vote(npc, "Alex")
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.phase, "game_over")
        self.assertTrue(self.window.weapon_action_panel.isHidden())
        before = self.window.round_number
        self.window.run_action_phase()
        self.assertEqual(self.window.round_number, before)

    def test_room_gating_preserves_old_and_new_crime_scenes(self):
        self.window.old_crime_scenes = ["Attic"]
        self.window.new_crime_scenes = ["Kitchen"]
        self.window.set_room_buttons_enabled(False)
        self.window.set_room_buttons_enabled(True)
        self.assertFalse(self.window.room_buttons["Attic"].isEnabled())
        self.assertFalse(self.window.room_buttons["Kitchen"].isEnabled())
        self.assertTrue(self.window.room_buttons["Library"].isEnabled())
        self.assertFalse(self.window.room_labels["Library"].isHidden())

    def test_disabled_room_cannot_be_entered_by_direct_callback(self):
        self.window.phase = "action"
        self.window.room_buttons_widget.show()
        self.window.room_buttons["Attic"].setEnabled(False)
        self.window.rooms_visited = []
        self.window.new_crime_scenes = []
        self.window.choose_room("Attic")
        self.assertEqual(self.window.rooms_visited, [])
        self.assertEqual(self.window.new_crime_scenes, [])

    def test_c4_cooldown_does_not_close_sleeping_room(self):
        self.window.phase = "action"
        self.window.selected_weapon = {"id": "c4"}
        self.window.c4_cooldown_nights = 1
        self.window.rooms_visited = []
        self.window.new_crime_scenes = []
        self.window.room_assignments = {room: [] for room in self.window.rooms}
        self.window.room_buttons_widget.show()
        with patch.object(self.window, "update_witness_memories") as memories, patch.object(
            self.window, "run_conversation_phase"
        ) as conversation:
            self.window.choose_room("Attic")
        self.assertEqual(self.window.new_crime_scenes, [])
        self.assertEqual(self.window.rooms_visited, ["Attic"])
        memories.assert_called_once_with(None)
        conversation.assert_called_once()

    def test_pen_reveals_one_shared_signature_to_every_character(self):
        for roll, signature in ((0.01, "Alex"), (0.99, "Debra")):
            with self.subTest(roll=roll):
                self.window.selected_weapon = {"id": "pen"}
                self.window.pending_pen_note_text = "Trust nobody"
                self.window.victims_last_night = ["Debra"]
                self.window.new_crime_scenes = []
                self.window.public_events = []
                with patch.object(game.random, "random", return_value=roll) as random_roll, patch.object(
                    self.window, "run_conversation_loop"
                ):
                    self.window.run_conversation_phase()
                random_roll.assert_called_once()
                notes = [next(event.text for event in self.window.build_character_context(name).public_events
                              if "note was found" in event.text) for name in self.window.characters]
                self.assertEqual(len(set(notes)), 1)
                self.assertIn("Trust nobody - Signed: " + signature, notes[0])
                self.assertEqual(sum("note was found" in event.text for event in self.window.public_events), 1)

    def test_pen_without_victim_does_not_reveal_a_note(self):
        self.window.selected_weapon = {"id": "pen"}
        self.window.pending_pen_note_text = "Trust nobody"
        self.window.victims_last_night = []
        self.window.new_crime_scenes = []
        with patch.object(game.random, "random") as random_roll, patch.object(
            self.window, "run_conversation_loop"
        ):
            self.window.run_conversation_phase()
        random_roll.assert_not_called()
        self.assertFalse(any("note was found" in event.text for event in self.window.public_events))

    def test_weapon_panel_stays_hidden_outside_room_selection(self):
        self.window.selected_weapon = {"id": "bare"}
        for phase in ("start", "weapon_select", "conversation", "voting", "game_over"):
            with self.subTest(phase=phase):
                self.window.phase = phase
                self.window.weapon_action_panel.show()
                self.window.position_weapon_panel()
                self.assertTrue(self.window.weapon_action_panel.isHidden())

    def test_stale_conversation_callbacks_cannot_resume_another_phase(self):
        self.window.current_conversation = ["Alex: Original"]
        self.window.most_recent_messageS = ["Alex: Original"]
        for phase, game_over in (("voting", False), ("action", False), ("conversation", True)):
            with self.subTest(phase=phase, game_over=game_over), patch.object(
                game.QThread, "start", side_effect=AssertionError("Stale callback launched a worker")
            ), patch.object(self.window, "run_conversation_loop") as resume:
                self.window.phase = phase
                self.window.game_over = game_over
                self.window.run_single_response("Jerry")
                self.window.run_multiple_responses()
                proposal = game.SpeechProposal(None, "Late reply")
                snapshot = (self.window.public_state(), (), ())
                self.window.handle_single_response("Jerry", proposal)
                self.window.handle_multiple_responses({"Jerry": proposal}, snapshot)
                self.window.handle_best_response(proposal, "Jerry")
                resume.assert_not_called()
                self.assertEqual(self.window.current_conversation, ["Alex: Original"])
                self.assertEqual(self.window.most_recent_messageS, ["Alex: Original"])

    def test_restart_rejects_queued_old_results_and_creates_one_new_window(self):
        self.window.player_name_input.setText("Literal input before possession")
        self.window.phase = "conversation"
        self.window.current_conversation = ["Alex: Original"]
        self.window.most_recent_messageS = ["Alex: Original"]
        self.window.NPCs = ["Jerry", "Dave", "Hank"]
        previous_quit_setting = self.app.quitOnLastWindowClosed()
        had_main_window = hasattr(self.app, "_main_window")
        previous_main_window = getattr(self.app, "_main_window", None)
        original_init = game.GameWindow.__init__
        created = []
        delivered = []

        def record_init(window):
            created.append(window)
            original_init(window)

        def deliver(callback, *args):
            delivered.append(callback.__name__)
            callback(*args)

        proposal = game.SpeechProposal(None, "Late reply")
        snapshot = (self.window.public_state(), (), ())
        callbacks = (
            (self.window.handle_single_response, ("Jerry", proposal)),
            (self.window.handle_multiple_responses, ({"Jerry": proposal}, snapshot)),
            (self.window.handle_best_response, (proposal, "Jerry")),
        )
        entered, release = Event(), Event()
        success = Mock()

        def operation():
            entered.set()
            if not release.wait(2):
                raise AssertionError("Old request was not released")
            return "late result"

        try:
            self.window.start_ai_request(operation, success, "Waiting for old request")
            self.wait_for(entered.is_set)
            with patch.object(game.GameWindow, "__init__", new=record_init), patch.object(
                game.QThread, "start"
            ) as start_worker, patch.object(self.window, "run_conversation_loop") as resume:
                for delay, (callback, args) in enumerate(callbacks, start=5):
                    QTimer.singleShot(delay, lambda callback=callback, args=args: deliver(callback, *args))
                self.window.restart_game()
                self.window.restart_game()
                QTimer.singleShot(10, release.set)
                QTest.qWait(80)
                self.wait_for(lambda: not self.window.active_workers)
                self.assertEqual(len(created), 1)
                self.assertIs(self.app._main_window, created[0])
                self.assertTrue(created[0].isVisible())
                self.assertEqual(created[0].phase, "start")
                self.assertEqual(created[0].player_name_input.text(), "Literal input before possession")
                self.assertEqual(len(delivered), 3)
                self.assertEqual(self.window.phase, "restarting")
                self.assertTrue(self.window.game_over)
                self.assertEqual(self.window.current_conversation, ["Alex: Original"])
                self.assertEqual(self.window.most_recent_messageS, ["Alex: Original"])
                start_worker.assert_not_called()
                resume.assert_not_called()
                success.assert_not_called()
        finally:
            release.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()
            self.app.setQuitOnLastWindowClosed(False)
            for window in created:
                window.close()
                window.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            if had_main_window:
                self.app._main_window = previous_main_window
            elif hasattr(self.app, "_main_window"):
                del self.app._main_window
            self.app.setQuitOnLastWindowClosed(previous_quit_setting)

    def test_parallel_private_drafts_commit_only_the_selected_reply(self):
        self.window.phase = "conversation"
        self.window.append_speech("Dave", "Where were you?", "Jerry")
        self.window.add_public_event("The cellar is sealed.")
        for name in ("Jerry", "Dave", "Hank"):
            self.window.remember(name, "experience", name + " private observation")
            self.window.remember(name, "disposition", name + " private feeling")
        barrier = Barrier(3)
        contexts = {}
        selector_entered, release_selector = Event(), Event()

        def speech(context):
            contexts[context.character] = context
            barrier.wait(timeout=2)
            return game.SpeechProposal(None, context.character + " draft")

        def select(state, events, dialogue, proposals):
            selector_entered.set()
            if not release_selector.wait(2):
                raise AssertionError("Selector was not released")
            return "Dave"

        self.speech.side_effect = speech
        self.selector.side_effect = select
        before = tuple(self.window.public_dialogue)
        try:
            with patch.object(self.window, "run_conversation_loop"):
                self.window.run_multiple_responses()
                self.wait_for(selector_entered.is_set)
                self.assertEqual(tuple(self.window.public_dialogue), before)
                self.assertEqual(set(contexts), {"Jerry", "Dave", "Hank"})
                for name, context in contexts.items():
                    self.assertEqual([record.text for record in context.experiences], [name + " private observation"])
                    self.assertEqual([record.text for record in context.dispositions], [name + " private feeling"])
                    self.assertEqual(context.dialogue, before)
                    self.assertEqual(context.dialogue[-1].addressee, "Jerry")
                    self.assertEqual(context.public_events, tuple(self.window.public_events))
                state, events, dialogue, proposals = self.selector.call_args.args
                self.assertEqual(dialogue, before)
                self.assertEqual(dialogue[-1].addressee, "Jerry")
                self.assertEqual(events, tuple(self.window.public_events))
                self.assertEqual(set(proposals), set(contexts))
                release_selector.set()
                self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
                self.assertEqual(self.window.public_dialogue[-1].speaker, "Dave")
                self.assertEqual(self.window.public_dialogue[-1].text, "Dave draft")
                self.assertEqual(len(self.window.public_dialogue), len(before) + 1)
                self.assertFalse(any("Jerry draft" in line or "Hank draft" in line
                                     for line in self.window.current_conversation))
                self.selector.assert_called_once()
        finally:
            release_selector.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()

    def test_speech_and_vote_snapshots_have_identical_private_and_public_evidence(self):
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.selected_weapon = {"id": "bare"}
        self.window.rooms_visited = ["Attic"]
        self.window.had_to_change_rooms = False
        self.window.room_assignments = {
            "Attic": ["Debra", "Jerry"], "Kitchen": ["Dave"], "Library": ["Hank"]
        }
        self.window.characters["Debra"]["alive"] = True
        self.window.new_crime_scenes = []
        self.window.commit_attack("Debra", "Attic")
        self.window.update_witness_memories("Debra")
        self.window.remember("Jerry", "disposition", "I distrust Alex.")
        self.window.append_speech("Alex", "I was alone, Jerry is lying.")
        operations = []
        with patch.object(self.window, "start_ai_request", side_effect=lambda operation, *args, **kwargs: operations.append(operation)):
            self.window.run_single_response("Jerry")
            self.window.run_voting_phase()
        self.assertEqual(len(operations), 2)
        operations[0]()
        operations[1]()
        speech_context = self.speech.call_args.args[0]
        vote_contexts = {call.args[0].character: call.args[0] for call in self.vote.call_args_list}
        self.assertEqual(speech_context, vote_contexts["Jerry"])
        self.assertIn("You witnessed Alex kill Debra", speech_context.experiences[0].text)
        self.assertEqual(speech_context.dispositions[0].text, "I distrust Alex.")
        self.assertEqual(speech_context.dialogue[-1].text, "I was alone, Jerry is lying.")
        self.assertTrue(all(record.kind == "experience" for record in speech_context.experiences))
        self.assertEqual(vote_contexts["Dave"].experiences[0].text, "You (Dave) spent the night alone in the Kitchen.")
        self.assertFalse(any("witnessed" in record.text for record in vote_contexts["Dave"].experiences))

    def test_previous_round_dialogue_and_private_observations_survive_new_round(self):
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.remember("Jerry", "experience", "I saw the first murder.")
        self.window.append_speech("Alex", "A claim from yesterday.")
        self.window.add_public_event("Yesterday's public finding.")
        self.window.round_number = 2
        self.window.new_crime_scenes = []
        self.window.victims_last_night = []
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_conversation_phase()
        context = self.window.build_character_context("Jerry")
        self.assertEqual(context.experiences[0].text, "I saw the first murder.")
        self.assertEqual(context.dialogue[0], game.DialogueLine(1, "Alex", "A claim from yesterday."))
        self.assertEqual(context.public_events[0].text, "Yesterday's public finding.")
        self.assertEqual(context.dialogue[-1].round_number, 2)
        self.assertFalse(any("A claim from yesterday." in line for line in self.window.current_conversation))

    def test_vote_results_remain_public_facts_in_following_round(self):
        self.window.round_number = 1
        self.window.remember("Jerry", "experience", "A private observation")
        original_private = tuple(self.window.characters["Jerry"]["experiences"])
        self.begin_vote()
        self.window.player_vote("NONE")
        for name in ("Jerry", "Dave", "Hank"):
            self.window.collect_ai_vote(name, "NONE")
        results = tuple(self.window.public_events)
        self.assertEqual(sum("voted to skip" in event.text for event in results), 4)
        self.assertTrue(any("no one was voted out" in event.text for event in results))
        self.assertTrue(all(event.kind == "public_event" and event.source == "Voting" for event in results))
        self.window.round_number = 2
        self.window.new_crime_scenes = []
        self.window.victims_last_night = []
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_conversation_phase()
        for name in ("Jerry", "Dave", "Hank"):
            context = self.window.build_character_context(name)
            self.assertEqual(context.public_events[:len(results)], results)
        self.assertEqual(tuple(self.window.characters["Jerry"]["experiences"]), original_private)

    def test_failed_speech_retry_uses_frozen_input_without_inventing_a_reply(self):
        self.window.phase = "conversation"
        self.window.append_speech("Detective", "A question.")
        self.window.remember("Jerry", "experience", "Original observation")
        seen = []

        def speech(context):
            seen.append(context)
            if len(seen) == 1:
                raise game.AIRequestError("Temporary failure. Please retry.")
            return game.SpeechProposal(None, "Recovered reply")

        self.speech.side_effect = speech
        before = tuple(self.window.public_dialogue)
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_single_response("Jerry")
            self.wait_for(lambda: self.window.retry_action is not None and not self.window.active_workers)
            self.assertFalse(self.window.ai_busy)
            self.assertFalse(self.window.retry_button.isHidden())
            self.assertIn("Temporary failure", self.window.secondary_output_area.text())
            self.assertEqual(tuple(self.window.public_dialogue), before)
            self.window.remember("Jerry", "experience", "A later observation")
            self.window.retry_button.click()
            self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
        self.assertIs(seen[0], seen[1])
        self.assertEqual([record.text for record in seen[1].experiences], ["Original observation"])
        self.assertEqual(self.window.public_dialogue[-1].text, "Recovered reply")
        self.assertTrue(self.window.retry_button.isHidden())
        self.assertIsNone(self.window.retry_action)

    def test_failed_vote_batch_keeps_player_ballot_and_retries_atomically(self):
        self.prepare_vote_call()
        failed_once = Event()

        def vote(context):
            if context.character == "Dave" and not failed_once.is_set():
                failed_once.set()
                raise game.AIRequestError("Voting service unavailable. Please retry.")
            return "NONE"

        self.vote.side_effect = vote
        self.window.call_vote()
        self.window.player_vote("NONE")
        self.wait_for(lambda: self.window.retry_action is not None and not self.window.active_workers)
        self.assertEqual(self.window.votes, {"Alex": "NONE"})
        self.assertFalse(self.window.voting_resolved)
        self.window.retry_button.click()
        self.wait_for(lambda: self.window.voting_resolved and not self.window.active_workers)
        self.assertEqual(self.window.votes, {"Alex": "NONE", "Jerry": "NONE", "Dave": "NONE", "Hank": "NONE"})
        self.assertTrue(all(record.kind == "public_event" for record in self.window.public_events))
        self.assertTrue(all(not data["experiences"] for data in self.window.characters.values()))

    def test_same_phase_stale_result_does_not_clear_new_request_or_mutate_history(self):
        self.window.phase = "conversation"
        self.window.new_crime_scenes = []
        self.window.victims_last_night = []
        old_entered, release_old, new_entered, release_new = Event(), Event(), Event(), Event()
        old_success, new_success = Mock(), Mock()

        def operation(entered, release, result):
            entered.set()
            if not release.wait(2):
                raise AssertionError("Request was not released")
            return result

        try:
            self.window.start_ai_request(lambda: operation(old_entered, release_old, "old"), old_success, "Old request")
            old_worker = self.window.active_workers[0]
            self.wait_for(old_entered.is_set)
            with patch.object(self.window, "run_conversation_loop"), patch.object(
                game.random, "choice", return_value="Jerry"
            ):
                self.window.run_conversation_phase()
            self.window.start_ai_request(lambda: operation(new_entered, release_new, "new"), new_success, "New request")
            self.wait_for(new_entered.is_set)
            before = tuple(self.window.public_dialogue)
            release_old.set()
            self.wait_for(lambda: old_worker not in self.window.active_workers)
            self.assertTrue(self.window.ai_busy)
            self.assertEqual(tuple(self.window.public_dialogue), before)
            old_success.assert_not_called()
            new_success.assert_not_called()
            release_new.set()
            self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
            new_success.assert_called_once_with("new")
        finally:
            release_old.set()
            release_new.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()

    def test_camera_equipment_is_visible_at_start_without_revealing_rooms_in_hard_mode(self):
        self.window.show_room_allocations = False
        self.window.player_name_input.setText("Alex")
        camera = game.NPCItemState({"Hank": "camera"})
        with patch.object(game.NPCItemState, "allocate", return_value=camera):
            self.window.start_game()
        self.assertTrue(self.window.npc_equipment_label.isVisible())
        self.assertIn("Hank is carrying a Camera", self.window.npc_equipment_label.text())
        self.assertFalse(any(room in self.window.npc_equipment_label.text() for room in self.window.rooms))
        bare = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        self.window.select_weapon_and_start(bare)
        self.assertTrue(self.window.npc_equipment_label.isHidden())
        text = self.window.output_area.toPlainText()
        self.assertIn("Hank is carrying a Camera", text)
        self.assertNotIn("Room Assignments", text)
        self.assertNotIn("Visible room:", text)
        context = self.window.build_character_context("Jerry")
        self.assertTrue(any(event.source == "Visible equipment" for event in context.public_events))
        self.assertFalse(any(event.source == "Photograph" for event in context.public_events))

    def test_camera_captures_before_attack_and_presents_once_after_owner_dies(self):
        self.window.npc_items = game.NPCItemState({"Hank": "camera"})
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        with patch.object(game.random, "choice", side_effect=lambda choices: choices[0]):
            self.window.run_action_phase()
        photos = self.window.npc_items.capture_night(1, {}, ())
        self.assertEqual(len(photos), 1)
        photo = photos[0]
        self.assertEqual(photo.owner, "Hank")
        self.assertNotIn("Alex", photo.occupants)
        self.assertFalse(any(event.source == "Photograph" for event in self.window.public_events))
        with patch.object(self.window, "run_conversation_loop"):
            self.window.choose_room(photo.room)
        self.assertFalse(self.window.characters["Hank"]["alive"])
        published = [event for event in self.window.public_events if event.source == "Photograph"]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].text, photo.public_description())
        self.assertIn(published[0], self.window.build_character_context("Jerry").public_events)
        self.assertEqual(self.window.output_area.toPlainText().count("genuine photograph"), 1)
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_conversation_phase()
        self.assertEqual(sum(event.source == "Photograph" for event in self.window.public_events), 1)
        self.assertEqual(self.window.output_area.toPlainText().count("genuine photograph"), 1)
        self.window.run_action_phase()
        self.assertEqual(self.window.npc_items.capture_night(2, {}, ()), ())

    def test_possession_does_not_rewrite_photo_or_continue_the_npc_camera(self):
        self.window.npc_items = game.NPCItemState({"Hank": "camera"})
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == "voodoo")
        with patch.object(game.random, "choice", side_effect=lambda choices: choices[0]):
            self.window.run_action_phase()
        photo, = self.window.npc_items.capture_night(1, {}, ())
        original = photo.public_description()
        with patch.object(self.window, "run_conversation_loop"):
            self.window.choose_room(photo.room)
        self.assertEqual(self.window.player_name, "Hank")
        self.assertNotIn("Hank", self.window.characters)
        self.assertEqual(photo.public_description(), original)
        self.assertIn("Visible people: Hank", original)
        self.assertNotIn("Alex", original)
        self.window.run_action_phase()
        self.assertEqual(self.window.npc_items.capture_night(2, {}, ()), ())

    def test_final_morning_still_presents_a_dead_owners_photo(self):
        self.window.round_number = 1
        self.window.npc_items = game.NPCItemState({"Hank": "camera"})
        photo, = self.window.npc_items.capture_night(1, {"Kitchen": ["Hank"]}, ("Hank",))
        self.window.characters["Hank"]["alive"] = False
        self.window.characters["Dave"]["alive"] = False
        self.window.run_conversation_phase()
        self.assertTrue(self.window.game_over)
        self.assertEqual(sum(event.source == "Photograph" for event in self.window.public_events), 1)
        self.assertIn(photo.public_description(), self.window.output_area.toPlainText())

    def test_close_retains_running_worker_until_safe_release_and_ignores_its_result(self):
        self.window.phase = "conversation"
        entered, release = Event(), Event()
        success = Mock()
        previous_quit = self.app.quitOnLastWindowClosed()
        previous_idle_flag = getattr(self.app, "_quit_when_idle", False)

        def operation():
            entered.set()
            if not release.wait(2):
                raise AssertionError("Request was not released")
            return "late result"

        try:
            with patch.object(self.app, "quit") as quit_app:
                self.window.start_ai_request(operation, success, "Waiting")
                self.wait_for(entered.is_set)
                worker = self.window.active_workers[0]
                self.app.setQuitOnLastWindowClosed(True)
                self.window.close()
                self.assertEqual(self.window.phase, "closed")
                self.assertIn(worker, self.app._ai_workers)
                quit_app.assert_not_called()
                release.set()
                self.wait_for(lambda: not self.window.active_workers)
                success.assert_not_called()
                self.assertNotIn(worker, self.app._ai_workers)
                quit_app.assert_called_once()
        finally:
            release.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()
            self.app._quit_when_idle = previous_idle_flag
            self.app.setQuitOnLastWindowClosed(previous_quit)


if __name__ == "__main__":
    unittest.main()
