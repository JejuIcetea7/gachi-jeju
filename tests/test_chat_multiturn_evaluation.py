import copy
import unittest
from unittest.mock import patch

from backend.app.routers.chat import ChatOptions
from backend.app.services import chat_api_evaluation as api_evaluation
from scripts import chat_multiturn_evaluation as evaluation
from scripts.chat_multiturn_state import build_followup_request


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
DESTINATION = {"latitude": 33.41, "longitude": 126.51}
OTHER = {"latitude": 33.42, "longitude": 126.52}
STAY = {
    "night": 1, "name": "테스트 숙소", "address": "",
    "latitude": 33.415, "longitude": 126.515,
    "provider_place_id": "123", "place_url": "https://place.map.kakao.com/123",
}


def make_record(minutes=60, route_id="new"):
    options = ChatOptions(
        departure="테스트 출발지", **{
            "departure_latitude": ORIGIN["latitude"],
            "departure_longitude": ORIGIN["longitude"],
        },
        start_date="2026-11-20", trip_days=1, transport="driving",
        mobility_aid="wheelchair", requirements=["accessible_toilet"],
        travel_styles=["공원"], return_to_origin=False,
    ).model_dump(mode="json")
    visit = {
        "start_time": "09:10", "end_time": "10:40" if minutes == 90 else "10:10",
        "item_type": "visit", "title": "테스트 관광지", "place_id": "TEST",
        "verified": True, "category": "공원", **DESTINATION,
        "transport": None, "duration_minutes": minutes, "distance_km": None,
        "accessibility_notes": [], "route_id": None,
    }
    move = {
        **visit, "start_time": "09:00", "end_time": "09:10",
        "item_type": "move", "title": "출발지 → 관광지", "place_id": None,
        "verified": None, "category": None, "latitude": None, "longitude": None,
        "transport": "자동차", "duration_minutes": 10, "distance_km": 1,
        "route_id": route_id,
    }
    body = {
        "reply": "요청한 일정입니다.", "options": copy.deepcopy(options),
        "plan": {"assumptions": [], "days": [{"day": 1, "items": [move, visit]}]},
        "map_routes": [], "transit_routes": [], "warnings": [],
        "meta": {"model_call_count": 1, "tool_call_counts": {}, "elapsed_seconds": 1},
    }
    routes = [{
        "route_id": route_id, "transport": "driving",
        "origin": dict(ORIGIN), "destination": dict(DESTINATION),
        "duration_seconds": 600, "distance_m": 1000,
    }]
    trace = [
        {"call_id": "search", "tool": "search_places", "input": {},
         "output": {"status": "ok", "places": [{"place_id": "TEST"}, {"place_id": "OTHER"}]}},
        {"call_id": "drive", "tool": "get_driving_time", "input": {},
         "output": {"status": "ok", "route_id": route_id, "duration_seconds": 600}},
    ]
    return {
        "request": {"message": "일정을 만들어 주세요.", "options": options},
        "http_status": 200, "response": body, "elapsed_seconds": 1,
        "attempts": [{
            "attempt": 1, "status": "returned", "capture_error": None,
            "error_type": None, "retry_feedback": [], "chat_reply": copy.deepcopy(body),
            "route_calls": routes, "tool_trace": trace,
            "metrics": {"model_call_count": 1, "tool_call_counts": {}}, "process": None,
        }],
    }


def make_case(record, **extra):
    return {
        "case_id": "M01-T02", "description": "연속 대화 단계 검증",
        "request": copy.deepcopy(record["request"]), "expectation": {"outcome": "plan"},
        **extra,
    }


def make_followup(previous, *, options_patch=None):
    record = make_record(90)
    record["request"] = build_followup_request(
        previous, "체류시간을 90분으로 바꿔 주세요.", options_patch=options_patch,
    )
    record["response"]["options"] = copy.deepcopy(record["request"]["options"])
    return record


