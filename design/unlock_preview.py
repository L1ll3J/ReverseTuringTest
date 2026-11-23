"""Visual-only preview of the production unlock page and shared overlay host.

Run python design/unlock_preview.py. Use --item ITEM_ID to change the preview,
--width/--height for the owner size, or --export PATH.png for an offline capture.
No gameplay module, profile, settings, API client, or audio device is loaded.
"""

import argparse
import os
from pathlib import Path
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from items import ITEM_CATALOGUE
from overlay_host import OverlayHost
from unlock_ui import UnlockPage, unlock_presentation


def create_preview(item_id="guardian_angel", width=800, height=600):
    """Use production components and canonical item data, without a game window."""
    if sys.platform == "win32" and QApplication.platformName() == "offscreen":
        QFont.insertSubstitution("Helvetica", "Segoe UI")
    window = QWidget()
    window.setWindowTitle("AlibAi unlock | Development preview")
    window.setStyleSheet("QWidget { background: #111111; color: #eeeeee; }")
    body = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    body.setPixelSize(18)
    font_id = QFontDatabase.addApplicationFont(str(SOURCE_ROOT / "fonts/PressStart2P-Regular.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    title = QFont(families[0] if families else body.family())
    title.setPixelSize(12)
    layout = QVBoxLayout(window)
    notice = QLabel("Unlock celebration | Development preview")
    notice.setTextFormat(Qt.PlainText)
    notice.setFont(body)
    layout.addWidget(notice)
    layout.addStretch()
    show = QPushButton("Show unlock again")
    show.setFont(body)
    layout.addWidget(show)
    host = OverlayHost(window, body_font=body, title_font=title)
    host.setStyleSheet("QDialog#gameOverlayHost { background: #2A2A2A; border: 3px solid white; } "
                      "QLabel { color: #E0E0E0; } "
                      "QToolButton { color: white; background: #333; border: 1px solid #777; padding: 8px; } "
                      "QToolButton:hover, QToolButton:focus { background: #444; border-color: white; }")
    window.resize(width, height)
    host.resize(min(620, width - 40), min(540, height - 40))

    def factory():
        return UnlockPage(unlock_presentation(item_id, (), ITEM_CATALOGUE), host.close_page,
                          body_font=body, title_font=title)

    show.clicked.connect(lambda: host.enqueue_unlock(factory, title="New Unlock!"))
    window.overlay_host = host
    window.show_unlock = lambda: host.enqueue_unlock(factory, title="New Unlock!")
    return window


def export_preview(window, destination):
    destination = Path(destination).resolve()
    if destination == SOURCE_ROOT or SOURCE_ROOT in destination.parents:
        raise ValueError("Export must be outside the source checkout.")
    if destination.suffix.lower() != ".png":
        raise ValueError("Export destination must end in .png.")
    # Child native dialogs are separate windows, so composite their actual grab.
    capture = window.grab()
    from PyQt5.QtGui import QPainter
    painter = QPainter(capture)
    host = window.overlay_host
    painter.drawPixmap(host.x() - window.x(), host.y() - window.y(), host.grab())
    painter.end()
    if not capture.save(str(destination), "PNG"):
        raise OSError("Could not save the preview screenshot.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", choices=tuple(ITEM_CATALOGUE), default="guardian_angel")
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args(argv)
    if args.export is not None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
    app = QApplication.instance() or QApplication([sys.argv[0]])
    window = create_preview(args.item, args.width, args.height)
    window.show()
    app.processEvents()
    host = window.overlay_host
    host.move(window.x() + (window.width() - host.width()) // 2,
              window.y() + (window.height() - host.height()) // 2)
    window.show_unlock()
    app.processEvents()
    if args.export is not None:
        try:
            export_preview(window, args.export)
        except (OSError, ValueError) as error:
            parser.exit(1, f"{error}\n")
        window.close()
        return 0
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
