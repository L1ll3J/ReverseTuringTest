"""Local QSettings checks, always isolated from user preferences."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QSettings

from preferences import Preferences
from input_bindings import InputBinding
from PyQt5.QtCore import Qt


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "settings.ini"
        self.environment = patch.dict(os.environ, {"ALIBAI_SETTINGS_PATH": str(self.path)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_missing_preferences_read_does_not_create_file(self):
        preferences = Preferences()
        self.assertEqual(preferences.last_player_name(), "")
        self.assertEqual(preferences.dictation_binding(), InputBinding())
        self.assertFalse(self.path.exists())

    def test_literal_name_roundtrips_and_preserves_unrelated_keys(self):
        settings = QSettings(str(self.path), QSettings.IniFormat)
        settings.setValue("audio/volume", 17)
        settings.sync()
        literal = "  JoSH O'Connor \u03a9  "
        self.assertTrue(Preferences().save_player_name(literal))
        reopened = Preferences()
        self.assertEqual(reopened.last_player_name(), literal)
        self.assertEqual(reopened.settings.value("audio/volume", type=int), 17)
        self.assertEqual(Path(reopened.settings.fileName()), self.path)

    def test_non_text_existing_name_falls_back_without_resetting_settings(self):
        settings = QSettings(str(self.path), QSettings.IniFormat)
        settings.setValue("player/last_entered_name", 12)
        settings.setValue("unrecognised/future", "retained")
        settings.sync()
        preferences = Preferences()
        self.assertEqual(preferences.last_player_name(), "")
        self.assertEqual(preferences.settings.value("unrecognised/future"), "retained")
        with self.assertRaises(TypeError):
            preferences.save_player_name(None)

    def test_unwritable_settings_returns_nonfatal_failure(self):
        preferences = Preferences(path=self.directory.name)
        self.assertFalse(preferences.save_player_name("Alex"))
        self.assertTrue(Path(self.directory.name).is_dir())

    def test_binding_roundtrips_preserve_name_and_unknown_settings(self):
        preferences = Preferences()
        self.assertTrue(preferences.save_player_name("Adad O'Connor"))
        preferences.settings.setValue("future/option", "retained")
        preferences.settings.sync()
        for binding in (InputBinding.for_key(Qt.Key_PageUp), InputBinding.for_mouse("mouse4"), InputBinding()):
            self.assertTrue(preferences.save_dictation_binding(binding))
            reopened = Preferences()
            self.assertEqual(reopened.dictation_binding(), binding)
            self.assertEqual(reopened.last_player_name(), "Adad O'Connor")
            self.assertEqual(reopened.settings.value("future/option"), "retained")

    def test_bad_or_future_binding_falls_back_without_rewriting_the_raw_value(self):
        preferences = Preferences()
        raw = '{"version":9,"kind":"future","data":"keep"}'
        preferences.settings.setValue("input/dictation_binding", raw)
        preferences.settings.setValue("future/option", "retained")
        preferences.settings.sync()
        before = self.path.read_bytes()
        self.assertEqual(preferences.dictation_binding(), InputBinding())
        self.assertEqual(preferences.settings.value("input/dictation_binding"), raw)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(TypeError):
            preferences.save_dictation_binding("PgUp")
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_binding_save_can_retry_after_the_path_is_repaired(self):
        self.path.mkdir()
        preferences = Preferences()
        binding = InputBinding.for_mouse("mouse5")
        self.assertFalse(preferences.save_dictation_binding(binding))
        self.assertEqual(preferences.dictation_binding(), InputBinding())
        self.path.rmdir()
        self.assertTrue(preferences.save_dictation_binding(binding))
        self.assertEqual(Preferences().dictation_binding(), binding)

    def test_microphone_selection_roundtrips_without_overwriting_other_settings(self):
        preferences = Preferences()
        self.assertEqual(preferences.microphone_name(), "")
        self.assertTrue(preferences.save_player_name("Alex"))
        self.assertTrue(preferences.save_dictation_binding(InputBinding.for_mouse("mouse4")))
        preferences.settings.setValue("future/option", "retained")
        preferences.settings.sync()
        for name in ("Microphone (USB Audio)", ""):
            self.assertTrue(preferences.save_microphone_name(name))
            reopened = Preferences()
            self.assertEqual(reopened.microphone_name(), name)
            self.assertEqual(reopened.last_player_name(), "Alex")
            self.assertEqual(reopened.dictation_binding(), InputBinding.for_mouse("mouse4"))
            self.assertEqual(reopened.settings.value("future/option"), "retained")

    def test_invalid_microphone_preference_falls_back_without_rewriting(self):
        preferences = Preferences()
        preferences.settings.setValue("audio/microphone", 12)
        preferences.settings.sync()
        original = self.path.read_bytes()
        self.assertEqual(preferences.microphone_name(), "")
        with self.assertRaises(TypeError):
            preferences.save_microphone_name(None)
        self.assertEqual(self.path.read_bytes(), original)

    def test_microphone_save_can_retry_after_the_path_is_repaired(self):
        self.path.mkdir()
        preferences = Preferences()
        self.assertFalse(preferences.save_microphone_name("Microphone (USB Audio)"))
        self.path.rmdir()
        self.assertTrue(preferences.save_microphone_name("Microphone (USB Audio)"))
        self.assertEqual(Preferences().microphone_name(), "Microphone (USB Audio)")


if __name__ == "__main__":
    unittest.main()
