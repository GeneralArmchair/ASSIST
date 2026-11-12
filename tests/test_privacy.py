"""Desk tests for Milestone 11, part 1: the privacy review.

No networking code is loaded, recordings keep only the Status.json fields ASSIST uses, and paths in messages hide
the home folder.
"""

import ast
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import record_status
from assist.status import RECORDED_FIELDS, Status, recorded_fields

ROOT = Path(__file__).parent.parent
SOURCES = sorted(ROOT.glob("assist/*.py")) + [ROOT / name for name in
                                              ("main.py", "record_status.py", "make_test_recording.py")]

# Modules that talk to the network, or open web pages or other programs. Not urllib.parse: it only splits text,
# and Python's own pathlib loads it.
NETWORK_MODULES = ("socket", "ssl", "http", "urllib.request", "urllib.error", "urllib3", "requests", "ftplib", "smtplib", "poplib",
                   "imaplib", "xmlrpc", "asyncio", "webbrowser", "subprocess", "PySide6.QtNetwork",
                   "PySide6.QtWebEngineCore", "PySide6.QtWebSockets")

# Status.json as the game writes it, including the account data ASSIST must not record.
FULL_STATUS = {
    "timestamp": "2026-11-11T08:00:00Z", "event": "Status", "Flags": 69206024, "Flags2": 0,
    "Pips": [4, 4, 4], "FireGroup": 0, "GuiFocus": 0, "Fuel": {"FuelMain": 0.0, "FuelReservoir": 0.45},
    "Cargo": 2.0, "LegalState": "Clean", "Latitude": 55.6, "Longitude": 111.0, "Heading": 12, "Altitude": 0,
    "BodyName": "Test 1 a", "PlanetRadius": 1389066.5, "Balance": 1234567890,
    "Destination": {"System": 1, "Body": 30, "Name": "$SAA_Unknown_Signal:#type=$PlanetaryMiningLocation_Name;"},
}
ACCOUNT_FIELDS = ("Balance", "Cargo", "Fuel", "LegalState", "Pips", "FireGroup")


def imported_names(path):
    """Every module name a source file imports."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def is_network(name):
    return any(name == module or name.startswith(module + ".") for module in NETWORK_MODULES)


class NoNetworkTests(unittest.TestCase):
    def test_no_source_file_imports_a_network_module(self):
        for path in SOURCES:
            with self.subTest(file=path.name):
                self.assertEqual(sorted(filter(is_network, imported_names(path))), [])

    def test_running_assist_loads_no_network_module(self):
        """Load every module and build the main window in a fresh Python; then list what got loaded."""
        script = (
            "import os, sys\n"
            "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import tempfile, main, record_status, make_test_recording\n"
            "from PySide6.QtWidgets import QApplication\n"
            "from assist import coverage, deposit_forms, map_view, surveys_dialog\n"
            "from assist.sources import ReplaySource\n"
            "from assist.survey import SurveyStore, load_pattern\n"
            "from assist.tracker import SurveyTracker\n"
            "from assist.window import StatusWindow\n"
            "app = QApplication([])\n"
            "with tempfile.TemporaryDirectory() as folder:\n"
            "    recording = os.path.join(folder, 'r.jsonl')\n"
            "    open(recording, 'w').close()\n"
            "    tracker = SurveyTracker(SurveyStore(folder), load_pattern(main.PATTERN_PATH))\n"
            "    window = StatusWindow(ReplaySource(recording, folder), tracker, 'test')\n"
            "    surveys_dialog.SurveysDialog(tracker, window.settings)\n"
            "print('\\n'.join(sorted(sys.modules)))\n"
        )
        # The test runner itself may have loaded such modules, so this runs in a fresh, isolated interpreter.
        result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = result.stdout.split()
        self.assertIn("assist.window", loaded)
        self.assertEqual([name for name in loaded if is_network(name)], [])


class RecordingTests(unittest.TestCase):
    def test_only_the_fields_assist_uses(self):
        line = json.loads(record_status.record_line(1762848000.0, FULL_STATUS))
        self.assertEqual(set(line), {"t", "status"})  # no "local": it gave away the time zone
        self.assertEqual(set(line["status"]), set(RECORDED_FIELDS))
        for field in ACCOUNT_FIELDS:
            self.assertNotIn(field, line["status"])

    def test_trimmed_status_decodes_the_same(self):
        """Everything ASSIST reads from Status.json survives the trimming."""
        full, trimmed = Status.from_json(FULL_STATUS), Status.from_json(recorded_fields(FULL_STATUS))
        for name in Status.__dataclass_fields__:
            if name != "raw":
                self.assertEqual(getattr(full, name), getattr(trimmed, name), name)
        self.assertEqual(full.gui_focus, trimmed.gui_focus)

    def test_full_keeps_everything(self):
        line = json.loads(record_status.record_line(1762848000.0, FULL_STATUS, full=True))
        self.assertEqual(line["status"], FULL_STATUS)

    def test_full_files_are_named_and_warned(self):
        now = datetime(2026, 11, 11, 8, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(record_status.recording_name(now), "recording_20261111_080000Z.jsonl")
        self.assertEqual(record_status.recording_name(now, full=True), "recording_20261111_080000Z_full.jsonl")
        self.assertIn("credit balance", record_status.FULL_WARNING)
        self.assertFalse(record_status.parse_args([]).full)
        self.assertTrue(record_status.parse_args(["--full"]).full)


class ShownPathTests(unittest.TestCase):
    def test_pattern_error_hides_the_home_folder(self):
        import main
        missing = Path.home() / "no such folder" / "pattern.json"
        with mock.patch.object(sys, "argv", ["main.py", "--pattern", str(missing)]), \
                mock.patch.object(main, "QApplication"), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main.main(), 1)
        self.assertIn("%USERPROFILE%", out.getvalue())
        self.assertNotIn(str(Path.home()), out.getvalue())


if __name__ == "__main__":
    unittest.main()
