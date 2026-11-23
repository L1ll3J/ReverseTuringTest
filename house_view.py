"""Proportional cutaway presentation driven only by public room observations."""

from dataclasses import dataclass
from typing import Iterable

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRegion, QTransform
from PyQt5.QtWidgets import QAbstractButton, QLabel, QVBoxLayout, QWidget


ARTWORK_SIZE = (1122, 1402)
ROOM_LABELS = {
    "attic": "Attic", "kitchen": "Kitchen", "library": "Library",
    "basement": "Basement", "cellar": "Cellar",
}


@dataclass(frozen=True)
class RoomObservation:
    """A caller-supplied public projection, never an actual occupancy record.

    A known name does not imply that every occupant has been observed. A light
    is an independently observed fact and is never derived from name counts.
    """

    room_id: str
    available: bool = True
    known_names: tuple[str, ...] = ()
    unknown_remainder: bool = True
    observed_at: str | None = None
    light_on: bool | None = None

    def __post_init__(self):
        if self.room_id not in ROOM_LABELS:
            raise ValueError("Unknown cutaway room")
        if type(self.available) is not bool or type(self.unknown_remainder) is not bool:
            raise TypeError("Availability and unknown remainder must be booleans")
        if not isinstance(self.known_names, (tuple, list)):
            raise TypeError("Known names must be a sequence of observed names")
        names = tuple(self.known_names)
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("Observed names must be nonempty text")
        if len(set(names)) != len(names):
            raise ValueError("Observed names must be distinct")
        if self.observed_at is not None and (not isinstance(self.observed_at, str) or not self.observed_at.strip()):
            raise ValueError("Observation time must be nonempty text when supplied")
        if self.light_on is not None and type(self.light_on) is not bool:
            raise TypeError("An observed light must be on, off, or unknown")
        object.__setattr__(self, "known_names", names)


def unknown_observations() -> tuple[RoomObservation, ...]:
    return tuple(RoomObservation(room_id) for room_id in ROOM_LABELS)


def room_paths() -> dict[str, QPainterPath]:
    """Map the actual 1122x1402 artwork interiors, leaving the hall untouched."""
    outlines = {
        "attic": ((112, 603), (116, 549), (267, 365), (398, 365), (530, 185),
                  (654, 365), (790, 365), (919, 548), (920, 603)),
        "kitchen": ((88, 645), (405, 645), (416, 665), (416, 904), (104, 904), (84, 883)),
        "library": ((626, 642), (895, 642), (916, 630), (1033, 648),
                    (1044, 685), (1039, 823), (1029, 887), (956, 914), (628, 907)),
        "basement": ((94, 951), (170, 938), (394, 938), (411, 958),
                     (411, 1139), (105, 1139), (92, 1121)),
    }
    result = {}
    for room_id, points in outlines.items():
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        path.closeSubpath()
        result[room_id] = path
    cellar = QPainterPath()
    cellar.moveTo(623, 1139)
    cellar.lineTo(623, 1010)
    cellar.cubicTo(635, 959, 690, 935, 771, 936)
    cellar.cubicTo(855, 937, 903, 955, 927, 1003)
    cellar.lineTo(927, 1139)
    cellar.closeSubpath()
    # The separate exterior door/stair is the same semantic Cellar control.
    cellar.addPolygon(QPolygonF([QPointF(x, y) for x, y in (
        (969, 958), (1040, 981), (1098, 1060), (1098, 1168), (977, 1168), (963, 1134)
    )]))
    cellar.closeSubpath()
    cellar.setFillRule(Qt.WindingFill)
    result["cellar"] = cellar
    return result


def observation_text(observation: RoomObservation) -> str:
    lines = []
    if observation.observed_at is not None:
        lines.append("Seen at " + observation.observed_at)
    if observation.known_names:
        lines.append("Observed: " + ", ".join(observation.known_names))
    elif observation.unknown_remainder:
        lines.append("Occupants unknown.")
    else:
        lines.append("No one was observed.")
    if observation.unknown_remainder and observation.known_names:
        lines.append("Others may be present.")
    elif not observation.unknown_remainder:
        lines.append("Complete observation at that time.")
    if observation.light_on is not None:
        lines.append("Light observed " + ("on." if observation.light_on else "off."))
    if not observation.available:
        lines.append("Unavailable.")
    return "\n".join(lines)


