"""The overlay: guidance in a transparent, click-through, always-on-top window on the game's monitor.

It never takes focus or input. Everything about it is set from the "Overlay..." dialog in the main window.
The rows differ from the main window's on purpose (Milestone 4).
"""

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QSlider,
                               QVBoxLayout, QWidget)

from .guidance import OFF_TARGET, format_distance, signed_pitch_text
from .ores import ORES, overlay_label
from .status import IN_SRV, SUPERCRUISE

TURN_THRESHOLD = 5           # degrees: a turn arrow lights above this
CROSS_CENTER = 100           # m: the fine-tune cross's center lights within this of the point
CROSS_DEAD_ZONE = 50         # m: a fine-tune arrow lights beyond this on its axis
CROSS_MAX_HEIGHT = 150       # m above the terrain: the fine-tune cross doesn't show above this
FULL_SCREEN_VIEWS = range(5, 12)  # GuiFocus: station services, galaxy and system maps, orrery, FSS, SAA, codex
GAME_WINDOW_CLASS = "FrontierDevelopmentsAppWinClass"
REFRESH_MS = 500             # how often the game window's state is checked

DEFAULT_X, DEFAULT_Y, DEFAULT_SIZE = 50, 18, 26   # % across, % down, text height in pixels
MIN_SIZE, MAX_SIZE = 14, 60

WHITE = QColor("#ffffff")
GREEN = QColor("#4cff7a")
GREY = QColor("#9a9a9a")
LAVENDER = QColor("#cdb8ff")
OUTLINE = QColor("#000000")

# Row 4.
DEPLOY_READY = "DEPLOY READY"
MISS = "MISS"
PLATES = {DEPLOY_READY: (QColor("#1fa64a"), WHITE), MISS: (QColor("#f0a020"), QColor("#2b1a00"))}
NOTICE_PLATE = (QColor("#1f6fd1"), WHITE)  # any other text in row 4
WIDEST_PLATE = "TIER 1 COMPLETE (13 skipped)"
# Row 1 when flying to a deposit, with the overlay's short ore names.
WIDEST_TITLE = max((overlay_label(f"D99 {name}") for name in ORES), key=len) + "  99.99 km"
TIER_NOTICE_SECONDS = 15     # how long the tier message stays once in the ship

# Game window states.
GAME_OK = "ok"
GAME_ABSENT = "absent"    # not running, or minimized
GAME_COVERED = "covered"  # another window has focus on the game's monitor


@dataclass(frozen=True)
class FineTuneCross:
    """Which parts of the cross are lit. The arrows say which way to move, assuming a level hover."""

    center: bool = False
    forward: bool = False
    back: bool = False
    left: bool = False
    right: bool = False


@dataclass(frozen=True)
class OverlayView:
    """What the overlay shows. None in a field leaves that row blank."""

    title: str
    bearing: str | None = None
    turn_left: bool = False
    turn_right: bool = False
    pitch: str | None = None
    pitch_stale: bool = False
    cue: str | None = None
    cross: FineTuneCross | None = None


SAMPLE = OverlayView("I2  3.41 km", "045°", turn_left=True, pitch="PITCH -38°", cue=DEPLOY_READY)
SAMPLE_WITH_CROSS = replace(SAMPLE, cross=FineTuneCross(forward=True, left=True))


def cross_from(guidance):
    """The fine-tune cross for a Guidance, or None if the ship is too high, or its heading or height isn't known."""
    if guidance.forward is None or guidance.height is None or guidance.height > CROSS_MAX_HEIGHT:
        return None
    return FineTuneCross(center=guidance.distance <= CROSS_CENTER,
                         forward=guidance.forward > CROSS_DEAD_ZONE, back=guidance.forward < -CROSS_DEAD_ZONE,
                         right=guidance.right > CROSS_DEAD_ZONE, left=guidance.right < -CROSS_DEAD_ZONE)


