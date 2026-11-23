"""Read-only unlock presentation built from the game's canonical content metadata."""

from dataclasses import dataclass
from pathlib import Path
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget


@dataclass(frozen=True)
class UnlockPresentation:
    content_id: str
    category: str
    name: str
    description: str
    image: str


def unlock_presentation(content_id, weapons, items):
    """Resolve only newly unlocked IDs supplied by the profile update."""
    if content_id in items:
        item = items[content_id]
        return UnlockPresentation(content_id, "Item", item.name, item.description,
                                  f"icons/items/{content_id}.png")
    weapon = next(weapon for weapon in weapons if weapon["id"] == content_id)
    return UnlockPresentation(content_id, "Weapon", weapon["name"], weapon["desc"], weapon["image"])


class _UnlockSprite(QWidget):
    def __init__(self, presentation, parent=None):
        super().__init__(parent)
        self.setObjectName("unlockSprite")
        self.setAccessibleName(presentation.name + " sprite")
        self.setFixedSize(200, 168)
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        self.pixmap = QPixmap(str(root / presentation.image))
        if not self.pixmap.isNull():
            self.pixmap = self.pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.FastTransformation)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(QColor("#e8e8e8"), 3))
        # Sparse square sparkles give the sprite room without animation or colour panels.
        for x, y, arm in ((22, 40, 6), (174, 24, 5), (180, 128, 7), (20, 142, 4)):
            painter.drawLine(x - arm, y, x + arm, y)
            painter.drawLine(x, y - arm, x, y + arm)
        if not self.pixmap.isNull():
            painter.drawPixmap((self.width() - self.pixmap.width()) // 2,
                               (self.height() - self.pixmap.height()) // 2, self.pixmap)
        painter.end()


class UnlockPage(QWidget):
    """A host-owned celebration page, with no dialog, timer, or progression writes."""

    def __init__(self, presentation, on_continue, *, body_font=None, title_font=None, parent=None):
        super().__init__(parent)
        self.presentation = presentation
        self.setObjectName("unlockPage")
        self.setAccessibleName("New unlock: " + presentation.name)
        self.setAttribute(Qt.WA_StyledBackground, True)
        body = QFont(body_font or QFontDatabase.systemFont(QFontDatabase.GeneralFont))
        if body_font is None:
            body.setPixelSize(18)
        title = QFont(title_font or body)
        title.setPixelSize(16)
        self.setStyleSheet('''
            QWidget#unlockPage { background: #171717; color: white; }
            QWidget#unlockPage QLabel { color: #eeeeee; background: transparent; border: none; }
            QWidget#unlockPage QScrollArea { background: transparent; border: none; }
            QWidget#unlockContents { background: transparent; }
            QWidget#unlockPage QPushButton {
                color: white; background: #242424; border: 2px solid #bbbbbb;
                padding: 12px 20px; min-height: 24px;
            }
            QWidget#unlockPage QPushButton:hover { background: #383838; border-color: white; }
            QWidget#unlockPage QPushButton:focus { border: 3px solid white; background: #383838; }
            QWidget#unlockPage QPushButton:pressed { background: #505050; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 8, 20, 16)
        layout.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setAccessibleName("Unlock details")
        contents = QWidget()
        contents.setObjectName("unlockContents")
        details = QVBoxLayout(contents)
        details.setContentsMargins(8, 8, 8, 8)
        details.setSpacing(16)

        def label(text, name, font):
            result = QLabel(text)
            result.setObjectName(name)
            result.setTextFormat(Qt.PlainText)
            result.setWordWrap(True)
            result.setAlignment(Qt.AlignCenter)
            result.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            # Explicit per-label rules preserve font roles under the game's global pixel rule.
            family = font.family().replace('\\', '\\\\').replace('"', '\\"')
            size = f"{font.pixelSize()}px" if font.pixelSize() > 0 else f"{font.pointSizeF():g}pt"
            result.setFont(font)
            result.setStyleSheet(f'font-family: "{family}"; font-size: {size};')
            result.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            details.addWidget(result)
            return result

        label(presentation.category, "unlockCategory", body)
        self.sprite = _UnlockSprite(presentation)
        details.addWidget(self.sprite, 0, Qt.AlignHCenter)
        self.name_label = label(presentation.name, "unlockName", title)
        self.effect_label = label(presentation.description, "unlockEffect", body)
        label("Available next run.", "unlockAvailability", body)
        details.addStretch(1)
        self.scroll.setWidget(contents)
        layout.addWidget(self.scroll, 1)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("unlockContinue")
        self.continue_button.setAccessibleName("Continue after unlock")
        controls = QFont(title_font if title_font is not None else body)
        self.continue_button.setFont(controls)
        family = controls.family().replace('\\', '\\\\').replace('"', '\\"')
        size = f"{controls.pixelSize()}px" if controls.pixelSize() > 0 else f"{controls.pointSizeF():g}pt"
        self.continue_button.setStyleSheet(f'font-family: "{family}"; font-size: {size};')
        self.continue_button.setFocusPolicy(Qt.StrongFocus)
        self.continue_button.setDefault(True)
        self.continue_button.clicked.connect(on_continue)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocusProxy(self.continue_button)
        layout.addWidget(self.continue_button)
