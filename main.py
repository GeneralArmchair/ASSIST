"""ASSIST (Armchair's Surface Survey & Information Sharing Tool): show the live game status, or replay a recording.

Usage: double-click ASSIST.cmd, or from the project folder:
    .venv\\Scripts\\python.exe main.py
    .venv\\Scripts\\python.exe main.py --replay recording_20261112_140000Z.jsonl --speed 4
A replay file name without a folder is looked up in Documents\\ASSIST\\recordings.

Surveys are saved in Documents\\ASSIST (change with --data-dir). A replay uses its own
replay\\ subfolder there, emptied when the replay starts, so desk tests never touch real surveys.
"""

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from assist import __version__
from assist.environment import START_ASSIST, venv_warning
from assist.overlay import GameWindow
from assist.paths import assist_folder, recordings_folder, shown
from assist.sources import LiveSource, ReplaySource
from assist.status import GAME_DIR, STATUS_PATH
from assist.survey import SurveyStore, load_pattern
from assist.tracker import SurveyTracker
from assist.window import StatusWindow

PATTERN_PATH = Path(__file__).parent / "pattern.json"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replay", metavar="FILE", help="replay a recording instead of reading the game")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier (default 1)")
    parser.add_argument("--max-gap", type=float, default=5.0,
                        help="shorten replay gaps longer than this many seconds (default 5)")
    parser.add_argument("--status", type=Path, default=STATUS_PATH, help="path to Status.json")
    parser.add_argument("--journal-dir", type=Path, default=GAME_DIR, help="folder with the journal files")
    parser.add_argument("--data-dir", type=Path, help="folder for survey files (default Documents\\ASSIST)")
    parser.add_argument("--pattern", type=Path, default=PATTERN_PATH, help="survey pattern file (default pattern.json)")
    return parser.parse_args()


def find_recording(name):
    path = Path(name)
    if not path.exists() and not path.parent.parts:
        path = recordings_folder() / name
    return path


def default_data_dir():
    return assist_folder()  # in the real Documents folder, even when OneDrive has moved it


def main():
    args = parse_args()
    app = QApplication(sys.argv)
    data_dir = args.data_dir or default_data_dir()
    try:
        pattern = load_pattern(args.pattern)
    except (OSError, ValueError, KeyError, TypeError) as error:
        # An OSError's text holds the full path; show only its reason, with the path through shown().
        reason = error.strerror if isinstance(error, OSError) and error.strerror else error
        print(f"Can't read the survey pattern {shown(args.pattern)}: {reason}")
        return 1

    game_window = None
    if args.replay:
        path = find_recording(args.replay)
        if not path.exists():
            print(f"Can't find recording {args.replay}")
            return 1
        if args.speed <= 0:
            print("--speed must be more than 0")
            return 1
        source = ReplaySource(path, args.journal_dir, speed=args.speed, max_gap=args.max_gap)
        title = f"ASSIST {__version__} - replay {path.name}"
        store = SurveyStore(data_dir / "replay")
        store.clear()
    else:
        source = LiveSource(args.status, args.journal_dir)
        title = f"ASSIST {__version__} - live"
        store = SurveyStore(data_dir)
        game_window = GameWindow()

    tracker = SurveyTracker(store, pattern)
    window = StatusWindow(source, tracker, title, game_window)
    window.add_log(f"ASSIST {__version__}")
    warning = venv_warning(START_ASSIST)
    if warning:
        window.show_warning(warning, "wrong Python")
    window.add_log(f"Survey files: {shown(store.folder)}")
    # Once, for files from before Milestone 9: archived surveys are named like their map images.
    for old, new in store.rename_archive_files():
        window.add_log(f"Renamed survey file {old} to {new}")
    tracker.report_duplicates()  # the window logs these through its tracker listener
    if tracker.survey:
        window.add_log(f"Continuing the active survey on {tracker.survey.poi.body}")
    window.show()
    source.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