def view_from(guidance, fine_tune=False):
    """The rows for a Guidance. fine_tune: add the cross while DEPLOY READY is showing."""
    error = guidance.heading_error
    lit = error is not None and not guidance.inside_circle
    in_srv = guidance.cue is None
    cue = None
    if not in_srv:
        if guidance.inside_circle:
            cue = DEPLOY_READY
        elif guidance.cue == OFF_TARGET:
            cue = MISS
    return OverlayView(
        title=f"{overlay_label(guidance.label)}  {format_distance(guidance.distance)}",
        bearing="—" if error is None else f"{round(guidance.bearing) % 360:03d}°",  # None: over the point
        turn_left=lit and error < -TURN_THRESHOLD,
        turn_right=lit and error > TURN_THRESHOLD,
        pitch=None if in_srv else signed_pitch_text(guidance.pitch, guidance.pitch_stale),
        pitch_stale=guidance.pitch_stale,
        cue=cue,
        cross=cross_from(guidance) if fine_tune and cue == DEPLOY_READY else None,
    )


def build_view(tracker, fine_tune=False):
    """The view for the tracker's current state, or None if there's nothing to show."""
    survey = tracker.survey
    if survey is None:
        return None
    guidance = tracker.guidance()
    if guidance is not None:
        return view_from(guidance, fine_tune)
    return None if survey.target else OverlayView("All points done or skipped")


class TierNotice:
    """The "TIER 1 COMPLETE" message: from the moment the tier is finished until a while after boarding.

    Only finishing the tier raises it, so a survey that is resumed or loaded already complete doesn't.
    Reopening the tier (undo, unskip) clears it, and finishing the tier again raises it again.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._survey_id = None
        self._was_complete = False
        self._showing = False
        self._deadline = None

    def text(self, survey, in_srv):
        """The message to show now, or None. Call it on every refresh, so that no change is missed."""
        survey_id = survey.id if survey else None
        complete, skipped = survey.tier_progress(1) if survey else (False, 0)
        if survey_id != self._survey_id:
            self._survey_id, self._was_complete, self._showing = survey_id, complete, False
        elif complete and not self._was_complete:
            self._was_complete, self._showing, self._deadline = True, True, None
        elif not complete:
            self._was_complete = self._showing = False
        if not self._showing:
            return None
        if not in_srv:
            if self._deadline is None:
                self._deadline = self._clock() + TIER_NOTICE_SECONDS
            elif self._clock() >= self._deadline:
                self._showing = False
                return None
        return f"TIER 1 COMPLETE ({skipped} skipped)" if skipped else "TIER 1 COMPLETE"


def with_notice(view, notice):
    """Row 4 shows the notice unless it already has a cue: DEPLOY READY and MISS come first."""
    return replace(view, cue=notice) if view and notice and not view.cue else view


def hidden_reason(enabled, status, survey, game_state):
    """Why the overlay should be hidden, or None. game_state None: don't apply the game window rules (replay)."""
    if not enabled:
        return "turned off"
    if survey is None:
        return "no active survey"
    if status is None or not status.has_position:
        return "no position"
    if status.has(SUPERCRUISE):
        return "supercruise"
    if status.gui_focus in FULL_SCREEN_VIEWS:
        return "full-screen game view"
    if game_state == GAME_ABSENT:
        return "game not running"
    if game_state == GAME_COVERED:
        return "another window covers the game"
    return None


