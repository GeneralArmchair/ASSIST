"""Preferences... (Milestone 15): ASSIST's settings sorted into categories, a list on the left and one page per category.

Each category is a page class with a title and a values() method. Later milestones add pages (Map Overlay, Map) to
PreferencesDialog.pages and settings to the existing pages, with their defaults in DEFAULTS. Values go in and come out as a plain dict keyed by their QSettings key, so
the dialog never touches QSettings itself: the main window reads, stores and applies them.
"""

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
                               QStackedWidget, QVBoxLayout, QWidget)

# The defaults, one per QSettings key. The main window reads each setting with its default from here.
DEFAULTS = {
    "auto_scan_detection": True,
}

SCAN_DETECTION_TEXT = "Detect the first scan of each SRV deployment automatically"
NO_MANUAL_PING_KEY = "Manual ping has no hotkey; set one in Hotkeys..."


class SurveySettingsPage(QWidget):
    """How a survey is recorded."""

    title = "Survey Settings"

    def __init__(self, values, manual_ping_bound=True):
        super().__init__()
        self.manual_ping_bound = manual_ping_bound
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.detection_box = QCheckBox(SCAN_DETECTION_TEXT)
        self.detection_box.setChecked(values["auto_scan_detection"])
        self.detection_box.setToolTip("Off: scans are marked only with Manual ping (the hotkey or the button)")
        self.no_key_note = QLabel(NO_MANUAL_PING_KEY)
        self.no_key_note.setStyleSheet("color: #b9770e;")
        layout.addWidget(self.detection_box)
        layout.addWidget(self.no_key_note)
        layout.addStretch()
        self.detection_box.toggled.connect(self._update_note)
        self._update_note()

    def _update_note(self, *_):
        self.no_key_note.setVisible(not self.detection_box.isChecked() and not self.manual_ping_bound)

    def values(self):
        return {"auto_scan_detection": self.detection_box.isChecked()}


class PreferencesDialog(QDialog):
    """values: the current settings, keyed like DEFAULTS (missing keys get the default). manual_ping_bound: whether
    the Manual ping hotkey has a key, for the note under the scan detection checkbox.
    After OK, self.values holds every setting as chosen; after Cancel it's unchanged."""

    def __init__(self, values, parent=None, manual_ping_bound=True):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.values = {**DEFAULTS, **values}
        self.pages = [SurveySettingsPage(self.values, manual_ping_bound)]
        self.categories = QListWidget()
        self.stack = QStackedWidget()
        for page in self.pages:
            self.categories.addItem(page.title)
            self.stack.addWidget(page)
        self.categories.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.categories.setCurrentRow(0)
        self.categories.setFixedWidth(max(160, self.categories.sizeHintForColumn(0) + 24))
        body = QHBoxLayout()
        body.addWidget(self.categories)
        body.addWidget(self.stack, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(buttons)
        self.resize(640, 320)

    def page(self, title):
        """The page for a category, by its title."""
        return next(page for page in self.pages if page.title == title)

    def accept(self):
        values = dict(self.values)
        for page in self.pages:
            values.update(page.values())
        self.values = values
        super().accept()
