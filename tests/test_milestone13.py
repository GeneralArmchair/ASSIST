"""Milestone 13: release prep. Setup.cmd, the public files, recordings named in UTC, the version.

Setup.cmd is run for real by cmd.exe in a scratch copy of the project (its folder name has a space, like many
Windows user folders). A fake `py` placed first on PATH stands in for the Python launcher: it is missing Python 3.12,
has only a 32-bit one, or passes everything on to this machine's real Python 3.12 (making .venv without pip). A stub
`pip` and `PySide6` on PYTHONPATH stand in for the download, so nothing goes online.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import record_status
from assist import __version__

PROJECT = Path(__file__).resolve().parent.parent
REAL_PYTHON = Path(getattr(sys, "_base_executable", "") or Path(sys.base_prefix) / "python.exe")
PIP_ARGS = ["install", "--require-hashes", "--only-binary=:all:", "--disable-pip-version-check", "--no-input",
            "-r", "requirements.txt"]

FAKE_PY = {
    "missing": "@echo off\r\nexit /b 103\r\n",  # what py says for a version that isn't installed
    "32bit": "@echo off\r\nexit /b 64\r\n",  # Setup.cmd's check exits 64 on a 32-bit Python
    "real": ("@echo off\r\n"
             'if not "%~1"=="-3.12" exit /b 103\r\n'
             'set "ARGS=%*"\r\n'
             'set "ARGS=%ARGS:*-3.12 =%"\r\n'
             'set "ARGS=%ARGS:-m venv=-m venv --without-pip%"\r\n'
             f'"{REAL_PYTHON}" %ARGS%\r\n'
             "exit /b %errorlevel%\r\n"),
}

STUB_PIP = """import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("\\t".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("STUB_PIP_EXIT", "0")))
"""


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=PROJECT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        raise unittest.SkipTest("git isn't available")
    return [PROJECT / name for name in out.decode("utf-8").split("\0") if name]


def tracked_text():
    """(name, text) for every tracked text file."""
    for path in tracked_files():
        if path.exists():
            try:
                yield path.relative_to(PROJECT).as_posix(), path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                pass  # binary


@unittest.skipUnless(sys.platform == "win32", "Setup.cmd runs on Windows")
class SetupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.project = self.root / "ASSIST test"
        self.project.mkdir()
        for name in ("Setup.cmd", "requirements.txt"):
            shutil.copy(PROJECT / name, self.project / name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.stub = self.root / "stub"
        (self.stub / "pip").mkdir(parents=True)
        (self.stub / "pip" / "__init__.py").write_text("")
        (self.stub / "pip" / "__main__.py").write_text(STUB_PIP)
        (self.stub / "PySide6").mkdir()
        (self.stub / "PySide6" / "__init__.py").write_text("")
        (self.stub / "PySide6" / "QtWidgets.py").write_text("")
        self.log = self.root / "pip.log"

    def run_setup(self, py="real", pip_exit=0, qt=True):
        (self.bin / "py.cmd").write_text(FAKE_PY[py], newline="")
        if not qt:
            shutil.rmtree(self.stub / "PySide6")
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME")}
        env.update(PATH=f"{self.bin};{os.environ['PATH']}", PYTHONPATH=str(self.stub), STUB_LOG=str(self.log),
                   STUB_PIP_EXIT=str(pip_exit))
        done = subprocess.run(["cmd", "/c", str(self.project / "Setup.cmd")], cwd=self.root, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
        return done.returncode, done.stdout.decode("ascii", "replace")

    def pip_calls(self):
        if not self.log.exists():
            return []
        return [line.split("\t") for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_no_python_312(self):
        code, out = self.run_setup("missing")
        self.assertEqual(code, 1)
        self.assertIn("Python 3.12 wasn't found", out)
        self.assertIn("py install 3.12", out)
        self.assertFalse((self.project / ".venv").exists())
        self.assertEqual(self.pip_calls(), [])

    def test_32bit_python(self):
        code, out = self.run_setup("32bit")
        self.assertEqual(code, 1)
        self.assertIn("32-bit", out)
        self.assertFalse((self.project / ".venv").exists())

    def test_sets_up_venv_and_installs_pinned_qt(self):
        code, out = self.run_setup()
        self.assertEqual(code, 0, out)
        venv_python = self.project / ".venv" / "Scripts" / "python.exe"
        self.assertTrue(venv_python.exists())
        self.assertTrue((self.project / ".venv" / "Scripts" / "pythonw.exe").exists())  # what ASSIST.cmd starts
        version = subprocess.run([str(venv_python), "-c", "import sys; print(sys.version_info[:2])"],
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(version, "(3, 12)")
        self.assertEqual(self.pip_calls(), [PIP_ARGS])
        self.assertIn("only step that uses the network", out)
        self.assertIn("double-clicking ASSIST.cmd", out)
        self.assertIn("Send to", out)
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), [".venv", "Setup.cmd", "requirements.txt"])

    def test_run_again_reuses_venv(self):
        self.assertEqual(self.run_setup()[0], 0)
        marker = self.project / ".venv" / "marker"
        marker.write_text("kept")
        code, out = self.run_setup()
        self.assertEqual(code, 0, out)
        self.assertIn("already in .venv", out)
        self.assertTrue(marker.exists())
        self.assertEqual(self.pip_calls(), [PIP_ARGS, PIP_ARGS])

    def test_broken_venv_is_left_alone(self):
        (self.project / ".venv").mkdir()
        (self.project / ".venv" / "something").write_text("x")
        code, out = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("Delete the .venv folder", out)
        self.assertTrue((self.project / ".venv" / "something").exists())
        self.assertEqual(self.pip_calls(), [])

    def test_pip_failure(self):
        code, out = self.run_setup(pip_exit=1)
        self.assertEqual(code, 1)
        self.assertIn("Installing Qt failed", out)
        self.assertNotIn("ASSIST is set up", out)

    def test_qt_not_loading(self):
        code, out = self.run_setup(qt=False)
        self.assertEqual(code, 1)
        self.assertIn("doesn't load", out)


class LauncherTests(unittest.TestCase):
    def test_nothing_alarming_in_the_scripts(self):
        """The two .cmd files start no other tools, change no settings and go nowhere online themselves."""
        for name in ("Setup.cmd", "ASSIST.cmd"):
            text = (PROJECT / name).read_text(encoding="ascii").lower()
            for word in ("powershell", "wscript", "cscript", "mshta", "curl", "bitsadmin", "certutil", "http",
                         "reg ", "schtasks", "setx", "mklink", "attrib", "--upgrade", "del ", "rmdir", "rd "):
                self.assertNotIn(word, text, f"{name}: {word}")

    @unittest.skipUnless(sys.platform == "win32", "ASSIST.cmd runs on Windows")
    def test_assist_cmd_points_to_setup(self):
        """Run before setting up, ASSIST.cmd says to double-click Setup.cmd and starts nothing."""
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "ASSIST test"
            project.mkdir()
            shutil.copy(PROJECT / "ASSIST.cmd", project / "ASSIST.cmd")
            done = subprocess.run(["cmd", "/c", str(project / "ASSIST.cmd")], cwd=root, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertIn("Double-click Setup.cmd", done.stdout.decode("ascii", "replace"))

    def test_windows_line_endings(self):
        self.assertIn("*.cmd text eol=crlf", (PROJECT / ".gitattributes").read_text(encoding="utf-8"))
        for name in ("Setup.cmd", "ASSIST.cmd"):
            data = (PROJECT / name).read_bytes()
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), name)


class PublicFilesTests(unittest.TestCase):
    GENERIC_NAMES = {"user", "admin", "administrator", "owner", "guest", "default", "public", "test"}

    def test_files_that_ship(self):
        names = {path.relative_to(PROJECT).as_posix() for path in tracked_files()}
        for name in ("LICENSE", "README.md", "GUIDE.md", "Setup.cmd", "ASSIST.cmd", ".gitattributes",
                     ".github/ISSUE_TEMPLATE/bug_report.md"):
            self.assertIn(name, names)
        self.assertNotIn("BRIEF.md", names)  # private working notes (Armchair)
        self.assertNotIn("DECISIONS.md", names)

    def test_license_is_gpl3(self):
        text = (PROJECT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 29 June 2007", text)
        self.assertIn("END OF TERMS AND CONDITIONS", text)

    def test_readme_and_guide(self):
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        for words in ("Setup.cmd", "GUIDE.md", "LICENSE", "GPL-3.0", "PyPI", ".exe"):
            self.assertIn(words, readme)
        self.assertNotIn("DECISIONS.md", readme)
        guide = (PROJECT / "GUIDE.md").read_text(encoding="utf-8")
        for words in ("Borderless", "unbound", "Issues", "_full"):
            self.assertIn(words, guide)

    def test_no_windows_user_name(self):
        names = {Path.home().name, os.environ.get("USERNAME", "")}
        names = {n for n in names if len(n) >= 3 and n.lower() not in self.GENERIC_NAMES}
        for name, text in tracked_text():
            for user in names:
                self.assertIsNone(re.search(rf"\b{re.escape(user)}\b", text, re.IGNORECASE),
                                  f"{name} contains the Windows user name")

    LOCAL_STAMP = re.compile(r"recording_\d{8}_\d{6}(?![\dZ])")
    OFFSET = re.compile(r"\d\d:\d\d(?::\d\d)?(?:\.\d+)? ?[+-](?!00:?00\b)\d\d:?\d\d\b"  # a time with its offset
                        r"|\b(?:UTC|GMT) ?[+-] ?\d"                                # UTC-6, GMT+1
                        r"|\b(?:from|offset|zone|dates?) [+-](?!0000\b)\d{4}\b", re.IGNORECASE)  # "moved from -0600"

    # Invented offsets in tests that check the conversion to UTC: not anyone's time zone.
    MADE_UP_OFFSETS = {("tests/test_map.py", "09:36:14+02:00")}

    def test_leak_patterns(self):
        """The patterns catch the leaks found in Milestone 13 and leave ordinary numbers alone."""
        for leak in ("recording_20261104_221051", "commit dates moved from -0600 to +0000", "2026-11-11 22:10:51 -0600",
                     "2026-11-11T22:10:51-06:00", "UTC-6"):
            self.assertTrue(self.LOCAL_STAMP.search(leak) or self.OFFSET.search(leak), leak)
        for fine in ("recording_20261112_140000Z.jsonl", "2026-11-12 05:41:10 +0000", "12:00:00+00:00",
                     "altitude -1000", "Altitude -1860", "heading 0300"):
            self.assertFalse(self.LOCAL_STAMP.search(fine) or self.OFFSET.search(fine), fine)

    def test_no_local_time_stamps_or_offsets(self):
        """Recording names in local time, or a UTC offset, would give away a time zone."""
        for name, text in tracked_text():
            if name == "tests/test_milestone13.py":
                continue  # the examples just above
            for pattern in (self.LOCAL_STAMP, self.OFFSET):
                for match in pattern.finditer(text):
                    if (name, match.group()) not in self.MADE_UP_OFFSETS:
                        self.fail(f"{name}: {match.group()}")

    def test_no_pointers_to_the_private_notes(self):
        """BRIEF.md and DECISIONS.md stay private, so nothing that ships refers readers to them."""
        for name, text in tracked_text():
            if name != "tests/test_milestone13.py":
                self.assertNotRegex(text, r"(BRIEF|DECISIONS)\.md", name)

    def test_no_email_addresses(self):
        address = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*[a-z]", re.IGNORECASE)
        for name, text in tracked_text():
            match = address.search(text)
            self.assertIsNone(match, f"{name}: {match and match.group()}")


class RecordingNameTests(unittest.TestCase):
    def test_named_in_utc_whatever_the_clock(self):
        elsewhere = timezone(timedelta(hours=9))
        now = datetime(2026, 11, 12, 23, 0, 0, tzinfo=elsewhere)
        self.assertEqual(record_status.recording_name(now), "recording_20261112_140000Z.jsonl")
        self.assertEqual(record_status.recording_name(now, full=True), "recording_20261112_140000Z_full.jsonl")


class VersionTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(__version__, "0.13.0")


if __name__ == "__main__":
    unittest.main()
