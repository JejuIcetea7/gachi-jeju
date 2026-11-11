import json
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.app.services.plan_evaluation import (
    calculate_process_metrics,
    check_conditions_kept,
    check_day_count,
    check_move_durations,
    check_places_from_search,
    check_plan_expected,
    check_transport_tools,
    evaluate_run,
    extract_tool_trace,
)

# 실제 DB 장소: P044 한라수목원(화장실 unknown), P052 낙천의자공원(장애인 화장실 Y)

def build_case(**overrides) -> dict:
    case = {
        "case_id": "T01",
        "expect_plan": True,
        "time_window": None,
        "request": {
            "message": "일정을 짜 주세요.",
            "options": {
                "transport": "driving",
                "mobility_aid": "wheelchair",
                "start_date": "2026-11-20",
                "trip_days": 1,
                "requirements": ["accessible_toilet"],
            },
        },
    }
    case.update(overrides)
    return case

def build_plan() -> dict:
    return {
        "assumptions": [],
        "days": [{
            "day": 1,
            "items": [
                {
                    "start_time": "10:00", "end_time": "10:20", "item_type": "move",
                    "title": "출발지 → 한라수목원", "place_id": None, "transport": "자동차",
                    "duration_minutes": 20, "distance_km": 6.2, "accessibility_notes": [],
                    "route_id": "route-1",
                },
                {
                    "start_time": "10:20", "end_time": "11:20", "item_type": "visit",
                    "title": "한라수목원", "place_id": "P044", "transport": None,
                    "duration_minutes": 60, "distance_km": None, "accessibility_notes": [],
                    "route_id": None,
                },
                {
                    "start_time": "11:20", "end_time": "11:35", "item_type": "move",
                    "title": "한라수목원 → 낙천의자공원", "place_id": None, "transport": "자동차",
                    "duration_minutes": 15, "distance_km": 5.0, "accessibility_notes": [],
                    "route_id": None,
                },
                {
                    "start_time": "11:35", "end_time": "12:35", "item_type": "visit",
                    "title": "낙천의자공원", "place_id": "P052", "transport": None,
                    "duration_minutes": 60, "distance_km": None, "accessibility_notes": [],
                    "route_id": None,
                },
            ],
        }],
    }

def build_chat_reply(plan: dict | None) -> dict:
    return {
        "reply": "일정을 만들었습니다.",
        "options": {
            "transport": "driving",
            "mobility_aid": "wheelchair",
            "start_date": "2026-11-20",
            "trip_days": 1,
            "requirements": ["accessible_toilet"],
        },
        "plan": plan,
    }

def build_tool_trace() -> list[dict]:
    return [
        {
            "call_id": "c1", "tool": "search_places", "input": {"category": "자연"},
            "output": {"status": "ok", "places": [{"place_id": "P044"}, {"place_id": "P052"}]},
        },
        {
            "call_id": "c2", "tool": "get_driving_time", "input": {"origin_latitude": 33.5},
            "output": {"status": "ok", "duration_seconds": 1190, "route_id": "route-1"},
        },
        {
            "call_id": "c3", "tool": "get_driving_time", "input": {"origin_latitude": 33.4},
            "output": {"status": "ok", "duration_seconds": 870, "route_id": "route-2"},
        },
    ]

def build_route_calls() -> list[dict]:
    return [
        {"route_id": "route-1", "transport": "driving", "distance_m": 6200, "duration_seconds": 1190},
        {"route_id": "route-2", "transport": "driving", "distance_m": 5000, "duration_seconds": 870},
    ]

class ToolTraceTests(unittest.TestCase):
    def test_pairs_tool_calls_with_outputs(self):
        messages = [
            HumanMessage(content="요청"),
            AIMessage(content="", tool_calls=[{"name": "search_places", "args": {"category": "자연"}, "id": "c1"}]),
            ToolMessage(content=json.dumps({"status": "ok", "places": []}), tool_call_id="c1"),
            AIMessage(content="끝"),
        ]

        tool_trace = extract_tool_trace(messages)

        self.assertEqual(tool_trace, [{
            "call_id": "c1",
            "tool": "search_places",
            "input": {"category": "자연"},
            "output": {"status": "ok", "places": []},
        }])

    def test_keeps_text_output_when_not_json(self):
        messages = [
            AIMessage(content="", tool_calls=[{"name": "find_location", "args": {}, "id": "c1"}]),
            ToolMessage(content="Error: timeout", tool_call_id="c1"),
        ]

        tool_trace = extract_tool_trace(messages)

        self.assertEqual(tool_trace[0]["output"], "Error: timeout")

