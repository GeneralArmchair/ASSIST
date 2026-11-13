"""Desk tests for Milestone 14: deposit form changes from field testing.

Manual Mark Deposit filled in with the player's position (measured if saved unchanged) and asking before adding the
same ore within 150 m; Remote Mark Deposit's km / m dropdown and wrong-unit checks; the guide; the version.
"""

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFormLayout, QLabel

from assist import __version__, survey_panel, surveys_dialog
from assist.deposit_forms import EditDepositDialog, ManualEntryDialog, MarkDepositDialog, range_unit, save_target_ores
from assist.ores import ALPHABETICAL
from assist.survey import KM, M, TYPED, doubtful_range, format_range, parse_range_km
from assist.survey_panel import SurveyPanel
from assist.surveys_dialog import SurveysDialog
from tests.test_deposits import HANDBRAKE, SRV_STOPPED, SettingsCase
from tests.test_guidance import offset, status
from tests.test_surveys import Case, archived_survey

app = QApplication.instance() or QApplication([])

ROOT = Path(__file__).resolve().parent.parent
HERE = (offset(37.0, 300))  # where the player is: full precision, not 4 decimals


def labels(form):
    """The text of every QLabel on a form."""
    return [label.text() for label in form.findChildren(QLabel)]


class RangeParsingTests(unittest.TestCase):
    def test_km_as_before(self):
        self.assertEqual(parse_range_km("2.37"), 2.37)
        self.assertEqual(parse_range_km("2,37", KM), 2.37)

    def test_m_is_converted_to_km(self):
        self.assertAlmostEqual(parse_range_km("410", M), 0.41)
        self.assertAlmostEqual(parse_range_km("41,5", M), 0.0415)

    def test_over_20_km_suggests_m(self):
        with self.assertRaisesRegex(ValueError, r"more than 20 km\. Did you mean m\?"):
            parse_range_km("410")

    def test_over_20_km_typed_in_m_is_rejected_without_the_hint(self):
        with self.assertRaises(ValueError) as caught:
            parse_range_km("20001", M)
        self.assertIn("more than 20 km", str(caught.exception))
        self.assertNotIn("Did you mean", str(caught.exception))
        self.assertEqual(parse_range_km("20000", M), 20)

    def test_bad_text_names_the_unit(self):
        for unit, example in ((KM, "such as 2.37"), (M, "such as 410")):
            for text in ("", "abc", "0", "-3"):
                with self.assertRaisesRegex(ValueError, f"Type the range in {unit}, {example}"):
                    parse_range_km(text, unit)

    def test_doubtful_only_under_10_in_m(self):
        self.assertTrue(doubtful_range("9.9", M))
        self.assertTrue(doubtful_range("1,5", M))
        self.assertFalse(doubtful_range("10", M))
        self.assertFalse(doubtful_range("1.5", KM))
        self.assertFalse(doubtful_range("abc", M))

    def test_log_uses_the_typed_unit(self):
        self.assertEqual(format_range(0.41, M), "410 m")
        self.assertEqual(format_range(0.0415, M), "41.5 m")
        self.assertEqual(format_range(1.5), "1.50 km")


