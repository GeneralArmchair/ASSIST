"""Global hotkeys through the Win32 RegisterHotKey API, and the dialog for rebinding them.

Everything ships unbound (a registered key is taken away from the game, and conflicts with game
bindings are real). Bindings are kept in QSettings, like the Advanced checkbox. In live mode the keys
are only registered while the game is the foreground window.
"""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QKeySequenceEdit, QLabel,
                               QMessageBox, QVBoxLayout)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_KEYS = {getattr(Qt.Key, f"Key_{c}"): ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
_KEYS.update({getattr(Qt.Key, f"Key_F{n}"): 0x6F + n for n in range(1, 25)})
_KEYS.update({
    Qt.Key.Key_Space: 0x20, Qt.Key.Key_PageUp: 0x21, Qt.Key.Key_PageDown: 0x22, Qt.Key.Key_End: 0x23,
    Qt.Key.Key_Home: 0x24, Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26, Qt.Key.Key_Right: 0x27,
    Qt.Key.Key_Down: 0x28, Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Delete: 0x2E, Qt.Key.Key_Pause: 0x13,
    Qt.Key.Key_ScrollLock: 0x91, Qt.Key.Key_Semicolon: 0xBA, Qt.Key.Key_Equal: 0xBB,
    Qt.Key.Key_Comma: 0xBC, Qt.Key.Key_Minus: 0xBD, Qt.Key.Key_Period: 0xBE, Qt.Key.Key_Slash: 0xBF,
    Qt.Key.Key_QuoteLeft: 0xC0, Qt.Key.Key_BracketLeft: 0xDB, Qt.Key.Key_Backslash: 0xDC,
    Qt.Key.Key_BracketRight: 0xDD, Qt.Key.Key_Apostrophe: 0xDE,
})
_NUMPAD = {getattr(Qt.Key, f"Key_{n}"): 0x60 + n for n in range(10)}
_NUMPAD.update({Qt.Key.Key_Asterisk: 0x6A, Qt.Key.Key_Plus: 0x6B, Qt.Key.Key_Minus: 0x6D,
                Qt.Key.Key_Period: 0x6E, Qt.Key.Key_Slash: 0x6F})


def to_native(sequence):
    """(modifiers, virtual key) for a one-key QKeySequence; None if Windows can't register it."""
    if sequence.isEmpty():
        return None
    combo = sequence[0]
    qt_mods = combo.keyboardModifiers()
    keys = _NUMPAD if qt_mods & Qt.KeyboardModifier.KeypadModifier else _KEYS
    vk = keys.get(combo.key())
    if vk is None:
        return None
    mods = MOD_NOREPEAT
    if qt_mods & Qt.KeyboardModifier.ShiftModifier:
        mods |= MOD_SHIFT
    if qt_mods & Qt.KeyboardModifier.ControlModifier:
        mods |= MOD_CONTROL
    if qt_mods & Qt.KeyboardModifier.AltModifier:
        mods |= MOD_ALT
    if qt_mods & Qt.KeyboardModifier.MetaModifier:
        mods |= MOD_WIN
    return mods, vk


class HotkeyManager(QAbstractNativeEventFilter):
    """actions: list of (name, label, callback). Bindings live in settings under hotkeys/<name>.

    in_front: a function that says whether the game is the foreground window. When given, the keys are
    registered only while it is, so they're free for typing in other programs (Armchair, 2026-11-08).
    None (replay): always registered. on_problem: called with each new problem found when re-registering.
    """

    CHECK_MS = 200

    def __init__(self, actions, settings, in_front=None, on_problem=None):
        super().__init__()
        self.actions = actions
        self.settings = settings
        self.in_front = in_front
        self.on_problem = on_problem
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self._user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self._registered = []
        self._active = False    # keys registered now
        self._wanted = False    # False until apply(), and while the rebinding dialog is open
        self._reported = []
        QCoreApplication.instance().installNativeEventFilter(self)
        self._timer = QTimer()
        self._timer.timeout.connect(self._sync)
        if in_front is not None:
            self._timer.start(self.CHECK_MS)

    def binding(self, name):
        text = self.settings.value(f"hotkeys/{name}", "", type=str)
        return QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)

    def set_binding(self, name, sequence):
        self.settings.setValue(f"hotkeys/{name}", sequence.toString(QKeySequence.SequenceFormat.PortableText))

    def release(self):
        """Give all keys back until the next apply() (while the rebinding dialog is capturing keys)."""
        self._wanted = False
        self._unregister()

    def apply(self):
        """Check and register all bound hotkeys. Returns a list of problems, for showing to the player.

        The keys are registered here even with the game in the background, so a key that another
        program holds is reported now. Then they're released until the game comes to the front.
        """
        self._unregister()
        problems = self._register()
        self._reported = problems
        self._wanted = True
        self._sync()
        return problems

    def _should_be_active(self):
        return self._wanted and (self.in_front is None or self.in_front())

    def _sync(self):
        should = self._should_be_active()
        if should and not self._active:
            problems = self._register()
            new = [p for p in problems if p not in self._reported]
            self._reported = problems
            if new and self.on_problem:
                for problem in new:
                    self.on_problem(problem)
        elif not should and self._active:
            self._unregister()

    def _unregister(self):
        for hotkey_id in self._registered:
            self._user32.UnregisterHotKey(None, hotkey_id)
        self._registered = []
        self._active = False

    def _register(self):
        problems = []
        seen = {}
        for hotkey_id, (name, label, _) in enumerate(self.actions, start=1):
            sequence = self.binding(name)
            if sequence.isEmpty():
                continue
            text = sequence.toString(QKeySequence.SequenceFormat.NativeText)
            native = to_native(sequence)
            if native is None:
                problems.append(f"{label}: {text} can't be used as a global hotkey")
            elif native in seen:
                problems.append(f"{label}: {text} is already used for {seen[native]}")
            elif not self._user32.RegisterHotKey(None, hotkey_id, *native):
                problems.append(f"{label}: {text} is already taken by another program")
            else:
                seen[native] = label
                self._registered.append(hotkey_id)
        self._active = True
        return problems

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and 1 <= msg.wParam <= len(self.actions):
                # Run it from the event loop, not from inside the native filter.
                QTimer.singleShot(0, self.actions[msg.wParam - 1][2])
                return True, 0
        return False, 0