def _font_style(font: QFont) -> str:
    family = font.family().replace("\\", "\\\\").replace('"', '\\"')
    size = f"{font.pixelSize()}px" if font.pixelSize() > 0 else f"{font.pointSizeF():g}pt"
    return f'font-family:"{family}"; font-size:{size};'


class RoomButton(QAbstractButton):
    """A real accessible button whose native hit area follows the room shape."""

    def __init__(self, room_id: str, owner):
        super().__init__(owner)
        self.room_id = room_id
        self.owner = owner
        self.local_path = QPainterPath()
        self.hovered = False
        self.setText(ROOM_LABELS[room_id])
        self.setAccessibleName(ROOM_LABELS[room_id])
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAutoRepeat(False)
        self.clicked.connect(lambda _checked=False: owner._selected(room_id))

    def set_path(self, path: QPainterPath):
        bounds = path.boundingRect().toAlignedRect()
        self.setGeometry(bounds)
        self.local_path = QTransform.fromTranslate(-bounds.x(), -bounds.y()).map(path)
        region = QRegion()
        for polygon in self.local_path.toSubpathPolygons():
            region = region.united(QRegion(polygon.toPolygon(), Qt.WindingFill))
        self.setMask(region)
        self.update()

    def hitButton(self, point):
        return self.local_path.contains(QPointF(point))

    def enterEvent(self, event):
        self.hovered = True
        self.owner._show_observation(self.room_id)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self.owner._show_observation(self.room_id)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if self.hasFocus() and self.isEnabled() and not event.isAutoRepeat():
                    self.click()
                event.accept()
                return
            if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                self.owner._move_focus(self.room_id, event.key())
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier):
            event.accept()
            return
        super().keyReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        observation = self.owner.observations[self.room_id]
        if observation.light_on is not None:
            p.fillPath(self.local_path, QColor(255, 255, 255, 15) if observation.light_on else QColor(0, 0, 0, 28))
        active = self.hovered or self.hasFocus() or self.owner.selected_room == self.room_id
        if active:
            p.fillPath(self.local_path, QColor(255, 255, 255, 12))
            p.setPen(QPen(QColor("#ededed" if self.isEnabled() else "#888888"), 1,
                          Qt.DotLine if self.hasFocus() else Qt.SolidLine))
            p.drawPath(self.local_path)
        elif not self.isEnabled():
            p.setPen(QPen(QColor("#666666"), 1, Qt.DashLine))
            p.drawPath(self.local_path)


