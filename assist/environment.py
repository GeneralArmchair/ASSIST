"""Whether ASSIST was started with the project's own Python (.venv).

Started by double-clicking main.py or as `.\\main.py`, Windows runs a script with the system's Python through the
launcher, which can drop options such as --full and --replay and uses whatever Qt that Python has, not the version
pinned in requirements.txt (Milestone 11: `record_status.py --full` recorded trimmed that way). ASSIST.cmd starts
it the right way.
"""

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
VENV = PROJECT / ".venv"

# How to start each script the right way.
START_ASSIST = "double-click ASSIST.cmd in the project folder"
START_RECORDER = r"run .venv\Scripts\python.exe record_status.py from the project folder"


def in_venv(prefix=None):
    """True if this Python is the project's .venv."""
    try:
        return Path(prefix or sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def venv_warning(how_to_start, prefix=None):
    """A warning to show if this Python isn't the project's .venv, else None."""
    if in_venv(prefix):
        return None
    return (f"Started with the wrong Python: options may be lost and a different Qt used. "
            f"Close it and {how_to_start}.")
