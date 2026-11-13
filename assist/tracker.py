"""Survey logic: turns the stream of Status snapshots into survey events.

Feed it every decoded Status (update) and the star system name (set_system); the
window's buttons call the action methods. Live and replay feed it the same way.
No Qt in here: the window subscribes with add_listener().

Rules:
- Leaving glide with a mining signal targeted resumes the unfinished survey of that name.
- Deploying the SRV with no active survey resumes a matching unfinished survey, or starts
  a new one centered on the SRV.
- Bit 6 (hardpoints) coming on in the SRV logs a ping. A deployment that ends without one
  is logged as "no ping detected". The first ping of a deployment completes the point the
  player was heading for (within the pattern's attempt distance).
- Boarding the ship selects the nearest unvisited point in the lowest tier left, unless the
  target is a deposit: that stays selected.
- Leaving the POI by any route archives the survey as unfinished.
- Closed surveys are never resumed. Re-opening one makes it unfinished again.
- Deposits of an archived survey can be edited from anywhere, straight in its archive file.
- A sighting of an ore within 400 m of a deposit of the same ore is never saved.
- Manual Mark Deposit asks before adding the same ore within 150 m of a deposit (the form asks; see near_same_ore).
"""

from dataclasses import dataclass

from . import geo
from .guidance import Guide, format_distance, is_ground_contact, signed_angle
from .status import GLIDE, HARDPOINTS, HAS_LAT_LONG, IN_SRV, SRV_HANDBRAKE, SRV_TURRET_VIEW, SUPERCRUISE
from .survey import (AUTO, AUTO_SKIP_BELOW, CLOSED, CUSTOM, CUSTOM_LABEL, DEPOSIT, KM, MANUAL, NO_PING, PING_KIND_NAMES, POI, SIGHTED,
                     SURVEY_POINT, TYPED, UNFINISHED, Deposit, Ping, Survey, format_range)

KEEP = object()  # update_deposit: leave the density as it is
LEAVE_DISTANCE = 10_000   # m from the center, horizontally: farther than this = left the POI
RESUME_DISTANCE = 7_500   # m: deploying this close to an unfinished survey's center resumes it
DUPLICATE_PING = 50       # m: a ping this close to the previous one is ignored
DUPLICATE_DEPOSIT = 150   # m: the same ore this close to a deposit is that deposit
SUSPECTED_DUPLICATE = 400 # m: the same ore this close is probably that deposit, badly estimated
# The SRV counts as still stopped by the handbrake within these of where the handbrake last had it.
# On release it settles by up to 3° and 2 m; driving off shows 10° or 3 m and more (verified in-game).
HANDBRAKE_SETTLE_DEGREES = 3
HANDBRAKE_SETTLE_METERS = 2.5

# What happened to a sighting.
SAVED = "saved"
DUPLICATE = "duplicate"
SUSPECTED = "suspected"
NO_SURVEY = "no_survey"


@dataclass
class Deployment:
    """The SRV deployment in progress."""

    lat: float
    lon: float
    time: str | None
    target: str | None = None  # the point the player was heading for
    pinged: bool = False


@dataclass(frozen=True)
class Sighting:
    """Where the player stood and which way they faced when they pressed the mark deposit hotkey."""

    lat: float
    lon: float
    heading: int
    time: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarkResult:
    outcome: str  # SAVED, DUPLICATE, SUSPECTED, NO_SURVEY
    message: str
    deposit: Deposit | None = None


