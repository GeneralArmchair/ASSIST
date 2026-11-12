"""Desk tests for Milestone 7: the golden-ring radius, coverage, and the survey map."""

import json
import math
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from assist import coverage as coverage_module
from assist import geo
from assist.coverage import ASSUMED_RADIUS, Coverage, coverage
from assist.deposit_forms import RadiusDialog
from assist.map_view import (IMAGE_SIZE, ORE_COLORS, MapWindow, _Painter, caption_lines, degrees_text, game_date, grid,
                             image_name, legend_groups, nice_step, ore_colors, ores_by_value, render_image, step_decimals,
                             title_lines, view_extent)
from assist.survey import AUTO, CONFIRMED, MANUAL, NO_PING, Deposit, Ping, SurveyStore, parse_radius_km, unusual_radius
from assist.survey_panel import SurveyPanel
from assist.surveys_dialog import SURVEY_COLUMNS, SurveysDialog
from assist.tracker import SurveyTracker
from tests.test_guidance import CENTER, PATTERN, PLANET_RADIUS, make_survey, offset, status
from tests.test_surveys import SRV_STOPPED, archived_survey

app = QApplication.instance() or QApplication([])


def ping_at(survey, meters, bearing=0, kind=AUTO):
    lat, lon = offset(meters, bearing)
    survey.pings.append(Ping(survey.next_ping_id(), None, lat, lon, kind))


class Case(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SurveyStore(self.folder.name)
        self.tracker = SurveyTracker(self.store, PATTERN)
        self.messages = []
        self.tracker.add_listener(lambda m: m and self.messages.append(m))
        coverage_module._cache.clear()

    def deploy_here(self, second=1):
        self.tracker.update(status(second=second))
        self.tracker.update(status(flags=SRV_STOPPED, second=second + 1))
        self.tracker.update(status(second=second + 2))


class RadiusInputTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_radius_km("5.12"), 5.12)
        self.assertEqual(parse_radius_km(" 5,12 "), 5.12)
        self.assertIsNone(parse_radius_km("  "))  # blank: not measured
        for bad in ("abc", "0", "-3", "21"):
            with self.assertRaises(ValueError):
                parse_radius_km(bad)

    def test_unusual(self):
        self.assertFalse(unusual_radius(4.7))
        self.assertFalse(unusual_radius(6.5))
        self.assertTrue(unusual_radius(3.9))
        self.assertTrue(unusual_radius(8.1))


class RadiusTests(Case):
    def test_set_on_the_ring_stores_where_and_the_center_offset(self):
        survey = make_survey()
        lat, lon = offset(5100, 90)  # standing 5.1 km from ASSIST's center, game says 5.2 km
        message = survey.set_radius(5.2, "2025-01-01T00:00:09Z", lat, lon)
        self.assertEqual(survey.poi.radius, 5200)
        self.assertEqual(survey.poi.radius_time, "2025-01-01T00:00:09Z")
        self.assertAlmostEqual(survey.center_offset(), 100, delta=0.5)
        self.assertIn("at least 100 m", message)

    def test_clear(self):
        survey = make_survey()
        survey.set_radius(5.2, None, *offset(5200))
        self.assertIn("cleared", survey.set_radius(None))
        self.assertIsNone(survey.poi.radius)
        self.assertIsNone(survey.poi.radius_lat)
        self.assertIsNone(survey.center_offset())

    def test_no_position_no_offset(self):
        survey = make_survey()
        survey.set_radius(5.2)
        self.assertIsNone(survey.center_offset())

    def test_tracker_saves_it_with_the_position(self):
        self.deploy_here()
        self.tracker.update(status(flags=SRV_STOPPED, lat=offset(5000, 90)[0], lon=offset(5000, 90)[1], second=9))
        self.tracker.set_radius(5.0, self.tracker.radius_position())
        saved = self.store.load_active()
        self.assertEqual(saved.poi.radius, 5000)
        self.assertAlmostEqual(saved.center_offset(), 0, delta=0.5)
        self.assertIn("POI radius set to 5.00 km", self.messages[-1])

    def test_tracker_without_a_survey(self):
        self.tracker.set_radius(5.0)
        self.assertEqual(self.messages[-1], "POI radius: no active survey")

    def test_archived_survey(self):
        survey = archived_survey(self.store, "2026-11-08T07:00:00Z")
        self.tracker.editor(survey.id).set_radius(5.5)
        on_disk = self.store.load_archived(survey.id)
        self.assertEqual(on_disk.poi.radius, 5500)
        self.assertIsNone(on_disk.poi.radius_lat)  # typed at the desk, not on the ring
        self.assertIn("Archived survey", self.messages[-1])

    def test_old_file_without_the_new_fields_loads(self):
        survey = archived_survey(self.store, "2026-11-08T07:00:00Z")
        path = self.store.archive_path(survey.id)
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("radius_time", "radius_lat", "radius_lon"):
            del data["poi"][key]
        data["version"] = 2
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(self.store.load_archived(survey.id).poi.radius)


