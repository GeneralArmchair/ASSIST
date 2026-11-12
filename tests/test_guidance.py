"""Desk tests for the guidance math, the deploy cue and the hotkey key mapping."""

import math
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from make_test_recording import CENTER, PLANET_RADIUS, guided, lithobrake
from assist import geo
from assist.guidance import (DEPLOY_ALTITUDE, NOT_READY, OFF_TARGET, READY, Guide,
                                    is_ground_contact, pitch_text, pitch_to, signed_angle, turn_text)
from assist.hotkeys import (MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, HotkeyManager,
                                   to_native)
from assist.status import Status
from assist.survey import POI, Survey, SurveyStore, load_pattern
from assist.tracker import SurveyTracker

PATTERN = load_pattern(Path(__file__).parent.parent / "pattern.json")
SHIELDS, LAT_LONG, SHIP, SRV = 1 << 3, 1 << 21, 1 << 24, 1 << 26
FROM_RADIUS, SUPERCRUISE, LANDED = 1 << 29, 1 << 4, 1 << 1
FLYING = SHIELDS | LAT_LONG | SHIP


def status(flags=FLYING, lat=CENTER[0], lon=CENTER[1], altitude=20, heading=0, second=0, flags2=0):
    return Status.from_json({
        "timestamp": f"2025-01-01T00:{second // 60:02d}:{second % 60:02d}Z", "Flags": flags, "Flags2": flags2,
        "Latitude": lat, "Longitude": lon, "Heading": heading, "Altitude": altitude,
        "BodyName": "Test", "PlanetRadius": PLANET_RADIUS})


def make_survey(target="C"):
    poi = POI(system=None, body="Test", planet_radius=PLANET_RADIUS, center_lat=CENTER[0], center_lon=CENTER[1])
    survey = Survey.new(poi, PATTERN, "2025-01-01T00:00:00Z")
    survey.target = target
    return survey


def offset(meters, bearing=180):
    """A position this far from the center."""
    return geo.destination(*CENTER, bearing, meters, PLANET_RADIUS)


class MathTests(unittest.TestCase):
    def test_signed_angle_wraps(self):
        self.assertEqual(signed_angle(10, 350), 20)
        self.assertEqual(signed_angle(350, 10), -20)
        self.assertEqual(signed_angle(90, 90), 0)
        self.assertEqual(signed_angle(270, 90), 180)

    def test_pitch_flat_case(self):
        """On a huge sphere, curvature vanishes: 1 km up at 1 km range is 45° down."""
        self.assertAlmostEqual(pitch_to(1000, 1000, 0, 1e10), -45, places=3)
        self.assertAlmostEqual(pitch_to(1000, 0, 1000, 1e10), 45, places=3)

    def test_pitch_includes_curvature(self):
        """Level with the target on a sphere, the target is half the central angle below the horizon."""
        radius = 100_000
        distance = 10_000
        half_angle = math.degrees(distance / radius) / 2
        self.assertAlmostEqual(pitch_to(distance, 0, 0, radius), -half_angle, places=6)

    def test_text(self):
        self.assertEqual(turn_text(-34.4, 45.2), "◄ LEFT 045°")
        self.assertEqual(turn_text(12.2, 300), "RIGHT 300° ►")
        self.assertEqual(turn_text(0.2, 7), "AHEAD 007°")
        self.assertEqual(turn_text(5, 359.7), "RIGHT 000° ►")
        self.assertEqual(turn_text(None, 45), "over the point")
        self.assertEqual(pitch_text(-12.3, False), "PITCH ▼ 12°")
        self.assertEqual(pitch_text(12.3, False), "PITCH ▲ 12°")
        self.assertEqual(pitch_text(-0.07, False), "PITCH 0°")
        self.assertEqual(pitch_text(-12.3, True), "PITCH ▼ 12°  (stale)")
        self.assertEqual(pitch_text(None, False), "PITCH —")


class GroundContactTests(unittest.TestCase):
    def test_real_touch_sequence(self):
        """From a real recording: altitude 0, then -1717 with bit 29 for one snapshot, then a bounce."""
        before = status(altitude=0)
        touch = status(flags=FLYING | FROM_RADIUS, altitude=-1717)
        self.assertTrue(is_ground_contact(touch, before, False))
        self.assertFalse(is_ground_contact(status(altitude=12), touch, True))

    def test_stays_in_contact_and_tool_started_on_ground(self):
        touch = status(flags=FLYING | FROM_RADIUS, altitude=-1860)
        self.assertTrue(is_ground_contact(touch, None, False))
        self.assertTrue(is_ground_contact(touch, touch, True))

    def test_positive_terrain_counts_too(self):
        self.assertTrue(is_ground_contact(status(flags=FLYING | FROM_RADIUS, altitude=800), status(altitude=5), False))

    def test_not_contact(self):
        high = status(flags=FLYING | FROM_RADIUS, altitude=6645)
        self.assertFalse(is_ground_contact(status(flags=FLYING | FROM_RADIUS | SUPERCRUISE, altitude=6645),
                                           status(altitude=6052), False))
        self.assertFalse(is_ground_contact(high, status(altitude=6052), False))  # a flip while climbing
        self.assertFalse(is_ground_contact(status(flags=FLYING, altitude=5), status(altitude=6), False))
        # The respawn after an SRV self-destruct: 218 km up with bit 29 on, right after the SRV.
        self.assertFalse(is_ground_contact(status(flags=FLYING | FROM_RADIUS, altitude=218_579),
                                           status(flags=SHIELDS | LAT_LONG | SRV, altitude=1), False))


