"""Make synthetic recordings for testing the survey logic at the desk.

Writes recordings in the same format as record_status.py, so they replay with
    .venv\\Scripts\\python.exe main.py --replay synthetic_full_walk.jsonl --speed 10

The simulated pilot flies to whatever point the survey tool selects (it runs the
tracker while generating), with deliberate mistakes along the way. Scenarios:

full_walk     approach a targeted mining signal, survey all 19 points, then supercruise away.
              Mistakes: one inner-ring landing 1.8 km off (beyond the attempt distance: an extra
              ping, the point stays open and is picked again), one landing 550 m off (still completes
              its point), one deployment without a ping followed by a redeploy that pings (replaces the
              "no ping detected" entry), one redeploy with a ping at the same spot (ignored, 50 m rule).
leave_resume  survey 6 points, fly 12 km away (left the POI: archived as unfinished),
              come back and deploy (resumes the survey), 3 more points, supercruise away,
              come back with a glide that ends 40 km out (resumes the survey on leaving glide),
              fly in, 2 more points, supercruise away, come back by dropping out of supercruise
              5 km out with no glide (resumes the survey), 1 more point, then supercruise away.
guided        the pilot flies by the tool's guidance over uneven terrain: leaves each hover facing the
              wrong way, turns onto the heading the tool gives, climbs, cruises, descends and hovers.
              Ends with a landing 650 m off (the amber cue).
lithobrake    ballistic arcs: boost up, coast with the nose swung 40° off the direction of travel,
              hit the ground near the point (bit 29 on, altitude = terrain height), bounce and
              lift to a hover to deploy.

Positions, bodies and timestamps are made up (dated 2025-01-01 so the replay doesn't
match a real journal's star system).
"""

import argparse
import json
import math
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from assist import geo
from assist.paths import recordings_folder, shown
from assist.status import Status
from assist.survey import SurveyStore, load_pattern
from assist.tracker import SurveyTracker

HERE = Path(__file__).parent
BODY = "Synthetic 3 b"
PLANET_RADIUS = 1_389_066.5
CENTER = (55.6264, 111.0289)
SIGNAL = {"System": 1, "Body": 30,
          "Name": "$SAA_Unknown_Signal:#type=$PlanetaryMiningLocation_Name;:#index=7;",
          "Name_Localised": "Planetary Mining Location Signal (7)"}

SHIELDS, SUPERCRUISE, HARDPOINTS = 1 << 3, 1 << 4, 1 << 6
TURRET_RETRACTED, HAS_LAT_LONG = 1 << 14, 1 << 21
IN_SHIP, IN_SRV, ALT_FROM_AVERAGE = 1 << 24, 1 << 26, 1 << 29
GLIDE = 1 << 12  # Flags2

SHIP_LOW = SHIELDS | HAS_LAT_LONG | IN_SHIP
SHIP_HIGH = SHIP_LOW | ALT_FROM_AVERAGE
ORBITAL_CRUISE = SHIP_HIGH | SUPERCRUISE
SRV = SHIELDS | HAS_LAT_LONG | IN_SRV


