"""Local, opt-in dictation bindings and a capture page with no global hotkeys."""

from dataclasses import dataclass
import json
import weakref

from PyQt5.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class BindingError(ValueError):
    pass


_MODIFIERS = int(Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
_QUALIFIERS = int(Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
_MODIFIER_KEYS = {int(key) for key in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Meta, Qt.Key_Alt, Qt.Key_AltGr)}
_COMMAND_KEYS = {int(key) for key in (Qt.Key_PageUp, Qt.Key_PageDown,
                                    Qt.Key_Pause, Qt.Key_Print, Qt.Key_ScrollLock)}
_MOUSE_BUTTONS = {"mouse4": int(Qt.ExtraButton1), "mouse5": int(Qt.ExtraButton2)}
_EDIT_ACTIONS = (QKeySequence.Copy, QKeySequence.Cut, QKeySequence.Paste,
                 QKeySequence.Undo, QKeySequence.Redo, QKeySequence.SelectAll)


def _key_problem(key, modifiers):
    if key in _MODIFIER_KEYS or key in (0, int(Qt.Key_unknown)):
        return "Choose a complete key or shortcut, rather than a modifier by itself."
    if modifiers & ~_MODIFIERS:
        return "Text-composition keys cannot be used for dictation."
    if key == int(Qt.Key_Escape):
        return "Escape is reserved for closing and cancelling."
    if key in (int(Qt.Key_Return), int(Qt.Key_Enter)):
        return "Enter is reserved for sending or confirming."
    if key in (int(Qt.Key_Tab), int(Qt.Key_Backtab)):
        return "Tab is reserved for moving keyboard focus."
    if key in (int(Qt.Key_Backspace), int(Qt.Key_Delete), int(Qt.Key_Insert)):
        return "Text editing keys stay available for editing."
    if modifiers & int(Qt.ControlModifier | Qt.MetaModifier) and key in {
            int(value) for value in (Qt.Key_A, Qt.Key_C, Qt.Key_V, Qt.Key_X, Qt.Key_Y, Qt.Key_Z)}:
        return "This shortcut is reserved for text editing."
    if ((key == int(Qt.Key_F4) and modifiers & int(Qt.AltModifier))
            or (key in (int(Qt.Key_Q), int(Qt.Key_W)) and modifiers & int(Qt.ControlModifier | Qt.MetaModifier))
            or (key == int(Qt.Key_Space) and modifiers & int(Qt.AltModifier))):
        return "This shortcut is reserved for window or application controls."
    printable = int(Qt.Key_Space) <= key <= 0x10FFFF and chr(key).isprintable()
    if printable:
        if not modifiers & _QUALIFIERS:
            return "Letters and typing characters stay available. Add Ctrl, Alt or Meta, or choose PgUp, PgDn or an F-key."
        if modifiers & int(Qt.ControlModifier) and modifiers & int(Qt.AltModifier):
            return "Ctrl+Alt typing keys can be used by AltGr. Choose another shortcut."
        return None
    if key in _COMMAND_KEYS or int(Qt.Key_F1) <= key <= int(Qt.Key_F35):
        return None
    return "Choose a qualified typing key, PgUp, PgDn, an F-key, or mouse button 4 or 5."


@dataclass(frozen=True)
class InputBinding:
    kind: str = "unbound"
    key: int = 0
    modifiers: int = 0
    button: str = ""

    def __post_init__(self):
        if self.kind not in ("unbound", "key", "mouse"):
            raise BindingError("Unknown binding kind")
        if type(self.key) is not int or type(self.modifiers) is not int:
            raise BindingError("Key and modifier values must be integers")
        if not isinstance(self.button, str):
            raise BindingError("A mouse button identifier must be text")
        if self.kind == "unbound":
            if self.key or self.modifiers or self.button:
                raise BindingError("An unbound action cannot contain input data")
        elif self.kind == "mouse":
            if self.button not in _MOUSE_BUTTONS or self.key or self.modifiers:
                raise BindingError("Choose mouse button 4 or 5")
        else:
            if self.button:
                raise BindingError("A key binding cannot contain a mouse button")
            problem = _key_problem(self.key, self.modifiers)
            if problem:
                raise BindingError(problem)

    @classmethod
    def for_key(cls, key, modifiers=Qt.NoModifier):
        return cls("key", int(key), int(modifiers) & ~int(Qt.KeypadModifier))

    @classmethod
    def for_mouse(cls, button):
        return cls("mouse", button=button)

    @property
    def label(self):
        if self.kind == "unbound":
            return "Unbound"
        if self.kind == "mouse":
            return "Mouse 4" if self.button == "mouse4" else "Mouse 5"
        return QKeySequence(self.key | self.modifiers).toString(QKeySequence.NativeText)

    def to_json(self):
        value = {"version": 1, "kind": self.kind}
        if self.kind == "key":
            value.update(key=self.key, modifiers=self.modifiers)
        elif self.kind == "mouse":
            value["button"] = self.button
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value):
        if not isinstance(value, str):
            return cls()
        try:
            data = json.loads(value)
            if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
                return cls()
            kind = data.get("kind")
            expected = ({"version", "kind", "key", "modifiers"} if kind == "key" else
                        {"version", "kind", "button"} if kind == "mouse" else {"version", "kind"})
            if set(data) != expected:
                return cls()
            return cls(kind, data.get("key", 0), data.get("modifiers", 0), data.get("button", ""))
        except (BindingError, TypeError, ValueError):
            return cls()


