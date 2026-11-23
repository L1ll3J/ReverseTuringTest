"""Standalone house interaction study. No gameplay, arrivals, models or saves."""

import argparse
import os
from pathlib import Path
import sys

from PyQt5.QtCore import QEventLoop, Qt, QTimer
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from house_view import HouseView, RoomObservation, unknown_observations


def example_observations():
    records = list(unknown_observations())
    records[2] = RoomObservation("library", known_names=("Jerry",),
                                 unknown_remainder=True, observed_at="00:40", light_on=True)
    records[4] = RoomObservation("cellar", available=False, observed_at="00:35", light_on=False)
    return tuple(records)


def load_fonts(assets):
    font_path = assets / "fonts/PressStart2P-Regular.ttf"
    if not font_path.is_file():
        raise ValueError(f"Missing pixel font: {font_path}")
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    if not families:
        raise ValueError(f"Unreadable pixel font: {font_path}")
    for filename in ("segoeui.ttf", "arial.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    available = QFontDatabase().families()
    body_family = next((name for name in ("Segoe UI", "Arial", "DejaVu Sans", "Liberation Sans")
                        if name in available), None)
    if body_family is None:
        raise ValueError("A readable body font such as Segoe UI, Arial or DejaVu Sans is required")
    body = QFont(body_family)
    body.setPixelSize(16)
    heading = QFont(families[0])
    heading.setPixelSize(10)
    return body, heading


class HouseStudy(QWidget):
    def __init__(self, artwork, assets):
        super().__init__()
        body, heading = load_fonts(assets)
        self.setFont(body)
        self.setWindowTitle("AlibAi | Standalone house interaction study | No gameplay")
        self.resize(1280, 800)
        self.setMinimumSize(800, 600)
        self.setStyleSheet("QWidget { background:#000; color:#ddd; } "
                           "QComboBox { border:1px solid #777; padding:5px 8px; } "
                           "QComboBox:focus { border-color:#eee; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 10)
        top = QHBoxLayout()
        title = QLabel("HOUSE")
        title.setFont(heading)
        top.addWidget(title)
        top.addStretch()
        self.projection = QComboBox()
        self.projection.setAccessibleName("Illustrative public observation view")
        self.projection.addItems(("Unknown rooms", "Illustrative observations"))
        top.addWidget(self.projection)
        outer.addLayout(top)
        self.house = HouseView(artwork, body_font=body, heading_font=heading)
        outer.addWidget(self.house, 1)
        self.status = QLabel("Tab or arrows choose a room. Enter or Space selects it.")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.projection.currentIndexChanged.connect(self.change_projection)
        self.house.room_selected.connect(lambda room_id: self.status.setText(
            "Selected: " + self.house.buttons[room_id].text() + ". No entry action is performed in this study."))

    def change_projection(self, index):
        self.house.set_observations(example_observations() if index else unknown_observations())


def export_study(window, destination):
    destination = Path(destination).expanduser().resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError("Export outside the source worktree")
    destination.mkdir(parents=True, exist_ok=True)
    window.show()
    app = QApplication.instance()

    def settle():
        app.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(30, loop.quit)
        loop.exec_()

    for width, height in ((800, 600), (1280, 800), (2560, 1440), (3440, 1440)):
        window.resize(width, height)
        for index, name, focus in ((0, "unknown", "attic"), (1, "observed", "library")):
            window.projection.setCurrentIndex(index)
            window.house.buttons[focus].setFocus()
            settle()
            path = destination / f"house-{name}-{width}x{height}.png"
            if not window.grab().save(str(path)):
                raise ValueError(f"Unable to save screenshot: {path}")
    window.close()
    print(f"Screenshots: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=ROOT)
    parser.add_argument("--artwork", type=Path, default=ROOT / "design/house-cutaway-v1.png")
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    if args.export:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    try:
        window = HouseStudy(args.artwork, args.assets.resolve())
        if args.export:
            export_study(window, args.export)
            return 0
    except ValueError as error:
        parser.error(str(error))
    app.setQuitOnLastWindowClosed(True)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