class RadiusFormTests(Case):
    def form(self, survey=None):
        self.deploy_here()
        return RadiusDialog(self.tracker, survey or self.tracker.survey, self.tracker.radius_position())

    def test_save(self):
        form = self.form()
        form.range.setText("5.12")
        form.save()
        self.assertEqual(self.tracker.survey.poi.radius, 5120)
        self.assertEqual(form.result(), RadiusDialog.DialogCode.Accepted)

    def test_unusual_needs_a_second_save(self):
        form = self.form()
        form.range.setText("9.5")
        form.save()
        self.assertIsNone(self.tracker.survey.poi.radius)
        self.assertIn("outside the usual", form.message.text())
        form.save()
        self.assertEqual(self.tracker.survey.poi.radius, 9500)

    def test_changed_text_is_warned_again(self):
        form = self.form()
        form.range.setText("9.5")
        form.save()
        form.range.setText("3.0")
        form.save()
        self.assertIsNone(self.tracker.survey.poi.radius)

    def test_rejected(self):
        form = self.form()
        form.range.setText("25")
        form.save()
        self.assertIn("more than 20 km", form.message.text())
        self.assertIsNone(self.tracker.survey.poi.radius)

    def test_opens_with_the_current_value_and_blank_clears(self):
        self.deploy_here()
        self.tracker.set_radius(5.2)
        form = RadiusDialog(self.tracker, self.tracker.survey)
        self.assertEqual(form.range.text(), "5.20")
        form.range.setText("")
        form.save()
        self.assertIsNone(self.tracker.survey.poi.radius)


class CoverageTests(Case):
    def test_one_ping_at_the_center(self):
        survey = make_survey()
        ping_at(survey, 0)
        survey.set_radius(2.0)
        self.assertAlmostEqual(coverage(survey).fraction, 1.0, places=3)
        survey.set_radius(4.0)
        self.assertAlmostEqual(coverage(survey).fraction, 0.25, places=3)  # area ratio (2/4)²

    def test_no_pings(self):
        self.assertEqual(coverage(make_survey()).fraction, 0.0)

    def test_no_ping_detected_entries_dont_count(self):
        survey = make_survey()
        ping_at(survey, 0, kind=NO_PING)
        self.assertEqual(coverage(survey).fraction, 0.0)
        for kind in (MANUAL, CONFIRMED):
            other = make_survey()
            other.id += kind
            ping_at(other, 0, kind=kind)
            self.assertGreater(coverage(other).fraction, 0)

    def test_assumed_radius_text(self):
        survey = make_survey()
        ping_at(survey, 0)
        result = coverage(survey)
        self.assertTrue(result.assumed)
        self.assertEqual(result.radius, ASSUMED_RADIUS)
        self.assertEqual(result.text(), "8% (assumed 7km radius)")  # Armchair's wording

    def test_measured_text(self):
        self.assertEqual(Coverage(0.912, 5120, False).text(), "91% of 5.12 km")

    def test_tier_one_matches_the_earlier_simulation(self):
        """Perfect tier 1 landings: 100% at 4.7 km and 88.7% at 6.5 km (Milestone 3)."""
        survey = make_survey()
        for w in survey.survey_points:
            if w.tier == 1:
                survey.pings.append(Ping(survey.next_ping_id(), None, w.lat, w.lon, AUTO))
        survey.set_radius(4.7)
        self.assertGreater(coverage(survey).fraction, 0.999)
        survey.set_radius(6.5)
        self.assertAlmostEqual(coverage(survey).fraction, 0.887, delta=0.003)

    def test_pings_outside_the_disk_add_only_what_is_inside(self):
        survey = make_survey()
        ping_at(survey, 7000)  # its circle reaches 5 km from the center: just past a 4.7 km ring
        survey.set_radius(4.7)
        self.assertEqual(coverage(survey).fraction, 0.0)

    def test_cache_follows_changes(self):
        survey = make_survey()
        ping_at(survey, 0)
        first = coverage(survey)
        self.assertIs(coverage(survey), first)
        ping_at(survey, 3400, 90)
        self.assertGreater(coverage(survey).fraction, first.fraction)
        survey.set_radius(3.0)
        self.assertFalse(coverage(survey).assumed)


