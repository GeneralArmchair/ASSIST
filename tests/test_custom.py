"""Desk tests for the custom destination (Milestone 8): a navigation aid to a lat/long, never saved."""

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QSettings
from PySide6.QtWidgets import QApplication

from make_test_recording import CENTER
from assist.deposit_forms import CustomDestinationDialog
from assist.guidance import OFF_TARGET, READY
from assist.map_view import MapWindow, render_image
from assist.overlay import DEPLOY_READY, MISS, build_view
from assist.status import HARDPOINTS
from assist.survey import CUSTOM_LABEL, NO_PING, SurveyStore
from assist.survey_panel import SurveyPanel
from assist.tracker import SurveyTracker
from tests.test_deposits import SRV_STOPPED
from tests.test_guidance import PATTERN, offset, status
from tests.test_map import Case

app = QApplication.instance() or QApplication([])

HARDPOINTS_ON = 1 << HARDPOINTS


class CustomCase(Case):
    """An active survey, the player hovering in the ship at the center."""

    def setUp(self):
        super().setUp()
        self.deploy_here()
        self.survey = self.tracker.survey

    def far_from_points(self):
        """A spot more than 500 m from every survey point: 2.5 km out, between the inner and outer rings' angles."""
        lat, lon = offset(2500, 15)
        nearest = min(self.survey.distance(lat, lon, w.lat, w.lon) for w in self.survey.survey_points)
        self.assertGreater(nearest, 500)
        return lat, lon

    def at(self, lat, lon, flags=None, second=20, altitude=20):
        kwargs = {} if flags is None else {"flags": flags}
        self.tracker.update(status(lat=lat, lon=lon, second=second, altitude=altitude, **kwargs))