def add_lodging_days(record, *, with_moves):
    options = record["request"]["options"]
    options.update(trip_days=2, lodging_status="provided", lodging_stays=[dict(STAY)])
    record["response"]["options"] = copy.deepcopy(options)
    days = record["response"]["plan"]["days"]
    visit = days[0]["items"][1]
    other_visit = {**copy.deepcopy(visit), "place_id": "OTHER",
                   "title": "다른 관광지", **OTHER}
    days.append({"day": 2, "items": [other_visit]})
    if with_moves:
        arrival = {**copy.deepcopy(days[0]["items"][0]),
                   "title": "관광지 → 숙소", "start_time": "10:10", "end_time": "10:20",
                   "route_id": "to-stay"}
        departure = {**copy.deepcopy(days[0]["items"][0]),
                     "title": "숙소 → 관광지", "route_id": "from-stay"}
        days[0]["items"].append(arrival)
        days[1]["items"].insert(0, departure)
        for route_id, origin, destination in (
            ("to-stay", DESTINATION, STAY), ("from-stay", STAY, OTHER),
        ):
            record["attempts"][0]["route_calls"].append({
                "route_id": route_id, "transport": "driving",
                "origin": {key: origin[key] for key in ("latitude", "longitude")},
                "destination": {key: destination[key] for key in ("latitude", "longitude")},
                "duration_seconds": 600,
            })
            record["attempts"][0]["tool_trace"].append({
                "call_id": route_id, "tool": "get_driving_time", "input": {},
                "output": {"status": "ok", "route_id": route_id, "duration_seconds": 600},
            })


class ChatMultiturnEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(
            api_evaluation, "read_places_by_id", return_value={
                "TEST": {"place_id": "TEST", "name": "테스트 관광지"},
                "OTHER": {"place_id": "OTHER", "name": "다른 관광지"},
            },
        ))

    def test_case_validation_is_repeatable_and_preserves_input(self):
        case = make_case(make_record(), expected_visits=[{"day": 1, "place_id": "TEST"}])
        saved = copy.deepcopy(case)
        validated = evaluation.validate_turn_case(case)
        self.assertEqual(evaluation.validate_turn_case(validated), validated)
        self.assertEqual(case, saved)
        without_visits = evaluation.validate_turn_case(make_case(make_record()))
        self.assertEqual(evaluation.validate_turn_case(without_visits), without_visits)

    def test_invalid_turn_fields_and_expectations_are_rejected(self):
        for extra in (
            {"unknown": 1}, {"options_patch": []}, {"options_patch": {"unknown": 1}},
            {"preserve_visits": 1}, {"expected_visits": None}, {"expected_visits": {}},
            {"expected_visits": [{"day": True, "place_id": "TEST"}]},
            {"expected_visits": [{"day": 1, "place_id": ""}]},
            {"expected_visits": [{"day": 1, "place_id": "TEST", "duration_minutes": True}]},
            {"expected_visits": [{"day": 1, "place_id": "TEST", "start_time": "24:00"}]},
            {"expected_visits": [{"day": 1, "place_id": "TEST", "unknown": "value"}]},
            {"expected_visits": [{"day": 1, "place_id": "TEST"}] * 2},
            {"options_patch": {"departure_latitude": float("nan")}},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                evaluation.validate_turn_case(make_case(make_record(), **extra))

    def test_first_turn_passes_using_real_api_scoring(self):
        record = make_record(90)
        score = evaluation.evaluate_chat_turn(make_case(
            record, expected_visits=[{"day": 1, "place_id": "TEST", "duration_minutes": 90}],
        ), record)
        self.assertTrue(score["automatic_passed"], score["checks"])
        self.assertIsNone(score["checks"]["turn_link"]["passed"])

    def test_wrong_visit_duration_fails_even_when_time_table_is_valid(self):
        record = make_record()
        score = evaluation.evaluate_chat_turn(make_case(
            record, expected_visits=[{"day": 1, "place_id": "TEST", "duration_minutes": 90}],
        ), record)
        self.assertTrue(score["checks"]["H3"]["passed"])
        self.assertFalse(score["checks"]["turn_visits"]["passed"])
        self.assertFalse(score["automatic_passed"])

    def test_expected_fixed_visit_time_is_checked(self):
        record = make_record()
        score = evaluation.evaluate_chat_turn(make_case(
            record, expected_visits=[{"day": 1, "place_id": "TEST", "start_time": "09:15"}],
        ), record)
        self.assertFalse(score["checks"]["turn_visits"]["passed"])

    def test_extra_missing_duplicate_and_wrong_day_visits_fail(self):
        for change in (
            lambda days: days[0]["items"].append({**days[0]["items"][1], "place_id": "OTHER"}),
            lambda days: days[0].update(items=[]),
            lambda days: days[0]["items"].append(copy.deepcopy(days[0]["items"][1])),
            lambda days: days[0].update(day=2),
        ):
            record = make_record()
            change(record["response"]["plan"]["days"])
            score = evaluation.evaluate_chat_turn(make_case(
                record, expected_visits=[{"day": 1, "place_id": "TEST"}],
            ), record)
            self.assertFalse(score["checks"]["turn_visits"]["passed"])

    def test_followup_preserves_visits_and_known_options(self):
        previous = make_record(route_id="old")
        record = make_followup(previous)
        score = evaluation.evaluate_chat_turn(make_case(
            record, preserve_visits=True,
            expected_visits=[{"day": 1, "place_id": "TEST", "duration_minutes": 90}],
        ), record, previous_record=previous)
        self.assertTrue(score["automatic_passed"], score["checks"])
        self.assertTrue(score["checks"]["turn_link"]["passed"])
        self.assertIn("requirements", score["checked_option_fields"])
        self.assertIn("lodging_stays", score["checked_option_fields"])

    def test_tampered_history_and_current_plan_fail_independent_link_check(self):
        previous = make_record(route_id="old")
        for field, value in (("history", []), ("current_plan", None)):
            record = make_followup(previous)
            record["request"][field] = value
            score = evaluation.evaluate_chat_turn(make_case(record), record, previous_record=previous)
            self.assertTrue(score["checks"]["capture"]["passed"])
            self.assertFalse(score["checks"]["turn_link"]["passed"])
            self.assertFalse(score["automatic_passed"])

    def test_lost_or_changed_followup_conditions_fail(self):
        previous = make_record(route_id="old")
        for field, value in (
            ("requirements", []), ("mobility_aid", "none"),
            ("start_date", "2026-11-21"), ("travel_styles", []),
        ):
            record = make_followup(previous)
            record["response"]["options"][field] = value
            score = evaluation.evaluate_chat_turn(make_case(record), record, previous_record=previous)
            self.assertFalse(score["checks"]["H8"]["passed"], field)
            self.assertFalse(score["automatic_passed"])

    def test_explicit_patch_can_clear_a_selected_requirement(self):
        previous = make_record(route_id="old")
        patch_options = {"requirements": [], "return_to_origin": False}
        record = make_followup(previous, options_patch=patch_options)
        score = evaluation.evaluate_chat_turn(
            make_case(record, options_patch=patch_options), record, previous_record=previous,
        )
        self.assertTrue(score["automatic_passed"], score["checks"])

    def test_expected_options_can_allow_an_explicit_natural_language_change(self):
        previous = make_record(route_id="old")
        record = make_followup(previous)
        record["request"]["message"] = "체류시간은 90분으로 하고 여행 종류를 자연으로 바꿔 주세요."
        record["response"]["options"]["travel_styles"] = ["자연"]
        score = evaluation.evaluate_chat_turn(make_case(
            record, expected_options={"travel_styles": ["자연"]},
        ), record, previous_record=previous)
        self.assertTrue(score["automatic_passed"], score["checks"])

    def test_preserved_visit_ids_cannot_be_replaced(self):
        previous = make_record(route_id="old")
        record = make_followup(previous)
        visit = record["response"]["plan"]["days"][0]["items"][1]
        visit.update(place_id="OTHER", title="다른 관광지")
        score = evaluation.evaluate_chat_turn(
            make_case(record, preserve_visits=True), record, previous_record=previous,
        )
        self.assertFalse(score["checks"]["turn_visits"]["passed"])

    def test_preserve_visits_needs_a_previous_plan_with_known_ids(self):
        for plan in (None, {"assumptions": [], "days": [{"day": 1, "items": [{
            **make_record()["response"]["plan"]["days"][0]["items"][1], "place_id": None, "verified": False,
        }]}]}):
            record = make_record()
            record["request"]["current_plan"] = plan
            score = evaluation.evaluate_chat_turn(make_case(record, preserve_visits=True), record)
            self.assertFalse(score["checks"]["turn_visits"]["passed"])

    def test_explicit_empty_visits_accepts_empty_plan_and_keeps_review_pending(self):
        record = make_record()
        record["response"]["plan"]["days"][0]["items"] = []
        case = make_case(record, expected_visits=[])
        case["expectation"] = {"outcome": "empty_plan", "empty_days": [1]}
        score = evaluation.evaluate_chat_turn(case, record)
        self.assertTrue(score["automatic_passed"], score["checks"])
        self.assertTrue(score["requires_manual_review"])

    def test_question_does_not_claim_a_new_visit_plan(self):
        record = make_record()
        record["response"]["plan"] = None
        case = make_case(record)
        case["expectation"] = {"outcome": "message"}
        score = evaluation.evaluate_chat_turn(case, record)
        self.assertTrue(score["automatic_passed"], score["checks"])
        self.assertIsNone(score["checks"]["turn_visits"]["passed"])
        case["expected_visits"] = []
        self.assertFalse(evaluation.evaluate_chat_turn(case, record)["automatic_passed"])

    def test_selected_lodging_requires_arrival_and_next_day_departure(self):
        record = make_record()
        add_lodging_days(record, with_moves=False)
        score = evaluation.evaluate_chat_turn(make_case(record), record)
        self.assertTrue(score["checks"]["endpoints"]["passed"])
        self.assertFalse(score["checks"]["turn_lodging"]["passed"])
        self.assertFalse(score["automatic_passed"])

    def test_selected_lodging_with_current_routes_passes(self):
        record = make_record()
        add_lodging_days(record, with_moves=True)
        score = evaluation.evaluate_chat_turn(make_case(record), record)
        self.assertTrue(score["automatic_passed"], score["checks"])

    def test_existing_endpoint_and_route_failures_are_never_overridden(self):
        record = make_record()
        add_lodging_days(record, with_moves=True)
        record["attempts"][0]["route_calls"][2]["origin"] = dict(ORIGIN)
        score = evaluation.evaluate_chat_turn(make_case(record), record)
        self.assertTrue(score["checks"]["turn_lodging"]["passed"])
        self.assertFalse(score["checks"]["endpoints"]["passed"])
        self.assertFalse(score["automatic_passed"])
        record = make_record()
        record["response"]["plan"]["days"][0]["items"][0]["route_id"] = "stale-route"
        score = evaluation.evaluate_chat_turn(make_case(record), record)
        self.assertFalse(score["checks"]["H6"]["passed"])
        self.assertFalse(score["automatic_passed"])

    def test_invalid_previous_record_is_a_failed_link(self):
        record = make_record()
        score = evaluation.evaluate_chat_turn(make_case(record), record, previous_record={})
        self.assertFalse(score["checks"]["turn_link"]["passed"])
        self.assertFalse(score["automatic_passed"])

    def test_first_turn_patch_is_rejected_before_api_scoring(self):
        record = make_record()
        with patch.object(evaluation, "evaluate_chat_record") as score:
            with self.assertRaises(ValueError):
                evaluation.evaluate_chat_turn(
                    make_case(record, options_patch={"requirements": []}), record,
                )
        score.assert_not_called()

    def test_inputs_are_preserved(self):
        previous = make_record(route_id="old")
        record = make_followup(previous)
        case = make_case(record, preserve_visits=True)
        saved = copy.deepcopy((previous, record, case))
        evaluation.evaluate_chat_turn(case, record, previous_record=previous)
        self.assertEqual((previous, record, case), saved)


if __name__ == "__main__":
    unittest.main()
