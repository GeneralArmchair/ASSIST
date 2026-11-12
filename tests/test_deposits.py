"""Desk tests for deposit marking: the ore list, duplicates, forms, the deposit list and flying to a deposit."""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from make_test_recording import CENTER, PLANET_RADIUS
from assist import geo
from assist.deposit_forms import (EditDepositDialog, ManualEntryDialog, MarkDepositDialog, OreRadios, TargetOresDialog,
                                  ore_order, save_target_ores, target_ores)
from assist.guidance import NOT_READY, READY
from assist.ores import ALPHABETICAL, BY_PRICE, ORES, overlay_label, sorted_ores
from assist.overlay import MISS, DEPLOY_READY, WIDEST_TITLE, box_size, build_view, overlay_font
from assist.status import HARDPOINTS, SRV_HANDBRAKE, SRV_TURRET_VIEW
from assist.survey import DEPOSIT, SIGHTED, TYPED, Survey, SurveyStore, parse_lat_lon, parse_range_km
from assist.survey_panel import SurveyPanel
from assist.tracker import DUPLICATE, NO_SURVEY, SAVED, SUSPECTED, Sighting, SurveyTracker
from tests.test_guidance import FLYING, LAT_LONG, PATTERN, SHIELDS, SRV, offset, status

app = QApplication.instance() or QApplication([])

HANDBRAKE = 1 << SRV_HANDBRAKE
TURRET_VIEW = 1 << SRV_TURRET_VIEW
HARDPOINTS_ON = 1 << HARDPOINTS
SRV_STOPPED = SHIELDS | LAT_LONG | SRV
SHIP_BOARDED = FLYING
PLATINUM_RANGE = 3.0  # km, due north of the sighting position


