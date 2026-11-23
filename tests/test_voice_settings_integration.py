"""Owner-local voice bindings and settings navigation through actual game controls."""

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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, QSize, Qt
from PyQt5.QtGui import QFont, QPixmap, QRegion
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QPushButton

import ai_runtime
import main as game
from input_bindings import BindingSettingsPage, InputBinding
from model_config_ui import ModelConfigPanel
from tests.test_dictation_integration import FakeRecorder


class VoiceSettingsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["voice-settings-integration"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            "ALIBAI_SETTINGS_PATH": str(Path(self.directory.name) / "settings.ini"),
            "ALIBAI_PROFILE_PATH": str(Path(self.directory.name) / "profile.json"),
            "ALIBAI_DEV_MODE": "0",
        }))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.stack.enter_context(patch("dictation.QAudioInput", side_effect=AssertionError("No physical microphone")))
        self.stack.enter_context(patch.object(ai_runtime, "OpenAI", side_effect=AssertionError("No API client")))
        self.stack.enter_context(patch.object(game, "QMediaPlayer"))
        self.stack.enter_context(patch.object(game, "DictationRecorder", FakeRecorder))
        self.stack.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        self.transcribe = self.stack.enter_context(patch.object(game.AIRuntime, "transcribe", return_value="Dictated words."))
        self.windows = []
        self.window = self.make_window()
        self.app._main_window = self.window

    def make_window(self):
        window = game.GameWindow()
        self.windows.append(window)
        window.showNormal()
        window.resize(1000, 700)
        window.activateWindow()
        self.app.processEvents()
        return window

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
        self.app._quit_when_idle = False
        self.app.setQuitOnLastWindowClosed(False)
        self.stack.close()
        self.directory.cleanup()

    def wait_for(self, condition):
        deadline = monotonic() + 3
        while not condition() and monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(condition(), "Queued operation did not finish")

    def button(self, name):
        button = self.window.overlay_host.current_page.findChild(QPushButton, name)
        self.assertIsNotNone(button)
        return button

    def open_controls(self):
        self.window.open_escape_menu()
        self.assertEqual(self.window.overlay_host.current_title, "Menu")
        QTest.mouseClick(self.button("pauseControls"), Qt.LeftButton)
        self.app.processEvents()
        page = self.window.overlay_host.current_page.findChild(BindingSettingsPage)
        self.assertIsNotNone(page)
        self.assertEqual(self.window.overlay_host.current_title, "Controls")
        return page

    def capture(self, key=None, mouse=None):
        page = self.open_controls()
        QTest.mouseClick(page.capture_button, Qt.LeftButton)
        if mouse is not None:
            QTest.mouseClick(page, mouse)
        else:
            QTest.keyClick(page.capture_button, key)
        self.app.processEvents()
        return page

    def resume(self):
        if self.window.pause_settings_active():
            QTest.mouseClick(self.button("settingsBack"), Qt.LeftButton)
        QTest.mouseClick(self.button("pauseResume"), Qt.LeftButton)
        self.window.activateWindow()
        self.app.processEvents()

    def open_input(self):
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.player_name = "Alex"
        self.window.characters = {name: {"alive": True, "profile": [], "experiences": [], "dispositions": []}
                                  for name in ("Dave", "Hank", "Jerry")}
        self.window.menu_widget.hide()
        self.window.append_speech("Dave", "Where were you?", "Alex")
        self.window.run_conversation_loop()
        self.window.interject_input.setFocus()
        self.app.processEvents()
        self.assertTrue(self.window.can_dictate())

    def test_controls_are_reachable_from_title_and_persist_without_default_binding(self):
        self.assertEqual(self.window.dictation_bindings.binding, InputBinding())
        self.assertTrue(self.window.dictation_binding_badge.isHidden())
        self.capture(key=Qt.Key_PageUp)
        selected = InputBinding.for_key(Qt.Key_PageUp)
        self.assertEqual(self.window.dictation_bindings.binding, selected)
        self.assertEqual(self.window.preferences.dictation_binding(), selected)
        self.resume()
        replacement = self.make_window()
        self.assertEqual(replacement.dictation_bindings.binding, selected)
        self.assertEqual(replacement.dictation_binding_badge.text(), selected.label)
        self.assertFalse(replacement.dictation_binding_badge.isHidden())
        self.assertFalse(self.window.dictation_recorder.is_recording)
        self.transcribe.assert_not_called()

    def test_bound_key_records_stops_and_edits_draft_without_speaking(self):
        self.capture(key=Qt.Key_PageUp)
        self.resume()
        self.open_input()
        self.window.interject_input.setText("Typed first.")
        before = tuple(self.window.public_dialogue)
        QTest.keyClick(self.window.interject_input, Qt.Key_PageUp)
        self.assertTrue(self.window.dictation_recorder.is_recording)
        QTest.keyClick(self.window.interject_input, Qt.Key_PageUp)
        self.wait_for(lambda: self.window.dictation_worker is None)
        self.assertEqual(self.window.interject_input.text(), "Typed first. Dictated words.")
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.assertTrue(self.window.player_reply_required())
        self.assertFalse(self.window.pass_button.isEnabled())
        self.assertEqual(self.window.dictation_binding_badge.text(), InputBinding.for_key(Qt.Key_PageUp).label)

    def visible_microphone_image(self, path):
        artwork = QPixmap(str(path))
        self.assertFalse(artwork.isNull())
        bounds = QRegion(artwork.mask()).boundingRect()
        self.assertFalse(bounds.isEmpty())
        return artwork.copy(bounds).scaled(48, 48, Qt.KeepAspectRatio, Qt.FastTransformation).toImage()

    def test_microphone_sprite_states_accessibility_and_static_active_highlight(self):
        self.open_input()
        button = self.window.dictation_button
        self.assertEqual(button.text(), "")
        self.assertEqual(button.accessibleName(), "Record voice")
        self.assertEqual(button.toolTip(), "")
        self.assertEqual(button.iconSize(), QSize(48, 48))
        self.assertGreaterEqual(button.width(), 64)
        self.assertFalse(button.icon().isNull())
        self.assertFalse(button.isChecked())
        self.assertFalse(self.window.dictation_glow.isEnabled())
        idle = button.icon().pixmap(button.iconSize()).toImage()
        expected_idle = self.visible_microphone_image(game.resource_path("icons/ui/microphone-idle.png"))
        self.assertEqual(idle, expected_idle)
        before = tuple(self.window.public_dialogue)
        button.click()
        self.assertEqual(button.accessibleName(), "Stop recording")
        self.assertEqual(button.toolTip(), "")
        self.assertTrue(button.isChecked())
        self.assertTrue(self.window.dictation_glow.isEnabled())
        active = button.icon().pixmap(button.iconSize()).toImage()
        expected_active = self.visible_microphone_image(game.resource_path("icons/ui/microphone-recording.png"))
        self.assertEqual(active, expected_active)
        self.assertNotEqual(active, idle)
        self.window.cancel_dictation()
        self.assertEqual(button.icon().pixmap(button.iconSize()).toImage(), idle)
        self.assertEqual(button.accessibleName(), "Record voice")
        self.assertFalse(button.isChecked())
        self.assertFalse(self.window.dictation_glow.isEnabled())
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.transcribe.assert_not_called()

    def test_microphone_resources_resolve_from_packaged_root_without_changing_raw_images(self):
        asset_root = Path(self.directory.name) / "bundle"
        sprite_root = asset_root / "icons" / "ui"
        sprite_root.mkdir(parents=True)
        source_bytes = {}
        for name in ("idle", "recording"):
            original = Path(game.resource_path(f"icons/ui/microphone-{name}.png"))
            source_bytes[name] = original.read_bytes()
            (sprite_root / original.name).write_bytes(source_bytes[name])
        house_asset = Path(game.resource_path("House_images/house-cutaway-v1.png"))
        house_root = asset_root / "House_images"
        house_root.mkdir()
        (house_root / house_asset.name).write_bytes(house_asset.read_bytes())
        with patch.object(sys, "_MEIPASS", str(asset_root), create=True):
            window = self.make_window()
        for recording, name in ((False, "idle"), (True, "recording")):
            packaged = sprite_root / f"microphone-{name}.png"
            expected = self.visible_microphone_image(packaged)
            self.assertEqual(window.dictation_icons[recording].pixmap(48, 48).toImage(), expected)
            self.assertEqual(packaged.read_bytes(), source_bytes[name])

    def test_microphone_error_and_transcription_keep_idle_state_without_sending(self):
        self.open_input()
        button = self.window.dictation_button
        idle = button.icon().pixmap(button.iconSize()).toImage()
        before = tuple(self.window.public_dialogue)
        self.window.dictation_recorder.start_error = "No microphone is available."
        button.click()
        self.assertFalse(button.isChecked())
        self.assertFalse(self.window.dictation_glow.isEnabled())
        self.assertEqual(button.icon().pixmap(button.iconSize()).toImage(), idle)
        self.assertEqual(button.accessibleName(), "Record voice")
        self.assertTrue(button.isEnabled())
        self.assertIn("No microphone", self.window.dictation_status.text())
        self.window.dictation_recorder.start_error = None
        entered, release = Event(), Event()

        def pending(_clip):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Test did not release transcription")
            return "Editable transcription."

        self.transcribe.side_effect = pending
        try:
            button.click()
            button.click()
            self.wait_for(entered.is_set)
            self.assertFalse(button.isEnabled())
            self.assertFalse(button.isChecked())
            self.assertFalse(self.window.dictation_glow.isEnabled())
            self.assertEqual(button.icon().pixmap(button.iconSize()).toImage(), idle)
            self.assertEqual(self.window.dictation_status.text(), "Transcribing...")
            self.assertEqual(tuple(self.window.public_dialogue), before)
        finally:
            release.set()
            self.wait_for(lambda: self.window.dictation_worker is None)
        self.assertTrue(button.isEnabled())
        self.assertEqual(self.window.interject_input.text(), "Editable transcription.")
        self.assertEqual(tuple(self.window.public_dialogue), before)

    def test_mouse_four_capture_dispatch_and_modal_suspension(self):
        self.capture(mouse=Qt.ExtraButton1)
        self.assertEqual(self.window.dictation_bindings.binding, InputBinding.for_mouse("mouse4"))
        self.resume()
        self.open_input()
        QTest.mouseClick(self.window.interject_input, Qt.ExtraButton1)
        self.assertTrue(self.window.dictation_recorder.is_recording)
        QTest.mouseClick(self.window.interject_input, Qt.ExtraButton1)
        self.wait_for(lambda: self.window.dictation_worker is None)
        self.open_controls()
        QTest.mouseClick(self.window.overlay_host, Qt.ExtraButton1)
        self.assertFalse(self.window.dictation_recorder.is_recording)
        self.assertEqual(self.transcribe.call_count, 1)

    def test_escape_cancels_capture_then_returns_to_menu_then_resumes(self):
        page = self.open_controls()
        QTest.mouseClick(page.capture_button, Qt.LeftButton)
        self.assertTrue(self.window.dictation_bindings.is_capturing)
        QTest.keyClick(page.capture_button, Qt.Key_Escape)
        self.assertFalse(self.window.dictation_bindings.is_capturing)
        self.assertEqual(self.window.overlay_host.current_title, "Controls")
        QTest.keyClick(self.window.overlay_host, Qt.Key_Escape)
        self.assertEqual(self.window.overlay_host.current_title, "Menu")
        QTest.keyClick(self.window.overlay_host, Qt.Key_Escape)
        self.assertIsNone(self.window.overlay_host.current_page)
        self.assertIsNone(self.window._esc_overlay)

    @patch.dict(os.environ, {"ALIBAI_DEV_MODE": "1"})
    def test_settings_replace_one_overlay_and_render_actual_runtime_configuration(self):
        self.window.ai_runtime.config = replace(self.window.ai_runtime.config, speech_model="configured-model", reasoning_effort="medium")
        self.window.open_escape_menu()
        QTest.mouseClick(self.button("pauseModels"), Qt.LeftButton)
        panel = self.window.overlay_host.current_page.findChild(ModelConfigPanel)
        self.assertEqual(panel.values["speech_model"], "configured-model")
        self.assertEqual(panel.values["reasoning_effort"], "medium")
        self.assertEqual(panel.values["dialogue_store"], "false")
        self.assertEqual(self.window.overlay_host.current_title, "Model configuration")
        visible_dialogs = [widget for widget in self.app.topLevelWidgets() if isinstance(widget, QDialog) and widget.isVisible()]
        self.assertEqual(visible_dialogs, [self.window.overlay_host])
        QTest.mouseClick(self.button("settingsBack"), Qt.LeftButton)
        QTest.mouseClick(self.button("pauseControls"), Qt.LeftButton)
        self.assertEqual(self.window.overlay_host.current_title, "Controls")
        self.assertIsNone(self.window._esc_overlay)

    def test_typing_and_reserved_game_controls_do_not_become_recording_keys(self):
        page = self.capture(key=Qt.Key_A)
        self.assertTrue(self.window.dictation_bindings.is_capturing)
        self.assertIn("typing", page.status_label.text())
        QTest.keyClick(page.capture_button, Qt.Key_Return)
        self.assertTrue(self.window.dictation_bindings.is_capturing)
        self.assertIn("reserved", page.status_label.text())
        self.assertEqual(self.window.dictation_bindings.binding, InputBinding())
        QTest.keyClick(page.capture_button, Qt.Key_PageUp)
        self.resume()
        self.open_input()
        QTest.keyClicks(self.window.interject_input, "My ordinary typed alibi")
        QTest.keyClick(self.window.interject_input, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(self.window.interject_input.selectedText(), "My ordinary typed alibi")
        self.assertFalse(self.window.dictation_recorder.is_recording)

    def test_phase_guards_block_start_but_allow_stopping_an_active_capture(self):
        self.capture(key=Qt.Key_PageUp)
        self.resume()
        self.open_input()
        self.window.ai_busy = True
        QTest.keyClick(self.window.interject_input, Qt.Key_PageUp)
        self.assertFalse(self.window.dictation_recorder.is_recording)
        self.window.ai_busy = False
        QTest.keyClick(self.window.interject_input, Qt.Key_PageUp)
        self.assertTrue(self.window.dictation_recorder.is_recording)
        self.window.phase = "action"
        QTest.keyClick(self.window.interject_input, Qt.Key_PageUp)
        self.assertFalse(self.window.dictation_recorder.is_recording)
        self.assertIsNone(self.window.dictation_context)
        self.transcribe.assert_not_called()
        QTest.keyClick(self.window.interject_input, Qt.Key_PageUp)
        self.assertFalse(self.window.dictation_recorder.is_recording)

    def test_failed_save_keeps_previous_binding_and_failure_is_inline(self):
        original = InputBinding.for_mouse("mouse4")
        self.window.dictation_bindings.set_binding(original)
        with patch.object(self.window.preferences, "save_dictation_binding", return_value=False):
            page = self.capture(key=Qt.Key_PageUp)
        self.assertEqual(self.window.dictation_bindings.binding, original)
        self.assertIn("previous choice", page.status_label.text())
        self.resume()
        self.open_input()
        self.window.dictation_bindings.action = Mock(side_effect=RuntimeError("Private exception sentinel"))
        QTest.mouseClick(self.window.interject_input, Qt.ExtraButton1)
        self.assertIn("could not be toggled", self.window.dictation_status.text())
        self.assertNotIn("Private exception sentinel", self.window.dictation_status.text())

    def test_sound_and_resume_keep_existing_game_state_and_input(self):
        self.open_input()
        self.window.interject_input.setText("Unfinished draft")
        self.window.open_escape_menu()
        QTest.mouseClick(self.button("pauseSound"), Qt.LeftButton)
        self.assertFalse(self.window.music_enabled)
        self.assertFalse(self.window.sfx_enabled)
        self.assertEqual(self.button("pauseSound").text(), "Sound: Off")
        self.resume()
        self.assertEqual(self.window.phase, "conversation")
        self.assertEqual(self.window.interject_input.text(), "Unfinished draft")
        self.assertTrue(self.window.player_reply_required())

    def test_restart_and_close_remove_old_binding_dispatch(self):
        self.capture(key=Qt.Key_PageUp)
        self.resume()
        self.open_input()
        old_controller = self.window.dictation_bindings
        self.window.open_escape_menu()
        QTest.mouseClick(self.button("pauseRestart"), Qt.LeftButton)
        self.wait_for(lambda: getattr(self.app, "_main_window", self.window) is not self.window)
        replacement = self.app._main_window
        self.windows.append(replacement)
        self.assertEqual(replacement.dictation_bindings.binding, InputBinding.for_key(Qt.Key_PageUp))
        self.assertTrue(old_controller._closed)
        QTest.keyClick(self.window.interject_input, Qt.Key_PageUp)
        self.assertFalse(self.window.dictation_recorder.is_recording)
        replacement.close()
        self.assertTrue(replacement.dictation_bindings._closed)
        self.assertTrue(replacement.dictation_recorder.closed)
        self.assertTrue(replacement.overlay_host.owner_cancelled)


class VoiceSettingsFixtureCleanupTests(unittest.TestCase):
    def test_failed_setup_restores_patches_and_removes_temporary_directory(self):
        original_transcribe = game.AIRuntime.transcribe
        original_recorder = game.DictationRecorder
        original_connect = socket.socket.connect
        previous_settings = os.environ.get("ALIBAI_SETTINGS_PATH")
        fixture = VoiceSettingsIntegrationTests("test_controls_are_reachable_from_title_and_persist_without_default_binding")
        with patch.object(fixture, "make_window", side_effect=RuntimeError("Injected setup failure")):
            result = unittest.TestResult()
            fixture.run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Injected setup failure", result.errors[0][1])
        self.assertIs(game.AIRuntime.transcribe, original_transcribe)
        self.assertIs(game.DictationRecorder, original_recorder)
        self.assertIs(socket.socket.connect, original_connect)
        self.assertEqual(os.environ.get("ALIBAI_SETTINGS_PATH"), previous_settings)
        self.assertFalse(Path(fixture.directory.name).exists())


if __name__ == "__main__":
    unittest.main()