class RemoteUnitTests(SettingsCase):
    def form(self, ores=("Platinum",), settings=True):
        form = MarkDepositDialog(self.tracker, self.sighting(), list(ores), settings=self.settings if settings else None)
        self.addCleanup(form.deleteLater)
        return form

    def range_row(self, form):
        layout = form.findChild(QFormLayout)
        return layout.itemAt(0, QFormLayout.ItemRole.LabelRole).widget().text(), \
            layout.itemAt(0, QFormLayout.ItemRole.FieldRole).layout()

    def test_label_and_dropdown_after_the_box(self):
        form = self.form()
        label, row = self.range_row(form)
        self.assertEqual(label, "Range:")
        self.assertNotIn("Range (km):", labels(form))
        self.assertIs(row.itemAt(0).widget(), form.range)
        self.assertIs(row.itemAt(1).widget(), form.unit)
        self.assertEqual([form.unit.itemText(i) for i in range(form.unit.count())], ["km", "m"])

    def test_km_by_default(self):
        self.assertEqual(self.form().unit.currentText(), "km")
        self.assertEqual(self.form(settings=False).unit.currentText(), "km")
        self.assertEqual(range_unit(self.settings), KM)
        self.assertEqual(range_unit(None), KM)

    def test_choice_remembered_between_runs(self):
        form = self.form()
        form.unit.setCurrentText("m")
        self.settings.sync()
        reopened = QSettings(self.ini, QSettings.Format.IniFormat)
        self.assertEqual(range_unit(reopened), M)
        self.assertEqual(self.form().unit.currentText(), "m")
        self.form().unit.setCurrentText("km")
        self.assertEqual(range_unit(self.settings), KM)

    def test_opening_the_form_writes_nothing(self):
        self.form()
        self.assertFalse(self.settings.contains("range_unit"))

    def test_odd_setting_falls_back_to_km(self):
        self.settings.setValue("range_unit", "miles")
        self.assertEqual(self.form().unit.currentText(), "km")

    def test_placeholder_follows_the_unit(self):
        form = self.form()
        self.assertEqual(form.range.placeholderText(), "up to 20")
        form.unit.setCurrentText("m")
        self.assertEqual(form.range.placeholderText(), "up to 20000")

    def test_numpad_flow_unchanged(self):
        """Hotkey, the ore's number, H/M/L, the range, Enter: saved before the dropdown is reached."""
        form = self.form(["Platinum", "Ruby", "Water"])
        form.show()
        QTest.keyClick(form.radios.group.buttons()[0], Qt.Key.Key_2)
        self.assertIs(form.focusWidget(), form.range)
        QTest.keyClicks(form.range, "h2.5")
        QTest.keyClick(form.range, Qt.Key.Key_Return)
        deposit = self.survey.deposits[0]
        self.assertEqual((deposit.ore, deposit.density, deposit.range_km), ("Ruby", "High", 2.5))
        self.assertFalse(form.isVisible())
        self.assertEqual(form.unit.currentText(), "km")  # Enter didn't touch the dropdown

    def test_numpad_flow_in_m(self):
        self.settings.setValue("range_unit", "m")
        form = self.form()
        form.show()
        self.assertIs(form.focusWidget(), form.range)
        QTest.keyClicks(form.range, "410")
        QTest.keyClick(form.range, Qt.Key.Key_Return)
        deposit = self.survey.deposits[0]
        self.assertAlmostEqual(deposit.range_km, 0.41)
        self.assertIn("410 m at 000°", self.messages[-1])

    def test_dropdown_is_after_the_box_in_the_tab_order(self):
        form = self.form()
        form.show()
        self.assertIs(form.range.nextInFocusChain(), form.unit)

    def test_under_10_m_needs_a_second_save(self):
        form = self.form()
        form.show()
        form.unit.setCurrentText("m")
        form.range.setText("4.1")
        form.save()
        self.assertEqual(self.survey.deposits, [])
        self.assertIn("did you mean km?", form.message.text())
        self.assertTrue(form.isVisible())
        form.save()
        self.assertAlmostEqual(self.survey.deposits[0].range_km, 0.0041)

    def test_changed_text_or_unit_is_checked_again(self):
        form = self.form()
        form.unit.setCurrentText("m")
        form.range.setText("4.1")
        form.save()
        form.range.setText("4.2")
        form.save()
        self.assertEqual(self.survey.deposits, [])  # asked again about 4.2
        form.unit.setCurrentText("km")
        form.save()  # 4.2 km is fine
        self.assertEqual(self.survey.deposits[0].range_km, 4.2)

    def test_over_20_km_suggests_m(self):
        form = self.form()
        form.range.setText("410")
        form.save()
        self.assertEqual(self.survey.deposits, [])
        self.assertIn("Did you mean m?", form.message.text())

    def test_panel_passes_the_settings(self):
        panel = SurveyPanel(self.tracker, self.settings)
        self.addCleanup(panel.deleteLater)
        self.settings.setValue("range_unit", "m")
        panel.mark_deposit()
        self.assertEqual(panel.mark_form.unit.currentText(), "m")
        panel.mark_form.unit.setCurrentText("km")
        self.assertEqual(range_unit(self.settings), KM)
        panel.mark_form.reject()


