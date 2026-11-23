"""Real Qt inventory controls without a second dialog or gameplay side effects."""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QPushButton, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget

from item_ui import InventoryPage
from items import ITEM_CATALOGUE, InventoryEntry, ItemDefinition


class InventoryPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["inventory-page-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.owner = QWidget()
        self.owner.setStyleSheet('QWidget { background: black; font-family: "Courier New"; font-size: 10px; }')
        self.layout = QVBoxLayout(self.owner)
        self.body = QFont("Segoe UI")
        self.body.setPixelSize(18)
        self.title = QFont("Monospace")
        self.title.setPixelSize(14)
        self.title.setBold(True)
        self.use = Mock()

    def tearDown(self):
        self.owner.close()
        self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def page(self, entries=(), definitions=ITEM_CATALOGUE, targets=(("body-dave", "Dave"), ("body-hank", "Hank")), **kwargs):
        page = InventoryPage(entries, definitions, targets, self.use,
                             body_font=self.body, title_font=self.title, **kwargs)
        self.layout.addWidget(page)
        self.owner.resize(650, 500)
        self.owner.show()
        self.app.processEvents()
        return page

    def test_real_target_selection_uses_opaque_body_id_and_self_uses_none(self):
        page = self.page((InventoryEntry("banana-one", "banana"), InventoryEntry("paper-one", "spare_paper")))
        self.assertNotIsInstance(page, QDialog)
        self.assertFalse(page.isWindow())
        self.assertEqual(page.findChildren(QDialog), [])
        target = page.target_boxes["banana-one"]
        target.setFocus()
        QTest.keyClick(target, Qt.Key_Down)
        self.assertEqual(target.currentText(), "Hank")
        QTest.mouseClick(page.use_buttons["banana-one"], Qt.LeftButton)
        self.use.assert_called_once_with("banana-one", "body-hank")
        QTest.mouseClick(page.use_buttons["paper-one"], Qt.LeftButton)
        self.assertEqual(self.use.call_args.args, ("paper-one", None))
        self.assertNotIn("paper-one", page.target_boxes)
        self.assertFalse(any(button.text() in ("Back", "Close", "?") for button in page.findChildren(QPushButton)))

    def test_no_target_or_ineligible_phase_disables_use_without_callbacks(self):
        page = self.page((InventoryEntry("banana-one", "banana"), InventoryEntry("paper-one", "spare_paper")), targets=())
        self.assertFalse(page.use_buttons["banana-one"].isEnabled())
        self.assertTrue(page.use_buttons["paper-one"].isEnabled())
        QTest.mouseClick(page.use_buttons["banana-one"], Qt.LeftButton)
        self.use.assert_not_called()
        blocked = self.page((InventoryEntry("paper-two", "spare_paper"),), can_use=False)
        self.assertFalse(blocked.use_buttons["paper-two"].isEnabled())
        QTest.mouseClick(blocked.use_buttons["paper-two"], Qt.LeftButton)
        self.use.assert_not_called()

    def test_literal_names_descriptions_and_target_labels_with_distinct_fonts(self):
        definition = ItemDefinition("banana", "<b>Delicious & ripe</b>", "Jerry's <b>banana</b> & a friendly gesture.", "living_npc", True)
        page = self.page((InventoryEntry("one", "banana"),), definitions={"banana": definition},
                         targets=(("opaque", "<b>Dave & Hank</b>"),))
        name = page.findChild(QLabel, "inventoryName_one")
        detail = page.findChild(QLabel, "inventoryDescription_one")
        self.assertEqual(name.text(), definition.name)
        self.assertEqual(detail.text(), definition.description)
        self.assertEqual(name.textFormat(), Qt.PlainText)
        self.assertEqual(detail.textFormat(), Qt.PlainText)
        self.assertEqual(detail.font().pixelSize(), 18)
        self.assertEqual(name.font().pixelSize(), 14)
        self.assertEqual(QFontInfo(name.font()).family(), QFontInfo(self.title).family())
        self.assertEqual(QFontInfo(detail.font()).family(), QFontInfo(self.body).family())
        self.assertEqual(page.use_buttons["one"].font().pixelSize(), 14)
        box = page.target_boxes["one"]
        self.assertEqual(box.font().pixelSize(), 18)
        self.assertEqual(box.currentText(), "<b>Dave & Hank</b>")
        self.assertEqual(box.accessibleName(), "Recipient for " + definition.name)
        self.assertEqual(page.use_buttons["one"].accessibleName(), "Use " + definition.name)
        self.assertTrue(detail.textInteractionFlags() & Qt.TextSelectableByMouse)

    def test_dropdown_has_visible_arrow_and_keyboard_opens_actual_choice_list(self):
        page = self.page((InventoryEntry("one", "banana"),))
        box = page.target_boxes["one"]
        option = QStyleOptionComboBox()
        box.initStyleOption(option)
        arrow = box.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, box)
        image = box.grab().toImage()
        inner = arrow.adjusted(4, 5, -4, -5).intersected(image.rect())
        self.assertGreater(inner.width(), 0)
        self.assertTrue(any(image.pixelColor(x, y).lightness() > 100
                            for x in range(inner.left(), inner.right() + 1)
                            for y in range(inner.top(), inner.bottom() + 1)), "Dropdown arrow must contrast with its dark background")
        box.setFocus()
        QTest.keyClick(box, Qt.Key_F4)
        self.app.processEvents()
        self.assertTrue(box.view().isVisible())
        QTest.keyClick(box.view(), Qt.Key_Down)
        QTest.keyClick(box.view(), Qt.Key_Return)
        self.assertEqual(box.currentData(), "body-hank")
        self.assertFalse(box.view().isVisible())

    def test_empty_inventory_has_only_contextual_empty_state(self):
        page = self.page()
        self.assertEqual(page.findChild(QLabel, "inventoryEmpty").text(), "Your inventory is empty.")
        self.assertFalse(page.findChildren(QComboBox))
        self.assertFalse(page.findChildren(QPushButton))
        self.assertEqual(page.use_buttons, {})
        self.use.assert_not_called()

    def test_use_button_has_visible_hover_and_keyboard_focus(self):
        page = self.page((InventoryEntry("one", "banana"),))
        box = page.target_boxes["one"]
        button = page.use_buttons["one"]
        box.setFocus()
        QTest.mouseMove(box)
        self.app.processEvents()
        before = button.grab().toImage().pixelColor(8, button.height() // 2)
        QTest.mouseMove(button, button.rect().center())
        self.app.processEvents()
        after = button.grab().toImage().pixelColor(8, button.height() // 2)
        self.assertNotEqual(before, after)
        QTest.keyClick(box, Qt.Key_Tab)
        self.assertTrue(button.hasFocus())
        focused = button.grab().toImage().pixelColor(0, button.height() // 2)
        self.assertGreater(focused.lightness(), 150)
        QTest.keyClick(button, Qt.Key_Space)
        self.use.assert_called_once_with("one", "body-dave")

    def test_target_generator_is_available_for_every_targeted_item(self):
        page = self.page((InventoryEntry("one", "banana"), InventoryEntry("two", "muffle")),
                         targets=((body, label) for body, label in (("d", "Dave"), ("h", "Hank"))))
        self.assertEqual([box.count() for box in page.target_boxes.values()], [2, 2])


if __name__ == "__main__":
    unittest.main()
