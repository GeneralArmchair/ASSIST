"""The deposit dialogs: Target Ores, marking a deposit, manual entry, and filling in size and measured position.
Also the POI radius form, which works the same way.

Target ores and their sort order are kept in QSettings, so they carry over between POIs and runs.
"""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QVBoxLayout)

from .ores import ALPHABETICAL, BY_PRICE, ORES, sorted_ores
from .survey import DENSITIES, MAX_RANGE_KM, MAX_SIZE, USUAL_RADIUS_KM, parse_lat_lon, parse_radius_km, parse_range_km, unusual_radius
from .tracker import SAVED

WARNING_STYLE = "background-color: #e69a28; color: black; padding: 4px;"
PROBLEM_STYLE = "color: #c0392b; font-weight: bold;"


def ore_order(settings):
    return settings.value("target_ores_order", ALPHABETICAL, type=str)


def target_ores(settings):
    """The ticked ores in the chosen sort order."""
    return sorted_ores(settings.value("target_ores", [], type=list), ore_order(settings))


def save_target_ores(settings, ores, order):
    settings.setValue("target_ores", list(ores))
    settings.setValue("target_ores_order", order)


class TargetOresDialog(QDialog):
    def __init__(self, ticked, order, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Define Target Ores")
        self.alphabetical = QRadioButton("Alphabetical")
        self.by_price = QRadioButton("By galactic average price")
        (self.by_price if order == BY_PRICE else self.alphabetical).setChecked(True)
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Sort:"))
        sort_row.addWidget(self.alphabetical)
        sort_row.addWidget(self.by_price)
        sort_row.addStretch()

        self.list = QListWidget()
        self.list.setMinimumSize(340, 520)
        self._fill(ticked)
        self.by_price.toggled.connect(lambda: self._fill(self.ticked()))  # fires for both radio buttons

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("The ticked ores are the choices when marking a deposit."))
        layout.addLayout(sort_row)
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def order(self):
        return BY_PRICE if self.by_price.isChecked() else ALPHABETICAL

    def _fill(self, ticked):
        self.list.clear()
        for name in sorted_ores(order=self.order()):
            item = QListWidgetItem(f"{name}  ({ORES[name]:,} cr)")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setCheckState(Qt.CheckState.Checked if name in ticked else Qt.CheckState.Unchecked)
            self.list.addItem(item)

    def ticked(self):
        items = (self.list.item(row) for row in range(self.list.count()))
        return [item.data(Qt.ItemDataRole.UserRole) for item in items
                if item.checkState() == Qt.CheckState.Checked]


class OreRadios(QGroupBox):
    """A radio button per target ore. Only a single target is preselected.

    The first nine are numbered: with the focus on the ores, pressing a number picks that ore and moves the
    focus to next_field, so marking needs no mouse (Armchair, 2026-11-08).
    """

    def __init__(self, ores, parent=None):
        super().__init__("Ore", parent)
        self.group = QButtonGroup(self)
        self.next_field = None
        layout = QVBoxLayout(self)
        for number, name in enumerate(ores, start=1):
            radio = QRadioButton(f"{number}   {name}" if number <= 9 else name)
            radio.setProperty("ore", name)
            radio.installEventFilter(self)
            self.group.addButton(radio)
            layout.addWidget(radio)
        if not ores:
            layout.addWidget(QLabel('No target ores are ticked. Cancel this and use the "Define Target Ores" button first.'))
        if len(ores) == 1:
            self.group.buttons()[0].setChecked(True)
        elif ores:
            hint = QLabel("Press an ore's number to pick it.")
            hint.setEnabled(False)  # greyed: a hint, not a field
            layout.addWidget(hint)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_9:
            self.pick(event.key() - Qt.Key.Key_1 + 1)
            return True
        return super().eventFilter(watched, event)

    def pick(self, number):
        """Check the numbered ore and move on. A number with no ore does nothing."""
        buttons = self.group.buttons()
        if 1 <= number <= min(9, len(buttons)):
            buttons[number - 1].setChecked(True)
            if self.next_field is not None:
                self.next_field.setFocus()

    def ore(self):
        checked = self.group.checkedButton()
        return checked.property("ore") if checked else None

    def needs_choice(self):
        return len(self.group.buttons()) > 1