class TrackerCase(unittest.TestCase):
    """A tracker with an active survey, the player in the SRV with the handbrake on, at the POI center."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SurveyStore(self.folder.name)
        self.tracker = SurveyTracker(self.store, PATTERN)
        self.messages = []
        self.tracker.add_listener(lambda m: m and self.messages.append(m))
        self.tracker.update(status())           # in the ship, hovering
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE, second=1))  # deployed: the survey starts
        self.survey = self.tracker.survey
        self.survey.target = "I1"

    def sighting(self, heading=0, lat=CENTER[0], lon=CENTER[1], warnings=()):
        return Sighting(lat, lon, heading, "2025-01-01T00:00:05Z", warnings)

    def mark(self, ore="Platinum", km=PLATINUM_RANGE, heading=0, **kwargs):
        return self.tracker.mark_deposit(self.sighting(heading, **kwargs), ore, km)

    def reload(self):
        return SurveyStore(self.folder.name).load_active()


class OreListTests(unittest.TestCase):
    def test_all_37_with_prices(self):
        self.assertEqual(len(ORES), 37)
        self.assertEqual(ORES["Alexandrite"], 205143)
        self.assertEqual(ORES["Haematite"], 1791)
        self.assertEqual(ORES["Water"], 260)

    def test_alphabetical(self):
        ores = sorted_ores()
        self.assertEqual(ores[0], "Alexandrite")
        self.assertEqual(ores[-1], "Water")

    def test_by_price_highest_first_ties_alphabetical(self):
        ores = sorted_ores(order=BY_PRICE)
        self.assertEqual(ores[:2], ["Alexandrite", "Monazite"])
        self.assertEqual(ores[-1], "Water")
        i = ores.index("Iridium")
        self.assertEqual(ores[i:i + 3], ["Iridium", "Periclase Dunite", "Thortveitite"])

    def test_ore_removed_from_the_list_is_dropped(self):
        self.assertEqual(sorted_ores(["Water", "Unobtainium"]), ["Water"])


class ParsingTests(unittest.TestCase):
    def test_range_accepts_km(self):
        self.assertEqual(parse_range_km("2.37"), 2.37)
        self.assertEqual(parse_range_km(" 0.041 "), 0.041)
        self.assertEqual(parse_range_km("2,37"), 2.37)
        self.assertEqual(parse_range_km("20"), 20)

    def test_range_above_20_rejected(self):
        with self.assertRaisesRegex(ValueError, "more than 20 km"):
            parse_range_km("20.01")

    def test_bad_ranges_rejected(self):
        for text in ("", "abc", "0", "-1", "nan", "1.5 km"):
            with self.assertRaises(ValueError, msg=text):
                parse_range_km(text)

    def test_lat_lon(self):
        self.assertEqual(parse_lat_lon("-12.3456", "45,5"), (-12.3456, 45.5))

    def test_bad_lat_lon_rejected(self):
        for lat, lon in (("91", "0"), ("0", "181"), ("", "1"), ("1", ""), ("nan", "0"), ("x", "0")):
            with self.assertRaises(ValueError, msg=(lat, lon)):
                parse_lat_lon(lat, lon)


class SightingTests(TrackerCase):
    def test_position_and_heading_taken_at_the_press(self):
        sighting = self.tracker.capture_sighting()
        lat, lon = offset(1000)
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE, lat=lat, lon=lon, heading=200, second=9))
        self.assertEqual((sighting.lat, sighting.lon, sighting.heading), (*CENTER, 0))
        result = self.tracker.mark_deposit(sighting, "Platinum", 2)
        self.assertEqual((result.deposit.from_lat, result.deposit.from_lon, result.deposit.heading), (*CENTER, 0))

    def test_no_warnings_with_the_handbrake_on(self):
        self.assertEqual(self.tracker.capture_sighting().warnings, ())

    def assert_handbrake_warning(self, expected):
        warnings = self.tracker.capture_sighting().warnings
        self.assertEqual(any("handbrake" in w for w in warnings), expected, warnings)

    def test_no_warning_after_a_tap(self):
        """The handbrake is only applied long enough to stop: released, with nothing moved since, is fine."""
        self.tracker.update(status(flags=SRV_STOPPED, second=2))
        self.assert_handbrake_warning(False)

    def test_settling_on_release_is_fine(self):
        lat, lon = offset(2, 90)
        self.tracker.update(status(flags=SRV_STOPPED, lat=lat, lon=lon, heading=3, second=2))
        self.assert_handbrake_warning(False)

    def test_warns_after_turning_since_the_handbrake(self):
        self.tracker.update(status(flags=SRV_STOPPED, heading=4, second=2))
        self.assert_handbrake_warning(True)

    def test_warns_after_moving_since_the_handbrake(self):
        lat, lon = offset(3, 90)
        self.tracker.update(status(flags=SRV_STOPPED, lat=lat, lon=lon, second=2))
        self.assert_handbrake_warning(True)

    def test_stopping_again_clears_the_warning(self):
        self.tracker.update(status(flags=SRV_STOPPED, heading=90, second=2))
        self.assert_handbrake_warning(True)
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE, heading=90, second=3))
        self.tracker.update(status(flags=SRV_STOPPED, heading=90, second=4))
        self.assert_handbrake_warning(False)

    def test_warns_when_the_handbrake_hasnt_been_used_this_deployment(self):
        self.tracker.update(status(flags=FLYING, second=2))
        self.tracker.update(status(flags=SRV_STOPPED, second=3))
        self.assert_handbrake_warning(True)

    def test_warns_in_turret_view(self):
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE | TURRET_VIEW, second=2))
        warnings = self.tracker.capture_sighting().warnings
        self.assertEqual(len(warnings), 1)
        self.assertIn("Turret view", warnings[0])

    def test_no_survey_no_sighting(self):
        self.tracker.survey = None
        self.assertIsNone(self.tracker.capture_sighting())
        self.assertIn("no active survey", self.messages[-1])

    def test_no_heading_no_sighting(self):
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE, heading=None, second=2))
        self.assertIsNone(self.tracker.capture_sighting())
        self.assertIn("no position or heading", self.messages[-1])

    def test_estimate_uses_the_great_circle(self):
        result = self.mark(km=2.37, heading=75)
        deposit = result.deposit
        self.assertEqual(result.outcome, SAVED)
        self.assertEqual(deposit.kind, SIGHTED)
        self.assertAlmostEqual(geo.distance(*CENTER, deposit.lat, deposit.lon, PLANET_RADIUS), 2370, delta=0.01)
        self.assertAlmostEqual(geo.bearing(*CENTER, deposit.lat, deposit.lon), 75, delta=0.01)
        self.assertEqual(deposit.range_km, 2.37)
        self.assertIsNone(deposit.size)
        self.assertFalse(deposit.measured)


class DuplicateTests(TrackerCase):
    def setUp(self):
        super().setUp()
        self.first = self.mark().deposit

    def again(self, meters_apart, ore="Platinum"):
        """A new sighting this far beyond the first deposit, along the same line."""
        return self.mark(ore=ore, km=PLATINUM_RANGE + meters_apart / 1000)

    def test_within_150_m_not_saved(self):
        result = self.again(149)
        self.assertEqual(result.outcome, DUPLICATE)
        self.assertEqual(len(self.survey.deposits), 1)
        self.assertIn("duplicate of D1 Platinum", result.message)
        self.assertNotIn("suspected", result.message)
        self.assertEqual(self.messages[-1], result.message)

    def test_between_150_and_400_m_warned_and_not_saved(self):
        for meters in (151, 399):
            result = self.again(meters)
            self.assertEqual(result.outcome, SUSPECTED, meters)
            self.assertIn("suspected duplicate of D1 Platinum", result.message)
            self.assertIn("Manual Mark Deposit", result.message)
        self.assertEqual(len(self.survey.deposits), 1)

    def test_beyond_400_m_saved(self):
        self.assertEqual(self.again(401).outcome, SAVED)
        self.assertEqual(len(self.survey.deposits), 2)

    def test_other_ore_at_the_same_spot_saved(self):
        self.assertEqual(self.again(0, ore="Water").outcome, SAVED)

    def test_nothing_is_averaged(self):
        position = (self.first.lat, self.first.lon)
        self.again(100)
        self.again(300)
        self.assertEqual((self.first.lat, self.first.lon), position)

    def test_measured_position_is_used_for_the_check(self):
        far = offset(10_000, 0)
        self.tracker.update_deposit(self.first.id, None, *far)
        self.assertEqual(self.again(0).outcome, SAVED)  # the old estimate no longer counts

    def test_no_survey(self):
        self.tracker.survey = None
        self.assertEqual(self.mark().outcome, NO_SURVEY)

    def test_manual_entry_skips_the_check(self):
        deposit = self.tracker.add_manual_deposit("Platinum", self.first.lat, self.first.lon)
        self.assertEqual(deposit.kind, TYPED)
        self.assertEqual(len(self.survey.deposits), 2)
        self.assertIsNone(deposit.heading)


class SavingTests(TrackerCase):
    def test_deposit_survives_a_reload(self):
        deposit = self.mark().deposit
        self.tracker.update_deposit(deposit.id, 4, 1.5, 2.5)
        loaded = self.reload()
        self.assertEqual(loaded.deposits, self.survey.deposits)
        self.assertEqual(loaded.deposits[0].size, 4)
        self.assertEqual((deposit.lat, deposit.lon), (loaded.deposits[0].lat, loaded.deposits[0].lon))

    def test_old_survey_files_still_load(self):
        data = json.loads(self.store.active_path.read_text(encoding="utf-8"))
        self.assertEqual(data["deposits"], [])  # what older files have
        self.assertEqual(Survey.from_dict(data).deposits, [])
        del data["deposits"]
        self.assertEqual(Survey.from_dict(data).deposits, [])

    def test_archived_with_the_survey(self):
        self.mark()
        self.tracker.close_survey()
        path = next(self.store.archive_dir.glob("*.json"))
        self.assertEqual(SurveyStore._read(path).deposits[0].ore, "Platinum")

    def test_update_keeps_the_estimate(self):
        deposit = self.mark().deposit
        estimate = deposit.lat, deposit.lon
        self.tracker.update_deposit(deposit.id, 3, 1.5, 2.5)
        self.assertEqual((deposit.lat, deposit.lon), estimate)
        self.assertEqual(deposit.position, (1.5, 2.5))
        self.tracker.update_deposit(deposit.id, None, None, None)
        self.assertEqual(deposit.position, estimate)
        self.assertIsNone(deposit.size)


class TargetTests(TrackerCase):
    def setUp(self):
        super().setUp()
        self.deposit = self.mark().deposit

    def board(self, second=10):
        self.tracker.update(status(flags=SHIP_BOARDED, second=second))

    def test_guidance_goes_to_the_deposit(self):
        self.tracker.fly_to_deposit(self.deposit.id)
        self.assertTrue(self.survey.target_is_deposit())
        self.assertEqual(self.survey.waypoint(self.deposit.label).kind, DEPOSIT)
        self.tracker.update(status(second=3))
        guidance = self.tracker.guidance()
        self.assertEqual(guidance.label, "D1 Platinum")
        self.assertAlmostEqual(guidance.distance, 3000, delta=0.1)
        self.assertAlmostEqual(guidance.bearing, 0, delta=0.01)

    def test_guidance_uses_the_measured_position(self):
        lat, lon = offset(1000, 0)
        self.tracker.update_deposit(self.deposit.id, None, lat, lon)
        self.tracker.fly_to_deposit(self.deposit.id)
        self.tracker.update(status(second=3))
        self.assertAlmostEqual(self.tracker.guidance().distance, 1000, delta=0.1)

    def test_target_survives_a_reload(self):
        self.tracker.fly_to_deposit(self.deposit.id)
        loaded = self.reload()
        self.assertTrue(loaded.target_is_deposit())

    def test_boarding_from_a_survey_point_advances(self):
        self.survey.target = "I1"
        self.board()
        self.assertFalse(self.survey.target_is_deposit())

    def test_boarding_with_a_deposit_target_leaves_it_selected(self):
        self.tracker.fly_to_deposit(self.deposit.id)
        self.board()
        self.assertEqual(self.survey.target, self.deposit.label)
        self.assertIn("stays on deposit", self.messages[-1])

    def _deploy_at_deposit(self, flags=SRV_STOPPED):
        self.board(second=10)
        self.tracker.fly_to_deposit(self.deposit.id)
        self.tracker.update(status(lat=self.deposit.lat, lon=self.deposit.lon, second=20))
        self.tracker.update(status(flags=flags, lat=self.deposit.lat, lon=self.deposit.lon, second=21))

    def test_inspecting_a_deposit_logs_no_no_ping_entry(self):
        self._deploy_at_deposit()
        before = list(self.survey.pings)
        self.board(second=40)
        self.assertEqual(self.survey.pings, before)

    def test_ping_near_a_deposit_doesnt_complete_it(self):
        self._deploy_at_deposit()
        self.tracker.update(status(flags=SRV_STOPPED | HARDPOINTS_ON, lat=self.deposit.lat, lon=self.deposit.lon, second=25))
        ping = self.survey.pings[-1]
        self.assertIsNone(ping.point)
        self.assertEqual(self.survey.target, self.deposit.label)
        self.assertFalse(self.survey.is_visited(self.survey.waypoint(self.deposit.label)))

    def test_step_target_reaches_deposits_and_wraps(self):
        self.survey.target = self.survey.waypoints[-1].label
        self.tracker.step_target(1)
        self.assertEqual(self.survey.target, self.deposit.label)
        self.tracker.step_target(1)
        self.assertEqual(self.survey.target, self.survey.waypoints[0].label)

    def test_selecting_a_survey_point_again(self):
        self.tracker.fly_to_deposit(self.deposit.id)
        self.tracker.select_target("O3")
        self.assertEqual(self.survey.target, "O3")

    def test_skip_refused_for_a_deposit(self):
        self.tracker.fly_to_deposit(self.deposit.id)
        self.tracker.toggle_skip(self.survey.target)
        self.assertEqual(self.survey.target, self.deposit.label)
        self.assertIn("survey points", self.messages[-1])

    def test_assign_to_target_refused_for_a_deposit(self):
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE | HARDPOINTS_ON, second=3))
        ping = self.survey.pings[-1]
        point = ping.point
        self.tracker.fly_to_deposit(self.deposit.id)
        self.tracker.assign_to_target(ping.id)
        self.assertEqual(ping.point, point)
        self.assertEqual(self.survey.target, self.deposit.label)

    def test_deleting_the_target_moves_to_a_survey_point(self):
        self.tracker.fly_to_deposit(self.deposit.id)
        self.tracker.delete_deposit(self.deposit.id)
        self.assertEqual(self.survey.deposits, [])
        self.assertFalse(self.survey.target_is_deposit())
        self.assertIsNotNone(self.survey.target)

    def test_deploy_cue_at_a_deposit(self):
        self.tracker.fly_to_deposit(self.deposit.id)
        lat, lon = geo.destination(self.deposit.lat, self.deposit.lon, 180, 400, PLANET_RADIUS)
        self.tracker.update(status(lat=lat, lon=lon, altitude=20, second=3))
        self.assertEqual(self.tracker.guidance().cue, READY)
        self.assertEqual(build_view(self.tracker).cue, DEPLOY_READY)

    def test_no_miss_at_a_deposit(self):
        """An off-target ping completes nothing at a deposit, so nothing says it will count."""
        self.tracker.fly_to_deposit(self.deposit.id)
        lat, lon = geo.destination(self.deposit.lat, self.deposit.lon, 180, 800, PLANET_RADIUS)
        self.tracker.update(status(lat=lat, lon=lon, altitude=20, second=3))
        guidance = self.tracker.guidance()
        self.assertEqual(guidance.cue, NOT_READY)
        self.assertNotIn("will count", guidance.cue_text)
        self.assertNotEqual(build_view(self.tracker).cue, MISS)

    def test_overlay_title_fits(self):
        self.assertEqual(WIDEST_TITLE, "D99 Low Temp. Diamonds  99.99 km")
        width = QFontMetricsF(overlay_font(26)).horizontalAdvance(WIDEST_TITLE)
        self.assertLessEqual(width, box_size(26)[0])

    def test_methanol_shortened_on_the_overlay(self):
        deposit = self.tracker.add_manual_deposit("Methanol Monohydrate Crystals", *CENTER)
        self.tracker.fly_to_deposit(deposit.id)
        self.assertTrue(build_view(self.tracker).title.startswith(f"D{deposit.id} Methanol  "))
        self.assertEqual(self.survey.deposit(deposit.id).label, f"D{deposit.id} Methanol Monohydrate Crystals")
        self.assertEqual(overlay_label("D5 Magnesite"), "D5 Magnesite")


class SettingsCase(TrackerCase):
    def setUp(self):
        super().setUp()
        self.ini = str(Path(self.folder.name) / "settings.ini")
        self.settings = QSettings(self.ini, QSettings.Format.IniFormat)


class TargetOresTests(SettingsCase):
    def test_nothing_ticked_at_first(self):
        self.assertEqual(target_ores(self.settings), [])
        self.assertEqual(ore_order(self.settings), ALPHABETICAL)

    def test_remembered_between_runs(self):
        for ores in (["Water"], ["Water", "Alexandrite"], []):
            save_target_ores(self.settings, ores, BY_PRICE)
            self.settings.sync()
            reopened = QSettings(self.ini, QSettings.Format.IniFormat)
            self.assertEqual(target_ores(reopened), sorted_ores(ores, BY_PRICE))
            self.assertEqual(ore_order(reopened), BY_PRICE)

    def test_dialog_lists_all_ores_and_keeps_ticks_when_resorted(self):
        dialog = TargetOresDialog(["Water", "Ruby"], ALPHABETICAL)
        self.assertEqual(dialog.list.count(), len(ORES))
        self.assertEqual(dialog.list.item(0).data(Qt.ItemDataRole.UserRole), "Alexandrite")
        self.assertEqual(dialog.ticked(), ["Ruby", "Water"])
        dialog.by_price.setChecked(True)
        self.assertEqual(dialog.order(), BY_PRICE)
        self.assertEqual(dialog.list.item(1).data(Qt.ItemDataRole.UserRole), "Monazite")
        self.assertEqual(dialog.ticked(), ["Ruby", "Water"])
        dialog.alphabetical.setChecked(True)
        self.assertEqual(dialog.order(), ALPHABETICAL)
        self.assertEqual(dialog.list.item(0).data(Qt.ItemDataRole.UserRole), "Alexandrite")

    def test_preselected_only_with_one_target(self):
        self.assertEqual(OreRadios(["Water"]).ore(), "Water")
        self.assertIsNone(OreRadios(["Water", "Ruby"]).ore())
        self.assertIsNone(OreRadios([]).ore())


class MarkFormTests(SettingsCase):
    def form(self, ores=("Platinum",), warnings=()):
        return MarkDepositDialog(self.tracker, self.sighting(warnings=warnings), list(ores))

    def test_saves_and_closes(self):
        form = self.form()
        form.show()
        form.range.setText("2.5")
        form.buttons.accepted.emit()
        self.assertEqual(len(self.survey.deposits), 1)
        self.assertFalse(form.isVisible())

    def test_ore_must_be_picked(self):
        form = self.form(["Platinum", "Water"])
        form.range.setText("2.5")
        form.save()
        self.assertEqual(self.survey.deposits, [])
        self.assertIn("Pick an ore", form.message.text())

    def test_range_above_20_stays_open(self):
        form = self.form()
        form.show()
        form.range.setText("21")
        form.save()
        self.assertEqual(self.survey.deposits, [])
        self.assertIn("more than 20 km", form.message.text())
        self.assertTrue(form.isVisible())

    def test_duplicate_is_shown_and_form_stays_open(self):
        self.mark()
        form = self.form()
        form.show()
        form.range.setText(str(PLATINUM_RANGE))
        form.save()
        self.assertEqual(len(self.survey.deposits), 1)
        self.assertIn("duplicate of D1 Platinum", form.message.text())
        self.assertTrue(form.isVisible())
        form.range.setText(str(PLATINUM_RANGE + 1))  # corrected a mistyped range
        form.save()
        self.assertEqual(len(self.survey.deposits), 2)

    def test_keyboard_only_with_several_ores(self):
        """Hotkey, the ore's number, the range, Enter: no mouse."""
        form = self.form(["Platinum", "Ruby", "Water"])
        form.show()
        QTest.keyClick(form.radios.group.buttons()[0], Qt.Key.Key_2)
        self.assertEqual(form.radios.ore(), "Ruby")
        self.assertIs(form.focusWidget(), form.range)
        QTest.keyClicks(form.range, "2.5")
        QTest.keyClick(form.range, Qt.Key.Key_Return)
        self.assertEqual([d.ore for d in self.survey.deposits], ["Ruby"])
        self.assertFalse(form.isVisible())

    def test_keyboard_only_with_density(self):
        """Hotkey, the ore's number, H, M or L for the density, the range, Enter."""
        form = self.form(["Platinum", "Ruby"])
        form.show()
        QTest.keyClick(form.radios.group.buttons()[0], Qt.Key.Key_1)
        QTest.keyClicks(form.range, "h2.5")  # the letter picks the density; it isn't typed into the range
        self.assertEqual(form.range.text(), "2.5")
        self.assertEqual(form.density.density(), "High")
        QTest.keyClick(form.range, Qt.Key.Key_L)
        QTest.keyClick(form.range, Qt.Key.Key_Return)
        deposit = self.survey.deposits[0]
        self.assertEqual((deposit.ore, deposit.density), ("Platinum", "Low"))
        self.assertIn("density Low", self.messages[-1])

    def test_density_is_optional(self):
        form = self.form()
        self.assertIsNone(form.density.density())  # "Not recorded" to start with
        form.range.setText("2.5")
        form.save()
        self.assertIsNone(self.survey.deposits[0].density)

    def test_number_without_an_ore_does_nothing(self):
        form = self.form(["Platinum", "Ruby"])
        QTest.keyClick(form.radios.group.buttons()[0], Qt.Key.Key_5)
        self.assertIsNone(form.radios.ore())

    def test_warnings_are_shown(self):
        form = self.form(warnings=("Handbrake is off",))
        texts = [form.fields.itemAt(i).widget().text() for i in range(form.fields.count())
                 if form.fields.itemAt(i).widget() and hasattr(form.fields.itemAt(i).widget(), "text")]
        self.assertIn("Handbrake is off", texts)


