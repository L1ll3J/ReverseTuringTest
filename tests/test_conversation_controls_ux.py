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

if __name__ == "__main__":
    unittest.main()