class DensityRadios(QGroupBox):
    """A deposit's density, seen at long range: not recorded, High, Medium or Low.

    Pressing H, M or L picks one from anywhere in the form (see watch), so it costs one key when marking.
    """

    KEYS = {Qt.Key.Key_H: "High", Qt.Key.Key_M: "Medium", Qt.Key.Key_L: "Low"}

    def __init__(self, density=None, parent=None):
        super().__init__("Density", parent)
        self.group = QButtonGroup(self)
        row = QHBoxLayout(self)
        for value in (None, *DENSITIES):
            radio = QRadioButton(f"{value} ({value[0]})" if value else "Not recorded")
            radio.setProperty("density", value or "")
            radio.setChecked(value == density)
            self.group.addButton(radio)
            row.addWidget(radio)
            self.watch(radio)
        row.addStretch()

    def watch(self, widget):
        """Let H, M and L typed in this widget pick the density."""
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.KeyPress and event.key() in self.KEYS
                and not event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier):
            self.set(self.KEYS[event.key()])
            return True
        return super().eventFilter(watched, event)

    def set(self, density):
        for radio in self.group.buttons():
            radio.setChecked((radio.property("density") or None) == density)

    def density(self):
        checked = self.group.checkedButton()
        return (checked.property("density") or None) if checked else None


