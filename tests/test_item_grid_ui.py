"""Offline input and responsive layout contracts for illustrated item controls."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ.setdefault('QT_QPA_FONTDIR', 'C:/Windows/Fonts')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel
from content import UNLOCK_GOALS
from item_catalogue_ui import ItemCataloguePanel
from item_ui import InlineItemDraftPage
from items import ITEM_CATALOGUE, DraftOffer
from progression import ProfileSnapshot


class ItemGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            page.close()
            page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def catalogue(self):
        profile = ProfileSnapshot(unlocked={'guardian_angel'})
        callback = Mock(return_value=ProfileSnapshot(unlocked={'guardian_angel'}, disabled_items={'guardian_angel'}))
        page = ItemCataloguePanel(ITEM_CATALOGUE, profile, UNLOCK_GOALS, callback, QFont('Segoe UI', 12))
        self.pages.append(page)
        page.resize(800, 600)
        page.show()
        self.app.processEvents()
        return page, callback

    def test_hover_and_keyboard_focus_expose_full_details_without_new_window(self):
        page, callback = self.catalogue()
        windows = set(QApplication.topLevelWidgets())
        card = page.cards['guardian_angel']
        page.scroll.ensureWidgetVisible(card)
        self.app.processEvents()
        QTest.mouseMove(card, QPoint(10, 10))
        self.app.processEvents()
        self.assertEqual(page.detail_description.text(), ITEM_CATALOGUE['guardian_angel'].description)
        locked = page.cards['booby_trap']
        locked.setFocus()
        self.app.processEvents()
        self.assertEqual(page.detail_goal.text(), 'Unlock: ' + UNLOCK_GOALS['booby_trap'])
        self.assertTrue(locked.property('locked'))
        self.assertFalse(locked.isCheckable())
        self.assertEqual(set(QApplication.topLevelWidgets()), windows)
        callback.assert_not_called()

    def test_whole_optional_card_toggles_by_mouse_and_space_and_preserves_widgets(self):
        page, callback = self.catalogue()
        card = page.cards['guardian_angel']
        page.scroll.ensureWidgetVisible(card)
        self.app.processEvents()
        label = card.findChild(QLabel, 'itemName_guardian_angel')
        point = label.mapTo(card, label.rect().center())
        QTest.mouseClick(card, Qt.LeftButton, pos=point)
        callback.assert_called_once_with('guardian_angel', False)
        self.assertFalse(card.isChecked())
        self.assertIs(page.checkboxes['guardian_angel'], card)
        callback.reset_mock()
        callback.return_value = ProfileSnapshot(unlocked={'guardian_angel'})
        card.setFocus()
        QTest.keyClick(card, Qt.Key_Space)
        callback.assert_called_once_with('guardian_angel', True)
        self.assertTrue(card.isChecked())
        identities = dict(page.cards)
        page.refresh(page.profile)
        self.assertEqual(identities, page.cards)

    def test_default_group_precedes_optional_and_grid_reflows_without_horizontal_scroll(self):
        page, _ = self.catalogue()
        self.assertTrue(all(ITEM_CATALOGUE[key].default for key, card in page.cards.items() if card in page.grids[0].cards))
        self.assertTrue(all(not ITEM_CATALOGUE[key].default for key, card in page.cards.items() if card in page.grids[1].cards))
        ranks = {rarity: index for index, rarity in enumerate(('green', 'blue', 'purple', 'gold'))}
        for default, grid in zip((True, False), page.grids):
            actual = [card.objectName().removeprefix('itemCard_') for card in grid.cards]
            expected = sorted((key for key, item in ITEM_CATALOGUE.items() if item.default == default),
                              key=lambda key: ranks[ITEM_CATALOGUE[key].rarity])
            self.assertEqual(actual, expected)
        for width, height in ((480, 600), (800, 600), (1920, 1080), (2560, 1440)):
            page.resize(width, height)
            self.app.processEvents()
            self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreaterEqual(page.grids[0].columns, 1)

    def test_room_card_and_reroll_actions_are_separate_native_keyboard_controls(self):
        take, reroll, leave = Mock(), Mock(), Mock()
        page = InlineItemDraftPage(DraftOffer('receipt', 'Kitchen', ('banana', 'muffle', 'love_potion')),
                                   ITEM_CATALOGUE, take, reroll, leave, reroll_count=1,
                                   can_reroll={0: True, 1: False, 2: True}, picks_remaining=3)
        self.pages.append(page)
        page.resize(800, 600)
        page.show()
        self.app.processEvents()
        self.assertEqual(page.offer_grid.columns, 3)
        widths = [card.width() for card in page.offer_grid.cards]
        self.assertLessEqual(max(widths)-min(widths), 1)
        QTest.mouseClick(page.take_buttons[0], Qt.LeftButton)
        take.assert_called_once_with('receipt', 0)
        page.reroll_buttons[0].click()
        reroll.assert_called_once_with('receipt', 0)
        self.assertEqual(take.call_count, 1)
        self.assertFalse(page.reroll_buttons[1].isEnabled())
        page.take_buttons[0].setFocus()
        QTest.keyClick(page.take_buttons[0], Qt.Key_Tab)
        self.assertTrue(page.reroll_buttons[0].hasFocus())
        QTest.keyClick(page.reroll_buttons[0], Qt.Key_Tab)
        self.assertTrue(page.take_buttons[1].hasFocus())
        QTest.keyClick(page.take_buttons[1], Qt.Key_Space)
        self.assertEqual(take.call_args.args, ('receipt', 1))
        page.resize(400, 600)
        self.app.processEvents()
        self.assertEqual(page.offer_grid.columns, 1)
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        self.assertFalse(any(button.text() == 'Take' for button in page.take_buttons.values()))