class SurveyTracker:
    def __init__(self, store, pattern):
        self.store = store
        self.pattern = pattern
        self.survey = store.load_active()
        self.system = None
        self.status = None        # latest Status
        self.deployment = None
        self.nav_target = None    # (body, POI name): latest mining-signal nav target
        # Whether the player has been within LEAVE_DISTANCE of the center since the survey
        # became active. Until then, being far away doesn't count as leaving (a survey resumed
        # at the end of a long glide is still on its way to the POI).
        self.arrived = False
        self.on_ground = False    # the ship is touching the ground
        self.braked = None        # the latest Status in this SRV deployment with the handbrake on
        # Off: bit 6 (HARDPOINTS) is ignored, so only Manual ping marks a scan (Milestone 9). The window sets it.
        self.detect_scans = True
        self.guide = Guide()
        self._listeners = []
        if self.survey and self.survey.target is None:
            # A custom destination isn't saved, so a survey that had one comes back with no target.
            self.survey.target = self.survey.choose_next(self.survey.poi.center_lat, self.survey.poi.center_lon)

    # --- plumbing -------------------------------------------------------------------------

    def add_listener(self, listener):
        """listener(message or None) is called after every survey change; message is for the log."""
        self._listeners.append(listener)

    def remove_listener(self, listener):
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _notify(self, message=None):
        for listener in self._listeners:
            listener(message)

    def _save(self, message=None):
        if self.survey:
            self.survey.updated = self._now()
            self.store.save_active(self.survey)
        self._notify(message)

    def _now(self):
        return self.status.timestamp if self.status else None

    def position(self):
        """(lat, lon) of the player now, or None."""
        if self.status and self.status.has_position:
            return self.status.latitude, self.status.longitude
        return None

    def guidance(self):
        """Guidance to the current target, or None."""
        return self.guide.guidance(self.status, self.survey, self.pattern.attempt_distance, self.on_ground)

    # --- input from the status source -----------------------------------------------------

    def set_system(self, name):
        self.system = name

    def update(self, status):
        if status.flags == 0:
            return  # not in the game (main menu, or the game just started)
        previous, self.status = self.status, status
        self._track_nav_target(status)
        if not status.has(IN_SRV):
            self.braked = None
        elif status.has(SRV_HANDBRAKE) and status.has_position and status.heading is not None:
            self.braked = status

        if self.survey:
            reason = self._left_poi(status)
            if reason:
                self._archive(UNFINISHED, f"Left the POI ({reason})")

        was_on_ground, self.on_ground = self.on_ground, is_ground_contact(status, previous, self.on_ground)
        if self.on_ground and not was_on_ground:
            self._on_ground_touch(status)

        if self.survey is None and previous is not None and not status.has(SUPERCRUISE) and not status.has2(GLIDE):
            if previous.has2(GLIDE):
                self._resume_in_normal_space(status, "on leaving glide")
            elif previous.has(SUPERCRUISE):
                # Dropping out of supercruise with no glide, e.g. high above the POI.
                self._resume_in_normal_space(status, "on leaving supercruise")

        in_srv = status.has(IN_SRV)
        was_in_srv = previous is not None and previous.has(IN_SRV)
        if in_srv and not was_in_srv:
            if previous is None:
                # Started while already in the SRV: don't invent a deployment or a ping,
                # but still catch a ping that hasn't happened yet.
                if status.has_position:
                    self.deployment = Deployment(status.latitude, status.longitude, status.timestamp,
                                                 target=self.survey and self.survey.target,
                                                 pinged=self._hardpoints(status))
            else:
                self._on_deploy(status)
        elif in_srv:
            if self.deployment and not self.deployment.pinged and self._hardpoints(status):
                self.deployment.pinged = True
                if status.has_position:
                    self._log_ping(status.latitude, status.longitude, AUTO, aimed_at=self.deployment.target)
        elif was_in_srv:
            self._on_board(status)

    def _hardpoints(self, status):
        """Bit 6 in the SRV: the first trigger pull of the deployment, taken as its first scan, if detection is on."""
        return self.detect_scans and status.has(HARDPOINTS)

    def kind_name(self, kind):
        """A ping kind for the log: with detection off, a deployment without a ping had "no manual ping"."""
        if kind == NO_PING and not self.detect_scans:
            return "no manual ping"
        return PING_KIND_NAMES[kind]

    def _track_nav_target(self, status):
        if status.body_name and self.nav_target and self.nav_target[0] != status.body_name:
            self.nav_target = None  # a mining signal only names POIs on its own body
        if status.destination_is_mining_signal and status.body_name:
            self.nav_target = (status.body_name, status.destination_name)

    def _nav_name_for(self, body):
        if self.nav_target and self.nav_target[0] == body:
            return self.nav_target[1]
        return None

    def _left_poi(self, status):
        """Why the player has left the active survey's POI, or None if they haven't."""
        if status.has(SUPERCRUISE):
            return "supercruise"
        if not status.has(HAS_LAT_LONG):
            return "no latitude/longitude"
        if status.body_name and status.body_name != self.survey.poi.body:
            return f"body changed to {status.body_name}"
        if status.has_position:
            d = self.survey.distance_from_center(status.latitude, status.longitude)
            if d <= LEAVE_DISTANCE:
                self.arrived = True
            elif self.arrived:
                return f"{d / 1000:.1f} km from the center"
        return None

    def _on_ground_touch(self, status):
        """A flat belly contact gives the terrain's height relative to the average radius here."""
        if not self.survey or not status.has_position or status.body_name != self.survey.poi.body:
            return
        self.survey.poi.ground_height = status.altitude
        d = self.survey.distance_from_center(status.latitude, status.longitude)
        self._save(f"Ground touch, {d / 1000:.1f} km from the center: "
                   f"terrain is {status.altitude:+,.0f} m from the average radius")

    def _on_deploy(self, status):
        if not status.has_position or not status.body_name or not status.planet_radius:
            self._notify("SRV deployed, but the game gave no position or body; nothing logged")
            return
        if self.survey is None:
            self.rescan_archive()  # a survey file copied into archive\ since can be resumed (Milestone 11)
            self._resume_or_start(status)
        self.deployment = Deployment(status.latitude, status.longitude, status.timestamp,
                                     target=self.survey.target)
        if self._hardpoints(status):
            self.deployment.pinged = True
            self._log_ping(status.latitude, status.longitude, AUTO, aimed_at=self.deployment.target)

    def _on_board(self, status):
        deployment, self.deployment = self.deployment, None
        if not self.survey:
            return
        if deployment and not deployment.pinged and not self._is_deposit(deployment.target):
            # A deployment to inspect a deposit doesn't need a ping, so there's nothing to flag.
            self._log_ping(deployment.lat, deployment.lon, NO_PING, time=deployment.time,
                           aimed_at=deployment.target)
        if self.survey.target_is_deposit():
            self._save(f"Target stays on deposit {self.survey.target}")
            return
        if self.survey.target_is_custom():
            self._save("Target stays on the custom destination")
            return
        where = self.position() or (deployment and (deployment.lat, deployment.lon))
        if where:
            self.survey.target = self.survey.choose_next(*where)
        target = self.survey.target
        if target is None:
            message = "All survey points done or skipped"
        elif deployment and target == deployment.target:
            last = self.survey.pings[-1] if self.survey.pings else None
            why = self.kind_name(NO_PING) if last and last.kind == NO_PING else "the ping didn't complete it"
            message = f"Next point: {target} again, it's still open ({why})"
        else:
            message = f"Next point: {target}"
        self._save(message)

    # --- starting, resuming, ending --------------------------------------------------------

    def _candidates(self, body):
        """Unfinished archived surveys on this body (and in this system, when both are known)."""
        return [s for s in self.store.unfinished()
                if s.poi.body == body
                and not (self.system and s.poi.system and s.poi.system != self.system)]

    def _by_nav_name(self, candidates, body):
        """The most recently updated candidate named like the current mining nav target, or None."""
        name = self._nav_name_for(body)
        named = [s for s in candidates if name and s.poi.name == name]
        return max(named, key=lambda s: s.updated or "", default=None)

    def _resume_in_normal_space(self, status, when):
        """Resume only the survey named like the targeted mining signal: no guessing from position here."""
        if not status.body_name:
            return
        survey = self._by_nav_name(self._candidates(status.body_name), status.body_name)
        if survey:
            self._resume(survey, when)

    def _resume_or_start(self, status):
        body = status.body_name
        candidates = self._candidates(body)
        survey = self._by_nav_name(candidates, body)
        if survey is None:
            # No matching name: fall back to the nearest one, unless the nav target names a different POI.
            name = self._nav_name_for(body)
            nearby = [s for s in candidates
                      if not (name and s.poi.name and s.poi.name != name)
                      and s.distance_from_center(status.latitude, status.longitude) <= RESUME_DISTANCE]
            survey = min(nearby, key=lambda s: s.distance_from_center(status.latitude, status.longitude),
                         default=None)
        if survey:
            self._resume(survey, "on deploying")
        else:
            self._start(status)

    def _resume(self, survey, when):
        self.store.take_from_archive(survey)
        self.survey = survey
        self.arrived = False
        if not survey.poi.name:
            survey.poi.name = self._nav_name_for(survey.poi.body)
        where = ""
        position = self.position()
        if position:
            d = survey.distance_from_center(*position)
            self.arrived = d <= LEAVE_DISTANCE
            where = f", {d / 1000:.1f} km from its center"
        self._refresh_target()
        self._save(f"Resumed survey {self._describe(survey)} {when}{where}")

    def _start(self, status):
        poi = POI(system=self.system, body=status.body_name, planet_radius=status.planet_radius,
                  center_lat=status.latitude, center_lon=status.longitude,
                  name=self._nav_name_for(status.body_name))
        self.survey = Survey.new(poi, self.pattern, status.timestamp)
        self.arrived = True
        self._refresh_target()
        self._save(f"Started survey {self._describe(self.survey)}")

    def _archive(self, state, message):
        survey = self.survey
        survey.state = state
        survey.updated = self._now()
        self.store.archive(survey)
        self.survey = None
        self.deployment = None
        if state == UNFINISHED and self.nav_target and self.nav_target[1] == survey.poi.name:
            self.nav_target = None  # that name belongs to the POI just left
        done, skipped, total = survey.progress()
        self._notify(f"{message}: survey {self._describe(survey)} archived as {state}, "
                     f"{done}/{total} points done")

    @staticmethod
    def _describe(survey):
        return survey.poi.name or f"on {survey.poi.body}"

    # --- pings ----------------------------------------------------------------------------

    def _aim(self, lat, lon, label):
        """The point a deployment's first ping completes: the one it was heading for, if within reach."""
        waypoint = self.survey.waypoint(label) if label else None
        if waypoint is None or waypoint.kind != SURVEY_POINT or self.survey.is_visited(waypoint):
            return None
        if self.survey.distance(waypoint.lat, waypoint.lon, lat, lon) > self.pattern.attempt_distance:
            return None
        return label

    def _log_ping(self, lat, lon, kind, time=None, aimed_at=None):
        """Add a ping to the active survey, applying the 50 m rule. Returns True if logged.

        aimed_at: the point the player was heading for, when this is a deployment's first ping.
        """
        survey = self.survey
        time = time or self._now()
        last = survey.pings[-1] if survey.pings else None
        if last and survey.distance(last.lat, last.lon, lat, lon) < DUPLICATE_PING:
            if last.kind == NO_PING and kind != NO_PING:
                # A real ping where a deployment found none: it replaces that entry.
                last.lat, last.lon, last.kind, last.time = lat, lon, kind, time
                if aimed_at:
                    last.point = self._aim(lat, lon, aimed_at)
                self._refresh_target()
                self._save(f"Ping #{last.id} replaces the no-ping entry within {DUPLICATE_PING} m"
                           f"{self._near(last)}")
                return True
            self._notify(f"{self.kind_name(kind).capitalize()} ignored: within {DUPLICATE_PING} m "
                         f"of ping #{last.id}")
            return False
        ping = Ping(id=survey.next_ping_id(), time=time, lat=lat, lon=lon, kind=kind,
                    point=self._aim(lat, lon, aimed_at))
        survey.pings.append(ping)
        self._refresh_target()
        self._save(f"Ping #{ping.id}: {self.kind_name(kind)}{self._near(ping)}")
        return True

    def _near(self, ping):
        """' at I3 (420 m off)' for the points the ping completes, or ' (extra ping)'."""
        if not ping.counts:
            return f" (aimed at {ping.point})" if ping.point else ""
        parts = []
        for label in self.survey.points_for(ping):
            waypoint = self.survey.waypoint(label)
            d = self.survey.distance(waypoint.lat, waypoint.lon, ping.lat, ping.lon)
            parts.append(f"{label} ({d:,.0f} m off)")
        return f" at {', '.join(parts)}" if parts else " (extra ping, completes no point)"

    def _refresh_target(self):
        """Apply the automatic skipping rule; then, if the selected point is done or skipped (or nothing is
        selected), pick the next one."""
        survey = self.survey
        message = _auto_skip(survey)
        if message:
            self._notify(message)
        target = survey.waypoint(survey.target) if survey.target else None
        if target is None or survey.is_visited(target):
            where = self.position() or (survey.poi.center_lat, survey.poi.center_lon)
            survey.target = survey.choose_next(*where)

    # --- button actions -------------------------------------------------------------------

    def manual_ping(self):
        position = self.position()
        if position is None:
            self._notify("Manual ping: no position from the game")
            return
        if self.survey is None:
            if not (self.status.body_name and self.status.planet_radius):
                self._notify("Manual ping: no body from the game")
                return
            self._resume_or_start(self.status)
        first_of_deployment = self.deployment and not self.deployment.pinged
        aimed_at = self.deployment.target if first_of_deployment else None
        if self._log_ping(*position, MANUAL, aimed_at=aimed_at) and self.deployment:
            self.deployment.pinged = True  # no "no ping detected" entry for this deployment

    def undo_last_ping(self):
        if self.survey and self.survey.pings:
            ping = self.survey.pings.pop()
            self._refresh_target()
            self._save(f"Undid ping #{ping.id} ({PING_KIND_NAMES[ping.kind]})")

    def delete_ping(self, ping_id):
        if self.survey:
            self.survey.pings = [p for p in self.survey.pings if p.id != ping_id]
            self._refresh_target()
            self._save(f"Deleted ping #{ping_id}")

    def _ping(self, ping_id):
        return next((p for p in self.survey.pings if p.id == ping_id), None) if self.survey else None

    def assign_to_target(self, ping_id):
        """Make a ping complete the selected point, however far off it is."""
        ping = self._ping(ping_id)
        target = self.survey.target if self.survey else None
        if ping and target and self.survey.target_is_deposit():
            self._notify("Assign to target: only a survey point can be completed by a ping, not a deposit")
        elif ping and target and self.survey.target_is_custom():
            self._notify("Assign to target: only a survey point can be completed by a ping, "
                         "not the custom destination")
        elif ping and target:
            ping.point = target
            self._refresh_target()
            self._save(f"Ping #{ping.id} assigned to {target}{self._near(ping)}")

    def select_target(self, label):
        if self.survey and self.survey.waypoint(label) and self.survey.target != label:
            self.survey.custom = None  # picking another target ends the custom destination
            self.survey.target = label
            self._save(f"Selected {'deposit' if self._is_deposit(label) else 'point'} {label}")

    def step_target(self, step):
        """Select the next (step 1) or previous (step -1) waypoint in list order, deposits last."""
        if not self.survey or not self.survey.waypoints:
            return
        labels = [w.label for w in self.survey.waypoints] + [d.label for d in self.survey.deposits]
        i = labels.index(self.survey.target) if self.survey.target in labels else -step
        self.select_target(labels[(i + step) % len(labels)])

    def toggle_skip(self, label):
        waypoint = self.survey.waypoint(label) if self.survey else None
        if waypoint and waypoint.kind in (DEPOSIT, CUSTOM):
            self._notify("Skip only applies to survey points")
        elif waypoint:
            skipped = self.survey.toggle_skip(label)
            self._refresh_target()
            self._save(f"Point {label} {'skipped' if skipped else 'no longer skipped'}")

    def start_survey(self):
        """Start a new survey centered here; an active one is archived as unfinished."""
        if not self.status or not self.status.has_position or not self.status.body_name \
                or not self.status.planet_radius:
            self._notify("Start survey: no position or body from the game")
            return
        if self.survey:
            self._archive(UNFINISHED, "New survey started")
        self._start(self.status)

    def start_survey_hotkey(self):
        """Start a survey only if none is active: in-game there's no confirmation dialog to see."""
        if self.survey:
            self._notify("Start survey hotkey ignored: a survey is active (use the button to replace it)")
            return
        self.start_survey()

    def close_survey(self):
        if self.survey:
            self._archive(CLOSED, "Closed")

    # --- archived surveys (the Surveys list) --------------------------------------------------

    def archived_surveys(self):
        return self.store.archived()

    def rescan_archive(self):
        """Read the archive folder again (when the Surveys list opens and on deploying with no active survey), and
        log each duplicate survey file once. Returns the log messages."""
        self.store.rescan()
        return self.report_duplicates()

    def report_duplicates(self):
        """Log survey files skipped because another file holds the same survey (once each). Returns the messages."""
        messages = [f"Skipped survey file {skipped}: same survey as {kept}"
                    for skipped, kept in self.store.new_duplicates()]
        for message in messages:
            self._notify(message)
        return messages

    def _change_archived(self, survey_id, change):
        """Apply change(survey) -> log message to an archived survey, read fresh from its file, and save it.

        Reading it fresh means a survey resumed and archived again since the list was drawn isn't
        overwritten with an old copy. One that's active now isn't in the archive, so it's refused.
        """
        survey = self.store.load_archived(survey_id)
        if survey is None:
            self._notify("That survey isn't in the archive any more (it may have been resumed)")
            return None
        message = change(survey)
        if message:
            self.store.save_archived(survey)
            self._notify(f"Archived survey {self._describe(survey)}: {message}")
        return survey

    def reopen(self, survey_id):
        """Make a closed survey unfinished, so returning to its POI resumes it."""
        def change(survey):
            if survey.state != CLOSED:
                return None
            survey.state = UNFINISHED
            return "re-opened, it resumes when you return to its POI"
        self._change_archived(survey_id, change)

    def close_archived(self, survey_id):
        def change(survey):
            if survey.state != UNFINISHED:
                return None
            survey.state = CLOSED
            return "closed"
        self._change_archived(survey_id, change)

    def editor(self, survey_id):
        """Something the deposit forms can use in place of the tracker, for an archived survey."""
        return ArchivedSurvey(self, survey_id)

    # --- deposits -------------------------------------------------------------------------

    def _is_deposit(self, label):
        waypoint = self.survey.waypoint(label) if self.survey and label else None
        return waypoint is not None and waypoint.kind == DEPOSIT

    def can_add_deposit(self):
        """Whether there's a survey to add a deposit to; if not, says so in the log."""
        if self.survey is None:
            self._notify("Deposit: no active survey")
        return self.survey is not None

    def capture_sighting(self):
        """The position and heading right now, with any reason to doubt them, or None if there's no reading."""
        if not self.can_add_deposit():
            return None
        status = self.status
        if status is None or not status.has_position or status.heading is None:
            self._notify("Remote Mark Deposit: no position or heading from the game")
            return None
        warnings = []
        if not self._stopped_by_handbrake(status):
            warnings.append("The handbrake hasn't been used since the SRV last moved: the heading may not be fresh. "
                            "To retake it, cancel, stop with the handbrake and mark again.")
        if status.has(SRV_TURRET_VIEW):
            warnings.append("Turret view is on: the heading is the SRV's, not the turret's.")
        return Sighting(status.latitude, status.longitude, status.heading, status.timestamp, tuple(warnings))

    def _stopped_by_handbrake(self, status):
        """Whether the SRV hasn't moved or turned since the handbrake was last on. The player only taps it to stop."""
        braked = self.braked
        return (braked is not None
                and abs(signed_angle(status.heading, braked.heading)) <= HANDBRAKE_SETTLE_DEGREES
                and self.survey.distance(braked.latitude, braked.longitude, status.latitude, status.longitude)
                <= HANDBRAKE_SETTLE_METERS)

    def manual_position(self):
        """(lat, lon, where) to fill Manual Mark Deposit with, taken when it opens; where is "SRV", "ship" and so on.
        None without a position."""
        status = self.status
        if status is None or not status.has_position:
            return None
        return status.latitude, status.longitude, status.mode

    def near_same_ore(self, ore, lat, lon):
        """(deposit, distance) of a deposit of this ore within 150 m of (lat, lon), or None."""
        nearest = self.survey.nearest_same_ore(ore, lat, lon) if self.survey else None
        return nearest if nearest and nearest[1] <= DUPLICATE_DEPOSIT else None

    def mark_deposit(self, sighting, ore, range_km, density=None, unit=KM):
        """Save a deposit from a sighting, unless the same ore is already recorded within 400 m of it."""
        survey = self.survey
        if survey is None:
            message = "Deposit not saved: no active survey"
            self._notify(message)
            return MarkResult(NO_SURVEY, message)
        lat, lon = geo.destination(sighting.lat, sighting.lon, sighting.heading, range_km * 1000,
                                   survey.poi.planet_radius)
        nearest = survey.nearest_same_ore(ore, lat, lon)
        if nearest and nearest[1] <= SUSPECTED_DUPLICATE:
            other, distance = nearest
            away = format_distance(distance)
            if distance <= DUPLICATE_DEPOSIT:
                outcome, message = DUPLICATE, f"Deposit not saved: duplicate of {other.label}, {away} away"
            else:
                outcome, message = SUSPECTED, (f"Deposit not saved: suspected duplicate of {other.label}, {away} away. "
                                               "Use Manual Mark Deposit if it's really a second deposit.")
            self._notify(message)
            return MarkResult(outcome, message)
        deposit = Deposit(id=survey.next_deposit_id(), ore=ore, time=sighting.time, lat=lat, lon=lon, kind=SIGHTED,
                          from_lat=sighting.lat, from_lon=sighting.lon, heading=sighting.heading, range_km=range_km,
                          density=density)
        survey.deposits.append(deposit)
        message = (f"Deposit {deposit.label} saved: {format_range(range_km, unit)} at {sighting.heading:03d}°"
                   f"{_density_note(density)}")
        self._save(message)
        return MarkResult(SAVED, message, deposit)

    def add_manual_deposit(self, ore, lat, lon, density=None, measured=False):
        """Add a deposit at a typed position; measured if it's the player's position, untouched. No duplicate check
        here: the form asks first, and saying "add" is how real twins get recorded."""
        if not self.can_add_deposit():
            return None
        deposit, message = _add_typed_deposit(self.survey, ore, self._now(), lat, lon, density, measured)
        self._save(message)
        return deposit

    def update_deposit(self, deposit_id, size, measured_lat, measured_lon, density=KEEP):
        """Set a deposit's size and measured position, and its density if given; None clears them."""
        if self.survey:
            message = _update_deposit(self.survey, deposit_id, size, measured_lat, measured_lon, density)
            if message:
                self._save(message)

    def delete_deposit(self, deposit_id):
        deposit = self.survey.deposit(deposit_id) if self.survey else None
        if deposit:
            self.survey.deposits.remove(deposit)
            self._refresh_target()
            self._save(f"Deleted deposit {deposit.label}")

    # --- golden-ring radius ------------------------------------------------------------------

    def radius_position(self):
        """(lat, lon, time) to store with a typed radius: taken when the form opens. (None, None, time) without a position."""
        position = self.position()
        return (*(position or (None, None)), self._now())

    def set_radius(self, radius_km, where=(None, None, None)):
        """Set the active survey's golden-ring radius from the range typed off the HUD; None clears it."""
        if not self.survey:
            self._notify("POI radius: no active survey")
            return
        lat, lon, time = where
        message = self.survey.set_radius(radius_km, time, lat, lon)
        self._refresh_target()
        self._save(message)

    # --- custom destination (Milestone 8): a navigation aid only, never saved ---------------

    def set_custom(self, lat, lon):
        """Guide the player to (lat, lon) until another target is picked, it's cleared, or the survey ends."""
        if not self.survey:
            self._notify("Custom destination: no active survey")
            return False
        self.survey.set_custom(lat, lon)
        away = format_distance(self.survey.distance_from_center(lat, lon))
        self._save(f"Custom destination: {lat:.4f}, {lon:.4f} ({away} from the center)")
        return True

    def clear_custom(self):
        survey = self.survey
        if not survey or not survey.target_is_custom():
            self._notify("Clear custom destination: there isn't one")
            return
        survey.custom = None
        where = self.position() or (survey.poi.center_lat, survey.poi.center_lon)
        survey.target = survey.choose_next(*where)
        self._save(f"Custom destination cleared; next point: {survey.target or 'none (all done or skipped)'}")

    def fly_to_deposit(self, deposit_id):
        deposit = self.survey.deposit(deposit_id) if self.survey else None
        if deposit:
            self.select_target(deposit.label)


