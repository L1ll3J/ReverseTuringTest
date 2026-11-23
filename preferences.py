"""Small local UI preferences, separate from progression and character identity."""

import os

from PyQt5.QtCore import QSettings

from input_bindings import InputBinding


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

    def dictation_binding(self):
        return InputBinding.from_json(self.settings.value("input/dictation_binding", ""))

    def save_dictation_binding(self, binding):
        if not isinstance(binding, InputBinding):
            raise TypeError("The dictation binding must be an InputBinding")
        return self._save_setting("input/dictation_binding", binding.to_json())

    def microphone_name(self):
        value = self.settings.value("audio/microphone", "")
        return value if isinstance(value, str) else ""

    def save_microphone_name(self, name):
        if not isinstance(name, str):
            raise TypeError("The microphone name must be text")
        return self._save_setting("audio/microphone", name)

    def _save_setting(self, key, value):
        # QSettings retains its first error. A fresh writer permits a later retry
        # after the user repairs the path, while changing only this preference.
        writer = QSettings(self.settings.fileName(), self.settings.format())
        had_value = writer.contains(key)
        previous = writer.value(key)
        writer.setValue(key, value)
        writer.sync()
        if writer.status() == QSettings.NoError:
            return True
        if had_value:
            writer.setValue(key, previous)
        else:
            writer.remove(key)
        writer.sync()
        return False
