"""Desk tests for the overlay: its rows, when it shows, and its window setup."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QDialog

from make_test_recording import full_walk, guided, lithobrake
from assist.guidance import Guide, signed_pitch_text
from assist.overlay import (DEPLOY_READY, GAME_ABSENT, GAME_COVERED, GAME_OK, MISS, SAMPLE, SAMPLE_WITH_CROSS,
                                   FineTuneCross, OverlayDialog, OverlayView, OverlayWindow, TierNotice, box_size,
                                   build_view, hidden_reason, paint_view, view_from, with_notice)
from assist.status import IN_SRV, Status
from assist.survey import AUTO, Ping, SurveyStore
from assist.tracker import SurveyTracker
from tests.test_guidance import (FLYING, FROM_RADIUS, LANDED, LAT_LONG, PATTERN, SHIELDS, SHIP, SRV, SUPERCRUISE, make_survey,
                                 offset, status)

app = QApplication.instance() or QApplication([])


def view_at(distance, heading, altitude=500, flags=FLYING, target="C", bearing_from=180, fine_tune=False):
    """The view from this far from the center. The center lies due north when bearing_from is 180."""
    survey = make_survey(target)
    lat, lon = offset(distance, bearing_from)
    guidance = Guide().guidance(status(flags=flags, lat=lat, lon=lon, altitude=altitude, heading=heading),
                                survey, PATTERN.attempt_distance, False)
    return view_from(guidance, fine_tune)


class RowTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(view_at(3000, 0).title, "C  3.00 km")

    def test_bearing_has_no_words(self):
        self.assertEqual(view_at(3000, 90).bearing, "000°")

    def test_turn_arrows_light_above_5_degrees(self):
        self.assertTrue(view_at(3000, 10).turn_left)     # heading 10, target at 0: turn left
        self.assertFalse(view_at(3000, 10).turn_right)
        self.assertTrue(view_at(3000, 350).turn_right)
        self.assertFalse(view_at(3000, 350).turn_left)

    def test_turn_arrows_grey_at_or_below_5_degrees(self):
        for heading in (0, 3, 357):
            view = view_at(3000, heading)
            self.assertFalse(view.turn_left or view.turn_right, heading)

    def test_turn_threshold_edge(self):
        """Heading is whole degrees, so a target due north makes the error exactly the heading."""
        self.assertFalse(view_at(3000, 5).turn_left)
        self.assertTrue(view_at(3000, 6).turn_left)

    def test_arrows_grey_inside_the_circle(self):
        view = view_at(300, 90)  # inside the 500 m circle, far off the heading
        self.assertFalse(view.turn_left or view.turn_right)

    def test_arrows_grey_over_the_point(self):
        view = view_at(20, 90)
        self.assertFalse(view.turn_left or view.turn_right)
        self.assertEqual(view.bearing, "—")

    def test_signed_pitch(self):
        self.assertEqual(signed_pitch_text(-38.4, False), "PITCH -38°")
        self.assertEqual(signed_pitch_text(12.2, False), "PITCH +12°")
        self.assertEqual(signed_pitch_text(0.3, False), "PITCH 0°")
        self.assertEqual(signed_pitch_text(-0.3, False), "PITCH 0°")
        self.assertEqual(signed_pitch_text(-4, True), "PITCH -4°  (stale)")
        self.assertEqual(signed_pitch_text(None, False), "PITCH —")

    def test_pitch_row_uses_the_guidance(self):
        self.assertEqual(view_at(1000, 0, altitude=1000).pitch, "PITCH -45°")

    def test_stale_pitch(self):
        survey = make_survey()
        guide = Guide()
        lat, lon = offset(3000)
        guide.guidance(status(lat=lat, lon=lon, altitude=500), survey, PATTERN.attempt_distance, False)
        landed = status(flags=FLYING | LANDED, lat=lat, lon=lon, altitude=0)
        view = view_from(guide.guidance(landed, survey, PATTERN.attempt_distance, False))
        self.assertTrue(view.pitch_stale)
        self.assertIn("(stale)", view.pitch)

    def test_row_4_blank_far_away(self):
        self.assertIsNone(view_at(3000, 0).cue)

    def test_deploy_ready_inside_circle_at_any_height(self):
        for altitude in (20, 28, 29, 500, 3000):
            self.assertEqual(view_at(300, 0, altitude=altitude).cue, DEPLOY_READY, altitude)

    def test_deploy_ready_when_landed_in_the_circle(self):
        self.assertEqual(view_at(300, 0, altitude=0, flags=FLYING | LANDED).cue, DEPLOY_READY)

    def test_miss_is_low_outside_the_circle_within_attempt_distance(self):
        self.assertEqual(view_at(1000, 0, altitude=20).cue, MISS)

    def test_every_point_has_a_500_m_circle(self):
        self.assertEqual({point.tolerance for point in PATTERN.points}, {500.0})
        self.assertEqual(view_at(490, 0, altitude=20).cue, DEPLOY_READY)
        self.assertEqual(view_at(510, 0, altitude=20).cue, MISS)

    def test_row_4_blank_high_outside_the_circle(self):
        self.assertIsNone(view_at(1000, 0, altitude=500).cue)

    def test_row_4_blank_low_beyond_attempt_distance(self):
        self.assertIsNone(view_at(PATTERN.attempt_distance + 200, 0, altitude=20).cue)

    def test_srv_shows_rows_1_and_2_only(self):
        view = view_at(300, 90, altitude=1, flags=SHIELDS | LAT_LONG | SRV)
        self.assertIsNotNone(view.bearing)
        self.assertIsNone(view.pitch)
        self.assertIsNone(view.cue)


class FineTuneCrossTests(unittest.TestCase):
    def cross(self, distance, heading, **kwargs):
        kwargs.setdefault("altitude", 100)
        return view_at(distance, heading, fine_tune=True, **kwargs).cross

    def test_off_unless_chosen(self):
        self.assertIsNone(view_at(300, 0, altitude=100).cross)

    def test_only_at_150_m_or_lower(self):
        self.assertIsNotNone(self.cross(300, 0, altitude=150))
        view = view_at(300, 0, altitude=151, fine_tune=True)
        self.assertIsNone(view.cross)
        self.assertEqual(view.cue, DEPLOY_READY)  # the limit is for the cross only

    def test_shows_when_landed(self):
        self.assertIsNotNone(self.cross(300, 0, altitude=0, flags=FLYING | LANDED))

    def test_bit_29_altitude_isnt_a_height(self):
        self.assertIsNone(self.cross(300, 0, altitude=20, flags=FLYING | FROM_RADIUS))

    def test_flat_on_the_ground_counts_as_low(self):
        lat, lon = offset(300)
        guidance = Guide().guidance(status(flags=FLYING | FROM_RADIUS, lat=lat, lon=lon, altitude=2000),
                                    make_survey(), PATTERN.attempt_distance, True)
        self.assertIsNotNone(view_from(guidance, True).cross)

    def test_only_with_deploy_ready(self):
        self.assertIsNotNone(self.cross(490, 0))
        self.assertIsNone(self.cross(510, 0))
        self.assertIsNone(self.cross(510, 0, altitude=20))  # MISS
        self.assertIsNone(self.cross(300, 0, altitude=1, flags=SHIELDS | LAT_LONG | SRV))

    def test_arrows_follow_the_ship_not_the_compass(self):
        """The point lies due north, 300 m away."""
        self.assertEqual(self.cross(300, 0), FineTuneCross(forward=True))
        self.assertEqual(self.cross(300, 180), FineTuneCross(back=True))
        self.assertEqual(self.cross(300, 90), FineTuneCross(left=True))
        self.assertEqual(self.cross(300, 270), FineTuneCross(right=True))

    def test_both_axes_on_a_diagonal(self):
        self.assertEqual(self.cross(300, 45), FineTuneCross(forward=True, left=True))
        self.assertEqual(self.cross(300, 225), FineTuneCross(back=True, right=True))

    def test_dead_zone_is_50_m_on_each_axis(self):
        self.assertEqual(self.cross(49, 0), FineTuneCross(center=True))
        self.assertEqual(self.cross(51, 0), FineTuneCross(center=True, forward=True))
        self.assertEqual(self.cross(51, 90), FineTuneCross(center=True, left=True))

    def test_center_lights_within_100_m(self):
        self.assertTrue(self.cross(99, 0).center)
        self.assertFalse(self.cross(101, 0).center)

    def test_works_over_the_point_where_the_turn_arrows_stop(self):
        view = view_at(20, 90, altitude=100, fine_tune=True)
        self.assertEqual(view.bearing, "—")
        self.assertEqual(view.cross, FineTuneCross(center=True))

    def test_no_heading_no_cross(self):
        survey = make_survey()
        lat, lon = offset(300)
        raw = dict(status(lat=lat, lon=lon, altitude=100).raw)
        raw.pop("Heading")
        guidance = Guide().guidance(Status.from_json(raw), survey, PATTERN.attempt_distance, False)
        view = view_from(guidance, True)
        self.assertEqual(view.cue, DEPLOY_READY)
        self.assertIsNone(view.cross)


class FakeTracker:
    def __init__(self, survey=None, status=None):
        self.survey = survey
        self.status = status
        self.listeners = []

    def add_listener(self, listener):
        self.listeners.append(listener)

    def guidance(self):
        return Guide().guidance(self.status, self.survey, PATTERN.attempt_distance, False)


class HiddenTests(unittest.TestCase):
    def setUp(self):
        self.survey = make_survey()
        self.status = status(lat=offset(3000)[0], lon=offset(3000)[1], altitude=500)

    def test_shown_in_flight(self):
        for game_state in (GAME_OK, None):
            self.assertIsNone(hidden_reason(True, self.status, self.survey, game_state))

    def test_turned_off(self):
        self.assertTrue(hidden_reason(False, self.status, self.survey, GAME_OK))

    def test_no_survey(self):
        self.assertTrue(hidden_reason(True, self.status, None, GAME_OK))

    def test_no_position(self):
        self.assertTrue(hidden_reason(True, status(flags=SHIELDS | SHIP), self.survey, GAME_OK))
        self.assertTrue(hidden_reason(True, None, self.survey, GAME_OK))

    def test_supercruise(self):
        self.assertTrue(hidden_reason(True, status(flags=FLYING | SUPERCRUISE), self.survey, GAME_OK))

    def test_game_absent_or_covered(self):
        self.assertTrue(hidden_reason(True, self.status, self.survey, GAME_ABSENT))
        self.assertTrue(hidden_reason(True, self.status, self.survey, GAME_COVERED))

    def test_gui_focus(self):
        """Side panels keep it (the pilot is still flying); full-screen views hide it."""
        def focused(value):
            raw = dict(self.status.raw, GuiFocus=value)
            return hidden_reason(True, Status.from_json(raw), self.survey, GAME_OK)
        for value in range(0, 5):
            self.assertIsNone(focused(value), value)
        for value in range(5, 12):
            self.assertTrue(focused(value), value)

    def test_all_points_done(self):
        survey = make_survey()
        survey.target = None
        self.assertEqual(build_view(FakeTracker(survey, self.status)).title, "All points done or skipped")

    def test_nothing_without_a_survey(self):
        self.assertIsNone(build_view(FakeTracker(None, self.status)))


class FakeGame:
    def __init__(self, state=GAME_OK):
        self.current = state

    def state(self):
        return self.current

    def monitor_origin(self):
        return None


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.folder.name) / "s.ini"), QSettings.Format.IniFormat)
        lat, lon = offset(3000)
        self.tracker = FakeTracker(make_survey(), status(lat=lat, lon=lon, altitude=500))
        self.game = FakeGame()
        self.overlay = OverlayWindow(self.tracker, self.settings, self.game)

    def tearDown(self):
        self.overlay.close()
        self.overlay._timer.stop()
        self.settings.sync()
        del self.settings
        self.folder.cleanup()

    def test_never_takes_input_or_focus(self):
        flags = self.overlay.windowFlags()
        for flag in (Qt.WindowType.WindowTransparentForInput, Qt.WindowType.WindowDoesNotAcceptFocus,
                     Qt.WindowType.WindowStaysOnTopHint, Qt.WindowType.FramelessWindowHint, Qt.WindowType.Tool):
            self.assertTrue(flags & flag, flag)
        self.assertTrue(self.overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))

    def test_shows_and_hides_with_the_rules(self):
        self.overlay.refresh()
        self.assertTrue(self.overlay.isVisible())
        self.game.current = GAME_COVERED
        self.overlay.refresh()
        self.assertFalse(self.overlay.isVisible())
        self.game.current = GAME_OK
        self.overlay.set_enabled(False)
        self.assertFalse(self.overlay.isVisible())
        self.assertFalse(self.settings.value("overlay/enabled", type=bool))

    def test_dialog_shows_sample_and_cancel_restores(self):
        self.overlay.set_enabled(False)
        before = self.overlay.y_percent
        dialog = OverlayDialog(self.overlay)
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.overlay._view, SAMPLE)
        dialog.y_slider.setValue(before + 20)
        self.assertEqual(self.overlay.y_percent, before + 20)
        dialog.reject()
        self.assertEqual(self.overlay.y_percent, before)
        self.assertFalse(self.overlay.isVisible())

    def test_dialog_ok_saves(self):
        dialog = OverlayDialog(self.overlay)
        dialog.size_slider.setValue(40)
        dialog.done(QDialog.DialogCode.Accepted)
        self.assertEqual(self.settings.value("overlay/size", type=int), 40)

    def test_fine_tune_is_off_by_default_and_ok_saves_it(self):
        self.assertFalse(self.overlay.fine_tune)
        height = box_size(self.overlay.text_size)[1]
        dialog = OverlayDialog(self.overlay)
        dialog.fine_tune.setChecked(True)
        self.assertEqual(self.overlay._view, SAMPLE_WITH_CROSS)
        self.assertGreater(self.overlay.height(), height)
        dialog.done(QDialog.DialogCode.Accepted)
        self.assertTrue(self.settings.value("overlay/fine_tune", type=bool))

    def test_fine_tune_cancel_restores(self):
        dialog = OverlayDialog(self.overlay)
        dialog.fine_tune.setChecked(True)
        dialog.reject()
        self.assertFalse(self.overlay.fine_tune)

    def test_cross_shows_in_flight_only_when_chosen(self):
        lat, lon = offset(300)
        self.tracker.status = status(lat=lat, lon=lon, altitude=20)
        self.overlay.refresh()
        self.assertIsNone(self.overlay._view.cross)
        self.overlay.fine_tune = True
        self.overlay.refresh()
        self.assertIsNotNone(self.overlay._view.cross)

    def test_placement_stays_on_screen(self):
        self.overlay.x_percent, self.overlay.y_percent = 100, 100
        self.overlay.text_size = 20  # the offscreen test platform's font is wide, and its screen only 800 px
        for fine_tune in (False, True):
            self.overlay.fine_tune = fine_tune
            self.overlay.refresh()
            area = self.overlay.screen().geometry()
            self.assertTrue(area.contains(self.overlay.geometry()))

    def test_paints_every_kind_of_view(self):
        width, height = box_size(26, True)
        for view in (SAMPLE, SAMPLE_WITH_CROSS, OverlayView("All points done or skipped"),
                     OverlayView("O3  1.05 km", "118°", turn_right=True, pitch="PITCH -4°  (stale)",
                                 pitch_stale=True, cue=MISS)):
            image = QImage(width, height, QImage.Format.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            paint_view(painter, width, view, 26)
            painter.end()
            self.assertGreater(sum(image.pixelColor(x, y).alpha() > 0
                                   for x in range(0, width, 3) for y in range(0, height, 3)), 0)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TierNoticeTests(unittest.TestCase):
    def setUp(self):
        self.survey = make_survey()
        self.clock = Clock()
        self.notice = TierNotice(self.clock)
        self.last = next(w for w in self.survey.survey_points if w.label == "O2")
        for waypoint in self.survey.survey_points:
            waypoint.skipped = waypoint.tier == 1 and waypoint is not self.last
        self.assertIsNone(self.notice.text(self.survey, False))  # the first look at this survey

    def _finish(self):
        self.survey.pings.append(Ping(id=1, time=None, lat=self.last.lat, lon=self.last.lon, kind=AUTO,
                                      point=self.last.label))

    def test_says_how_many_were_skipped(self):
        self._finish()
        self.assertEqual(self.notice.text(self.survey, True), "TIER 1 COMPLETE (12 skipped)")

    def test_no_skipped_count_when_none_were_skipped(self):
        for waypoint in self.survey.survey_points:
            waypoint.skipped = False
        self.survey.pings = [Ping(id=i, time=None, lat=w.lat, lon=w.lon, kind=AUTO, point=w.label)
                             for i, w in enumerate(self.survey.survey_points) if w.tier == 1]
        self.assertEqual(self.notice.text(self.survey, True), "TIER 1 COMPLETE")

    def test_skipping_the_last_point_also_finishes_the_tier(self):
        self.last.skipped = True
        self.assertEqual(self.notice.text(self.survey, False), "TIER 1 COMPLETE (13 skipped)")

    def test_stays_in_the_srv_and_clears_after_boarding(self):
        self._finish()
        self.clock.now = 500
        self.assertTrue(self.notice.text(self.survey, True))     # any time in the SRV
        self.assertTrue(self.notice.text(self.survey, False))    # boarded: the 15 s start here
        self.clock.now = 514.9
        self.assertTrue(self.notice.text(self.survey, False))
        self.clock.now = 515
        self.assertIsNone(self.notice.text(self.survey, False))
        self.assertIsNone(self.notice.text(self.survey, True))   # gone for good

    def test_undo_clears_and_finishing_again_raises_it(self):
        self._finish()
        self.assertTrue(self.notice.text(self.survey, True))
        self.survey.pings.clear()
        self.assertIsNone(self.notice.text(self.survey, True))
        self._finish()
        self.assertTrue(self.notice.text(self.survey, True))

    def test_tier_2_points_dont_matter(self):
        self.assertIsNone(self.notice.text(self.survey, False))
        self.survey.waypoint("O1").skipped = True
        self.assertIsNone(self.notice.text(self.survey, False))

    def test_already_complete_survey_stays_quiet(self):
        self._finish()
        self.assertIsNone(TierNotice(self.clock).text(self.survey, True))

    def test_leaving_clears_it(self):
        self._finish()
        self.assertTrue(self.notice.text(self.survey, True))
        self.assertIsNone(self.notice.text(None, False))

    def test_row_4_priority(self):
        view = OverlayView("I2  212 m", cue=DEPLOY_READY)
        self.assertEqual(with_notice(view, "TIER 1 COMPLETE").cue, DEPLOY_READY)
        self.assertEqual(with_notice(OverlayView("I2  3 km"), "TIER 1 COMPLETE").cue, "TIER 1 COMPLETE")
        self.assertIsNone(with_notice(None, "TIER 1 COMPLETE"))


class FlightTests(unittest.TestCase):
    """The simulated flights, through the real tracker: the overlay's rows stay consistent all the way."""

    def _fly(self, scenario):
        seen = set()
        with tempfile.TemporaryDirectory() as folder:
            tracker = SurveyTracker(SurveyStore(Path(folder)), PATTERN)
            for entry in scenario():
                snapshot = Status.from_json(entry["status"])
                tracker.update(snapshot)
                view = build_view(tracker)
                if view is None or tracker.guidance() is None:
                    continue
                g = tracker.guidance()
                self.assertFalse(view.turn_left and view.turn_right)
                if g.inside_circle:
                    self.assertFalse(view.turn_left or view.turn_right)
                if snapshot.has(IN_SRV):
                    self.assertIsNone(view.pitch)
                    self.assertIsNone(view.cue)
                elif g.inside_circle:
                    self.assertEqual(view.cue, DEPLOY_READY)
                seen.add(view.cue)
        return seen

    def test_full_walk_announces_tier_1_once(self):
        """Raised in the SRV by the last tier 1 ping, gone 15 s after boarding, with no repeat in tier 2."""
        clock = Clock()
        notice = TierNotice(clock)
        changes = []
        shown = None
        with tempfile.TemporaryDirectory() as folder:
            tracker = SurveyTracker(SurveyStore(Path(folder)), PATTERN)
            for entry in full_walk():
                snapshot = Status.from_json(entry["status"])
                tracker.update(snapshot)
                clock.now = entry["t"]
                text = notice.text(tracker.survey, snapshot.has(IN_SRV))
                if text != shown:
                    changes.append((text, snapshot.has(IN_SRV)))
                    shown = text
        self.assertEqual([c[0] for c in changes], ["TIER 1 COMPLETE", None])
        self.assertTrue(changes[0][1])   # raised in the SRV
        self.assertFalse(changes[1][1])  # cleared in the ship

    def test_guided_flight_reaches_deploy_ready(self):
        self.assertIn(DEPLOY_READY, self._fly(guided))

    def test_lithobrake_flight(self):
        self.assertIn(DEPLOY_READY, self._fly(lithobrake))