def _auto_skip(survey):
    """Apply Survey.auto_skip: a log message, or None if nothing changed."""
    skipped, unskipped = survey.auto_skip()
    parts = []
    if skipped:
        parts.append(f"Skipped {', '.join(skipped)}: each would add under {AUTO_SKIP_BELOW:.0%} to the coverage")
    if unskipped:
        parts.append(f"No longer skipped: {', '.join(unskipped)}")
    if not parts:
        return None
    radius = f" (radius {survey.poi.radius / 1000:.2f} km)" if survey.poi.radius else " (radius cleared)"
    return "; ".join(parts) + radius


def _density_note(density):
    return f", density {density}" if density else ""


def _add_typed_deposit(survey, ore, time, lat, lon, density=None, measured=False):
    """Manual Mark Deposit: (the new deposit, a log message). Measured means it's where the player was."""
    deposit = Deposit(id=survey.next_deposit_id(), ore=ore, time=time, lat=lat, lon=lon, kind=TYPED, density=density)
    if measured:
        deposit.measured_lat, deposit.measured_lon = lat, lon
    survey.deposits.append(deposit)
    return deposit, (f"Deposit {deposit.label} added by Manual Mark Deposit at {lat:.4f}, {lon:.4f}"
                     f"{' (measured: your position)' if measured else ''}{_density_note(density)}")


