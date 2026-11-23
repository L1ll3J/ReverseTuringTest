"""Offline tests for the main-menu catalogue and caller-owned pool updates."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt5.QtGui import QFont, QFontDatabase, QFontInfo, QPalette, QPixmap
from PyQt5.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton, QWidget

from content import CONTENT_CATALOGUE, UNLOCK_GOALS
from item_catalogue_ui import ItemCataloguePanel
from item_ui import InventoryPage, ItemDraftDialog
from items import ITEM_CATALOGUE, DraftOffer, InventoryEntry
from progression import ProfileError, ProfileSnapshot, ProfileStore, ProgressEvent


class ItemCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["item-catalogue-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32" and cls.app.platformName() == "offscreen":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        self.font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        self.font.setPixelSize(18)
        self.callback = Mock()
        self.parent = QWidget()
        self.parent.setStyleSheet('QWidget { font-family: "Courier New"; font-size: 10px; background-color: black; }')
        self.panels = []

    def tearDown(self):
        self.parent.close()
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def panel(self, profile=None, **kwargs):
        panel = ItemCataloguePanel(ITEM_CATALOGUE, profile or ProfileSnapshot(), UNLOCK_GOALS,
                                   self.callback, self.font, self.parent, **kwargs)
        panel.resize(560, 480)
        self.parent.show()
        panel.show()
        self.app.processEvents()
        self.panels.append(panel)
        return panel

    def test_lists_defaults_and_locked_goal_without_optional_checkbox(self):
        panel = self.panel()
        for item_id, item in ITEM_CATALOGUE.items():
            self.assertEqual(panel.findChild(QLabel, f"itemName_{item_id}").text(), item.name)
            self.assertTrue(panel.findChild(QLabel, f"itemIcon_{item_id}").isVisible())
            expected = "Default - always included" if item.default else "Locked"
            self.assertEqual(panel.findChild(QLabel, f"itemStatus_{item_id}").text(), expected)
            if not item.default:
                goal = panel.findChild(QLabel, f"itemGoal_{item_id}")
                self.assertEqual(goal.text(), "Unlock: " + UNLOCK_GOALS[item_id])
        goal = panel.findChild(QLabel, "itemGoal_guardian_angel")
        self.assertEqual(goal.text(), "Unlock: " + UNLOCK_GOALS["guardian_angel"])
        self.assertFalse(panel.checkboxes)
        self.assertFalse(panel.findChildren(QCheckBox))
        self.callback.assert_not_called()

    def test_real_profile_unlock_and_pool_toggle_roundtrip_through_catalogue(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory) / "profile.json", CONTENT_CATALOGUE)
            panel = self.panel(store.read())
            self.assertEqual(len(panel.findChildren(QLabel, "itemName_booby_trap")), 1)
            profile = store.record(ProgressEvent("run", 1, "marked_kill_completed")).profile
            panel.refresh(profile)
            self.assertEqual(set(panel.checkboxes), {"booby_trap"})
            self.assertIn("booby_trap", store.item_pool())
            self.callback.side_effect = store.set_item_enabled
            panel.checkboxes["booby_trap"].setChecked(False)
            self.assertNotIn("booby_trap", store.item_pool())
            reopened = ProfileStore(store.path, CONTENT_CATALOGUE)
            panel.refresh(reopened.read())
            self.assertFalse(panel.checkboxes["booby_trap"].isChecked())
            self.assertNotIn("booby_trap", reopened.item_pool())
            self.assertEqual(panel.findChild(QLabel, "itemStatus_booby_trap").text(), "Unlocked")

    def test_optional_checkbox_uses_returned_snapshot_and_refresh_does_not_write(self):
        profile = ProfileSnapshot(unlocked={"guardian_angel"})
        excluded = ProfileSnapshot(unlocked={"guardian_angel"}, disabled_items={"guardian_angel"})
        panel = self.panel(profile)
        self.assertEqual(set(panel.checkboxes), {"guardian_angel"})
        self.assertTrue(panel.checkboxes["guardian_angel"].isChecked())
        self.callback.return_value = excluded
        panel.checkboxes["guardian_angel"].setChecked(False)
        self.callback.assert_called_once_with("guardian_angel", False)
        self.assertIs(panel.profile, excluded)
        self.assertFalse(panel.checkboxes["guardian_angel"].isChecked())
        self.assertEqual(panel.findChild(QLabel, "itemStatus_guardian_angel").text(), "Unlocked")
        panel.refresh(ProfileSnapshot())
        self.assertFalse(panel.checkboxes)
        self.assertEqual(self.callback.call_count, 1)

    def test_profile_error_rolls_back_without_retrying_or_losing_snapshot(self):
        profile = ProfileSnapshot(unlocked={"guardian_angel"}, disabled_items={"guardian_angel"})
        panel = self.panel(profile)
        self.callback.side_effect = ProfileError("Could not save <local> preferences.")
        panel.checkboxes["guardian_angel"].setChecked(True)
        self.assertIs(panel.profile, profile)
        self.assertFalse(panel.checkboxes["guardian_angel"].isChecked())
        self.assertTrue(panel.error_label.isVisible())
        self.assertEqual(panel.error_label.text(), "Could not save <local> preferences.")
        self.assertEqual(panel.error_label.textFormat(), Qt.PlainText)
        self.callback.assert_called_once_with("guardian_angel", True)
        panel.refresh(profile)
        self.assertTrue(panel.error_label.isHidden())

    def test_loads_icons_from_supplied_directory_and_has_missing_icon_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            image = QPixmap(40, 20)
            image.fill(Qt.yellow)
            self.assertTrue(image.save(str(Path(directory) / "banana.png")))
            panel = self.panel(icon_root=directory)
            icon = panel.findChild(QLabel, "itemIcon_banana")
            self.assertFalse(icon.pixmap().isNull())
            self.assertEqual(icon.pixmap().size().width(), 64)
            self.assertEqual(icon.pixmap().size().height(), 32)
            missing = panel.findChild(QLabel, "itemIcon_muffle")
            self.assertEqual(missing.text(), "")
            self.assertEqual(missing.accessibleName(), "Muffle")
            self.assertEqual(missing.width(), 64)

    def test_all_text_resolves_to_readable_font_despite_parent_style(self):
        panel = self.panel(ProfileSnapshot(unlocked={"guardian_angel"}))
        expected = QFontInfo(self.font).family()
        for widget in panel.findChildren(QLabel) + panel.findChildren(QCheckBox):
            with self.subTest(widget=widget.objectName()):
                self.assertEqual(QFontInfo(widget.font()).family(), expected)
                self.assertEqual(widget.font().pixelSize(), 18)
                if isinstance(widget, QLabel):
                    self.assertEqual(widget.textFormat(), Qt.PlainText)
                    if not widget.objectName().startswith("itemIcon_"):
                        self.assertTrue(widget.wordWrap())

    def test_packaged_resource_root_and_shared_dialog_sprites_keep_callbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            icons = Path(directory) / "icons" / "items"
            icons.mkdir(parents=True)
            image = QPixmap(8, 8)
            image.fill(Qt.green)
            for item_id in ITEM_CATALOGUE:
                self.assertTrue(image.save(str(icons / f"{item_id}.png")))
            choose, reroll, skip, use = Mock(), Mock(), Mock(), Mock()
            with patch.object(sys, "_MEIPASS", directory, create=True):
                offer = DraftOffer("offer", "Kitchen", ("banana", "muffle", "spare_paper"))
                draft = ItemDraftDialog(offer, ITEM_CATALOGUE, 1, True,
                                        choose, reroll, skip, self.font, self.parent)
                inventory = InventoryPage((InventoryEntry("instance", "banana"),), ITEM_CATALOGUE,
                                          (("body", "Dave"),), use, body_font=self.font, parent=self.parent)
                for dialog, expected_count in ((draft, 3), (inventory, 1)):
                    sprites = [label for label in dialog.findChildren(QLabel)
                               if label.objectName().startswith("itemIcon_")]
                    self.assertEqual(len(sprites), expected_count)
                    for sprite in sprites:
                        self.assertFalse(sprite.pixmap().isNull())
                        self.assertEqual(sprite.pixmap().width(), 64)
                next(button for button in draft.findChildren(QPushButton) if button.text() == "Take").click()
                choose.assert_called_once_with("offer", 0)
                next(button for button in draft.findChildren(QPushButton) if button.text() == "Reroll").click()
                reroll.assert_called_once_with("offer", 0)
                next(button for button in inventory.findChildren(QPushButton) if button.text() == "Use").click()
                use.assert_called_once_with("instance", "body")
                draft.resolve()
                skip.assert_not_called()

    def test_card_text_and_checkbox_show_card_background_under_parent_black_style(self):
        panel = self.panel(ProfileSnapshot(unlocked={"guardian_angel"}))
        name = panel.findChild(QLabel, "itemName_banana")
        checkbox = panel.checkboxes["guardian_angel"]
        for widget in (name, checkbox):
            with self.subTest(widget=widget.objectName()):
                panel.scroll.ensureWidgetVisible(widget)
                self.app.processEvents()
                point = widget.mapTo(panel, QPoint(widget.width() - 2, widget.height() - 2))
                image = panel.grab().toImage()
                self.assertEqual(image.pixelColor(point).name(), "#252525")
        self.callback.side_effect = ProfileError("Could not save preferences.")
        checkbox.setChecked(False)
        self.app.processEvents()
        self.assertTrue(panel.error_label.isVisible())
        self.assertEqual(panel.error_label.palette().color(QPalette.WindowText).name(), "#ffb3a9")


if __name__ == "__main__":
    unittest.main()
