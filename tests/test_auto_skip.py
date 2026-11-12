"""Desk tests for skipping survey points that would add under 1% to the coverage (Armchair, 2026-11-08)."""

import unittest

from assist.survey import AUTO, AUTO_SKIPPED, KEPT, Ping, low_value_points
from tests.test_guidance import make_survey, offset
from tests.test_map import Case
from tests.test_surveys import archived_survey

OUTER = [f"O{n}" for n in range(1, 13)]
TIER_2 = ["O1", "O3", "O5", "O7", "O9", "O11"]


def skipped(survey):
    return [w.label for w in survey.survey_points if w.skipped]


def ping(survey, lat, lon, point=None):
    survey.pings.append(Ping(survey.next_ping_id(), None, lat, lon, AUTO, point))


class RuleTests(unittest.TestCase):
    def test_nothing_skipped_without_a_radius(self):
        survey = make_survey()
        self.assertEqual(survey.auto_skip(), ([], []))
        self.assertEqual(skipped(survey), [])

    def test_by_radius(self):
        for radius, expected in ((4.3, OUTER), (4.7, OUTER), (5.2, TIER_2), (6.5, []), (7.0, [])):
            survey = make_survey()
            survey.set_radius(radius)
            survey.auto_skip()
            self.assertEqual(skipped(survey), expected, radius)
            self.assertTrue(all(survey.waypoint(label).auto_skip == AUTO_SKIPPED for label in expected))

    def test_a_ping_off_target_keeps_the_outer_point_next_to_it(self):
        survey = make_survey()
        for w in survey.survey_points[:7]:
            if w.label == "I1":
                ping(survey, *offset(1900, 0), point="I1")  # aimed at I1 (3.4 km north), landed 1.5 km short
            else:
                ping(survey, w.lat, w.lon, w.label)
        survey.set_radius(4.7)
        survey.auto_skip()
        self.assertNotIn("O1", skipped(survey))
        self.assertIn("O7", skipped(survey))

    def test_twins_do_not_both_go(self):
        """Two candidates on the same spot each add nothing the other doesn't: only one is dropped."""
        self.assertEqual(low_value_points(4000, [], {"a": (0, 0), "b": (0, 0)}), ["a"])

    def test_clearing_the_radius_unskips(self):
        survey = make_survey()
        survey.set_radius(4.3)
        survey.auto_skip()
        survey.set_radius(None)
        self.assertEqual(survey.auto_skip(), ([], OUTER))
        self.assertEqual(skipped(survey), [])
        self.assertTrue(all(w.auto_skip is None for w in survey.survey_points))


class PlayerTests(Case):
    def setUp(self):
        super().setUp()
        self.deploy_here()
        self.survey = self.tracker.survey

    def test_setting_the_radius_skips_and_says_so(self):
        self.tracker.set_radius(4.3)
        self.assertEqual(skipped(self.survey), OUTER)
        self.assertTrue(any(m.startswith("Skipped O1, O2") and "under 1%" in m for m in self.messages), self.messages)
        self.assertNotIn(self.survey.target, OUTER)

    def test_unskipped_by_the_player_stays_unskipped(self):
        self.tracker.set_radius(4.3)
        self.tracker.toggle_skip("O4")
        self.assertEqual(self.survey.waypoint("O4").auto_skip, KEPT)
        self.tracker.set_radius(4.2)  # the rule runs again
        self.assertFalse(self.survey.waypoint("O4").skipped)

    def test_player_skips_are_left_alone_and_count(self):
        self.tracker.set_radius(5.2)
        self.assertIn("O1", skipped(self.survey))
        self.tracker.toggle_skip("I1")  # nobody will scan I1 now, so O1 (in line with it) is worth it again
        self.assertNotIn("O1", skipped(self.survey))
        self.assertIn("I1", skipped(self.survey))
        self.assertIsNone(self.survey.waypoint("I1").auto_skip)
        self.assertTrue(any(m.startswith("No longer skipped: O1") for m in self.messages), self.messages)

    def test_archived_survey(self):
        survey = archived_survey(self.store, "2026-11-08T07:00:00Z")
        self.tracker.editor(survey.id).set_radius(4.3)
        self.assertEqual(skipped(self.store.load_archived(survey.id)), OUTER)


if __name__ == "__main__":
    unittest.main()