def _update_deposit(survey, deposit_id, size, measured_lat, measured_lon, density=KEEP):
    """Set a deposit's size and measured position (and density, unless KEEP): a log message, or None if there's
    no such deposit."""
    deposit = survey.deposit(deposit_id)
    if deposit is None:
        return None
    deposit.size, deposit.measured_lat, deposit.measured_lon = size, measured_lat, measured_lon
    if density is not KEEP:
        deposit.density = density
    measured = f"{measured_lat:.4f}, {measured_lon:.4f}" if deposit.measured else "none"
    return (f"Deposit {deposit.label}: size {size or 'blank'}, density {deposit.density or 'blank'}, "
            f"measured position {measured}")


class ArchivedSurvey:
    """Stands in for the tracker in the deposit forms, for a survey in the archive.

    Each change reads the file fresh, applies itself and writes the file back (see _change_archived).
    """

    def __init__(self, tracker, survey_id):
        self.tracker = tracker
        self.survey_id = survey_id

    def add_manual_deposit(self, ore, lat, lon, density=None, measured=False):
        added = []

        def change(survey):
            deposit, message = _add_typed_deposit(survey, ore, self.tracker._now(), lat, lon, density, measured)
            added.append(deposit)
            return message
        self.tracker._change_archived(self.survey_id, change)
        return added[0] if added else None

    def update_deposit(self, deposit_id, size, measured_lat, measured_lon, density=KEEP):
        self.tracker._change_archived(
            self.survey_id,
            lambda survey: _update_deposit(survey, deposit_id, size, measured_lat, measured_lon, density))

    def set_radius(self, radius_km, where=(None, None, None)):
        """An archived survey's radius, typed at the desk: there's no position on its ring to store then."""
        def change(survey):
            message = survey.set_radius(radius_km, self.tracker._now())
            skipping = _auto_skip(survey)
            return f"{message}. {skipping}" if skipping else message
        self.tracker._change_archived(self.survey_id, change)

    def delete_deposit(self, deposit_id):
        def change(survey):
            deposit = survey.deposit(deposit_id)
            if deposit is None:
                return None
            survey.deposits.remove(deposit)
            if survey.target == deposit.label:
                survey.target = None  # chosen again when it's resumed
            return f"Deleted deposit {deposit.label}"
        self.tracker._change_archived(self.survey_id, change)