class GuidanceTests(unittest.TestCase):
    def guide(self, st, survey=None, on_ground=False, guide=None):
        return (guide or Guide()).guidance(st, survey or make_survey(), PATTERN.attempt_distance, on_ground)

    def test_heading_error_sign(self):
        lat, lon = offset(5000)  # south of the center: the center is due north
        self.assertAlmostEqual(self.guide(status(lat=lat, lon=lon, heading=30)).heading_error, -30, delta=0.1)
        self.assertAlmostEqual(self.guide(status(lat=lat, lon=lon, heading=330)).heading_error, 30, delta=0.1)

    def test_on_course_band_follows_the_circle(self):
        """500 m circle at 5 km is about ±5.7°; closer in, the band widens; inside, always on course."""
        lat, lon = offset(5000)
        self.assertTrue(self.guide(status(lat=lat, lon=lon, heading=4)).on_course)
        self.assertFalse(self.guide(status(lat=lat, lon=lon, heading=6)).on_course)
        lat, lon = offset(1000)
        self.assertTrue(self.guide(status(lat=lat, lon=lon, heading=20)).on_course)
        lat, lon = offset(300)
        self.assertTrue(self.guide(status(lat=lat, lon=lon, heading=170)).on_course)

    def test_over_the_point_has_no_direction(self):
        lat, lon = offset(20)
        g = self.guide(status(lat=lat, lon=lon))
        self.assertIsNone(g.heading_error)
        self.assertIsNone(g.pitch)
        self.assertTrue(g.on_course)

    def test_pitch_uses_terrain_altitude_when_bit_29_is_off(self):
        lat, lon = offset(2000)
        g = self.guide(status(lat=lat, lon=lon, altitude=2000))
        self.assertAlmostEqual(g.pitch, -45, delta=0.2)
        self.assertFalse(g.pitch_stale)

    def test_target_height_is_always_zero(self):
        """A measured ground height doesn't affect pitch, in normal flight or in supercruise."""
        survey = make_survey()
        survey.poi.ground_height = -1860
        lat, lon = offset(2000)
        normal = self.guide(status(lat=lat, lon=lon, altitude=2000), survey)
        self.assertAlmostEqual(normal.pitch, -45, delta=0.2)
        cruise = self.guide(status(flags=FLYING | FROM_RADIUS | SUPERCRUISE, lat=lat, lon=lon, altitude=2000), survey)
        self.assertAlmostEqual(cruise.pitch, -45, delta=0.2)

    def test_pitch_stale_on_ground_touch_and_cleared_by_new_target(self):
        guide = Guide()
        survey = make_survey()
        lat, lon = offset(2000)
        live = self.guide(status(lat=lat, lon=lon, altitude=500), survey, guide=guide)
        touch = status(flags=FLYING | FROM_RADIUS, lat=lat, lon=lon, altitude=-1717)
        stale = self.guide(touch, survey, on_ground=True, guide=guide)
        self.assertEqual(stale.pitch, live.pitch)
        self.assertTrue(stale.pitch_stale)
        survey.target = "I1"
        self.assertIsNone(self.guide(touch, survey, on_ground=True, guide=guide).pitch)

    def test_deploy_cue(self):
        inside = offset(300)
        amber = offset(1000)
        too_far = offset(2000)
        cue = lambda where, **kw: self.guide(status(lat=where[0], lon=where[1], **kw)).cue
        self.assertEqual(cue(inside, altitude=DEPLOY_ALTITUDE), READY)
        self.assertEqual(cue(inside, altitude=DEPLOY_ALTITUDE + 1), NOT_READY)
        self.assertEqual(cue(amber, altitude=20), OFF_TARGET)
        self.assertEqual(cue(too_far, altitude=20), NOT_READY)
        self.assertEqual(cue(inside, altitude=20, flags=FLYING | FROM_RADIUS), NOT_READY)
        self.assertIsNone(cue(inside, altitude=1, flags=SHIELDS | LAT_LONG | SRV))

    def test_cue_not_amber_for_a_point_already_done(self):
        survey = make_survey()
        survey.waypoint("C").skipped = True
        where = offset(1000)
        self.assertEqual(self.guide(status(lat=where[0], lon=where[1]), survey).cue, NOT_READY)

    def test_on_ground_blocks_the_cue(self):
        g = self.guide(status(flags=FLYING | FROM_RADIUS, altitude=-1860), on_ground=True)
        self.assertEqual(g.cue, NOT_READY)
        self.assertNotIn("DEPLOY READY", g.cue_text)

    def test_landed_blocks_the_cue_and_isnt_a_ground_touch(self):
        """From a real landing: landed flag, altitude 0, bit 29 off. The Rhino needs a hover."""
        for meters in (300, 1000):  # inside the circle, and where a hover would show amber
            lat, lon = offset(meters)
            landed = status(flags=FLYING | LANDED, lat=lat, lon=lon, altitude=0)
            g = self.guide(landed)
            self.assertEqual(g.cue, NOT_READY)
            self.assertIn("Inside the circle" if meters == 300 else "to the circle", g.cue_text)
        self.assertFalse(is_ground_contact(landed, status(altitude=2), False))

    def test_pitch_stale_while_landed(self):
        guide = Guide()
        survey = make_survey()
        lat, lon = offset(2000)
        live = self.guide(status(lat=lat, lon=lon, altitude=500), survey, guide=guide)
        g = self.guide(status(flags=FLYING | LANDED, lat=lat, lon=lon, altitude=0), survey, guide=guide)
        self.assertEqual(g.pitch, live.pitch)
        self.assertTrue(g.pitch_stale)


