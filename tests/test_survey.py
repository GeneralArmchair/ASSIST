"""Desk tests for the survey logic. Run from the project folder:
    .venv\\Scripts\\python.exe -m unittest -v
"""

import tempfile
import unittest
from pathlib import Path

from make_test_recording import CENTER, PLANET_RADIUS, full_walk, leave_resume
from assist import geo
from assist.status import Status
from assist.survey import (AUTO, CLOSED, NO_PING, UNFINISHED, SurveyStore, load_pattern)
from assist.tracker import SurveyTracker

PATTERN = load_pattern(Path(__file__).parent.parent / "pattern.json")


class GeoTests(unittest.TestCase):
    def test_destination_round_trip(self):
        for bearing in (0, 45, 135, 270, 359):
            lat, lon = geo.destination(*CENTER, bearing, 5500, PLANET_RADIUS)
            self.assertAlmostEqual(geo.distance(*CENTER, lat, lon, PLANET_RADIUS), 5500, delta=0.01)
            self.assertAlmostEqual(geo.bearing(*CENTER, lat, lon), bearing % 360, delta=0.01)

    def test_crosses_date_line(self):
        lat, lon = geo.destination(0, 179.999, 90, 5000, PLANET_RADIUS)
        self.assertLess(lon, -179)
        self.assertAlmostEqual(geo.distance(0, 179.999, lat, lon, PLANET_RADIUS), 5000, delta=0.01)

    def test_pattern_spacing(self):
        """Neighboring inner-ring points are 3.4 km apart on the sphere."""
        inner = [p for p in PATTERN.points if p.label.startswith("I")]
        points = [geo.destination(*CENTER, p.bearing, p.distance, PLANET_RADIUS) for p in inner]
        for a, b in zip(points, points[1:] + points[:1]):
            self.assertAlmostEqual(geo.distance(*a, *b, PLANET_RADIUS), 3400, delta=5)


def replay(records, folder):
    """Run recorded snapshots through a fresh tracker, collecting its log messages."""
    tracker = SurveyTracker(SurveyStore(folder), PATTERN)
    messages, targets = [], []
    tracker.add_listener(lambda m: m and messages.append(m))
    for record in records:
        tracker.update(Status.from_json(record["status"]))
        if tracker.survey and (not targets or targets[-1] != tracker.survey.target):
            targets.append(tracker.survey.target)
    return tracker, messages, targets


class FullWalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = SurveyStore(cls.temp.name)
        cls.tracker, cls.messages, cls.targets = replay(full_walk(), cls.temp.name)
        archived = list(cls.store.archive_dir.glob("*.json"))
        assert len(archived) == 1, archived
        cls.survey = SurveyStore._read(archived[0])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_points_done_and_archived_unfinished(self):
        self.assertIsNone(self.tracker.survey)
        self.assertEqual(self.survey.state, UNFINISHED)  # leaving never finishes a survey
        self.assertEqual(self.survey.progress(), (19, 0, 19))
        self.assertFalse(self.store.active_path.exists())

    def test_poi_named_from_nav_target(self):
        self.assertEqual(self.survey.poi.name, "Planetary Mining Location Signal (7)")

    def test_tier_2_only_after_tier_1(self):
        tiers = [self.survey.waypoint(label).tier for label in self.targets if label]
        first_tier_2 = tiers.index(2)
        self.assertTrue(all(t == 2 for t in tiers[first_tier_2:]), tiers)

    def test_far_miss_is_extra_and_point_picked_again(self):
        self.assertTrue(any("again, it's still open (the ping didn't complete it)" in m for m in self.messages))
        extras = [p for p in self.survey.pings if not self.survey.points_for(p)]
        self.assertEqual(len(extras), 1)  # the 1.8 km miss
        self.assertIsNone(extras[0].point)

    def test_near_miss_completes_its_point(self):
        far_off = [w for w in self.survey.survey_points
                   if (p := self.survey.done_by(w)) and self.survey.distance(w.lat, w.lon, p.lat, p.lon) > w.tolerance]
        self.assertEqual(len(far_off), 1)  # the 550 m miss, completed because it was aimed at that point
        self.assertTrue(far_off[0].label.startswith("I"))

    def test_no_ping_entry_replaced_and_duplicate_ignored(self):
        self.assertTrue(any("replaces the no-ping entry" in m for m in self.messages))
        self.assertTrue(any("ignored: within 50 m" in m for m in self.messages))
        self.assertFalse(any(p.kind == NO_PING for p in self.survey.pings))
        self.assertEqual(len(self.survey.pings), 20)  # 19 points + the miss
        self.assertTrue(all(p.kind == AUTO for p in self.survey.pings))


