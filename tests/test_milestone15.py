"""Desk tests for Milestone 15: the Preferences dialog (Survey Settings, with the scan detection checkbox moved there
from Hotkeys...) and the public CHANGELOG.md."""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

from assist import __version__, window as window_module
from assist.hotkeys import HotkeyDialog, HotkeyManager
from assist.preferences import (DEFAULTS, NO_MANUAL_PING_KEY, SCAN_DETECTION_TEXT, PreferencesDialog,
                                SurveySettingsPage)
from assist.sources import ReplaySource
from assist.survey import SurveyStore
from assist.tracker import SurveyTracker
from tests.test_guidance import PATTERN
from tests.test_milestone13 import PublicFilesTests

app = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parent.parent


class PreferencesDialogTests(unittest.TestCase):
    def dialog(self, values=None, bound=True):
        dialog = PreferencesDialog({} if values is None else values, manual_ping_bound=bound)
        self.addCleanup(dialog.close)
        dialog.show()
        return dialog

    def test_title_and_categories(self):
        dialog = self.dialog()
        self.assertEqual(dialog.windowTitle(), "Preferences")
        self.assertEqual(dialog.categories.item(0).text(), "Survey Settings")
        self.assertEqual(dialog.categories.currentRow(), 0)
        self.assertIs(dialog.stack.currentWidget(), dialog.page("Survey Settings"))

    def test_each_category_has_its_page(self):
        dialog = self.dialog()
        self.assertEqual(dialog.categories.count(), dialog.stack.count())
        for row, page in enumerate(dialog.pages):
            dialog.categories.setCurrentRow(row)
            self.assertIs(dialog.stack.currentWidget(), page)
            self.assertEqual(dialog.categories.item(row).text(), page.title)

    def test_checkbox_shows_the_setting(self):
        for on in (True, False):
            page = self.dialog({"auto_scan_detection": on}).page("Survey Settings")
            self.assertIsInstance(page, SurveySettingsPage)
            self.assertEqual(page.detection_box.text(), SCAN_DETECTION_TEXT)
            self.assertEqual(page.detection_box.isChecked(), on)

    def test_missing_setting_gets_the_default(self):
        self.assertTrue(DEFAULTS["auto_scan_detection"])
        self.assertTrue(self.dialog().page("Survey Settings").detection_box.isChecked())

    def test_note_only_when_off_and_manual_ping_unbound(self):
        page = self.dialog({"auto_scan_detection": True}, bound=False).page("Survey Settings")
        self.assertFalse(page.no_key_note.isVisible())
        page.detection_box.setChecked(False)
        self.assertTrue(page.no_key_note.isVisible())
        self.assertEqual(page.no_key_note.text(), NO_MANUAL_PING_KEY)
        self.assertIn("Hotkeys...", NO_MANUAL_PING_KEY)
        page.detection_box.setChecked(True)
        self.assertFalse(page.no_key_note.isVisible())

    def test_no_note_with_manual_ping_bound(self):
        page = self.dialog({"auto_scan_detection": False}, bound=True).page("Survey Settings")
        self.assertFalse(page.no_key_note.isVisible())

    def test_ok_returns_the_choice(self):
        dialog = self.dialog({"auto_scan_detection": True})
        dialog.page("Survey Settings").detection_box.setChecked(False)
        dialog.accept()
        self.assertEqual(dialog.values, {"auto_scan_detection": False})

    def test_cancel_changes_nothing(self):
        dialog = self.dialog({"auto_scan_detection": True})
        dialog.page("Survey Settings").detection_box.setChecked(False)
        dialog.reject()
        self.assertEqual(dialog.values, {"auto_scan_detection": True})


class HotkeysDialogTests(unittest.TestCase):
    def test_checkbox_gone_from_hotkeys(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = QSettings(str(Path(folder.name) / "settings.ini"), QSettings.Format.IniFormat)
        manager = HotkeyManager([("manual_ping", "Manual ping", lambda: None)], settings)
        self.addCleanup(app.removeNativeEventFilter, manager)
        dialog = HotkeyDialog(manager)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.findChildren(QCheckBox), [])
        self.assertIn("manual_ping", dialog.edits)
        with self.assertRaises(TypeError):
            HotkeyDialog(manager, scan_detection=True)


class WindowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        recording = Path(folder.name) / "r.jsonl"
        recording.write_text("", encoding="utf-8")
        self.tracker = SurveyTracker(SurveyStore(folder.name), PATTERN)
        self.window = window_module.StatusWindow(ReplaySource(recording, folder.name), self.tracker, "test")
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(app.removeNativeEventFilter, self.window.hotkeys)
        # Never write to the real settings in the registry.
        self.window.settings = QSettings(str(Path(folder.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.window.hotkeys.settings = self.window.settings

    def status_bar_buttons(self):
        return [w.text() for w in self.window.statusBar().findChildren(QPushButton)]

    def test_button_next_to_hotkeys(self):
        buttons = self.status_bar_buttons()
        self.assertIn("Preferences...", buttons)
        self.assertEqual(buttons.index("Preferences..."), buttons.index("Hotkeys...") + 1)

    def test_preferences_show_the_current_setting(self):
        self.tracker.detect_scans = False
        self.assertEqual(self.window.preferences(), {"auto_scan_detection": False})

    def test_turning_detection_off_is_stored_used_and_logged(self):
        self.tracker.detect_scans = True
        self.window.apply_preferences({"auto_scan_detection": False})
        self.assertFalse(self.tracker.detect_scans)
        self.assertFalse(self.window.settings.value("auto_scan_detection", type=bool))
        self.assertIn("Automatic scan detection is off", self.window.survey_log.toPlainText())

    def test_unchanged_writes_nothing(self):
        self.tracker.detect_scans = True
        log = self.window.survey_log.toPlainText()
        self.window.apply_preferences({"auto_scan_detection": True})
        self.assertFalse(self.window.settings.contains("auto_scan_detection"))
        self.assertEqual(self.window.survey_log.toPlainText(), log)

    def test_dialog_told_whether_manual_ping_has_a_key(self):
        for key, bound in (("", False), ("F9", True)):
            self.window.hotkeys.set_binding("manual_ping", QKeySequence(key))
            with mock.patch.object(window_module, "PreferencesDialog") as dialog:
                dialog.return_value.exec.return_value = False
                self.window._edit_preferences()
            self.assertEqual(dialog.call_args.kwargs["manual_ping_bound"], bound)

    def test_ok_applies_cancel_doesnt(self):
        self.tracker.detect_scans = True
        with mock.patch.object(window_module, "PreferencesDialog") as dialog:
            dialog.return_value.exec.return_value = False
            dialog.return_value.values = {"auto_scan_detection": False}
            self.window._edit_preferences()
            self.assertTrue(self.tracker.detect_scans)
            dialog.return_value.exec.return_value = True
            self.window._edit_preferences()
            self.assertFalse(self.tracker.detect_scans)


class ChangelogTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    def versions(self):
        return re.findall(r"^## (\d+)\.(\d+)\.(\d+)", self.text, re.MULTILINE)

    def test_current_version_first(self):
        self.assertEqual(".".join(self.versions()[0]), __version__)

    def test_newest_first_and_complete(self):
        versions = [tuple(map(int, v)) for v in self.versions()]
        self.assertEqual(versions, sorted(versions, reverse=True))
        milestones = {minor for _, minor, _ in versions}
        self.assertTrue(set(range(6, 16)) <= milestones, milestones)
        self.assertIn("## Before 0.6.0", self.text)

    def test_describes_this_version(self):
        for words in ("Preferences...", "Survey Settings", SCAN_DETECTION_TEXT):
            self.assertIn(words, self.text.split("## 0.14.0")[0])

    def test_linked_from_readme_and_guide(self):
        for name in ("README.md", "GUIDE.md"):
            self.assertIn("CHANGELOG.md", (ROOT / name).read_text(encoding="utf-8"), name)

    def test_nothing_private(self):
        """The leak checks of Milestone 13, run on the changelog even before it's tracked by git."""
        checks = PublicFilesTests()
        for pattern in (checks.LOCAL_STAMP, checks.OFFSET):
            self.assertIsNone(pattern.search(self.text))
        self.assertNotRegex(self.text, r"(BRIEF|DECISIONS)\.md")
        self.assertNotRegex(self.text, r"[\w.+-]+@[\w-]+\.[\w.-]*[a-z]")
        for user in {Path.home().name, os.environ.get("USERNAME", "")}:
            if len(user) >= 3 and user.lower() not in PublicFilesTests.GENERIC_NAMES:
                self.assertNotRegex(self.text, rf"(?i)\b{re.escape(user)}\b")

    def test_windows_line_endings(self):
        raw = (ROOT / "CHANGELOG.md").read_bytes()
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))


class GuideTests(unittest.TestCase):
    def test_guide_points_to_preferences(self):
        guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("**Preferences...**", guide)
        self.assertNotIn("The checkbox at the top of Hotkeys...", guide)


class VersionTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(__version__, "0.15.0")


if __name__ == "__main__":
    unittest.main()
