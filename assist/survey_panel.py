"""The survey part of the main window: POI, survey points, deposits, pings and the action buttons."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QFormLayout, QGridLayout, QGroupBox, QHeaderView,
                               QLabel, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import geo
from .coverage import coverage
from .deposit_forms import (CustomDestinationDialog, EditDepositDialog, ManualEntryDialog, MarkDepositDialog, RadiusDialog, TargetOresDialog,
                            ore_order, save_target_ores, target_ores)
from .guidance import OFF_TARGET, READY, format_distance, pitch_text, turn_text
from .survey import AUTO_SKIPPED, NO_PING, PING_KIND_NAMES, TYPED
from .map_view import MapWindow
from .surveys_dialog import SurveysDialog

NO_VALUE = "—"
CUE_STYLES = {READY: "background-color: #2e9e4f; color: white;",
              OFF_TARGET: "background-color: #e69a28; color: black;"}
ON_COURSE_STYLE = "color: #2e9e4f;"
STALE_STYLE = "color: #8a6fb0; font-style: italic;"  # a distinct color: not live guidance
TARGET_COLOR = QColor(255, 200, 60, 90)
DONE_COLOR = QColor(80, 180, 80, 60)
FAR_OFF_COLOR = QColor(230, 150, 40, 70)  # done, but the ping landed outside the point's tolerance
SKIPPED_TEXT = QColor("#8a8a8a")  # skipped points: grey and struck through, readable on dark and light themes


def local_time(timestamp):
    """A game UTC timestamp as local HH:MM:SS, like the log."""
    try:
        return f"{datetime.fromisoformat(timestamp).astimezone():%H:%M:%S}"
    except (TypeError, ValueError):
        return timestamp or NO_VALUE


class SurveyPanel(QWidget):
    def __init__(self, tracker, settings, parent=None):
        super().__init__(parent)
        self.tracker = tracker
        self.settings = settings
        self.mark_form = None  # the open Remote Mark Deposit form
        self.radius_form = None  # the open Set POI radius form
        self.map_window = None  # the live map, made when first opened
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self.values = {}
        form = QFormLayout()
        for key in ("POI", "Where", "Center", "State", "Radius", "Coverage"):
            label = QLabel(NO_VALUE)
            label.setFont(mono)
            label.setWordWrap(True)
            self.values[key] = label
            form.addRow(key + ":", label)
        poi_box = QGroupBox("Survey")
        poi_box.setLayout(form)

        large = QFont(mono)
        large.setPointSize(16)
        large.setBold(True)
        self.g_target = self._guidance_label(large)
        self.g_turn = self._guidance_label(large)
        self.g_pitch = self._guidance_label(large)
        self.g_cue = self._guidance_label(large)
        guidance_box = QGroupBox("Guidance")
        guidance_layout = QVBoxLayout(guidance_box)
        for label in (self.g_target, self.g_turn, self.g_pitch, self.g_cue):
            guidance_layout.addWidget(label)

        self.points =self._table(["Point", "Tier", "State", "Distance", "Bearing"], mono)
        # No row selection: the only highlighted row should be the target.
        self.points.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.points.cellClicked.connect(self._point_clicked)
        point_buttons = QGridLayout()
        self._button(point_buttons, 0, 0, "Previous point", lambda: tracker.step_target(-1))
        self._button(point_buttons, 0, 1, "Next point", lambda: tracker.step_target(1))
        self._button(point_buttons, 0, 2, "Skip / unskip target", self._toggle_skip)
        self._button(point_buttons, 1, 0, "Custom destination...", self.custom_destination,
                     "Navigate to a latitude and longitude of your choosing (or right-click the map).\n"
                     "A navigation aid only: nothing is recorded.")
        self._button(point_buttons, 1, 1, "Clear custom destination", tracker.clear_custom,
                     "Stop guiding to the custom destination and go back to the next survey point")
        points_box = QGroupBox("Survey points (click a row to select it as the target)")
        points_layout = QVBoxLayout(points_box)
        points_layout.addWidget(self.points)
        points_layout.addLayout(point_buttons)

        self.deposits = self._table(["Deposit", "Size", "Density", "Estimated lat, long", "Measured lat, long", "Distance", "Bearing"], mono)
        self.deposits.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.deposits.customContextMenuRequested.connect(self._deposit_context_menu)
        deposit_buttons = QGridLayout()
        self._button(deposit_buttons, 0, 0, "Define Target Ores", self.choose_target_ores,
                     "Choose which ores are offered when marking a deposit")
        self._button(deposit_buttons, 0, 1, "Remote Mark Deposit", self.mark_deposit,
                     "Takes the position and heading now, then asks for the ore and range.\n"
                     "Stop with the handbrake first, pointing the SRV at the deposit.")
        self._button(deposit_buttons, 0, 2, "Manual Mark Deposit", self.manual_entry,
                     "Add a deposit by typed latitude and longitude, with no duplicate check,\n"
                     "for distinct deposits close together.")
        self._button(deposit_buttons, 1, 0, "Navigate to selected", self._fly_to_deposit)
        self._button(deposit_buttons, 1, 1, "Update size / location", self._edit_deposit,
                     "Fill in the size and the position read off the HUD")
        self._button(deposit_buttons, 1, 2, "Delete selected", self._delete_deposit)
        self.target_ores_label = QLabel()
        self.target_ores_label.setWordWrap(True)
        self._show_target_ores()
        self.deposits_box = QGroupBox("Deposits")  # the window places it, in the roomier left column
        deposits_layout = QVBoxLayout(self.deposits_box)
        deposits_layout.addWidget(self.target_ores_label)
        deposits_layout.addWidget(self.deposits)
        deposits_layout.addLayout(deposit_buttons)

        self.pings = self._table(["#", "Time", "Kind", "At point", "Latitude, longitude"], mono)
        ping_buttons = QGridLayout()
        self._button(ping_buttons, 0, 0, "Manual ping", tracker.manual_ping)
        self._button(ping_buttons, 0, 1, "Undo last ping", tracker.undo_last_ping)
        self._button(ping_buttons, 0, 2, "Assign to target", self._assign_to_target,
                     "Make the selected ping complete the current target point,\nhowever far off it landed.")
        self._button(ping_buttons, 0, 3, "Delete selected", self._delete_ping)
        pings_box = QGroupBox("Pings")
        pings_layout = QVBoxLayout(pings_box)
        pings_layout.addWidget(self.pings)
        pings_layout.addLayout(ping_buttons)

        survey_buttons = QGridLayout()
        self._button(survey_buttons, 0, 0, "Start new survey here", self._start_survey)
        self._button(survey_buttons, 0, 1, "Close survey", self._close_survey)
        self._button(survey_buttons, 0, 2, "Surveys...", self.show_surveys,
                     "Every archived survey: re-open or close them, and fill in deposit sizes from anywhere")
        self._button(survey_buttons, 1, 0, "Set POI radius", self.set_radius,
                     "Drive to the golden ring, then type the range to the POI center shown on the HUD")
        self._button(survey_buttons, 1, 1, "Map...", self.show_map,
                     "The survey map with scanner coverage, live")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(guidance_box)
        layout.addWidget(poi_box)
        layout.addWidget(points_box, stretch=3)
        layout.addWidget(pings_box, stretch=2)
        layout.addLayout(survey_buttons)

        tracker.add_listener(lambda message: self.refresh())
        self.refresh()

    @staticmethod
    def _guidance_label(font):
        label = QLabel(NO_VALUE)
        label.setFont(font)
        return label

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

    def refresh(self):
        """Redraw everything. Called after every survey change and every new status."""
        survey = self.tracker.survey
        v = self.values
        self._show_guidance(survey)
        if survey is None:
            for key in v:
                v[key].setText(NO_VALUE)
            v["State"].setText("no active survey (deploy the SRV to start one, or target an "
                               "unfinished survey's mining signal to resume it)")
            self.points.setRowCount(0)
            self.deposits.setRowCount(0)
            self.pings.setRowCount(0)
            return

        poi = survey.poi
        v["POI"].setText(poi.name or "(no mining signal targeted)")
        v["Where"].setText(f"{poi.system or '?'} / {poi.body}")
        v["Center"].setText(f"{poi.center_lat:.6f}, {poi.center_lon:.6f}")
        done, skipped, total = survey.progress()
        unconfirmed = sum(1 for p in survey.pings if p.kind == NO_PING)
        state = f"{done}/{total} points done"
        if skipped:
            state += f", {skipped} skipped"
        state += f", {len(survey.pings)} pings"
        if unconfirmed:
            state += f" ({unconfirmed} without a detected ping)"
        v["State"].setText(state)
        v["Radius"].setText(self.radius_text(survey))
        v["Coverage"].setText(coverage(survey).text())
        self._fill_points(survey)
        self._fill_deposits(survey)
        self._fill_pings(survey)

    @staticmethod
    def radius_text(survey):
        poi = survey.poi
        if poi.radius is None:
            return 'not measured (use "Set POI radius" at the golden ring)'
        text = f"{poi.radius / 1000:.2f} km"
        offset = survey.center_offset()
        if offset is not None:
            text += f" (ASSIST's center is at least {offset:,.0f} m from the game's)"
        return text

    def _show_guidance(self, survey):
        g = self.tracker.guidance()
        if g is None:
            if survey is None:
                text = "No active survey"
            elif survey.target:
                text = f"{self.target_text(survey)}  (no position from the game)"
            else:
                text = "All points done or skipped"
            self.g_target.setText(text)
            for label in (self.g_turn, self.g_pitch, self.g_cue):
                label.setText("")
                label.setStyleSheet("")
            return
        self.g_target.setText(f"{self.target_text(survey)}  {format_distance(g.distance)}")
        self.g_turn.setText(turn_text(g.heading_error, g.bearing))
        self.g_turn.setStyleSheet(ON_COURSE_STYLE if g.on_course else "")
        self.g_pitch.setText(pitch_text(g.pitch, g.pitch_stale))
        self.g_pitch.setStyleSheet(STALE_STYLE if g.pitch_stale else "")
        self.g_cue.setText(g.cue_text)
        self.g_cue.setStyleSheet(CUE_STYLES.get(g.cue, ""))

    @staticmethod
    def target_text(survey):
        """The target's label; the custom destination shows its lat/long: "Custom (55.6676, 111.0289)"."""
        if survey.target_is_custom():
            return f"{survey.target} ({survey.custom.lat:.4f}, {survey.custom.lon:.4f})"
        return survey.target

    def custom_destination(self):
        CustomDestinationDialog(self.tracker, self).exec()

    def _fill_points(self, survey):
        where = self.tracker.position()
        bold = QFont(self.points.font())
        bold.setBold(True)
        struck = QFont(self.points.font())
        struck.setStrikeOut(True)
        self.points.setRowCount(len(survey.waypoints))
        for row, waypoint in enumerate(survey.waypoints):
            ping = survey.done_by(waypoint)
            if ping:
                d = survey.distance(waypoint.lat, waypoint.lon, ping.lat, ping.lon)
                state = f"done (#{ping.id}, {format_distance(d)} off)"
            elif waypoint.skipped:
                state = "skipped (adds under 1%)" if waypoint.auto_skip == AUTO_SKIPPED else "skipped"
            else:
                state = ""
            if waypoint.label == survey.target:
                state = ("TARGET  " + state).strip()
            if where:
                distance = format_distance(survey.distance(where[0], where[1], waypoint.lat, waypoint.lon))
                heading = f"{geo.bearing(where[0], where[1], waypoint.lat, waypoint.lon):03.0f}°"
            else:
                distance = heading = NO_VALUE
            cells = [waypoint.label, str(waypoint.tier), state, distance, heading]
            far_off = ping is not None and d > waypoint.tolerance
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if waypoint.label == survey.target:
                    item.setBackground(QBrush(TARGET_COLOR))
                    item.setFont(bold)
                elif ping:
                    item.setBackground(QBrush(FAR_OFF_COLOR if far_off else DONE_COLOR))
                elif waypoint.skipped:  # by the player or by ASSIST: the state column says which
                    item.setForeground(QBrush(SKIPPED_TEXT))
                    if column != 2:  # the state stays plain, so "skipped (adds under 1%)" reads easily
                        item.setFont(struck)
                self.points.setItem(row, column, item)

    def _fill_deposits(self, survey):
        where = self.tracker.position()
        selected = self._selected_id(self.deposits)
        bold = QFont(self.deposits.font())
        bold.setBold(True)
        self.deposits.setRowCount(len(survey.deposits))
        for row, deposit in enumerate(survey.deposits):
            estimated = f"{deposit.lat:.4f}, {deposit.lon:.4f}" + (" (typed)" if deposit.kind == TYPED else "")
            measured = f"{deposit.measured_lat:.4f}, {deposit.measured_lon:.4f}" if deposit.measured else NO_VALUE
            if where:
                distance = format_distance(survey.distance(*where, *deposit.position))
                heading = f"{geo.bearing(*where, *deposit.position):03.0f}°"
            else:
                distance = heading = NO_VALUE
            cells = [deposit.label, str(deposit.size) if deposit.size else NO_VALUE, deposit.density or NO_VALUE,
                     estimated, measured, distance, heading]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, deposit.id)
                if deposit.label == survey.target:
                    item.setBackground(QBrush(TARGET_COLOR))
                    item.setFont(bold)
                self.deposits.setItem(row, column, item)
            if deposit.id == selected:
                self.deposits.selectRow(row)

    def _fill_pings(self, survey):
        selected = self._selected_id(self.pings)
        self.pings.setRowCount(len(survey.pings))
        for row, ping in enumerate(reversed(survey.pings)):  # newest first
            if ping.counts:
                labels = []
                for label in survey.points_for(ping):
                    w = survey.waypoint(label)
                    labels.append(f"{label} ({format_distance(survey.distance(w.lat, w.lon, ping.lat, ping.lon))})")
                at = ", ".join(labels) or "(extra)"
            else:
                at = f"(aimed at {ping.point})" if ping.point else ""
            cells = [str(ping.id), local_time(ping.time), PING_KIND_NAMES.get(ping.kind, ping.kind),
                     at, f"{ping.lat:.6f}, {ping.lon:.6f}"]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, ping.id)
                self.pings.setItem(row, column, item)
            if ping.id == selected:
                self.pings.selectRow(row)

    @staticmethod
    def _selected_id(table):
        items = table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _selected_ping_id(self):
        return self._selected_id(self.pings)

    def _selected_deposit(self):
        survey = self.tracker.survey
        return survey.deposit(self._selected_id(self.deposits)) if survey else None

    def _show_target_ores(self):
        self.target_ores_label.setText("Target ores: " + (", ".join(target_ores(self.settings)) or "none ticked"))

    # --- actions --------------------------------------------------------------------------

    def _point_clicked(self, row, column):
        survey = self.tracker.survey
        if survey and row < len(survey.waypoints):
            self.tracker.select_target(survey.waypoints[row].label)

    def _toggle_skip(self):
        if self.tracker.survey and self.tracker.survey.target:
            self.tracker.toggle_skip(self.tracker.survey.target)

    def _assign_to_target(self):
        ping_id = self._selected_ping_id()
        if ping_id is not None:
            self.tracker.assign_to_target(ping_id)

    def _delete_ping(self):
        ping_id = self._selected_ping_id()
        if ping_id is not None:
            self.tracker.delete_ping(ping_id)

    def choose_target_ores(self):
        dialog = TargetOresDialog(target_ores(self.settings), ore_order(self.settings), self)
        if dialog.exec():
            save_target_ores(self.settings, dialog.ticked(), dialog.order())
            self._show_target_ores()

    def mark_deposit(self):
        """Take the position and heading now, then open the form. This is the hotkey's action."""
        if self.mark_form is None:
            sighting = self.tracker.capture_sighting()
            if sighting is None:
                return
            self.mark_form = MarkDepositDialog(self.tracker, sighting, target_ores(self.settings), self,
                                               settings=self.settings)
            self.mark_form.finished.connect(self._mark_form_closed)
            self.mark_form.open()
        # Otherwise a form is already open: bring it back rather than silently retaking the sighting.
        self._bring_forward(self.mark_form)

    def _mark_form_closed(self):
        self.mark_form.deleteLater()
        self.mark_form = None

    def _bring_forward(self, dialog):
        """The hotkey is pressed with the game focused, so the form has to take the keyboard for the range."""
        if self.window().isMinimized():
            self.window().showNormal()
        dialog.raise_()
        dialog.activateWindow()

    def set_radius(self):
        """The Set POI radius form; also the hotkey's action. Where the player stands is taken now."""
        survey = self.tracker.survey
        if survey is None:
            self.tracker.set_radius(None)  # says there's no active survey
            return
        if self.radius_form is None:
            self.radius_form = RadiusDialog(self.tracker, survey, self.tracker.radius_position(), self)
            self.radius_form.finished.connect(self._radius_form_closed)
            self.radius_form.open()
        self._bring_forward(self.radius_form)

    def _radius_form_closed(self):
        self.radius_form.deleteLater()
        self.radius_form = None

    def show_map(self):
        if self.map_window is None:
            self.map_window = MapWindow(self.tracker, parent=self)
        self.map_window.show()
        self.map_window.raise_()
        self.map_window.activateWindow()

    def status_changed(self):
        if self.map_window is not None:
            self.map_window.status_changed()

    def manual_entry(self):
        """Manual Mark Deposit, filled in with the player's position now. It may hand over to a nearby deposit's
        Update size / location form instead."""
        if not self.tracker.can_add_deposit():
            return
        form = ManualEntryDialog(self.tracker, target_ores(self.settings), self,
                                 where=self.tracker.manual_position(), check_near=True)
        form.exec()
        if form.update_instead:
            deposit, lat, lon, density = form.update_instead
            self._edit(deposit.id, measured=(lat, lon), density=density)

    def deposit_menu(self, item):
        """The right-click menu for a row of the Deposits table, or None off the rows.

        It selects the row first, and its actions keep to that deposit even if the table is redrawn meanwhile.
        """
        survey = self.tracker.survey
        deposit = survey.deposit(item.data(Qt.ItemDataRole.UserRole)) if survey and item else None
        if deposit is None:
            return None
        self.deposits.selectRow(item.row())
        deposit_id = deposit.id
        menu = QMenu(self)
        same = lambda: self.tracker.survey is survey  # not another survey's deposit with the same number
        menu.addAction("Navigate to selected", lambda: same() and self._fly_to(deposit_id))
        menu.addAction("Update size / location", lambda: same() and self._edit(deposit_id))
        return menu

    def _deposit_context_menu(self, position):
        menu = self.deposit_menu(self.deposits.itemAt(position))
        if menu:
            menu.exec(self.deposits.viewport().mapToGlobal(position))

    def _fly_to_deposit(self):
        deposit = self._selected_deposit()
        if deposit:
            self._fly_to(deposit.id)

    def _edit_deposit(self):
        deposit = self._selected_deposit()
        if deposit:
            self._edit(deposit.id)

    # The buttons act on the selected row, the right-click menu on the deposit it was opened on.
    # (Separate methods: a button's clicked signal would pass its checked flag as the id.)
    def _fly_to(self, deposit_id):
        self.tracker.fly_to_deposit(deposit_id)

    def _edit(self, deposit_id, **filled):
        survey = self.tracker.survey
        deposit = survey.deposit(deposit_id) if survey else None
        if deposit:  # gone if it was deleted, or the survey closed, while the menu was open
            EditDepositDialog(self.tracker, deposit, self, **filled).exec()

    def _delete_deposit(self):
        deposit = self._selected_deposit()
        if deposit and self._ask("Delete deposit", f"Delete deposit {deposit.label}?"):
            self.tracker.delete_deposit(deposit.id)

    def _ask(self, title, text):
        answer = QMessageBox.question(self, title, text)
        return answer == QMessageBox.StandardButton.Yes

    def _start_survey(self):
        if self.tracker.survey and not self._ask(
                "Start new survey", "Archive the current survey as unfinished and start a new one here?"):
            return
        self.tracker.start_survey()

    def _close_survey(self):
        survey = self.tracker.survey
        if not survey:
            return
        if self._ask("Close survey", 'Close this survey? Closed surveys can be re-opened within the "Surveys..." menu'):
            self.tracker.close_survey()

    def show_surveys(self):
        SurveysDialog(self.tracker, self.settings, self).exec()
