"""Compact collection tiles with a stable, inspectable detail pane."""

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QFont, QFontMetrics, QPixmap
from PyQt5.QtWidgets import (
    QBoxLayout, QCheckBox, QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from item_ui import InventoryPage, CollectionTileButton, ITEM_SCROLLBAR_STYLE, RARITY_COLORS, ResponsiveItemGrid, dimmed_sprite, item_pixmap, item_sprite
from progression import ProfileError


class CatalogueDetails(QFrame):
    """Literal descriptive content shared by item and weapon browsers."""

    def __init__(self, body_font, title_font, parent=None):
        super().__init__(parent)
        self.setObjectName("catalogueDetails")
        self.setStyleSheet('QFrame#catalogueDetails { background:#0b0b0b; border:none; } QFrame#catalogueDetails QLabel { color:#dedede; background:transparent; border:none; }')
        self._body_font = QFont(body_font)
        self._title_font = QFont(title_font)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(22, 24, 22, 24)
        self.column.setSpacing(18)
        self.icon = QLabel()
        self.icon.setObjectName("catalogueDetailIcon")
        self.icon.setFixedSize(152, 152)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setTextFormat(Qt.PlainText)
        self.icon.setWordWrap(True)
        self.icon.setStyleSheet("background:transparent; border:none;")
        InventoryPage._font(self.icon, self._body_font)
        self.column.addWidget(self.icon, 0, Qt.AlignHCenter)
        self.name = self._label("", self._title_font)
        self.status = self._label("")
        self.description = self._label("")
        self.flavour = self._label("")
        self.flavour.setStyleSheet(self.flavour.styleSheet() + "color:#999999;")
        self.goal = self._label("")
        for label in (self.name, self.status, self.description, self.flavour, self.goal):
            self.column.addWidget(label)
        self.column.addStretch()

    def _label(self, text, font=None):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setContentsMargins(2, 1, 2, 1)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        InventoryPage._font(label, font or self._body_font)
        return label

    def show_entry(self, *, name, description, flavour="", status="", goal="", pixmap=None):
        self.name.setText(name)
        self.status.setText(status)
        self.description.setText(description)
        self.flavour.setText(flavour)
        self.flavour.setVisible(bool(flavour))
        self.goal.setText(goal)
        self.goal.setVisible(bool(goal))
        if pixmap is not None and not pixmap.isNull():
            self.icon.setPixmap(pixmap.scaled(152, 152, Qt.KeepAspectRatio, Qt.FastTransformation))
        else:
            self.icon.clear()
        self.updateGeometry()


class CatalogueDetailsScroll(QScrollArea):
    """Prefer a stable inspector height without imposing a window minimum."""

    def sizeHint(self):
        return QSize(380, 720)


def details_scroll(details):
    scroll = CatalogueDetailsScroll()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setStyleSheet(ITEM_SCROLLBAR_STYLE + 'QScrollArea { background:#0b0b0b; border:none; }')
    scroll.setWidget(details)
    return scroll


class ItemCataloguePanel(QWidget):
    def __init__(self, definitions, profile, unlock_goals, on_toggle, body_font,
                 parent=None, *, icon_root=None, title_font=None):
        super().__init__(parent)
        self.setObjectName("itemCataloguePanel")
        self.setFont(body_font)
        self.setStyleSheet('''
            QWidget#itemCataloguePanel { background:#000000; color:#dedede; }
            QWidget#itemCataloguePanel QLabel { color:#dedede; background:transparent; border:none; }
            QWidget#itemCataloguePanel QScrollArea { border:none; background:transparent; }
            QWidget#itemCatalogueContents { background:#000000; }
            QWidget#itemCataloguePanel QPushButton {
                background:#0b0b0b; color:#dedede; border:1px solid transparent;
                border-bottom-color:#333333; padding:4px;
            }
            QWidget#itemCataloguePanel QPushButton:hover,
            QWidget#itemCataloguePanel QPushButton:focus { background:#141414; border-color:#dddddd; }
            QWidget#itemCataloguePanel QPushButton[locked="true"] {
                background:#070707; border-color:transparent; border-bottom-color:#222222;
            }
            QWidget#itemCataloguePanel QPushButton[locked="true"]:focus { border-color:#666666; }
            QWidget#itemCataloguePanel QLabel#itemCatalogueError { color:#ffb3a9; }
            QWidget#itemCataloguePanel QCheckBox { color:#dddddd; background:transparent; spacing:10px; }
            QWidget#itemCataloguePanel QCheckBox::indicator { width:16px; height:16px; border:1px solid #777777; background:#111111; }
            QWidget#itemCataloguePanel QCheckBox::indicator:checked { background:#dddddd; border-color:#dddddd; }
        ''')
        self._definitions = dict(definitions)
        self._unlock_goals = dict(unlock_goals)
        self._on_toggle = on_toggle
        self._body_font = QFont(body_font)
        self._title_font = QFont(title_font or body_font)
        if title_font is None:
            self._title_font.setBold(True)
        self._profile = None
        self._inspected = None
        self._sprites = {}
        self._dimmed_sprites = {}
        self._detail_sprites = {}
        self._pool_toggle = None
        self.checkboxes = {}
        self.cards = {}
        self.setAccessibleDescription(
            "Hover over an item or focus it with Tab to inspect its effect and unlock goal. "
            "Click unlocked optional items or use their pool control to enable or disable them."
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.error_label = self._label("")
        self.error_label.setObjectName("itemCatalogueError")
        self.error_label.hide()
        outer.addWidget(self.error_label)
        self.browser = QBoxLayout(QBoxLayout.LeftToRight)
        self.browser.setContentsMargins(0, 0, 0, 0)
        self.browser.setSpacing(24)
        outer.addLayout(self.browser, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(ITEM_SCROLLBAR_STYLE)
        self.browser.addWidget(self.scroll, 1)
        self.details = CatalogueDetails(self._body_font, self._title_font)
        self.detail_name = self.details.name
        self.detail_description = self.details.description
        self.detail_flavour = self.details.flavour
        self.detail_goal = self.details.goal
        self.detail_status = self.details.status
        self.details_scroll = details_scroll(self.details)
        self.browser.addWidget(self.details_scroll, 0, Qt.AlignTop)
        content = QWidget()
        content.setObjectName("itemCatalogueContents")
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(14)
        metrics = QFontMetrics(self._title_font)
        longest = max((max(metrics.horizontalAdvance(word), metrics.boundingRect(word).width())
                       for item in self._definitions.values() for word in item.name.split()), default=0)
        self.grids = []
        self.group_headings = []
        for heading in ("Available", "Locked"):
            title = self._label(heading, self._title_font)
            self.group_headings.append(title)
            rows.addWidget(title)
            grid = ResponsiveItemGrid(max(180, longest + 28))
            self.grids.append(grid)
            rows.addWidget(grid)
        rows.addStretch()
        self.scroll.setWidget(content)
        for item_id, definition in self._definitions.items():
            card = CollectionTileButton()
            card.setObjectName(f"itemCard_{item_id}")
            card.setAccessibleName(definition.name)
            column = QVBoxLayout(card)
            column.setContentsMargins(12, 12, 12, 12)
            column.setSpacing(10)
            icon = item_sprite(item_id, icon_root=icon_root)
            InventoryPage._font(icon, self._body_font)
            icon.setAccessibleName(definition.name)
            self._sprites[item_id] = QPixmap(icon.pixmap()) if icon.pixmap() else None
            self._detail_sprites[item_id] = item_pixmap(item_id, icon_root=icon_root)
            column.addWidget(icon, 0, Qt.AlignHCenter)
            name = self._label(definition.name, self._title_font)
            name.setObjectName(f"itemName_{item_id}")
            name.setAlignment(Qt.AlignCenter)
            column.addWidget(name)
            status = self._label("")
            status.setObjectName(f"itemStatus_{item_id}")
            status.hide()
            column.addWidget(status)
            for child in card.findChildren(QLabel):
                child.setAttribute(Qt.WA_TransparentForMouseEvents)
                child.setTextInteractionFlags(Qt.NoTextInteraction)
                child.setFocusPolicy(Qt.NoFocus)
            card.inspected.connect(lambda key=item_id: self._inspect(key))
            card.clicked.connect(lambda _checked=False, key=item_id: self._inspect(key))
            card.toggled.connect(lambda enabled, key=item_id: self._toggle(key, enabled))
            self.cards[item_id] = card
        self.refresh(profile)
        if self._definitions:
            self._inspect(next(iter(self._definitions)))
        self._size_details()

    def sizeHint(self):
        return QSize(1780, super().sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._size_details()

    def _size_details(self):
        stacked = self.width() < 680
        self.details.icon.setVisible(not stacked)
        self.browser.setDirection(QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight)
        self.details_scroll.setMinimumWidth(0)
        self.details_scroll.setMaximumWidth(16777215 if stacked else max(264, min(380, self.width() // 3)))
        if stacked:
            self.details_scroll.setMinimumHeight(180)
            self.details_scroll.setMaximumHeight(max(180, min(280, self.height() // 2)))
        else:
            self.details_scroll.setMinimumHeight(0)
            self.details_scroll.setMaximumHeight(720)
            self.details_scroll.setFixedWidth(max(264, min(380, self.width() // 3)))

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
        unlocked = definition.default or item_id in self._profile.unlocked
        status = "Default" if definition.default else ("Enabled" if item_id not in self._profile.disabled_items else "Disabled") if unlocked else "Locked"
        self.details.show_entry(
            name=definition.name, description=definition.description, flavour=definition.flavour,
            status=f"{definition.rarity.capitalize()} / {status}",
            goal="Unlock: " + self._unlock_goals.get(item_id, "") if not unlocked else "",
            pixmap=self._detail_sprites[item_id],
        )
        if unlocked and not definition.default and self._pool_toggle is None:
            self._pool_toggle = QCheckBox("Include in item pool")
            self._pool_toggle.setObjectName("itemPoolToggle")
            InventoryPage._font(self._pool_toggle, self._body_font)
            self._pool_toggle.toggled.connect(self._toggle_inspected)
            self.details.column.insertWidget(self.details.column.count() - 1, self._pool_toggle)
        if self._pool_toggle is not None:
            self._pool_toggle.blockSignals(True)
            self._pool_toggle.setChecked(item_id not in self._profile.disabled_items)
            self._pool_toggle.blockSignals(False)
            self._pool_toggle.setVisible(unlocked and not definition.default)

    def _toggle_inspected(self, enabled):
        if self._inspected in self.checkboxes:
            self.cards[self._inspected].setChecked(enabled)

    def _arrange_groups(self):
        ranks = {rarity: rank for rank, rarity in enumerate(RARITY_COLORS)}
        ordered = sorted(self._definitions, key=lambda key: ranks.get(self._definitions[key].rarity, len(ranks)))
        for available, heading, grid in zip((True, False), self.group_headings, self.grids):
            desired = [self.cards[key] for key in ordered if (self._definitions[key].default or key in self._profile.unlocked) == available]
            if grid.cards != desired:
                while grid.grid.count():
                    grid.grid.takeAt(0)
                grid.cards = desired
                grid._arrange()
            heading.setVisible(bool(desired))
            grid.setVisible(bool(desired))

    def refresh(self, profile):
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
            card.blockSignals(True)
            card.setCheckable(optional)
            card.setChecked(optional and item_id not in profile.disabled_items)
            card.blockSignals(False)
            card.setProperty("locked", not unlocked)
            card.style().unpolish(card)
            card.style().polish(card)
            if optional:
                self.checkboxes[item_id] = card
            status = "Default" if definition.default else ("Enabled" if item_id not in profile.disabled_items else "Disabled") if unlocked else "Locked"
            card.findChild(QLabel, f"itemStatus_{item_id}").setText(status)
            name = card.findChild(QLabel, f"itemName_{item_id}")
            colour = RARITY_COLORS[definition.rarity] if unlocked and item_id not in profile.disabled_items else "#666666"
            name.setStyleSheet(name.styleSheet() + f"color:{colour};")
            icon = card.findChild(QLabel, f"itemIcon_{item_id}")
            source = self._sprites[item_id]
            if source is not None:
                if not unlocked and item_id not in self._dimmed_sprites:
                    self._dimmed_sprites[item_id] = dimmed_sprite(source)
                icon.setPixmap(source if unlocked else self._dimmed_sprites[item_id])
            goal = " Unlock: " + self._unlock_goals.get(item_id, "") if not unlocked else ""
            card.setAccessibleDescription(f"{definition.rarity.capitalize()} tier. {status}. {definition.description} {definition.flavour}{goal}")
        self._arrange_groups()
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
            if self._pool_toggle is not None and self._inspected == item_id:
                self._pool_toggle.blockSignals(True)
                self._pool_toggle.setChecked(item_id not in self._profile.disabled_items)
                self._pool_toggle.blockSignals(False)
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        card = self.cards[item_id]
        card.blockSignals(True)
        card.setChecked(item_id not in profile.disabled_items)
        card.blockSignals(False)
        self.refresh(profile)
        if self._inspected == item_id:
            self._inspect(item_id)
