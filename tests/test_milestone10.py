"""Desk tests for Milestone 10: the Surveys list's System field."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QComboBox, QCompleter

from assist import __version__
from assist.survey import UNFINISHED
from assist.surveys_dialog import THIS_BODY, THIS_SYSTEM, SurveysDialog, matching_systems
from tests.test_surveys import Case, archived_survey

SECTOR = ["Tascheter Sector GW-W c1-1", "Tascheter Sector GW-W c1-17", "Tascheter Sector AB-C d2"]


class MatchingTests(unittest.TestCase):
    systems = SECTOR + ["LFT 509", "Sol", None]

    def test_part_of_a_name_any_case(self):
        self.assertEqual(matching_systems("tascheter", self.systems), set(SECTOR))
        self.assertEqual(matching_systems("gw-w", self.systems), set(SECTOR[:2]))

    def test_exact_name_wins(self):
        self.assertEqual(matching_systems("Tascheter Sector GW-W c1-1", self.systems), {SECTOR[0]})
        self.assertEqual(matching_systems("  tascheter sector gw-w C1-1 ", self.systems), {SECTOR[0]})  # pasted
        self.assertEqual(matching_systems("Tascheter Sector GW-W c1-17", self.systems), {SECTOR[1]})

    def test_empty_and_unknown(self):
        self.assertEqual(matching_systems("  ", self.systems), set(SECTOR + ["LFT 509", "Sol"]))
        self.assertEqual(matching_systems("Achenar", self.systems), set())


class SystemFieldTests(Case):
    def setUp(self):
        super().setUp()
        self.settings = QSettings(os.path.join(self.folder.name, "settings.ini"), QSettings.Format.IniFormat)
        self.surveys = {}
        for day, system in enumerate(SECTOR + ["Sol"], start=1):
            survey = archived_survey(self.store, f"2026-11-0{day}T07:00:00Z", body=f"{system} 1",
                                     state=UNFINISHED if day == 2 else "closed")
            survey.poi.system = system
            self.store.save_archived(survey)
            self.surveys[system] = survey
        self.tracker.set_system("Sol")
        self.dialog = SurveysDialog(self.tracker, self.settings)
        self.addCleanup(self.dialog.reject)

    def systems_listed(self):
        table = self.dialog.table
        return sorted(table.item(row, 1).text() for row in range(table.rowCount()))

    def type(self, text):
        self.dialog.system_filter.setEditText(text)

    def test_dropdown_offers_the_archive_systems(self):
        field = self.dialog.system_filter
        self.assertTrue(field.isEditable())
        self.assertEqual([field.itemText(i) for i in range(field.count())],
                         sorted(SECTOR + ["Sol"], key=str.casefold))
        self.assertEqual(field.currentText(), "")  # starts empty, not on the first system
        self.assertEqual(field.insertPolicy(), QComboBox.InsertPolicy.NoInsert)
        self.assertEqual(field.completer().filterMode(), Qt.MatchFlag.MatchContains)
        self.assertEqual(field.completer().completionMode(), QCompleter.CompletionMode.PopupCompletion)
        self.assertGreaterEqual(field.minimumContentsLength(), 30)
        self.assertEqual(len(self.systems_listed()), 4)

    def test_typing_filters_and_exact_name_wins(self):
        self.type("tascheter")
        self.assertEqual(self.systems_listed(), sorted(SECTOR))
        self.type("Tascheter Sector GW-W c1-1")
        self.assertEqual(self.systems_listed(), [SECTOR[0]])
        self.type("")
        self.assertEqual(len(self.systems_listed()), 4)

    def test_show_greys_out_and_is_ignored(self):
        dialog = self.dialog
        dialog.show_filter.setCurrentIndex(dialog.show_filter.findData(THIS_SYSTEM))
        self.assertEqual(self.systems_listed(), ["Sol"])
        self.type("tascheter")
        self.assertFalse(dialog.show_filter.isEnabled())
        self.assertEqual(self.systems_listed(), sorted(SECTOR))
        self.type("")
        self.assertTrue(dialog.show_filter.isEnabled())
        self.assertEqual(dialog.show_filter.currentData(), THIS_SYSTEM)  # back as it was
        self.assertEqual(self.systems_listed(), ["Sol"])
        dialog.show_filter.setCurrentIndex(dialog.show_filter.findData(THIS_BODY))
        self.type("Sol")
        self.assertEqual(self.systems_listed(), ["Sol"])

    def test_combines_with_deposit_and_size(self):
        survey = self.store.load_archived(self.surveys[SECTOR[1]].id)
        survey.deposits[0].size = 5
        self.store.save_archived(survey)
        dialog = self.dialog
        dialog.size_filter.setCurrentIndex(dialog.size_filter.findData(5))
        self.type("tascheter")
        self.assertEqual(self.systems_listed(), [SECTOR[1]])
        dialog.ore_filter.setCurrentIndex(dialog.ore_filter.findData("Water"))
        self.assertEqual(self.systems_listed(), [])
        self.assertEqual(dialog.message.text(), "No archived surveys match")

    def test_unknown_system_message(self):
        self.type("Achenar")
        self.assertEqual(self.systems_listed(), [])
        self.assertEqual(self.dialog.message.text(), 'No archived surveys in a system matching "Achenar"')

    def test_right_click_shows_only_that_system(self):
        table = self.dialog.table
        row = next(r for r in range(table.rowCount()) if table.item(r, 1).text() == SECTOR[0])
        menu = self.dialog.row_menu(table.item(row, 4))  # any cell, not just System
        [action] = menu.actions()
        self.assertEqual(action.text(), f"Show only {SECTOR[0]}")
        action.trigger()
        self.assertEqual(self.dialog.system_filter.currentText(), SECTOR[0])
        self.assertEqual(self.systems_listed(), [SECTOR[0]])  # not c1-17 too
        self.assertIsNone(self.dialog.row_menu(None))

    def test_typed_text_survives_an_archive_change(self):
        self.type("tasch")
        new = archived_survey(self.store, "2026-11-09T07:00:00Z", body="Achenar 1")
        new.poi.system = "Achenar"
        self.store.save_archived(new)
        self.dialog.refresh()
        field = self.dialog.system_filter
        self.assertEqual(field.currentText(), "tasch")
        self.assertIn("Achenar", [field.itemText(i) for i in range(field.count())])
        self.assertEqual(self.systems_listed(), sorted(SECTOR))


if __name__ == "__main__":
    unittest.main()
