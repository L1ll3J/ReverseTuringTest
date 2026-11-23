"""Standalone development preview of the real item catalogue.

Run ``python design/item_gallery.py`` or add ``--export PATH.png`` to save
the current viewport outside the source checkout. No game or profile is loaded.
"""

import argparse
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from item_catalogue_ui import ItemCataloguePanel
from items import ITEM_CATALOGUE


def create_gallery():
    """Build real catalogue widgets without a profile store or gameplay state."""
    if sys.platform == "win32" and QApplication.platformName() == "offscreen":
        QFont.insertSubstitution("Helvetica", "Segoe UI")
    window = QWidget()
    window.setObjectName("itemGallery")
    window.setWindowTitle("AlibAi item catalogue | Development preview")
    window.setStyleSheet("QWidget { background: #1e1e1e; }")
    font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    font.setPixelSize(18)
    font_id = QFontDatabase.addApplicationFont(str(SOURCE_ROOT / "fonts" / "PressStart2P-Regular.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    title_font = QFont(families[0] if families else "Sans-Serif")
    title_font.setPixelSize(12)
    layout = QVBoxLayout(window)
    notice = QLabel(
        f"Development preview: all {len(ITEM_CATALOGUE)} catalogue definitions. All items are shown "
        "as unlocked; pool controls are disabled. No profile is loaded or saved. "
        "Catalogue presence does not mean every effect is wired into gameplay."
    )
    notice.setObjectName("itemGalleryNotice")
    notice.setTextFormat(Qt.PlainText)
    notice.setWordWrap(True)
    notice.setFont(font)
    notice.setStyleSheet("color: #dedede; background: transparent;")
    layout.addWidget(notice)
    profile = SimpleNamespace(unlocked=frozenset(ITEM_CATALOGUE), disabled_items=frozenset())
    panel = ItemCataloguePanel(ITEM_CATALOGUE, profile, {}, lambda *_: profile, font,
                               title_font=title_font)
    for checkbox in panel.checkboxes.values():
        checkbox.setEnabled(False)
        checkbox.setToolTip("Pool preferences are unavailable in the development preview.")
    layout.addWidget(panel, 1)
    window.catalogue = panel
    window.resize(1000, 800)
    return window


def export_viewport(window, destination):
    """Save a PNG viewport outside the source tree, raising on failed writes."""
    destination = Path(destination).resolve()
    if destination == SOURCE_ROOT or SOURCE_ROOT in destination.parents:
        raise ValueError("Export must be outside the source checkout.")
    if destination.suffix.lower() != ".png":
        raise ValueError("Export destination must end in .png.")
    if not window.grab().save(str(destination), "PNG"):
        raise OSError("Could not save the gallery screenshot.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, help="Save the initial viewport as a PNG outside the source checkout")
    args = parser.parse_args(argv)
    if args.export is not None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or QApplication([sys.argv[0]])
    window = create_gallery()
    window.show()
    if args.export is not None:
        app.processEvents()
        try:
            export_viewport(window, args.export)
        except (OSError, ValueError) as error:
            parser.exit(1, f"{error}\n")
        window.close()
        return 0
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