class EastNorthTests(unittest.TestCase):
    def test_directions(self):
        survey = make_survey()
        east, north = survey.east_north(*offset(3000, 0))
        self.assertAlmostEqual(east, 0, delta=0.01)
        self.assertAlmostEqual(north, 3000, delta=0.01)
        east, north = survey.east_north(*offset(3000, 90))
        self.assertAlmostEqual(east, 3000, delta=0.01)
        self.assertAlmostEqual(north, 0, delta=0.5)
        self.assertEqual(survey.east_north(*CENTER), (0.0, 0.0))


def add_deposit(survey, ore, size=None, meters=1000, bearing=0, measured=False):
    lat, lon = offset(meters, bearing)
    deposit = Deposit(survey.next_deposit_id(), ore, None, lat, lon, size=size)
    if measured:
        deposit.measured_lat, deposit.measured_lon = lat, lon
    survey.deposits.append(deposit)
    return deposit


class LegendTests(unittest.TestCase):
    def test_ores_most_valuable_first(self):
        survey = make_survey()
        for ore in ("Water", "Platinum", "Monazite", "Platinum"):
            add_deposit(survey, ore)
        self.assertEqual(ores_by_value(survey), ["Monazite", "Platinum", "Water"])

    def test_colours_reused_from_the_sixth_ore(self):
        survey = make_survey()
        ores = ["Alexandrite", "Monazite", "Grandidierite", "Rhodplumsite", "Serendibite", "Iridium", "Water"]
        for ore in ores:
            add_deposit(survey, ore)
        colors = ore_colors(survey)
        self.assertEqual(len({colors[o].name() for o in ores[:5]}), 5)
        self.assertEqual(colors["Iridium"], ORE_COLORS[0])
        self.assertEqual(colors["Water"], ORE_COLORS[1])
        for color in ORE_COLORS:  # never the scan tint's red or the ring's gold
            self.assertFalse(color.red() > 180 and color.green() < 100 and color.blue() < 100)
            self.assertNotEqual(color.name(), "#d4af37")

    def test_within_an_ore_largest_first_unsized_last(self):
        survey = make_survey()
        a = add_deposit(survey, "Platinum", None)
        b = add_deposit(survey, "Platinum", 2, bearing=90)
        c = add_deposit(survey, "Platinum", 5, bearing=180)
        d = add_deposit(survey, "Gold", 1, bearing=270)
        self.assertEqual(legend_groups(survey), [("Platinum", [c, b, a]), ("Gold", [d])])


