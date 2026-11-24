"""Inline room offers keep interaction and ownership inside the existing host."""

import os
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import Mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from item_ui import InlineItemDraftPage
from items import ITEM_CATALOGUE, DraftOffer, DraftState, Inventory, InventoryError, ItemDefinition, eligible_item_pool


class InlineRewardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["inline-reward-tests"])
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
        self.take, self.reroll, self.leave = Mock(), Mock(), Mock()
        self.offer = DraftOffer("opaque:offer/17", "Kitchen", ("banana", "muffle", "spare_paper"))

    def tearDown(self):
        self.owner.close()
        self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def page(self, offer=None, definitions=ITEM_CATALOGUE, **kwargs):
        kwargs.setdefault("reroll_count", 1)
        kwargs.setdefault("can_reroll", True)
        page = InlineItemDraftPage(offer or self.offer, definitions,
                                   self.take, self.reroll, self.leave,
                                   body_font=self.body, title_font=self.title, **kwargs)
        self.layout.addWidget(page)
        self.owner.resize(560, 400)
        self.owner.show()
        self.app.processEvents()
        return page

    def test_embedded_buttons_bind_each_slot_and_exact_offer_id_without_windows(self):
        before = set(self.app.topLevelWidgets())
        page = self.page()
        self.assertNotIsInstance(page, QDialog)
        self.assertFalse(page.isWindow())
        self.assertEqual(page.findChildren(QDialog), [])
        self.assertEqual(set(self.app.topLevelWidgets()), before)
        for slot in range(3):
            page.take_buttons[slot].click()
            self.take.assert_called_with(self.offer.offer_id, slot)
            page.reroll_buttons[slot].click()
            self.reroll.assert_called_with(self.offer.offer_id, slot)
        page.leave_button.click()
        self.leave.assert_called_once_with(self.offer.offer_id)
        self.assertEqual(self.take.call_count, 3)
        self.assertEqual(self.reroll.call_count, 3)
        self.assertTrue(page.isVisible())

    def test_caller_replaces_snapshot_and_stale_callbacks_remain_authoritatively_guarded(self):
        drafts = DraftState("player", eligible_item_pool(("guardian_angel",)), random.Random(5))
        inventory = Inventory("player")
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        rejected = []

        def take(offer_id, slot):
            try:
                drafts.choose(offer_id, slot, inventory)
            except InventoryError as error:
                rejected.append(str(error))

        self.take = take
        old = self.page(offer)
        self.assertIs(drafts.pending_offer, offer)
        self.assertEqual(inventory.entries, ())
        updated = drafts.reroll(offer.offer_id, 1, inventory.snapshot())
        fresh = self.page(updated, reroll_count=0, can_reroll=False)
        old.take_buttons[1].click()
        self.assertEqual(rejected, ["This item offer is no longer available"])
        self.assertEqual(inventory.entries, ())
        self.assertIs(drafts.pending_offer, updated)
        fresh.take_buttons[1].click()
        self.assertEqual(inventory.entries[0].item_id, updated.options[1])
        self.assertIsNone(drafts.pending_offer)

    def test_disabled_rerolls_follow_both_caller_inputs_without_consuming_or_callbacks(self):
        for count, available in ((0, True), (2, False), (0, False)):
            with self.subTest(count=count, available=available):
                page = self.page(reroll_count=count, can_reroll=available)
                self.assertIn(f"Rerolls remaining: {count}", page.findChild(QLabel, "inlineItemStatus").text())
                for button in page.reroll_buttons.values():
                    self.assertFalse(button.isEnabled())
                    button.click()
                self.assertTrue(all(button.isEnabled() for button in page.take_buttons.values()))
        self.reroll.assert_not_called()
        self.take.assert_not_called()
        self.leave.assert_not_called()

    def test_help_collapses_inline_with_mouse_or_keyboard_and_does_not_resolve_offer(self):
        page = self.page(picks_remaining=2, reroll_count=3)
        self.assertEqual(page.findChild(QLabel, "inlineItemStatus").text(), "Choose 2 items. Rerolls remaining: 3.")
        self.assertTrue(page.help_label.isHidden())
        before = set(self.app.topLevelWidgets())
        QTest.mouseClick(page.help_button, Qt.LeftButton)
        self.assertTrue(page.help_label.isVisible())
        self.assertTrue(page.help_button.isChecked())
        QTest.keyClick(page.help_button, Qt.Key_Space)
        self.assertTrue(page.help_label.isHidden())
        self.assertFalse(page.help_button.isChecked())
        self.assertEqual(set(self.app.topLevelWidgets()), before)
        self.take.assert_not_called()
        self.reroll.assert_not_called()
        self.leave.assert_not_called()

    def test_plain_names_readable_descriptions_pixel_controls_and_distinct_sprites(self):
        definition = ItemDefinition("banana", "<b>Banana & friends</b>", "A <b>literal</b> description & detail.", "living_npc", True)
        definitions = dict(ITEM_CATALOGUE, banana=definition)
        page = self.page(definitions=definitions)
        for slot, item_id in enumerate(self.offer.options):
            name = page.findChild(QLabel, f"inlineItemName_{slot}")
            detail = page.findChild(QLabel, f"inlineItemDescription_{slot}")
            sprite = page.findChild(QLabel, f"itemIcon_{item_id}")
            self.assertEqual(name.text(), definitions[item_id].name)
            self.assertEqual(detail.text(), definitions[item_id].description)
            self.assertEqual(name.textFormat(), Qt.PlainText)
            self.assertEqual(detail.textFormat(), Qt.PlainText)
            self.assertTrue(name.wordWrap())
            self.assertTrue(detail.wordWrap())
            self.assertEqual(QFontInfo(name.font()).family(), QFontInfo(self.title).family())
            self.assertEqual(QFontInfo(detail.font()).family(), QFontInfo(self.body).family())
            self.assertEqual(detail.font().pixelSize(), 18)
            self.assertEqual(name.font().pixelSize(), 14)
            self.assertEqual(sprite.accessibleName(), definitions[item_id].name)
            self.assertFalse(sprite.pixmap().isNull())
            self.assertEqual(sprite.width(), 160)
            self.assertEqual(page.take_buttons[slot].accessibleName(), "Take " + definitions[item_id].name)
            self.assertEqual(page.reroll_buttons[slot].accessibleName(), "Reroll " + definitions[item_id].name)
        for button in page.findChildren(QPushButton):
            icon = button in page.reroll_buttons.values()
            self.assertEqual(QFontInfo(button.font()).family(), QFontInfo(self.body if icon else self.title).family())
            self.assertEqual(button.font().pixelSize(), 24 if icon else 14)
            self.assertEqual(button.focusPolicy(), Qt.StrongFocus)

    def test_mouse_hover_keyboard_focus_and_pressed_state_have_visible_feedback(self):
        page = self.page()
        take, reroll = page.take_buttons[0], page.reroll_buttons[0]
        page.scroll.ensureWidgetVisible(take)
        page.help_button.setFocus()
        QTest.mouseMove(page.help_button)
        self.app.processEvents()
        before = take.grab().toImage().pixelColor(8, take.height() // 2)
        positions = {label.objectName(): label.geometry() for label in take.findChildren(QLabel)}
        QTest.mouseMove(take, take.rect().center())
        self.app.processEvents()
        hover = take.grab().toImage().pixelColor(8, take.height() // 2)
        self.assertNotEqual(before, hover)
        self.assertEqual(positions, {label.objectName(): label.geometry() for label in take.findChildren(QLabel)})
        QTest.mousePress(take, Qt.LeftButton)
        self.app.processEvents()
        pressed = take.grab().toImage().pixelColor(8, take.height() // 2)
        self.assertNotEqual(hover, pressed)
        self.assertEqual(positions, {label.objectName(): label.geometry() for label in take.findChildren(QLabel)})
        QTest.mouseRelease(take, Qt.LeftButton)
        self.take.assert_called_once_with(self.offer.offer_id, 0)
        take.setFocus()
        QTest.keyClick(take, Qt.Key_Tab)
        self.assertTrue(reroll.hasFocus())
        self.assertGreater(reroll.grab().toImage().pixelColor(0, reroll.height() // 2).lightness(), 150)
        QTest.keyClick(reroll, Qt.Key_Space)
        self.reroll.assert_called_once_with(self.offer.offer_id, 0)

    def test_flavour_is_separate_literal_text_and_does_not_replace_effects(self):
        definition = ItemDefinition("banana", "Banana", "Actual effect.", "self", True,
                                    flavour="A <b>literal</b> flavour & aside.")
        without_flavour = ItemDefinition("muffle", "Muffle", "Effect without flavour.", "living_npc", True)
        page = self.page(definitions=dict(ITEM_CATALOGUE, banana=definition, muffle=without_flavour))
        self.assertEqual(page.findChild(QLabel, "inlineItemDescription_0").text(), "Actual effect.")
        flavour = page.findChild(QLabel, "inlineItemFlavour_0")
        self.assertEqual(flavour.text(), definition.flavour)
        self.assertEqual(flavour.textFormat(), Qt.PlainText)
        self.assertEqual(flavour.font().pixelSize(), 18)
        self.assertIsNone(page.findChild(QLabel, "inlineItemFlavour_1"))

    def test_small_host_scrolls_all_controls_and_wraps_long_descriptions(self):
        definitions = dict(ITEM_CATALOGUE)
        definitions["banana"] = ItemDefinition("banana", "Banana of Friendship", "Readable description. " * 30, "living_npc", True)
        page = self.page(definitions=definitions)
        self.owner.resize(420, 320)
        self.app.processEvents()
        self.assertLessEqual(self.owner.width(), 420)
        self.assertGreater(page.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        detail = page.findChild(QLabel, "inlineItemDescription_0")
        self.assertGreaterEqual(detail.height(), detail.heightForWidth(detail.width()))
        for button in (page.findChild(QLabel, "inlineItemName_0"), page.reroll_buttons[2], page.leave_button):
            page.scroll.ensureWidgetVisible(button)
            self.app.processEvents()
            position = button.mapTo(page.scroll.viewport(), QPoint(0, 0))
            self.assertGreaterEqual(position.x(), 0)
            self.assertGreaterEqual(position.y(), 0)
            self.assertLessEqual(position.x() + button.width(), page.scroll.viewport().width())
            self.assertLessEqual(position.y() + button.height(), page.scroll.viewport().height())

    def test_hiding_closing_and_escape_never_skip_caller_owned_offer(self):
        page = self.page()
        QTest.keyClick(page.take_buttons[0], Qt.Key_Escape)
        page.hide()
        page.show()
        page.close()
        self.take.assert_not_called()
        self.reroll.assert_not_called()
        self.leave.assert_not_called()


if __name__ == "__main__":
    unittest.main()