class TrackerTests(CustomCase):
    def test_becomes_the_target_with_guidance(self):
        lat, lon = self.far_from_points()
        self.assertTrue(self.tracker.set_custom(lat, lon))
        self.assertEqual(self.survey.target, CUSTOM_LABEL)
        g = self.tracker.guidance()
        self.assertEqual(g.label, CUSTOM_LABEL)
        self.assertAlmostEqual(g.distance, 2500, delta=1)
        self.assertIn("Custom destination: ", self.messages[-1])

    def test_needs_an_active_survey(self):
        tracker = SurveyTracker(SurveyStore(os.path.join(self.folder.name, "other")), PATTERN)
        self.assertFalse(tracker.set_custom(*CENTER))

    def test_never_saved(self):
        previous = self.survey.target
        self.tracker.set_custom(*self.far_from_points())
        with open(os.path.join(self.folder.name, "active.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("custom", data)
        self.assertIsNone(data["target"])
        self.assertNotIn(CUSTOM_LABEL, json.dumps(data))
        # Restarting ASSIST: the custom destination is gone and a survey point is the target again.
        restarted = SurveyTracker(SurveyStore(self.folder.name), PATTERN)
        self.assertIsNone(restarted.survey.custom)
        self.assertEqual(restarted.survey.target, previous)

    def test_stays_through_boarding_and_a_missed_ping_is_still_flagged(self):
        lat, lon = self.far_from_points()
        self.tracker.set_custom(lat, lon)
        self.at(lat, lon, second=20)
        self.at(lat, lon, flags=SRV_STOPPED, second=21)   # deployed, no ping
        self.at(lat, lon, second=22)                      # boarded
        self.assertEqual(self.survey.target, CUSTOM_LABEL)
        self.assertEqual(self.survey.pings[-1].kind, NO_PING)
        self.assertIsNone(self.survey.pings[-1].point)
        self.assertEqual(self.messages[-1], "Target stays on the custom destination")

    def test_a_ping_there_completes_nothing_by_aim(self):
        lat, lon = self.far_from_points()
        self.tracker.set_custom(lat, lon)
        done_before = self.survey.progress()[0]
        self.at(lat, lon, second=20)
        self.at(lat, lon, flags=SRV_STOPPED | HARDPOINTS_ON, second=21)
        ping = self.survey.pings[-1]
        self.assertTrue(ping.counts)
        self.assertIsNone(ping.point)
        self.assertEqual(self.survey.progress()[0], done_before)
        self.assertEqual(self.survey.target, CUSTOM_LABEL)

    def test_a_ping_there_completes_a_survey_point_in_range(self):
        point = next(w for w in self.survey.survey_points if w.label == "O3")
        lat, lon = point.lat, point.lon
        self.tracker.set_custom(lat, lon)
        self.at(lat, lon, second=20)
        self.at(lat, lon, flags=SRV_STOPPED | HARDPOINTS_ON, second=21)
        self.assertIsNotNone(self.survey.done_by(point))
        self.assertEqual(self.survey.target, CUSTOM_LABEL)  # completing a point doesn't end it

    def test_picking_another_target_ends_it(self):
        self.tracker.set_custom(*self.far_from_points())
        self.tracker.select_target("I2")
        self.assertEqual(self.survey.target, "I2")
        self.assertIsNone(self.survey.custom)

    def test_next_point_hotkey_ends_it(self):
        self.tracker.set_custom(*self.far_from_points())
        self.tracker.step_target(1)
        self.assertNotEqual(self.survey.target, CUSTOM_LABEL)
        self.assertIsNone(self.survey.custom)

    def test_a_new_one_replaces_it(self):
        self.tracker.set_custom(*offset(1000, 0))
        self.tracker.set_custom(*offset(2000, 90))
        self.assertAlmostEqual(self.tracker.guidance().distance, 2000, delta=1)

    def test_clear_goes_back_to_the_next_survey_point(self):
        self.tracker.set_custom(*self.far_from_points())
        self.tracker.clear_custom()
        self.assertIsNone(self.survey.custom)
        self.assertEqual(self.survey.target, self.survey.choose_next(*CENTER))
        self.assertIn("Custom destination cleared", self.messages[-1])

    def test_clear_without_one(self):
        target = self.survey.target
        self.tracker.clear_custom()
        self.assertEqual(self.survey.target, target)
        self.assertIn("there isn't one", self.messages[-1])

    def test_skip_and_assign_refuse_it(self):
        self.tracker.set_custom(*self.far_from_points())
        self.tracker.toggle_skip(CUSTOM_LABEL)
        self.assertIn("Skip only applies to survey points", self.messages[-1])
        ping_id = self.survey.pings[-1].id
        self.tracker.assign_to_target(ping_id)
        self.assertIn("not the custom destination", self.messages[-1])
        self.assertNotEqual(self.survey.pings[-1].point, CUSTOM_LABEL)

    def test_points_table_and_progress_unchanged(self):
        progress = self.survey.progress()
        self.tracker.set_custom(*self.far_from_points())
        self.assertEqual(self.survey.progress(), progress)
        self.assertNotIn(CUSTOM_LABEL, [w.label for w in self.survey.waypoints])

    def test_leaving_ends_it(self):
        self.tracker.set_custom(*self.far_from_points())
        self.tracker.close_survey()
        archived = self.store.archived()[0]
        self.assertIsNone(archived.custom)
        self.assertIsNone(archived.target)


class CueTests(CustomCase):
    def test_deploy_ready_inside_the_circle(self):
        lat, lon = self.far_from_points()
        self.tracker.set_custom(lat, lon)
        here = self.survey.custom
        self.at(*offset(2500 - 300, 15))  # 300 m short of it
        self.assertEqual(self.tracker.guidance().cue, READY)
        self.assertEqual(build_view(self.tracker).cue, DEPLOY_READY)
        self.assertIs(self.survey.custom, here)

    def test_miss_outside_the_circle_within_1500_m(self):
        self.tracker.set_custom(*self.far_from_points())
        self.at(*offset(2500 - 1000, 15))
        g = self.tracker.guidance()
        self.assertEqual(g.cue, OFF_TARGET)
        self.assertNotIn("will count", g.cue_text)  # nothing gets completed there
        self.assertEqual(g.cue_text, "Hover OK, 500 m outside the circle")
        self.assertEqual(build_view(self.tracker).cue, MISS)

    def test_no_miss_beyond_1500_m(self):
        self.tracker.set_custom(*self.far_from_points())
        self.at(*offset(2500 - 1800, 15))
        g = self.tracker.guidance()
        self.assertNotEqual(g.cue, OFF_TARGET)
        self.assertEqual(g.cue_text, "Hover OK, 1.30 km outside the circle")
        self.assertIsNone(build_view(self.tracker).cue)

    def test_overlay_title(self):
        self.tracker.set_custom(*offset(1230, 0))
        self.assertEqual(build_view(self.tracker).title, "Custom  1.23 km")


class FormTests(CustomCase):
    def test_saves(self):
        dialog = CustomDestinationDialog(self.tracker)
        dialog.lat.setText(f"{CENTER[0] + 0.01:.4f}")
        dialog.lon.setText(f"{CENTER[1]:.4f}".replace(".", ","))  # a decimal comma is fine
        dialog.save()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        self.assertEqual(self.survey.target, CUSTOM_LABEL)
        self.assertAlmostEqual(self.survey.custom.lat, CENTER[0] + 0.01, places=4)

    def test_rejects_bad_input(self):
        dialog = CustomDestinationDialog(self.tracker)
        dialog.lat.setText("95")
        dialog.lon.setText("10")
        dialog.save()
        self.assertEqual(dialog.message.text(), "Latitude must be between -90 and 90")
        self.assertIsNone(self.survey.custom)

    def test_no_survey(self):
        tracker = SurveyTracker(SurveyStore(os.path.join(self.folder.name, "other")), PATTERN)
        dialog = CustomDestinationDialog(tracker)
        dialog.lat.setText("1")
        dialog.lon.setText("2")
        dialog.save()
        self.assertIn("No active survey", dialog.message.text())


class PanelTests(CustomCase):
    def test_guidance_shows_custom_with_its_lat_long(self):
        settings = QSettings(os.path.join(self.folder.name, "settings.ini"), QSettings.Format.IniFormat)
        panel = SurveyPanel(self.tracker, settings)
        lat, lon = offset(1230, 0)
        self.tracker.set_custom(lat, lon)
        self.assertEqual(panel.g_target.text(), f"Custom ({lat:.4f}, {lon:.4f})  1.23 km")


class MapTests(CustomCase):
    def window(self, survey_id=None):
        window = MapWindow(self.tracker, survey_id)
        window.resize(1000, 720)
        window.show()
        self.addCleanup(window.close)
        return window

    def test_right_click_navigates_to_that_spot(self):
        window = self.window()
        view = window.view
        center = QPointF(view.width() * 0.3, view.height() * 0.5)  # somewhere on the map
        where = view.lat_lon_at(center)
        self.assertIsNotNone(where)
        menu = view.navigate_menu(center)
        action = menu.actions()[0]
        self.assertEqual(action.text(), f"Navigate here ({where[0]:.4f}, {where[1]:.4f})")
        self.assertIsNone(self.survey.custom)  # opening the menu changes nothing
        action.trigger()
        self.assertEqual(self.survey.target, CUSTOM_LABEL)
        self.assertAlmostEqual(self.survey.custom.lat, where[0], places=9)

    def test_the_spot_round_trips_through_the_projection(self):
        from assist.map_view import _Painter
        from PySide6.QtCore import QRectF
        window = self.window()
        view = window.view
        painter = _Painter(None, QRectF(view.rect()), self.survey)
        lat, lon = offset(3000, 120)
        back = view.lat_lon_at(painter.at(lat, lon))
        self.assertLess(self.survey.distance(lat, lon, *back), 1)

    def test_no_menu_off_the_map_or_on_an_archived_map(self):
        window = self.window()
        self.assertIsNone(window.view.navigate_menu(QPointF(window.view.width() - 5, 5)))  # the legend
        self.tracker.close_survey()
        survey_id = self.store.archived()[0].id
        archived = self.window(survey_id)
        view = archived.view
        self.assertIsNone(view.navigate_menu(QPointF(view.width() * 0.3, view.height() * 0.5)))

    def test_drawn_live_only(self):
        self.tracker.set_custom(*offset(1500, 45))
        window = self.window()
        window.view.grab()  # draws the marker without errors
        self.tracker.set_custom(*offset(30000, 45))
        window.view.grab()  # off the map: an edge arrow
        image = render_image(self.survey)  # the saved image has no player, so no custom marker
        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
