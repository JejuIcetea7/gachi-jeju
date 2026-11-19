import copy
import unittest

from backend.app.services.lodging_planning import prepare_undecided_lodging_plan, validate_lodging_boundaries


def move(identifier):
    return {"item_type": "move", "route_id": identifier, "title": identifier,
            "start_time": "09:00", "end_time": "09:10", "duration_minutes": 10}


def visit(name="관광지"):
    return {"item_type": "visit", "title": name, "start_time": "09:10", "end_time": "10:10",
            "duration_minutes": 60, "accessibility_notes": ["확인한 안내"]}


def plan():
    return {"assumptions": ["기존 조건"], "days": [
        {"day": 1, "items": [move("initial"), visit("첫날 관광지"), move("unknown-arrival")]},
        {"day": 2, "items": [move("unknown-departure"), visit("다음 날 관광지"), move("return")]},
    ]}


class UndecidedLodgingPlanTests(unittest.TestCase):
    def prepare(self, value, *, days=2, status="undecided", stays=None):
        return prepare_undecided_lodging_plan(value, trip_days=days, lodging_status=status, lodging_stays=stays or [])

    def test_omits_only_unselected_boundary_moves_and_preserves_original(self):
        original = plan()
        before = copy.deepcopy(original)
        prepared, warnings = self.prepare(original)
        self.assertEqual([item["title"] for item in prepared["days"][0]["items"]], ["initial", "첫날 관광지"])
        self.assertEqual([item["title"] for item in prepared["days"][1]["items"]], ["다음 날 관광지", "return"])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(original, before)
        prepared["days"][0]["items"][1]["accessibility_notes"].append("변경")
        self.assertEqual(original, before)

    def test_consecutive_boundary_moves_are_removed_without_touching_internal_move(self):
        value = {"days": [
            {"day": 1, "items": [move("initial"), visit(), move("walk-to-unknown"), move("bus-to-unknown")]},
            {"day": 2, "items": [move("walk-from-unknown"), move("bus-from-unknown"),
                                 visit("첫 관광지"), move("internal"), visit("둘째 관광지"), move("return")]},
        ]}
        prepared, warnings = self.prepare(value)
        self.assertEqual([item["route_id"] for item in prepared["days"][1]["items"] if item["item_type"] == "move"],
                         ["internal", "return"])
        self.assertTrue(all("2개" in warning for warning in warnings))

    def test_selected_stay_boundaries_are_preserved_for_strict_validation(self):
        original = plan()
        prepared, warnings = self.prepare(original, stays=[{"night": 1}])
        self.assertEqual(prepared, original)
        self.assertEqual(warnings, [])

    def test_partial_stays_only_remove_unknown_night_boundaries(self):
        value = {"days": [
            {"day": 1, "items": [move("initial"), visit(), move("unknown-night1")]},
            {"day": 2, "items": [move("unknown-start2"), visit(), move("known-night2")]},
            {"day": 3, "items": [move("known-start3"), visit(), move("return")]},
        ]}
        prepared, _ = self.prepare(value, days=3, stays=[{"night": 2}])
        self.assertEqual(prepared["days"][0]["items"][-1]["item_type"], "visit")
        self.assertEqual(prepared["days"][1]["items"][0]["item_type"], "visit")
        self.assertEqual(prepared["days"][1]["items"][-1]["route_id"], "known-night2")
        self.assertEqual(prepared["days"][2]["items"][0]["route_id"], "known-start3")

    def test_visit_times_durations_and_existing_assumptions_are_not_rewritten(self):
        original = plan()
        prepared, _ = self.prepare(original)
        for before, after in zip(original["days"], prepared["days"]):
            self.assertEqual([item for item in before["items"] if item["item_type"] == "visit"],
                             [item for item in after["items"] if item["item_type"] == "visit"])
        self.assertEqual(prepared["assumptions"][0], "기존 조건")
        self.assertTrue(any("현재 방문 시각은 유지" in note for note in prepared["assumptions"]))

    def test_preparation_is_idempotent_and_satisfies_boundary_validation(self):
        prepared, _ = self.prepare(plan())
        again, warnings = self.prepare(prepared)
        self.assertEqual(again, prepared)
        self.assertEqual(warnings, [])
        self.assertEqual(validate_lodging_boundaries(
            prepared, trip_days=2, lodging_status="undecided", lodging_stays=[],
        ), [])

    def test_old_one_day_and_no_overnight_requests_keep_previous_behavior(self):
        for days, status in ((1, "undecided"), (2, None), (2, "not_needed")):
            prepared, warnings = self.prepare(plan(), days=days, status=status)
            self.assertEqual(prepared, plan())
            self.assertEqual(warnings, [])

    def test_days_without_visits_are_not_silently_turned_into_empty_success(self):
        value = {"days": [{"day": 1, "items": []}, {"day": 2, "items": [move("unknown")]}]}
        prepared, warnings = self.prepare(value)
        self.assertEqual(prepared, value)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