class GameWindow:
    """Looks at the game's window. Read-only: nothing is ever sent to it."""

    MONITOR_DEFAULTTONEAREST = 2

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    def __init__(self):
        user32 = ctypes.WinDLL("user32")
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.IsIconic.argtypes = (wintypes.HWND,)
        user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(self._MonitorInfo))
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        self._user32 = user32

    def _process(self, hwnd):
        """The id of the process that owns a window. Only compared with the game's, to tell whether the game is in
        front: no process is opened, read or inspected."""
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def state(self):
        user32 = self._user32
        game = user32.FindWindowW(GAME_WINDOW_CLASS, None)
        if not game or user32.IsIconic(game):
            return GAME_ABSENT
        foreground = user32.GetForegroundWindow()
        if not foreground or self._process(foreground) == self._process(game):
            return GAME_OK
        same_monitor = (user32.MonitorFromWindow(foreground, self.MONITOR_DEFAULTTONEAREST)
                        == user32.MonitorFromWindow(game, self.MONITOR_DEFAULTTONEAREST))
        return GAME_COVERED if same_monitor else GAME_OK

    def in_front(self):
        """True if the game's window is the foreground window (for the hotkeys)."""
        user32 = self._user32
        game = user32.FindWindowW(GAME_WINDOW_CLASS, None)
        foreground = user32.GetForegroundWindow()
        return bool(game and foreground) and self._process(foreground) == self._process(game)

    def monitor_origin(self):
        """Top-left of the game's monitor as (x, y), or None if the game isn't there."""
        game = self._user32.FindWindowW(GAME_WINDOW_CLASS, None)
        if not game:
            return None
        info = self._MonitorInfo()
        info.cbSize = ctypes.sizeof(self._MonitorInfo)
        monitor = self._user32.MonitorFromWindow(game, self.MONITOR_DEFAULTTONEAREST)
        if not self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return info.rcMonitor.left, info.rcMonitor.top


# --- drawing --------------------------------------------------------------------------------

def overlay_font(size, italic=False):
    font = QFont()
    font.setFamilies(["Bahnschrift", "Segoe UI"])
    font.setPixelSize(size)
    font.setBold(True)
    font.setItalic(italic)
    return font


def box_size(size, fine_tune=False):
    """(width, height) of the overlay: fixed, so that nothing moves as the values change."""
    metrics = QFontMetricsF(overlay_font(size))
    width = max(metrics.horizontalAdvance(text) for text in ("All points done or skipped", WIDEST_PLATE, WIDEST_TITLE)) + size * 2
    height = _row_height(size) * 4 + size * 0.5
    return round(width), round(height + _cross_reach(size) * 2 if fine_tune else height)


def _row_height(size):
    return size * 1.45


def _cross_reach(size):
    """From the center of the cross to the tip of an arrow, plus room for the outline."""
    return size * 1.3


def _outlined(painter, path, fill, size):
    painter.strokePath(path, QPen(OUTLINE, max(2.0, size / 8), Qt.PenStyle.SolidLine,
                                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.fillPath(path, fill)


def _text(painter, text, center_x, baseline, font, color, size):
    path = QPainterPath()
    path.addText(center_x - QFontMetricsF(font).horizontalAdvance(text) / 2, baseline, font, text)
    _outlined(painter, path, color, size)


def _arrow(painter, tip_x, tip_y, dx, dy, color, size):
    """A triangle with its tip at (tip_x, tip_y), pointing along the unit direction (dx, dy)."""
    half = size * 0.38
    back_x, back_y = tip_x - dx * size * 0.62, tip_y - dy * size * 0.62
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(tip_x, tip_y), QPointF(back_x + dy * half, back_y - dx * half),
                               QPointF(back_x - dy * half, back_y + dx * half)]))
    path.closeSubpath()
    _outlined(painter, path, color, size)


def _lit(on):
    return GREEN if on else GREY


def _paint_cross(painter, center_x, center_y, cross, size):
    """A circle with an arrow on each side. Up means forward, whichever way the ship faces."""
    radius = size * 0.38
    tip = size * 1.2
    path = QPainterPath()
    path.addEllipse(QPointF(center_x, center_y), radius, radius)
    _outlined(painter, path, _lit(cross.center), size)
    _arrow(painter, center_x, center_y - tip, 0, -1, _lit(cross.forward), size)
    _arrow(painter, center_x, center_y + tip, 0, 1, _lit(cross.back), size)
    _arrow(painter, center_x - tip, center_y, -1, 0, _lit(cross.left), size)
    _arrow(painter, center_x + tip, center_y, 1, 0, _lit(cross.right), size)


