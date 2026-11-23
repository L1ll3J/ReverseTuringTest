"""Small, state-driven inventory and room-offer widgets."""

from pathlib import Path
import sys

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen, QPixmap, QPolygon
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget,
)


def item_sprite(item_id, parent=None, *, icon_root=None):
    """Return a bounded sprite label using source or packaged resources."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    directory = Path(icon_root) if icon_root is not None else root / "icons" / "items"
    label = QLabel(parent)
    label.setObjectName(f"itemIcon_{item_id}")
    label.setTextFormat(Qt.PlainText)
    label.setFixedSize(64, 64)
    label.setAlignment(Qt.AlignCenter)
    pixmap = QPixmap(str(directory / f"{item_id}.png"))
    if not pixmap.isNull():
        label.setPixmap(pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.FastTransformation))
    return label


class ItemDraftDialog(QDialog):
    def __init__(self, offer, definitions, rerolls_remaining, can_reroll,
                 choose, reroll, skip, body_font, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Something left behind")
        self.setModal(True)
        self.setFont(body_font)
        self.setStyleSheet(f'QWidget {{ font-family: "{body_font.family()}"; font-size: 18px; }} QPushButton:disabled {{ color: #777; }}')
        self.setMinimumWidth(520)
        screen = self.screen().availableGeometry()
        self.resize(min(720, int(screen.width() * 0.85)), min(600, int(screen.height() * 0.85)))
        self._skip = skip
        self._resolved = False
        layout = QVBoxLayout(self)
        heading = QLabel(f"Your first visit to the {offer.room_id}")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        intro = QLabel("Take one item to keep for a later discussion, or leave them behind.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        for slot, item_id in enumerate(offer.options):
            definition = definitions[item_id]
            card = QFrame()
            card.setObjectName("itemOfferCard")
            card.setStyleSheet("QFrame#itemOfferCard { border: 1px solid #555; padding: 8px; }")
            row = QHBoxLayout(card)
            row.addWidget(item_sprite(item_id), 0, Qt.AlignTop)
            description = QLabel(f"{definition.name}\n{definition.description}")
            description.setWordWrap(True)
            row.addWidget(description, 1)
            take = QPushButton("Take")
            take.clicked.connect(lambda _checked=False, index=slot: choose(offer.offer_id, index))
            row.addWidget(take)
            roll = QPushButton("Reroll")
            roll.setEnabled(bool(rerolls_remaining and can_reroll))
            roll.setToolTip("One slot reroll per run. An alternative item must be in your pool.")
            roll.clicked.connect(lambda _checked=False, index=slot: reroll(offer.offer_id, index))
            row.addWidget(roll)
            layout.addWidget(card)
        leave = QPushButton("Leave these behind")
        leave.clicked.connect(self.reject)
        layout.addWidget(leave)

    def resolve(self):
        self._resolved = True
        self.accept()

    def reject(self):
        if not self._resolved:
            self._skip()


class _TargetComboBox(QComboBox):
    """Keep native combo-box behavior with a contrasting game-style chevron."""

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, self)
        center = arrow.center()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(QColor("#e0e0e0" if self.isEnabled() else "#777777"), 2))
        painter.drawPolyline(QPolygon((QPoint(center.x() - 4, center.y() - 2),
                                       QPoint(center.x(), center.y() + 2),
                                       QPoint(center.x() + 4, center.y() - 2))))
        painter.end()


class InventoryPage(QWidget):
    """Inventory content for the game's existing overlay, with no dialog of its own.

    Entries and eligible targets are caller-owned snapshots. The caller also
    applies item effects and replaces this page after a successful use.
    """

    def __init__(self, entries, definitions, targets, on_use, *, can_use=True,
                 body_font=None, title_font=None, parent=None):
        super().__init__(parent)
        if not callable(on_use):
            raise TypeError("Inventory use requires a callback")
        self.setObjectName("inventoryPage")
        self.setAccessibleName("Inventory items")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._body_font = QFont(body_font if body_font is not None else
                                QFontDatabase.systemFont(QFontDatabase.GeneralFont))
        if body_font is None:
            self._body_font.setPixelSize(18)
        self._title_font = QFont(title_font if title_font is not None else self._body_font)
        if title_font is None:
            self._title_font.setBold(True)
        self.setFont(self._body_font)
        self.setStyleSheet('''
            QWidget#inventoryPage { background: #1e1e1e; color: #e0e0e0; }
            QFrame#inventoryCard { background: #252525; border: 1px solid #555; }
            QWidget#inventoryContents { background: transparent; }
            QWidget#inventoryPage QLabel { color: #e0e0e0; background: transparent; border: none; }
            QWidget#inventoryPage QScrollArea { background: transparent; border: none; }
            QWidget#inventoryPage QComboBox {
                color: #e0e0e0; background: #1b1b1b; border: 1px solid #888;
                padding: 7px 8px; min-height: 24px;
            }
            QWidget#inventoryPage QComboBox:hover { border-color: #dedede; }
            QWidget#inventoryPage QComboBox:focus { border: 2px solid #dedede; }
            QWidget#inventoryPage QComboBox::drop-down {
                width: 28px; border-left: 1px solid #888;
            }
            QWidget#inventoryPage QComboBox::down-arrow { image: none; }
            QWidget#inventoryPage QComboBox QAbstractItemView {
                color: #e0e0e0; background: #252525;
                selection-color: white; selection-background-color: #444;
            }
            QWidget#inventoryPage QPushButton {
                color: #e0e0e0; background: #2a2a2a; border: 1px solid #777;
                padding: 9px 16px; min-height: 24px;
            }
            QWidget#inventoryPage QPushButton:hover { background: #3a3a3a; border-color: #dedede; }
            QWidget#inventoryPage QPushButton:focus { border: 2px solid #dedede; }
            QWidget#inventoryPage QPushButton:pressed { background: #444; }
            QWidget#inventoryPage QPushButton:disabled { color: #777; border-color: #555; }
        ''')
        self.target_boxes = {}
        self.use_buttons = {}
        target_snapshot = tuple(targets)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("inventoryContents")
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(12)
        entries = tuple(entries)
        if not entries:
            empty = self._label("Your inventory is empty.", self._body_font)
            empty.setObjectName("inventoryEmpty")
            rows.addWidget(empty)
        for entry in entries:
            definition = definitions[entry.item_id]
            card = QFrame()
            card.setObjectName("inventoryCard")
            row = QVBoxLayout(card)
            row.setContentsMargins(14, 14, 14, 14)
            row.setSpacing(12)
            description = QHBoxLayout()
            description.setSpacing(14)
            sprite = item_sprite(entry.item_id)
            sprite.setAccessibleName(definition.name)
            description.addWidget(sprite, 0, Qt.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(8)
            name = self._label(definition.name, self._title_font)
            name.setObjectName(f"inventoryName_{entry.instance_id}")
            text.addWidget(name)
            detail = self._label(definition.description, self._body_font)
            detail.setObjectName(f"inventoryDescription_{entry.instance_id}")
            text.addWidget(detail)
            description.addLayout(text, 1)
            row.addLayout(description)
            controls = QHBoxLayout()
            target = None
            if definition.target_kind == "living_npc":
                target = _TargetComboBox()
                target.setObjectName(f"inventoryTarget_{entry.instance_id}")
                target.setAccessibleName(f"Recipient for {definition.name}")
                target.setFocusPolicy(Qt.StrongFocus)
                self._font(target, self._body_font)
                for body_id, name in target_snapshot:
                    target.addItem(name, body_id)
                self.target_boxes[entry.instance_id] = target
                controls.addWidget(target, 1)
            else:
                controls.addStretch()
            button = QPushButton("Use")
            button.setObjectName(f"inventoryUse_{entry.instance_id}")
            button.setAccessibleName(f"Use {definition.name}")
            button.setFocusPolicy(Qt.StrongFocus)
            button.setAutoDefault(False)
            self._font(button, self._title_font)
            button.setEnabled(can_use and (target is None or target.count() > 0))
            button.clicked.connect(lambda _checked=False, instance=entry.instance_id, box=target:
                                   on_use(instance, box.currentData() if box is not None else None))
            self.use_buttons[entry.instance_id] = button
            controls.addWidget(button)
            row.addLayout(controls)
            rows.addWidget(card)
        rows.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll)

    @staticmethod
    def _font(widget, font):
        widget.setFont(font)
        family = font.family().replace("\\", "\\\\").replace('"', '\\"')
        size = f"{font.pixelSize()}px" if font.pixelSize() > 0 else f"{font.pointSizeF():g}pt"
        weight = "bold" if font.bold() else "normal"
        style = "italic" if font.italic() else "normal"
        widget.setStyleSheet(f'font-family: "{family}"; font-size: {size}; font-weight: {weight}; font-style: {style};')

    def _label(self, text, font):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._font(label, font)
        return label
