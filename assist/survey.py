"""Survey data model, the survey pattern, and saving surveys to disk.

A Survey is one POI plus everything recorded there: pings, waypoints and deposits.
No Qt in here, so it can be tested on its own.
"""

import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, geo

# 2: "assist_version" added, and the state "finished" renamed "closed".
# 3: where and when the golden-ring radius was typed (poi.radius_time, radius_lat, radius_lon).
# 4: deposit density, and survey points ASSIST skipped on its own (waypoint.auto_skip).
# Older files still load.
FORMAT_VERSION = 4

# Survey states.
ACTIVE = "active"
UNFINISHED = "unfinished"  # left the POI; deploying near it again resumes it
CLOSED = "closed"          # the player said it's done; never resumed until re-opened
OLD_FINISHED = "finished"  # what format 1 called CLOSED

# Ping kinds. Every kind except NO_PING counts towards marking points done.
AUTO = "auto"            # hardpoints flag (bit 6) came on in the SRV
MANUAL = "manual"        # Manual ping button
NO_PING = "no_ping"      # deployed and boarded again without bit 6: "no ping detected"
CONFIRMED = "confirmed"  # a NO_PING counted by the "Count as ping" button, removed in 0.10.1;
                        # kept so older files still load and count

PING_KIND_NAMES = {AUTO: "ping", MANUAL: "manual ping", NO_PING: "no ping detected",
                   CONFIRMED: "confirmed ping"}

# Waypoint kinds.
SURVEY_POINT = "survey"
DEPOSIT = "deposit"
CUSTOM = "custom"  # the player's custom destination: a navigation aid only, never saved (Milestone 8)
CUSTOM_LABEL = "Custom"

# How a deposit's position was recorded.
SIGHTED = "sighted"  # range and heading from where the player stood
TYPED = "typed"      # Manual entry

MAX_RANGE_KM = 20       # a longer deposit range (or POI radius) is rejected as a typing mistake
USUAL_RADIUS_KM = (4.0, 8.0)  # a typed POI radius outside this is unusual (4.3-7.05 km seen so far): warn
MAX_SIZE = 6            # deposit sizes run from 1 to this
DENSITIES = ("High", "Medium", "Low")  # a deposit's density, seen at long range (Armchair)
SCAN_RANGE = 2000.0     # m: the Rhino's deposit scanner range

# Automatic skipping (Armchair, 2026-11-08): once the radius is known, a survey point whose scan would add less
# than this share of the POI to the coverage is skipped. See Survey.auto_skip.
AUTO_SKIP_BELOW = 0.01
AUTO_SKIPPED = "skipped"  # Waypoint.auto_skip: ASSIST skipped it, and may unskip it again
KEPT = "kept"             # the player unskipped a point ASSIST had skipped: ASSIST leaves it alone from then on
DEPOSIT_TOLERANCE = 500.0  # m: the landing circle when flying to a deposit, the same as survey points
CUSTOM_TOLERANCE = 500.0   # m: the landing circle at a custom destination, the same again


@dataclass
class PatternPoint:
    label: str
    bearing: float     # degrees clockwise from north, from the POI center
    distance: float    # meters from the POI center
    tolerance: float   # any ping this close marks the point done, even if aimed elsewhere
    tier: int          # lower tiers are visited first


@dataclass
class Pattern:
    points: list[PatternPoint]
    # The first ping of a deployment completes the point the player was heading for,
    # however far off it lands, as long as it's within this many meters of it.
    attempt_distance: float = 1500.0


def load_pattern(path):
    """The survey pattern from a JSON config file (see pattern.json)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    points = [PatternPoint(label=str(p["label"]), bearing=float(p["bearing"]),
                           distance=float(p["distance"]), tolerance=float(p["tolerance"]),
                           tier=int(p.get("tier", 1)))
              for p in data["points"]]
    if not points:
        raise ValueError("the pattern has no points")
    return Pattern(points, float(data.get("attempt_distance", Pattern.attempt_distance)))


@dataclass
class Ping:
    id: int
    time: str | None  # game UTC timestamp
    lat: float
    lon: float
    kind: str
    point: str | None = None  # label of the point the player was heading for, if this ping completes it

    @property
    def counts(self):
        return self.kind != NO_PING


@dataclass
class Waypoint:
    """Anything to fly to. Survey points are stored; a deposit's is made from it when asked for."""

    label: str
    lat: float
    lon: float
    kind: str = SURVEY_POINT
    tier: int = 1
    tolerance: float = 0.0
    skipped: bool = False
    auto_skip: str | None = None  # AUTO_SKIPPED, KEPT, or None: see Survey.auto_skip


