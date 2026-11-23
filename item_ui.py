"""Small, state-driven inventory and room-offer widgets."""

from pathlib import Path
import sys

from PyQt5.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen, QPixmap, QPolygon
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget,
)


def item_sprite(item_id, parent=None, *, icon_root=None, size=64):
    """Return a bounded sprite label using source or packaged resources."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    directory = Path(icon_root) if icon_root is not None else root / "icons" / "items"
    label = QLabel(parent)
    label.setObjectName(f"itemIcon_{item_id}")
    label.setTextFormat(Qt.PlainText)
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignCenter)
    pixmap = QPixmap(str(directory / f"{item_id}.png"))
    if not pixmap.isNull():
        label.setPixmap(pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.FastTransformation))
    return label


RARITY_COLORS = {"green": "#91bc91", "blue": "#9dbfe5", "purple": "#c5a5dc", "gold": "#dfc383"}


class ItemCardButton(QPushButton):
    """Native keyboard activation with a clickable illustrated surface."""
    inspected = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def sizeHint(self):
        return self.layout().sizeHint() if self.layout() else super().sizeHint()

    def minimumSizeHint(self):
        return QSize(0, self.sizeHint().height())

    def enterEvent(self, event):
        self.inspected.emit()
        super().enterEvent(event)

    def focusInEvent(self, event):
        self.inspected.emit()
        super().focusInEvent(event)


class ResponsiveItemGrid(QWidget):
    """Reflow existing cards without replacing their signal connections."""
    def __init__(self, minimum_card_width=220, maximum_columns=None, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._minimum_card_width = minimum_card_width
        self._maximum_columns = maximum_columns
        self.cards = []
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)

    def add_card(self, card):
        self.cards.append(card)
        self._arrange()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def _arrange(self):
        spacing = self.grid.spacing()
        columns = max(1, (self.width() + spacing) // (self._minimum_card_width + spacing))
        columns = min(columns, self._maximum_columns or max(1, len(self.cards)))
        if columns == self.columns and self.grid.count() == len(self.cards):
            return
        old_columns = self.columns
        self.columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(max(columns, old_columns)):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        self.updateGeometry()


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


class GameComboBox(QComboBox):
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
                 body_font=None, title_font=None, target_options=None, unavailable=None, parent=None):
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
        self.message_boxes = {}
        self.room_boxes = {}
        self.use_buttons = {}
        target_snapshot = tuple(targets)
        options = {"living_npc": target_snapshot, **(target_options or {})}
        unavailable = unavailable or {}
        self.feedback_label = self._label("", self._body_font)
        self.feedback_label.setObjectName("inventoryFeedback")
        self.feedback_label.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.feedback_label)
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
            if definition.flavour:
                flavour = self._label(definition.flavour, self._body_font)
                flavour.setObjectName(f"inventoryFlavour_{entry.instance_id}")
                text.addWidget(flavour)
            description.addLayout(text, 1)
            row.addLayout(description)
            controls = QHBoxLayout()
            target = None
            if definition.target_kind != "self":
                target = GameComboBox()
                target.setObjectName(f"inventoryTarget_{entry.instance_id}")
                target.setAccessibleName(f"Recipient for {definition.name}")
                target.setFocusPolicy(Qt.StrongFocus)
                self._font(target, self._body_font)
                for body_id, name in options.get("living_npc" if definition.target_kind == "living_npc_room" else definition.target_kind, ()):
                    target.addItem(name, body_id)
                self.target_boxes[entry.instance_id] = target
                controls.addWidget(target, 1)
            else:
                controls.addStretch()
            room_target = None
            if definition.target_kind == "living_npc_room":
                room_target = GameComboBox()
                room_target.setObjectName(f"inventoryRoom_{entry.instance_id}")
                room_target.setAccessibleName(f"Room for {definition.name}")
                self._font(room_target, self._body_font)
                for room_id, name in options.get("room", ()):
                    room_target.addItem(name, room_id)
                self.room_boxes[entry.instance_id] = room_target
                controls.addWidget(room_target, 1)
            button = QPushButton("Use")
            button.setObjectName(f"inventoryUse_{entry.instance_id}")
            button.setAccessibleName(f"Use {definition.name}")
            button.setFocusPolicy(Qt.StrongFocus)
            button.setAutoDefault(False)
            self._font(button, self._title_font)
            button.setEnabled(can_use and (target is None or target.count() > 0)
                              and (room_target is None or room_target.count() > 0))
            self.use_buttons[entry.instance_id] = button
            controls.addWidget(button)
            row.addLayout(controls)
            message = None
            if definition.requires_message:
                message = QPlainTextEdit()
                message.setObjectName(f"inventoryMessage_{entry.instance_id}")
                message.setAccessibleName(f"Message for {definition.name}")
                message.setPlaceholderText("Write your message (up to 2000 characters)")
                message.setMaximumHeight(110)
                self._font(message, self._body_font)
                message.setStyleSheet(message.styleSheet() + "background:#1b1b1b; color:#e0e0e0; border:1px solid #888;")
                row.insertWidget(row.count() - 1, message)
                self.message_boxes[entry.instance_id] = message
            reason = unavailable.get(entry.item_id)
            if reason:
                button.setEnabled(False)
                button.setToolTip(reason)
                row.addWidget(self._label(reason, self._body_font))
            def activate(_checked=False, instance=entry.instance_id, box=target,
                         editor=message, kind=definition.target_kind, room_box=room_target):
                kwargs = {}
                if editor is not None:
                    text = editor.toPlainText()
                    if not text.strip() or len(text) > 2000:
                        self.show_feedback("Write a message between one and 2000 characters.")
                        return
                    kwargs["message"] = text
                target_id = box.currentData() if box is not None else None
                if room_box is not None:
                    kwargs["room_id"] = room_box.currentData()
                if kind == "room":
                    kwargs["room_id"] = target_id
                    target_id = None
                on_use(instance, target_id, **kwargs)
            button.clicked.connect(activate)
            rows.addWidget(card)
        rows.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll)

    def show_feedback(self, message):
        self.feedback_label.setText(message)
        self.feedback_label.show()

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


class InlineItemDraftPage(QWidget):
    """Room reward content for an existing layout, with caller-owned draft rules.

    Each page represents one immutable offer snapshot. Callbacks receive that
    exact offer ID and, for item actions, its slot index. The caller applies the
    action and replaces the page when the offer changes.
    """

    def __init__(self, offer, definitions, on_take, on_reroll, on_leave, *,
                 reroll_count, can_reroll, picks_remaining=1,
                 body_font=None, title_font=None, parent=None):
        super().__init__(parent)
        if not all(callable(callback) for callback in (on_take, on_reroll, on_leave)):
            raise TypeError("Room rewards require take, reroll and leave callbacks")
        self.setObjectName("inlineItemDraftPage")
        self.setAccessibleName(f"Items found in {offer.room_id}")
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
            QWidget#inlineItemDraftPage { background: #000000; color: #e0e0e0; }
            QWidget#inlineItemContents { background: #000000; }
            QWidget#inlineItemDraftPage QLabel {
                color: #e0e0e0; background: transparent; border: none;
            }
            QWidget#inlineItemDraftPage QScrollArea { background: transparent; border: none; }
            QWidget#inlineItemDraftPage QPushButton {
                color: #e0e0e0; background: #0b0b0b; border: 1px solid #444;
                padding: 9px 16px; min-height: 24px;
            }
            QWidget#inlineItemDraftPage QPushButton:hover { background: #161616; border-color: #dedede; }
            QWidget#inlineItemDraftPage QPushButton:focus { border: 2px solid #dedede; }
            QWidget#inlineItemDraftPage QPushButton:pressed { background: #202020; }
            QWidget#inlineItemDraftPage QPushButton:disabled { color: #777; border-color: #555; }
        ''')
        self.take_buttons = {}
        self.reroll_buttons = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("inlineItemContents")
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(12)
        heading = QHBoxLayout()
        title = self._label(f"Found in the {offer.room_id}", self._title_font)
        title.setObjectName("inlineItemHeading")
        heading.addWidget(title, 1)
        self.help_button = self._button("?", "inlineItemHelpToggle", "Help with room items")
        self.help_button.setCheckable(True)
        heading.addWidget(self.help_button, 0, Qt.AlignTop)
        rows.addLayout(heading)
        count = "item" if picks_remaining == 1 else "items"
        status = self._label(
            f"Choose {picks_remaining} {count}. Rerolls remaining: {reroll_count}.",
            self._body_font,
        )
        status.setObjectName("inlineItemStatus")
        rows.addWidget(status)
        self.help_label = self._label(
            "Take an item to keep for a later discussion. Reroll replaces one item "
            "when another item is available. Leave these behind to continue without "
            "taking the remaining items.", self._body_font,
        )
        self.help_label.setObjectName("inlineItemHelp")
        self.help_label.hide()
        self.help_button.toggled.connect(self.help_label.setVisible)
        rows.addWidget(self.help_label)
        offer_id = offer.offer_id
        self.offer_grid = ResponsiveItemGrid(220, maximum_columns=3)
        rows.addWidget(self.offer_grid)
        for slot, item_id in enumerate(offer.options):
            definition = definitions[item_id]
            take = ItemCardButton()
            InventoryPage._font(take, self._title_font)
            take.setObjectName(f"inlineItemTake_{slot}")
            take.setAccessibleName(f"Take {definition.name}")
            take.setAccessibleDescription(definition.description + " " + definition.flavour)
            take.setMinimumHeight(380)
            color = RARITY_COLORS.get(getattr(definition, "rarity", "green"), RARITY_COLORS["green"])
            selector = f"QPushButton#{take.objectName()}"
            take.setStyleSheet(selector + " { " + take.styleSheet() + f" padding: 0; background: #0b0b0b; border: 1px solid #333; border-top: 2px solid {color}; }}"
                               + selector + ":hover, " + selector + ":focus { border: 2px solid white; background: #161616; }"
                               + selector + ":pressed { background: #202020; }")
            contents = QVBoxLayout(take)
            contents.setContentsMargins(24, 24, 24, 20)
            contents.setSpacing(12)
            sprite = item_sprite(item_id, size=128)
            sprite.setAttribute(Qt.WA_TransparentForMouseEvents)
            sprite.setAccessibleName(definition.name)
            contents.addWidget(sprite, 0, Qt.AlignHCenter)
            name = self._label(definition.name, self._title_font)
            name.setObjectName(f"inlineItemName_{slot}")
            name.setStyleSheet(name.styleSheet() + f"color: {color};")
            name.setAlignment(Qt.AlignCenter)
            contents.addWidget(name)
            detail = self._label(definition.description, self._body_font)
            detail.setObjectName(f"inlineItemDescription_{slot}")
            contents.addWidget(detail)
            if definition.flavour:
                flavour = self._label(definition.flavour, self._body_font)
                flavour.setObjectName(f"inlineItemFlavour_{slot}")
                flavour.setStyleSheet(flavour.styleSheet() + "color: #999999;")
                contents.addWidget(flavour)
            for label in take.findChildren(QLabel):
                label.setAttribute(Qt.WA_TransparentForMouseEvents)
                label.setTextInteractionFlags(Qt.NoTextInteraction)
                label.setFocusPolicy(Qt.NoFocus)
            contents.addStretch()
            take.clicked.connect(lambda _checked=False, index=slot: on_take(offer_id, index))
            self.take_buttons[slot] = take
            reroll = self._button("\u21bb", f"inlineItemReroll_{slot}", f"Reroll {definition.name}")
            available = can_reroll.get(slot, False) if hasattr(can_reroll, "get") else can_reroll
            reroll.setEnabled(reroll_count > 0 and available)
            reroll.setFixedSize(40, 40)
            reroll_font = QFont(self._body_font)
            reroll_font.setPixelSize(24)
            InventoryPage._font(reroll, reroll_font)
            reroll.setStyleSheet(reroll.styleSheet() + "padding: 0; min-height: 0;")
            if reroll_count <= 0:
                reroll.setToolTip("No rerolls remaining.")
            elif not available:
                reroll.setToolTip("No alternative items are available for this offer.")
            else:
                reroll.setToolTip(f"Reroll {definition.name}")
            reroll.clicked.connect(lambda _checked=False, index=slot: on_reroll(offer_id, index))
            self.reroll_buttons[slot] = reroll
            contents.addWidget(reroll, 0, Qt.AlignRight)
            self.offer_grid.add_card(take)
        self.leave_button = self._button(
            "Leave these behind", "inlineItemLeave", "Leave the remaining items behind",
        )
        self.leave_button.clicked.connect(lambda _checked=False: on_leave(offer_id))
        rows.addWidget(self.leave_button)
        rows.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll)

    def sizeHint(self):
        natural = super().sizeHint()
        return QSize(1280, natural.height())

    def _label(self, text, font):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        label.setFocusPolicy(Qt.ClickFocus)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        InventoryPage._font(label, font)
        return label

    def _button(self, text, object_name, accessible_name):
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setAccessibleName(accessible_name)
        button.setFocusPolicy(Qt.StrongFocus)
        button.setAutoDefault(False)
        InventoryPage._font(button, self._title_font)
        return button