def paint_view(painter, width, view, size):
    """Draw a view in a box of the given width, starting at y = 0."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = overlay_font(size)
    metrics = QFontMetricsF(font)
    row = _row_height(size)
    center_x = width / 2
    baseline = size * 0.25 + (row + metrics.capHeight()) / 2

    _text(painter, view.title, center_x, baseline, font, WHITE, size)

    if view.bearing is not None:
        y = baseline + row
        _text(painter, view.bearing, center_x, y, font, WHITE, size)
        gap = metrics.horizontalAdvance("000°") / 2 + size * 0.45  # fixed: the arrows don't move with the digits
        middle = y - metrics.capHeight() / 2
        _arrow(painter, center_x - gap - size * 0.62, middle, -1, 0, _lit(view.turn_left), size)
        _arrow(painter, center_x + gap + size * 0.62, middle, 1, 0, _lit(view.turn_right), size)

    if view.pitch is not None:
        _text(painter, view.pitch, center_x, baseline + row * 2,
              overlay_font(size, italic=view.pitch_stale), LAVENDER if view.pitch_stale else WHITE, size)

    if view.cue:
        background, foreground = PLATES.get(view.cue, NOTICE_PLATE)
        y = baseline + row * 3
        plate_width = metrics.horizontalAdvance(view.cue) + size
        plate = QRectF(center_x - plate_width / 2, y - metrics.capHeight() - size * 0.3,
                       plate_width, metrics.capHeight() + size * 0.6)
        painter.setPen(QPen(OUTLINE, max(1.0, size / 16)))
        painter.setBrush(background)
        painter.drawRoundedRect(plate, size * 0.15, size * 0.15)
        path = QPainterPath()
        path.addText(center_x - metrics.horizontalAdvance(view.cue) / 2, y, font, view.cue)
        painter.fillPath(path, foreground)

    if view.cross:
        _paint_cross(painter, center_x, row * 4 + size * 0.5 + _cross_reach(size), view.cross, size)


# --- the window -----------------------------------------------------------------------------

class OverlayWindow(QWidget):
    def __init__(self, tracker, settings, game_window=None, clock=time.monotonic):
        """game_window None: no game window rules, and "follow the game" means the primary monitor (replay)."""
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("ASSIST overlay")
        self.tracker = tracker
        self.settings = settings
        self.game_window = game_window
        self.sample = False       # the dialog is open: show the sample, whatever the rules say
        self.tier_notice = TierNotice(clock)
        self._view = None
        self.enabled = settings.value("overlay/enabled", True, type=bool)
        self.load_placement()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        tracker.add_listener(lambda message: self.refresh())

    # --- settings ----------------------------------------------------------------------------

    def load_placement(self):
        self.monitor = self.settings.value("overlay/monitor", "", type=str)  # "": follow the game
        self.x_percent = self.settings.value("overlay/x", DEFAULT_X, type=int)
        self.y_percent = self.settings.value("overlay/y", DEFAULT_Y, type=int)
        self.text_size = self.settings.value("overlay/size", DEFAULT_SIZE, type=int)
        self.fine_tune = self.settings.value("overlay/fine_tune", False, type=bool)

    def save_placement(self):
        self.settings.setValue("overlay/monitor", self.monitor)
        self.settings.setValue("overlay/x", self.x_percent)
        self.settings.setValue("overlay/y", self.y_percent)
        self.settings.setValue("overlay/size", self.text_size)
        self.settings.setValue("overlay/fine_tune", self.fine_tune)

    def set_enabled(self, on):
        self.enabled = on
        self.settings.setValue("overlay/enabled", on)
        self.refresh()

    # --- showing -----------------------------------------------------------------------------

    def _screen(self):
        screens = QGuiApplication.screens()
        if self.monitor:
            for screen in screens:
                if screen.name() == self.monitor:
                    return screen
        elif self.game_window:
            origin = self.game_window.monitor_origin()
            for screen in screens:
                if origin == (screen.geometry().x(), screen.geometry().y()):
                    return screen
        return QGuiApplication.primaryScreen()

    def _place(self):
        area = self._screen().geometry()
        width, height = box_size(self.text_size, self.fine_tune)
        # The percentages place the center of the box horizontally and its top vertically.
        x = area.x() + round(area.width() * self.x_percent / 100 - width / 2)
        y = area.y() + round(area.height() * self.y_percent / 100)
        x = min(max(x, area.x()), area.x() + area.width() - width)
        y = min(max(y, area.y()), area.y() + area.height() - height)
        if self.geometry().getRect() != (x, y, width, height):
            self.setGeometry(x, y, width, height)

    def refresh(self):
        status = self.tracker.status
        # Before anything can hide the overlay: finishing the tier mustn't be missed while it's hidden.
        notice = self.tier_notice.text(self.tracker.survey, bool(status and status.has(IN_SRV)))
        if self.sample:
            view = SAMPLE_WITH_CROSS if self.fine_tune else SAMPLE
        else:
            game_state = self.game_window.state() if self.game_window else None
            reason = hidden_reason(self.enabled, status, self.tracker.survey, game_state)
            view = None if reason else with_notice(build_view(self.tracker, self.fine_tune), notice)
        if view is None:
            self._view = None
            self.hide()
            return
        self._place()
        if view != self._view:
            self._view = view
            self.update()
        self.show()

    def paintEvent(self, event):
        if self._view:
            painter = QPainter(self)
            paint_view(painter, self.width(), self._view, self.text_size)


class OverlayDialog(QDialog):
    """Monitor, position, text size and the fine-tune cross, with a live sample on the overlay. Cancel puts everything back."""

    def __init__(self, overlay, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Overlay")
        self.overlay = overlay

        self.monitor = QComboBox()
        self.monitor.addItem("Follow the game's monitor", "")
        for screen in QGuiApplication.screens():
            geometry = screen.geometry()
            self.monitor.addItem(f"{screen.name()}  ({geometry.width()}x{geometry.height()} at {geometry.x()},{geometry.y()})",
                                 screen.name())
        index = self.monitor.findData(overlay.monitor)
        self.monitor.setCurrentIndex(max(index, 0))
        self.x_slider = self._slider(0, 100, overlay.x_percent)
        self.y_slider = self._slider(0, 100, overlay.y_percent)
        self.size_slider = self._slider(MIN_SIZE, MAX_SIZE, overlay.text_size)
        self.monitor.currentIndexChanged.connect(self._changed)
        self.fine_tune = QCheckBox(f"Show the fine-tune cross inside the circle, below {CROSS_MAX_HEIGHT} m")
        self.fine_tune.setChecked(overlay.fine_tune)
        self.fine_tune.toggled.connect(self._changed)

        form = QFormLayout()
        form.addRow("Monitor:", self.monitor)
        form.addRow("Across:", self.x_slider)
        form.addRow("Down:", self.y_slider)
        form.addRow("Text size:", self.size_slider)
        form.addRow("", self.fine_tune)
        note = QLabel("The sample on the overlay moves as you change these.\n"
                      "The cross assumes a level hover and can lag by several seconds.\n"
                      "The overlay itself never takes clicks or keys.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

        overlay.sample = True
        overlay.refresh()

    def _slider(self, low, high, value):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(value)
        slider.setMinimumWidth(320)
        slider.valueChanged.connect(self._changed)
        return slider

    def _changed(self):
        overlay = self.overlay
        overlay.monitor = self.monitor.currentData()
        overlay.x_percent = self.x_slider.value()
        overlay.y_percent = self.y_slider.value()
        overlay.text_size = self.size_slider.value()
        overlay.fine_tune = self.fine_tune.isChecked()
        overlay.update()
        overlay.refresh()

    def done(self, result):
        """Runs for OK, Cancel and closing the dialog."""
        if result == QDialog.DialogCode.Accepted:
            self.overlay.save_placement()
        else:
            self.overlay.load_placement()
        self.overlay.sample = False
        self.overlay.refresh()
        super().done(result)