def fly(records, folder):
    """Replay through a tracker, collecting the guidance after every snapshot."""
    tracker = SurveyTracker(SurveyStore(folder), PATTERN)
    messages, log = [], []
    tracker.add_listener(lambda m: m and messages.append(m))
    for record in records:
        st = Status.from_json(record["status"])
        tracker.update(st)
        log.append((st, tracker.guidance()))
    return tracker, messages, log


class GuidedFlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.tracker, cls.messages, cls.log = fly(guided(), cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_pilot_following_the_tool_lands_ready(self):
        ready = [g for _, g in self.log if g and g.cue == READY]
        self.assertEqual(len(ready), 4)  # the hover at the end of each of the 4 accurate legs
        self.assertTrue(any(g.cue == OFF_TARGET for _, g in self.log if g), "the 650 m landing shows amber")
        self.assertFalse(any("extra ping" in m for m in self.messages), self.messages)

    def test_starts_off_course_and_gets_on_course(self):
        flying = [g for st, g in self.log if g and st.has(24) and st.altitude and st.altitude > 100
                  and g.heading_error is not None]
        self.assertTrue(any(not g.on_course for g in flying))
        self.assertGreater(sum(g.on_course for g in flying) / len(flying), 0.7)

    def test_pitch_live_throughout_low_flight_and_blank_in_srv(self):
        for st, g in self.log:
            if g is None or st.has(4) or st.has(29):
                continue
            if st.has(26):
                self.assertIsNone(g.pitch)
            elif g.distance > 50:
                self.assertFalse(g.pitch_stale)
                self.assertIsNotNone(g.pitch)

    def test_no_false_ground_touches(self):
        self.assertFalse(any("Ground touch" in m for m in self.messages), self.messages)


class LithobrakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.tracker, cls.messages, cls.log = fly(lithobrake(), cls.temp.name)
        cls.survey = SurveyStore(cls.temp.name).unfinished()[0]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_each_impact_measures_the_ground(self):
        touches = [m for m in self.messages if "Ground touch" in m]
        self.assertEqual(len(touches), 4, self.messages)
        self.assertLess(self.survey.poi.ground_height, -1000)

    def test_impact_shows_stale_pitch_and_on_ground(self):
        contact = [g for st, g in self.log if g and st.has(29) and not st.has(4)]
        self.assertEqual(len(contact), 4)
        for g in contact:
            self.assertEqual(g.cue, NOT_READY)
            self.assertTrue(g.pitch_stale)


class HotkeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_key_mapping(self):
        self.assertEqual(to_native(QKeySequence("F13")), (MOD_NOREPEAT, 0x7C))
        self.assertEqual(to_native(QKeySequence("Ctrl+Alt+N")), (MOD_NOREPEAT | MOD_CONTROL | MOD_ALT, ord("N")))
        self.assertEqual(to_native(QKeySequence("Shift+5")), (MOD_NOREPEAT | MOD_SHIFT, ord("5")))
        self.assertEqual(to_native(QKeySequence("Num+7")), (MOD_NOREPEAT, 0x67))
        self.assertIsNone(to_native(QKeySequence()))

    def test_unbound_by_default_and_duplicates_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "test.ini"), QSettings.Format.IniFormat)
            manager = HotkeyManager([("a", "Action A", lambda: None), ("b", "Action B", lambda: None)], settings)
            self.assertEqual(manager.apply(), [])
            self.assertEqual(manager._registered, [])
            combo = QKeySequence("Ctrl+Alt+Shift+F20")
            manager.set_binding("a", combo)
            manager.set_binding("b", combo)
            problems = manager.apply()
            self.assertEqual(len(manager._registered), 1)
            self.assertIn("already used for Action A", problems[0])
            manager.release()


if __name__ == "__main__":
    unittest.main()
