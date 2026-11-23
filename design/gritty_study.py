"""Isolated visual study. No gameplay, models, audio, saves, or game imports."""

import argparse
import os
from pathlib import Path
import random
import sys

from PyQt5.QtCore import QEventLoop, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen, QPixmap, QPolygonF, QRegion
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QShortcut, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)
from PyQt5.QtGui import QKeySequence


ASSET_ROOT = Path(__file__).resolve().parents[1]
INK = "#efefef"
GREY = "#a4a4a4"
BODY_FAMILY = "Segoe UI"
CAST = ("Jerry", "Dave", "Owen", "Hank", "Debra", "You")
LINES = (
    ("Dave", "The man is dead, Hank. You can have a sandwich."),
    ("Hank", "Kitchen. There was bread. I chose not to make things worse."),
    ("Jerry", "My wife would have loved this place. She always liked a quiet skirting board."),
    ("Debra", "One light goes out. Another learns to lie."),
    ("Owen", "I was with Jerry. I'd remember a face like that, even in the dark."),
)
ITEMS = (
    ("throw_voice", "Throw Voice", "A borrowed voice, carried through a gilded horn."),
    ("booby_trap", "Booby Trap", "Looks expensive. Mind where you put your feet."),
    ("dark_whisper", "Dark Whisper", "An empty locket. Something inside is still listening."),
    ("love_potion", "Love Potion", "Sweet on the tongue. Bitter by morning."),
    ("lucky_coin", "(Un)Lucky Coin", "A little weight in the hand. A great deal hanging in the balance."),
    ("presidential_decree", "Presidential Decree", "A seal, a ribbon, and an unreasonable amount of confidence."),
    ("revolver", "Revolver", "A short grey barrel and a worn wooden grip."),
    ("watch_this_drive", "Watch this Drive", "A red target, a gold arrow, and a little chequered flag."),
    ("banana", "Banana of Friendship", "Bright yellow. Mercifully uncomplicated."),
    ("muffle", "Muffle", "A tightly knotted cloth. Not much room for conversation."),
    ("spare_paper", "Spare Paper", "Three small ballots, fanned like a very dull hand of cards."),
    ("guardian_angel", "Guardian Angel", "Pale wings, a small halo, and something blue at the centre."),
    ("soul_transfusion", "Soul Transfusion", "Two silhouettes with something pale passing between them."),
    ("spin_the_dagger", "Spin the Dagger", "A silver blade caught in a little circle of motion."),
    ("mass_discombobulator", "Mass Discombobulator", "Brass, a spiral lens, and an antenna with questionable intentions."),
    ("occult_ritual", "Occult Ritual", "Three violet candles arranged with considerable confidence."),
    ("forged_note", "Forged Note", "An ivory fold, a feather, and a suspiciously tidy seal."),
    ("divine_revelation", "Divine Revelation", "A watchful eye inside a small golden sunburst."),
    ("mail_in_ballot", "Mail-in Ballot", "A cream envelope with a ballot peeking out of the corner."),
    ("lucky_match", "Lucky Match", "A brass matchbox and one partly charred match."),
    ("deep_pockets", "Deep Pockets", "Worn brown leather, two generous pockets, and brass clasps."),
    ("pocket_gavel", "Pocket Gavel", "A tiny walnut gavel with its own little sound block."),
    ("listening_glass", "Listening Glass", "Smoky cut glass with a whisper caught inside."),
    ("stay_of_execution", "Stay of Execution", "A folded court writ with a blue seal and a silver clasp."),
    ("room_reservation", "Room Reservation", "An old brass key with a plum-coloured enamel tag."),
)
ROOMS = (
    ("Attic", "A roof full of old trunks. The beams settle like somebody clearing their throat."),
    ("Kitchen", "Cold flagstones. A knife rack. Somebody has left the kettle on."),
    ("Library", "Leather chairs and dust. Every book looks as though it knows something."),
    ("Basement", "A narrow stair beneath the house. The light does not quite reach the last step."),
    ("Cellar", "Bottles lying quietly in the dark. For once, the company seems agreeable."),
)


