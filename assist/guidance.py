"""Guidance to the target point: turn direction, bearing, pitch and the deploy cue.

No Qt in here: the plain window shows a Guidance now, the overlay will show the same one later.
Angles are degrees. Signs: heading error positive = target is to the right;
pitch negative = target is below the horizon (nose down).

Altitude (verified in-game):
- Pitch uses the altitude as the game reports it and takes the target's height as zero. With bit 29 off
  that means level with the terrain under the ship, so pitch shifts when the terrain below does.
- Bit 29 on in normal flight means the ship's belly is flat on the ground, and the altitude is the
  terrain's height relative to average radius (e.g. -1860). It isn't a flying height. A touch with
  just the bow or stern doesn't do this: bit 29 stays off and altitude reads a small positive number.
- A proper landing sets the landed flag and altitude reads 0, with bit 29 off: no terrain height.
"""

import math
from dataclasses import dataclass

from . import geo
from .status import ALTITUDE_FROM_AVERAGE_RADIUS, GLIDE, IN_SHIP, IN_SRV, LANDED, SUPERCRUISE
from .survey import CUSTOM, SURVEY_POINT

DEPLOY_ALTITUDE = 28     # m above terrain: highest hover that shows the cue (the game's limit is 30)
OVER_POINT = 50          # m: closer than this, the direction to the point is meaningless
MIN_ON_COURSE = 1.0      # degrees: narrowest "on course" band
CONTACT_MAX_ALTITUDE = 30_000     # m: larger readings aren't terrain (the respawn after SRV death is 218 km)
CONTACT_MAX_BEFORE = 2_000        # m: flying height just before a touch (a lithobrake arrives fast)

# Deploy cue states.
READY = "ready"            # inside the circle, low enough
OFF_TARGET = "off_target"  # low enough, outside the circle but close enough to complete the point
NOT_READY = "not_ready"


def signed_angle(to_deg, from_deg):
    """The turn from one direction to another, in the range -180 (exclusive) to 180."""
    turn = (to_deg - from_deg) % 360
    return turn - 360 if turn > 180 else turn


def pitch_to(distance, ship_height, target_height, radius):
    """Angle from the ship's horizon to the target, from their 3D positions on the sphere.

    distance: great-circle meters along the surface at the average radius.
    Heights are meters above the average radius.
    """
    angle = distance / radius
    ship = radius + ship_height
    target = radius + target_height
    return math.degrees(math.atan2(target * math.cos(angle) - ship, target * math.sin(angle)))


def is_ground_contact(status, previous, was_contact):
    """Whether the ship is flat on the ground: bit 29 on in normal flight, see the module docstring."""
    if not status.has(IN_SHIP) or not status.has(ALTITUDE_FROM_AVERAGE_RADIUS):
        return False
    if status.has(SUPERCRUISE) or status.has2(GLIDE) or status.altitude is None:
        return False
    if abs(status.altitude) > CONTACT_MAX_ALTITUDE:
        return False
    if previous is None or was_contact:
        return True  # started the tool on the ground, or still there
    return (previous.has(IN_SHIP) and not previous.has(ALTITUDE_FROM_AVERAGE_RADIUS)
            and previous.altitude is not None and previous.altitude <= CONTACT_MAX_BEFORE)


@dataclass(frozen=True)
class Guidance:
    label: str
    distance: float
    bearing: float
    heading_error: float | None   # None: no heading, or over the point
    on_course: bool               # the ship's heading line passes through the landing circle
    inside_circle: bool
    pitch: float | None
    pitch_stale: bool             # the last known pitch, not a live one
    cue: str | None               # READY, OFF_TARGET, NOT_READY; None in the SRV
    cue_text: str
    forward: float | None = None  # m to the target along the ship's heading, negative = behind; None: no heading
    right: float | None = None    # m to the target across the heading, negative = to the left
    height: float | None = None   # m above the terrain, 0 on the ground; None: not known (bit 29 in flight, no altitude)


