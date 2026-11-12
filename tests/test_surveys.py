"""Desk tests for Milestone 6: closed surveys, re-opening, the Surveys list, editing archived deposits, versions."""

import json
import os
import re
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QPushButton

from assist import __version__
from assist.deposit_forms import EditDepositDialog, ManualEntryDialog
from assist.survey import (CLOSED, FORMAT_VERSION, POI, TYPED, UNFINISHED, Deposit, Survey, SurveyStore)
from assist.survey_panel import SurveyPanel
from assist.surveys_dialog import ALL, THIS_BODY, THIS_SYSTEM, SurveysDialog, has_deposit, local_date
from assist.tracker import SurveyTracker
from tests.test_guidance import CENTER, FLYING, LAT_LONG, PATTERN, PLANET_RADIUS, SHIELDS, SRV, status

app = QApplication.instance() or QApplication([])

SRV_STOPPED = SHIELDS | LAT_LONG | SRV


def archived_survey(store, created, body="Elsewhere 1", state=CLOSED, deposits=1, name="Mining Signal (3)"):
    """Put a survey straight into the archive, as if it had been surveyed and left earlier."""
    poi = POI(system="Sol", body=body, planet_radius=PLANET_RADIUS, center_lat=10.0, center_lon=20.0, name=name)
    survey = Survey.new(poi, PATTERN, created)
    survey.state = state
    for i in range(1, deposits + 1):
        survey.deposits.append(Deposit(id=i, ore="Platinum", time=created, lat=10.0 + i / 100, lon=20.0))
    store.save_archived(survey)
    return survey