class DictationBindings(QObject):
    """Dispatch only inside one active game window; capture is explicitly armed.

    The Qt application event filter is local to this process and owner. It does
    not register OS shortcuts or consume typing keys before ignoring an action.
    """

    activated = pyqtSignal()
    activation_failed = pyqtSignal(str)
    binding_changed = pyqtSignal(object)
    capture_started = pyqtSignal()
    capture_cancelled = pyqtSignal()
    binding_captured = pyqtSignal(object)
    capture_rejected = pyqtSignal(str)

    def __init__(self, owner, action, *, binding=None, can_trigger=None, conflict_reason=None):
        if not isinstance(owner, QWidget) or not callable(action):
            raise TypeError("Bindings require an owning widget and an action")
        super().__init__(owner)
        self.owner = owner
        self.action = action
        self.can_trigger = can_trigger if can_trigger is not None else lambda: True
        self.extra_conflict_reason = conflict_reason
        self._binding = InputBinding()
        self._capture = None
        self._held_keys = set()
        self._held_mouse = set()
        self._closed = False
        self.initial_problem = None
        if binding is not None:
            try:
                self.initial_problem = self.conflict_reason(binding)
            except Exception:
                self.initial_problem = "Could not check the saved binding. It remains unbound."
            if self.initial_problem is None:
                self._binding = binding
        owner.destroyed.connect(self.close)
        QApplication.instance().installEventFilter(self)

    @property
    def binding(self):
        return self._binding

    @property
    def is_capturing(self):
        return self._capture is not None and self._capture() is not None

    @property
    def capture_widget(self):
        return self._capture() if self._capture is not None else None

    def conflict_reason(self, binding):
        if not isinstance(binding, InputBinding):
            raise TypeError("A dictation binding must be an InputBinding")
        if binding.kind == "key":
            sequence = QKeySequence(binding.key | binding.modifiers)
            if any(sequence == shortcut for action in _EDIT_ACTIONS
                   for shortcut in QKeySequence.keyBindings(action)):
                return "This shortcut is reserved for text editing."
        if binding.kind != "unbound" and self.extra_conflict_reason is not None:
            reason = self.extra_conflict_reason(binding)
            if reason is not None and not isinstance(reason, str):
                raise TypeError("A conflict reason must be text or None")
            return reason or None
        return None

    def set_binding(self, binding):
        problem = self.conflict_reason(binding)
        if problem:
            raise BindingError(problem)
        if self._binding != binding:
            self._binding = binding
            self.binding_changed.emit(binding)

    @staticmethod
    def _descends_from(widget, ancestor):
        while isinstance(widget, QWidget):
            if widget is ancestor:
                return True
            widget = widget.parentWidget()
        return False

    def _owns(self, widget):
        return self._descends_from(widget, self.owner)

    def begin_capture(self, widget):
        if self._closed or not self._owns(widget) or not widget.isVisible():
            return False
        self.cancel_capture()
        self._capture = weakref.ref(widget)
        self.capture_started.emit()
        return True

    def cancel_capture(self):
        if self._capture is not None:
            self._capture = None
            self.capture_cancelled.emit()

    def close(self):
        if not self._closed:
            self._closed = True
            self.cancel_capture()
            self._held_keys.clear()
            self._held_mouse.clear()
            application = QApplication.instance()
            if application is not None:
                application.removeEventFilter(self)

    def _normal_allowed(self):
        if (self._closed or not self.owner.isVisible() or not self.owner.window().isActiveWindow()
                or QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None):
            return False
        try:
            return bool(self.can_trigger())
        except Exception:
            return False

    def _matches_key(self, event):
        return (self._binding.kind == "key" and event.key() == self._binding.key
                and (int(event.modifiers()) & ~int(Qt.KeypadModifier)) == self._binding.modifiers)

    def _matches_mouse(self, event):
        return self._binding.kind == "mouse" and int(event.button()) == _MOUSE_BUTTONS[self._binding.button]

    def _capture_binding(self, candidate):
        try:
            problem = self.conflict_reason(candidate)
        except Exception:
            self.capture_rejected.emit("Could not check this binding. Please try again.")
            return
        if problem:
            self.capture_rejected.emit(problem)
            return
        self._capture = None
        self.binding_captured.emit(candidate)

    def _activate(self):
        try:
            self.action()
        except Exception:
            self.activation_failed.emit("Voice recording could not be toggled. Please try again.")
        else:
            self.activated.emit()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.ApplicationDeactivate or (kind == QEvent.WindowDeactivate and self._owns(watched)):
            self._held_keys.clear()
            self._held_mouse.clear()
            self.cancel_capture()
            return False
        if watched is self.owner and kind in (QEvent.Close, QEvent.DeferredDelete):
            self.close()
            return False
        capturing = self._capture() if self._capture is not None else None
        if capturing is not None and kind in (QEvent.Hide, QEvent.Close, QEvent.DeferredDelete):
            if self._descends_from(capturing, watched):
                self.cancel_capture()
        if not self._owns(watched) or self._closed:
            return False
        if kind == QEvent.KeyRelease:
            held = event.key() in self._held_keys
            if not event.isAutoRepeat():
                self._held_keys.discard(event.key())
            return held
        if kind == QEvent.MouseButtonRelease:
            held = int(event.button()) in self._held_mouse
            self._held_mouse.discard(int(event.button()))
            return held
        capturing = self._capture() if self._capture is not None else None
        if kind == QEvent.ShortcutOverride:
            if capturing is not None or (self._matches_key(event) and self._normal_allowed()):
                event.accept()
                return True
            return False
        if kind == QEvent.KeyPress:
            if capturing is not None:
                if event.key() == Qt.Key_Escape:
                    self.cancel_capture()
                elif not event.isAutoRepeat() and event.key() not in _MODIFIER_KEYS:
                    try:
                        candidate = InputBinding.for_key(event.key(), event.modifiers())
                    except BindingError as error:
                        self.capture_rejected.emit(str(error))
                    else:
                        self._held_keys.add(event.key())
                        self._capture_binding(candidate)
                return True
            if self._matches_key(event) and self._normal_allowed():
                if not event.isAutoRepeat() and event.key() not in self._held_keys:
                    self._held_keys.add(event.key())
                    self._activate()
                return True
        if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            if capturing is not None and int(event.button()) in _MOUSE_BUTTONS.values():
                self._held_mouse.add(int(event.button()))
                name = next(name for name, number in _MOUSE_BUTTONS.items() if number == int(event.button()))
                self._capture_binding(InputBinding.for_mouse(name))
                return True
            if self._matches_mouse(event) and self._normal_allowed():
                if int(event.button()) not in self._held_mouse:
                    self._held_mouse.add(int(event.button()))
                    self._activate()
                return True
        return False