class _DepositForm(QDialog):
    """Shared layout: the fields, then a message line, then Save and Cancel. Enter saves."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(PROBLEM_STYLE)
        self.message.hide()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.fields = QVBoxLayout()
        layout = QVBoxLayout(self)
        layout.addLayout(self.fields)
        layout.addWidget(self.message)
        layout.addWidget(self.buttons)

    def show_message(self, text):
        self.message.setText(text)
        self.message.show()

    def save(self):
        raise NotImplementedError


class MarkDepositDialog(_DepositForm):
    """Ore and range for a sighting. Stays open if nothing was saved, showing why."""

    def __init__(self, tracker, sighting, ores, parent=None):
        super().__init__("Remote Mark Deposit", parent)
        self.tracker = tracker
        self.sighting = sighting
        self.fields.addWidget(QLabel(f"Heading {sighting.heading:03d}° from {sighting.lat:.4f}, {sighting.lon:.4f}"))
        for warning in sighting.warnings:
            label = QLabel(warning)
            label.setWordWrap(True)
            label.setStyleSheet(WARNING_STYLE)
            self.fields.addWidget(label)
        self.radios = OreRadios(ores)
        self.fields.addWidget(self.radios)
        self.density = DensityRadios()
        self.fields.addWidget(self.density)
        self.range = QLineEdit()
        self.range.setPlaceholderText(f"up to {MAX_RANGE_KM}")
        self.density.watch(self.range)
        for radio in self.radios.group.buttons():
            self.density.watch(radio)
        form = QFormLayout()
        form.addRow("Range (km):", self.range)
        self.fields.addLayout(form)
        hint = QLabel("Press H, M or L for the density, then type the range.")
        hint.setEnabled(False)  # greyed: a hint, not a field
        self.fields.addWidget(hint)
        self.radios.next_field = self.range
        if self.radios.needs_choice():
            self.radios.group.buttons()[0].setFocus()
        else:
            self.range.setFocus()

    def save(self):
        ore = self.radios.ore()
        if ore is None:
            self.show_message("Pick an ore")
            return
        try:
            range_km = parse_range_km(self.range.text())
        except ValueError as error:
            self.show_message(str(error))
            return
        result = self.tracker.mark_deposit(self.sighting, ore, range_km, self.density.density())
        if result.outcome == SAVED:
            self.accept()
        else:
            self.show_message(result.message)


class ManualEntryDialog(_DepositForm):
    """Ore and typed position, with no duplicate check: for real twins close together."""

    def __init__(self, tracker, ores, parent=None):
        super().__init__("Manual Mark Deposit", parent)
        self.tracker = tracker
        self.radios = OreRadios(ores)
        self.fields.addWidget(self.radios)
        self.density = DensityRadios()
        self.fields.addWidget(self.density)
        self.lat = QLineEdit()
        self.lon = QLineEdit()
        for widget in (self.lat, self.lon, *self.radios.group.buttons()):
            self.density.watch(widget)
        form = QFormLayout()
        form.addRow("Latitude:", self.lat)
        form.addRow("Longitude:", self.lon)
        self.fields.addLayout(form)
        self.radios.next_field = self.lat

    def save(self):
        ore = self.radios.ore()
        if ore is None:
            self.show_message("Pick an ore")
            return
        try:
            lat, lon = parse_lat_lon(self.lat.text(), self.lon.text())
        except ValueError as error:
            self.show_message(str(error))
            return
        self.tracker.add_manual_deposit(ore, lat, lon, self.density.density())
        self.accept()


class EditDepositDialog(_DepositForm):
    """Size and measured position, read off the HUD on a visit. The estimate stays as it was."""

    def __init__(self, tracker, deposit, parent=None):
        super().__init__(f"Deposit {deposit.label}", parent)
        self.tracker = tracker
        self.deposit_id = deposit.id
        self.size = QComboBox()
        self.size.addItem("(blank)", None)
        for size in range(1, MAX_SIZE + 1):
            self.size.addItem(str(size), size)
        self.size.setCurrentIndex(self.size.findData(deposit.size))
        self.density = QComboBox()
        self.density.addItem("(blank)", None)
        for value in DENSITIES:
            self.density.addItem(value, value)
        self.density.setCurrentIndex(max(0, self.density.findData(deposit.density)))
        self.lat = QLineEdit("" if deposit.measured_lat is None else str(deposit.measured_lat))
        self.lon = QLineEdit("" if deposit.measured_lon is None else str(deposit.measured_lon))
        form = QFormLayout()
        form.addRow("Estimated position:", QLabel(f"{deposit.lat:.4f}, {deposit.lon:.4f}"))
        form.addRow(f"Size (1-{MAX_SIZE}):", self.size)
        form.addRow("Density:", self.density)
        form.addRow("Measured latitude:", self.lat)
        form.addRow("Measured longitude:", self.lon)
        self.fields.addLayout(form)

    def save(self):
        lat_text, lon_text = self.lat.text().strip(), self.lon.text().strip()
        lat = lon = None
        if lat_text or lon_text:  # both blank clears the measured position
            try:
                lat, lon = parse_lat_lon(lat_text, lon_text)
            except ValueError as error:
                self.show_message(str(error))
                return
        self.tracker.update_deposit(self.deposit_id, self.size.currentData(), lat, lon, self.density.currentData())
        self.accept()


class RadiusDialog(_DepositForm):
    """The golden-ring radius: the range to the POI center on the HUD, typed while standing on the ring.

    target is the tracker (active survey) or an ArchivedSurvey. Where the player is comes from when the form opened.
    A radius outside the usual range needs a second Save. Blank and Save clears it.
    """

    def __init__(self, target, survey, where=(None, None, None), parent=None):
        super().__init__("Set POI radius", parent)
        self.target = target
        self.where = where
        self.warned = None  # the text that has been warned about
        radius = survey.poi.radius
        name = survey.poi.name or survey.poi.body
        hint = QLabel(f"{name}: drive to the golden ring and type the range to the POI center shown on the HUD.\n"
                      "Leave it blank to clear it.")
        hint.setWordWrap(True)
        self.fields.addWidget(hint)
        self.range = QLineEdit("" if radius is None else f"{radius / 1000:.2f}")
        self.range.setPlaceholderText("such as 5.12")
        self.range.selectAll()
        form = QFormLayout()
        form.addRow("Radius (km):", self.range)
        self.fields.addLayout(form)
        self.range.setFocus()

    def save(self):
        text = self.range.text()
        try:
            km = parse_radius_km(text)
        except ValueError as error:
            self.show_message(str(error))
            return
        if km is not None and unusual_radius(km) and self.warned != text:
            low, high = USUAL_RADIUS_KM
            self.warned = text
            self.show_message(f"{km:.2f} km is outside the usual {low:g}-{high:g} km (4.3-7.05 km seen so far). "
                              "Save again to keep it.")
            return
        self.target.set_radius(km, self.where)
        self.accept()


class CustomDestinationDialog(_DepositForm):
    """A typed latitude and longitude to navigate to: the custom destination (Milestone 8). Nothing is saved."""

    def __init__(self, tracker, parent=None):
        super().__init__("Custom destination", parent)
        self.tracker = tracker
        hint = QLabel("Type a latitude and longitude, such as one read off the map's grid.\n"
                      "ASSIST guides you there until you pick another target or clear it.\n"
                      'Right-clicking the map ("Navigate here") does the same without typing.')
        hint.setWordWrap(True)
        self.fields.addWidget(hint)
        self.lat = QLineEdit()
        self.lon = QLineEdit()
        self.lat.setPlaceholderText("such as 55.6676")
        self.lon.setPlaceholderText("such as 111.0289")
        form = QFormLayout()
        form.addRow("Latitude:", self.lat)
        form.addRow("Longitude:", self.lon)
        self.fields.addLayout(form)
        self.setMinimumWidth(380)  # the hint reads badly when wrapped narrower
        self.lat.setFocus()

    def save(self):
        try:
            lat, lon = parse_lat_lon(self.lat.text(), self.lon.text())
        except ValueError as error:
            self.show_message(str(error))
            return
        if self.tracker.set_custom(lat, lon):
            self.accept()
        else:
            self.show_message("No active survey: deploy the SRV or resume a survey first")
