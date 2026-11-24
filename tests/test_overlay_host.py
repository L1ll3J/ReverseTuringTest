"""Offline modal ownership, help, deferred notification and focus checks."""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget

from overlay_host import OverlayHost


class OverlayHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["overlay-host-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.owner = QWidget()
        layout = QVBoxLayout(self.owner)
        self.draft = QLineEdit("An unfinished alibi")
        layout.addWidget(self.draft)
        self.owner.resize(800, 600)
        self.owner.show()
        self.owner.activateWindow()
        self.draft.setFocus()
        self.app.processEvents()
        self.host = OverlayHost(self.owner)

    def tearDown(self):
        if not sip.isdeleted(self.host):
            self.host.cancel_owner()
        if not sip.isdeleted(self.owner):
            self.owner.close()
            self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def events(self):
        self.app.processEvents()
        QTest.qWait(1)
        self.app.processEvents()

    def page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        entry = QLineEdit("Choose a recipient")
        layout.addWidget(entry)
        return page, entry

    def test_session_reset_invalidates_pages_and_queued_notifications_but_remains_usable(self):
        page, _ = self.page()
        self.assertTrue(self.host.open_page(page, title="Menu"))
        stale = Mock(side_effect=lambda: self.page()[0])
        self.host.enqueue_unlock(stale, title="Old unlock")
        self.host.reset_session()
        self.events()
        self.assertFalse(self.host.owner_cancelled)
        self.assertIsNone(self.host.current_page)
        self.assertEqual(self.host.pending_unlock_count, 0)
        stale.assert_not_called()
        fresh, entry = self.page()
        self.assertTrue(self.host.open_page(fresh, title="New menu"))
        self.events()
        self.assertIs(self.host.current_page, fresh)
        self.assertIs(self.app.focusWidget(), entry)
        self.host.close_page()
        queued = Mock(side_effect=lambda: self.page()[0])
        self.host.enqueue_unlock(queued, title="Scheduled unlock")
        self.host.reset_session()
        self.events()
        queued.assert_not_called()

    def test_one_modal_surface_rejects_second_page_without_taking_ownership(self):
        first, _ = self.page()
        second, _ = self.page()
        self.assertTrue(self.host.open_page(first, title="Inventory", help_text="Use items between replies."))
        self.events()
        self.assertFalse(self.host.open_page(second, title="Transcript"))
        self.assertIs(self.host.current_page, first)
        self.assertIsNone(second.parent())
        self.assertIs(self.app.activeModalWidget(), self.host)
        self.assertEqual(self.host.windowModality(), Qt.WindowModal)
        self.assertTrue(self.host.windowFlags() & Qt.FramelessWindowHint)
        self.assertEqual(len([w for w in self.app.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()]), 1)
        second.deleteLater()

    def test_supplied_font_roles_survive_an_inherited_global_font_rule(self):
        self.owner.setStyleSheet('QWidget { font-family: "Courier New"; font-size: 8px; }')
        body = QFont("Arial")
        body.setPixelSize(19)
        heading = QFont("Monospace")
        heading.setPixelSize(13)
        host = OverlayHost(self.owner, body_font=body, title_font=heading)
        page, _ = self.page()
        host.open_page(page, title="Inventory", help_text="Readable help")
        host.help_button.setChecked(True)
        self.events()
        self.assertEqual(host.help_label.font().family(), body.family())
        self.assertEqual(host.help_label.font().pixelSize(), 19)
        self.assertEqual(host.title_label.font().family(), heading.family())
        self.assertEqual(host.title_label.font().pixelSize(), 13)
        host.cancel_owner()

    def test_inline_help_preserves_text_selection_and_native_focus(self):
        page, entry = self.page()
        self.host.open_page(page, title="Inventory", help_text="A plain <help> line & no popup.")
        self.events()
        self.assertIs(self.app.focusWidget(), entry)
        entry.setSelection(2, 8)
        selected, start = entry.selectedText(), entry.selectionStart()
        QTest.mouseClick(self.host.help_button, Qt.LeftButton)
        self.events()
        self.assertTrue(self.host.help_visible)
        self.assertEqual(self.host.help_label.text(), "A plain <help> line & no popup.")
        self.assertEqual(self.host.help_label.textFormat(), Qt.PlainText)
        self.assertEqual((entry.text(), entry.selectedText(), entry.selectionStart()),
                         ("Choose a recipient", selected, start))
        self.assertIs(self.app.activeModalWidget(), self.host)
        QTest.mouseClick(self.host.help_button, Qt.LeftButton)
        self.events()
        self.assertFalse(self.host.help_visible)
        self.assertIs(self.app.focusWidget(), entry)
        self.assertEqual((entry.selectedText(), entry.selectionStart()), (selected, start))

    def test_escape_closes_once_and_restores_owner_focus(self):
        page, entry = self.page()
        closed = QSignalSpy(self.host.page_closed)
        self.host.open_page(page, title="Inventory")
        self.events()
        QTest.keyClick(entry, Qt.Key_Escape)
        self.events()
        self.assertIsNone(self.host.current_page)
        self.assertFalse(self.host.isVisible())
        self.assertEqual(list(closed), [["Inventory"]])
        self.assertIs(self.app.focusWidget(), self.draft)
        self.assertEqual(self.draft.text(), "An unfinished alibi")

    def test_help_preserves_reversed_unicode_selection_and_later_edits(self):
        page, entry = self.page()
        entry.setText("Adad 🧀 alibi")
        self.host.open_page(page, title="Inventory", help_text="Inline help")
        self.events()
        entry.setSelection(8, -3)
        before = (entry.selectedText(), entry.selectionStart(), entry.cursorPosition())
        QTest.mouseClick(self.host.help_button, Qt.LeftButton)
        self.events()
        self.assertEqual((entry.selectedText(), entry.selectionStart(), entry.cursorPosition()), before)
        entry.setFocus()
        entry.selectAll()
        entry.insert("Changed draft")
        entry.setCursorPosition(3)
        self.host.help_button.setChecked(False)
        self.events()
        self.assertEqual(entry.text(), "Changed draft")
        self.assertEqual(entry.cursorPosition(), 3)
        self.assertIs(self.app.focusWidget(), entry)

    def test_inline_help_preserves_multiline_input_selection(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        entry = QTextEdit("First account.\nSecond account.")
        layout.addWidget(entry)
        self.host.open_page(page, title="A draft", help_text="Inline help")
        self.events()
        cursor = entry.document().find("Second account")
        entry.setTextCursor(cursor)
        before = (cursor.selectedText(), cursor.anchor(), cursor.position())
        QTest.mouseClick(self.host.help_button, Qt.LeftButton)
        self.events()
        after = entry.textCursor()
        self.assertEqual((after.selectedText(), after.anchor(), after.position()), before)
        QTest.mouseClick(self.host.help_button, Qt.LeftButton)
        self.events()
        self.assertIs(self.app.focusWidget(), entry)
        after = entry.textCursor()
        self.assertEqual((after.selectedText(), after.anchor(), after.position()), before)

    def test_replacement_from_own_signal_defers_sender_deletion(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        button = QPushButton("Replace this page")
        layout.addWidget(button)
        next_page, _ = self.page()
        alive_in_callback = []

        def replace():
            self.host.replace_page(next_page, title="Next", help_text="New inline help")
            alive_in_callback.append(not sip.isdeleted(button))

        button.clicked.connect(replace)
        self.host.open_page(page, title="First", help_text="Old help")
        self.events()
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertEqual(alive_in_callback, [True])
        self.assertIs(self.host.current_page, next_page)
        self.assertEqual(self.host.current_title, "Next")
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(page))
        self.assertFalse(sip.isdeleted(next_page))

    def test_unlocks_wait_until_current_page_closes_and_appear_in_order(self):
        page, _ = self.page()
        first = Mock(side_effect=lambda: QLabel("Love unlocked"))
        second = Mock(side_effect=lambda: QLabel("Knife unlocked"))
        self.host.open_page(page, title="Inventory")
        self.host.enqueue_unlock(first, title="Love")
        self.host.enqueue_unlock(second, title="Knife")
        self.events()
        first.assert_not_called()
        second.assert_not_called()
        self.assertEqual(self.host.pending_unlock_count, 2)
        self.host.close_page()
        self.assertIsNone(self.host.current_page)
        self.events()
        self.assertEqual(self.host.current_title, "Love")
        self.assertEqual(first.call_count, 1)
        second.assert_not_called()
        self.assertIs(self.app.activeModalWidget(), self.host)
        self.host.close_page()
        self.events()
        self.assertEqual(self.host.current_title, "Knife")
        self.assertEqual(second.call_count, 1)
        self.assertEqual(self.host.pending_unlock_count, 0)

    def test_repeated_close_does_not_strand_a_scheduled_unlock(self):
        page, _ = self.page()
        factory = Mock(side_effect=lambda: QLabel("A queued unlock"))
        self.host.open_page(page, title="Inventory")
        self.host.enqueue_unlock(factory, title="Unlock")
        self.host.close_page()
        self.host.close_page()
        self.events()
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.host.current_title, "Unlock")

    def test_open_and_close_before_pending_timer_keeps_unlock_queue_in_order(self):
        first = Mock(side_effect=lambda: QLabel("First unlock"))
        second = Mock(side_effect=lambda: QLabel("Second unlock"))
        self.host.enqueue_unlock(first, title="First")
        self.host.enqueue_unlock(second, title="Second")
        page, _ = self.page()
        self.host.open_page(page, title="Inventory")
        self.host.close_page()
        self.events()
        self.assertEqual(first.call_count, 1)
        second.assert_not_called()
        self.assertEqual(self.host.current_title, "First")
        self.host.close_page()
        self.events()
        self.assertEqual(second.call_count, 1)
        self.assertEqual(self.host.current_title, "Second")
        self.assertEqual(self.host.pending_unlock_count, 0)

    def test_parent_close_cancels_scheduled_old_run_notification(self):
        factory = Mock(side_effect=lambda: QLabel("Stale unlock"))
        self.host.enqueue_unlock(factory, title="Old run")
        self.owner.close()
        self.events()
        factory.assert_not_called()
        self.assertTrue(self.host.owner_cancelled)
        self.assertEqual(self.host.pending_unlock_count, 0)
        self.assertFalse(self.host.isVisible())
        self.assertFalse(self.host.enqueue_unlock(factory, title="Still stale"))

    def test_explicit_owner_cancellation_closes_active_page_and_leaves_app_policy(self):
        page, _ = self.page()
        factory = Mock(side_effect=lambda: QLabel("Old run"))
        before = self.app.quitOnLastWindowClosed()
        self.host.open_page(page, title="Inventory")
        self.host.enqueue_unlock(factory, title="Unlock")
        self.host.cancel_owner()
        self.events()
        self.assertIsNone(self.host.current_page)
        self.assertFalse(self.host.isVisible())
        self.assertEqual(self.host.pending_unlock_count, 0)
        factory.assert_not_called()
        self.assertEqual(self.app.quitOnLastWindowClosed(), before)
        replacement = QLabel("Not adopted")
        self.assertFalse(self.host.open_page(replacement, title="Old callback"))
        self.assertIsNone(replacement.parent())
        replacement.deleteLater()

    def test_hidden_owner_defers_queue_until_visible(self):
        self.owner.hide()
        page, _ = self.page()
        self.assertFalse(self.host.open_page(page, title="Hidden owner"))
        self.assertIsNone(page.parent())
        page.deleteLater()
        factory = Mock(side_effect=lambda: QLabel("A notification"))
        self.host.enqueue_unlock(factory, title="Deferred")
        self.events()
        factory.assert_not_called()
        self.assertEqual(self.host.pending_unlock_count, 1)
        self.owner.show()
        self.events()
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.host.current_title, "Deferred")

    def test_owner_choice_predicate_retains_queue_until_explicit_resume(self):
        ready = [False]
        host = OverlayHost(self.owner, unlock_ready=lambda: ready[0])
        factory = Mock(side_effect=lambda: QLabel("A saved unlock"))
        host.enqueue_unlock(factory, title="New Unlock!")
        self.events()
        factory.assert_not_called()
        self.assertEqual(host.pending_unlock_count, 1)
        ready[0] = True
        host.resume_unlocks()
        self.events()
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(host.current_title, "New Unlock!")
        host.cancel_owner()
        host.deleteLater()

    def test_predicate_deferred_unlock_cannot_survive_cancel_owner(self):
        host = OverlayHost(self.owner, unlock_ready=lambda: False)
        factory = Mock(side_effect=lambda: QLabel("Old owner"))
        host.enqueue_unlock(factory, title="New Unlock!")
        self.events()
        host.cancel_owner()
        host.resume_unlocks()
        self.events()
        factory.assert_not_called()
        self.assertEqual(host.pending_unlock_count, 0)
        host.deleteLater()

    def test_minimized_owner_retains_unlock_until_restored(self):
        self.owner.showMinimized()
        self.events()
        factory = Mock(side_effect=lambda: QLabel("A saved unlock"))
        self.host.enqueue_unlock(factory, title="New Unlock!")
        self.events()
        factory.assert_not_called()
        self.assertEqual(self.host.pending_unlock_count, 1)
        self.owner.showNormal()
        self.events()
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.host.current_title, "New Unlock!")

    def test_failed_factory_is_sanitized_and_next_notification_can_open(self):
        failed = QSignalSpy(self.host.unlock_failed)
        self.host.enqueue_unlock(Mock(side_effect=ValueError("private exception detail")), title="Broken")
        self.host.enqueue_unlock(lambda: QLabel("A valid unlock"), title="Next")
        self.events()
        self.events()
        self.assertEqual(self.host.current_title, "Next")
        self.assertEqual(list(failed), [["An unlock notification could not be displayed."]])

    def test_owner_cancellation_during_factory_never_opens_returned_page(self):
        created = []

        def factory():
            self.owner.close()
            page = QLabel("Too late")
            created.append(page)
            return page

        self.host.enqueue_unlock(factory, title="Old callback")
        self.events()
        self.assertTrue(self.host.owner_cancelled)
        self.assertIsNone(self.host.current_page)
        self.assertFalse(self.host.isVisible())
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(created[0]))

    def test_owner_cancellation_during_failed_factory_suppresses_stale_notice(self):
        failed = QSignalSpy(self.host.unlock_failed)

        def factory():
            self.host.cancel_owner()
            raise ValueError("An old-run failure")

        self.host.enqueue_unlock(factory, title="Old callback")
        self.events()
        self.assertTrue(self.host.owner_cancelled)
        self.assertEqual(list(failed), [])
        self.assertIsNone(self.host.current_page)

    def test_factory_cannot_open_a_second_page_reentrantly(self):
        rejected = []
        extra = QLabel("A second layer")

        def factory():
            rejected.append(self.host.open_page(extra, title="Second"))
            return QLabel("The queued page")

        self.host.enqueue_unlock(factory, title="Only page")
        self.events()
        self.assertEqual(rejected, [False])
        self.assertEqual(self.host.current_title, "Only page")
        self.assertIsNone(extra.parent())
        extra.deleteLater()

    def test_deleting_owner_cancels_owned_timers_before_factory_runs(self):
        factory = Mock(side_effect=lambda: QLabel("Never shown"))
        self.host.enqueue_unlock(factory, title="Old run")
        self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.events()
        factory.assert_not_called()
        self.assertTrue(sip.isdeleted(self.host))


if __name__ == "__main__":
    unittest.main()
