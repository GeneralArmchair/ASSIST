"""Where Status.json snapshots come from: the live game, or a recording.

Both sources emit the same signals through the same _handle() code, so everything
downstream (the window now, survey logic later) can't tell them apart.
"""

import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .journal import JournalTail, SystemHistory
from .paths import shown
from .status import GAME_DIR, STATUS_PATH, Status, read_status_file

STATUS_POLL_MS = 250
JOURNAL_POLL_MS = 1000


class StatusSource(QObject):
    status_changed = Signal(object)  # a Status, only when it differs from the previous one
    system_changed = Signal(str)     # star system name
    message = Signal(str)            # something worth showing the user
    detail = Signal(str)             # technical notes, shown only in the Advanced changes log

    def __init__(self, parent=None):
        super().__init__(parent)
        self._previous = None
        self._system = None

    def _handle(self, data):
        """Decode one raw Status.json dict and pass it on. None (unreadable) is ignored."""
        if data is None or data == self._previous:
            return
        self._previous = data
        self.status_changed.emit(Status.from_json(data))

    def _set_system(self, system):
        if system and system != self._system:
            self._system = system
            self.system_changed.emit(system)


class LiveSource(StatusSource):
    """Poll Status.json and the journal on timers."""

    def __init__(self, status_path=STATUS_PATH, journal_folder=GAME_DIR, parent=None):
        super().__init__(parent)
        self.status_path = Path(status_path)
        self.journal = JournalTail(journal_folder)
        self._missing_reported = False

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_POLL_MS)
        self._status_timer.timeout.connect(self._read_status)
        self._journal_timer = QTimer(self)
        self._journal_timer.setInterval(JOURNAL_POLL_MS)
        self._journal_timer.timeout.connect(self._read_journal)

    def start(self):
        self.message.emit(f"Live: reading {shown(self.status_path)}")
        self._read_journal()
        if self._system is None:
            self.message.emit(f"No star system found in journal files in {shown(self.journal.folder)}")
        self._read_status()
        self._status_timer.start()
        self._journal_timer.start()

    def _read_status(self):
        if not self.status_path.exists():
            if not self._missing_reported:
                self.message.emit(f"Waiting for {shown(self.status_path)} (is the game running?)")
                self._missing_reported = True
            return
        self._missing_reported = False
        # Half-written or locked reads come back as None and are retried on the next tick.
        self._handle(read_status_file(self.status_path))

    def _read_journal(self):
        self._set_system(self.journal.poll())


class ReplaySource(StatusSource):
    """Play back a recording from record_status.py with its original timing.

    speed: 2 plays twice as fast. Gaps longer than max_gap seconds (e.g. sitting at the
    main menu) are shortened to max_gap. The star system is looked up in the journal
    files by each snapshot's timestamp, so it's correct as long as those journals still exist.
    """

    finished = Signal()
    progress = Signal(int, int)  # snapshots played, total

    def __init__(self, recording_path, journal_folder=GAME_DIR, speed=1.0, max_gap=5.0, parent=None):
        super().__init__(parent)
        self.recording_path = Path(recording_path)
        self.speed = speed
        self.max_gap = max_gap
        self.records = self._load(self.recording_path)
        self.history = SystemHistory(journal_folder)
        self._index = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._play_next)

    @staticmethod
    def _load(path):
        records = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # e.g. a last line cut short when the recorder was closed
                if isinstance(record, dict) and isinstance(record.get("status"), dict):
                    records.append(record)
        return records

    def start(self):
        self.message.emit(f"Replay: {self.recording_path.name}, {len(self.records)} snapshots, "
                          f"speed {self.speed:g}x, gaps capped at {self.max_gap:g} s")
        self._index = 0
        self._timer.start(0)

    def _play_next(self):
        if self._index >= len(self.records):
            self.message.emit("Replay finished")
            self.finished.emit()
            return
        record = self.records[self._index]
        self._set_system(self.history.system_at(record["status"].get("timestamp")))
        self._handle(record["status"])
        self._index += 1
        self.progress.emit(self._index, len(self.records))

        if self._index < len(self.records):
            gap = self.records[self._index].get("t", 0) - record.get("t", 0)
            if gap > self.max_gap:
                if gap - self.max_gap >= 10:
                    self.detail.emit(f"Skipping {gap - self.max_gap:.0f} s of a {gap:.0f} s gap")
                gap = self.max_gap
            self._timer.start(max(0, int(gap / self.speed * 1000)))
        else:
            self._timer.start(0)
