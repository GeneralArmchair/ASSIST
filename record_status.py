"""Record every change to Elite Dangerous' Status.json into a log file.

Usage (from the project folder):
    .venv\\Scripts\\python.exe record_status.py [--full] [path to Status.json]
Press Ctrl+C to stop.

Each line of the log is one JSON object:
    {"t": <seconds since 1970, float>, "status": {...Status.json...}}
Only snapshots that differ from the previous one are written.

By default only the Status.json fields ASSIST uses are kept (assist.status.RECORDED_FIELDS), so a recording sent
with a bug report doesn't carry the player's credit balance, cargo, fuel or legal state. --full keeps everything,
for working on a feature that needs other fields; those files end in _full.jsonl (Milestone 11).
File names carry the start time in UTC, e.g. recording_20261112_140000Z.jsonl (Milestone 13).
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from assist.environment import START_RECORDER, venv_warning
from assist.paths import recordings_folder, shown
from assist.status import STATUS_PATH, recorded_fields

POLL_SECONDS = 0.1

FLAG_SUPERCRUISE = 1 << 4
FLAG_IN_SHIP = 1 << 24
FLAG_IN_SRV = 1 << 26


def read_status(path):
    """Return the parsed Status.json, or None if it's missing or half-written."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else None
    except (OSError, json.JSONDecodeError):
        return None


def describe(status):
    """One short human-readable line, so you can see the recorder is working."""
    flags = status.get("Flags", 0)
    if flags & FLAG_IN_SRV:
        where = "SRV"
    elif flags & FLAG_SUPERCRUISE:
        where = "supercruise"
    elif flags & FLAG_IN_SHIP:
        where = "ship"
    else:
        where = "other"
    if "Latitude" in status:
        pos = (f"lat {status['Latitude']:.5f} lon {status['Longitude']:.5f} "
               f"hdg {status.get('Heading', '?')} alt {status.get('Altitude', '?')} m")
    else:
        pos = "no position"
    return f"{where:<11} {pos}"


FULL_WARNING = ("--full: recording ALL of Status.json, including your credit balance, cargo, fuel and legal "
                "state.\nDon't send this file to anyone; record without --full for a bug report.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Record every change to Status.json into a log file.")
    parser.add_argument("path", nargs="?", type=Path, default=STATUS_PATH, help="path to Status.json")
    parser.add_argument("--full", action="store_true",
                        help="keep every field, including credit balance and cargo (not for sharing)")
    return parser.parse_args(argv)


def recording_name(now, full=False):
    """The file name, from the start time in UTC (the "Z"). A local time in the name next to the UTC times inside
    would give away the player's time zone when a recording is shared (Milestone 13)."""
    return f"recording_{now.astimezone(timezone.utc):%Y%m%d_%H%M%S}Z{'_full' if full else ''}.jsonl"


def record_line(now, status, full=False):
    """One line of the recording (without the newline)."""
    return json.dumps({"t": now, "status": status if full else recorded_fields(status)})


def main():
    warning = venv_warning(START_RECORDER)
    if warning:
        print(warning + "\n")
    args = parse_args()
    path = args.path
    if not path.exists():
        print(f"Can't find {shown(path)}")
        print("Pass the full path to Status.json as an argument if your Saved Games folder is elsewhere.")
        return 1

    folder = recordings_folder()
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / recording_name(datetime.now(timezone.utc), args.full)
    if args.full:
        print(FULL_WARNING + "\n")
    print(f"Reading   {shown(path)}")
    print(f"Recording {shown(out_path)}")
    print("Press Ctrl+C to stop.\n")

    previous = None
    count = 0
    with out_path.open("w", encoding="utf-8") as out:
        try:
            while True:
                status = read_status(path)
                if status is not None and status != previous:
                    now = time.time()
                    out.write(record_line(now, status, args.full) + "\n")
                    out.flush()  # so nothing is lost if the window is closed
                    previous = status
                    count += 1
                    print(f"{datetime.fromtimestamp(now):%H:%M:%S}  {describe(status)}")
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            pass
    print(f"\nStopped. {count} snapshots saved to {shown(out_path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