SCAN_DETECTION_TEXT = "Detect the first scan of each SRV deployment automatically"
NO_MANUAL_PING_KEY = "Manual ping has no hotkey; set one below."


class HotkeyDialog(QDialog):
    """scan_detection: whether automatic scan detection is on, for the checkbox at the top (Milestone 9); None for none.
    After OK, self.scan_detection holds the checkbox's state."""

    def __init__(self, manager, parent=None, scan_detection=None):
        super().__init__(parent)
        self.setWindowTitle("Hotkeys")
        self.manager = manager
        self.edits = {}
        self.scan_detection = scan_detection
        self.detection_box = self.no_key_note = None
        layout = QVBoxLayout(self)
        if scan_detection is not None:
            self.detection_box = QCheckBox(SCAN_DETECTION_TEXT)
            self.detection_box.setChecked(scan_detection)
            self.detection_box.setToolTip("Off: scans are marked only with Manual ping (the hotkey or the button)")
            self.no_key_note = QLabel(NO_MANUAL_PING_KEY)
            layout.addWidget(self.detection_box)
            layout.addWidget(self.no_key_note)
        form = QFormLayout()
        for name, label, _ in manager.actions:
            edit = QKeySequenceEdit(manager.binding(name))
            edit.setClearButtonEnabled(True)
            edit.setMaximumSequenceLength(1)
            self.edits[name] = edit
            form.addRow(label + ":", edit)
        note = QLabel("Click a box and press a key to bind it; the x clears it.\n"
                      "A bound key stops reaching the game, so pick keys the game doesn't use.\n"
                      "The keys only work while the game is the window in front.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)
        if self.detection_box:
            self.detection_box.toggled.connect(self._update_note)
            if "manual_ping" in self.edits:
                self.edits["manual_ping"].keySequenceChanged.connect(self._update_note)
            self._update_note()
        manager.release()  # so the boxes can capture keys that are currently bound

    def _update_note(self, *_):
        edit = self.edits.get("manual_ping")
        unbound = edit is not None and edit.keySequence().isEmpty()
        self.no_key_note.setVisible(not self.detection_box.isChecked() and unbound)

    def accept(self):
        for name, edit in self.edits.items():
            self.manager.set_binding(name, edit.keySequence())
        problems = self.manager.apply()
        if problems:
            QMessageBox.warning(self, "Hotkeys", "\n".join(problems))
            self.manager.release()
            return  # stay open so the player can fix them
        if self.detection_box:
            self.scan_detection = self.detection_box.isChecked()
        super().accept()

    def reject(self):
        self.manager.apply()
        super().reject()