@dataclass
class Deposit:
    id: int
    ore: str
    time: str | None  # game UTC timestamp, at the hotkey press for a sighted deposit
    lat: float        # estimated position, or the typed one for a TYPED deposit
    lon: float
    kind: str = SIGHTED
    # What a sighted deposit's position was worked out from.
    from_lat: float | None = None
    from_lon: float | None = None
    heading: int | None = None
    range_km: float | None = None
    density: str | None = None  # one of DENSITIES, seen from afar; None if not recorded
    # Filled in after visiting it, typed from the HUD.
    size: int | None = None
    measured_lat: float | None = None
    measured_lon: float | None = None

    @property
    def label(self):
        return f"D{self.id} {self.ore}"

    @property
    def measured(self):
        return self.measured_lat is not None and self.measured_lon is not None

    @property
    def position(self):
        """(lat, lon), best known: measured always wins over the estimate."""
        return (self.measured_lat, self.measured_lon) if self.measured else (self.lat, self.lon)


def _number(text):
    """A typed number; a decimal comma is fine. Raises ValueError."""
    return float(text.strip().replace(",", "."))


def parse_range_km(text):
    """A typed deposit range in km. Raises ValueError with a message for the player."""
    try:
        km = _number(text)
    except ValueError:
        raise ValueError("Type the range in km, such as 2.37") from None
    if km > MAX_RANGE_KM:
        raise ValueError(f"Range rejected: more than {MAX_RANGE_KM} km")
    if not km > 0:
        raise ValueError("Type the range in km, such as 2.37")
    return km


def parse_radius_km(text):
    """A typed golden-ring radius in km; blank means "not measured" (None). Raises ValueError with a message."""
    if not text.strip():
        return None
    try:
        km = _number(text)
    except ValueError:
        raise ValueError("Type the range to the POI center in km, such as 5.12") from None
    if km > MAX_RANGE_KM:
        raise ValueError(f"Radius rejected: more than {MAX_RANGE_KM} km")
    if not km > 0:
        raise ValueError("Type the range to the POI center in km, such as 5.12")
    return km


def unusual_radius(km):
    """Whether a typed radius is outside the usual range, worth a second look."""
    low, high = USUAL_RADIUS_KM
    return not low <= km <= high


def parse_lat_lon(lat_text, lon_text):
    """Typed latitude and longitude in degrees. Raises ValueError with a message for the player."""
    try:
        lat, lon = _number(lat_text), _number(lon_text)
    except ValueError:
        raise ValueError("Type both latitude and longitude in degrees, such as -12.3456") from None
    if not -90 <= lat <= 90:
        raise ValueError("Latitude must be between -90 and 90")
    if not -180 <= lon <= 180:
        raise ValueError("Longitude must be between -180 and 180")
    return lat, lon


@dataclass
class POI:
    system: str | None
    body: str
    planet_radius: float
    center_lat: float
    center_lon: float
    name: str | None = None      # most recent mining-signal nav target on this body
    radius: float | None = None  # golden-ring radius in m, typed from the HUD; None until measured
    ground_height: float | None = None  # m from the average radius, from the latest ground touch here
    # Where and when the radius was typed, as data only: the player is on the ring then.
    radius_time: str | None = None
    radius_lat: float | None = None
    radius_lon: float | None = None

    @property
    def radius_measured(self):
        return self.radius is not None