class ManualFormTests(SettingsCase):
    def test_saves_a_typed_position(self):
        form = ManualEntryDialog(self.tracker, ["Platinum"])
        form.lat.setText("1.5")
        form.lon.setText("-2.5")
        form.save()
        deposit = self.survey.deposits[0]
        self.assertEqual((deposit.ore, deposit.lat, deposit.lon, deposit.kind), ("Platinum", 1.5, -2.5, TYPED))

    def test_bad_position_rejected(self):
        form = ManualEntryDialog(self.tracker, ["Platinum"])
        form.lat.setText("95")
        form.lon.setText("0")
        form.save()
        self.assertEqual(self.survey.deposits, [])
        self.assertIn("Latitude", form.message.text())

    def test_density(self):
        form = ManualEntryDialog(self.tracker, ["Platinum"])
        QTest.keyClick(form.lat, Qt.Key.Key_M)
        form.lat.setText("1.5")
        form.lon.setText("-2.5")
        form.save()
        self.assertEqual(self.survey.deposits[0].density, "Medium")

    def test_number_picks_the_ore_and_moves_to_latitude(self):
        form = ManualEntryDialog(self.tracker, ["Platinum", "Water"])
        form.show()
        QTest.keyClick(form.radios.group.buttons()[0], Qt.Key.Key_2)
        self.assertEqual(form.radios.ore(), "Water")
        self.assertIs(form.focusWidget(), form.lat)