class ManualPositionTests(SettingsCase):
    """Manual Mark Deposit in the main window, filled in with the player's position."""

    def setUp(self):
        super().setUp()
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE, lat=HERE[0], lon=HERE[1], second=5))

    def form(self, ores=("Platinum",)):
        form = ManualEntryDialog(self.tracker, list(ores), where=self.tracker.manual_position(), check_near=True)
        self.addCleanup(form.deleteLater)
        return form

    def test_manual_position(self):
        self.assertEqual(self.tracker.manual_position(), (HERE[0], HERE[1], "SRV"))
        self.tracker.update(status(lat=HERE[0], lon=HERE[1], second=6))  # back in the ship
        self.assertEqual(self.tracker.manual_position()[2], "ship")

    def test_no_position(self):
        self.tracker.update(status(flags=SRV_STOPPED & ~(1 << 21), second=6))  # no lat/long
        self.assertIsNone(self.tracker.manual_position())
        form = self.form()
        self.assertEqual((form.lat.text(), form.lon.text()), ("", ""))
        self.assertIn("No position from the game: type the latitude and longitude.", labels(form))

    def test_filled_in_with_4_decimals_and_says_so(self):
        form = self.form()
        self.assertEqual((form.lat.text(), form.lon.text()), (f"{HERE[0]:.4f}", f"{HERE[1]:.4f}"))
        self.assertIn("Filled in with your position (SRV) when this form opened. Type over it to change it.",
                      labels(form))

    def test_saved_unchanged_is_measured_at_full_precision(self):
        self.form().save()
        deposit = self.survey.deposits[0]
        self.assertEqual(deposit.kind, TYPED)
        self.assertEqual((deposit.lat, deposit.lon), HERE)
        self.assertEqual((deposit.measured_lat, deposit.measured_lon), HERE)
        self.assertTrue(deposit.measured)
        self.assertIn("(measured: your position)", self.messages[-1])
        self.assertTrue(self.reload().deposit(1).measured)

    def test_typed_over_is_an_estimate(self):
        form = self.form()
        form.lat.setText("1.5")
        form.save()
        deposit = self.survey.deposits[0]
        self.assertEqual((deposit.lat, deposit.lon), (1.5, round(HERE[1], 4)))
        self.assertFalse(deposit.measured)
        self.assertNotIn("measured", self.messages[-1])

    def test_position_taken_once_when_the_form_opens(self):
        form = self.form()
        self.tracker.update(status(flags=SRV_STOPPED, lat=CENTER_LAT, lon=CENTER_LON, second=9))  # drove off
        form.save()
        self.assertEqual(self.survey.deposits[0].position, HERE)

    def test_panel_opens_it_filled_in(self):
        panel = SurveyPanel(self.tracker, self.settings)
        self.addCleanup(panel.deleteLater)
        opened = []
        with mock.patch.object(survey_panel.ManualEntryDialog, "exec", new=lambda form: opened.append(form)):
            panel.manual_entry()
        form = opened[0]
        self.assertEqual(form.lat.text(), f"{HERE[0]:.4f}")
        self.assertTrue(form.check_near)


CENTER_LAT, CENTER_LON = offset(0)


class ArchivedManualTests(Case):
    """The Surveys... dialog's Manual Mark Deposit stays empty and asks nothing, even with a position."""

    def setUp(self):
        super().setUp()
        self.survey = archived_survey(self.store, "2026-11-02T07:00:00Z", deposits=1)
        self.deploy_here()  # a position from the game
        self.dialog = SurveysDialog(self.tracker, QSettings(os.path.join(self.folder.name, "s.ini"),
                                                            QSettings.Format.IniFormat))
        self.addCleanup(self.dialog.deleteLater)
        self.dialog.table.selectRow(0)

    def test_empty_with_no_source_line(self):
        opened = []
        with mock.patch.object(surveys_dialog.ManualEntryDialog, "exec", new=lambda form: opened.append(form)):
            self.dialog._manual_entry()
        form = opened[0]
        self.assertEqual((form.lat.text(), form.lon.text()), ("", ""))
        self.assertIsNone(form.source)
        self.assertFalse(form.check_near)

    def test_no_question_next_to_a_deposit(self):
        form = ManualEntryDialog(self.tracker.editor(self.survey.id), ["Platinum"])
        lat, lon = self.survey.deposit(1).position
        form.lat.setText(str(lat))
        form.lon.setText(str(lon))
        with mock.patch.object(ManualEntryDialog, "ask_near") as ask:
            form.save()
        ask.assert_not_called()
        on_disk = self.store.load_archived(self.survey.id)
        self.assertEqual(len(on_disk.deposits), 2)
        self.assertFalse(on_disk.deposit(2).measured)