@dataclass
class Survey:
    id: str
    poi: POI
    created: str | None
    updated: str | None = None
    state: str = ACTIVE
    waypoints: list[Waypoint] = field(default_factory=list)
    pings: list[Ping] = field(default_factory=list)
    target: str | None = None    # label of the selected waypoint
    deposits: list[Deposit] = field(default_factory=list)
    # The custom destination while it's the target. Never saved: to_dict leaves it out.
    custom: Waypoint | None = field(default=None, compare=False)

    @classmethod
    def new(cls, poi, pattern, time):
        survey = cls(id=make_survey_id(time, poi.body), poi=poi, created=time, updated=time)
        for point in pattern.points:
            lat, lon = geo.destination(poi.center_lat, poi.center_lon, point.bearing,
                                       point.distance, poi.planet_radius)
            survey.waypoints.append(Waypoint(label=point.label, lat=lat, lon=lon, tier=point.tier,
                                             tolerance=point.tolerance))
        return survey

    # --- geometry -------------------------------------------------------------------------

    def distance(self, lat1, lon1, lat2, lon2):
        return geo.distance(lat1, lon1, lat2, lon2, self.poi.planet_radius)

    def distance_from_center(self, lat, lon):
        return self.distance(self.poi.center_lat, self.poi.center_lon, lat, lon)

    def east_north(self, lat, lon):
        """(east, north) in meters from the center: great-circle distance along the bearing from the center."""
        d = self.distance_from_center(lat, lon)
        if d == 0:
            return 0.0, 0.0
        b = math.radians(geo.bearing(self.poi.center_lat, self.poi.center_lon, lat, lon))
        return d * math.sin(b), d * math.cos(b)

    def lat_lon(self, east, north):
        """The position east and north meters from the center: the reverse of east_north."""
        return geo.destination(self.poi.center_lat, self.poi.center_lon,
                               math.degrees(math.atan2(east, north)), math.hypot(east, north), self.poi.planet_radius)

    def center_offset(self):
        """At least how far ASSIST's center is from the game's, in m: the radius was typed standing on the ring.

        None if the radius or where it was typed isn't known.
        """
        poi = self.poi
        if poi.radius is None or poi.radius_lat is None or poi.radius_lon is None:
            return None
        return abs(self.distance_from_center(poi.radius_lat, poi.radius_lon) - poi.radius)

    def set_radius(self, radius_km, time=None, lat=None, lon=None):
        """Set (or with None, clear) the golden-ring radius: a log message."""
        poi = self.poi
        if radius_km is None:
            poi.radius = poi.radius_time = poi.radius_lat = poi.radius_lon = None
            return "POI radius cleared (not measured)"
        poi.radius, poi.radius_time, poi.radius_lat, poi.radius_lon = radius_km * 1000, time, lat, lon
        message = f"POI radius set to {radius_km:.2f} km"
        offset = self.center_offset()
        if offset is not None:
            message += f" (so ASSIST's center is at least {offset:,.0f} m from the game's)"
        return message

    # --- waypoints ------------------------------------------------------------------------

    def waypoint(self, label):
        """The survey point, deposit or custom destination with this label, or None."""
        if label == CUSTOM_LABEL:
            return self.custom
        found = next((w for w in self.waypoints if w.label == label), None)
        if found is None:
            deposit = next((d for d in self.deposits if d.label == label), None)
            if deposit:
                found = Waypoint(deposit.label, *deposit.position, kind=DEPOSIT, tolerance=DEPOSIT_TOLERANCE)
        return found

    def set_custom(self, lat, lon):
        """Make (lat, lon) the custom destination and the target."""
        self.custom = Waypoint(CUSTOM_LABEL, lat, lon, kind=CUSTOM, tolerance=CUSTOM_TOLERANCE)
        self.target = CUSTOM_LABEL

    def target_is_custom(self):
        return self.target == CUSTOM_LABEL and self.custom is not None

    def target_is_deposit(self):
        target = self.waypoint(self.target) if self.target else None
        return target is not None and target.kind == DEPOSIT

    @property
    def survey_points(self):
        return [w for w in self.waypoints if w.kind == SURVEY_POINT]

    def completes(self, ping, waypoint):
        """Whether a ping completes a waypoint: it was aimed at it, or landed within its tolerance."""
        if not ping.counts or waypoint.kind != SURVEY_POINT:  # pings have no bearing on a deposit
            return False
        return ping.point == waypoint.label or             self.distance(waypoint.lat, waypoint.lon, ping.lat, ping.lon) <= waypoint.tolerance

    def done_by(self, waypoint):
        """The closest ping that completes the waypoint, or None."""
        pings = [p for p in self.pings if self.completes(p, waypoint)]
        return min(pings, key=lambda p: self.distance(waypoint.lat, waypoint.lon, p.lat, p.lon), default=None)

    def points_for(self, ping):
        """Labels of the survey points a ping completes."""
        return [w.label for w in self.survey_points if self.completes(ping, w)]

    def is_visited(self, waypoint):
        return waypoint.skipped or self.done_by(waypoint) is not None

    def choose_next(self, lat, lon):
        """Label of the nearest unvisited survey point in the lowest tier that has any; None if all visited."""
        unvisited = [w for w in self.survey_points if not self.is_visited(w)]
        if not unvisited:
            return None
        tier = min(w.tier for w in unvisited)
        candidates = [w for w in unvisited if w.tier == tier]
        return min(candidates, key=lambda w: self.distance(lat, lon, w.lat, w.lon)).label

    def progress(self):
        """(done, skipped, total) survey points."""
        points = self.survey_points
        done = sum(1 for w in points if self.done_by(w) is not None)
        skipped = sum(1 for w in points if w.skipped and self.done_by(w) is None)
        return done, skipped, len(points)

    def tier_progress(self, tier):
        """(every point in the tier is done or skipped, how many of them are skipped and not done)."""
        points = [w for w in self.survey_points if w.tier == tier]
        complete = bool(points) and all(self.is_visited(w) for w in points)
        return complete, sum(1 for w in points if w.skipped and self.done_by(w) is None)

    def auto_skip(self):
        """Skip the survey points not worth visiting, and unskip the ones that are again: (skipped, unskipped) labels.

        Only once the radius is known. A point is skipped when its scan would add less than AUTO_SKIP_BELOW of the
        POI's area, counting the pings so far and the other points still to visit (Armchair, 2026-11-08). Points
        are dropped one at a time, the least useful first, so two points covering the same ground don't both go.
        Points the player skipped, and ones the player unskipped after ASSIST skipped them (KEPT), are left alone.
        """
        points = self.survey_points
        candidates, fixed = [], [self.east_north(p.lat, p.lon) for p in self.pings if p.counts]
        if self.poi.radius is not None:
            for w in points:
                if self.done_by(w) is not None:
                    continue
                if w.auto_skip == KEPT:
                    if not w.skipped:
                        fixed.append(self.east_north(w.lat, w.lon))  # still to visit
                elif not w.skipped or w.auto_skip == AUTO_SKIPPED:
                    candidates.append(w)
        drop = set(low_value_points(self.poi.radius, fixed,
                                    {w.label: self.east_north(w.lat, w.lon) for w in candidates})) if candidates else set()
        skipped, unskipped = [], []
        for w in points:
            if w.label in drop and not w.skipped:
                w.skipped, w.auto_skip = True, AUTO_SKIPPED
                skipped.append(w.label)
            elif w.auto_skip == AUTO_SKIPPED and w.label not in drop:
                w.auto_skip = None
                if w.skipped:
                    w.skipped = False
                    unskipped.append(w.label)
        return skipped, unskipped

    def toggle_skip(self, label):
        """The player skips or unskips a survey point. Unskipping one ASSIST skipped keeps it from then on."""
        waypoint = next(w for w in self.survey_points if w.label == label)
        waypoint.skipped = not waypoint.skipped
        if not waypoint.skipped and waypoint.auto_skip == AUTO_SKIPPED:
            waypoint.auto_skip = KEPT
        return waypoint.skipped

    def next_ping_id(self):
        return max((p.id for p in self.pings), default=0) + 1

    # --- deposits -------------------------------------------------------------------------

    def deposit(self, deposit_id):
        return next((d for d in self.deposits if d.id == deposit_id), None)

    def next_deposit_id(self):
        return max((d.id for d in self.deposits), default=0) + 1

    def nearest_same_ore(self, ore, lat, lon):
        """(deposit, distance) of the closest deposit of this ore, or None."""
        same = [(d, self.distance(*d.position, lat, lon)) for d in self.deposits if d.ore == ore]
        return min(same, key=lambda pair: pair[1], default=None)

    # --- JSON -----------------------------------------------------------------------------

    def to_dict(self):
        data = asdict(self)
        # The custom destination is a navigation aid only and is never saved (Armchair, Milestone 8).
        data.pop("custom")
        if data["target"] == CUSTOM_LABEL:
            data["target"] = None
        data["version"] = FORMAT_VERSION
        data["assist_version"] = __version__  # the version that last wrote the file
        return data

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data.pop("version", None)
        data.pop("assist_version", None)
        data.pop("custom", None)
        if data.get("state") == OLD_FINISHED:
            data["state"] = CLOSED
        data["poi"] = POI(**data["poi"])
        data["waypoints"] = [Waypoint(**w) for w in data.get("waypoints", [])]
        data["pings"] = [Ping(**p) for p in data.get("pings", [])]
        data["deposits"] = [Deposit(**d) for d in data.get("deposits", [])]
        return cls(**data)


