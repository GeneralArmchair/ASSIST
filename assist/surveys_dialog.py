"""The Surveys list: every archived survey, re-opening and closing them, and editing their deposits from anywhere.

The active survey isn't listed: the main window handles it. Each change is written straight to the
survey's archive file (see SurveyTracker._change_archived).
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDialog, QDialogButtonBox, QGridLayout,
                               QGroupBox, QHeaderView, QLabel, QMenu, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QHBoxLayout, QVBoxLayout)

from .coverage import coverage
from .deposit_forms import EditDepositDialog, ManualEntryDialog, RadiusDialog, target_ores
from .map_view import MapWindow
from .ores import sorted_ores
from .survey import CLOSED, MAX_SIZE, TYPED, UNFINISHED

NO_VALUE = "—"
SURVEY_COLUMNS = ["Date", "System", "Body", "POI", "State", "Points", "Deposits", "Radius", "Coverage"]
ASSUMED_COLOR = QColor("#8a8a8a")  # a coverage worked out on the assumed radius
DEPOSIT_COLUMNS = ["Deposit", "Size", "Density", "Estimated lat, long", "Measured lat, long"]

# The Show filter.
ALL, THIS_SYSTEM, THIS_BODY = "all", "system", "body"


def has_deposit(survey, ore=None, min_size=None):
    """Whether the survey has a deposit of this ore (None: any) of at least this size (None: any, even unsized).

    With a minimum size, a deposit with no size doesn't match: its size is unknown.
    """
    return any((ore is None or d.ore == ore) and (min_size is None or (d.size is not None and d.size >= min_size))
               for d in survey.deposits)


def matching_systems(text, systems):
    """The systems the System field's text picks out of these.

    An exact name (ignoring case and surrounding spaces) picks only that system, so "...c1-1" doesn't also pick
    "...c1-17"; anything else picks every system whose name contains the text. Empty text picks them all.
    """
    systems = {s for s in systems if s}
    wanted = text.strip().casefold()
    if not wanted:
        return systems
    exact = {s for s in systems if s.casefold() == wanted}
    return exact or {s for s in systems if wanted in s.casefold()}


def local_date(timestamp):
    """A game UTC timestamp as a local 'YYYY-MM-DD HH:MM', which also sorts correctly as text."""
    try:
        return f"{datetime.fromisoformat(timestamp).astimezone():%Y-%m-%d %H:%M}"
    except (TypeError, ValueError):
        return timestamp or NO_VALUE


def deposits_summary(survey):
    """'5, 2 sized', or '0'."""
    count = len(survey.deposits)
    sized = sum(1 for d in survey.deposits if d.size is not None)
    return f"{count}, {sized} sized" if count else "0"


class SortItem(QTableWidgetItem):
    """A table cell that sorts by a key of its own rather than by its text."""

    def __init__(self, text, key):
        super().__init__(text)
        self.key = key

    def __lt__(self, other):
        if isinstance(other, SortItem):
            return self.key < other.key
        return super().__lt__(other)


class SurveysDialog(QDialog):
    def __init__(self, tracker, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Surveys")
        self.tracker = tracker
        tracker.rescan_archive()  # files copied into archive\ while ASSIST runs show up (Milestone 11)
        self.settings = settings
        self.surveys = {}  # id -> Survey, as last read from the archive
        self.maps = []  # map windows opened from here
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self.show_filter = QComboBox()
        for text, value in (("All surveys", ALL), ("This system", THIS_SYSTEM), ("This body", THIS_BODY)):
            self.show_filter.addItem(text, value)
        self.show_filter.setToolTip("Only surveys in the system or on the body you're at now")
        self.ore_filter = QComboBox()
        self.ore_filter.addItem("Any ore", None)
        for ore in sorted_ores():
            self.ore_filter.addItem(ore, ore)
        self.size_filter = QComboBox()
        self.size_filter.addItem("Any size", None)
        for size in range(1, MAX_SIZE + 1):
            self.size_filter.addItem(f"{size} or larger", size)
        self.size_filter.setToolTip("Deposits with no size recorded don't match a minimum size")
        self.system_filter = self._system_field()
        filters = QHBoxLayout()
        for label, combo in (("Show:", self.show_filter), ("Deposit:", self.ore_filter), ("Min size:", self.size_filter)):
            filters.addWidget(QLabel(label))
            filters.addWidget(combo)
            combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(QLabel("System:"))
        filters.addWidget(self.system_filter)
        filters.addStretch()
        self.table = self._table(SURVEY_COLUMNS, mono)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)  # newest first
        self.table.itemSelectionChanged.connect(self._fill_deposits)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        survey_buttons = QGridLayout()
        self._button(survey_buttons, 0, 0, "Re-open survey", self._reopen,
                     "Make a closed survey unfinished again:\nit resumes when you return to its POI.")
        self._button(survey_buttons, 0, 1, "Close survey", self._close,
                     "Close an unfinished survey without going back to it.\nClosed surveys aren't resumed.")
        self._button(survey_buttons, 0, 2, "Set POI radius", self._set_radius,
                     "Type the golden-ring radius noted earlier, or clear it")
        self._button(survey_buttons, 0, 3, "Map", self._show_map, "The selected survey's map, with scanner coverage")
        surveys_box = QGroupBox("Archived surveys (the active survey is in the main window)")
        surveys_layout = QVBoxLayout(surveys_box)
        surveys_layout.addLayout(filters)
        surveys_layout.addWidget(self.table)
        surveys_layout.addLayout(survey_buttons)

        self.deposits = self._table(DEPOSIT_COLUMNS, mono)
        self.deposits.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.deposits.customContextMenuRequested.connect(self._deposit_context_menu)
        deposit_buttons = QGridLayout()
        self._button(deposit_buttons, 0, 0, "Update size / location", self._edit_deposit,
                     "Fill in the size and the position read off the HUD")
        self._button(deposit_buttons, 0, 1, "Manual Mark Deposit", self._manual_entry,
                     "Add a deposit by typed latitude and longitude, with no duplicate check")
        self._button(deposit_buttons, 0, 2, "Delete selected", self._delete_deposit)
        self.deposits_box = QGroupBox("Deposits")
        deposits_layout = QVBoxLayout(self.deposits_box)
        deposits_layout.addWidget(self.deposits)
        deposits_layout.addLayout(deposit_buttons)

        self.message = QLabel()
        self.message.setWordWrap(True)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(surveys_box, stretch=3)
        layout.addWidget(self.deposits_box, stretch=2)
        layout.addWidget(self.message)
        layout.addWidget(close)
        self.resize(1060, 640)

        # Only changes come with a message, and every change to the archive does; plain status updates don't.
        self._listener = lambda message: message and self.refresh()
        tracker.add_listener(self._listener)
        self.finished.connect(lambda: tracker.remove_listener(self._listener))
        self.refresh()

    def _system_field(self):
        """An editable dropdown of the archive's systems; its text overrides Show."""
        field = QComboBox()
        field.setEditable(True)
        field.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)  # typing never adds an entry
        field.setMinimumContentsLength(30)  # sector names are long
        field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        field.lineEdit().setPlaceholderText("Any system")
        field.lineEdit().setClearButtonEnabled(True)
        completer = field.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)  # like the filter: anywhere in the name
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        field.setToolTip("Type or paste a system name, or part of one (\"tascheter\" finds the whole sector).\n"
                         "An exact name shows only that system. While this has text, Show is ignored.")
        field.editTextChanged.connect(self._system_changed)
        return field

    def _system_text(self):
        return self.system_filter.currentText().strip()

    def _system_changed(self):
        self.show_filter.setEnabled(not self._system_text())
        self.refresh()

    def _fill_systems(self, surveys):
        """Offer the archive's systems in the System field, keeping what's typed in it."""
        systems = sorted({s.poi.system for s in surveys if s.poi.system}, key=str.casefold)
        if systems == [self.system_filter.itemText(i) for i in range(self.system_filter.count())]:
            return
        text = self.system_filter.currentText()
        self.system_filter.blockSignals(True)
        self.system_filter.clear()
        self.system_filter.addItems(systems)
        self.system_filter.setCurrentIndex(-1)
        self.system_filter.setEditText(text)
        self.system_filter.blockSignals(False)

    def show_only_system(self, system):
        self.system_filter.setEditText(system)

    def row_menu(self, item):
        """The right-click menu for a row of the survey table, or None."""
        survey = self.surveys.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if survey is None or not survey.poi.system:
            return None
        menu = QMenu(self)
        system = survey.poi.system
        menu.addAction(f"Show only {system}", lambda: self.show_only_system(system))
        return menu

    def _context_menu(self, position):
        menu = self.row_menu(self.table.itemAt(position))
        if menu:
            menu.exec(self.table.viewport().mapToGlobal(position))

    @staticmethod
    def _table(headers, font):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setFont(font)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 6)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _button(grid, row, column, text, action, tooltip=None):
        button = QPushButton(text)
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(action)
        grid.addWidget(button, row, column)

    # --- display --------------------------------------------------------------------------

    def _current_body(self):
        status = self.tracker.status
        return status.body_name if status else None

    def _filtering(self):
        return (self.show_filter.currentData() != ALL or bool(self._system_text())
                or self.ore_filter.currentData() is not None or self.size_filter.currentData() is not None)

    def _filtered(self, surveys):
        show = self.show_filter.currentData()
        if self._system_text():  # overrides Show
            systems = matching_systems(self._system_text(), (s.poi.system for s in surveys))
            surveys = [s for s in surveys if s.poi.system in systems]
        elif show == THIS_SYSTEM:
            system = self.tracker.system
            surveys = [s for s in surveys if system and s.poi.system == system]
        elif show == THIS_BODY:
            body = self._current_body()
            surveys = [s for s in surveys if body and s.poi.body == body]
        ore, min_size = self.ore_filter.currentData(), self.size_filter.currentData()
        if ore is not None or min_size is not None:
            surveys = [s for s in surveys if has_deposit(s, ore, min_size)]
        return surveys

    def refresh(self):
        """Read the archive again and redraw, keeping the selection. Called after every survey change."""
        selected = self.selected_id()
        archived = self.tracker.archived_surveys()
        self._fill_systems(archived)
        surveys = self._filtered(archived)
        self.surveys = {s.id: s for s in surveys}

        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)  # so rows don't move while they're being filled
        self.table.clearSelection()
        self.table.setRowCount(len(surveys))
        for row, survey in enumerate(surveys):
            done, skipped, total = survey.progress()
            cells = [
                SortItem(local_date(survey.created), survey.created or ""),
                QTableWidgetItem(survey.poi.system or "?"),
                QTableWidgetItem(survey.poi.body),
                QTableWidgetItem(survey.poi.name or NO_VALUE),
                QTableWidgetItem(survey.state),
                SortItem(f"{done}/{total}" + (f", {skipped} skipped" if skipped else ""), (done, total)),
                SortItem(deposits_summary(survey), len(survey.deposits)),
                SortItem(f"{survey.poi.radius / 1000:.2f} km" if survey.poi.radius is not None else NO_VALUE,
                         survey.poi.radius or 0),
                self._coverage_item(survey),
            ]
            for column, item in enumerate(cells):
                item.setData(Qt.ItemDataRole.UserRole, survey.id)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(sorting)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == selected:
                self.table.selectRow(row)
        if not surveys:
            if not archived:
                self.message.setText("No archived surveys")
            elif self._system_text() and not matching_systems(self._system_text(), (s.poi.system for s in archived)):
                self.message.setText(f"No archived surveys in a system matching \"{self._system_text()}\"")
            else:
                self.message.setText("No archived surveys match")
        else:
            self.message.setText("")
        self._fill_deposits()

    @staticmethod
    def _coverage_item(survey):
        result = coverage(survey)
        item = SortItem(f"{result.fraction * 100:.0f}%", result.fraction)
        if result.assumed:
            item.setForeground(QBrush(ASSUMED_COLOR))
            item.setToolTip(f"{result.text()}: the radius hasn't been measured")
        else:
            item.setToolTip(result.text())
        return item

    def selected_id(self):
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def selected_survey(self):
        return self.surveys.get(self.selected_id())

    def _fill_deposits(self):
        survey = self.selected_survey()
        selected = self._selected_deposit_id()
        self.deposits.setRowCount(0)
        if survey is None:
            self.deposits_box.setTitle("Deposits (select a survey)")
            return
        self.deposits_box.setTitle(f"Deposits: {survey.poi.name or survey.poi.body}, {survey.poi.body}")
        self.deposits.setRowCount(len(survey.deposits))
        for row, deposit in enumerate(survey.deposits):
            estimated = f"{deposit.lat:.4f}, {deposit.lon:.4f}" + (" (typed)" if deposit.kind == TYPED else "")
            measured = f"{deposit.measured_lat:.4f}, {deposit.measured_lon:.4f}" if deposit.measured else NO_VALUE
            cells = [deposit.label, str(deposit.size) if deposit.size else NO_VALUE, deposit.density or NO_VALUE,
                     estimated, measured]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, deposit.id)
                self.deposits.setItem(row, column, item)
            if deposit.id == selected:
                self.deposits.selectRow(row)

    def _selected_deposit_id(self):
        items = self.deposits.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _selected_deposit(self):
        survey = self.selected_survey()
        return survey.deposit(self._selected_deposit_id()) if survey else None

    # --- actions --------------------------------------------------------------------------

    def _ask(self, title, text):
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def _reopen(self):
        survey = self.selected_survey()
        if survey is None:
            return
        if survey.state != CLOSED:
            self.message.setText("Only a closed survey can be re-opened. An unfinished one already resumes "
                                 "when you return to its POI.")
            return
        self.tracker.reopen(survey.id)

    def _close(self):
        survey = self.selected_survey()
        if survey is None:
            return
        if survey.state != UNFINISHED:
            self.message.setText("That survey is already closed")
            return
        name = f"{survey.poi.name} on {survey.poi.body}" if survey.poi.name else f"on {survey.poi.body}"
        if self._ask("Close survey", f"Close the survey {name}? "
                     "Closed surveys aren't resumed, but you can re-open them here."):
            self.tracker.close_archived(survey.id)

    def _set_radius(self):
        survey = self.selected_survey()
        if survey:
            RadiusDialog(self.tracker.editor(survey.id), survey, parent=self).exec()

    def _show_map(self):
        survey = self.selected_survey()
        if survey:
            window = MapWindow(self.tracker, survey.id, parent=self)
            self.maps.append(window)
            window.show()

    def _edit_deposit(self):
        survey, deposit = self.selected_survey(), self._selected_deposit()
        if deposit:
            self._edit(survey.id, deposit.id)

    def _edit(self, survey_id, deposit_id):
        survey = self.surveys.get(survey_id)  # as last read: the archive may have changed while the menu was open
        deposit = survey.deposit(deposit_id) if survey else None
        if deposit:
            EditDepositDialog(self.tracker.editor(survey.id), deposit, self).exec()

    def deposit_menu(self, item):
        """The right-click menu for a row of the Deposits table, or None off the rows. It selects the row first."""
        survey = self.selected_survey()
        deposit = survey.deposit(item.data(Qt.ItemDataRole.UserRole)) if survey and item else None
        if deposit is None:
            return None
        self.deposits.selectRow(item.row())
        survey_id, deposit_id = survey.id, deposit.id
        menu = QMenu(self)
        menu.addAction("Update size / location", lambda: self._edit(survey_id, deposit_id))
        return menu

    def _deposit_context_menu(self, position):
        menu = self.deposit_menu(self.deposits.itemAt(position))
        if menu:
            menu.exec(self.deposits.viewport().mapToGlobal(position))

    def _manual_entry(self):
        survey = self.selected_survey()
        if survey:
            ManualEntryDialog(self.tracker.editor(survey.id), target_ores(self.settings), self).exec()

    def _delete_deposit(self):
        survey, deposit = self.selected_survey(), self._selected_deposit()
        if deposit and self._ask("Delete deposit", f"Delete deposit {deposit.label} from this archived survey?"):
            self.tracker.editor(survey.id).delete_deposit(deposit.id)
