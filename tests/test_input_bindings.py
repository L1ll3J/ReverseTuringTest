"""Offline binding capture and owner-local dispatch with real Qt input events."""

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout, QWidget

from input_bindings import BindingBadge, BindingError, BindingSettingsPage, DictationBindings, InputBinding
from overlay_host import OverlayHost


class BindingDataTests(unittest.TestCase):
    def test_versioned_roundtrip_and_unknown_data_fall_back_to_unbound(self):
        for binding in (InputBinding(), InputBinding.for_key(Qt.Key_PageUp),
                        InputBinding.for_key(Qt.Key_M, Qt.ControlModifier | Qt.ShiftModifier),
                        InputBinding.for_mouse("mouse4"), InputBinding.for_mouse("mouse5")):
            self.assertEqual(InputBinding.from_json(binding.to_json()), binding)
        for value in (None, 12, "bad json", "[]", '{"version":2,"kind":"mouse","button":"mouse4"}',
                      '{"version":true,"kind":"unbound"}', '{"version":1,"kind":"unknown"}',
                      '{"version":1,"kind":"mouse","button":"mouse9"}',
                      '{"version":1,"kind":"key","key":true,"modifiers":0}',
                      '{"version":1,"kind":"key","key":16777215,"modifiers":67108864}',
                      '{"version":1,"kind":"unbound","future":"preserve in settings"}'):
            self.assertEqual(InputBinding.from_json(value), InputBinding())

    def test_typing_editing_focus_and_exit_keys_remain_reserved(self):
        forbidden = ((Qt.Key_A, Qt.NoModifier), (Qt.Key_A, Qt.ShiftModifier),
                     (Qt.Key_Space, Qt.NoModifier), (Qt.Key_Return, Qt.NoModifier),
                     (Qt.Key_Escape, Qt.NoModifier), (Qt.Key_Tab, Qt.NoModifier),
                     (Qt.Key_Backspace, Qt.ControlModifier), (Qt.Key_C, Qt.ControlModifier),
                     (Qt.Key_Q, Qt.ControlModifier), (Qt.Key_F4, Qt.AltModifier),
                     (Qt.Key_Q, Qt.ControlModifier | Qt.AltModifier))
        for key, modifiers in forbidden:
            with self.subTest(key=key, modifiers=int(modifiers)):
                with self.assertRaises(BindingError):
                    InputBinding.for_key(key, modifiers)
        self.assertEqual(InputBinding.for_key(Qt.Key_PageDown).kind, "key")
        self.assertEqual(InputBinding.for_key(Qt.Key_F2).kind, "key")
        self.assertEqual(InputBinding.for_key(Qt.Key_M, Qt.ControlModifier).kind, "key")


class BindingDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["binding-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.owner = QWidget()
        layout = QVBoxLayout(self.owner)
        self.input = QLineEdit()
        layout.addWidget(self.input)
        self.owner.show()
        self.owner.activateWindow()
        self.input.setFocus()
        self.app.processEvents()
        self.action = Mock()
        self.allowed = True
        self.controller = DictationBindings(self.owner, self.action, can_trigger=lambda: self.allowed)
        self.host = OverlayHost(self.owner)
        self.other = None

    def tearDown(self):
        if not sip.isdeleted(self.controller):
            self.controller.close()
        if not sip.isdeleted(self.host):
            self.host.cancel_owner()
        if not sip.isdeleted(self.owner):
            self.owner.close()
            self.owner.deleteLater()
        if self.other is not None:
            self.other.close()
            self.other.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def events(self):
        self.app.processEvents()
        QTest.qWait(1)
        self.app.processEvents()

    def settings_page(self, save=None):
        callback = save if save is not None else Mock(return_value=True)
        page = BindingSettingsPage(self.controller, callback)
        self.assertTrue(self.host.open_page(page, title="Controls", help_text="Rebind locally."))
        self.events()
        return page, callback

    def test_default_unbound_and_bound_commands_preserve_normal_typing(self):
        self.assertEqual(self.controller.binding, InputBinding())
        QTest.keyClicks(self.input, "A natural alibi")
        QTest.keyClick(self.input, Qt.Key_PageUp)
        self.action.assert_not_called()
        self.controller.set_binding(InputBinding.for_key(Qt.Key_PageUp))
        QTest.keyClicks(self.input, " still intact")
        QTest.keyClick(self.input, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(self.input.selectedText(), "A natural alibi still intact")
        QTest.keyClick(self.input, Qt.Key_PageUp)
        self.assertEqual(self.action.call_count, 1)
        self.assertEqual(self.input.text(), "A natural alibi still intact")

    def test_qualified_key_and_side_mouse_buttons_work_while_typing(self):
        self.controller.set_binding(InputBinding.for_key(Qt.Key_M, Qt.ControlModifier | Qt.ShiftModifier))
        QTest.keyClicks(self.input, "not consumed")
        QTest.keyClick(self.input, Qt.Key_M, Qt.ControlModifier | Qt.ShiftModifier)
        self.assertEqual(self.action.call_count, 1)
        self.assertEqual(self.input.text(), "not consumed")
        for button, qt_button in (("mouse4", Qt.ExtraButton1), ("mouse5", Qt.ExtraButton2)):
            self.controller.set_binding(InputBinding.for_mouse(button))
            QTest.mouseClick(self.input, qt_button)
        self.assertEqual(self.action.call_count, 3)
        self.assertEqual(self.input.text(), "not consumed")

    def test_assigned_badge_stays_visible_and_clear_removes_it(self):
        badge = BindingBadge(self.controller, parent=self.owner)
        self.assertTrue(badge.isHidden())
        self.controller.set_binding(InputBinding.for_key(Qt.Key_PageUp))
        self.assertFalse(badge.isHidden())
        self.assertEqual(badge.text(), self.controller.binding.label)
        for _ in range(7):
            QTest.keyClick(self.input, Qt.Key_PageUp)
        self.assertFalse(badge.isHidden())
        self.assertEqual(self.action.call_count, 7)
        self.controller.set_binding(InputBinding())
        self.assertTrue(badge.isHidden())
        self.assertEqual(badge.text(), "")
    def test_held_and_autorepeat_keys_toggle_once_until_physical_release(self):
        self.controller.set_binding(InputBinding.for_key(Qt.Key_PageUp))
        for repeat in (False, True, False):
            QApplication.sendEvent(self.input, QKeyEvent(QEvent.KeyPress, Qt.Key_PageUp, Qt.NoModifier, "", repeat, 1))
        self.assertEqual(self.action.call_count, 1)
        QApplication.sendEvent(self.input, QKeyEvent(QEvent.KeyRelease, Qt.Key_PageUp, Qt.NoModifier, "", True, 1))
        QApplication.sendEvent(self.input, QKeyEvent(QEvent.KeyPress, Qt.Key_PageUp, Qt.NoModifier))
        self.assertEqual(self.action.call_count, 1)
        QApplication.sendEvent(self.input, QKeyEvent(QEvent.KeyRelease, Qt.Key_PageUp, Qt.NoModifier))
        QTest.keyClick(self.input, Qt.Key_PageUp)
        self.assertEqual(self.action.call_count, 2)

    def test_deactivation_clears_held_input_and_other_windows_do_not_dispatch(self):
        self.controller.set_binding(InputBinding.for_key(Qt.Key_PageUp))
        QApplication.sendEvent(self.input, QKeyEvent(QEvent.KeyPress, Qt.Key_PageUp, Qt.NoModifier))
        self.assertEqual(self.action.call_count, 1)
        QApplication.sendEvent(self.owner, QEvent(QEvent.WindowDeactivate))
        QApplication.sendEvent(self.input, QKeyEvent(QEvent.KeyPress, Qt.Key_PageUp, Qt.NoModifier))
        self.assertEqual(self.action.call_count, 2)
        self.other = QWidget()
        layout = QVBoxLayout(self.other)
        other_input = QLineEdit()
        layout.addWidget(other_input)
        self.other.show()
        self.other.activateWindow()
        other_input.setFocus()
        self.events()
        QTest.keyClick(other_input, Qt.Key_PageUp)
        self.assertEqual(self.action.call_count, 2)

    def test_disabled_actions_do_not_trigger_and_action_failures_stay_safe(self):
        self.controller.set_binding(InputBinding.for_key(Qt.Key_PageUp))
        self.allowed = False
        QTest.keyClick(self.input, Qt.Key_PageUp)
        self.action.assert_not_called()
        self.allowed = True
        self.action.side_effect = RuntimeError("private details")
        failed = QSignalSpy(self.controller.activation_failed)
        QTest.keyClick(self.input, Qt.Key_PageUp)
        self.assertEqual(list(failed), [["Voice recording could not be toggled. Please try again."]])
        self.action.side_effect = None
        QTest.keyClick(self.input, Qt.Key_PageUp)
        self.assertEqual(self.action.call_count, 2)

    def test_modal_capture_accepts_key_and_mouse_without_recording_behind_it(self):
        self.controller.set_binding(InputBinding.for_key(Qt.Key_PageUp))
        page, saved = self.settings_page()
        QTest.keyClick(page.capture_button, Qt.Key_PageUp)
        self.action.assert_not_called()
        QTest.mouseClick(page.capture_button, Qt.LeftButton)
        self.assertTrue(self.controller.is_capturing)
        QTest.keyClick(page.capture_button, Qt.Key_PageDown)
        self.assertEqual(self.controller.binding, InputBinding.for_key(Qt.Key_PageDown))
        self.assertEqual(saved.call_count, 1)
        self.assertEqual(page.binding_label.text(), self.controller.binding.label)
        QTest.mouseClick(page.capture_button, Qt.LeftButton)
        QTest.mouseClick(page, Qt.ExtraButton2)
        self.assertEqual(self.controller.binding, InputBinding.for_mouse("mouse5"))
        self.assertEqual(saved.call_count, 2)
        self.assertEqual(page.binding_label.text(), "Mouse 5")
        self.action.assert_not_called()
        QTest.mouseClick(page.clear_button, Qt.LeftButton)
        self.assertEqual(self.controller.binding, InputBinding())
        self.assertEqual(page.binding_label.text(), "Unbound")
        self.assertEqual(len([w for w in self.app.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()]), 1)

    def test_invalid_typing_key_and_conflict_are_inline_and_escape_only_cancels_capture(self):
        page, saved = self.settings_page()
        QTest.mouseClick(page.capture_button, Qt.LeftButton)
        QTest.keyClick(page.capture_button, Qt.Key_A)
        self.assertTrue(self.controller.is_capturing)
        self.assertIn("typing", page.status_label.text())
        saved.assert_not_called()
        self.controller.extra_conflict_reason = lambda binding: "PgUp is already used by another control." if binding.key == Qt.Key_PageUp else None
        QTest.keyClick(page.capture_button, Qt.Key_PageUp)
        self.assertIn("already used", page.status_label.text())
        self.assertTrue(self.controller.is_capturing)
        QTest.keyClick(page.capture_button, Qt.Key_Escape)
        self.assertFalse(self.controller.is_capturing)
        self.assertIs(self.host.current_page, page)
        self.assertTrue(self.host.isVisible())
        self.assertEqual(self.controller.binding, InputBinding())
        saved.assert_not_called()

    def test_failed_save_leaves_previous_binding_and_hidden_pages_do_not_save_twice(self):
        original = InputBinding.for_mouse("mouse4")
        self.controller.set_binding(original)
        inactive_save = Mock(return_value=True)
        inactive = BindingSettingsPage(self.controller, inactive_save)
        saved = Mock(return_value=False)
        page, _ = self.settings_page(saved)
        QTest.mouseClick(page.capture_button, Qt.LeftButton)
        QTest.keyClick(page.capture_button, Qt.Key_PageDown)
        self.assertEqual(self.controller.binding, original)
        self.assertEqual(page.binding_label.text(), "Mouse 4")
        self.assertIn("previous choice", page.status_label.text())
        inactive_save.assert_not_called()
        saved.return_value = True
        QTest.mouseClick(page.capture_button, Qt.LeftButton)
        QTest.keyClick(page.capture_button, Qt.Key_PageDown)
        self.assertEqual(self.controller.binding, InputBinding.for_key(Qt.Key_PageDown))
        self.assertEqual(saved.call_count, 2)
        inactive_save.assert_not_called()
        inactive.deleteLater()

    def test_hiding_capture_page_and_closing_owner_cancel_pending_capture(self):
        page, saved = self.settings_page()
        QTest.mouseClick(page.capture_button, Qt.LeftButton)
        self.assertTrue(self.controller.is_capturing)
        self.host.close_page()
        self.events()
        self.assertFalse(self.controller.is_capturing)
        saved.assert_not_called()
        self.controller.set_binding(InputBinding.for_key(Qt.Key_PageUp))
        self.owner.close()
        QApplication.sendEvent(self.input, QKeyEvent(QEvent.KeyPress, Qt.Key_PageUp, Qt.NoModifier))
        self.action.assert_not_called()

    def test_owner_destruction_clears_held_inputs_and_removes_dispatch(self):
        self.controller.set_binding(InputBinding.for_key(Qt.Key_PageUp))
        QApplication.sendEvent(self.input, QKeyEvent(QEvent.KeyPress, Qt.Key_PageUp, Qt.NoModifier))
        self.assertEqual(self.action.call_count, 1)
        self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.events()
        self.assertTrue(sip.isdeleted(self.controller))
        self.assertEqual(self.controller._held_keys, set())
        self.assertEqual(self.controller._held_mouse, set())


if __name__ == "__main__":
    unittest.main()
