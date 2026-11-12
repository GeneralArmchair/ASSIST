"""The main window: the survey on the right, logs and deposits on the left.

"Advanced" (a checkbox in the status bar, remembered between runs) also shows the decoded
Status.json and a log of every flag change; without it only the survey log is shown.
"""

from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QLabel, QMainWindow, QPlainTextEdit,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from .hotkeys import HotkeyDialog, HotkeyManager
from .overlay import OverlayDialog, OverlayWindow
from .status import FLAG_NAMES, FLAGS2_NAMES, set_bits
from .survey_panel import SurveyPanel

NO_VALUE = "—"


class StatusWindow(QMainWindow):
    def __init__(self, source, tracker, title, game_window=None):
        super().__init__()
        self.setWindowTitle(title)
        self._previous = None
        self.tracker = tracker

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.values = {}
        form = QFormLayout()
        for key in ("System", "Body", "Nav target", "Mode", "Latitude", "Longitude", "Heading",
                    "Altitude", "Game time", "Flags", "Flags2", "Progress"):
            label = QLabel(NO_VALUE)
            label.setFont(mono)
            label.setWordWrap(True)
            self.values[key] = label
            form.addRow(key + ":", label)
        status_box = QGroupBox("Status")
        status_box.setLayout(form)

        self.log = self._log_view(mono)
        log_box = QGroupBox("Changes (every flag change)")
        QVBoxLayout(log_box).addWidget(self.log)
        self.survey_log = self._log_view(mono)
        survey_log_box = QGroupBox("Survey log")
        QVBoxLayout(survey_log_box).addWidget(self.survey_log)
        self.advanced_widgets = [status_box, log_box]

        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(status_box)
        layout.addWidget(log_box, stretch=2)
        layout.addWidget(survey_log_box, stretch=1)
        self.settings = QSettings("ASSIST", "ASSIST")
        self.survey_panel = SurveyPanel(tracker, self.settings)
        layout.addWidget(self.survey_panel.deposits_box, stretch=2)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.survey_panel)
        splitter.setSizes([670, 620])
        # A red strip above everything, hidden unless something is wrong with how ASSIST was started (Milestone 11).
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background: #c0392b; color: white; font-weight: bold; padding: 8px;")
        self.banner.hide()
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self.banner)
        central_layout.addWidget(splitter)
        self.setCentralWidget(central)
        self.resize(1290, 800)

        self.advanced = QCheckBox("Advanced")
        self.advanced.setToolTip("Show the decoded Status.json and every flag change")
        self.advanced.toggled.connect(self._set_advanced)
        self.statusBar().addPermanentWidget(self.advanced)
        self.advanced.setChecked(self.settings.value("advanced", False, type=bool))
        self._set_advanced(self.advanced.isChecked())

        self.overlay = OverlayWindow(tracker, self.settings, game_window)
        self.overlay_box = QCheckBox("Overlay")
        self.overlay_box.setToolTip("Show the guidance over the game")
        self.overlay_box.setChecked(self.overlay.enabled)
        self.overlay_box.toggled.connect(self.overlay.set_enabled)
        self.statusBar().addPermanentWidget(self.overlay_box)
        overlay_button = QPushButton("Overlay...")
        overlay_button.clicked.connect(lambda: OverlayDialog(self.overlay, self).exec())
        self.statusBar().addPermanentWidget(overlay_button)

        self.hotkeys = HotkeyManager([
            ("next_point", "Next point", lambda: tracker.step_target(1)),
            ("previous_point", "Previous point", lambda: tracker.step_target(-1)),
            ("start_survey", "Start survey", tracker.start_survey_hotkey),
            ("manual_ping", "Manual ping", tracker.manual_ping),
            ("toggle_overlay", "Show / hide overlay", self.overlay_box.toggle),
            ("mark_deposit", "Remote Mark Deposit", self.survey_panel.mark_deposit),
            ("set_radius", "Set POI radius", self.survey_panel.set_radius),
        ], self.settings, in_front=game_window.in_front if game_window else None,
            on_problem=lambda problem: self.add_log(f"Hotkey problem: {problem}"))
        hotkeys_button = QPushButton("Hotkeys...")
        hotkeys_button.clicked.connect(self._edit_hotkeys)
        self.statusBar().addPermanentWidget(hotkeys_button)

        source.status_changed.connect(self.show_status)
        source.system_changed.connect(self.show_system)
        source.system_changed.connect(tracker.set_system)
        tracker.add_listener(self._survey_message)
        source.message.connect(self.add_log)
        source.detail.connect(lambda text: self.add_log(text, survey_log=False))
        if hasattr(source, "progress"):
            source.progress.connect(lambda done, total: self.values["Progress"].setText(f"{done} / {total}"))
        else:
            self.values["Progress"].setText("live")
        for problem in self.hotkeys.apply():
            self.add_log(f"Hotkey problem: {problem}")
        tracker.detect_scans = self.settings.value("auto_scan_detection", True, type=bool)
        self.add_log(self._detection_text())

    def _detection_text(self):
        if self.tracker.detect_scans:
            return "Automatic scan detection is on"
        return "Automatic scan detection is off: only Manual ping marks a scan"

    def _edit_hotkeys(self):
        """The Hotkeys dialog, which also holds the automatic scan detection checkbox (Milestone 9)."""
        dialog = HotkeyDialog(self.hotkeys, self, scan_detection=self.tracker.detect_scans)
        if dialog.exec() and dialog.scan_detection != self.tracker.detect_scans:
            self.tracker.detect_scans = dialog.scan_detection
            self.settings.setValue("auto_scan_detection", dialog.scan_detection)
            self.add_log(self._detection_text())

    def closeEvent(self, event):
        self.overlay.close()  # so that it can't keep the program alive
        super().closeEvent(event)

    @staticmethod
    def _log_view(font):
        view = QPlainTextEdit(readOnly=True)
        view.setFont(font)
        view.setMaximumBlockCount(2000)
        return view

    def _set_advanced(self, on):
        for widget in self.advanced_widgets:
            widget.setVisible(on)
        self.settings.setValue("advanced", on)

    def show_warning(self, text, title_marker):
        """Show text in the red banner for the whole session, mark the window title, and log it."""
        self.banner.setText(text)
        self.banner.show()
        self.setWindowTitle(f"{self.windowTitle()} ({title_marker})")
        self.add_log(text)

    def add_log(self, text, when=None, survey_log=True):
        """Add a line to the Changes log and, unless survey_log is False, to the survey log."""
        prefix = f"{when:%H:%M:%S}  " if when else " " * 10
        self.log.appendPlainText(prefix + text)
        if survey_log:
            self.survey_log.appendPlainText(prefix + text)

    def _survey_message(self, message):
        if message:
            status = self.tracker.status
            when = status.local_time if status else None
            prefix = f"{when:%H:%M:%S}  " if when else " " * 10
            self.log.appendPlainText(prefix + "Survey: " + message)
            self.survey_log.appendPlainText(prefix + message)

    def show_system(self, name):
        self.values["System"].setText(name)
        self.add_log(f"System: {name}")

    def show_status(self, status):
        v = self.values
        if status.body_name:
            radius = f"  (radius {status.planet_radius / 1000:,.1f} km)" if status.planet_radius else ""
            v["Body"].setText(status.body_name + radius)
        else:
            v["Body"].setText(NO_VALUE)
        v["Nav target"].setText(self._describe_target(status))
        v["Mode"].setText(status.mode)
        if status.has_position:
            v["Latitude"].setText(f"{status.latitude:.6f}")
            v["Longitude"].setText(f"{status.longitude:.6f}")
        else:
            v["Latitude"].setText(NO_VALUE)
            v["Longitude"].setText(NO_VALUE)
        v["Heading"].setText(NO_VALUE if status.heading is None else f"{status.heading}°")
        v["Altitude"].setText(NO_VALUE if status.altitude is None
                              else f"{status.altitude:,.0f} m above {status.altitude_reference}")
        when = status.local_time
        v["Game time"].setText(f"{when:%Y-%m-%d %H:%M:%S}" if when else NO_VALUE)
        v["Flags"].setText(self._describe_flags(status.flags, FLAG_NAMES))
        v["Flags2"].setText(self._describe_flags(status.flags2, FLAGS2_NAMES))
        self._log_changes(status, when)
        self._previous = status
        # After the status is shown and logged, so survey messages appear below the change that caused them.
        self.tracker.update(status)
        self.survey_panel.refresh()
        self.survey_panel.status_changed()
        self.overlay.refresh()

    @staticmethod
    def _describe_target(status):
        if not status.destination_name:
            return NO_VALUE
        return status.destination_name + ("  (mining signal)" if status.destination_is_mining_signal else "")

    @staticmethod
    def _describe_flags(value, names):
        bits = ", ".join(f"{bit} {names.get(bit, 'unknown')}" for bit in set_bits(value))
        return f"{value}  [{bits}]" if bits else f"{value}"

    def _log_changes(self, status, when):
        old = self._previous
        if old is None:
            self.add_log(f"First status: {status.mode}, body {status.body_name or NO_VALUE}", when,
                         survey_log=False)
            return
        changes = []
        for value_old, value_new, names, label in ((old.flags, status.flags, FLAG_NAMES, ""),
                                                   (old.flags2, status.flags2, FLAGS2_NAMES, "F2 ")):
            for bit in range(max(value_old.bit_length(), value_new.bit_length())):
                was, now = value_old >> bit & 1, value_new >> bit & 1
                if was != now:
                    sign = "+" if now else "-"
                    changes.append(f"{sign}{label}{names.get(bit, f'bit {bit}')}")
        if status.body_name != old.body_name:
            changes.append(f"body {old.body_name or NO_VALUE} -> {status.body_name or NO_VALUE}")
        if status.destination_name != old.destination_name:
            changes.append(f"nav target {self._describe_target(status)}")
        if changes:
            where = ""
            if status.has_position:
                where = f"   @ {status.latitude:.5f}, {status.longitude:.5f}, alt {status.altitude:,.0f} m"
            self.add_log(", ".join(changes) + where, when, survey_log=False)
