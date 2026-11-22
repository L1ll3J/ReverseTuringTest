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


if __name__ == "__main__":
    unittest.main()