def pixel_font(family, size):
    font = QFont(family)
    font.setPixelSize(size)
    return font


def rule():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet("background: #565656; border: 0;")
    return line


def label(text, *, pixel=None, size=18, color=INK, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    widget.setStyleSheet(f"color:{color}; background:transparent; font-size:{size}px;")
    widget.setFont(pixel_font(pixel if pixel else BODY_FAMILY, size))
    widget.setAccessibleName(text)
    return widget


class StudyButton(QPushButton):
    """Give focused study buttons Return support without application-wide hooks."""

    @staticmethod
    def _is_return(event):
        return (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier))

    def keyPressEvent(self, event):
        if self._is_return(event):
            if self.hasFocus() and self.isEnabled() and not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._is_return(event):
            event.accept()
            return
        super().keyReleaseEvent(event)


def button(text, action, pixel, *, size=12):
    widget = StudyButton(text)
    widget.setFont(pixel_font(pixel, size))
    widget.setMinimumHeight(42)
    widget.setCursor(Qt.PointingHandCursor)
    widget.clicked.connect(action)
    return widget


class SuspectStage(QWidget):
    """Dim cast placement is fixed until a new illustrative run is requested."""

    def __init__(self, pixel, parent=None, *, idle=False):
        super().__init__(parent)
        self.pixel = pixel
        self.idle = idle
        self.cast_order = list(CAST)
        self.reduced_motion = False
        self.speaker = "Dave"
        self.frame = 14
        self.light_x = 320.0
        self.setMinimumHeight(166)
        self.setMaximumHeight(270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("A quiet sitting room with a table, a lamp and anonymous silhouettes" if idle else
                               "Suspects seated in a dark sitting room; the speaking suspect is illuminated")
        self.arrange(37)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        if not idle:
            self.timer.start(80)

    def arrange(self, seed):
        self.cast_order = list(CAST)
        random.Random(seed).shuffle(self.cast_order)
        self.light_x = self.position(self.speaker)
        self.update()

    def position(self, name):
        return 85 + self.cast_order.index(name) * 94

    def speak(self, name):
        self.speaker = name
        self.frame = 0
        if self.reduced_motion:
            self.light_x = self.position(name)
        self.update()

    def tick(self):
        if self.idle or self.reduced_motion or not self.isVisible():
            return
        self.frame += 1
        target = self.position(self.speaker)
        self.light_x += (target - self.light_x) * 0.32
        if self.frame <= 66:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        p.scale(self.width() / 640, self.height() / 160)
        p.setRenderHint(QPainter.Antialiasing, False)
        # Sparse old-house panelling, window and a long table.
        p.setPen(QPen(QColor("#252525"), 1))
        for x in range(21, 639, 40):
            p.drawLine(x, 17, x, 100)
        p.drawLine(16, 97, 624, 97)
        p.drawRect(276, 7, 88, 66)
        p.drawLine(320, 7, 320, 73)
        p.drawLine(276, 38, 364, 38)
        p.setPen(QPen(QColor("#444444"), 1))
        p.drawLine(283, 64, 304, 43)
        p.drawLine(337, 23, 356, 12)
        visible = self.idle or self.reduced_motion or self.frame < 60
        intensity = 0.36 if self.idle else (
            1.0 if self.reduced_motion or self.frame < 47 else max(0, (60 - self.frame) / 13))
        light_x = 320.0 if self.idle else self.light_x
        if visible:
            # A coarse cone of light, with no glow, blur, or colour accent.
            cone = QPolygonF([QPointF(320, -14), QPointF(light_x - 46, 127),
                              QPointF(light_x + 46, 127)])
            p.save()
            p.setClipRegion(QRegion(cone.toPolygon()))
            p.setPen(QColor(int(36 * intensity), int(36 * intensity), int(36 * intensity)))
            for y in range(0, 128, 3):
                for x in range(y % 2, 640, 4):
                    p.drawPoint(x, y)
            p.restore()
        if self.idle:
            p.fillRect(312, 0, 16, 3, QColor("#707070"))
            p.fillRect(308, 3, 24, 2, QColor("#555555"))
        for name in self.cast_order:
            x = self.position(name)
            proximity = max(0.0, 1 - abs(light_x - x) / 45)
            active = not self.idle and visible and name == self.speaker and proximity > 0.8
            bob = 0 if self.reduced_motion or not active else ((self.frame // 3) % 2)
            tone = int(27 + 163 * intensity * proximity) if visible else 27
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(tone, tone, tone))
            head_y = 68 + bob
            if name == "Jerry":
                p.drawRect(int(x) - 9, head_y - 5, 18, 5)
                p.drawRect(int(x) - 13, head_y, 26, 3)
            elif name == "Debra":
                p.drawRect(int(x) + 8, head_y + 2, 5, 7)
            elif name == "Hank":
                p.drawRect(int(x) - 8, head_y, 16, 5)
            p.drawRect(int(x) - 7, head_y + 4, 14, 17)
            p.drawRect(int(x) - 4, head_y + 21, 8, 5)
            width = 26 if name == "Dave" else 21 if name == "Debra" else 23
            p.drawRect(int(x) - width // 2, 94 + bob, width, 24)
            p.drawRect(int(x) - width // 2 - 4, 98 + bob, width + 8, 14)
            if active:
                p.fillRect(int(x) - 4, head_y + 10, 2, 2, Qt.black)
                p.fillRect(int(x) + 3, head_y + 10, 2, 2, Qt.black)
                p.setPen(QColor("#ededed"))
                p.setFont(pixel_font(self.pixel, 6))
                p.drawText(QRectF(x - 40, 133, 80, 13), Qt.AlignCenter, name.upper())
        p.fillRect(21, 115, 598, 10, QColor("#101010"))
        p.setPen(QPen(QColor("#575757"), 1))
        p.drawLine(21, 115, 619, 115)
        p.setPen(QColor("#252525"))
        for x in range(27, 617, 9):
            p.drawLine(x, 119, x + 4, 119)


class Floorplan(QWidget):
    def __init__(self, pixel, parent=None):
        super().__init__(parent)
        self.pixel = pixel
        self.selected = "Library"
        self.setMinimumSize(260, 250)
        self.setAccessibleName("Country-house floor plan with the selected room outlined in white")

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        p.scale(self.width() / 480, self.height() / 330)
        p.setRenderHint(QPainter.Antialiasing, False)
        rooms = {"Attic": QRect(135, 25, 210, 62), "Kitchen": QRect(65, 114, 159, 85),
                 "Library": QRect(254, 114, 159, 85), "Basement": QRect(65, 223, 159, 80),
                 "Cellar": QRect(254, 223, 159, 80)}
        p.setPen(QPen(QColor("#444444"), 1))
        p.drawLine(240, 87, 240, 305)
        p.drawLine(224, 154, 254, 154)
        p.drawLine(224, 262, 254, 262)
        for name, rect in rooms.items():
            active = name == self.selected
            p.setPen(QPen(QColor(INK if active else "#555555"), 2 if active else 1))
            p.drawRect(rect)
            if active:
                p.setPen(QColor("#282828"))
                for y in range(rect.top() + 4, rect.bottom() - 3, 5):
                    p.drawLine(rect.left() + 4, y, rect.right() - 4, y)
            p.fillRect(rect.adjusted(14, 23, -14, -23), Qt.black)
            p.setPen(QColor(INK if active else GREY))
            p.setFont(pixel_font(self.pixel, 8))
            p.drawText(QRectF(rect), Qt.AlignCenter, name.upper())


class ItemInspection(QDialog):
    def __init__(self, assets, pixel, parent):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowTitle("AlibAi | Item artwork inspection | Illustrative visual study")
        self.setWindowModality(Qt.WindowModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setObjectName("itemInspection")
        self.setStyleSheet("#itemInspection { border: 1px solid #d0d0d0; background: #000; }")
        self.resize(min(790, parent.width() - 48), min(520, parent.height() - 48))
        self.assets = assets
        self.pixel = pixel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        heading = QHBoxLayout()
        heading.addWidget(label("POCKETS", pixel=pixel, size=16))
        heading.addStretch()
        close = button("BACK  [ESC]", self.reject, pixel, size=10)
        heading.addWidget(close)
        layout.addLayout(heading)
        layout.addWidget(rule())
        body = QHBoxLayout()
        body.setSpacing(28)
        self.list = QListWidget()
        self.list.setAccessibleName("Items to inspect")
        self.list.setMinimumWidth(160)
        self.list.setMaximumWidth(225)
        self.list.setFont(QFont(BODY_FAMILY, 15))
        for item_id, title, description in ITEMS:
            entry = QListWidgetItem(title)
            entry.setData(Qt.UserRole, item_id)
            entry.setToolTip(title)
            entry.setSizeHint(QSize(180, 44))
            self.list.addItem(entry)
        body.addWidget(self.list, 1)
        right = QVBoxLayout()
        self.sprite = QLabel()
        self.sprite.setAlignment(Qt.AlignCenter)
        self.sprite.setMinimumSize(120, 150)
        self.title = label("", pixel=pixel, size=13, wrap=True)
        self.title.setMinimumHeight(50)
        self.description = label("", size=18, color=GREY, wrap=True)
        self.description.setMinimumHeight(72)
        right.addWidget(self.sprite, 1)
        right.addWidget(self.title)
        right.addWidget(self.description)
        body.addLayout(right, 2)
        layout.addLayout(body, 1)
        layout.addWidget(label("Choose an object to look closer.", size=14, color=GREY))
        self.list.currentRowChanged.connect(self.select)
        self.list.setCurrentRow(0)
        self.list.setFocus()

    def select(self, row):
        if not 0 <= row < len(ITEMS):
            return
        item_id, title, description = ITEMS[row]
        image = QPixmap(str(self.assets / "icons/items" / (item_id + ".png")))
        self.sprite.setPixmap(image.scaled(205, 205, Qt.KeepAspectRatio, Qt.FastTransformation))
        self.sprite.setAccessibleName(title + " artwork")
        self.title.setText(title.upper())
        self.description.setText(description)


class GrittyStudy(QWidget):
    def __init__(self, assets):
        super().__init__()
        self.assets = Path(assets).expanduser().resolve()
        self._validate_assets()
        font_id = QFontDatabase.addApplicationFont(str(self.assets / "fonts/PressStart2P-Regular.ttf"))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if not families:
            raise ValueError("The pinned asset root does not contain a loadable Press Start 2P font.")
        self.pixel = families[0]
        # Offscreen Qt may not enumerate Windows fonts until explicitly loaded.
        for filename in ("segoeui.ttf", "arial.ttf"):
            candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if candidate.is_file():
                QFontDatabase.addApplicationFont(str(candidate))
        global BODY_FAMILY
        available = QFontDatabase().families()
        BODY_FAMILY = next((name for name in ("Segoe UI", "Arial", "DejaVu Sans", "Liberation Sans")
                            if name in available), "")
        if not BODY_FAMILY:
            raise ValueError("A readable body font is required. Install Segoe UI, Arial or DejaVu Sans.")
        self.setWindowTitle("AlibAi | Illustrative visual study | Not a playable demo")
        self.resize(1280, 800)
        self.setMinimumSize(800, 600)
        self.setFont(QFont(BODY_FAMILY, 12))
        self.item_dialog = None
        self.line_index = 0
        self.selected_room = "Library"
        self.setStyleSheet("""
            QWidget { background: #000; color: #efefef; }
            QPushButton { border: 1px solid transparent; padding: 8px 12px; text-align: left; }
            QPushButton:hover { color: #000; background: #efefef; }
            QPushButton:focus { border: 1px solid #efefef; }
            QPushButton:pressed { color: #000; background: #aaa; }
            QListWidget { border: 0; padding: 0; outline: 0; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #303030; }
            QListWidget::item:selected { color: #000; background: #ddd; }
            QListWidget:focus { border: 1px solid #8a8a8a; }
            QScrollArea { border: 0; }
            QCheckBox { color: #aaa; font-size: 14px; spacing: 8px; }
            QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #888; background: #000; }
            QCheckBox::indicator:checked { background: #ddd; }
            QScrollBar:vertical { width: 10px; background: #111; }
            QScrollBar::handle:vertical { background: #777; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 18, 28, 16)
        outer.setSpacing(12)
        self.header = QWidget()
        top = QHBoxLayout(self.header)
        top.setContentsMargins(0, 0, 0, 0)
        self.location = label("", pixel=self.pixel, size=11)
        top.addWidget(self.location)
        top.addStretch()
        self.motion = QCheckBox("Reduced motion")
        self.motion.toggled.connect(self.set_reduced_motion)
        top.addWidget(self.motion)
        outer.addWidget(self.header)
        self.header_rule = rule()
        outer.addWidget(self.header_rule)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.menu_page = self.make_menu()
        self.conversation_page = self.make_conversation()
        self.rooms_page = self.make_rooms()
        for page in (self.menu_page, self.conversation_page, self.rooms_page):
            self.stack.addWidget(page)
        self.navigation_rule = rule()
        outer.addWidget(self.navigation_rule)
        self.navigation = QWidget()
        bottom = QHBoxLayout(self.navigation)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(12)
        for title, page in (("HOUSE", "menu"), ("ROOMS", "rooms"), ("CONVERSATION", "conversation")):
            bottom.addWidget(button(title, lambda checked=False, page=page: self.go(page), self.pixel, size=9))
        bottom.addStretch()
        bottom.addWidget(button("POCKETS  [I]", self.open_items, self.pixel, size=9))
        outer.addWidget(self.navigation)
        self.shortcuts = []
        for sequence, action in (("Alt+1", lambda: self.go("menu")), ("Alt+2", lambda: self.go("rooms")),
                                 ("Alt+3", lambda: self.go("conversation")), ("I", self.open_items),
                                 ("Escape", lambda: self.go("menu"))):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(action)
            self.shortcuts.append(shortcut)
        self.go("menu")

    def _validate_assets(self):
        required = [self.assets / "fonts/PressStart2P-Regular.ttf"]
        required.extend(self.assets / "icons/items" / (item_id + ".png") for item_id, _, _ in ITEMS)
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise ValueError("Missing study assets: " + ", ".join(missing))
        for path in required[1:]:
            if QPixmap(str(path)).isNull():
                raise ValueError(f"Unreadable item artwork: {path}")

    def make_menu(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addStretch(2)
        self.menu_logo = label("AlibAi", pixel=self.pixel, size=45)
        self.menu_logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.menu_logo)
        layout.addSpacing(10)
        self.menu_stage = SuspectStage(self.pixel, idle=True)
        layout.addWidget(self.menu_stage, 3)
        actions = QWidget()
        actions.setFixedWidth(240)
        controls = QVBoxLayout(actions)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        self.play_button = button("PLAY", self.new_run, self.pixel, size=13)
        self.items_button = button("ITEMS", self.open_items, self.pixel, size=13)
        self.quit_button = button("QUIT", self.close, self.pixel, size=13)
        for control in (self.play_button, self.items_button, self.quit_button):
            control.setStyleSheet("text-align: center;")
            controls.addWidget(control)
        layout.addWidget(actions, alignment=Qt.AlignHCenter)
        layout.addStretch(2)
        return page

    def make_conversation(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(13)
        self.stage = SuspectStage(self.pixel)
        layout.addWidget(self.stage, 3)
        layout.addWidget(rule())
        self.dialogue_scroll = QScrollArea()
        self.dialogue_scroll.setWidgetResizable(True)
        text_page = QWidget()
        text = QVBoxLayout(text_page)
        text.setContentsMargins(18, 6, 18, 8)
        text.setSpacing(14)
        text.addWidget(label("DETECTIVE", pixel=self.pixel, size=10, color=GREY))
        text.addWidget(label("Tell me where you were when the lights went out.", size=18, color=GREY, wrap=True))
        text.addSpacing(10)
        self.speaker_label = label("DAVE", pixel=self.pixel, size=14)
        self.speech_label = label(LINES[0][1], size=21, wrap=True)
        self.speech_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        text.addWidget(self.speaker_label)
        text.addWidget(self.speech_label)
        text.addStretch()
        self.dialogue_scroll.setWidget(text_page)
        layout.addWidget(self.dialogue_scroll, 3)
        controls = QHBoxLayout()
        controls.addWidget(label("The room waits.", size=15, color=GREY))
        controls.addStretch()
        self.listen_button = button("LISTEN  [SPACE]", self.next_line, self.pixel, size=11)
        controls.addWidget(self.listen_button)
        layout.addLayout(controls)
        self.listen_shortcut = QShortcut(QKeySequence("Space"), page)
        self.listen_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.listen_shortcut.setAutoRepeat(False)
        self.listen_shortcut.activated.connect(self.next_line)
        return page

    def make_rooms(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(15, 14, 15, 6)
        layout.addWidget(label("WHERE WILL YOU SPEND THE NIGHT?", pixel=self.pixel, size=12, wrap=True))
        row = QHBoxLayout()
        row.setSpacing(24)
        self.room_list = QListWidget()
        self.room_list.setAccessibleName("Rooms in the house")
        self.room_list.setFont(pixel_font(self.pixel, 10))
        self.room_list.setMaximumWidth(255)
        self.room_list.setMinimumWidth(190)
        for room, _ in ROOMS:
            self.room_list.addItem(room.upper())
        self.floorplan = Floorplan(self.pixel)
        row.addWidget(self.room_list, 1)
        row.addWidget(self.floorplan, 3)
        layout.addLayout(row, 1)
        layout.addWidget(rule())
        self.room_title = label("THE LIBRARY", pixel=self.pixel, size=13)
        self.room_description = label(ROOMS[2][1], size=18, color=GREY, wrap=True)
        self.room_description.setMinimumHeight(58)
        layout.addWidget(self.room_title)
        layout.addWidget(self.room_description)
        self.room_list.currentRowChanged.connect(self.select_room)
        self.room_list.setCurrentRow(2)
        return page

    def select_room(self, row):
        if not 0 <= row < len(ROOMS):
            return
        self.selected_room, description = ROOMS[row]
        self.floorplan.selected = self.selected_room
        self.floorplan.update()
        self.room_title.setText("THE " + self.selected_room.upper())
        self.room_description.setText(description)

    def new_run(self):
        seed = random.SystemRandom().randrange(2 ** 32)
        self.stage.arrange(seed)
        self.menu_stage.arrange(seed)
        self.line_index = 0
        self.set_line(0)
        self.go("conversation")

    def set_line(self, index):
        self.line_index = index % len(LINES)
        speaker, words = LINES[self.line_index]
        self.speaker_label.setText(speaker.upper())
        self.speech_label.setText(words)
        self.stage.speak(speaker)

    def next_line(self):
        if self.stack.currentWidget() is self.conversation_page and self.item_dialog is None:
            self.set_line(self.line_index + 1)

    def go(self, page):
        if self.item_dialog is not None:
            return
        pages = {"menu": self.menu_page, "rooms": self.rooms_page, "conversation": self.conversation_page}
        self.stack.setCurrentWidget(pages[page])
        self.location.setText({"menu": "", "rooms": "NIGHT TWO", "conversation": "THE SITTING ROOM"}[page])
        for widget in (self.header, self.header_rule, self.navigation, self.navigation_rule):
            widget.setVisible(page != "menu")
        if page == "conversation":
            self.listen_button.setFocus()
        elif page == "rooms":
            self.room_list.setFocus()
        else:
            self.play_button.setFocus()

    def set_reduced_motion(self, checked):
        if hasattr(self, "stage"):
            self.stage.reduced_motion = bool(checked)
            self.stage.light_x = self.stage.position(self.stage.speaker)
            self.stage.update()

    def open_items(self):
        if self.item_dialog is not None:
            self.item_dialog.activateWindow()
            return
        previous_focus = self.focusWidget()
        dialog = ItemInspection(self.assets, self.pixel, self)
        self.item_dialog = dialog
        dialog.move(self.mapToGlobal(QPoint((self.width() - dialog.width()) // 2,
                                           (self.height() - dialog.height()) // 2)))

        def closed(_result):
            self.item_dialog = None
            self.activateWindow()
            if previous_focus is not None:
                QTimer.singleShot(0, previous_focus.setFocus)

        dialog.finished.connect(closed)
        dialog.show()

    def closeEvent(self, event):
        if self.item_dialog is not None:
            self.item_dialog.reject()
        self.stage.timer.stop()
        self.menu_stage.timer.stop()
        super().closeEvent(event)


def export_study(window, destination):
    destination = Path(destination).expanduser().resolve()
    protected_roots = (window.assets, ASSET_ROOT)
    if any(destination == root or root in destination.parents for root in protected_roots):
        raise ValueError("Export screenshots outside the study and asset worktrees.")
    destination.mkdir(parents=True, exist_ok=True)
    window.stage.timer.stop()
    window.stage.arrange(37)
    window.menu_stage.arrange(37)
    window.show()
    app = QApplication.instance()

    def settle():
        # Let native layout, modal focus and repaint events finish before capture.
        app.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(25, loop.quit)
        loop.exec_()

    for width, height, suffix in ((1280, 800, "1280"), (800, 600, "800")):
        window.resize(width, height)
        settle()
        for page in ("menu", "conversation", "rooms"):
            window.go(page)
            window.set_line(0)
            window.stage.frame = 14
            window.stage.light_x = window.stage.position("Dave")
            settle()
            path = destination / f"{page}-{suffix}.png"
            if not window.grab().save(str(path)):
                raise ValueError(f"Unable to write screenshot: {path}")
        window.go("conversation")
        window.open_items()
        settle()
        aliases = {"throw_voice": "horn", "booby_trap": "trap",
                   "dark_whisper": "whisper", "love_potion": "potion"}
        for row, (item_id, _title, _description) in enumerate(ITEMS):
            name = aliases.get(item_id, item_id)
            window.item_dialog.list.setCurrentRow(row)
            settle()
            path = destination / f"item-{name}-{suffix}.png"
            composite = window.grab()
            painter = QPainter(composite)
            origin = window.item_dialog.mapToGlobal(QPoint(0, 0)) - window.mapToGlobal(QPoint(0, 0))
            painter.drawPixmap(origin, window.item_dialog.grab())
            painter.end()
            if not composite.save(str(path)):
                raise ValueError(f"Unable to write screenshot: {path}")
            # A modal native dialog is a separate window, so save it separately too.
            inspection_path = destination / f"inspection-{name}-{suffix}.png"
            if not window.item_dialog.grab().save(str(inspection_path)):
                raise ValueError(f"Unable to write screenshot: {inspection_path}")
        window.item_dialog.reject()
        settle()
    window.close()
    print(f"Screenshots: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=ASSET_ROOT,
                        help="Game asset root. Defaults only to this study's own worktree.")
    parser.add_argument("--export", type=Path,
                        help="Write deterministic offscreen screenshots outside the asset worktree, then exit.")
    args = parser.parse_args()
    if args.export:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    try:
        window = GrittyStudy(args.assets)
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
