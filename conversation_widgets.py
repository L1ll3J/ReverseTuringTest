"""Small QTextEdit-compatible input affordances for the conversation."""

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import Qt, QRectF, QSize, QPoint, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap, QRegion, QBitmap, QRadialGradient, QPolygon
from PyQt5.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QSizePolicy, QTextEdit, QVBoxLayout, QWidget


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


def _unknown_body_art():
    """An anonymous physical silhouette, with no inferred identity or personality."""
    image = QPixmap(48, 104)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#242424"))
    painter.drawPolygon(QPolygon([QPoint(17, 5), QPoint(31, 5), QPoint(35, 12),
                                 QPoint(33, 27), QPoint(29, 31), QPoint(19, 31),
                                 QPoint(15, 24), QPoint(14, 13)]))
    painter.setBrush(QColor("#3d3d3d"))
    painter.drawPolygon(QPolygon([QPoint(17, 30), QPoint(31, 30), QPoint(40, 39),
                                 QPoint(44, 72), QPoint(38, 76), QPoint(34, 53),
                                 QPoint(33, 77), QPoint(30, 99), QPoint(23, 99),
                                 QPoint(22, 78), QPoint(19, 99), QPoint(12, 99),
                                 QPoint(13, 75), QPoint(13, 53), QPoint(9, 76),
                                 QPoint(3, 73), QPoint(8, 40)]))
    painter.setBrush(QColor("#65615c"))
    painter.drawRect(18, 12, 3, 12)
    painter.drawRect(18, 31, 3, 28)
    painter.drawRect(12, 42, 3, 17)
    painter.setBrush(QColor("#1a1a1a"))
    painter.drawRect(23, 37, 3, 39)
    painter.drawRect(10, 98, 11, 5)
    painter.drawRect(23, 98, 11, 5)
    painter.end()
    return image


class _StageBody(QAbstractButton):
    def __init__(self, participant, name_font, parent=None):
        super().__init__(parent)
        self.participant = participant
        self.active = False
        self.setFont(name_font)
        self.setText(participant.name)
        self.setAccessibleName(f"Address {participant.name}")
        self.setToolTip(f"Address {participant.name}")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.photo = QPixmap(participant.photo) if participant.photo is not None else QPixmap()
        self.uses_fallback_art = self.photo.isNull()
        if self.uses_fallback_art:
            self.photo = _unknown_body_art()
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
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        name_height = self.fontMetrics().height() + 12
        art = QRectF(8, 6, max(0, self.width() - 16), max(0, self.height() - name_height - 12))
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
            painter.fillRect(self.width() // 2 - 20, self.height() - 3, 40, 2, QColor("#f0eee8"))


class ConversationStage(QWidget):
    """Compact native Qt cast stage. The caller owns phase and speaker state.

    Supply artwork by body ID through StageParticipant. No character appearance
    or speaker identity is inferred from dialogue text or model responses.
    """

    speaker_chosen = pyqtSignal(str)

    def __init__(self, parent=None, *, name_font=None, body_font=None):
        super().__init__(parent)
        self.setObjectName("conversationStage")
        self.setAccessibleName("Conversation participants")
        self.setMinimumHeight(150)
        self.setMaximumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.name_font = QFont(name_font or self.font())
        if name_font is None:
            self.name_font.setPixelSize(12)
        self._participants = ()
        self._buttons = {}
        self._active_speaker = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 0, 16, 0)
        outer.setSpacing(4)
        self._layout = QHBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        outer.addLayout(self._layout, 1)
        self.reply_notice = QLabel(self)
        self.reply_notice.setAlignment(Qt.AlignCenter)
        notice_font = QFont(body_font or QFontDatabase.systemFont(QFontDatabase.GeneralFont))
        notice_font.setPixelSize(15)
        self.reply_notice.setFont(notice_font)
        self.reply_notice.setStyleSheet(f'font-family:"{notice_font.family()}"; font-size:15px; color:#d6d0c3; background:transparent; border:none; padding:3px;')
        self.reply_notice.setWordWrap(True)
        self.reply_notice.hide()
        outer.addWidget(self.reply_notice)

    @property
    def participants(self):
        return self._participants

    @property
    def active_speaker(self):
        return self._active_speaker

    def sizeHint(self):
        return QSize(1280, 192)

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
        self.update()

    def set_required_reply(self, name):
        """Show a current player-facing requirement supplied by the game state."""
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError("A required reply needs a current display name")
        self.reply_notice.setText(f"{name}, you have been addressed. Reply to continue." if name else "")
        self.reply_notice.setVisible(name is not None)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        active = self._buttons.get(self._active_speaker)
        if active is not None:
            centre = active.geometry().center()
            glow = QRadialGradient(centre.x(), self.height() * .37, self.height() * 1.05)
            glow.setColorAt(0, QColor(221, 216, 205, 25))
            glow.setColorAt(.65, QColor(221, 216, 205, 8))
            glow.setColorAt(1, QColor(221, 216, 205, 0))
            painter.fillRect(self.rect(), glow)
