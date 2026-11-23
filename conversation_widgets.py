"""Small QTextEdit-compatible input affordances for the conversation."""

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import Qt, QRectF, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap, QRegion, QBitmap, QRadialGradient
from PyQt5.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QTextEdit, QWidget


class DialogueView(QTextEdit):
    speaker_chosen = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        cursor = Qt.PointingHandCursor if self.anchorAt(event.pos()).startswith("npc:") else Qt.IBeamCursor
        self.viewport().setCursor(cursor)

    def mouseReleaseEvent(self, event):
        anchor = self.anchorAt(event.pos())
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and anchor.startswith("npc:") and not self.textCursor().hasSelection():
            self.speaker_chosen.emit(anchor[4:])


@dataclass(frozen=True)
class StageParticipant:
    """A visible body and its presentation, independent of the occupying mind."""

    body_id: str
    name: str
    photo: str | QPixmap | None = None

    def __post_init__(self):
        if not isinstance(self.body_id, str) or not self.body_id.strip():
            raise ValueError("A stage participant requires a body ID")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("A stage participant requires a name")
        if self.photo is not None and not isinstance(self.photo, (str, QPixmap)):
            raise TypeError("Stage artwork must be a file path or QPixmap")


def character_art_path(name):
    """Return provisional cast artwork for a known original character, if present."""
    if name not in {"Dave", "Hank", "Jerry", "Owen", "Debra"}:
        return None
    path = Path(__file__).resolve().parent / "icons" / "characters" / f"{name.lower()}-candidate-v1.png"
    return str(path) if path.is_file() else None


class _StageBody(QAbstractButton):
    def __init__(self, participant, name_font, parent=None):
        super().__init__(parent)
        self.participant = participant
        self.active = False
        self.setFont(name_font)
        self.setText(participant.name)
        self.setAccessibleName(f"Choose {participant.name} as next speaker")
        self.setToolTip(participant.name)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.photo = QPixmap(participant.photo) if participant.photo is not None else QPixmap()
        if not self.photo.isNull():
            # Trim transparent padding once, preserving the original source asset.
            mask = QBitmap.fromImage(self.photo.toImage().createAlphaMask())
            bounds = QRegion(mask).boundingRect()
            if not bounds.isEmpty():
                self.photo = self.photo.copy(bounds)

    def sizeHint(self):
        return QSize(110, 192)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#090909"))
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        name_height = self.fontMetrics().height() + 12
        art = QRectF(8, 6, max(0, self.width() - 16), max(0, self.height() - name_height - 12))
        if self.active:
            glow = QRadialGradient(art.center(), max(art.width(), art.height()) * .65)
            glow.setColorAt(0, QColor(255, 255, 255, 24))
            glow.setColorAt(1, QColor(255, 255, 255, 0))
            painter.fillRect(art, glow)
        if not self.photo.isNull() and art.width() > 0 and art.height() > 0:
            size = self.photo.size().scaled(art.size().toSize(), Qt.KeepAspectRatio)
            target = QRectF(art.center().x() - size.width() / 2, art.bottom() - size.height(), size.width(), size.height())
            painter.setOpacity(1.0 if self.active else .30)
            painter.drawPixmap(target.toRect(), self.photo)
            painter.setOpacity(1.0)
        painter.setFont(self.font())
        painter.setPen(QColor("#f0eee8") if self.active else QColor("#96938e"))
        name_rect = self.rect().adjusted(4, self.height() - name_height, -4, -4)
        text = self.fontMetrics().elidedText(self.participant.name, Qt.ElideRight, name_rect.width())
        painter.drawText(name_rect, Qt.AlignHCenter | Qt.AlignVCenter, text)
        if self.active:
            painter.fillRect(self.width() // 2 - 12, self.height() - 3, 24, 2, QColor("#d5d2c9"))
        if self.hasFocus():
            painter.setPen(QColor("#96938e"))
            painter.drawRect(self.rect().adjusted(2, 2, -3, -3))


class ConversationStage(QWidget):
    """Compact native Qt cast stage. The caller owns phase and speaker state.

    Supply artwork by body ID through StageParticipant. No character appearance
    or speaker identity is inferred from dialogue text or model responses.
    """

    speaker_chosen = pyqtSignal(str)

    def __init__(self, parent=None, *, name_font=None):
        super().__init__(parent)
        self.setObjectName("conversationStage")
        self.setAccessibleName("Conversation participants")
        self.setMinimumHeight(150)
        self.setMaximumHeight(216)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.name_font = QFont(name_font or self.font())
        if name_font is None:
            self.name_font.setPixelSize(12)
        self._participants = ()
        self._buttons = {}
        self._active_speaker = None
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 0, 12, 0)
        self._layout.setSpacing(8)

    @property
    def participants(self):
        return self._participants

    @property
    def active_speaker(self):
        return self._active_speaker

    def set_participants(self, participants):
        participants = tuple(participants)
        if any(not isinstance(p, StageParticipant) for p in participants):
            raise TypeError("ConversationStage requires StageParticipant values")
        if len({p.body_id for p in participants}) != len(participants):
            raise ValueError("Stage body IDs must be unique")
        # Validate the whole snapshot before replacing visible widgets.
        for button in self._buttons.values():
            self._layout.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._buttons.clear()
        self._participants = participants
        for participant in participants:
            button = _StageBody(participant, self.name_font, self)
            button.clicked.connect(lambda checked=False, body_id=participant.body_id: self.speaker_chosen.emit(body_id))
            self._buttons[participant.body_id] = button
            self._layout.addWidget(button, 1)
        self.set_active_speaker(self._active_speaker)

    def set_active_speaker(self, body_id):
        self._active_speaker = body_id if body_id in self._buttons else None
        for participant_id, button in self._buttons.items():
            button.active = participant_id == self._active_speaker
            button.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#090909"))
        painter.setPen(QColor("#292827"))
        painter.drawLine(12, self.height() - 1, self.width() - 12, self.height() - 1)
