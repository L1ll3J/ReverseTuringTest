"""Typed, literal display records and reading-position aware message rendering.

The caller owns game semantics and supplies records on the GUI thread. An
existing QTextEdit subclass keeps its input and anchor handling unchanged.
"""

from dataclasses import dataclass
from time import monotonic
from typing import Callable, Iterable, Literal

from PyQt5.QtCore import Qt, QSize, QTextBoundaryFinder, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget


@dataclass(frozen=True)
class DisplayRecord:
    record_id: str
    kind: Literal["speech", "event", "section"]
    text: str
    speaker: str | None = None
    speaker_id: str | None = None
    round_number: int | None = None
    phase: str | None = None

    def __post_init__(self):
        if not isinstance(self.record_id, str) or not self.record_id:
            raise ValueError("Display records require a nonempty ID")
        if self.kind not in ("speech", "event", "section"):
            raise ValueError("Unsupported display record kind")
        if not isinstance(self.text, str):
            raise TypeError("Display text must be a string")
        for name in ("speaker", "speaker_id", "phase"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be nonempty text when supplied")
        if self.kind == "speech" and not self.speaker:
            raise ValueError("Speech records require a speaker")
        if self.round_number is not None and (type(self.round_number) is not int or self.round_number < 0):
            raise ValueError("A display round must be a nonnegative integer")


class MessageLog(QWidget):
    """Wrap an existing editor without parsing its content or handling name clicks.

    Body and header text are inserted literally. ``name_format`` is a trusted
    formatter for speaker headers; it may provide an ``npc:`` anchor format.
    Existing editor content is retained until an explicit clear/set_records.
    """

    unread_changed = pyqtSignal(int)
    follow_tail_changed = pyqtSignal(bool)
    reveal_started = pyqtSignal(str)
    reveal_finished = pyqtSignal(str)

    def sizeHint(self):
        return QSize(1280, super().sizeHint().height())

    def __init__(self, editor: QTextEdit, *, body_font=None, speaker_font=None,
                 name_format: Callable[[DisplayRecord], QTextCharFormat] | None = None,
                 parent=None):
        super().__init__(parent)
        if not isinstance(editor, QTextEdit):
            raise TypeError("MessageLog requires a QTextEdit-compatible editor")
        self.editor = editor
        self.body_font = QFont(body_font if body_font is not None else editor.document().defaultFont())
        self.speaker_font = QFont(speaker_font if speaker_font is not None else self.body_font)
        if speaker_font is None:
            self.speaker_font.setBold(True)
        self.name_format = name_format
        self._records = []
        self._by_id = {}
        self._unread_count = 0
        self._rendering = False
        self._reveal_enabled = False
        self._revealing_id = None
        self._reveal_positions = ()
        self._reveal_extras = []
        self._reveal_visible = 0
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setInterval(16)
        self._reveal_timer.timeout.connect(self._advance_reveal)
        bar = editor.verticalScrollBar()
        self._follow_tail = bar.maximum() - bar.value() <= 1

        editor.setReadOnly(True)
        editor.setAcceptRichText(False)
        editor.document().setDefaultFont(self.body_font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(editor, 1)
        layout.setSpacing(4)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.verticalScrollBar().setStyleSheet('''
            QScrollBar:vertical { background:transparent; width:9px; margin:2px; }
            QScrollBar::handle:vertical { background:#4b4b4b; min-height:28px; border-radius:2px; }
            QScrollBar::handle:vertical:hover { background:#888; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
        ''')
        editor.document().setDocumentMargin(16)
        controls = QHBoxLayout()
        controls.addStretch()
        self.latest_button = QPushButton("Latest", self)
        self.latest_button.setFont(self.body_font)
        self.latest_button.setMinimumHeight(34)
        self.latest_button.setCursor(Qt.PointingHandCursor)
        self.latest_button.setStyleSheet(
            "QPushButton { color: #eeeae2; background: #242321; border: 1px solid #625f59;"
            " padding: 4px 14px; } QPushButton:hover { background: #363430; }"
            "QPushButton:focus { border-color: #eeeae2; }")
        self.latest_button.setAccessibleName("Jump to latest messages")
        self.latest_button.clicked.connect(self.jump_to_latest)
        self.latest_button.hide()
        controls.addWidget(self.latest_button)
        layout.addLayout(controls)
        bar = editor.verticalScrollBar()
        bar.valueChanged.connect(self._scroll_changed)
        bar.rangeChanged.connect(self._range_changed)
        editor.selectionChanged.connect(self._selection_changed)

    @property
    def revealing_record_id(self):
        return self._revealing_id

    def set_reveal_enabled(self, enabled):
        """Animate committed live speech without delaying canonical records or text."""
        if type(enabled) is not bool:
            raise TypeError("Speech reveal requires a boolean setting")
        self._reveal_enabled = enabled
        if not enabled:
            self.finish_reveal()

    def _selection_changed(self):
        if self.editor.textCursor().hasSelection():
            self.finish_reveal()

    def _start_reveal(self, record, start, end):
        finder = QTextBoundaryFinder(QTextBoundaryFinder.Grapheme, record.text)
        boundaries = [start]
        while True:
            boundary = finder.toNextBoundary()
            if boundary < 0:
                break
            boundaries.append(start + boundary)
        if len(boundaries) <= 1:
            return
        self._revealing_id = record.record_id
        self._reveal_positions = tuple(boundaries)
        self._reveal_end = end
        self._reveal_visible = 0
        self._reveal_extras = self.editor.extraSelections()
        self._reveal_started_at = monotonic()
        self._reveal_duration = min(.9, max(.24, len(boundaries) / 550))
        self._paint_reveal()
        self._reveal_timer.start()
        self.reveal_started.emit(record.record_id)

    def _paint_reveal(self):
        position = self._reveal_positions[self._reveal_visible]
        hidden = QTextEdit.ExtraSelection()
        hidden.cursor = QTextCursor(self.editor.document())
        hidden.cursor.setPosition(position)
        hidden.cursor.setPosition(self._reveal_end, QTextCursor.KeepAnchor)
        hidden.format.setForeground(QColor(0, 0, 0, 0))
        self.editor.setExtraSelections([*self._reveal_extras, hidden])
        # Follow the revealed line without changing the user's actual cursor.
        if self._follow_tail:
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(position)
            rect = self.editor.cursorRect(cursor)
            bar = self.editor.verticalScrollBar()
            self._rendering = True
            try:
                if rect.top() < 0:
                    bar.setValue(bar.value() + rect.top())
                elif rect.bottom() > self.editor.viewport().height():
                    bar.setValue(bar.value() + rect.bottom() - self.editor.viewport().height())
            finally:
                self._rendering = False

    def _advance_reveal(self):
        if self._revealing_id is None:
            return
        count = len(self._reveal_positions) - 1
        progress = (monotonic() - self._reveal_started_at) / self._reveal_duration
        self._reveal_visible = min(count, max(1, int(progress * count)))
        if self._reveal_visible == count:
            self.finish_reveal()
        else:
            self._paint_reveal()

    def finish_reveal(self):
        """Immediately expose any pending speech; safe on append, selection or clear."""
        if self._revealing_id is None:
            return
        record_id, self._revealing_id = self._revealing_id, None
        self._reveal_timer.stop()
        self.editor.setExtraSelections(self._reveal_extras)
        self._reveal_extras = []
        self._reveal_positions = ()
        if self._follow_tail:
            self._rendering = True
            try:
                self.editor.verticalScrollBar().setValue(self.editor.verticalScrollBar().maximum())
            finally:
                self._rendering = False
        self.reveal_finished.emit(record_id)

    @property
    def records(self):
        return tuple(self._records)

    @property
    def unread_count(self):
        return self._unread_count

    @property
    def follows_tail(self):
        return self._follow_tail

    def _at_tail(self):
        bar = self.editor.verticalScrollBar()
        return bar.maximum() - bar.value() <= 1

    def _set_follow_tail(self, value):
        if self._follow_tail != value:
            self._follow_tail = value
            self.follow_tail_changed.emit(value)

    def _set_unread(self, count):
        changed = self._unread_count != count
        self._unread_count = count
        self.latest_button.setText(f"Latest - {count} new" if count else "Latest")
        self.latest_button.setVisible(count > 0)
        if changed:
            self.unread_changed.emit(count)

    def _scroll_changed(self, _value):
        if self._rendering:
            return
        if self._revealing_id is not None:
            # A deliberate scroll takes precedence over presentation animation.
            self._set_follow_tail(self._at_tail())
            self.finish_reveal()
        at_tail = self._at_tail()
        self._set_follow_tail(at_tail)
        if at_tail:
            self._set_unread(0)

    def _range_changed(self, _minimum, maximum):
        if not self._rendering and self._revealing_id is not None:
            self._paint_reveal()
            return
        if not self._rendering and self._follow_tail:
            self._rendering = True
            try:
                self.editor.verticalScrollBar().setValue(maximum)
            finally:
                self._rendering = False

    def _prepare(self, records, existing):
        pending = []
        known = dict(existing)
        for record in records:
            if not isinstance(record, DisplayRecord):
                raise TypeError("MessageLog accepts DisplayRecord values")
            previous = known.get(record.record_id)
            if previous is not None:
                if previous != record:
                    raise ValueError("A display ID cannot identify different records")
                continue
            header = QTextCharFormat()
            header.setFont(self.speaker_font)
            if record.speaker and self.name_format is not None:
                supplied = self.name_format(record)
                if not isinstance(supplied, QTextCharFormat):
                    raise TypeError("The name formatter must return QTextCharFormat")
                header.merge(supplied)
            if record.speaker_id is None:
                header.setAnchor(False)
                header.setAnchorHref("")
                header.setAnchorNames([])
            pending.append((record, header))
            known[record.record_id] = record
        return pending

    def append_record(self, record: DisplayRecord):
        return bool(self.append_records((record,)))

    def append_records(self, records: Iterable[DisplayRecord]):
        pending = self._prepare(records, self._by_id)
        if not pending:
            return 0
        self._append_prepared(pending)
        return len(pending)

    def _append_prepared(self, pending, *, animate=True):
        self.finish_reveal()
        vertical = self.editor.verticalScrollBar()
        horizontal = self.editor.horizontalScrollBar()
        follow = self._at_tail()
        old_vertical, old_horizontal = vertical.value(), horizontal.value()
        selection = self.editor.textCursor()
        anchor, position = selection.anchor(), selection.position()
        preserve_cursor = selection.hasSelection() or not follow
        self._rendering = True
        try:
            cursor = QTextCursor(self.editor.document())
            cursor.movePosition(QTextCursor.End)
            cursor.beginEditBlock()
            for record, header in pending:
                body_range = self._render_record(cursor, record, header)
                self._records.append(record)
                self._by_id[record.record_id] = record
            cursor.endEditBlock()
            self._set_follow_tail(follow)
            if preserve_cursor:
                restored = QTextCursor(self.editor.document())
                restored.setPosition(anchor)
                restored.setPosition(position, QTextCursor.KeepAnchor)
                self.editor.setTextCursor(restored)
            vertical.setValue(vertical.maximum() if follow else old_vertical)
            horizontal.setValue(old_horizontal)
        finally:
            self._rendering = False
        self._set_unread(0 if follow else self._unread_count + len(pending))
        if (animate and self._reveal_enabled and follow and not preserve_cursor
                and len(pending) == 1 and record.kind == "speech" and body_range is not None):
            self._start_reveal(record, *body_range)

    def _render_record(self, cursor, record, header):
        body = QTextCharFormat()
        body.setFont(self.body_font)
        body.setAnchor(False)
        body.setAnchorHref("")
        body.setAnchorNames([])
        block = QTextBlockFormat()
        block.setLineHeight(130, QTextBlockFormat.ProportionalHeight)
        block.setTopMargin(0)
        block.setBottomMargin(9)
        if not cursor.document().isEmpty():
            cursor.insertBlock(block, body)
        else:
            cursor.setBlockFormat(block)
            cursor.setCharFormat(body)
        if record.kind == "section":
            block.setTopMargin(14)
            block.setBottomMargin(8)
            cursor.setBlockFormat(block)
            cursor.insertText(record.text, header)
            return
        if record.speaker:
            heading = QTextBlockFormat(block)
            heading.setTopMargin(4)
            heading.setBottomMargin(3)
            cursor.setBlockFormat(heading)
            cursor.insertText(record.speaker, header)
            cursor.insertBlock(block, body)
        start = cursor.position()
        cursor.insertText(record.text, body)
        return start, cursor.position()

    def set_records(self, records: Iterable[DisplayRecord], *, follow_tail=True):
        pending = self._prepare(records, {})
        self.clear()
        self._append_prepared(pending, animate=False)
        if not follow_tail:
            self.editor.verticalScrollBar().setValue(self.editor.verticalScrollBar().minimum())
            self._set_follow_tail(self._at_tail())
            self._set_unread(0)

    def clear(self):
        self.finish_reveal()
        self._rendering = True
        try:
            self.editor.clear()
            self._records.clear()
            self._by_id.clear()
            self._set_follow_tail(True)
        finally:
            self._rendering = False
        self._set_unread(0)

    def jump_to_latest(self):
        self._set_follow_tail(True)
        self._rendering = True
        try:
            bar = self.editor.verticalScrollBar()
            bar.setValue(bar.maximum())
        finally:
            self._rendering = False
        self._set_unread(0)