class Pilot:
    """Emits status snapshots and feeds them to a survey tracker, so it can follow its targets."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.records = []
        self.lat, self.lon = geo.destination(*CENTER, 200, 30_000, PLANET_RADIUS)
        self.altitude, self.heading = 60_000.0, 20
        self.flags, self.flags2 = ORBITAL_CRUISE, 0
        self.destination = SIGNAL
        self._temp = tempfile.TemporaryDirectory()
        self.tracker = SurveyTracker(SurveyStore(self._temp.name), load_pattern(HERE / "pattern.json"))

    # --- snapshots ------------------------------------------------------------------------

    def emit(self, seconds=1.0, **changes):
        self.time += timedelta(seconds=seconds)
        for key, value in changes.items():
            setattr(self, key, value)
        status = {"timestamp": self.time.strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "Status",
                  "Flags": self.flags, "Flags2": self.flags2}
        if self.flags & HAS_LAT_LONG:
            status.update(Latitude=round(self.lat, 6), Longitude=round(self.lon, 6),
                          Heading=int(self.heading) % 360, Altitude=int(self.altitude),
                          BodyName=BODY, PlanetRadius=PLANET_RADIUS)
        if self.destination and not self.flags & IN_SRV:
            status["Destination"] = self.destination
        self.records.append({"t": self.time.timestamp(), "status": status})
        self.tracker.update(Status.from_json(status))

    def distance_to(self, lat, lon):
        return geo.distance(self.lat, self.lon, lat, lon, PLANET_RADIUS)

    # --- flying ---------------------------------------------------------------------------

    def approach(self):
        """Orbital cruise above the POI with the signal targeted, glide, then fly to the center."""
        self.emit(2)
        self.emit(5, altitude=25_000.0)
        self.emit(5, flags=SHIP_LOW, flags2=GLIDE, altitude=9_000.0)
        self.emit(10, flags2=0, altitude=2_500.0)
        self.fly_to(*CENTER, cruise_altitude=2_500)

    def fly_to(self, lat, lon, cruise_altitude=1_500, hover=20):
        """Lift off, fly along the great circle and come to a hover at the given point."""
        if self.altitude < cruise_altitude:
            self.emit(2, altitude=self.altitude + 100)
            self.emit(3, altitude=float(cruise_altitude))
        start_lat, start_lon = self.lat, self.lon
        total = self.distance_to(lat, lon)
        heading = geo.bearing(start_lat, start_lon, lat, lon)
        steps = max(2, int(total / 500))
        for i in range(1, steps + 1):
            self.lat, self.lon = geo.destination(start_lat, start_lon, heading, total * i / steps, PLANET_RADIUS)
            self.emit(2, heading=geo.bearing(self.lat, self.lon, lat, lon) if i < steps else heading)
        self.emit(3, altitude=300.0)
        self.emit(3, altitude=float(hover))

    def fly_near(self, lat, lon, miss=None, miss_bearing=None):
        """Fly to a point, landing within ~150 m of it, or exactly miss meters off."""
        off = miss if miss is not None else self.rng.uniform(0, 150)
        direction = miss_bearing if miss_bearing is not None else self.rng.uniform(0, 360)
        lat, lon = geo.destination(lat, lon, direction, off, PLANET_RADIUS)
        self.fly_to(lat, lon)

    def deploy(self, ping=True):
        """Deploy the Rhino from the hover, ping (or not), and board the ship again."""
        hover = (self.lat, self.lon, self.altitude)
        # The SRV lands ~25 m from the ship's reported position, always on the same side of the ship.
        self.lat, self.lon = geo.destination(self.lat, self.lon, self.heading + 120, 25, PLANET_RADIUS)
        self.emit(2, flags=SRV | TURRET_RETRACTED, altitude=0.0)
        if ping:
            self.emit(3, flags=SRV | TURRET_RETRACTED | HARDPOINTS)
            self.emit(8, flags=SRV | HARDPOINTS)
            srv = SRV | HARDPOINTS
        else:
            self.emit(10, flags=SRV)
            srv = SRV
        self.emit(6, flags=srv | TURRET_RETRACTED)  # request to board
        self.lat, self.lon, self.altitude = hover
        self.emit(17, flags=SHIP_LOW, altitude=13.0)
        self.emit(2, altitude=60.0)

    def hop(self, meters):
        """Move a short way at low altitude and hover again (e.g. to redeploy)."""
        self.lat, self.lon = geo.destination(self.lat, self.lon, self.rng.uniform(0, 360), meters, PLANET_RADIUS)
        self.emit(4, altitude=20.0)

    def supercruise_away(self):
        self.emit(3, altitude=3_000.0)
        self.emit(6, altitude=6_000.0)  # normal flight: bit 29 stays off, it comes on with supercruise
        self.emit(1, flags=ORBITAL_CRUISE, altitude=12_000.0)
        self.emit(5, altitude=40_000.0)

    def finish(self):
        self._temp.cleanup()
        return self.records

    def target(self):
        survey = self.tracker.survey
        return survey.waypoint(survey.target) if survey and survey.target else None


def full_walk(seed=1):
    pilot = Pilot(seed)
    pilot.approach()
    pilot.deploy()  # center: starts the survey
    far_miss = near_miss = no_ping_done = duplicate_done = False
    for visit in range(1, 40):
        waypoint = pilot.target()
        if waypoint is None:
            break
        if not far_miss and waypoint.label.startswith("I"):
            # Sideways along the ring, so it doesn't land on another point by accident.
            sideways = geo.bearing(*CENTER, waypoint.lat, waypoint.lon) + 90
            pilot.fly_near(waypoint.lat, waypoint.lon, miss=1800, miss_bearing=sideways)
            pilot.deploy()
            far_miss = True
        elif far_miss and not near_miss and waypoint.label.startswith("I"):
            sideways = geo.bearing(*CENTER, waypoint.lat, waypoint.lon) + 90
            pilot.fly_near(waypoint.lat, waypoint.lon, miss=550, miss_bearing=sideways)
            pilot.deploy()
            near_miss = True
        elif not no_ping_done and waypoint.label.startswith("O"):
            pilot.fly_near(waypoint.lat, waypoint.lon)
            pilot.deploy(ping=False)
            pilot.hop(20)
            pilot.deploy()
            no_ping_done = True
        elif not duplicate_done and visit >= 5:
            pilot.fly_near(waypoint.lat, waypoint.lon)
            pilot.deploy()
            pilot.hop(15)
            pilot.deploy()
            duplicate_done = True
        else:
            pilot.fly_near(waypoint.lat, waypoint.lon)
            pilot.deploy()
    pilot.supercruise_away()
    return pilot.finish()


def leave_resume(seed=2):
    pilot = Pilot(seed)
    pilot.approach()
    pilot.deploy()
    for _ in range(6):
        waypoint = pilot.target()
        pilot.fly_near(waypoint.lat, waypoint.lon)
        pilot.deploy()
    remembered = pilot.target()
    away = geo.destination(*CENTER, 250, 12_000, PLANET_RADIUS)
    pilot.fly_to(*away)  # passes 10 km from the center: left the POI
    pilot.emit(20)
    pilot.fly_near(remembered.lat, remembered.lon)
    pilot.deploy()  # resumes the survey
    for _ in range(3):
        waypoint = pilot.target()
        pilot.fly_near(waypoint.lat, waypoint.lon)
        pilot.deploy()
    pilot.supercruise_away()
    # A sloppy orbital approach: the glide ends 40 km from the POI.
    pilot.lat, pilot.lon = geo.destination(*CENTER, 100, 40_000, PLANET_RADIUS)
    pilot.emit(5, altitude=25_000.0)
    pilot.emit(5, flags=SHIP_LOW, flags2=GLIDE, altitude=9_000.0)
    pilot.emit(10, flags2=0, altitude=2_500.0)  # resumes the survey here
    for _ in range(2):
        waypoint = pilot.target()
        pilot.fly_near(waypoint.lat, waypoint.lon)
        pilot.deploy()
    pilot.supercruise_away()
    # Dropping out of supercruise high above the POI, with no glide.
    pilot.lat, pilot.lon = geo.destination(*CENTER, 300, 5_000, PLANET_RADIUS)
    pilot.emit(5, altitude=20_000.0)
    pilot.emit(5, flags=SHIP_LOW, altitude=20_000.0)  # resumes the survey here
    waypoint = pilot.target()
    pilot.fly_near(waypoint.lat, waypoint.lon)
    pilot.deploy()
    pilot.supercruise_away()
    return pilot.finish()


class TerrainPilot(Pilot):
    """A pilot flying over uneven terrain: the reported altitude follows the ground under the ship."""

    def ground(self, lat=None, lon=None):
        """Terrain height relative to the average radius: about 1.5 km low, rolling by ±200 m."""
        lat = self.lat if lat is None else lat
        lon = self.lon if lon is None else lon
        return (-1500 + 120 * math.sin(math.radians(lat) * PLANET_RADIUS / 2000)
                + 80 * math.cos(math.radians(lon) * PLANET_RADIUS / 3100))

    def fly_at(self, seconds, height, heading=None):
        """Emit a snapshot with the ship at this height above the average radius."""
        self.emit(seconds, altitude=height - self.ground(), heading=self.heading if heading is None else heading)

    def forward(self, course, meters):
        self.lat, self.lon = geo.destination(self.lat, self.lon, course, meters, PLANET_RADIUS)

    def touch_ground(self):
        """The game's report of a touch: bit 29 on, and the altitude is the terrain's height."""
        self.emit(1, flags=SHIP_LOW | ALT_FROM_AVERAGE, altitude=self.ground())
        self.emit(1, flags=SHIP_LOW, altitude=8.0)

    def hover_down(self, hover=20):
        for altitude in (60.0, 35.0, float(hover)):
            self.emit(2, altitude=altitude)

    def guided_leg(self, wrong_way=70, cruise=1_500, speed=250, turn_rate=20, stop_short=0):
        """Fly to the current target by the tool's guidance, turning at a limited rate."""
        self.emit(1, heading=(self.heading + wrong_way) % 360)
        height = self.altitude + self.ground()
        while True:
            guidance = self.tracker.guidance()
            if guidance.distance <= max(speed, 60) + stop_short:
                break
            error = guidance.heading_error or 0
            self.heading = (self.heading + max(-turn_rate, min(turn_rate, error))) % 360
            ground_now = self.ground()
            # Climb at 45°, cruise, then descend at about 30° towards the point.
            wanted = min(cruise, (guidance.distance - stop_short) * 0.58) + ground_now
            height += max(-speed, min(speed * 0.7, wanted - height))
            self.forward(self.heading, speed if abs(error) < 45 else speed / 3)
            self.fly_at(1, height)
        self.hover_down()

    def arc(self, target, gravity=5.0, speed=120.0, short_by=120, nose_off=40):
        """A ballistic hop that ends in the ground near the target, then a lift to a hover."""
        distance = self.distance_to(target.lat, target.lon) - short_by
        course = geo.bearing(self.lat, self.lon, target.lat, target.lon)
        steps = max(4, round(distance / speed))
        climb = gravity * steps / 2
        self.heading = round(course)
        height = self.altitude + self.ground()
        start = height
        for t in range(1, steps):
            self.forward(course, distance / steps)
            flown = climb * t - gravity * t * t / 2
            height = start + flown
            # The nose swings away during the coast; the ship keeps going the same way.
            nose = course + nose_off if 2 <= t <= steps - 2 else course
            self.fly_at(1, height, heading=round(nose) % 360)
        self.forward(course, distance / steps)
        self.touch_ground()
        self.hover_down()


def guided(seed=3):
    pilot = TerrainPilot(seed)
    pilot.approach()
    pilot.hover_down()
    pilot.deploy()
    for _ in range(4):
        pilot.guided_leg()
        pilot.deploy()
    pilot.guided_leg(stop_short=650)
    pilot.deploy()
    pilot.supercruise_away()
    return pilot.finish()


def lithobrake(seed=4):
    pilot = TerrainPilot(seed)
    pilot.approach()
    pilot.hover_down()
    pilot.deploy()
    for _ in range(4):
        pilot.arc(pilot.target())
        pilot.deploy()
    pilot.supercruise_away()
    return pilot.finish()


SCENARIOS = {"full_walk": full_walk, "leave_resume": leave_resume, "guided": guided, "lithobrake": lithobrake}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="where to write them (default Documents\\ASSIST\\recordings)")
    args = parser.parse_args()
    args.out_dir = args.out_dir or recordings_folder()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, make in SCENARIOS.items():
        records = make()
        path = args.out_dir / f"synthetic_{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
        print(f"{shown(path)}: {len(records)} snapshots, "
              f"{records[-1]['t'] - records[0]['t']:.0f} s of game time")


if __name__ == "__main__":
    main()