class NearDepositTests(SettingsCase):
    """Manual Mark Deposit with the player within 150 m of a deposit of the same ore."""

    def setUp(self):
        super().setUp()
        self.d1 = self.mark().deposit  # D1 Platinum, 3 km north, sighted
        self.lat, self.lon = self.near(80)
        self.tracker.update(status(flags=SRV_STOPPED | HANDBRAKE, lat=self.lat, lon=self.lon, second=5))

    def near(self, meters):
        from assist import geo
        from make_test_recording import PLANET_RADIUS
        return geo.destination(self.d1.lat, self.d1.lon, 90, meters, PLANET_RADIUS)

    def form(self, ores=("Platinum",)):
        form = ManualEntryDialog(self.tracker, list(ores), where=self.tracker.manual_position(), check_near=True)
        self.addCleanup(form.deleteLater)
        return form

    def save(self, form, choice):
        with mock.patch.object(ManualEntryDialog, "ask_near", return_value=choice) as ask:
            form.save()
        return ask

    def test_asks_with_the_deposit_and_distance(self):
        ask = self.save(self.form(), ManualEntryDialog.BACK)
        deposit, distance = ask.call_args.args
        self.assertIs(deposit, self.d1)
        self.assertAlmostEqual(distance, 80, delta=1)

    def test_back_keeps_the_form_open(self):
        form = self.form()
        form.show()
        self.save(form, ManualEntryDialog.BACK)
        self.assertEqual(len(self.survey.deposits), 1)
        self.assertTrue(form.isVisible())
        self.assertIsNone(form.update_instead)

    def test_add_saves_a_twin(self):
        form = self.form()
        self.save(form, ManualEntryDialog.ADD)
        self.assertEqual([d.label for d in self.survey.deposits], ["D1 Platinum", "D2 Platinum"])
        self.assertTrue(self.survey.deposits[1].measured)
        self.assertIsNone(form.update_instead)

    def test_update_closes_and_hands_over(self):
        form = self.form()
        form.show()
        QTest.keyClick(form.lat, Qt.Key.Key_M)  # density Medium
        self.save(form, ManualEntryDialog.UPDATE)
        self.assertEqual(len(self.survey.deposits), 1)
        self.assertFalse(form.isVisible())
        self.assertEqual(form.update_instead, (self.d1, self.lat, self.lon, "Medium"))
        self.assertFalse(self.d1.measured)  # nothing saved yet

    def test_typed_position_is_checked_too(self):
        form = self.form()
        lat, lon = self.near(120)
        form.lat.setText(f"{lat:.6f}")
        form.lon.setText(f"{lon:.6f}")
        ask = self.save(form, ManualEntryDialog.ADD)
        ask.assert_called_once()
        self.assertFalse(self.survey.deposits[1].measured)

    def test_no_question_past_150_m_or_for_another_ore(self):
        form = self.form()
        lat, lon = self.near(200)
        form.lat.setText(f"{lat:.6f}")
        form.lon.setText(f"{lon:.6f}")
        self.save(form, ManualEntryDialog.BACK).assert_not_called()
        self.assertEqual(len(self.survey.deposits), 2)
        self.save(self.form(["Water"]), ManualEntryDialog.BACK).assert_not_called()
        self.assertEqual(len(self.survey.deposits), 3)

    def test_measured_position_of_a_deposit_is_what_counts(self):
        self.tracker.update_deposit(self.d1.id, None, *self.near(1000))  # it was really 1 km east
        self.save(self.form(), ManualEntryDialog.BACK).assert_not_called()

    def answer(self, key):
        """The real question box, answered with a key."""
        form = self.form()
        seen = []

        def press():
            box = QApplication.activeModalWidget()
            seen.append((box.text(), [b.text() for b in box.buttons()]))
            QTest.keyClick(box, key)
        QTimer.singleShot(0, press)
        return form.ask_near(self.d1, 80.4), seen[0]

    def test_question_box_enter_updates(self):
        choice, (text, buttons) = self.answer(Qt.Key.Key_Return)
        self.assertEqual(choice, ManualEntryDialog.UPDATE)
        self.assertEqual(text, "D1 Platinum is 80 m away. Update D1 instead?")
        self.assertCountEqual(buttons, ["Update D1", "Add new deposit", "Back"])

    def test_question_box_escape_goes_back(self):
        self.assertEqual(self.answer(Qt.Key.Key_Escape)[0], ManualEntryDialog.BACK)

    def test_panel_opens_the_update_form_filled_in(self):
        save_target_ores(self.settings, ["Platinum"], ALPHABETICAL)
        panel = SurveyPanel(self.tracker, self.settings)
        self.addCleanup(panel.deleteLater)
        edits = []

        def manual_exec(form):
            QTest.keyClick(form.lat, Qt.Key.Key_H)
            with mock.patch.object(ManualEntryDialog, "ask_near", return_value=ManualEntryDialog.UPDATE):
                form.save()
        with mock.patch.object(survey_panel.ManualEntryDialog, "exec", new=manual_exec), \
                mock.patch.object(survey_panel.EditDepositDialog, "exec", new=lambda form: edits.append(form)):
            panel.manual_entry()
        edit = edits[0]
        self.assertEqual(edit.deposit_id, self.d1.id)
        self.assertEqual((edit.lat.text(), edit.lon.text()), (f"{self.lat:.4f}", f"{self.lon:.4f}"))
        self.assertEqual(edit.density.currentData(), "High")
        self.assertIn("Measured position filled in from Manual Mark Deposit. Save to keep it.", labels(edit))
        self.assertFalse(self.d1.measured)  # cancelled: nothing changed
        edit.save()
        self.assertEqual((self.d1.measured_lat, self.d1.measured_lon), (self.lat, self.lon))  # full precision
        self.assertEqual(len(self.survey.deposits), 1)