class LeaveResumeTests(unittest.TestCase):
    def test_leave_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            tracker, messages, _ = replay(leave_resume(), folder)
            left = [m for m in messages if m.startswith("Left the POI")]
            self.assertEqual(len(left), 4, messages)
            self.assertIn("km from the center", left[0])
            self.assertIn("supercruise", left[1])
            self.assertIn("supercruise", left[2])  # not "km from the center" right after the far resume
            self.assertIn("supercruise", left[3])
            resumed = [m for m in messages if "Resumed survey" in m]
            self.assertEqual(len(resumed), 3, messages)
            self.assertIn("on deploying", resumed[0])
            self.assertIn("on leaving glide, 40.0 km from its center", resumed[1])
            self.assertIn("on leaving supercruise, 5.0 km from its center", resumed[2])
            self.assertEqual(sum("Started survey" in m for m in messages), 1)
            archived = SurveyStore(folder).unfinished()
            self.assertEqual(len(archived), 1)
            # center + 6 + the resume deployment + 3 + 2 after the glide + 1 after the supercruise drop
            self.assertEqual(archived[0].progress()[0], 14)


class ActionTests(unittest.TestCase):
    """Buttons and the 50 m rule, driven with hand-made snapshots."""

    SRV = (1 << 3) | (1 << 21) | (1 << 26)
    SHIP = (1 << 3) | (1 << 21) | (1 << 24)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SurveyStore(self.temp.name)
        self.tracker = SurveyTracker(self.store, PATTERN)
        self.second = 0

    def tearDown(self):
        self.temp.cleanup()

    def status(self, flags, lat, lon):
        self.second += 1
        self.tracker.update(Status.from_json({
            "timestamp": f"2025-01-01T00:{self.second // 60:02d}:{self.second % 60:02d}Z", "Flags": flags,
            "Latitude": lat, "Longitude": lon, "Altitude": 0, "BodyName": "Test", "PlanetRadius": PLANET_RADIUS}))

    def deploy(self, lat, lon, ping):
        self.status(self.SHIP, lat, lon)
        self.status(self.SRV, lat, lon)
        if ping:
            self.status(self.SRV | (1 << 6), lat, lon)
        self.status(self.SHIP, lat, lon)

    def test_no_ping_and_undo(self):
        self.deploy(*CENTER, ping=False)
        survey = self.tracker.survey
        self.assertEqual([p.kind for p in survey.pings], [NO_PING])
        self.assertEqual(survey.target, "C")  # a no-ping entry doesn't count
        self.assertFalse(hasattr(self.tracker, "count_as_ping"))  # removed in 0.10.1
        self.tracker.undo_last_ping()
        self.assertEqual(survey.pings, [])
        self.assertIsNone(survey.done_by(survey.waypoint("C")))
        self.assertEqual(self.store.load_active().pings, [])  # saved after every change

    def test_manual_ping_in_srv_prevents_no_ping_entry(self):
        self.status(self.SHIP, *CENTER)
        self.status(self.SRV, *CENTER)
        self.tracker.manual_ping()
        self.status(self.SHIP, *CENTER)
        self.assertEqual([p.kind for p in self.tracker.survey.pings], ["manual"])

    def test_skip_moves_target_and_close_archives(self):
        self.deploy(*CENTER, ping=True)
        first = self.tracker.survey.target
        self.tracker.toggle_skip(first)
        self.assertNotEqual(self.tracker.survey.target, first)
        self.tracker.close_survey()
        self.assertIsNone(self.tracker.survey)
        self.assertEqual(self.store.unfinished(), [])
        # A closed survey isn't resumed: deploying here starts a new one.
        self.deploy(*CENTER, ping=True)
        self.assertEqual(len(self.tracker.survey.pings), 1)

    def test_ping_completes_target_within_attempt_distance(self):
        self.deploy(*CENTER, ping=True)
        survey = self.tracker.survey
        target = survey.waypoint(survey.target)
        near = geo.destination(target.lat, target.lon, 0, 1_200, PLANET_RADIUS)
        self.deploy(*near, ping=True)
        self.assertIs(survey.done_by(target), survey.pings[-1])
        self.assertEqual(survey.pings[-1].point, target.label)

    def test_ping_beyond_attempt_distance_is_extra(self):
        self.deploy(*CENTER, ping=True)
        survey = self.tracker.survey
        target = survey.waypoint(survey.target)
        # Toward the center from the target, 1.6 km: far from every point.
        bearing_in = geo.bearing(target.lat, target.lon, *CENTER)
        far = geo.destination(target.lat, target.lon, bearing_in, 1_600, PLANET_RADIUS)
        self.deploy(*far, ping=True)
        self.assertIsNone(survey.pings[-1].point)
        self.assertIsNone(survey.done_by(target))
        self.assertEqual(survey.target, target.label)  # picked again
        self.tracker.assign_to_target(survey.pings[-1].id)
        self.assertIs(survey.done_by(target), survey.pings[-1])

    def test_far_deploy_starts_new_survey(self):
        self.deploy(*CENTER, ping=True)
        far = geo.destination(*CENTER, 90, 11_000, PLANET_RADIUS)
        self.deploy(*far, ping=True)
        self.assertEqual(len(self.store.unfinished()), 1)
        self.assertAlmostEqual(self.tracker.survey.poi.center_lon, far[1], places=6)


if __name__ == "__main__":
    unittest.main()
