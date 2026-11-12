"""Desk tests for Milestone 9: turning automatic scan detection off, the scan circle at the custom destination,
recordings in Documents\\ASSIST\\recordings, and archived survey files named like the maps."""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSettings, QStandardPaths
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter
from PySide6.QtWidgets import QApplication

import main
from make_test_recording import CENTER
from assist.hotkeys import HotkeyDialog, HotkeyManager
from assist.map_view import draw_map, image_name
from assist.paths import assist_folder, documents_folder, recordings_folder
from assist.survey import AUTO, CLOSED, MANUAL, NO_PING, SurveyStore, file_name
from tests.test_custom import CustomCase
from tests.test_survey import ActionTests
from tests.test_surveys import archived_survey

app = QApplication.instance() or QApplication([])


class DetectionOffTests(unittest.TestCase):
    """ActionTests' snapshots (not its tests), with automatic scan detection turned off."""

    SRV, SHIP = ActionTests.SRV, ActionTests.SHIP
    status, deploy, tearDown = ActionTests.status, ActionTests.deploy, ActionTests.tearDown

    def setUp(self):
        ActionTests.setUp(self)
        self.tracker.detect_scans = False
        self.messages = []
        self.tracker.add_listener(lambda m: m and self.messages.append(m))

    def test_hardpoints_ignored_and_missed_scan_still_logged(self):
        self.deploy(*CENTER, ping=True)  # bit 6 comes on, but detection is off
        survey = self.tracker.survey
        self.assertEqual([p.kind for p in survey.pings], [NO_PING])
        self.assertEqual(survey.target, "C")  # picked again
        self.assertTrue(any("no manual ping" in m for m in self.messages), self.messages)
        self.assertFalse(any("no ping detected" in m for m in self.messages))

    def test_first_manual_ping_is_aimed(self):
        self.deploy(*CENTER, ping=False)
        survey = self.tracker.survey
        target = survey.target
        self.status(self.SHIP, *CENTER)
        self.status(self.SRV, *CENTER)
        self.status(self.SRV | (1 << 6), *CENTER)
        self.tracker.manual_ping()
        self.status(self.SHIP, *CENTER)
        kinds = [p.kind for p in survey.pings]
        self.assertNotIn(AUTO, kinds)
        self.assertEqual(survey.pings[-1].kind, MANUAL)
        self.assertNotEqual(survey.target, target)

    def test_started_in_the_srv_with_hardpoints_on(self):
        self.status(self.SRV | (1 << 6), *CENTER)
        self.assertFalse(self.tracker.deployment.pinged)

    def test_on_by_default(self):
        self.assertTrue(type(self.tracker)(self.store, self.tracker.pattern).detect_scans)


class DetectionCheckboxTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = QSettings(str(Path(folder.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.manager = HotkeyManager([("manual_ping", "Manual ping", lambda: None)], self.settings)
        self.addCleanup(app.removeNativeEventFilter, self.manager)

    def dialog(self, on):
        dialog = HotkeyDialog(self.manager, scan_detection=on)
        self.addCleanup(dialog.close)
        dialog.show()
        return dialog

    def test_note_only_when_off_and_unbound(self):
        dialog = self.dialog(True)
        self.assertTrue(dialog.detection_box.isChecked())
        self.assertFalse(dialog.no_key_note.isVisible())
        dialog.detection_box.setChecked(False)
        self.assertTrue(dialog.no_key_note.isVisible())
        dialog.edits["manual_ping"].setKeySequence(QKeySequence("F9"))
        self.assertFalse(dialog.no_key_note.isVisible())

    def test_ok_returns_the_choice_cancel_doesnt(self):
        dialog = self.dialog(True)
        dialog.detection_box.setChecked(False)
        dialog.reject()
        self.assertTrue(dialog.scan_detection)
        dialog = self.dialog(True)
        dialog.detection_box.setChecked(False)
        dialog.accept()
        self.assertFalse(dialog.scan_detection)

    def test_without_the_option(self):
        dialog = HotkeyDialog(self.manager)
        self.addCleanup(dialog.close)
        self.assertIsNone(dialog.detection_box)


class ScanCircleTests(CustomCase):
    def white_pixels(self, player):
        image = QImage(900, 700, QImage.Format.Format_RGB32)
        painter = QPainter(image)
        draw_map(painter, QRect(0, 0, 900, 700), self.survey, player)
        painter.end()
        count = 0
        for x in range(0, 900, 2):
            for y in range(0, 700, 2):
                c = QColor(image.pixel(x, y))
                count += c.red() > 170 and c.green() > 170 and c.blue() > 170
        return count

    def test_white_circle_on_the_live_map_only(self):
        player = (*CENTER, 0)
        before = self.white_pixels(player)
        self.tracker.set_custom(*self.far_from_points())
        self.assertGreater(self.white_pixels(player) - before, 50)  # a 2 km circle of white dashes
        self.assertLess(abs(self.white_pixels(None) - self.white_pixels_without_custom()), 5)

    def white_pixels_without_custom(self):
        custom, target = self.survey.custom, self.survey.target
        self.tracker.clear_custom()
        try:
            return self.white_pixels(None)
        finally:
            self.tracker.set_custom(custom.lat, custom.lon)


class RecordingsFolderTests(unittest.TestCase):
    def test_in_documents_assist(self):
        qt = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        self.assertEqual(os.path.normcase(documents_folder()), os.path.normcase(os.path.normpath(qt)))
        self.assertEqual(recordings_folder(), documents_folder() / "ASSIST" / "recordings")
        self.assertEqual(main.default_data_dir(), assist_folder())

    def test_replay_name_looked_up_there(self):
        self.assertEqual(main.find_recording("recording_x.jsonl"), recordings_folder() / "recording_x.jsonl")


class SurveyFileNameTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = SurveyStore(folder.name)

    def names(self):
        return sorted(p.name for p in self.store.archive_dir.glob("*.json"))

    def test_named_like_the_map(self):
        survey = archived_survey(self.store, "2026-11-08T07:36:14Z", body="LFT 509 B 3 b",
                                 name="Planetary Mining Location Signal (5)")
        self.assertEqual(self.names(), ["LFT 509 B 3 b - Signal (5) - 2026-11-08 07-36.json"])
        self.assertEqual(image_name(survey), "LFT 509 B 3 b - Signal (5) - 2026-11-08 07-36.png")
        self.assertEqual(self.store.load_archived(survey.id).id, survey.id)  # the id inside is unchanged
        self.assertEqual(survey.id, "2026-11-08T07-36-14_LFT-509-B-3-b")

    def test_clash_gets_a_number(self):
        a = archived_survey(self.store, "2026-11-08T07:36:14Z", body="X 1")
        b = archived_survey(self.store, "2026-11-08T07:36:50Z", body="X 1")
        self.assertEqual(self.names(), ["X 1 - Signal (3) - 2026-11-08 07-36 (2).json",
                                        "X 1 - Signal (3) - 2026-11-08 07-36.json"])
        self.assertEqual({s.id for s in self.store.archived()}, {a.id, b.id})
        archived_survey(self.store, "2026-11-08T07:36:14Z", body="X 1")  # a again: keeps its own file
        self.assertEqual(len(self.names()), 2)

    def test_old_files_renamed_once(self):
        survey = archived_survey(self.store, "2026-11-04T06:25:58Z", body="Cupiat 1 a")
        new = self.store.archive_path(survey.id)
        old = self.store.archive_dir / f"{survey.id}.json"
        new.rename(old)  # as saved before Milestone 9
        store = SurveyStore(self.store.folder)
        self.assertEqual(store.rename_archive_files(), [(old.name, new.name)])
        self.assertEqual(self.names(), [new.name])
        self.assertEqual(store.rename_archive_files(), [])
        self.assertEqual(store.load_archived(survey.id).id, survey.id)

    def test_changes_follow_the_file(self):
        survey = archived_survey(self.store, "2026-11-08T07:36:14Z", body="X 1", name=None)
        self.assertEqual(self.names(), ["X 1 - 2026-11-08 07-36.json"])
        survey.poi.name = "Mining Signal (7)"
        self.store.save_archived(survey)
        self.assertEqual(self.names(), ["X 1 - Signal (7) - 2026-11-08 07-36.json"])
        self.store.take_from_archive(survey)
        self.assertEqual(self.names(), [])
        self.assertEqual(self.store.load_active().id, survey.id)
        self.store.archive(survey)
        self.assertEqual(self.names(), ["X 1 - Signal (7) - 2026-11-08 07-36.json"])

    def test_active_file_keeps_its_name(self):
        survey = archived_survey(self.store, "2026-11-08T07:36:14Z")
        self.store.take_from_archive(survey)
        self.assertTrue((self.store.folder / "active.json").exists())

    def test_file_name_without_extension(self):
        survey = archived_survey(self.store, "2026-11-08T07:36:14Z", body='A:B "c"', state=CLOSED)
        self.assertEqual(file_name(survey, ""), "A-B -c- - Signal (3) - 2026-11-08 07-36")
        self.assertTrue(json.loads(self.store.archive_path(survey.id).read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
