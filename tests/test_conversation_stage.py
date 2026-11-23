"""Native stage body identity, input, and artwork rendering checks."""
import os
from pathlib import Path
import sys
import unittest
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication
from conversation_widgets import ConversationStage, StageParticipant, character_art_path


class ConversationStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["stage-tests"])

    def setUp(self):
        self.stage = ConversationStage()
        self.stage.resize(900, 200)
        self.stage.show()

    def tearDown(self):
        self.stage.close()
        self.stage.deleteLater()

    def test_body_identity_drives_clicks_even_with_duplicate_names(self):
        self.stage.set_participants([StageParticipant("body-a", "Dave"), StageParticipant("body-b", "Dave")])
        spy = QSignalSpy(self.stage.speaker_chosen)
        self.app.processEvents()
        button = self.stage._buttons["body-b"]
        QTest.mouseClick(button, Qt.LeftButton)
        button.setFocus()
        QTest.keyClick(button, Qt.Key_Space)
        self.assertEqual(list(spy), [["body-b"], ["body-b"]])

    def test_active_body_is_cleared_when_removed_and_invalid_snapshot_is_atomic(self):
        self.stage.set_participants([StageParticipant("one", "One"), StageParticipant("two", "Two")])
        self.stage.set_active_speaker("two")
        self.assertTrue(self.stage._buttons["two"].active)
        with self.assertRaises(ValueError):
            self.stage.set_participants([StageParticipant("one", "One"), StageParticipant("one", "Other")])
        self.assertEqual(len(self.stage.participants), 2)
        self.assertEqual(self.stage.active_speaker, "two")
        self.stage.set_participants([StageParticipant("one", "One")])
        self.assertIsNone(self.stage.active_speaker)
        self.stage.set_participants([])
        self.assertEqual(self.stage.participants, ())

    def test_active_art_is_brighter_and_missing_art_keeps_name_available(self):
        photo = QPixmap(20, 40)
        photo.fill(QColor("#cccccc"))
        self.stage.set_participants([StageParticipant("one", "One", photo), StageParticipant("two", "Two", "missing.png")])
        self.app.processEvents()
        button = self.stage._buttons["one"]
        sample_x = button.x() + button.width() // 2
        before = self.stage.grab().toImage().pixelColor(sample_x, button.y() + 70).lightness()
        self.stage.set_active_speaker("one")
        self.app.processEvents()
        after = self.stage.grab().toImage().pixelColor(sample_x, button.y() + 70).lightness()
        self.assertGreater(after, before)
        self.assertEqual(self.stage._buttons["two"].text(), "Two")
        self.assertTrue(self.stage._buttons["two"].uses_fallback_art)
        self.assertFalse(self.stage._buttons["two"].photo.isNull())
        self.assertIsNone(character_art_path("Unknown"))
        self.assertTrue(Path(character_art_path("Dave")).is_file())

    def test_required_reply_notice_is_explicit_and_clears_without_replacing_cast(self):
        self.stage.set_participants([StageParticipant("old-player-body", "John")])
        self.stage.set_required_reply("Dave")
        self.assertIn("Dave, you have been addressed", self.stage.reply_notice.text())
        self.assertFalse(self.stage.reply_notice.isHidden())
        self.assertEqual(self.stage.participants[0].body_id, "old-player-body")
        self.stage.set_required_reply(None)
        self.assertTrue(self.stage.reply_notice.isHidden())
        with self.assertRaises(ValueError):
            self.stage.set_required_reply("")

if __name__ == "__main__":
    unittest.main()