class Guide:
    """Works out guidance, remembering the last pitch to show while there's no live one."""

    def __init__(self):
        self._last_pitch = None   # (target label, pitch)

    def guidance(self, status, survey, attempt_distance, on_ground):
        """Guidance to the survey's target, or None if there's no target or no position."""
        target = survey.waypoint(survey.target) if survey and survey.target else None
        if target is None or status is None or not status.has_position:
            return None
        lat, lon = status.latitude, status.longitude
        distance = survey.distance(lat, lon, target.lat, target.lon)
        bearing = geo.bearing(lat, lon, target.lat, target.lon)
        over_point = distance < OVER_POINT
        on_ground = on_ground or status.has(LANDED)  # the Rhino can't deploy from either, only a hover
        heading_error = None
        if status.heading is not None and not over_point:
            heading_error = signed_angle(bearing, status.heading)
        band = max(MIN_ON_COURSE, math.degrees(math.atan2(target.tolerance, distance))) if distance else 180
        on_course = (distance <= target.tolerance or
                     (heading_error is not None and abs(heading_error) <= band))
        pitch, stale = self._pitch(status, survey, target.label, distance, on_ground, over_point)
        # Only an unvisited survey point can be completed by an off-target ping. The custom destination
        # completes nothing, but shows MISS the same way (Armchair, Milestone 8).
        can_complete = target.kind == SURVEY_POINT and not survey.is_visited(target)
        cue, cue_text = _deploy_cue(status, distance, target.tolerance,
                                    attempt_distance if can_complete or target.kind == CUSTOM else None, on_ground)
        if target.kind == CUSTOM and cue_text.startswith("Hover OK"):
            cue_text = f"Hover OK, {format_distance(distance - target.tolerance)} outside the circle"
        forward = right = None
        if status.heading is not None:  # unlike heading_error, this stays meaningful over the point
            angle = math.radians(bearing - status.heading)
            forward, right = distance * math.cos(angle), distance * math.sin(angle)
        height = 0 if on_ground else None if status.has(ALTITUDE_FROM_AVERAGE_RADIUS) else status.altitude
        return Guidance(target.label, distance, bearing, heading_error, on_course,
                        distance <= target.tolerance, pitch, stale, cue, cue_text, forward, right, height)

    def _pitch(self, status, survey, label, distance, on_ground, over_point):
        """(pitch, stale)."""
        if status.has(IN_SRV) or over_point:
            self._last_pitch = None
            return None, False
        if status.altitude is None or on_ground:
            if self._last_pitch and self._last_pitch[0] == label:
                return self._last_pitch[1], True
            return None, False
        pitch = pitch_to(distance, status.altitude, 0.0, survey.poi.planet_radius)
        self._last_pitch = (label, pitch)
        return pitch, False


def _deploy_cue(status, distance, tolerance, attempt_distance, on_ground):
    """(cue, text). attempt_distance None: an off-target ping wouldn't complete this point."""
    if status.has(IN_SRV):
        return None, "In the SRV"
    if on_ground:
        return NOT_READY, ("Inside the circle" if distance <= tolerance
                           else f"{format_distance(distance - tolerance)} to the circle")
    low = (status.altitude is not None and status.altitude <= DEPLOY_ALTITUDE
           and not status.has(ALTITUDE_FROM_AVERAGE_RADIUS))
    if distance <= tolerance:
        if low:
            return READY, "DEPLOY READY"
        return NOT_READY, f"Inside the circle: descend to {DEPLOY_ALTITUDE} m"
    if low:
        if attempt_distance is None:
            return NOT_READY, f"Hover OK, {format_distance(distance - tolerance)} outside the circle"
        if distance <= attempt_distance:
            return OFF_TARGET, f"Hover OK, {format_distance(distance - tolerance)} outside the circle: will count, off target"
        return NOT_READY, "Hover OK, but too far off to complete this point"
    return NOT_READY, f"{format_distance(distance - tolerance)} to the circle"


# --- text, shared by the window and the overlay -------------------------------------------

def format_distance(meters):
    return f"{meters / 1000:.2f} km" if meters >= 1000 else f"{meters:.0f} m"


def turn_text(error, bearing):
    """Which way to turn, and the compass bearing to turn to (as on the game's HUD)."""
    if error is None:
        return "over the point"
    compass = f"{round(bearing) % 360:03d}°"
    if round(abs(error)) == 0:
        return f"AHEAD {compass}"
    return f"◄ LEFT {compass}" if error < 0 else f"RIGHT {compass} ►"


def pitch_text(pitch, stale):
    if pitch is None:
        return "PITCH —"
    degrees = round(abs(pitch))
    arrow = "" if degrees == 0 else "▼ " if pitch < 0 else "▲ "
    return f"PITCH {arrow}{degrees}°" + ("  (stale)" if stale else "")


def signed_pitch_text(pitch, stale):
    """The overlay's pitch row: a sign instead of an arrow, negative = below the horizon."""
    if pitch is None:
        return "PITCH —"
    degrees = round(pitch)
    value = f"{degrees:+d}°" if degrees else "0°"
    return f"PITCH {value}" + ("  (stale)" if stale else "")
