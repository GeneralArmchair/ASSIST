"""Desk tests for Milestone 11, part 2: survey files copied into archive\\ while ASSIST runs."""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

import record_status
from assist.environment import PROJECT as ROOT, START_ASSIST, START_RECORDER, VENV, venv_warning
from assist.sources import ReplaySource
from assist.survey import ACTIVE, POI, UNFINISHED, Survey, SurveyStore
from assist.surveys_dialog import SurveysDialog
from assist.tracker import SurveyTracker
from assist.window import StatusWindow
from tests.test_guidance import CENTER, PATTERN, PLANET_RADIUS
from tests.test_surveys import Case, archived_survey


class ReceivedCase(Case):
    def setUp(self):
        super().setUp()
        self.other = tempfile.TemporaryDirectory()  # another player's Documents\ASSIST
        self.addCleanup(self.other.cleanup)
        self.other_store = SurveyStore(self.other.name)

    def receive(self, survey, name=None):
        """Copy another player's archive file into ours, as a player would from Discord."""
        source = self.other_store.archive_path(survey.id)
        target = self.store.archive_dir / (name or source.name)
        shutil.copy(source, target)
        return target


class SurveysListTests(ReceivedCase):
    def dialog(self):
        dialog = SurveysDialog(self.tracker, QSettings("ASSIST-tests", "milestone11"))
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_copied_file_shows_when_the_list_opens(self):
        archived_survey(self.store, "2026-11-01T10:00:00Z")
        self.assertEqual(len(self.dialog().surveys), 1)
        friend = archived_survey(self.other_store, "2026-11-02T10:00:00Z", body="Friend 2 b")
        self.receive(friend)
        self.assertIn(friend.id, self.dialog().surveys)
        self.assertEqual(len(self.tracker.archived_surveys()), 2)

    def test_removed_file_goes_when_the_list_opens(self):
        mine = archived_survey(self.store, "2026-11-01T10:00:00Z")
        self.dialog()
        self.store.archive_path(mine.id).unlink()
        self.assertEqual(self.dialog().surveys, {})

    def test_duplicate_skipped_and_logged_once(self):
        mine = archived_survey(self.store, "2026-11-01T10:00:00Z")
        back = self.store.load_archived(mine.id)
        back.updated = "2026-11-05T10:00:00Z"  # continued by a friend, then sent back
        self.other_store.save_archived(back)
        copy = self.receive(back, name="received copy.json")
        self.dialog()
        kept = self.store.archive_path(mine.id)
        self.assertEqual(kept, copy)  # the most recently updated copy is used
        self.assertEqual(len(self.tracker.archived_surveys()), 1)
        skipped = [m for m in self.messages if m.startswith("Skipped survey file")]
        self.assertEqual(len(skipped), 1)
        self.assertIn("received copy.json", skipped[0].split(": same survey as ")[1])
        self.dialog()  # opening again doesn't repeat the warning
        self.assertEqual(len([m for m in self.messages if m.startswith("Skipped survey file")]), 1)

    def test_duplicate_tie_keeps_the_first_name(self):
        mine = archived_survey(self.store, "2026-11-01T10:00:00Z")
        first = self.store.archive_path(mine.id)
        shutil.copy(first, self.store.archive_dir / "zz copy.json")
        self.store.rescan()
        self.assertEqual(self.store.archive_path(mine.id), first)
        self.assertEqual(self.store.new_duplicates(), [("zz copy.json", first.name)])
        self.assertEqual(self.store.new_duplicates(), [])

    def test_saving_does_not_overwrite_a_skipped_duplicate(self):
        mine = archived_survey(self.store, "2026-11-01T10:00:00Z")
        original = self.store.archive_path(mine.id)
        shutil.copy(original, self.store.archive_dir / "a copy.json")  # sorts first, same "updated": it's kept
        self.store.rescan()
        self.assertEqual(self.store.new_duplicates(), [(original.name, "a copy.json")])
        before = original.read_bytes()
        survey = self.store.load_archived(mine.id)
        survey.deposits.clear()
        self.store.save_archived(survey)
        # The skipped file is left as it was; the kept one takes the map-style name with " (2)", since it's taken.
        self.assertEqual(original.read_bytes(), before)
        saved = self.store.archive_path(mine.id)
        self.assertEqual(saved.name, original.name.replace(".json", " (2).json"))
        self.assertEqual(self.store.load_archived(mine.id).deposits, [])
        self.assertEqual(len(list(self.store.archive_dir.glob("*.json"))), 2)


