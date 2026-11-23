"""Main-menu item catalogue driven by the caller's progression snapshot."""

from PyQt5.QtCore import QRect, QSize, Qt
from PyQt5.QtGui import QFont, QFontMetrics, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from item_ui import InventoryPage, ItemCardButton, ITEM_SCROLLBAR_STYLE, RARITY_COLORS, ResponsiveItemGrid, item_sprite
from progression import ProfileError


class ItemCataloguePanel(QWidget):
    def __init__(self, definitions, profile, unlock_goals, on_toggle, body_font,
                 parent=None, *, icon_root=None, title_font=None):
        super().__init__(parent)
        self.setObjectName("itemCataloguePanel")
        self.setFont(body_font)
        self.setStyleSheet('''
            QWidget#itemCataloguePanel { background: #000000; color: #dedede; }
            QWidget#itemCataloguePanel QLabel { color: #dedede; background: transparent; border: none; }
            QWidget#itemCataloguePanel QScrollArea { border: none; background: transparent; }
            QWidget#itemCatalogueContents { background: #000000; }
            QWidget#itemCataloguePanel QPushButton {
                background: #0b0b0b; color: #dedede; border: 1px solid #333; padding: 4px;
            }
            QWidget#itemCataloguePanel QPushButton:hover,
            QWidget#itemCataloguePanel QPushButton:focus { background: #161616; border: 2px solid white; }
            QWidget#itemCataloguePanel QPushButton[locked="true"] { background: #070707; border-color: #222; }
            QWidget#itemCataloguePanel QPushButton[locked="true"] QLabel { color: #777; }
            QWidget#itemCataloguePanel QLabel#itemCatalogueError { color: #ffb3a9; }
            QFrame#itemCatalogueDetails { background: #0b0b0b; border: 1px solid #333; }
        ''')
        self._definitions = dict(definitions)
        self._unlock_goals = dict(unlock_goals)
        self._on_toggle = on_toggle
        self._body_font = QFont(body_font)
        self._title_font = QFont(title_font or body_font)
        if title_font is None:
            self._title_font.setBold(True)
        self._icon_root = icon_root
        self._profile = None
        self._inspected = None
        self.checkboxes = {}
        self.cards = {}
        self._sprites = {}
        layout = QVBoxLayout(self)
        self.setAccessibleDescription(
            "Click unlocked optional items to enable or disable them. "
            "Hover over an item or focus it with Tab to inspect its effect and unlock goal."
        )
        self.details = QFrame()
        self.details.setObjectName("itemCatalogueDetails")
        detail_layout = QVBoxLayout(self.details)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        self.detail_name = self._label("", self._title_font)
        self.detail_description = self._label("")
        self.detail_flavour = self._label("")
        self.detail_flavour.setStyleSheet(self.detail_flavour.styleSheet() + "color: #999999;")
        self.detail_goal = self._label("")
        for label in (self.detail_name, self.detail_description, self.detail_flavour, self.detail_goal):
            detail_layout.addWidget(label)
        self.detail_flavour.hide()
        self.detail_goal.hide()
        detail_layout.addStretch()
        layout.addWidget(self.details)
        self.error_label = self._label("")
        self.error_label.setObjectName("itemCatalogueError")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(ITEM_SCROLLBAR_STYLE)
        layout.addWidget(self.scroll, 1)
        content = QWidget()
        content.setObjectName("itemCatalogueContents")
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(12)
        self.grids = []
        title_metrics = QFontMetrics(self._title_font)
        longest_word = max((max(title_metrics.horizontalAdvance(word),
                                title_metrics.boundingRect(word).width())
                            for definition in self._definitions.values()
                            for word in definition.name.split()), default=0)
        # Keep whole words inside the existing card margins and focused border.
        minimum_card_width = max(180, longest_word + 2 * 12 + 2 * 2)
        for default, heading in ((True, "Default items"), (False, "Optional items")):
            rows.addWidget(self._label(heading, self._title_font))
            grid = ResponsiveItemGrid(minimum_card_width)
            self.grids.append(grid)
            for item_id, definition in self._definitions.items():
                if definition.default != default:
                    continue
                card = ItemCardButton()
                card.setObjectName(f"itemCard_{item_id}")
                card.setAccessibleName(definition.name)
                column = QVBoxLayout(card)
                column.setContentsMargins(12, 12, 12, 12)
                column.setSpacing(8)
                icon = item_sprite(item_id, icon_root=icon_root)
                InventoryPage._font(icon, self._body_font)
                icon.setAccessibleName(definition.name)
                self._sprites[item_id] = QPixmap(icon.pixmap()) if icon.pixmap() else None
                column.addWidget(icon, 0, Qt.AlignHCenter)
                name = self._label(definition.name, self._title_font)
                name.setObjectName(f"itemName_{item_id}")
                name.setAlignment(Qt.AlignCenter)
                column.addWidget(name)
                status = self._label("")
                status.setObjectName(f"itemStatus_{item_id}")
                status.setAlignment(Qt.AlignCenter)
                column.addWidget(status)
                for label in card.findChildren(QLabel):
                    label.setAttribute(Qt.WA_TransparentForMouseEvents)
                    label.setTextInteractionFlags(Qt.NoTextInteraction)
                    label.setFocusPolicy(Qt.NoFocus)
                card.inspected.connect(lambda key=item_id: self._inspect(key))
                card.toggled.connect(lambda enabled, key=item_id: self._toggle(key, enabled))
                self.cards[item_id] = card
                grid.add_card(card)
            rows.addWidget(grid)
        rows.addStretch()
        self.scroll.setWidget(content)
        self.refresh(profile)
        self._size_details()
        if self._definitions:
            self._inspect(next(iter(self._definitions)))

    def sizeHint(self):
        natural = super().sizeHint()
        return QSize(1440, natural.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._size_details()

    def showEvent(self, event):
        super().showEvent(event)
        self._size_details()

    def _size_details(self):
        """Reserve the tallest detail at this width so inspection cannot move cards."""
        if not self.isVisible():
            return
        width = max(1, self.width() - 50)
        title_metrics = QFontMetrics(self._title_font)
        body_metrics = QFontMetrics(self._body_font)
        heights = []
        for item_id, definition in self._definitions.items():
            fields = [(definition.name, title_metrics), (definition.description, body_metrics)]
            if definition.flavour:
                fields.append((definition.flavour, body_metrics))
            if not definition.default:
                fields.append(("Unlock: " + self._unlock_goals.get(item_id, ""), body_metrics))
            height = sum(metrics.boundingRect(QRect(0, 0, width, 10000), Qt.TextWordWrap, text).height()
                         for text, metrics in fields)
            heights.append(height + 24 + self.details.layout().spacing() * (len(fields) - 1))
        self.details.setFixedHeight(max(heights, default=0))

    @property
    def profile(self):
        return self._profile

    def _label(self, text, font=None):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        InventoryPage._font(label, font or self._body_font)
        return label

    def _inspect(self, item_id):
        self.details.show()
        self._inspected = item_id
        definition = self._definitions[item_id]
        self.detail_name.setText(definition.name)
        self.detail_description.setText(definition.description)
        self.detail_flavour.setText(definition.flavour)
        self.detail_flavour.setVisible(bool(definition.flavour))
        locked = not definition.default and item_id not in self._profile.unlocked
        self.detail_goal.setText("Unlock: " + self._unlock_goals.get(item_id, "") if locked else "")
        self.detail_goal.setVisible(locked)

    def refresh(self, profile):
        """Update card state in place without emitting preference writes."""
        self.error_label.clear()
        self.error_label.hide()
        if profile == self._profile:
            return
        self._profile = profile
        self.checkboxes.clear()
        for item_id, definition in self._definitions.items():
            card = self.cards[item_id]
            unlocked = definition.default or item_id in profile.unlocked
            optional = unlocked and not definition.default
            card.setCursor(Qt.PointingHandCursor if optional else Qt.ArrowCursor)
            enabled = item_id not in profile.disabled_items
            card.blockSignals(True)
            card.setCheckable(optional)
            card.setChecked(optional and enabled)
            card.blockSignals(False)
            card.setProperty("locked", not unlocked)
            card.style().unpolish(card)
            card.style().polish(card)
            if optional:
                self.checkboxes[item_id] = card
            status = "Default" if definition.default else ("Enabled" if enabled else "Disabled") if unlocked else "Locked"
            card.findChild(QLabel, f"itemStatus_{item_id}").setText(status)
            name = card.findChild(QLabel, f"itemName_{item_id}")
            color = RARITY_COLORS.get(getattr(definition, "rarity", "green"), RARITY_COLORS["green"]) if unlocked else "#777777"
            InventoryPage._font(name, self._title_font)
            name.setStyleSheet(name.styleSheet() + f"color: {color};")
            icon = card.findChild(QLabel, f"itemIcon_{item_id}")
            pixmap = self._sprites[item_id]
            if pixmap is not None:
                if unlocked:
                    icon.setPixmap(pixmap)
                else:
                    original = pixmap.toImage()
                    image = original.convertToFormat(QImage.Format_Grayscale8).convertToFormat(QImage.Format_ARGB32)
                    painter = QPainter(image)
                    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
                    painter.drawImage(0, 0, original)
                    painter.end()
                    icon.setPixmap(QPixmap.fromImage(image))
            goal = " Unlock: " + self._unlock_goals.get(item_id, "") if not unlocked else ""
            tier = getattr(definition, "rarity", "green").capitalize()
            card.setAccessibleDescription(f"{tier} tier. {status}. {definition.description} {definition.flavour}{goal}")
        if self._inspected is not None:
            self._inspect(self._inspected)

    def _toggle(self, item_id, enabled):
        try:
            profile = self._on_toggle(item_id, enabled)
        except ProfileError as error:
            card = self.cards[item_id]
            card.blockSignals(True)
            card.setChecked(item_id not in self._profile.disabled_items)
            card.blockSignals(False)
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        card = self.cards[item_id]
        card.blockSignals(True)
        card.setChecked(item_id not in profile.disabled_items)
        card.blockSignals(False)
        self.refresh(profile)
