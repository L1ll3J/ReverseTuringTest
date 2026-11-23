"""Main-menu item catalogue driven by the caller's progression snapshot."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from item_ui import InventoryPage, ItemCardButton, RARITY_COLORS, ResponsiveItemGrid, item_sprite
from progression import ProfileError


class ItemCataloguePanel(QWidget):
    def __init__(self, definitions, profile, unlock_goals, on_toggle, body_font,
                 parent=None, *, icon_root=None, title_font=None):
        super().__init__(parent)
        self.setObjectName("itemCataloguePanel")
        self.setFont(body_font)
        self.setStyleSheet('''
            QWidget#itemCataloguePanel { background: #1e1e1e; color: #dedede; }
            QWidget#itemCataloguePanel QLabel { color: #dedede; background: transparent; border: none; }
            QWidget#itemCataloguePanel QScrollArea { border: none; background: transparent; }
            QWidget#itemCatalogueContents { background: #1e1e1e; }
            QWidget#itemCataloguePanel QPushButton {
                background: #252525; color: #dedede; border: 1px solid #777; padding: 4px;
            }
            QWidget#itemCataloguePanel QPushButton:hover,
            QWidget#itemCataloguePanel QPushButton:focus { background: #363636; border: 2px solid white; }
            QWidget#itemCataloguePanel QPushButton[locked="true"] { background: #202020; border-color: #444; }
            QWidget#itemCataloguePanel QPushButton[locked="true"] QLabel { color: #777; }
            QWidget#itemCataloguePanel QLabel#itemCatalogueError { color: #ffb3a9; }
            QFrame#itemCatalogueDetails { background: #252525; border: 1px solid #777; }
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
        layout.addWidget(self._label("Discover items during a run. Click unlocked optional items to enable or disable them."))
        self.details = QFrame()
        self.details.setObjectName("itemCatalogueDetails")
        detail_layout = QVBoxLayout(self.details)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        self.detail_name = self._label("Inspect an item", self._title_font)
        self.detail_description = self._label("Hover over an item or focus it with Tab to see its effect and unlock goal.")
        self.detail_flavour = self._label("")
        self.detail_goal = self._label("")
        for label in (self.detail_name, self.detail_description, self.detail_flavour, self.detail_goal):
            detail_layout.addWidget(label)
        self.detail_flavour.hide()
        self.detail_goal.hide()
        layout.addWidget(self.details)
        self.error_label = self._label("")
        self.error_label.setObjectName("itemCatalogueError")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll, 1)
        content = QWidget()
        content.setObjectName("itemCatalogueContents")
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(12)
        self.grids = []
        for default, heading in ((True, "Default items"), (False, "Optional items")):
            rows.addWidget(self._label(heading, self._title_font))
            grid = ResponsiveItemGrid(195)
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
            card.setAccessibleDescription(f"{status}. {definition.description} {definition.flavour}{goal}")
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