class MapTests(Case):
    def survey(self):
        survey = make_survey()
        survey.poi.name = "Mining Signal (5)"
        survey.poi.system = "Sol"
        for w in survey.survey_points[:7]:
            survey.pings.append(Ping(survey.next_ping_id(), None, w.lat, w.lon, AUTO, w.label))
        ping_at(survey, 5500, 30, NO_PING)
        add_deposit(survey, "Platinum", 4, measured=True)
        add_deposit(survey, "Gold", None, meters=15000, bearing=200)  # beyond the map: an edge arrow
        return survey

    def test_image_is_fixed_size(self):
        image = render_image(self.survey())
        self.assertEqual((image.width(), image.height()), IMAGE_SIZE)

    def test_diamonds_are_filled_when_the_size_is_known(self):
        survey = make_survey()
        sized = add_deposit(survey, "Platinum", 4, meters=1500, bearing=90)           # not measured, but sized
        unsized = add_deposit(survey, "Platinum", None, meters=1500, bearing=270, measured=True)
        image = render_image(survey)
        painter = _Painter(None, QRectF(0, 0, *IMAGE_SIZE), survey, font_px=17)
        color = ore_colors(survey)["Platinum"]
        def pixel(deposit):
            at = painter.at(*deposit.position)
            return image.pixelColor(round(at.x()), round(at.y()))
        self.assertEqual(pixel(sized).rgb(), color.rgb())
        self.assertNotEqual(pixel(unsized).rgb(), color.rgb())

    def test_view_fits_the_ring_and_every_ping_circle(self):
        survey = make_survey()
        ping_at(survey, 6000)
        self.assertGreaterEqual(view_extent(survey), 8000)
        self.assertGreaterEqual(view_extent(make_survey()), ASSUMED_RADIUS)

    def test_caption_has_no_paths_and_uses_game_time(self):
        survey = self.survey()
        lines = caption_lines(survey)
        self.assertIn("UTC", lines[0])
        self.assertIn("Radius: not measured", lines)
        self.assertTrue(lines[-1].endswith("(assumed 7km radius)"))
        text = " ".join(lines)
        self.assertNotIn("\\", text)
        self.assertNotIn(os.path.expanduser("~"), text)

    def test_game_date(self):
        self.assertEqual(game_date("2026-11-08T07:00:00Z"), "2026-11-08 07:00 UTC")

    def test_title_is_system_body_poi(self):
        survey = self.survey()
        survey.poi.system, survey.poi.body = "LFT 509", "LFT 509 B 3 b"
        self.assertEqual(title_lines(survey), ["LFT 509", "LFT 509 B 3 b", "Mining Signal (5)"])
        survey.poi.system, survey.poi.name = None, None
        self.assertEqual(title_lines(survey), ["(system unknown)", "LFT 509 B 3 b", "(unnamed POI)"])


    def test_live_window_follows_the_active_survey(self):
        window = MapWindow(self.tracker)
        self.assertIsNone(window.survey())
        self.deploy_here()
        self.assertIs(window.survey(), self.tracker.survey)
        window.show()
        self.assertIn("active survey", window.windowTitle())
        self.assertIsNotNone(window._player())
        window.close()

    def test_saves_an_image(self):
        self.deploy_here()
        window = MapWindow(self.tracker)
        path = os.path.join(self.folder.name, "map.png")
        window.write_image(self.tracker.survey, path)
        self.assertEqual(QImage(path).size().width(), IMAGE_SIZE[0])
        self.assertIn("Saved", window.message.text())
        default = window.default_image_path(self.tracker.survey)
        self.assertEqual(default.parent.name, "maps")
        self.assertEqual(default.name, image_name(self.tracker.survey))
        window.close()

    def test_archived_window_rereads_its_file(self):
        survey = archived_survey(self.store, "2026-11-08T07:00:00Z")
        window = MapWindow(self.tracker, survey.id)
        window.show()
        self.assertIsNone(window._player())  # no live position on an archived survey's map
        self.tracker.editor(survey.id).set_radius(5.0)
        self.assertEqual(window.survey().poi.radius, 5000)
        window.close()

    def test_redraws_on_moving_only(self):
        self.deploy_here()
        window = MapWindow(self.tracker)
        window.status_changed()
        first = window._last_player
        self.tracker.update(status(second=10))  # same place
        window.status_changed()
        self.assertEqual(window._last_player, first)
        self.tracker.update(status(lat=offset(100)[0], lon=offset(100)[1], second=11))
        window.status_changed()
        self.assertNotEqual(window._last_player, first)
        window.close()