class EditFormTests(SettingsCase):
    def setUp(self):
        super().setUp()
        self.deposit = self.mark().deposit

    def test_blank_at_first(self):
        form = EditDepositDialog(self.tracker, self.deposit)
        self.assertIsNone(form.size.currentData())
        self.assertEqual(form.lat.text(), "")

    def test_scale_is_1_to_6(self):
        form = EditDepositDialog(self.tracker, self.deposit)
        sizes = [form.size.itemData(i) for i in range(form.size.count())]
        self.assertEqual(sizes, [None, 1, 2, 3, 4, 5, 6])

    def test_saves_size_and_measured(self):
        form = EditDepositDialog(self.tracker, self.deposit)
        form.size.setCurrentIndex(form.size.findData(5))
        form.lat.setText("1.5")
        form.lon.setText("2.5")
        form.save()
        self.assertEqual((self.deposit.size, self.deposit.measured_lat, self.deposit.measured_lon), (5, 1.5, 2.5))
        reopened = EditDepositDialog(self.tracker, self.deposit)
        self.assertEqual(reopened.size.currentData(), 5)
        self.assertEqual(reopened.lat.text(), "1.5")

    def test_size_alone(self):
        form = EditDepositDialog(self.tracker, self.deposit)
        form.size.setCurrentIndex(form.size.findData(2))
        form.save()
        self.assertEqual(self.deposit.size, 2)
        self.assertFalse(self.deposit.measured)

    def test_density_can_be_changed_and_is_kept_otherwise(self):
        self.tracker.update_deposit(self.deposit.id, 3, None, None, "High")
        form = EditDepositDialog(self.tracker, self.deposit)
        self.assertEqual(form.density.currentData(), "High")
        form.density.setCurrentIndex(form.density.findData("Medium"))
        form.save()
        self.assertEqual(self.deposit.density, "Medium")
        self.tracker.update_deposit(self.deposit.id, 4, None, None)  # no density given: left as it is
        self.assertEqual(self.deposit.density, "Medium")
        self.assertEqual(self.reload().deposits[0].density, "Medium")

    def test_half_a_position_rejected(self):
        form = EditDepositDialog(self.tracker, self.deposit)
        form.lat.setText("1.5")
        form.save()
        self.assertIsNone(self.deposit.measured_lat)
        self.assertTrue(form.message.text())


