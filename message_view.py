"""Typed, literal display records and reading-position aware message rendering.

The caller owns game semantics and supplies records on the GUI thread. An
existing QTextEdit subclass keeps its input and anchor handling unchanged.
"""

from dataclasses import dataclass
from typing import Callable, Iterable, Literal

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
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
        bar = editor.verticalScrollBar()
        self._follow_tail = bar.maximum() - bar.value() <= 1

        editor.setReadOnly(True)
        editor.setAcceptRichText(False)
        editor.document().setDefaultFont(self.body_font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(editor, 1)
        controls = QHBoxLayout()
        controls.addStretch()
        self.latest_button = QPushButton("Latest", self)
        self.latest_button.setFont(self.body_font)
        self.latest_button.setAccessibleName("Jump to latest messages")
        self.latest_button.clicked.connect(self.jump_to_latest)
        self.latest_button.hide()
        controls.addWidget(self.latest_button)
        layout.addLayout(controls)
        bar = editor.verticalScrollBar()
        bar.valueChanged.connect(self._scroll_changed)
        bar.rangeChanged.connect(self._range_changed)

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
        at_tail = self._at_tail()
        self._set_follow_tail(at_tail)
        if at_tail:
            self._set_unread(0)

    def _range_changed(self, _minimum, maximum):
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

    def _append_prepared(self, pending):
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
                self._render_record(cursor, record, header)
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

    def _render_record(self, cursor, record, header):
        body = QTextCharFormat()
        body.setFont(self.body_font)
        body.setAnchor(False)
        body.setAnchorHref("")
        body.setAnchorNames([])
        block = QTextBlockFormat()
        block.setLineHeight(130, QTextBlockFormat.ProportionalHeight)
        block.setTopMargin(0)
        block.setBottomMargin(10)
        if not cursor.document().isEmpty():
            cursor.insertBlock(block, body)
        else:
            cursor.setBlockFormat(block)
            cursor.setCharFormat(body)
        if record.kind == "section":
            block.setTopMargin(12)
            cursor.setBlockFormat(block)
            cursor.insertText(record.text, header)
            return
        if record.speaker:
            heading = QTextBlockFormat(block)
            heading.setTopMargin(8)
            heading.setBottomMargin(3)
            cursor.setBlockFormat(heading)
            cursor.insertText(record.speaker, header)
            cursor.insertBlock(block, body)
        cursor.insertText(record.text, body)

    def set_records(self, records: Iterable[DisplayRecord], *, follow_tail=True):
        pending = self._prepare(records, {})
        self.clear()
        self._append_prepared(pending)
        if not follow_tail:
            self.editor.verticalScrollBar().setValue(self.editor.verticalScrollBar().minimum())
            self._set_follow_tail(self._at_tail())
            self._set_unread(0)

    def clear(self):
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