class ImageNameTests(unittest.TestCase):
    def survey(self, body="LFT 509 B 3 b", name="Mining Signal (5)"):
        survey = make_survey()
        survey.poi.body, survey.poi.name = body, name
        survey.created = "2026-11-08T07:36:14Z"
        return survey

    def test_body_signal_and_time(self):
        self.assertEqual(image_name(self.survey()), "LFT 509 B 3 b - Signal (5) - 2026-11-08 07-36.png")

    def test_long_signal_names_are_shortened(self):
        name = image_name(self.survey(body="Tascheter Sector GW-W c1-17 5", name="Planetary Mining Location Signal (10)"))
        self.assertEqual(name, "Tascheter Sector GW-W c1-17 5 - Signal (10) - 2026-11-08 07-36.png")
        self.assertIn(" - Rich Ice Field - ", image_name(self.survey(name="Rich Ice Field")))  # no "Signal": as it is

    def test_no_poi_name(self):
        self.assertEqual(image_name(self.survey(name=None)), "LFT 509 B 3 b - 2026-11-08 07-36.png")

    def test_characters_windows_refuses(self):
        name = image_name(self.survey(body='A/B:C*D?"E<F>G|H' + chr(92) + "I", name="Mining Signal"))
        self.assertEqual(name, "A-B-C-D--E-F-G-H-I - Signal - 2026-11-08 07-36.png")
        self.assertEqual(image_name(self.survey(body="Mitterand's World")).split(" - ")[0], "Mitterand's World")

    def test_long_names_are_shortened(self):
        name = image_name(self.survey(body="Hypuae Euq " * 20, name="Mining Signal " * 20))
        self.assertLessEqual(len(name), 150)
        self.assertTrue(name.startswith("Hypuae Euq"))
        self.assertIn(" - Signal", name)
        self.assertTrue(name.endswith(" - 2026-11-08 07-36.png"))

    def test_time_is_utc(self):
        survey = self.survey()
        survey.created = "2026-11-08T09:36:14+02:00"
        self.assertTrue(image_name(survey).endswith("2026-11-08 07-36.png"))


class GridTests(unittest.TestCase):
    def test_round_steps(self):
        self.assertAlmostEqual(nice_step(0.4), 0.05)
        self.assertAlmostEqual(nice_step(0.8), 0.1)
        self.assertAlmostEqual(nice_step(1.5), 0.1)
        self.assertAlmostEqual(nice_step(1.6), 0.2)
        self.assertAlmostEqual(nice_step(360), 20)
        self.assertEqual([step_decimals(s) for s in (0.05, 0.1, 0.2, 2, 0.001)], [2, 1, 1, 0, 3])
        self.assertEqual(degrees_text(-0.0001, 2), "0.00")
        self.assertEqual(degrees_text(-12.35, 2), "-12.35")

    def test_reverse_projection(self):
        survey = make_survey()
        lat, lon = survey.lat_lon(3000, -4000)
        east, north = survey.east_north(lat, lon)
        self.assertAlmostEqual(east, 3000, delta=0.01)
        self.assertAlmostEqual(north, -4000, delta=0.01)

    def test_lines_are_at_their_lat_and_long(self):
        survey = make_survey()
        extent = 7000
        lat_lines, lon_lines, lat_step, lon_step = grid(survey, extent)
        self.assertTrue(7 <= len(lat_lines) <= 21, len(lat_lines))
        self.assertTrue(7 <= len(lon_lines) <= 21, len(lon_lines))
        for lat, line in lat_lines:
            self.assertAlmostEqual(lat / lat_step, round(lat / lat_step), places=6)  # round values only
            for east, north in line[::12]:
                self.assertAlmostEqual(survey.lat_lon(east, north)[0], lat, places=6)
        for lon, line in lon_lines:
            self.assertAlmostEqual(lon / lon_step, round(lon / lon_step), places=6)
            for east, north in line[::12]:
                self.assertAlmostEqual(survey.lat_lon(east, north)[1], lon, places=6)
        # The lines cover the whole map, corner to corner.
        lats = [lat for lat, _ in lat_lines]
        corner_lat = survey.lat_lon(extent, extent)[0]
        self.assertGreater(max(lats) + lat_step, corner_lat)

    def test_across_the_date_line(self):
        survey = make_survey()
        survey.poi.center_lon = 179.99
        _, lon_lines, _, _ = grid(survey, 7000)
        lons = [lon for lon, _ in lon_lines]
        self.assertTrue(all(-180 <= lon <= 180 for lon in lons))
        self.assertTrue(any(lon > 179 for lon in lons) and any(lon < -179 for lon in lons), lons)

    def test_near_a_pole(self):
        survey = make_survey()
        survey.poi.center_lat = 89.99
        lat_lines, lon_lines, _, _ = grid(survey, 7000)
        self.assertTrue(lat_lines and lon_lines)
        self.assertLessEqual(len(lon_lines), 21)
        render_image(survey)  # draws without trouble