class PanelTests(SettingsCase):
    def setUp(self):
        super().setUp()
        self.panel = SurveyPanel(self.tracker, self.settings)

    def cell(self, row, column):
        return self.panel.deposits.item(row, column).text()

    def select_row(self, row):
        self.panel.deposits.selectRow(row)

    def test_list_shows_a_deposit(self):
        self.mark()
        self.assertEqual(self.panel.deposits.rowCount(), 1)
        self.assertEqual(self.cell(0, 0), "D1 Platinum")
        self.assertEqual(self.cell(0, 1), "—")
        self.assertEqual(self.cell(0, 2), "—")  # density not recorded
        self.assertEqual(self.cell(0, 4), "—")
        self.assertEqual(self.cell(0, 5), "3.00 km")
        self.assertEqual(self.cell(0, 6), "000°")

    def test_estimate_and_measured_shown_side_by_side(self):
        deposit = self.mark().deposit
        self.tracker.update_deposit(deposit.id, 3, 1.5, 2.5)
        self.assertEqual(self.cell(0, 1), "3")
        self.assertEqual(self.cell(0, 3), f"{deposit.lat:.4f}, {deposit.lon:.4f}")
        self.assertEqual(self.cell(0, 4), "1.5000, 2.5000")

    def test_typed_is_marked(self):
        self.tracker.add_manual_deposit("Water", 1.5, 2.5)
        self.assertEqual(self.cell(0, 3), "1.5000, 2.5000 (typed)")

    def test_selection_survives_a_refresh(self):
        self.mark()
        self.mark(ore="Water")
        self.select_row(1)
        self.panel.refresh()
        self.assertEqual(self.panel._selected_deposit().ore, "Water")

    def test_fly_to_selected(self):
        self.mark()
        self.select_row(0)
        self.panel._fly_to_deposit()
        self.assertEqual(self.survey.target, "D1 Platinum")

    def test_target_ores_label(self):
        save_target_ores(self.settings, ["Water", "Ruby"], ALPHABETICAL)
        self.panel._show_target_ores()
        self.assertEqual(self.panel.target_ores_label.text(), "Target ores: Ruby, Water")

    def test_hotkey_action_opens_one_form_and_captures_once(self):
        save_target_ores(self.settings, ["Platinum"], ALPHABETICAL)
        self.panel.show()
        self.panel.mark_deposit()
        form = self.panel.mark_form
        self.assertTrue(form.isVisible())
        self.assertEqual(form.sighting.heading, 0)
        self.assertEqual(form.radios.ore(), "Platinum")
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE, heading=90, second=5))
        self.panel.mark_deposit()  # a second press doesn't retake it
        self.assertIs(self.panel.mark_form, form)
        self.assertEqual(form.sighting.heading, 0)
        form.reject()
        self.assertIsNone(self.panel.mark_form)
        self.panel.mark_deposit()  # after closing, it takes a new one
        self.assertEqual(self.panel.mark_form.sighting.heading, 90)
        self.panel.mark_form.reject()

    def test_hotkey_action_without_a_survey(self):
        self.tracker.close_survey()
        self.panel.mark_deposit()
        self.assertIsNone(self.panel.mark_form)
        self.assertEqual(self.panel.deposits.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
