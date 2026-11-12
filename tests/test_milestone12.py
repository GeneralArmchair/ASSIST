"""Desk tests for Milestone 12: right-clicking a deposit in the main window and in the Surveys list."""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from assist import survey_panel, surveys_dialog
from assist.survey_panel import SurveyPanel
from assist.surveys_dialog import SurveysDialog
from tests.test_deposits import SettingsCase
from tests.test_surveys import Case, archived_survey

MAIN_ACTIONS = ["Navigate to selected", "Update size / location"]  # the buttons' wording (Armchair)


def actions(menu):
    return [action.text() for action in menu.actions()]


def right_click(table, row):
    """Ask for the table's context menu over a row, as a right-click does; returns the menu shown, or None.

    The real menu opens; a timer reads and closes it.
    """
    shown = []

    def close_popup():
        popup = QApplication.activePopupWidget()
        if popup is not None:
            shown.append(actions(popup))
            popup.close()

    position = table.visualItemRect(table.item(row, 0)).center() if row is not None else QPoint(5, 1000)
    QTimer.singleShot(50, close_popup)
    table.customContextMenuRequested.emit(position)
    QTest.qWait(100)  # let the timer run when no menu opened
    return shown[0] if shown else None


class MainWindowMenuTests(SettingsCase):
    def setUp(self):
        super().setUp()
        self.panel = SurveyPanel(self.tracker, self.settings)
        self.panel.resize(1000, 800)
        self.addCleanup(self.panel.deleteLater)
        self.mark()
        self.mark(ore="Water", heading=90)
        self.table = self.panel.deposits

    def menu(self, row):
        return self.panel.deposit_menu(self.table.item(row, 3))  # any cell of the row

    def test_buttons_wording_and_no_delete(self):
        self.assertEqual(actions(self.menu(0)), MAIN_ACTIONS)

    def test_right_click_selects_the_row_under_the_cursor(self):
        self.table.selectRow(0)
        self.menu(1)
        self.assertEqual(self.panel._selected_deposit().ore, "Water")

    def test_navigate_goes_to_the_clicked_deposit(self):
        self.table.selectRow(0)
        self.menu(1).actions()[0].trigger()
        self.assertEqual(self.survey.target, "D2 Water")

    def test_update_opens_the_form_for_the_clicked_deposit(self):
        with mock.patch.object(survey_panel, "EditDepositDialog") as form:
            self.menu(1).actions()[1].trigger()
        self.assertEqual(form.call_args.args[1].label, "D2 Water")
        form.return_value.exec.assert_called_once()

    def test_menu_keeps_its_deposit_through_a_redraw(self):
        menu = self.menu(1)
        self.table.selectRow(0)  # e.g. the row moved under a redraw while the menu was open
        self.panel.refresh()
        menu.actions()[0].trigger()
        self.assertEqual(self.survey.target, "D2 Water")

    def test_nothing_if_the_deposit_went_while_the_menu_was_open(self):
        menu = self.menu(1)
        self.tracker.delete_deposit(2)
        with mock.patch.object(survey_panel, "EditDepositDialog") as form:
            menu.actions()[1].trigger()
            menu.actions()[0].trigger()
        form.assert_not_called()
        self.assertNotEqual(self.survey.target, "D2 Water")

    def test_nothing_if_the_survey_changed_while_the_menu_was_open(self):
        menu = self.menu(0)
        self.tracker.close_survey()
        with mock.patch.object(survey_panel, "EditDepositDialog") as form:
            menu.actions()[1].trigger()
            menu.actions()[0].trigger()  # no error with no survey
        form.assert_not_called()

    def test_no_menu_off_the_rows(self):
        self.assertIsNone(self.panel.deposit_menu(None))
        self.assertIsNone(right_click(self.table, None))

    def test_right_click_is_wired(self):
        self.assertEqual(right_click(self.table, 1), MAIN_ACTIONS)
        self.assertEqual(self.panel._selected_deposit().ore, "Water")

    def test_buttons_still_act_on_the_selection(self):
        self.table.selectRow(1)
        self.panel._fly_to_deposit()
        self.assertEqual(self.survey.target, "D2 Water")
        with mock.patch.object(survey_panel, "EditDepositDialog") as form:
            self.panel._edit_deposit()
        self.assertEqual(form.call_args.args[1].label, "D2 Water")

    def test_other_tables_have_no_menu(self):
        for table in (self.panel.points, self.panel.pings):
            self.assertNotEqual(table.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)


class SurveysListMenuTests(Case):
    def setUp(self):
        super().setUp()
        self.survey = archived_survey(self.store, "2026-11-02T07:00:00Z", deposits=2)
        self.dialog = SurveysDialog(self.tracker, QSettings("ASSIST-tests", "milestone12"))
        self.dialog.resize(1060, 640)
        self.addCleanup(self.dialog.deleteLater)
        self.dialog.table.selectRow(0)
        self.table = self.dialog.deposits

    def test_update_only_for_the_clicked_deposit(self):
        self.table.selectRow(0)
        self.assertEqual(right_click(self.table, 1), ["Update size / location"])
        self.assertEqual(self.dialog._selected_deposit().id, 2)
        menu = self.dialog.deposit_menu(self.table.item(1, 0))
        with mock.patch.object(surveys_dialog, "EditDepositDialog") as form:
            menu.actions()[0].trigger()
        self.assertEqual(form.call_args.args[1].id, 2)
        self.assertEqual(form.call_args.args[0].survey_id, self.survey.id)  # edits the archived survey

    def test_no_menu_off_the_rows(self):
        self.assertIsNone(self.dialog.deposit_menu(None))
        self.assertIsNone(right_click(self.table, None))

    def test_survey_table_keeps_its_own_menu(self):
        menu = self.dialog.row_menu(self.dialog.table.item(0, 0))
        self.assertEqual(actions(menu), ["Show only Sol"])


if __name__ == "__main__":
    unittest.main()
