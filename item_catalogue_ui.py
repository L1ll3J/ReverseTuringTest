"""Main-menu item catalogue driven by the caller's progression snapshot."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from item_ui import item_sprite
from progression import ProfileError


class ItemCataloguePanel(QWidget):
    def __init__(self, definitions, profile, unlock_goals, on_toggle, body_font,
                 parent=None, *, icon_root=None):
        super().__init__(parent)
        self.setObjectName("itemCataloguePanel")
        self.setFont(body_font)
        self.setStyleSheet(f'''
            QWidget#itemCataloguePanel QLabel, QWidget#itemCataloguePanel QCheckBox {{
                font-family: "{body_font.family()}"; font-size: 18px; color: #dedede;
                background: transparent;
            }}
            QFrame#itemCatalogueCard {{ border: 1px solid #555; background: #252525; }}
            QWidget#itemCataloguePanel QLabel#itemCatalogueError {{ color: #ffb3a9; }}
        ''')
        self._definitions = dict(definitions)
        self._unlock_goals = dict(unlock_goals)
        self._on_toggle = on_toggle
        self._body_font = QFont(body_font)
        self._icon_root = icon_root
        self._profile = profile
        self.checkboxes = {}

        layout = QVBoxLayout(self)
        introduction = self._label(
            "Discover items during a run and keep them until you need them. "
            "Choose which unlocked items can appear on your next run."
        )
        layout.addWidget(introduction)
        self.error_label = self._label("")
        self.error_label.setObjectName("itemCatalogueError")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll, 1)
        self.refresh(profile)

    @property
    def profile(self):
        return self._profile

    def _label(self, text):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setFont(self._body_font)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        return label

    def refresh(self, profile):
        """Apply a caller-supplied snapshot without writing or emitting toggles."""
        self._profile = profile
        self.error_label.clear()
        self.error_label.hide()
        self.checkboxes = {}
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(12)
        for item_id, definition in self._definitions.items():
            card = QFrame()
            card.setObjectName("itemCatalogueCard")
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 14, 14, 14)
            row.setSpacing(16)
            icon = item_sprite(item_id, icon_root=self._icon_root)
            icon.setFont(self._body_font)
            icon.setAccessibleName(definition.name)
            row.addWidget(icon, 0, Qt.AlignTop)
            details = QVBoxLayout()
            name = self._label(definition.name)
            name.setObjectName(f"itemName_{item_id}")
            name_font = QFont(self._body_font)
            name_font.setBold(True)
            name.setFont(name_font)
            details.addWidget(name)
            description = self._label(definition.description)
            description.setObjectName(f"itemDescription_{item_id}")
            details.addWidget(description)
            if definition.flavour:
                flavour = self._label(definition.flavour)
                flavour.setObjectName(f"itemFlavour_{item_id}")
                details.addWidget(flavour)
            unlocked = definition.default or item_id in profile.unlocked
            status = "Default - always included" if definition.default else "Unlocked" if unlocked else "Locked"
            status_label = self._label(status)
            status_label.setObjectName(f"itemStatus_{item_id}")
            details.addWidget(status_label)
            if not unlocked:
                goal = self._label("Unlock: " + self._unlock_goals[item_id])
                goal.setObjectName(f"itemGoal_{item_id}")
                details.addWidget(goal)
            elif not definition.default:
                checkbox = QCheckBox("Include in item pool")
                checkbox.setObjectName(f"itemEnabled_{item_id}")
                checkbox.setFont(self._body_font)
                checkbox.setChecked(item_id not in profile.disabled_items)
                checkbox.toggled.connect(lambda enabled, key=item_id: self._toggle(key, enabled))
                self.checkboxes[item_id] = checkbox
                details.addWidget(checkbox)
            row.addLayout(details, 1)
            rows.addWidget(card)
        rows.addStretch()
        previous = self.scroll.takeWidget()
        self.scroll.setWidget(content)
        if previous is not None:
            # A refresh can run inside a checkbox signal; retire its sender afterward.
            previous.deleteLater()

    def _toggle(self, item_id, enabled):
        try:
            profile = self._on_toggle(item_id, enabled)
        except ProfileError as error:
            checkbox = self.checkboxes[item_id]
            checkbox.blockSignals(True)
            checkbox.setChecked(item_id not in self._profile.disabled_items)
            checkbox.blockSignals(False)
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        self.refresh(profile)