def low_value_points(radius, fixed, candidates, threshold=AUTO_SKIP_BELOW, steps=100):
    """Labels of the candidate points to drop, least useful first.

    radius: the POI's, in m. fixed: (east, north) of scans that happen anyway (pings so far, points kept).
    candidates: {label: (east, north)}. The POI disk is sampled on a grid of steps x steps; a candidate's worth is
    the share of samples only it would cover. The least useful goes while it's under threshold, then the rest are
    weighed again. Ties go to the point farthest from the center. Pure Python: about 0.1 s.
    """
    step = 2 * radius / steps
    reach = SCAN_RANGE ** 2
    labels = list(candidates)
    total = 0
    open_samples = []  # for each sample no fixed scan covers: which candidates would
    for i in range(steps):
        x = -radius + (i + 0.5) * step
        for j in range(steps):
            y = -radius + (j + 0.5) * step
            if x * x + y * y > radius * radius:
                continue
            total += 1
            if any((x - fx) ** 2 + (y - fy) ** 2 <= reach for fx, fy in fixed):
                continue
            covering = [k for k, (cx, cy) in candidates.items() if (x - cx) ** 2 + (y - cy) ** 2 <= reach]
            if covering:
                open_samples.append(covering)
    alive, dropped = set(labels), []
    while alive:
        only = dict.fromkeys(alive, 0)
        for covering in open_samples:
            left = [k for k in covering if k in alive]
            if len(left) == 1:
                only[left[0]] += 1
        worst = min(alive, key=lambda k: (only[k], -math.hypot(*candidates[k]), k))
        if only[worst] >= threshold * total:
            break
        alive.remove(worst)
        dropped.append(worst)
    return dropped