class HouseView(QWidget):
    """Pure presentation. Room selection emits an ID and applies no game action."""

    room_selected = pyqtSignal(str)

    def __init__(self, artwork, *, body_font=None, heading_font=None, parent=None):
        super().__init__(parent)
        self.artwork = QPixmap(artwork) if isinstance(artwork, QPixmap) else QPixmap(str(artwork))
        if self.artwork.isNull() or (self.artwork.width(), self.artwork.height()) != ARTWORK_SIZE:
            raise ValueError("House artwork must be the readable 1122x1402 cutaway used by this hit map")
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 300)
        self.setAccessibleName("House rooms")
        self.paths = room_paths()
        self.observations = {record.room_id: record for record in unknown_observations()}
        self.selected_room = None
        self.info_room = None
        self.image_rect = QRectF()
        self.image_transform = QTransform()
        self.buttons = {room_id: RoomButton(room_id, self) for room_id in ROOM_LABELS}
        ordered = tuple(self.buttons.values())
        for first, second in zip(ordered, ordered[1:]):
            QWidget.setTabOrder(first, second)
        self.info_panel = QWidget(self)
        self.info_panel.setObjectName("roomObservation")
        self.info_panel.setStyleSheet("#roomObservation { background:#000; border-left:1px solid #777; }")
        layout = QVBoxLayout(self.info_panel)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(8)
        self.info_title = QLabel("ROOMS")
        self.info_title.setTextFormat(Qt.PlainText)
        self.info_body = QLabel("Point at a room\nor use Tab and the arrows.")
        self.info_body.setTextFormat(Qt.PlainText)
        self.info_body.setWordWrap(True)
        body_font = QFont(body_font if body_font is not None else self.font())
        heading_font = QFont(heading_font if heading_font is not None else body_font)
        self.info_title.setFont(heading_font)
        self.info_body.setFont(body_font)
        self.info_title.setStyleSheet(_font_style(heading_font) + "color:#eee; background:#000;")
        self.info_body.setStyleSheet(_font_style(body_font) + "color:#bcbcbc; background:#000;")
        layout.addWidget(self.info_title)
        layout.addWidget(self.info_body)
        self.set_observations(unknown_observations())

    def set_observations(self, records: Iterable[RoomObservation]):
        records = tuple(records)
        if any(not isinstance(record, RoomObservation) for record in records):
            raise TypeError("HouseView accepts public RoomObservation records only")
        if len(records) != len(ROOM_LABELS) or {record.room_id for record in records} != set(ROOM_LABELS):
            raise ValueError("Supply one public observation for each of the five rooms")
        self.observations = {record.room_id: record for record in records}
        for room_id, button in self.buttons.items():
            record = self.observations[room_id]
            button.setEnabled(record.available)
            button.setCursor(Qt.PointingHandCursor if record.available else Qt.ArrowCursor)
            button.setAccessibleDescription(observation_text(record))
            button.update()
        if self.info_room is not None:
            self._show_observation(self.info_room)
        self._layout_scene()

    def _show_observation(self, room_id):
        self.info_room = room_id
        self.info_title.setText(ROOM_LABELS[room_id].upper())
        self.info_body.setText(observation_text(self.observations[room_id]))
        self._place_info()

    def _place_info(self):
        side_width = int(self.image_rect.left()) - 24
        width = min(238, max(146, side_width))
        self.info_panel.setFixedWidth(width)
        self.info_panel.adjustSize()
        x = max(12, int(self.image_rect.left()) - width - 24)
        y = max(12, int(self.image_rect.top() + self.image_rect.height() * 0.38))
        if side_width < 146:
            y = max(12, self.height() - self.info_panel.height() - 12)
        y = min(y, max(0, self.height() - self.info_panel.height() - 8))
        self.info_panel.move(x, y)
        self.info_panel.raise_()

    def _layout_scene(self):
        bounds = self.contentsRect().adjusted(8, 8, -8, -8)
        scale = min(max(1, bounds.width()) / ARTWORK_SIZE[0], max(1, bounds.height()) / ARTWORK_SIZE[1])
        width, height = ARTWORK_SIZE[0] * scale, ARTWORK_SIZE[1] * scale
        self.image_rect = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
        self.image_transform = QTransform.fromTranslate(self.image_rect.x(), self.image_rect.y())
        self.image_transform.scale(scale, scale)
        for room_id, path in self.paths.items():
            self.buttons[room_id].set_path(self.image_transform.map(path))
        self._place_info()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_scene()

    def room_at(self, widget_point):
        inverse, invertible = self.image_transform.inverted()
        if not invertible:
            return None
        image_point = inverse.map(QPointF(widget_point))
        return next((room_id for room_id, path in self.paths.items() if path.contains(image_point)), None)

    def _selected(self, room_id):
        if not self.observations[room_id].available:
            return
        self.selected_room = room_id
        self._show_observation(room_id)
        for button in self.buttons.values():
            button.update()
        self.room_selected.emit(room_id)

    def _move_focus(self, room_id, key):
        vector = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
                  Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}[key]
        origin = self.paths[room_id].boundingRect().center()
        choices = []
        for other_id, path in self.paths.items():
            if other_id == room_id or not self.buttons[other_id].isEnabled():
                continue
            delta = path.boundingRect().center() - origin
            forward = delta.x() * vector[0] + delta.y() * vector[1]
            sideways = abs(delta.x() * vector[1] - delta.y() * vector[0])
            if forward > 1 and sideways <= forward:
                choices.append((2 * sideways + forward, tuple(ROOM_LABELS).index(other_id), other_id))
        if choices:
            self.buttons[min(choices)[2]].setFocus(Qt.OtherFocusReason)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawPixmap(self.image_rect, self.artwork, QRectF(self.artwork.rect()))
