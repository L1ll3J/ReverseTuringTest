"""Small QTextEdit-compatible input affordances for the conversation."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QTextEdit


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