class RequiredCheckTests(unittest.TestCase):
    def test_good_run_passes_every_check(self):
        evaluation = evaluate_run(build_case(), build_chat_reply(build_plan()), build_tool_trace(), build_route_calls())

        self.assertTrue(evaluation["plan_delivered"])
        self.assertTrue(evaluation["passed"], evaluation["checks"])

    def test_plan_without_required_options_is_not_delivered(self):
        chat_reply = build_chat_reply(build_plan())
        chat_reply["options"]["start_date"] = None

        evaluation = evaluate_run(build_case(), chat_reply, build_tool_trace(), build_route_calls())

        self.assertFalse(evaluation["plan_delivered"])
        self.assertFalse(evaluation["checks"]["H1"]["passed"])
        self.assertIsNone(evaluation["checks"]["H2"]["passed"])

    def test_question_case_passes_when_no_plan(self):
        case = build_case(expect_plan=False)

        result = check_plan_expected(case, build_chat_reply(None), None)

        self.assertTrue(result["passed"])

    def test_missing_day_fails(self):
        case = build_case()
        case["request"]["options"]["trip_days"] = 2

        result = check_day_count(case, build_chat_reply(build_plan()), build_plan())

        self.assertFalse(result["passed"])

    def test_time_window_is_checked(self):
        case = build_case(time_window={"start": "10:00", "end": "12:00"})

        evaluation = evaluate_run(case, build_chat_reply(build_plan()), build_tool_trace(), build_route_calls())

        self.assertFalse(evaluation["checks"]["H3"]["passed"])

    def test_unknown_place_fails_database_check(self):
        plan = build_plan()
        plan["days"][0]["items"][1]["place_id"] = "P999"

        evaluation = evaluate_run(build_case(), build_chat_reply(plan), build_tool_trace(), build_route_calls())

        self.assertFalse(evaluation["checks"]["H4"]["passed"])
        self.assertFalse(evaluation["checks"]["H5"]["passed"])

    def test_place_not_from_search_fails(self):
        tool_trace = build_tool_trace()
        tool_trace[0]["output"]["places"] = [{"place_id": "P044"}]

        result = check_places_from_search(build_plan(), tool_trace)

        self.assertFalse(result["passed"])
        self.assertEqual(len(result["reasons"]), 1)

    def test_move_duration_uses_route_id(self):
        plan = build_plan()
        plan["days"][0]["items"][0]["duration_minutes"] = 25

        result = check_move_durations(plan, build_tool_trace(), build_route_calls())

        self.assertFalse(result["passed"])
        self.assertIn("도구 결과 20분", result["reasons"][0])

    def test_move_duration_allows_one_minute_difference(self):
        plan = build_plan()
        plan["days"][0]["items"][2]["duration_minutes"] = 16

        result = check_move_durations(plan, build_tool_trace(), build_route_calls())

        self.assertTrue(result["passed"], result["reasons"])

    def test_move_without_matching_tool_result_fails(self):
        plan = build_plan()
        plan["days"][0]["items"][2]["duration_minutes"] = 40

        result = check_move_durations(plan, build_tool_trace(), build_route_calls())

        self.assertFalse(result["passed"])

    def test_unknown_route_id_fails(self):
        plan = build_plan()
        plan["days"][0]["items"][0]["route_id"] = "made-up"

        result = check_move_durations(plan, build_tool_trace(), build_route_calls())

        self.assertFalse(result["passed"])

    def test_forbidden_transport_tool_fails(self):
        case = build_case()
        case["request"]["options"]["transport"] = "walking"

        result = check_transport_tools(case, build_tool_trace())

        self.assertFalse(result["passed"])
        self.assertEqual(len(result["reasons"]), 2)

    def test_transport_check_skipped_without_transport(self):
        case = build_case()
        case["request"]["options"] = {}

        result = check_transport_tools(case, build_tool_trace())

        self.assertIsNone(result["passed"])

    def test_dropped_requirement_fails(self):
        chat_reply = build_chat_reply(build_plan())
        chat_reply["options"]["requirements"] = []
        chat_reply["options"]["mobility_aid"] = None

        result = check_conditions_kept(build_case(), chat_reply)

        self.assertFalse(result["passed"])
        self.assertEqual(len(result["reasons"]), 2)

class EmptyDayTests(unittest.TestCase):
    def test_day_without_visit_fails(self):
        plan = build_plan()
        plan["days"][0]["items"] = []

        evaluation = evaluate_run(build_case(), build_chat_reply(plan), build_tool_trace(), build_route_calls())

        self.assertFalse(evaluation["checks"]["H9"]["passed"])
        self.assertFalse(evaluation["passed"])

    def test_empty_day_check_skipped_without_plan(self):
        case = build_case(expect_plan=False)

        evaluation = evaluate_run(case, build_chat_reply(None), [], [])

        self.assertIsNone(evaluation["checks"]["H9"]["passed"])

class MetricTests(unittest.TestCase):
    def test_visit_metrics_count_thin_days(self):
        plan = build_plan()
        second_day = {"day": 2, "items": [plan["days"][0]["items"][1]]}
        plan["days"].append(second_day)

        evaluation = evaluate_run(build_case(), build_chat_reply(plan), build_tool_trace(), build_route_calls())

        self.assertEqual(
            evaluation["visit_metrics"],
            {"day_count": 2, "thin_day_count": 1, "visit_counts_by_day": [2, 1]},
        )

    def test_requirement_coverage_uses_database_columns(self):
        evaluation = evaluate_run(build_case(), build_chat_reply(build_plan()), build_tool_trace(), build_route_calls())

        self.assertEqual(
            evaluation["requirement_coverage"],
            {"accessible_toilet": {"visit_count": 2, "evidence_count": 1}},
        )

    def test_process_metrics_count_repeated_calls_and_tokens(self):
        messages = [
            AIMessage(
                content="",
                tool_calls=[{"name": "search_places", "args": {"category": "자연"}, "id": "c1"}],
                usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "search_places", "args": {"category": "자연"}, "id": "c2"}],
                usage_metadata={"input_tokens": 200, "output_tokens": 20, "total_tokens": 220},
            ),
        ]
        tool_trace = extract_tool_trace(messages)

        metrics = calculate_process_metrics(messages, tool_trace)

        self.assertEqual(metrics["model_call_count"], 2)
        self.assertEqual(metrics["tool_call_counts"], {"search_places": 2})
        self.assertEqual(metrics["repeated_tool_call_count"], 1)
        self.assertEqual(metrics["input_tokens"], 300)
        self.assertEqual(metrics["output_tokens"], 30)

if __name__ == "__main__":
    unittest.main()
