"""Real catalogue preview coverage without game startup or profile writes."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt5.QtGui import QFontInfo, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel

from design.item_gallery import SOURCE_ROOT, create_gallery, export_viewport
from item_catalogue_ui import ItemCataloguePanel
from item_ui import InlineItemDraftPage, InventoryPage
from items import ITEM_CATALOGUE, DraftOffer, InventoryEntry


class ItemGalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["item-gallery-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.window = create_gallery()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_all_real_rows_have_readable_effects_and_approved_optional_flavour_at_both_sizes(self):
        self.assertIsInstance(self.window.catalogue, ItemCataloguePanel)
        panel = self.window.catalogue
        names = [label for label in panel.findChildren(QLabel)
                 if label.objectName().startswith("itemName_")]
        self.assertEqual(len(names), len(ITEM_CATALOGUE))
        for name in names:
            self.assertEqual(QFontInfo(name.font()).family(), "Press Start 2P")
            self.assertEqual(QFontInfo(name.font()).pixelSize(), 12)
        for width, height in ((800, 600), (1920, 1080)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.window.show()
                self.app.processEvents()
                self.assertEqual(self.window.width(), width)
                self.assertEqual(panel.scroll.horizontalScrollBar().maximum(), 0)
                for item_id, item in ITEM_CATALOGUE.items():
                    detail = panel.findChild(QLabel, f"itemDescription_{item_id}")
                    self.assertEqual(detail.text(), item.description)
                    self.assertEqual(detail.textFormat(), Qt.PlainText)
                    self.assertEqual(QFontInfo(detail.font()).pixelSize(), 18)
                    self.assertGreaterEqual(detail.height(), detail.heightForWidth(detail.width()))
                    panel.scroll.ensureWidgetVisible(detail)
                    self.app.processEvents()
                    point = detail.mapTo(panel.scroll.viewport(), QPoint(0, 0))
                    self.assertGreaterEqual(point.x(), 0)
                    self.assertGreaterEqual(point.y(), 0)
                    self.assertLessEqual(point.x() + detail.width(), panel.scroll.viewport().width())
                    self.assertLessEqual(point.y() + detail.height(), panel.scroll.viewport().height())
                    flavour = panel.findChild(QLabel, f"itemFlavour_{item_id}")
                    if item.flavour:
                        self.assertEqual(flavour.text(), item.flavour)
                        self.assertEqual(flavour.textFormat(), Qt.PlainText)
                        self.assertGreaterEqual(flavour.height(), flavour.heightForWidth(flavour.width()))
                    else:
                        self.assertIsNone(flavour)
                self.assertTrue(all(not box.isEnabled() for box in panel.checkboxes.values()))

    def test_export_checks_source_tree_png_and_failed_save(self):
        self.window.show()
        self.app.processEvents()
        with self.assertRaisesRegex(ValueError, "outside"):
            export_viewport(self.window, SOURCE_ROOT / "preview.png")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.png"
            export_viewport(self.window, path)
            self.assertFalse(QPixmap(str(path)).isNull())
            with self.assertRaisesRegex(ValueError, "png"):
                export_viewport(self.window, path.with_suffix(".jpg"))
            with self.assertRaisesRegex(OSError, "save"):
                export_viewport(self.window, Path(directory) / "missing" / "preview.png")
            with patch.object(self.window, "grab") as grab:
                grab.return_value.save.return_value = False
                with self.assertRaises(OSError):
                    export_viewport(self.window, path)

    def test_inventory_and_inline_effects_with_flavour_remain_reachable_at_both_sizes(self):
        entries = tuple(InventoryEntry(item_id, item_id) for item_id in ITEM_CATALOGUE)
        inventory = InventoryPage(entries, ITEM_CATALOGUE, (), lambda *_: None)
        offer = DraftOffer("preview-test", "Kitchen", ("love_potion", "lucky_coin", "watch_this_drive"))
        draft = InlineItemDraftPage(offer, ITEM_CATALOGUE, lambda *_: None, lambda *_: None,
                                   lambda *_: None, reroll_count=1, can_reroll=True)
        try:
            for page, prefix in ((inventory, "inventory"), (draft, "inlineItem")):
                for size in ((800, 600), (1920, 1080)):
                    with self.subTest(page=prefix, size=size):
                        page.resize(*size)
                        page.show()
                        self.app.processEvents()
                        self.assertEqual(page.width(), size[0])
                        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
                        for label in page.findChildren(QLabel):
                            if not label.objectName().startswith((prefix + "Description_", prefix + "Flavour_")):
                                continue
                            self.assertEqual(label.textFormat(), Qt.PlainText)
                            self.assertEqual(QFontInfo(label.font()).pixelSize(), 18)
                            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                            page.scroll.ensureWidgetVisible(label)
                            self.app.processEvents()
                            point = label.mapTo(page.scroll.viewport(), QPoint(0, 0))
                            self.assertGreaterEqual(point.x(), 0)
                            self.assertGreaterEqual(point.y(), 0)
                            self.assertLessEqual(point.x() + label.width(), page.scroll.viewport().width())
                            self.assertLessEqual(point.y() + label.height(), page.scroll.viewport().height())
                page.hide()
        finally:
            for page in (inventory, draft):
                page.close()
                page.deleteLater()

    def test_standalone_import_does_not_start_game_model_or_profile(self):
        code = (
            "import sys; from unittest.mock import patch; "
            "from PyQt5.QtWidgets import QApplication; "
            "from design.item_gallery import create_gallery; "
            "from progression import ProfileStore; app=QApplication([]); "
            "guard=patch.object(ProfileStore, 'read', side_effect=AssertionError('profile read')); "
            "guard.start(); window=create_gallery(); "
            "assert not ({'main', 'ai_runtime', 'openai', 'content'} & sys.modules.keys()); "
            "window.close()"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=SOURCE_ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_export_cli_selects_offscreen_and_returns_failure_for_unsaved_png(self):
        environment = dict(os.environ, QT_QPA_PLATFORM="invalid-preview-platform")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cli-preview.png"
            command = [sys.executable, str(SOURCE_ROOT / "design" / "item_gallery.py"), "--export"]
            result = subprocess.run([*command, str(destination)], cwd=SOURCE_ROOT,
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(QPixmap(str(destination)).isNull())
            result = subprocess.run([*command, str(Path(directory) / "missing" / "preview.png")],
                                    cwd=SOURCE_ROOT, env=environment, capture_output=True,
                                    text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Could not save", result.stderr)


if __name__ == "__main__":
    unittest.main()
