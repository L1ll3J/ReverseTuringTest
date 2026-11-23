"""Offline reading and literal-rendering checks using real Qt documents."""

import os
from pathlib import Path
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QTextEdit

from conversation_widgets import DialogueView
from message_view import DisplayRecord, MessageLog


class MessageViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["message-view-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.editor = DialogueView()
        self.editor.setReadOnly(True)
        self.original_flags = self.editor.textInteractionFlags()
        body = QFont("Sans Serif")
        body.setPixelSize(18)
        heading = QFont("Monospace")
        heading.setPixelSize(12)
        self.view = MessageLog(self.editor, body_font=body, speaker_font=heading)
        self.view.resize(520, 320)
        self.view.show()
        self.app.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def fill(self):
        self.view.append_records(DisplayRecord(str(i), "speech", f"Line {i}: " + "A witness account. " * 8,
                                               speaker="Dave", speaker_id="body-dave", round_number=1,
                                               phase="discussion") for i in range(24))
        self.app.processEvents()
        self.assertGreater(self.editor.verticalScrollBar().maximum(), 0)

    def test_literal_text_and_trusted_name_anchors_keep_existing_click_handler(self):
        self.assertEqual(self.editor.textInteractionFlags(), self.original_flags)
        def header(record):
            fmt = QTextCharFormat()
            fmt.setAnchor(True)
            fmt.setAnchorHref("npc:" + record.speaker_id)
            fmt.setForeground(QColor("blue"))
            return fmt

        self.view.name_format = header
        text = "Jerry's <b>body</b> & a literal <a href='npc:wrong'>claim</a>.\nA second line."
        record = DisplayRecord("speech-1", "speech", text, speaker="Dave", speaker_id="opaque-body-id")
        self.view.append_record(record)
        self.app.processEvents()
        self.assertEqual(self.editor.toPlainText(), "Dave\n" + text)
        header_cursor = QTextCursor(self.editor.document())
        header_cursor.setPosition(2)
        self.assertEqual(header_cursor.charFormat().anchorHref(), "npc:opaque-body-id")
        body_cursor = self.editor.document().find("Jerry")
        self.assertFalse(body_cursor.charFormat().isAnchor())
        self.assertEqual(body_cursor.charFormat().anchorHref(), "")
        self.assertEqual(body_cursor.charFormat().font().pixelSize(), 18)
        self.assertEqual(header_cursor.charFormat().font().pixelSize(), 12)
        self.assertFalse(body_cursor.charFormat().hasProperty(QTextCharFormat.ForegroundBrush))
        clicked = QSignalSpy(self.editor.speaker_chosen)
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(1)
        point = self.editor.cursorRect(cursor).center()
        QTest.mouseClick(self.editor.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(list(clicked), [["opaque-body-id"]])

    def test_tail_follows_append_without_moving_a_selection(self):
        self.fill()
        bar = self.editor.verticalScrollBar()
        self.assertEqual(bar.value(), bar.maximum())
        self.view.append_record(DisplayRecord("tail", "event", "A new discovery."))
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.maximum())
        self.assertEqual(self.view.unread_count, 0)
        self.assertTrue(self.view.latest_button.isHidden())

    def test_scrolled_reading_position_and_selection_survive_new_records(self):
        self.fill()
        cursor = self.editor.document().find("Line 4:")
        self.editor.setTextCursor(cursor)
        self.editor.verticalScrollBar().setValue(0)
        self.app.processEvents()
        before = self.editor.textCursor()
        selected, anchor, position = before.selectedText(), before.anchor(), before.position()
        self.view.append_records((DisplayRecord("new-1", "event", "One event."),
                                  DisplayRecord("new-2", "speech", "An answer.", speaker="Hank")))
        self.app.processEvents()
        after = self.editor.textCursor()
        self.assertEqual((after.selectedText(), after.anchor(), after.position()), (selected, anchor, position))
        self.assertEqual(self.editor.verticalScrollBar().value(), 0)
        self.assertEqual(self.view.unread_count, 2)
        self.assertFalse(self.view.latest_button.isHidden())
        QTest.mouseClick(self.view.latest_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.editor.verticalScrollBar().value(), self.editor.verticalScrollBar().maximum())
        self.assertEqual(self.editor.textCursor().selectedText(), selected)
        self.assertEqual(self.view.unread_count, 0)

    def test_selection_ending_at_document_end_does_not_absorb_new_text(self):
        self.fill()
        cursor = QTextCursor(self.editor.document())
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.verticalScrollBar().setValue(0)
        before = self.editor.textCursor()
        selected, anchor, position = before.selectedText(), before.anchor(), before.position()
        self.view.append_record(DisplayRecord("not-selected", "event", "This was not selected."))
        self.app.processEvents()
        after = self.editor.textCursor()
        self.assertEqual((after.selectedText(), after.anchor(), after.position()), (selected, anchor, position))
        self.assertEqual(self.editor.verticalScrollBar().value(), 0)

    def test_scrolling_back_to_tail_clears_unread_and_resumes_following(self):
        self.fill()
        bar = self.editor.verticalScrollBar()
        bar.setValue(0)
        self.view.append_record(DisplayRecord("waiting", "event", "Waiting below."))
        self.assertEqual(self.view.unread_count, 1)
        bar.setValue(bar.maximum())
        self.app.processEvents()
        self.assertEqual(self.view.unread_count, 0)
        self.assertTrue(self.view.follows_tail)
        self.view.append_record(DisplayRecord("next", "event", "Now following."))
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.maximum())

    def test_duplicate_ids_are_idempotent_and_bad_batches_preserve_the_log(self):
        record = DisplayRecord("a", "event", "Original event")
        self.assertTrue(self.view.append_record(record))
        self.assertFalse(self.view.append_record(record))
        before = self.editor.toPlainText()
        with self.assertRaises(ValueError):
            self.view.append_records((DisplayRecord("b", "event", "Do not partly append"),
                                      DisplayRecord("a", "event", "Conflicting content")))
        self.assertEqual(self.editor.toPlainText(), before)
        self.assertEqual(self.view.records, (record,))
        self.view.name_format = lambda _record: "untrusted html is not a format"
        with self.assertRaises(TypeError):
            self.view.append_record(DisplayRecord("bad-format", "speech", "A body", speaker="Dave"))
        self.assertEqual(self.editor.toPlainText(), before)

    def test_explicit_snapshot_replaces_without_parsing_and_can_start_at_top(self):
        self.editor.setPlainText("Existing editor content <not parsed>")
        self.view.append_record(DisplayRecord("a", "section", "Morning 1", round_number=1, phase="discussion"))
        self.assertIn("Existing editor content <not parsed>", self.editor.toPlainText())
        records = tuple(DisplayRecord(str(i), "event", f"Recorded fact {i}. " * 5) for i in range(30))
        self.view.set_records(records, follow_tail=False)
        self.app.processEvents()
        self.assertNotIn("Existing editor", self.editor.toPlainText())
        self.assertEqual(self.view.records, records)
        self.assertEqual(self.editor.verticalScrollBar().value(), 0)
        self.assertEqual(self.view.unread_count, 0)
        self.view.clear()
        self.assertEqual(self.view.records, ())
        self.assertEqual(self.editor.toPlainText(), "")
        self.assertTrue(self.view.latest_button.isHidden())

    def test_unread_listener_can_jump_without_leaving_a_stale_button(self):
        self.fill()
        self.editor.verticalScrollBar().setValue(0)
        self.view.unread_changed.connect(lambda count: self.view.jump_to_latest() if count else None)
        self.view.append_record(DisplayRecord("observed", "event", "A new line."))
        self.app.processEvents()
        self.assertEqual(self.view.unread_count, 0)
        self.assertTrue(self.view.latest_button.isHidden())
        self.assertEqual(self.editor.verticalScrollBar().value(), self.editor.verticalScrollBar().maximum())


if __name__ == "__main__":
    unittest.main()