FILE_NAME_LIMIT = 150  # characters in a map image's or survey file's name: well inside Windows' 260 for the whole path
_NOT_IN_FILE_NAMES = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def file_name(survey, extension):
    """'LFT 509 B 3 b - Signal (5) - 2026-11-08 07-36' plus the extension: the body, the POI shortened to "Signal (n)",
    the date and time in UTC. Used for map images (Armchair, 2026-11-08 and 09) and archived survey files (Milestone 9).
    Body first, so one body's files sort together; the time keeps names apart.
    Characters Windows doesn't allow become '-'; the body and POI are shortened to keep within FILE_NAME_LIMIT.
    """
    def clean(text):
        return _NOT_IN_FILE_NAMES.sub("-", text).strip(" .")

    body = clean(survey.poi.body or "") or "unknown body"
    # Everything before "Signal" goes: "Planetary Mining Location Signal (10)" becomes "Signal (10)".
    poi = clean(re.sub(r"^.*?\bsignal\b", "Signal", survey.poi.name or "", count=1, flags=re.IGNORECASE))
    try:
        when = f"{datetime.fromisoformat(survey.created).astimezone(timezone.utc):%Y-%m-%d %H-%M}"
    except (TypeError, ValueError):
        when = "undated"
    tail = f" - {when}{extension}"
    room = FILE_NAME_LIMIT - len(tail)
    if poi:
        body = body[:max(20, room - len(poi) - 3)].rstrip(" .")
        poi = poi[:room - len(body) - 3].rstrip(" .")
        return f"{body} - {poi}{tail}"
    return f"{body[:room].rstrip(' .')}{tail}"


def make_survey_id(time, body):
    """A file-name-safe id like '2026-11-03T07-36-14_LFT-509-B-3-b'."""
    stamp = re.sub(r"[^0-9T]+", "-", (time or "unknown").rstrip("Z")).strip("-")
    name = re.sub(r"[^A-Za-z0-9]+", "-", body or "unknown").strip("-")
    return f"{stamp}_{name}"


