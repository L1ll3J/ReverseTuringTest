"""Native input geometry and game-menu presentation regression checks."""

import unittest

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest

import test_voice_settings_integration as fixtures


class ConversationControlsUXTests(unittest.TestCase):
    setUpClass = fixtures.VoiceSettingsIntegrationTests.__dict__["setUpClass"]
    setUp = fixtures.VoiceSettingsIntegrationTests.setUp
    tearDown = fixtures.VoiceSettingsIntegrationTests.tearDown
    make_window = fixtures.VoiceSettingsIntegrationTests.make_window
    open_input = fixtures.VoiceSettingsIntegrationTests.open_input
    wait_for = fixtures.VoiceSettingsIntegrationTests.wait_for

    def test_recording_button_stays_under_pointer_when_cancel_appears(self):
        for width, height in ((1280, 800), (2560, 1440)):
            with self.subTest(width=width):
                self.window.resize(width, height)
                self.open_input()
                self.app.processEvents()
                button = self.window.dictation_button
                before = button.mapToGlobal(QPoint(0, 0))
                QTest.mouseClick(button, Qt.LeftButton)
                self.app.processEvents()
                self.assertTrue(self.window.dictation_recorder.is_recording)
                self.assertEqual(button.mapToGlobal(QPoint(0, 0)), before)
                self.assertFalse(button.geometry().intersects(self.window.dictation_cancel_button.geometry()))
                QTest.mouseClick(button, Qt.LeftButton)
                self.wait_for(lambda: self.window.dictation_worker is None)
                self.app.processEvents()
                self.assertEqual(button.mapToGlobal(QPoint(0, 0)), before)

    def test_text_controls_fit_and_input_is_borderless_and_centred(self):
        self.open_input()
        self.app.processEvents()
        for button in (self.window.say_button, self.window.game_menu_button, self.window.inventory_button):
            with self.subTest(button=button.text()):
                self.assertIn("border: none", button.styleSheet())
                self.assertLessEqual(button.fontMetrics().horizontalAdvance(button.text()), button.width())
        self.assertEqual(self.window.interject_input.alignment(), Qt.AlignCenter)
        self.assertNotIn("border-bottom", self.window.interject_input.styleSheet())

    def test_required_reply_notice_tracks_the_current_human_reply_obligation(self):
        self.open_input()
        self.app.processEvents()
        self.assertFalse(self.window.conversation_stage.reply_notice.isHidden())
        self.assertIn(self.window.display_actor(self.window.player_name),
                      self.window.conversation_stage.reply_notice.text())
        self.window.append_speech(self.window.player_name, "I was in the kitchen.")
        self.assertTrue(self.window.conversation_stage.reply_notice.isHidden())

    def test_say_is_below_the_composer_and_centered_without_growing_normal_windows(self):
        self.open_input()
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            with self.subTest(width=width):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual((self.window.width(), self.window.height()), (width, height))
                input_bottom = self.window.interject_input.mapTo(self.window, self.window.interject_input.rect().bottomLeft()).y()
                say_top = self.window.say_button.mapTo(self.window, QPoint(0, 0)).y()
                say_center = self.window.say_button.mapTo(self.window, self.window.say_button.rect().center()).x()
                self.assertGreaterEqual(say_top, input_bottom)
                self.assertLessEqual(abs(say_center - self.window.rect().center().x()), 2)

if __name__ == "__main__":
    unittest.main()