class DeployTests(ReceivedCase):
    def unfinished_here(self, store):
        poi = POI(system=None, body="Test", planet_radius=PLANET_RADIUS, center_lat=CENTER[0], center_lon=CENTER[1])
        survey = Survey.new(poi, PATTERN, "2026-11-01T10:00:00Z")
        survey.state = UNFINISHED
        store.save_archived(survey)
        return survey

    def test_copied_unfinished_survey_resumes_on_deploying(self):
        self.store.archived()  # the index is read now, before the file arrives
        friend = self.unfinished_here(self.other_store)
        self.receive(friend)
        self.deploy_here()
        self.assertIsNotNone(self.tracker.survey)
        self.assertEqual(self.tracker.survey.id, friend.id)
        self.assertEqual(self.tracker.survey.state, ACTIVE)
        self.assertIsNone(self.store.archive_path(friend.id))  # moved to active.json

    def test_no_rescan_while_a_survey_is_active(self):
        self.deploy_here()  # starts a survey of its own
        with mock.patch.object(self.store, "rescan") as rescan:
            self.deploy_here(second=10)
        rescan.assert_not_called()


class VenvWarningTests(unittest.TestCase):
    def test_none_with_the_project_venv(self):
        self.assertIsNone(venv_warning(START_ASSIST, prefix=VENV))
        self.assertIsNone(venv_warning(START_ASSIST))  # the tests run with .venv's Python

    def test_warns_with_another_python(self):
        self.assertIn("ASSIST.cmd", venv_warning(START_ASSIST, prefix=r"C:\Python314"))
        self.assertIn(r".venv\Scripts\python.exe record_status.py",
                      venv_warning(START_RECORDER, prefix=r"C:\Python314"))

    def test_recorder_prints_it_before_reading_options(self):
        with mock.patch.object(record_status, "venv_warning", return_value="WARNING"), \
                mock.patch.object(record_status, "parse_args", side_effect=SystemExit), \
                redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            record_status.main()
        self.assertIn("WARNING", out.getvalue())

    def test_launcher_uses_the_project_python(self):
        text = (ROOT / "ASSIST.cmd").read_text(encoding="utf-8")
        self.assertIn(r'".venv\Scripts\pythonw.exe" main.py %*', text)
        self.assertIn('cd /d "%~dp0"', text)


class BannerTests(unittest.TestCase):
    def window(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        recording = Path(folder.name) / "r.jsonl"
        recording.write_text("", encoding="utf-8")
        tracker = SurveyTracker(SurveyStore(folder.name), PATTERN)
        window = StatusWindow(ReplaySource(recording, folder.name), tracker, "ASSIST 0.11.0 - live")
        self.addCleanup(window.deleteLater)
        return window

    def test_hidden_normally(self):
        window = self.window()
        self.assertTrue(window.banner.isHidden())
        self.assertEqual(window.windowTitle(), "ASSIST 0.11.0 - live")

    def test_shown_with_title_marker_and_log(self):
        window = self.window()
        text = venv_warning(START_ASSIST, prefix=r"C:\Python314")
        window.show_warning(text, "wrong Python")
        self.assertFalse(window.banner.isHidden())
        self.assertEqual(window.banner.text(), text)
        self.assertEqual(window.windowTitle(), "ASSIST 0.11.0 - live (wrong Python)")
        self.assertIn(text, window.survey_log.toPlainText())
        # Above everything else in the window.
        self.assertIs(window.centralWidget().layout().itemAt(0).widget(), window.banner)


if __name__ == "__main__":
    unittest.main()