class SurveyStore:
    """Survey files in one folder: active.json, plus archive\\<name>.json for the rest.

    Archived files are named like the map images (file_name; Milestone 9). A survey is found by the id inside its
    file, not by its file name, so a name can change without breaking anything.
    """

    def __init__(self, folder):
        self.folder = Path(folder)
        self.active_path = self.folder / "active.json"
        self.archive_dir = self.folder / "archive"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._paths = None  # survey id -> archive file, read when first needed and again on rescan()
        self._duplicates = []  # (skipped file name, kept file name) from the latest read of the folder
        self._reported = set()  # skipped file names already returned by new_duplicates()

    @staticmethod
    def _write(path, survey):
        # Write to a temporary file and swap it in, so a crash mid-write can't leave half a file.
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(survey.to_dict(), indent=1), encoding="utf-8")
        os.replace(temp, path)

    @staticmethod
    def _read(path):
        try:
            return Survey.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _index(self):
        """{survey id: path} for every readable archive file."""
        if self._paths is None:
            self._scan()
        return self._paths

    def _scan(self):
        """Read the archive folder. Two files holding the same survey (the same id, e.g. a copy received back from
        another player): the most recently updated one is used, the other skipped and listed in _duplicates."""
        found = {}  # id -> (survey, path)
        self._duplicates = []
        for path in sorted(self.archive_dir.glob("*.json")):
            survey = self._read(path)
            if not survey:
                continue
            other = found.get(survey.id)
            if other is None:
                found[survey.id] = (survey, path)
                continue
            # Ties keep the first by name, so the choice doesn't change from one read to the next.
            keep, skip = ((survey, path), other) if (survey.updated or "") > (other[0].updated or "")                 else (other, (survey, path))
            found[survey.id] = keep
            self._duplicates.append((skip[1].name, keep[1].name))
        self._paths = {survey_id: path for survey_id, (_, path) in found.items()}

    def rescan(self):
        """Read the archive folder again, so files copied in while ASSIST runs are found (Milestone 11)."""
        self._scan()

    def new_duplicates(self):
        """(skipped file name, kept file name) for each duplicate survey file not returned before."""
        new = [pair for pair in self._duplicates if pair[0] not in self._reported]
        self._reported.update(skipped for skipped, _ in new)
        return new

    def _archive_path(self, survey):
        """Where this survey's archive file belongs: its file_name, with " (2)" and so on if another survey has it."""
        mine = self._index().get(survey.id)
        taken = {path for survey_id, path in self._index().items() if survey_id != survey.id}
        stem = file_name(survey, "")
        path, n = self.archive_dir / f"{stem}.json", 1
        while path in taken or (path != mine and path.exists()):
            n += 1
            path = self.archive_dir / f"{stem} ({n}).json"
        return path

    def _write_archived(self, survey):
        old = self._index().get(survey.id)
        path = self._archive_path(survey)
        self._write(path, survey)
        if old and old != path:
            old.unlink(missing_ok=True)  # renamed: its POI name or date changed
        self._paths[survey.id] = path

    def rename_archive_files(self):
        """Give every archive file its file_name (once, for files from before Milestone 9). Returns (old, new) names."""
        renamed = []
        for survey_id, old in sorted(self._index().items(), key=lambda item: item[1].name):
            survey = self._read(old)
            new = self._archive_path(survey) if survey else old
            if new != old:
                os.replace(old, new)
                self._paths[survey_id] = new
                renamed.append((old.name, new.name))
        return renamed

    def load_active(self):
        if not self.active_path.exists():
            return None
        return self._read(self.active_path)

    def save_active(self, survey):
        self._write(self.active_path, survey)

    def archive(self, survey):
        """Move the survey from active.json to the archive (its state should already be set)."""
        self._write_archived(survey)
        self.active_path.unlink(missing_ok=True)

    def archived(self):
        """All archived surveys that can be read, closed and unfinished."""
        surveys = []
        for path in sorted(self._index().values()):
            survey = self._read(path)
            if survey:
                surveys.append(survey)
        return surveys

    def unfinished(self):
        """All unfinished archived surveys."""
        return [s for s in self.archived() if s.state == UNFINISHED]

    def archive_path(self, survey_id):
        """The archive file of the survey with this id, or None."""
        path = self._index().get(survey_id)
        return path if path and path.exists() else None

    def load_archived(self, survey_id):
        """The archived survey with this id, read fresh from its file, or None if it isn't in the archive."""
        path = self.archive_path(survey_id)
        return self._read(path) if path else None

    def save_archived(self, survey):
        """Write a change to a survey that stays in the archive."""
        self._write_archived(survey)

    def take_from_archive(self, survey):
        """Make an archived survey the active one again."""
        survey.state = ACTIVE
        self.save_active(survey)
        path = self._index().pop(survey.id, None)
        if path:
            path.unlink(missing_ok=True)

    def clear(self):
        """Delete this folder's survey files (only used for the replay folder)."""
        self.active_path.unlink(missing_ok=True)
        for path in self.archive_dir.glob("*.json"):
            path.unlink()
        self._paths = None
