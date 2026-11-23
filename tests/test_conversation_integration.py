"""Offline Qt targeting and speculative turn lifecycle with controlled fake requests."""

import contextlib
from dataclasses import replace
import os
from pathlib import Path
import socket
import sys
import tempfile
from threading import Event
from time import monotonic
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import ai_runtime
import main as game
from ai_context import MemoryRecord, render_character_context
from game_rules import MUFFLED_SPEECH


class ConversationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["conversation-integration"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = contextlib.ExitStack()
        self.patches.enter_context(patch.dict(os.environ, {
            "ALIBAI_DEV_MODE": "0", "ALIBAI_PROFILE_PATH": str(Path(self.directory.name) / "profile.json"),
            "ALIBAI_SETTINGS_PATH": str(Path(self.directory.name) / "preferences.ini"),
        }))
        self.patches.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.patches.enter_context(patch.object(ai_runtime, "OpenAI", side_effect=AssertionError("Live API client")))
        self.patches.enter_context(patch("dictation.QAudioInput", side_effect=AssertionError("Real microphone")))
        self.patches.enter_context(patch.object(game, "QMediaPlayer"))
        self.speech = self.patches.enter_context(patch.object(game.AIRuntime, "speech", side_effect=self.reply))
        self.selector = self.patches.enter_context(patch.object(game.AIRuntime, "select_reply",
            side_effect=lambda state, events, dialogue, proposals: next(iter(proposals))))
        self.window = game.GameWindow()
        self.windows = [self.window]
        self.releases = []
        self.preparation_paused = True
        self.original_prepare = self.window.maybe_prepare_next_turn
        self.patches.enter_context(patch.object(self.window, "maybe_prepare_next_turn",
            side_effect=lambda: None if self.preparation_paused else self.original_prepare()))
        self.window.player_name = "Alex"
        self.window.characters = {name: {"alive": True, "profile": [], "experiences": [], "dispositions": []}
                                  for name in ("Dave", "Hank", "Jerry")}
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.menu_widget.hide()
        self.window.output_area.show()
        self.window.conversation_buttons_widget.show()
        self.window.new_crime_scenes = []
        self.window.victims_last_night = []
        self.window.rooms_visited = []
        self.window.refresh_recipient_choices()
        self.app.processEvents()

    def tearDown(self):
        self.preparation_paused = True
        for window in self.windows:
            window.invalidate_prepared_turn("test_cleanup")
        for release in self.releases:
            release.set()
        for window in self.windows:
            self.wait_for(lambda: not window.active_workers)
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app._quit_when_idle = False
        self.app.setQuitOnLastWindowClosed(False)
        self.patches.close()
        self.directory.cleanup()

    @staticmethod
    def reply(context):
        return game.SpeechProposal(None, context.character + " draft")

    def wait_for(self, condition):
        deadline = monotonic() + 5
        while not condition() and monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(condition(), "Queued conversation operation did not finish")

    def free_floor(self):
        self.window.append_speech("Dave", "My account.")
        self.window.run_conversation_loop()

    def prepare(self):
        self.preparation_paused = False
        self.window.maybe_prepare_next_turn()

    def cached(self):
        self.wait_for(lambda: self.window.turn_preparation.ready is not None
                      and self.window.turn_preparation.active is None)

    def target(self, name):
        self.window.refresh_recipient_choices()
        self.window.recipient_selector.setCurrentIndex(
            self.window.recipient_selector.findData(self.window.body_id(name)))

    def send_direct(self, name):
        if self.window.interject_input.isHidden():
            self.window.interject_conversation()
        self.target(name)
        self.window.interject_input.setText("Your account, please.")
        self.window.send_interjection()

    def block_proposals(self):
        entered, release = Event(), Event()
        self.releases.append(release)
        seen = []

        def reply(context):
            seen.append(context)
            if len(seen) >= 3:
                entered.set()
            if not release.wait(4):
                raise game.AIRequestError("Test request was not released")
            return self.reply(context)

        self.speech.side_effect = reply
        self.prepare()
        self.wait_for(entered.is_set)
        return release, seen

    def test_direct_message_has_one_target_request_without_batch_or_selector(self):
        self.free_floor()
        self.send_direct("Hank")
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual([call.args[0].character for call in self.speech.call_args_list], ["Hank"])
        self.selector.assert_not_called()
        self.assertEqual(self.window.public_dialogue[-2].addressee, "Hank")
        self.assertEqual(self.window.public_dialogue[-1].speaker, "Hank")
        self.assertEqual(self.window.direct_replies.count_for(self.window.body_id("Hank")), 1)
        self.assertIsNone(self.window.recipient_selector.currentData())
        self.assertTrue(self.window.pass_button.isEnabled())

    def test_direct_reply_to_another_npc_returns_to_shared_floor(self):
        self.free_floor()
        self.speech.side_effect = lambda _context: game.SpeechProposal("Jerry", "Jerry, what did you hear?")
        self.send_direct("Hank")
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.speech.call_count, 1)
        self.assertEqual(self.window.public_dialogue[-1].addressee, "Jerry")
        self.assertTrue(self.window.pass_button.isEnabled())
        self.selector.assert_not_called()

    def test_clicking_speaker_selects_live_body_and_preserves_existing_draft(self):
        self.free_floor()
        cursor = self.window.output_area.document().find("Dave")
        cursor.setPosition(cursor.selectionStart() + 1)
        point = self.window.output_area.cursorRect(cursor).center()
        self.assertTrue(self.window.output_area.anchorAt(point).startswith("npc:"))
        QTest.mouseClick(self.window.output_area.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(self.window.recipient_selector.currentData(), self.window.body_id("Dave"))
        self.assertTrue(self.window.interject_input.isVisible())
        self.window.interject_input.setText("My draft")
        self.window.choose_speaker_recipient(self.window.body_id("Hank"))
        self.assertEqual(self.window.interject_input.text(), "My draft")
        self.window.characters["Hank"]["alive"] = False
        self.window.choose_speaker_recipient(self.window.body_id("Hank"))
        self.assertEqual(self.window.interject_input.text(), "My draft")

    def test_three_direct_replies_cap_without_selection_or_forgery_reset(self):
        self.free_floor()
        for _ in range(3):
            self.send_direct("Hank")
            self.wait_for(lambda: not self.window.active_workers)
        hank = self.window.body_id("Hank")
        self.window.interject_conversation()
        self.target("Dave")
        self.target("Hank")
        self.assertEqual(self.window.direct_replies.count_for(hank), 3)
        self.assertFalse(self.window.say_button.isEnabled())
        self.assertIn("3/3", self.window.recipient_hint.text())
        before = tuple(self.window.public_dialogue)
        self.window.interject_input.setText("Another question")
        self.window.send_interjection()
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.assertEqual(self.speech.call_count, 3)
        self.window.append_speech("Dave", "Someone's apparent statement.", origin="forged")
        self.assertEqual(self.window.direct_replies.count_for(hank), 3)
        xml = render_character_context(self.window.build_character_context("Dave"))
        self.assertNotIn('origin="forged"', xml)
        self.window.append_speech("Detective", "A public announcement.")
        self.assertEqual(self.window.direct_replies.count_for(hank), 3)
        self.window.append_speech("Dave", "An ordinary contribution.")
        self.assertEqual(self.window.direct_replies.count_for(hank), 0)

    def test_group_remains_available_to_answer_after_direct_cap(self):
        self.free_floor()
        for _ in range(3):
            self.window.append_speech("Hank", "My answer.", direct_reply=True)
        self.window.append_speech("Hank", "And where were you?", "Alex")
        self.window.run_conversation_loop()
        self.target("Hank")
        self.assertFalse(self.window.say_button.isEnabled())
        self.window.recipient_selector.setCurrentIndex(0)
        self.assertTrue(self.window.say_button.isEnabled())
        self.window.interject_input.setText("I was upstairs.")
        with patch.object(self.window, "run_multiple_responses") as group:
            self.window.send_interjection()
        group.assert_called_once()
        self.assertFalse(self.window.player_reply_required())
        self.assertIsNone(self.window.public_dialogue[-1].addressee)

    def test_direct_retry_counts_once_and_keeps_original_target(self):
        self.free_floor()
        self.speech.side_effect = [game.AIRequestError("Temporary failure"), game.SpeechProposal(None, "Recovered")]
        self.send_direct("Hank")
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.direct_replies.count_for(self.window.body_id("Hank")), 0)
        self.target("Dave")
        self.window.retry_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual([call.args[0].character for call in self.speech.call_args_list], ["Hank", "Hank"])
        self.assertEqual(self.window.direct_replies.count_for(self.window.body_id("Hank")), 1)
        self.assertEqual(self.window.public_dialogue[-1].speaker, "Hank")

    def test_new_discussion_resets_direct_counter(self):
        self.free_floor()
        self.window.append_speech("Hank", "My answer.", direct_reply=True)
        self.window.round_number = 2
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_conversation_phase()
        self.assertEqual(self.window.direct_replies.count_for(self.window.body_id("Hank")), 0)

    def test_preparation_has_all_candidates_and_no_public_commit_before_pass(self):
        self.free_floor()
        before = tuple(self.window.public_dialogue)
        self.prepare()
        self.cached()
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.assertFalse(self.window.ai_busy)
        self.assertTrue(self.window.pass_button.isEnabled())
        self.assertEqual({call.args[0].character for call in self.speech.call_args_list}, {"Dave", "Hank", "Jerry"})
        self.assertEqual(self.selector.call_count, 1)
        self.preparation_paused = True
        self.window.pass_conversation()
        self.assertEqual(len(self.window.public_dialogue), len(before) + 1)
        self.assertEqual(self.speech.call_count, 3)
        self.assertEqual(self.selector.call_count, 1)
        self.assertIsNone(self.window.turn_preparation.ready)

    def test_pass_joins_inflight_pipeline_and_finished_worker_prepares_following_turn(self):
        self.free_floor()
        release, _ = self.block_proposals()
        first = self.window.turn_preparation.active.identifier
        self.window.pass_conversation()
        self.window.pass_conversation()
        self.assertTrue(self.window.ai_busy)
        self.assertEqual(self.window.awaiting_preparation, first)
        self.assertEqual(self.speech.call_count, 3)
        release.set()
        self.wait_for(lambda: len(self.window.public_dialogue) == 2)
        self.cached()
        self.assertGreater(self.window.turn_preparation.ready.ticket_id, first)
        self.assertEqual(len(self.window.public_dialogue), 2)
        self.assertEqual(self.speech.call_count, 6)
        self.assertEqual(self.selector.call_count, 2)

    def test_interjection_invalidates_old_candidates_but_direct_reply_does_not_wait(self):
        self.free_floor()
        entered, release = Event(), Event()
        self.releases.append(release)
        old_seen = []

        def reply(context):
            if context.dialogue[-1].speaker == "Alex":
                return self.reply(context)
            old_seen.append(context)
            if len(old_seen) == 3:
                entered.set()
            if not release.wait(4):
                raise game.AIRequestError("Not released")
            return self.reply(context)

        self.speech.side_effect = reply
        self.prepare()
        self.wait_for(entered.is_set)
        old = self.window.turn_preparation.active
        self.preparation_paused = True
        self.send_direct("Hank")
        self.wait_for(lambda: self.window.public_dialogue[-1].speaker == "Hank")
        self.assertTrue(old.cancelled.is_set())
        self.assertIs(self.window.turn_preparation.active, old)
        self.assertEqual(self.speech.call_count, 4)
        self.assertFalse(release.is_set())
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.selector.assert_not_called()
        self.assertIsNone(self.window.turn_preparation.ready)

    def test_repeated_invalidations_do_not_accumulate_speculative_batches(self):
        self.free_floor()
        release, _ = self.block_proposals()
        for number in range(5):
            self.window.remember("Hank", "experience", f"Observation {number}")
            self.window.maybe_prepare_next_turn()
        self.assertEqual(self.speech.call_count, 3)
        release.set()
        self.cached()
        self.assertEqual(self.speech.call_count, 6)
        self.assertEqual(self.selector.call_count, 1)
        hank = next(context for context in self.window.turn_preparation.ready.snapshot.contexts if context.character == "Hank")
        self.assertEqual(len(hank.experiences), 5)

    def test_snapshot_checks_reject_unannounced_memory_effect_and_identity_changes(self):
        for change in ("memory", "effect", "identity"):
            with self.subTest(change=change):
                self.free_floor()
                self.speech.reset_mock()
                self.selector.reset_mock()
                release, _ = self.block_proposals()
                self.preparation_paused = True
                if change == "memory":
                    self.window.characters["Hank"]["experiences"].append(MemoryRecord(1, "experience", "A new fact"))
                elif change == "effect":
                    self.window.inventory.grant("muffle")
                else:
                    self.window.body_ids["Hank"] += "new"
                release.set()
                self.wait_for(lambda: not self.window.active_workers)
                self.selector.assert_not_called()
                self.assertIsNone(self.window.turn_preparation.ready)

    def test_changed_cached_context_falls_back_without_publishing_old_reply(self):
        self.free_floor()
        self.prepare()
        self.cached()
        self.preparation_paused = True
        self.window.characters["Hank"]["experiences"].append(MemoryRecord(1, "experience", "New fact"))
        before = tuple(self.window.public_dialogue)
        with patch.object(self.window, "run_multiple_responses") as group:
            self.window.pass_conversation()
        group.assert_called_once()
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.assertIsNone(self.window.turn_preparation.ready)

    def test_required_reply_and_composing_input_never_prepare(self):
        self.window.append_speech("Hank", "Where were you?", "Alex")
        self.window.run_conversation_loop()
        self.prepare()
        self.speech.assert_not_called()
        self.window.append_speech("Hank", "My account.")
        self.preparation_paused = True
        self.window.run_conversation_loop()
        self.window.interject_conversation()
        self.prepare()
        self.speech.assert_not_called()
        self.assertIsNone(self.window.turn_preparation.active)

    def test_background_failure_is_silent_until_pass_and_retries_frozen_context(self):
        self.free_floor()
        self.speech.side_effect = game.AIRequestError("Temporarily unavailable")
        self.prepare()
        self.wait_for(lambda: self.window.turn_preparation.failure is not None
                      and self.window.turn_preparation.active is None)
        original = self.window.turn_preparation.failure.snapshot
        self.assertEqual(self.window.secondary_output_area.text(), "")
        self.assertTrue(self.window.retry_button.isHidden())
        before = self.speech.call_count
        self.preparation_paused = True
        self.window.pass_conversation()
        self.assertIn("unavailable", self.window.secondary_output_area.text())
        self.assertEqual(self.speech.call_count, before)
        self.assertTrue(self.window.retry_button.isVisible())
        self.speech.side_effect = self.reply
        self.window.retry_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(len(self.window.public_dialogue), 2)
        retried = self.speech.call_args_list[-3:]
        self.assertEqual({call.args[0] for call in retried}, set(original.contexts))

    def test_context_change_invalidates_prepared_failure_retry(self):
        self.free_floor()
        self.speech.side_effect = game.AIRequestError("Temporarily unavailable")
        self.prepare()
        self.wait_for(lambda: self.window.turn_preparation.failure is not None
                      and self.window.turn_preparation.active is None)
        self.window.pass_conversation()
        retry = self.window.retry_action
        before = self.speech.call_count
        self.window.remember("Hank", "experience", "New fact")
        self.preparation_paused = True
        retry()
        self.assertEqual(self.speech.call_count, before)
        self.assertIsNone(self.window.retry_action)

    def test_awaited_preparation_failure_restores_controls_without_public_reply(self):
        self.free_floor()
        entered, release = Event(), Event()
        self.releases.append(release)

        def unavailable(_context):
            entered.set()
            release.wait(4)
            raise game.AIRequestError("Conversation unavailable")

        self.speech.side_effect = unavailable
        self.prepare()
        self.wait_for(entered.is_set)
        self.window.pass_conversation()
        self.preparation_paused = True
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertFalse(self.window.ai_busy)
        self.assertTrue(self.window.pass_button.isEnabled())
        self.assertTrue(self.window.retry_button.isVisible())
        self.assertIn("unavailable", self.window.secondary_output_area.text())
        self.assertEqual(len(self.window.public_dialogue), 1)
        self.selector.assert_not_called()

    def test_context_change_during_guarded_retry_cannot_publish_stale_reply(self):
        self.free_floor()
        self.speech.side_effect = game.AIRequestError("Temporarily unavailable")
        self.prepare()
        self.wait_for(lambda: self.window.turn_preparation.failure is not None
                      and self.window.turn_preparation.active is None)
        self.window.pass_conversation()
        entered, release = Event(), Event()
        self.releases.append(release)

        def reply(context):
            entered.set()
            release.wait(4)
            return self.reply(context)

        self.speech.side_effect = reply
        self.preparation_paused = True
        self.window.retry_button.click()
        self.wait_for(entered.is_set)
        self.window.remember("Hank", "experience", "A new observation")
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertFalse(self.window.ai_busy)
        self.assertTrue(self.window.pass_button.isEnabled())
        self.assertEqual(len(self.window.public_dialogue), 1)
        self.selector.assert_not_called()

    def test_invalidated_inflight_selector_never_enters_public_history(self):
        self.free_floor()
        entered, release = Event(), Event()
        self.releases.append(release)

        def select(*_args):
            entered.set()
            release.wait(4)
            return "Dave"

        self.selector.side_effect = select
        self.prepare()
        self.wait_for(entered.is_set)
        self.preparation_paused = True
        self.window.interject_conversation()
        self.window.interject_input.setText("My new account")
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(len(self.window.public_dialogue), 1)
        self.assertEqual(self.window.interject_input.text(), "My new account")
        self.assertIsNone(self.window.turn_preparation.ready)

    def test_item_use_invalidates_cache_and_muffle_is_applied_to_next_reply(self):
        self.free_floor()
        self.prepare()
        self.cached()
        self.preparation_paused = True
        entry = self.window.inventory.grant("muffle")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertIsNone(self.window.turn_preparation.ready)
        self.selector.side_effect = lambda *_args: "Dave"
        self.window.pass_conversation()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.public_dialogue[-1].text, MUFFLED_SPEECH)

    def test_close_retains_old_preparation_until_safe_finish_without_public_result(self):
        self.free_floor()
        release, _ = self.block_proposals()
        workers = tuple(self.window.active_workers)
        before = tuple(self.window.public_dialogue)
        self.window.close()
        self.assertTrue(all(worker in self.app._ai_workers for worker in workers))
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.selector.assert_not_called()

    def test_night_phase_change_discards_pending_preparation(self):
        self.free_floor()
        release, _ = self.block_proposals()
        before = tuple(self.window.public_dialogue)
        self.window.run_action_phase()
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.phase, "action")
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.assertIsNone(self.window.turn_preparation.ready)
        self.selector.assert_not_called()

    def test_restart_keeps_old_preparation_out_of_new_window(self):
        self.free_floor()
        release, _ = self.block_proposals()
        before = tuple(self.window.public_dialogue)
        original_init = game.GameWindow.__init__
        previous = getattr(self.app, "_main_window", None)
        created = []

        def record_init(window):
            original_init(window)
            created.append(window)
            self.windows.append(window)

        try:
            with patch.object(game.GameWindow, "__init__", new=record_init):
                self.window.restart_game()
                self.wait_for(lambda: bool(created))
            self.assertEqual(created[0].phase, "start")
            release.set()
            self.wait_for(lambda: not self.window.active_workers)
            self.assertEqual(tuple(self.window.public_dialogue), before)
            self.assertEqual(created[0].public_dialogue, [])
            self.selector.assert_not_called()
        finally:
            if previous is not None:
                self.app._main_window = previous
            elif hasattr(self.app, "_main_window"):
                del self.app._main_window


if __name__ == "__main__":
    unittest.main()