class Case(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SurveyStore(self.folder.name)
        self.tracker = SurveyTracker(self.store, PATTERN)
        self.messages = []
        self.tracker.add_listener(lambda m: m and self.messages.append(m))

    def deploy_here(self, second=1):
        """Fly in and deploy the SRV at the test POI's center, then board again."""
        self.tracker.update(status(second=second))
        self.tracker.update(status(flags=SRV_STOPPED, second=second + 1))
        self.tracker.update(status(second=second + 2))

    def on_disk(self, survey_id):
        return self.store.load_archived(survey_id)


class VersionTests(unittest.TestCase):
    def test_scheme(self):
        self.assertRegex(__version__, r"^0\.\d+\.\d+$")  # 0.<milestone>.<fix> until sharing

    def test_written_in_survey_files(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SurveyStore(folder)
            survey = archived_survey(store, "2026-11-08T07:00:00Z")
            data = json.loads(store.archive_path(survey.id).read_text(encoding="utf-8"))
        self.assertEqual(data["version"], FORMAT_VERSION)
        self.assertEqual(FORMAT_VERSION, 4)
        self.assertEqual(data["assist_version"], __version__)


class ClosedStateTests(Case):
    def test_old_finished_files_load_as_closed(self):
        survey = archived_survey(self.store, "2026-11-01T07:00:00Z")
        path = self.store.archive_path(survey.id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(state="finished", version=1)
        del data["assist_version"]
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.on_disk(survey.id).state, CLOSED)
        self.assertEqual(self.store.unfinished(), [])  # still never resumed
        # Not rewritten just by being read.
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["state"], "finished")

    def test_close_survey_writes_closed(self):
        self.deploy_here()
        survey_id = self.tracker.survey.id
        self.tracker.close_survey()
        self.assertEqual(self.on_disk(survey_id).state, CLOSED)
        self.assertIn("archived as closed", self.messages[-1])

    def test_closed_survey_is_not_resumed(self):
        self.deploy_here()
        first = self.tracker.survey.id
        self.tracker.close_survey()
        self.deploy_here(second=10)
        self.assertNotEqual(self.tracker.survey.id, first)


class ReopenTests(Case):
    def setUp(self):
        super().setUp()
        self.deploy_here()
        self.survey_id = self.tracker.survey.id
        self.pings = len(self.tracker.survey.pings)
        self.tracker.close_survey()

    def test_reopen_makes_it_unfinished_and_returning_resumes_it(self):
        self.tracker.reopen(self.survey_id)
        self.assertEqual(self.on_disk(self.survey_id).state, UNFINISHED)
        self.assertIsNone(self.tracker.survey)  # nothing becomes active until the player returns
        self.deploy_here(second=10)
        self.assertEqual(self.tracker.survey.id, self.survey_id)
        self.assertIn("Resumed survey", " ".join(self.messages))

    def test_reopen_leaves_the_active_survey_alone(self):
        archived_survey(self.store, "2026-11-01T07:00:00Z")
        self.deploy_here(second=10)
        active = self.tracker.survey.id
        self.tracker.reopen(self.survey_id)
        self.assertEqual(self.tracker.survey.id, active)

    def test_close_from_the_list(self):
        self.tracker.reopen(self.survey_id)
        self.tracker.close_archived(self.survey_id)
        self.assertEqual(self.on_disk(self.survey_id).state, CLOSED)
        self.deploy_here(second=10)
        self.assertNotEqual(self.tracker.survey.id, self.survey_id)

    def test_reopening_an_unfinished_survey_changes_nothing(self):
        self.tracker.reopen(self.survey_id)
        count = len(self.messages)
        self.tracker.reopen(self.survey_id)
        self.assertEqual(len(self.messages), count)


class ArchivedEditTests(Case):
    """Deposits of an archived survey, edited from anywhere: here the player is on another body."""

    def setUp(self):
        super().setUp()
        self.survey = archived_survey(self.store, "2026-11-02T07:00:00Z", deposits=2)
        self.survey.target = "D2 Platinum"
        self.store.save_archived(self.survey)
        self.editor = self.tracker.editor(self.survey.id)

    def test_size_and_measured_position(self):
        form = EditDepositDialog(self.editor, self.survey.deposit(1))
        form.size.setCurrentIndex(form.size.findData(4))
        form.lat.setText("10.5")
        form.lon.setText("20.5")
        form.save()
        deposit = self.on_disk(self.survey.id).deposit(1)
        self.assertEqual((deposit.size, deposit.measured_lat, deposit.measured_lon), (4, 10.5, 20.5))
        self.assertEqual(deposit.lat, 10.01)  # the estimate stays
        self.assertIsNone(self.tracker.survey)  # nothing was made active
        self.assertIn("Archived survey Mining Signal (3): Deposit D1 Platinum: size 4", self.messages[-1])

    def test_closed_stays_closed(self):
        self.editor.update_deposit(1, 3, None, None)
        self.assertEqual(self.on_disk(self.survey.id).state, CLOSED)

    def test_manual_entry(self):
        form = ManualEntryDialog(self.editor, ["Water"])
        form.lat.setText("10.2")
        form.lon.setText("20.2")
        form.save()
        deposit = self.on_disk(self.survey.id).deposit(3)
        self.assertEqual((deposit.ore, deposit.kind, deposit.lat), ("Water", TYPED, 10.2))

    def test_delete_clears_a_target_on_it(self):
        self.editor.delete_deposit(2)
        survey = self.on_disk(self.survey.id)
        self.assertEqual([d.id for d in survey.deposits], [1])
        self.assertIsNone(survey.target)

    def test_change_on_disk_since_reading_is_kept(self):
        stale = self.store.load_archived(self.survey.id)  # what the list drew
        newer = self.store.load_archived(self.survey.id)
        newer.deposits[1].size = 6  # e.g. resumed, visited and left again since
        self.store.save_archived(newer)
        self.editor.update_deposit(stale.deposits[0].id, 2, None, None)
        survey = self.on_disk(self.survey.id)
        self.assertEqual([d.size for d in survey.deposits], [2, 6])

    def test_resumed_survey_is_refused(self):
        self.store.take_from_archive(self.store.load_archived(self.survey.id))
        self.editor.update_deposit(1, 2, None, None)
        self.assertIsNone(self.on_disk(self.survey.id))  # no archive copy recreated next to the active one
        self.assertIsNone(self.store.load_active().deposit(1).size)
        self.assertIn("isn't in the archive", self.messages[-1])


class SurveysDialogTests(Case):
    def setUp(self):
        super().setUp()
        self.settings = QSettings(os.path.join(self.folder.name, "settings.ini"), QSettings.Format.IniFormat)
        self.old = archived_survey(self.store, "2026-11-01T07:00:00Z", body="Old 1")
        self.new = archived_survey(self.store, "2026-11-05T07:00:00Z", state=UNFINISHED, deposits=3)
        self.deploy_here()  # an active survey on "Test", which isn't listed
        self.dialog = SurveysDialog(self.tracker, self.settings)
        self.addCleanup(self.dialog.reject)

    def ids(self):
        return [self.dialog.table.item(row, 0).data(0x0100) for row in range(self.dialog.table.rowCount())]

    def select(self, survey):
        self.dialog.table.selectRow(self.ids().index(survey.id))

    def test_lists_archived_newest_first_without_the_active_one(self):
        self.assertEqual(self.ids(), [self.new.id, self.old.id])
        row = [self.dialog.table.item(0, c).text() for c in range(self.dialog.table.columnCount())]
        self.assertEqual(row[0], local_date("2026-11-05T07:00:00Z"))
        self.assertEqual(row[1:5], ["Sol", "Elsewhere 1", "Mining Signal (3)", "unfinished"])
        self.assertEqual(row[6], "3, 0 sized")

    def show(self, value):
        self.dialog.show_filter.setCurrentIndex(self.dialog.show_filter.findData(value))

    def test_this_body_only(self):
        archived_survey(self.store, "2026-11-03T07:00:00Z", body="Test")
        self.show(THIS_BODY)
        self.assertEqual(len(self.ids()), 1)
        self.assertEqual(self.dialog.selected_survey(), None)
        self.assertEqual(self.dialog.surveys[self.ids()[0]].poi.body, "Test")
        self.show(ALL)
        self.assertEqual(len(self.ids()), 3)

    def test_this_system_only(self):
        other = archived_survey(self.store, "2026-11-03T07:00:00Z")
        other.poi.system = "Achenar"
        self.store.save_archived(other)
        self.tracker.set_system("Sol")
        self.show(THIS_SYSTEM)
        self.assertEqual(sorted(self.ids()), sorted([self.new.id, self.old.id]))
        self.tracker.set_system(None)  # no system from the journal: nothing matches
        self.dialog.refresh()
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.dialog.message.text(), "No archived surveys match")

    def test_ore_and_min_size(self):
        survey = self.store.load_archived(self.new.id)
        survey.deposits[0].size = 4
        survey.deposits[1].ore, survey.deposits[1].size = "Water", 6
        self.store.save_archived(survey)
        dialog = self.dialog
        dialog.ore_filter.setCurrentIndex(dialog.ore_filter.findData("Platinum"))
        self.assertEqual(sorted(self.ids()), sorted([self.new.id, self.old.id]))  # unsized still match "Any size"
        dialog.size_filter.setCurrentIndex(dialog.size_filter.findData(4))
        self.assertEqual(self.ids(), [self.new.id])
        dialog.size_filter.setCurrentIndex(dialog.size_filter.findData(5))
        self.assertEqual(self.ids(), [])  # the Platinum is 4, the size 6 is Water
        dialog.ore_filter.setCurrentIndex(0)  # any ore
        self.assertEqual(self.ids(), [self.new.id])
        self.assertEqual(dialog.ore_filter.count(), 38)  # "Any ore" and all 37

    def test_has_deposit(self):
        survey = self.store.load_archived(self.old.id)  # one unsized Platinum
        self.assertTrue(has_deposit(survey, "Platinum"))
        self.assertFalse(has_deposit(survey, "Platinum", 1))
        self.assertFalse(has_deposit(survey, "Water"))

    def test_selecting_shows_its_deposits(self):
        self.select(self.new)
        self.assertEqual(self.dialog.deposits.rowCount(), 3)
        self.select(self.old)
        self.assertEqual(self.dialog.deposits.rowCount(), 1)

    def test_reopen_button_refreshes_and_keeps_the_selection(self):
        self.select(self.old)
        self.dialog._reopen()
        self.assertEqual(self.on_disk(self.old.id).state, UNFINISHED)
        self.assertEqual(self.dialog.selected_id(), self.old.id)
        self.assertEqual(self.dialog.table.item(self.ids().index(self.old.id), 4).text(), "unfinished")

    def test_reopen_refuses_an_unfinished_one(self):
        self.select(self.new)
        self.dialog._reopen()
        self.assertIn("Only a closed survey", self.dialog.message.text())

    def test_edit_refreshes_the_deposit_list(self):
        self.select(self.new)
        self.tracker.editor(self.new.id).update_deposit(2, 5, None, None)
        self.assertEqual(self.dialog.deposits.item(1, 1).text(), "5")
        self.assertEqual(self.dialog.table.item(self.ids().index(self.new.id), 6).text(), "3, 1 sized")

    def test_stops_listening_when_closed(self):
        listeners = len(self.tracker._listeners)
        self.dialog.reject()
        self.assertEqual(len(self.tracker._listeners), listeners - 1)


class PanelButtonTests(Case):
    def test_close_and_surveys_buttons(self):
        settings = QSettings(os.path.join(self.folder.name, "settings.ini"), QSettings.Format.IniFormat)
        panel = SurveyPanel(self.tracker, settings)
        texts = {b.text() for b in panel.findChildren(QPushButton)}
        self.assertIn("Close survey", texts)
        self.assertIn("Surveys...", texts)
        self.assertFalse(any(re.search("finish", t, re.I) for t in texts))


if __name__ == "__main__":
    unittest.main()
