"""Offline dictation controls and retained QThreads with fake microphone input."""

import contextlib
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

from PyQt5.QtCore import QObject, QCoreApplication, QEvent, pyqtSignal
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QTextEdit

import ai_runtime
import main as game
from dev_trace import TraceRun


class FakeRecorder(QObject):
    started = pyqtSignal(int)
    ready = pyqtSignal(int, bytes)
    failed = pyqtSignal(int, str)
    cancelled = pyqtSignal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.generation = 0
        self.is_recording = False
        self.closed = False
        self.start_error = None
        self.clip = b"private audio sentinel"

    def is_current(self, generation):
        return not self.closed and generation == self.generation

    def start(self):
        self.generation += 1
        if self.start_error:
            self.failed.emit(self.generation, self.start_error)
        else:
            self.is_recording = True
            self.started.emit(self.generation)
        return self.generation

    def stop(self):
        self.is_recording = False
        self.ready.emit(self.generation, self.clip)

    def cancel(self):
        previous = self.generation
        self.generation += 1
        self.is_recording = False
        self.cancelled.emit(previous)

    def close(self):
        self.cancel()
        self.closed = True


class DictationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["dictation-integration"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32" and cls.app.platformName() == "offscreen":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        self.patches = contextlib.ExitStack()
        self.directory = tempfile.TemporaryDirectory()
        self.patches.enter_context(patch.dict(os.environ, {
            "ALIBAI_DEV_MODE": "0", "ALIBAI_PROFILE_PATH": str(Path(self.directory.name) / "profile.json"),
        }))
        self.patches.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.patches.enter_context(patch("dictation.QAudioInput", side_effect=AssertionError("Real microphone access")))
        self.patches.enter_context(patch.object(ai_runtime, "OpenAI", side_effect=AssertionError("Live API client")))
        self.patches.enter_context(patch.object(game, "QMediaPlayer"))
        self.patches.enter_context(patch.object(game, "DictationRecorder", FakeRecorder))
        self.transcribe = self.patches.enter_context(patch.object(game.AIRuntime, "transcribe", return_value="Spoken words."))
        self.windows = []
        self.releases = []
        self.window = self.make_window()

    def tearDown(self):
        for release in self.releases:
            release.set()
        for window in self.windows:
            self.wait_for(lambda: not window.active_workers)
            window.close()
        self.app.processEvents()
        for window in self.windows:
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app._quit_when_idle = False
        self.app.setQuitOnLastWindowClosed(False)
        self.patches.close()
        self.directory.cleanup()

    def make_window(self):
        window = game.GameWindow()
        self.windows.append(window)
        self.app.processEvents()
        window.player_name = "Alex"
        window.characters = {name: {"alive": True, "profile": [], "experiences": [], "dispositions": []}
                             for name in ("Dave", "Hank", "Jerry")}
        window.new_crime_scenes = []
        window.rooms_visited = []
        window.victims_last_night = []
        window.run_multiple_responses = Mock()
        return window

    def wait_for(self, condition):
        deadline = monotonic() + 3
        while not condition() and monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(condition(), "Queued dictation operation did not finish")

    def open_input(self, window=None):
        window = window or self.window
        window.phase = "conversation"
        window.round_number = 1
        window.append_speech("Dave", "Where were you?", "Alex")
        window.run_conversation_loop()
        self.app.processEvents()
        self.assertTrue(window.dictation_controls.isVisible())

    def pending_transcription(self, window=None, *, error=None):
        window = window or self.window
        entered, release = Event(), Event()
        self.releases.append(release)

        def pending(_clip):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test did not release transcription")
            if error:
                raise error
            return "Late spoken words."

        self.transcribe.side_effect = pending
        window.dictation_button.click()
        self.assertTrue(window.dictation_recorder.is_recording)
        window.dictation_button.click()
        self.wait_for(entered.is_set)
        return release

    def test_record_stop_appends_editable_draft_without_sending(self):
        self.open_input()
        self.window.interject_input.setText("Typed first.")
        before = tuple(self.window.public_dialogue)
        self.window.dictation_button.click()
        self.assertEqual(self.window.dictation_button.text(), "Stop recording")
        self.assertTrue(self.window.dictation_cancel_button.isVisible())
        self.window.dictation_button.click()
        self.wait_for(lambda: self.window.dictation_worker is None)
        self.assertEqual(self.window.interject_input.text(), "Typed first. Spoken words.")
        self.assertTrue(self.window.interject_input.isEnabled())
        self.assertTrue(self.window.say_button.isEnabled())
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.window.run_multiple_responses.assert_not_called()
        self.transcribe.assert_called_once_with(self.window.dictation_recorder.clip)
        self.window.interject_input.setText("Edited before sending.")
        self.window.say_button.click()
        self.assertEqual(self.window.public_dialogue[-1].text, "Edited before sending.")

    def test_pending_transcription_keeps_current_edits_and_gameplay_available(self):
        self.open_input()
        self.window.interject_input.setText("Original draft.")
        release = self.pending_transcription()
        self.assertFalse(self.window.ai_busy)
        self.assertFalse(self.window.dictation_button.isEnabled())
        self.assertTrue(self.window.interject_input.isEnabled())
        self.assertTrue(self.window.say_button.isEnabled())
        self.assertTrue(self.window.pass_button.isEnabled())
        self.window.interject_input.setText("Rewritten while waiting. ")
        self.window.interject_input.selectAll()
        release.set()
        self.wait_for(lambda: self.window.dictation_worker is None)
        self.assertEqual(self.window.interject_input.text(), "Rewritten while waiting. Late spoken words.")
        self.assertEqual(len(self.window.public_dialogue), 1)

    def test_cancel_capture_keeps_typed_draft_and_rejects_late_clip(self):
        self.open_input()
        self.window.interject_input.setText("Keep this.")
        self.window.dictation_button.click()
        generation = self.window.dictation_recorder.generation
        self.window.dictation_cancel_button.click()
        self.window.dictation_recorder.ready.emit(generation, b"old clip")
        self.assertFalse(self.window.dictation_recorder.is_recording)
        self.assertEqual(self.window.interject_input.text(), "Keep this.")
        self.assertTrue(self.window.dictation_button.isEnabled())
        self.transcribe.assert_not_called()

    def test_cancel_request_invalidates_result_but_retains_worker_until_finished(self):
        self.open_input()
        release = self.pending_transcription()
        worker = self.window.dictation_worker
        self.window.dictation_cancel_button.click()
        self.assertIn(worker, self.app._ai_workers)
        self.assertIn(worker, self.window.active_workers)
        self.assertFalse(self.window.dictation_button.isEnabled())
        self.window.interject_input.setText("Keep typing.")
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "Keep typing.")
        self.assertTrue(self.window.dictation_button.isEnabled())
        self.assertNotIn(worker, self.app._ai_workers)

    def test_state_transitions_invalidate_pending_results(self):
        for action in ("pass", "send", "vote", "voting", "night", "conversation", "game_over", "hide", "disable"):
            with self.subTest(action=action):
                window = self.make_window()
                self.open_input(window)
                release = self.pending_transcription(window)
                window.interject_input.setText("Typed words only.")
                with patch.object(window, "start_ai_request"):
                    if action == "pass":
                        window.pass_conversation()
                    elif action == "send":
                        window.send_interjection()
                    elif action == "vote":
                        window.current_conversation = ["Discussion"] * 20
                        window.call_vote()
                    elif action == "voting":
                        window.run_voting_phase()
                    elif action == "night":
                        window.run_action_phase()
                    elif action == "conversation":
                        window.run_conversation_phase()
                    elif action == "game_over":
                        window.end_game_screen(2)
                    elif action == "hide":
                        window.interject_input.hide()
                    else:
                        window.interject_input.setEnabled(False)
                self.assertIsNone(window.dictation_context)
                release.set()
                self.wait_for(lambda: not window.active_workers)
                self.assertNotIn("Late spoken words.", window.interject_input.text())
                self.assertTrue(all("Late spoken words." not in line.text for line in window.public_dialogue))
                if action == "send":
                    self.assertEqual(window.public_dialogue[-1].text, "Typed words only.")

    def test_input_session_change_ignores_old_failure_after_reopening(self):
        self.open_input()
        release = self.pending_transcription(error=game.AIRequestError("Old failure"))
        self.window.pass_conversation()
        self.window.interject_conversation()
        self.window.interject_input.setText("New input session")
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "New input session")
        self.assertNotIn("Old failure", self.window.dictation_status.text())

    def test_new_round_guard_rejects_callback_even_without_phase_change(self):
        self.open_input()
        release = self.pending_transcription()
        self.window.round_number += 1
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "")

    def test_close_before_transcript_keeps_worker_alive_and_ignores_result(self):
        self.open_input()
        release = self.pending_transcription()
        worker = self.window.dictation_worker
        self.window.interject_input.setText("Draft before close")
        self.window.close()
        self.assertTrue(self.window.dictation_recorder.closed)
        self.assertIn(worker, self.app._ai_workers)
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "Draft before close")
        self.assertEqual(len(self.window.public_dialogue), 1)

    def test_restart_invalidates_old_request_and_new_window_has_clean_input(self):
        self.open_input()
        release = self.pending_transcription()
        self.window.interject_input.setText("Old window draft")
        self.window.restart_game()
        self.wait_for(lambda: getattr(self.app, "_main_window", self.window) is not self.window)
        replacement = self.app._main_window
        self.windows.append(replacement)
        self.wait_for(lambda: self.window.phase == "restarting" and not self.window.isVisible())
        release.set()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "Old window draft")
        self.assertEqual(replacement.interject_input.text(), "")
        self.assertIsNone(replacement.dictation_context)

    def test_missing_microphone_and_api_failure_preserve_draft(self):
        self.open_input()
        self.window.interject_input.setText("Keep my draft")
        self.window.dictation_recorder.start_error = "No microphone is available."
        self.window.dictation_button.click()
        self.assertIn("No microphone", self.window.dictation_status.text())
        self.assertTrue(self.window.dictation_button.isEnabled())
        self.transcribe.assert_not_called()
        self.window.dictation_recorder.start_error = None
        self.transcribe.side_effect = game.AIRequestError("The API key was rejected.")
        self.window.dictation_button.click()
        self.window.dictation_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "Keep my draft")
        self.assertIn("key was rejected", self.window.dictation_status.text())
        self.assertTrue(self.window.dictation_button.isEnabled())

    def test_unexpected_worker_failure_is_sanitized(self):
        self.open_input()
        self.transcribe.side_effect = RuntimeError("private provider details")
        self.window.dictation_button.click()
        self.window.dictation_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertNotIn("private", self.window.dictation_status.text())
        self.assertIn("could not be completed", self.window.dictation_status.text())

    def test_enabled_trace_contains_neither_raw_clip_nor_unsubmitted_transcript(self):
        self.open_input()
        trace = TraceRun(enabled=True, root=Path(self.directory.name) / "traces")
        trace.start({})
        self.window.trace = trace
        self.window.ai_runtime.trace = trace
        self.transcribe.return_value = "private draft sentinel"
        self.window.dictation_button.click()
        self.window.dictation_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.window.trace_state("input_waiting")
        data = trace.run_dir.joinpath("events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("private draft sentinel", data)
        self.assertNotIn("private audio sentinel", data)
        self.assertEqual({path.name for path in trace.run_dir.iterdir()}, {"manifest.json", "events.jsonl"})
        self.assertEqual(len(self.window.public_dialogue), 1)

    def test_controls_keep_readable_resolved_font_and_bottom_three_buttons(self):
        self.open_input()
        for widget in (self.window.dictation_button, self.window.dictation_cancel_button, self.window.dictation_status):
            self.assertNotEqual(QFontInfo(widget.font()).family(), self.window.pixel_font_family)
            self.assertEqual(widget.font().pixelSize(), 18)
        layout = self.window.conversation_buttons_layout
        self.assertEqual([layout.itemAt(i).widget() for i in range(layout.count())],
                         [self.window.pass_button, self.window.interject_button, self.window.vote_button])
        self.window.show_how_to_play()
        help_window = self.window.how_to_play_window
        self.addCleanup(help_window.close)
        self.assertIn("Nothing is spoken until you choose Say", help_window.findChild(QTextEdit).toPlainText())

    def test_input_length_limit_preserves_existing_draft_without_partial_insertion(self):
        self.open_input()
        self.window.interject_input.setMaxLength(12)
        self.window.interject_input.setText("Full draft")
        self.window.dictation_button.click()
        self.window.dictation_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "Full draft")
        self.assertIn("will not fit", self.window.dictation_status.text())

    def test_unicode_draft_appends_at_actual_end_and_respects_qt_character_limit(self):
        self.open_input()
        self.window.interject_input.setText("\U0001f642 draft")
        self.window.dictation_button.click()
        self.window.dictation_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "\U0001f642 draft Spoken words.")
        self.window.interject_input.setMaxLength(5)
        self.window.interject_input.setText("\U0001f642")
        self.transcribe.return_value = "\U0001f642a"
        self.window.dictation_button.click()
        self.window.dictation_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "\U0001f642")
        self.assertIn("will not fit", self.window.dictation_status.text())

    def test_automatic_capture_completion_uses_same_manual_draft_flow(self):
        self.open_input()
        original_start = self.window.dictation_recorder.start

        def complete_immediately():
            generation = original_start()
            self.window.dictation_recorder.stop()
            return generation

        self.window.dictation_recorder.start = complete_immediately
        self.window.dictation_button.click()
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual(self.window.interject_input.text(), "Spoken words.")
        self.assertEqual(len(self.window.public_dialogue), 1)


if __name__ == "__main__":
    unittest.main()