class EditFormFilledTests(SettingsCase):
    def setUp(self):
        super().setUp()
        self.deposit = self.mark().deposit

    def form(self, **filled):
        form = EditDepositDialog(self.tracker, self.deposit, **filled)
        self.addCleanup(form.deleteLater)
        return form

    def test_typed_over_measured_position_is_what_was_typed(self):
        form = self.form(measured=HERE)
        form.lat.setText("1.5")
        form.save()
        self.assertEqual((self.deposit.measured_lat, self.deposit.measured_lon), (1.5, round(HERE[1], 4)))

    def test_density_only_fills_a_blank_one(self):
        self.assertEqual(self.form(measured=HERE, density="Low").density.currentData(), "Low")
        self.tracker.update_deposit(self.deposit.id, None, None, None, "High")
        self.assertEqual(self.form(measured=HERE, density="Low").density.currentData(), "High")

    def test_without_filling_unchanged(self):
        self.tracker.update_deposit(self.deposit.id, 2, 1.25, 2.5)
        form = self.form()
        self.assertEqual((form.lat.text(), form.lon.text()), ("1.25", "2.5"))
        self.assertNotIn("Measured position filled in from Manual Mark Deposit. Save to keep it.", labels(form))


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")

    def test_wrong_claims_gone(self):
        for wrong in ("`1.51 km`", "`41 m`", "its position is averaged", "you get a warning"):
            self.assertNotIn(wrong, self.guide)

    def test_describes_the_changes(self):
        for text in ("dropdown after the box to m", "remembers the unit", "under 10 m",
                     "filled in with your position", "counts as measured", "within 150 m of a deposit of the same ore",
                     "Add new deposit", "Nothing is ever averaged"):
            self.assertIn(text, self.guide)


class VersionTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(__version__, "0.14.0")


if __name__ == "__main__":
    unittest.main()