class WindowTests(Case):
    def test_panel_shows_radius_and_coverage(self):
        settings = QSettings(os.path.join(self.folder.name, "settings.ini"), QSettings.Format.IniFormat)
        panel = SurveyPanel(self.tracker, settings)
        self.deploy_here()
        panel.refresh()
        self.assertIn("not measured", panel.values["Radius"].text())
        self.assertTrue(panel.values["Coverage"].text().endswith("(assumed 7km radius)"))
        self.tracker.set_radius(4.0)
        panel.refresh()
        self.assertEqual(panel.values["Radius"].text(), "4.00 km")
        self.assertIn("of 4.00 km", panel.values["Coverage"].text())

    def test_skipped_rows_are_grey_and_struck_through(self):
        settings = QSettings(os.path.join(self.folder.name, "settings.ini"), QSettings.Format.IniFormat)
        panel = SurveyPanel(self.tracker, settings)
        self.deploy_here()
        self.tracker.set_radius(4.3)  # ASSIST skips the outer ring
        panel.refresh()
        rows = {panel.points.item(r, 0).text(): r for r in range(panel.points.rowCount())}
        skipped, open_point = rows["O1"], rows["I1"]
        self.assertTrue(panel.points.item(skipped, 0).font().strikeOut())
        self.assertFalse(panel.points.item(skipped, 2).font().strikeOut())  # the state stays easy to read
        self.assertEqual(panel.points.item(skipped, 2).text(), "skipped (adds under 1%)")
        self.assertNotEqual(panel.points.item(skipped, 0).foreground().color(),
                            panel.points.item(open_point, 0).foreground().color())
        self.assertFalse(panel.points.item(open_point, 0).font().strikeOut())

    def test_panel_set_radius_opens_one_form(self):
        settings = QSettings(os.path.join(self.folder.name, "settings.ini"), QSettings.Format.IniFormat)
        panel = SurveyPanel(self.tracker, settings)
        panel.set_radius()
        self.assertIsNone(panel.radius_form)  # no active survey
        self.deploy_here()
        panel.set_radius()
        form = panel.radius_form
        panel.set_radius()
        self.assertIs(panel.radius_form, form)  # pressing again brings the same form back
        form.reject()

    def test_surveys_list_columns(self):
        measured = archived_survey(self.store, "2026-11-08T07:00:00Z")
        archived_survey(self.store, "2026-11-08T08:00:00Z", body="Other 2")
        self.tracker.editor(measured.id).set_radius(5.0)
        settings = QSettings(os.path.join(self.folder.name, "settings.ini"), QSettings.Format.IniFormat)
        dialog = SurveysDialog(self.tracker, settings)
        radius_column, coverage_column = SURVEY_COLUMNS.index("Radius"), SURVEY_COLUMNS.index("Coverage")
        rows = {dialog.table.item(r, 2).text(): r for r in range(dialog.table.rowCount())}
        self.assertEqual(dialog.table.item(rows["Elsewhere 1"], radius_column).text(), "5.00 km")
        self.assertEqual(dialog.table.item(rows["Other 2"], radius_column).text(), "—")
        assumed = dialog.table.item(rows["Other 2"], coverage_column)
        self.assertIn("(assumed 7km radius)", assumed.toolTip())
        self.assertEqual(assumed.text(), "0%")
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
