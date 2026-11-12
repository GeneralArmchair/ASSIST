"""Read the star system name from the game's journal files (read-only).

The journal is one JSON object per line. The system comes from the most recent
Location / FSDJump / CarrierJump event's StarSystem.
"""

import bisect
import json
from pathlib import Path

from .status import GAME_DIR

SYSTEM_EVENTS = {"Location", "FSDJump", "CarrierJump"}


def journal_files(folder=GAME_DIR):
    """All journal files in folder, oldest first (by modification time)."""
    files = Path(folder).glob("Journal.*.log")
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


def system_events(lines):
    """Yield (timestamp, star system) for each system event in an iterable of byte lines."""
    for line in lines:
        if b'"StarSystem"' not in line:
            continue  # cheap pre-filter: most lines aren't system events
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if event.get("event") in SYSTEM_EVENTS and event.get("StarSystem"):
            yield event.get("timestamp", ""), event["StarSystem"]


def last_system_in(path):
    """The star system from the last system event in one journal file, or None."""
    system = None
    try:
        with open(path, "rb") as f:
            for _, system in system_events(f):
                pass
    except OSError:
        return None
    return system


class JournalTail:
    """Follow the newest journal file and keep track of the current star system.

    Call poll() on a timer. Each call reads only what the game appended since the last call.
    """

    def __init__(self, folder=GAME_DIR):
        self.folder = Path(folder)
        self.path = None
        self.offset = 0
        self.partial = b""  # an incomplete last line, finished on a later poll
        self.system = None

    def poll(self):
        """Read new journal lines; return the current star system (None if not known yet)."""
        try:
            files = journal_files(self.folder)
        except OSError:
            return self.system
        if not files:
            return self.system

        newest = files[-1]
        first_read = False
        if newest != self.path:
            # First start, or the game was restarted and began a new file.
            self.path, self.offset, self.partial = newest, 0, b""
            first_read = True

        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return self.system
        self.offset += len(data)

        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        for _, system in system_events(lines):
            self.system = system

        if first_read and self.system is None:
            # A fresh game session has no system event until the player loads in;
            # fall back to the newest older file that has one.
            for older in reversed(files[:-1]):
                self.system = last_system_in(older)
                if self.system:
                    break
        return self.system


class SystemHistory:
    """Every system event from all journal files, for looking up the system at a past time (replay)."""

    def __init__(self, folder=GAME_DIR):
        events = []
        try:
            files = journal_files(folder)
        except OSError:
            files = []
        for path in files:
            try:
                with open(path, "rb") as f:
                    events.extend(system_events(f))
            except OSError:
                pass
        events.sort()
        self.timestamps = [t for t, _ in events]
        self.systems = [s for _, s in events]

    def system_at(self, timestamp):
        """The star system at a game UTC timestamp like '2026-11-03T07:36:14Z', or None."""
        if not timestamp:
            return None
        # ISO timestamps in the same format sort correctly as plain strings.
        i = bisect.bisect_right(self.timestamps, timestamp)
        return self.systems[i - 1] if i else None
