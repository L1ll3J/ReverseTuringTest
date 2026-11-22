"""Small, state-driven inventory and room-offer widgets."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)


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


class InventoryDialog(QDialog):
    def __init__(self, entries, definitions, targets, can_use, use, body_font, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inventory")
        self.setFont(body_font)
        self.setStyleSheet(f'QWidget {{ font-family: "{body_font.family()}"; font-size: 18px; }} QPushButton:disabled {{ color: #777; }}')
        self.setMinimumSize(500, 300)
        screen = self.screen().availableGeometry()
        self.resize(min(640, int(screen.width() * 0.85)), min(600, int(screen.height() * 0.85)))
        layout = QVBoxLayout(self)
        intro = QLabel("Keep items between nights. Use them while the discussion is waiting for you.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        rows = QVBoxLayout(content)
        if not entries:
            rows.addWidget(QLabel("Your inventory is empty. Explore new rooms to find items."))
        for entry in entries:
            definition = definitions[entry.item_id]
            card = QFrame()
            row = QVBoxLayout(card)
            label = QLabel(f"{definition.name}\n{definition.description}")
            label.setWordWrap(True)
            row.addWidget(label)
            controls = QHBoxLayout()
            target = None
            if definition.target_kind == "living_npc":
                target = QComboBox()
                for body_id, name in targets:
                    target.addItem(name, body_id)
                controls.addWidget(target, 1)
            button = QPushButton("Use")
            button.setEnabled(can_use and (target is None or target.count() > 0))
            button.clicked.connect(lambda _checked=False, instance=entry.instance_id, box=target:
                                   use(instance, box.currentData() if box is not None else None))
            controls.addWidget(button)
            row.addLayout(controls)
            rows.addWidget(card)
        rows.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        close = QPushButton("Back")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
