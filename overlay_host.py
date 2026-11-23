"""One game-owned modal surface with inline help and deferred notifications.

All calls belong to the GUI thread. Accepted pages transfer widget ownership to
the host. Call cancel_owner before a restart/handoff; that cancellation is final
for this host instance. Colours and page presentation remain caller-owned.
"""

from collections import deque
from dataclasses import dataclass
import weakref

from PyQt5.QtCore import QEvent, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget


@dataclass(frozen=True)
class _QueuedPage:
    factory: object
    title: str
    help_text: str


def _apply_font(widget, font):
    widget.setFont(font)
    family = font.family().replace("\\", "\\\\").replace('"', '\\"')
    size = f"{font.pixelSize()}px" if font.pixelSize() > 0 else f"{font.pointSizeF():g}pt"
    weight = "bold" if font.bold() else "normal"
    style = "italic" if font.italic() else "normal"
    widget.setStyleSheet(f'font-family: "{family}"; font-size: {size}; font-weight: {weight}; font-style: {style};')


class OverlayHost(QDialog):
    page_opened = pyqtSignal(str)
    page_changed = pyqtSignal(str)
    page_closed = pyqtSignal(str)
    unlock_failed = pyqtSignal(str)

    def __init__(self, parent, *, body_font=None, title_font=None):
        if not isinstance(parent, QWidget):
            raise TypeError("OverlayHost requires its owning window")
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("gameOverlayHost")
        self.setWindowModality(Qt.WindowModal)
        self.setAccessibleName("Game overlay")
        self.resize(640, 480)
        self._current_page = None
        self._current_title = ""
        self._queue = deque()
        self._cancelled = False
        self._draining = False
        self._generation = 0
        self._queue_generation = 0
        self._focus_generation = 0
        self._return_focus = None
        self._page_focus = None
        self._selection_snapshot = None

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.PlainText)
        self.title_label.setWordWrap(True)
        if title_font is not None:
            _apply_font(self.title_label, title_font)
        header.addWidget(self.title_label, 1)
        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setCheckable(True)
        self.help_button.setAccessibleName("Show overlay help")
        self.help_button.setFocusPolicy(Qt.StrongFocus)
        self.help_button.toggled.connect(self._show_help)
        header.addWidget(self.help_button)
        self.close_button = QToolButton()
        self.close_button.setText("Close")
        self.close_button.setAccessibleName("Close overlay")
        self.close_button.setFocusPolicy(Qt.StrongFocus)
        self.close_button.clicked.connect(self.close_page)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.page_area = QWidget()
        self._page_layout = QVBoxLayout(self.page_area)
        self._page_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.page_area, 1)
        self.help_label = QLabel()
        self.help_label.setTextFormat(Qt.PlainText)
        self.help_label.setWordWrap(True)
        self.help_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.help_label.setAccessibleName("Overlay help")
        if body_font is not None:
            _apply_font(self.help_label, body_font)
            _apply_font(self.help_button, body_font)
            _apply_font(self.close_button, body_font)
        self.help_label.hide()
        layout.addWidget(self.help_label)

        self._queue_timer = QTimer(self)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.timeout.connect(self._drain_queue)
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.timeout.connect(self._focus_page)
        parent.installEventFilter(self)
        QApplication.instance().focusChanged.connect(self._focus_changed)

    @property
    def current_page(self):
        return self._current_page

    @property
    def current_title(self):
        return self._current_title

    @property
    def pending_unlock_count(self):
        return len(self._queue)

    @property
    def owner_cancelled(self):
        return self._cancelled

    @property
    def help_visible(self):
        return self.help_button.isChecked() and not self.help_label.isHidden()

    def _validate_page(self, page):
        if not isinstance(page, QWidget):
            raise TypeError("An overlay page must be a QWidget")
        if page is self or page.isAncestorOf(self):
            raise ValueError("An overlay cannot contain its owner or itself")

    def _owner_available(self):
        owner = self.parentWidget()
        return not self._cancelled and owner is not None and owner.isVisible() and not owner.isMinimized()

    def open_page(self, page, *, title, help_text=""):
        self._validate_page(page)
        if not self._owner_available() or self._draining or self._current_page is not None:
            return False
        focused = QApplication.focusWidget()
        self._return_focus = weakref.ref(focused) if focused is not None else None
        self._install_page(page, title, help_text)
        super().open()
        self._schedule_focus()
        self.page_opened.emit(title)
        return True

    def replace_page(self, page, *, title, help_text=""):
        self._validate_page(page)
        if not self._owner_available() or self._draining:
            return False
        if self._current_page is None:
            return self.open_page(page, title=title, help_text=help_text)
        if page is not self._current_page:
            self._retire_page()
        self._install_page(page, title, help_text)
        self._schedule_focus()
        self.page_changed.emit(title)
        return True

    def _install_page(self, page, title, help_text):
        self._generation += 1
        self._current_page = page
        self._current_title = title
        self.setWindowTitle(title)
        self.title_label.setText(title)
        self.help_button.setChecked(False)
        self.help_button.setVisible(bool(help_text))
        self.help_label.setText(help_text)
        self.help_label.hide()
        self._page_focus = None
        self._selection_snapshot = None
        if self._page_layout.indexOf(page) < 0:
            self._page_layout.addWidget(page)
        page.show()

    def _retire_page(self):
        page = self._current_page
        self._current_page = None
        self._current_title = ""
        self._page_focus = None
        self._selection_snapshot = None
        if page is not None:
            self._page_layout.removeWidget(page)
            page.hide()
            page.deleteLater()

    def _show_help(self, visible):
        focused = QApplication.focusWidget()
        page = self._current_page
        if page is not None and focused is not None and (focused is page or page.isAncestorOf(focused)):
            self._capture_selection(focused)
        self.help_label.setVisible(bool(visible and self.help_label.text()))
        self._restore_selection()
        if not visible and self._current_page is not None:
            self._schedule_focus()

    def _focus_changed(self, _old, current):
        page = self._current_page
        if page is not None and current is not None and (current is page or page.isAncestorOf(current)):
            self._page_focus = weakref.ref(current)
            current.installEventFilter(self)

    def _capture_selection(self, widget):
        if callable(getattr(widget, "textCursor", None)) and callable(getattr(widget, "setTextCursor", None)):
            self._selection_snapshot = (weakref.ref(widget), "cursor", widget.document().revision(),
                                        QTextCursor(widget.textCursor()))
        elif (callable(getattr(widget, "selectionStart", None))
              and callable(getattr(widget, "setSelection", None))):
            start, length, position = widget.selectionStart(), widget.selectionLength(), widget.cursorPosition()
            anchor = start + length if start >= 0 and position == start else start
            self._selection_snapshot = (weakref.ref(widget), "line", widget.text(),
                                        (anchor, position))

    def _restore_selection(self):
        if self._selection_snapshot is None:
            return
        reference, kind, revision, selection = self._selection_snapshot
        widget = reference()
        focused = self._page_focus() if self._page_focus is not None else None
        if widget is None or widget is not focused:
            return
        try:
            if kind == "cursor" and widget.document().revision() == revision:
                widget.setTextCursor(selection)
            elif kind == "line" and widget.text() == revision:
                anchor, position = selection
                if anchor < 0:
                    widget.setCursorPosition(position)
                else:
                    widget.setSelection(anchor, position - anchor)
        except RuntimeError:
            pass

    def _schedule_focus(self):
        self._focus_generation = self._generation
        self._focus_timer.start(0)

    def _focus_page(self):
        if (self._cancelled or self._focus_generation != self._generation
                or self._current_page is None or not self.isVisible()):
            return
        candidate = self._page_focus() if self._page_focus is not None else None
        try:
            if candidate is not None and candidate.isVisible() and candidate.isEnabled():
                candidate.setFocus(Qt.OtherFocusReason)
                self._restore_selection()
                return
        except RuntimeError:
            pass
        page = self._current_page
        for candidate in (page, *page.findChildren(QWidget)):
            if candidate.focusPolicy() != Qt.NoFocus and candidate.isEnabled() and candidate.isVisibleTo(self):
                candidate.setFocus(Qt.OtherFocusReason)
                self._restore_selection()
                return

    def close_page(self):
        self.done(QDialog.Rejected)

    def reject(self):
        self.close_page()

    def accept(self):
        self.done(QDialog.Accepted)

    def done(self, result):
        if self._current_page is None:
            if self.isVisible():
                super().done(result)
            return
        title = self._current_title
        had_page = self._current_page is not None
        self._generation += 1
        self._focus_timer.stop()
        self._retire_page()
        super().done(result)
        if had_page:
            self.page_closed.emit(title)
        if not self._cancelled and self._current_page is None:
            self._restore_focus()
            self._schedule_next()

    def _restore_focus(self):
        candidate = self._return_focus() if self._return_focus is not None else None
        self._return_focus = None
        try:
            if candidate is not None and candidate.isVisible() and candidate.isEnabled():
                owner = self.parentWidget()
                if owner is not None:
                    owner.activateWindow()
                candidate.setFocus(Qt.OtherFocusReason)
        except RuntimeError:
            pass

    def enqueue_unlock(self, factory, *, title, help_text=""):
        if not callable(factory):
            raise TypeError("An unlock notification requires a page factory")
        if self._cancelled:
            return False
        self._queue.append(_QueuedPage(factory, title, help_text))
        self._schedule_next()
        return True

    def _schedule_next(self):
        if (self._cancelled or self._draining or self._current_page is not None
                or not self._queue):
            return
        if self._queue_timer.isActive():
            if self._queue_generation == self._generation:
                return
            self._queue_timer.stop()
        self._queue_generation = self._generation
        self._queue_timer.start(0)

    def _drain_queue(self):
        if (self._cancelled or self._queue_generation != self._generation
                or self._current_page is not None or not self._owner_available() or not self._queue):
            return
        queued = self._queue.popleft()
        self._draining = True
        try:
            page = queued.factory()
            self._validate_page(page)
        except Exception:
            self._draining = False
            if not self._cancelled:
                self.unlock_failed.emit("An unlock notification could not be displayed.")
                self._schedule_next()
            return
        self._draining = False
        if self._cancelled or not self._owner_available():
            page.deleteLater()
            return
        self.open_page(page, title=queued.title, help_text=queued.help_text)

    def cancel_owner(self):
        if self._cancelled:
            return
        self._cancelled = True
        self._generation += 1
        self._queue.clear()
        self._queue_timer.stop()
        self._focus_timer.stop()
        self._return_focus = None
        if self._current_page is not None or self.isVisible():
            self.done(QDialog.Rejected)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget():
            if event.type() == QEvent.Close:
                self.cancel_owner()
            elif event.type() == QEvent.Hide:
                self._generation += 1
                self._queue_timer.stop()
            elif event.type() in (QEvent.Show, QEvent.WindowStateChange):
                self._schedule_next()
        elif event.type() == QEvent.FocusOut and self._current_page is not None:
            page = self._current_page
            if watched is page or page.isAncestorOf(watched):
                self._capture_selection(watched)
        return super().eventFilter(watched, event)
