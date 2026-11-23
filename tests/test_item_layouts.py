"""Real reward and weapon layouts fit their text at playable window sizes."""

import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import QLabel

import test_item_integration as fixtures


class ItemLayoutIntegrationTests(unittest.TestCase):
    setUpClass = fixtures.ItemIntegrationTests.__dict__["setUpClass"]
    setUp = fixtures.ItemIntegrationTests.setUp
    tearDown = fixtures.ItemIntegrationTests.tearDown
    weapon = fixtures.ItemIntegrationTests.weapon
    night = fixtures.ItemIntegrationTests.night

    def test_first_visit_rewards_use_generous_height_without_normal_screen_scrollbars(self):
        self.window.showNormal()
        self.window.resize(1920, 1080)
        self.night()
        self.window.choose_room("Basement")
        self.app.processEvents()
        page = self.window.draft_page
        self.assertGreaterEqual(page.height(), 600)
        self.assertEqual(page.offer_grid.columns, 3)
        self.assertEqual(page.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        for width, height in ((1280, 800), (2560, 1440)):
            self.window.resize(width, height)
            self.app.processEvents()
            self.assertEqual(page.offer_grid.columns, 3)
            self.assertEqual(page.scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
            self.assertTrue(all(card.height() >= 480 for card in page.offer_grid.cards))
            for label in page.findChildren(QLabel):
                if label.objectName().startswith(("inlineItemName_", "inlineItemDescription_", "inlineItemFlavour_")):
                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))

    def test_locked_weapon_descriptions_and_goals_fit_after_reflow(self):
        self.window.showNormal()
        for width, height in ((800, 600), (1280, 800), (1920, 1080), (2560, 1440), (800, 600)):
            self.window.resize(width, height)
            self.window.resize_weapon_cards()
            self.app.processEvents()
            self.assertEqual(self.window.width(), width)
            self.assertEqual(self.window.weapon_scroll.horizontalScrollBar().maximum(), 0)
            for card in self.window.weapon_cards:
                weapon_id = card.property("weapon_id")
                description = card.findChild(QLabel, "weaponDescription_" + weapon_id)
                name = card.findChild(QLabel, "weaponName_" + weapon_id)
                self.assertGreaterEqual(description.height(), description.heightForWidth(description.width()), weapon_id)
                self.assertGreaterEqual(name.height(), name.heightForWidth(name.width()), weapon_id)
                self.assertGreaterEqual(name.width(), max(QFontMetrics(name.font()).horizontalAdvance(word)
                                                        for word in name.text().split()), weapon_id)
                self.assertLessEqual(description.geometry().bottom(), card.height() - 16, weapon_id)
            voodoo = self.window.weapon_picker_widget.findChild(QLabel, "weaponDescription_voodoo")
            self.assertTrue(voodoo.text().endswith("your former body the following night."))
            card = next(card for card in self.window.weapon_cards if card.property("weapon_id") == "voodoo")
            self.assertEqual(card.cursor().shape(), Qt.ArrowCursor)


if __name__ == "__main__":
    unittest.main()
