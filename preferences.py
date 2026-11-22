"""Small local UI preferences, separate from progression and character identity."""

import os

from PyQt5.QtCore import QSettings


class Preferences:
    def __init__(self, path=None):
        path = path if path is not None else os.getenv("ALIBAI_SETTINGS_PATH")
        self.settings = (QSettings(str(path), QSettings.IniFormat) if path else
                         QSettings(QSettings.IniFormat, QSettings.UserScope, "AlibAi", "AlibAi"))

    def last_player_name(self):
        value = self.settings.value("player/last_entered_name", "")
        return value if isinstance(value, str) else ""

    def save_player_name(self, text):
        if not isinstance(text, str):
            raise TypeError("The remembered name must be text")
        self.settings.setValue("player/last_entered_name", text)
        self.settings.sync()
        return self.settings.status() == QSettings.NoError