def _font_role(widget, font):
    if font is None:
        return
    widget.setFont(font)
    family = font.family().replace("\\", "\\\\").replace('"', '\\"')
    size = f"{font.pixelSize()}px" if font.pixelSize() > 0 else f"{font.pointSizeF():g}pt"
    widget.setStyleSheet(f'font-family: "{family}"; font-size: {size};')


class BindingBadge(QLabel):
    """A persistent assigned-input label; unbound actions show no badge."""

    def __init__(self, controller, *, font=None, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setAccessibleName("Dictation shortcut")
        _font_role(self, font)
        controller.binding_changed.connect(self._changed)
        self._changed(controller.binding)

    def _changed(self, binding):
        self.setText(binding.label if binding.kind != "unbound" else "")
        self.setVisible(binding.kind != "unbound")


class BindingSettingsPage(QWidget):
    """Page content for an existing OverlayHost; it never opens a dialog."""

    def __init__(self, controller, save_callback, *, body_font=None, heading_font=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.save_callback = save_callback
        self._capturing = False
        layout = QVBoxLayout(self)
        heading = QLabel("Voice control")
        heading.setTextFormat(Qt.PlainText)
        _font_role(heading, heading_font)
        layout.addWidget(heading)
        self.binding_label = QLabel(controller.binding.label)
        self.binding_label.setTextFormat(Qt.PlainText)
        self.binding_label.setAccessibleName("Current dictation binding")
        _font_role(self.binding_label, body_font)
        layout.addWidget(self.binding_label)
        buttons = QHBoxLayout()
        self.capture_button = QPushButton("Change binding")
        self.capture_button.setAccessibleName("Change dictation binding")
        self.capture_button.clicked.connect(self._toggle_capture)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setAccessibleName("Clear dictation binding")
        self.clear_button.clicked.connect(self._clear)
        for button in (self.capture_button, self.clear_button):
            _font_role(button, heading_font)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status_label = QLabel(controller.initial_problem or "")
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setWordWrap(True)
        _font_role(self.status_label, body_font)
        self.status_label.setVisible(bool(self.status_label.text()))
        layout.addWidget(self.status_label)
        layout.addStretch()
        controller.capture_started.connect(self._capture_started)
        controller.capture_cancelled.connect(self._capture_cancelled)
        controller.capture_rejected.connect(self._rejected)
        controller.binding_captured.connect(self._captured)
        controller.binding_changed.connect(self._changed)

    def _toggle_capture(self):
        if self.controller.is_capturing:
            self.controller.cancel_capture()
        else:
            self.controller.begin_capture(self)

    def _capture_started(self):
        if self.controller.capture_widget is not self:
            return
        self._capturing = True
        self.capture_button.setText("Cancel capture")
        self.status_label.setText("Press a shortcut, PgUp/PgDn, an F-key, or mouse button 4/5. Escape cancels.")
        self.status_label.show()

    def _capture_cancelled(self):
        if not self._capturing:
            return
        self._capturing = False
        self.capture_button.setText("Change binding")
        self.status_label.clear()
        self.status_label.hide()

    def _rejected(self, reason):
        if not self._capturing:
            return
        self.status_label.setText(reason)
        self.status_label.show()

    def _captured(self, binding):
        if not self._capturing:
            return
        self._capturing = False
        self.capture_button.setText("Change binding")
        self._save(binding)

    def _changed(self, binding):
        self.binding_label.setText(binding.label)

    def _clear(self):
        self.controller.cancel_capture()
        self._save(InputBinding())

    def _save(self, binding):
        try:
            problem = self.controller.conflict_reason(binding)
            if problem:
                raise BindingError(problem)
            saved = self.save_callback(binding)
            if saved is not True:
                raise OSError("Binding save did not succeed")
            self.controller.set_binding(binding)
        except BindingError as error:
            self.status_label.setText(str(error))
            self.status_label.show()
        except Exception:
            self.status_label.setText("Could not save this binding. Your previous choice is still active.")
            self.status_label.show()
        else:
            self.status_label.clear()
            self.status_label.hide()
