import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.agent import chat_planner
from backend.app.routers import chat
from backend.app.routers import plan as plan_router
from backend.app.services.chat_route_points import validate_plan_visit_order

import test_chat_route_endpoints as fixture


ORIGIN = {"latitude": 33.45, "longitude": 126.57}
OLD_STAY = {"latitude": 33.5438, "longitude": 126.6747}
NEW_STAY = {"latitude": 33.2474, "longitude": 126.4080}
OPTIONS = {
    "departure": "센터", "departure_latitude": ORIGIN["latitude"],
    "departure_longitude": ORIGIN["longitude"], "start_date": "2026-11-20",
    "trip_days": 2, "transport": "driving", "return_to_origin": True,
    "lodging_status": "provided",
    "lodging_stays": [{"night": 1, "name": "새 숙소", **NEW_STAY}],
}


def make_plan():
    return {"days": [
        {"day": day, "items": [
            {"item_type": "visit", "place_id": place_id, "title": place_id,
             "start_time": f"{9 + index:02}:00", "end_time": f"{10 + index:02}:00",
             "duration_minutes": 60}
            for index, place_id in enumerate(ids)
        ]}
        for day, ids in ((1, ["A", "B"]), (2, ["C", "D"]))
    ]}


def order(day, origin, destination, ids=None):
    return {
        "tool": "suggest_visit_order",
        "input": {"day": day, "place_ids": ids or (["A", "B"] if day == 1 else ["C", "D"])},
        "output": {"status": "ok", "day": day,
                   "origin": copy.deepcopy(origin), "destination": copy.deepcopy(destination)},
    }


def current_orders():
    return [order(1, ORIGIN, NEW_STAY), order(2, NEW_STAY, ORIGIN)]


def messages_for(trace):
    messages = []
    for number, record in enumerate(trace):
        call_id = f"order-{number}"
        messages.extend([
            SimpleNamespace(type="ai", tool_calls=[{
                "id": call_id, "name": record["tool"], "args": record["input"],
            }]),
            SimpleNamespace(type="tool", tool_call_id=call_id,
                            content=json.dumps(record["output"])),
        ])
    return messages


class VisitOrderEvidenceTests(unittest.TestCase):
    def check(self, trace, *, plan=None, options=None):
        return validate_plan_visit_order(
            make_plan() if plan is None else plan,
            OPTIONS if options is None else options, trace,
        )

    def test_old_hotel_cannot_supply_new_hotel_order_boundaries(self):
        errors = self.check([order(1, ORIGIN, OLD_STAY), order(2, OLD_STAY, ORIGIN)])
        self.assertEqual(len(errors), 2)
        self.assertIn("도착", errors[0])
        self.assertIn("출발", errors[1])

    def test_new_hotel_boundaries_are_accepted_regardless_of_reference_label(self):
        trace = current_orders()
        trace[0]["output"]["destination"]["point_ref"] = "found:current"
        trace[1]["output"]["origin"]["point_ref"] = "found:current"
        self.assertEqual(self.check(trace), [])

    def test_exploring_old_and_new_candidates_is_allowed(self):
        for trace in (
            [order(1, ORIGIN, OLD_STAY), *current_orders()],
            [*current_orders(), order(1, ORIGIN, OLD_STAY)],
        ):
            self.assertEqual(self.check(trace), [])

    def test_unknown_lodging_boundaries_must_stay_unknown(self):
        options = {**OPTIONS, "lodging_status": "undecided", "lodging_stays": []}
        self.assertEqual(self.check(
            [order(1, ORIGIN, None), order(2, None, ORIGIN)], options=options,
        ), [])
        errors = self.check([order(1, ORIGIN, ORIGIN)], options=options)
        self.assertEqual(len(errors), 1)

    def test_return_false_does_not_invent_a_return_boundary(self):
        options = {**OPTIONS, "return_to_origin": False}
        self.assertEqual(self.check([order(2, NEW_STAY, None)], options=options), [])
        self.assertEqual(len(self.check([order(2, NEW_STAY, ORIGIN)], options=options)), 1)

    def test_not_needed_lodging_allows_daily_departure_and_return(self):
        options = {**OPTIONS, "lodging_status": "not_needed", "lodging_stays": []}
        self.assertEqual(self.check(
            [order(1, ORIGIN, ORIGIN), order(2, ORIGIN, ORIGIN)], options=options,
        ), [])

    def test_partial_day_comparisons_and_legacy_responses_are_not_reinterpreted(self):
        trace = [order(1, OLD_STAY, OLD_STAY, ["A"])]
        self.assertEqual(self.check(trace), [])
        self.assertEqual(self.check([]), [])
        self.assertEqual(validate_plan_visit_order(None, OPTIONS, []), [])

    def test_final_order_can_differ_from_the_straight_line_suggestion(self):
        trace = current_orders()
        trace[0]["output"]["suggested_place_ids"] = ["B", "A"]
        self.assertEqual(self.check(trace), [])

    def test_failed_malformed_and_other_day_records_are_not_used(self):
        failed = order(1, ORIGIN, OLD_STAY)
        failed["output"]["status"] = "details_required"
        bad_day = order(1, ORIGIN, OLD_STAY)
        bad_day["output"]["day"] = 2
        malformed = order(1, ORIGIN, OLD_STAY)
        malformed["input"]["place_ids"] = [{"place_id": "A"}, "B"]
        self.assertEqual(self.check([None, failed, bad_day, malformed, {"tool": "other"}]), [])

    def test_inputs_are_not_changed(self):
        value = (make_plan(), copy.deepcopy(OPTIONS), current_orders())
        before = copy.deepcopy(value)
        self.assertEqual(validate_plan_visit_order(*value), [])
        self.assertEqual(value, before)


class VisitOrderProducerTests(unittest.TestCase):
    def run_agent(self, trace):
        reply = chat.ChatAgentReply(reply="숙소를 반영했습니다.", options=OPTIONS, plan=make_plan())
        messages = [{"role": "user", "content": "숙소를 변경해 주세요."}]
        agent = Mock()
        agent.invoke.return_value = {
            "messages": messages + messages_for(trace), "structured_response": reply,
        }
        with patch.object(chat_planner, "build_chat_agent", return_value=agent):
            return chat_planner.run_chat_agent(messages, chat.ChatAgentReply)

    def test_runner_reports_old_lodging_boundaries(self):
        result = self.run_agent([order(1, ORIGIN, OLD_STAY), order(2, OLD_STAY, ORIGIN)])
        self.assertEqual(len(result["visit_order_errors"]), 2)

    def test_runner_accepts_new_lodging_boundaries(self):
        self.assertEqual(self.run_agent(current_orders())["visit_order_errors"], [])


class VisitOrderServerTests(unittest.TestCase):
    def run_responses(self, error_lists):
        results = [{
            "chat_reply": chat.ChatAgentReply(
                reply="일정을 확인했습니다.", options=fixture.OPTIONS, plan=fixture.make_plan(),
            ),
            "messages": [], "route_calls": fixture.make_records(),
            "model_call_count": 1, "tool_call_counts": {},
            "visit_order_errors": errors,
        } for errors in error_lists]
        with (
            patch.object(chat, "run_chat_agent", side_effect=results) as agent,
            patch.object(plan_router, "find_place_in_database", return_value={
                "place_id": "TEST_PLACE", "name": "DB 관광지", "category": "자연",
                **fixture.DESTINATION,
            }),
            patch.object(chat, "read_closed_days_by_place_id", return_value={}),
            patch.object(chat, "read_opening_hours_by_place_id", return_value={}),
        ):
            response = chat.create_chat(chat.ChatRequest(
                message="일정을 만들어 주세요.", options=fixture.OPTIONS,
            ))
            if isinstance(response, dict):
                return 200, response, agent.call_count
            return response.status_code, json.loads(response.body), agent.call_count

    def test_order_error_retries_and_then_accepts_corrected_evidence(self):
        status, body, attempts = self.run_responses([["새 숙소 기준으로 다시 비교해 주세요."], []])
        self.assertEqual((status, attempts), (200, 2))
        self.assertIsNotNone(body["plan"])

    def test_second_order_failure_stops_at_the_existing_retry_limit(self):
        errors = ["새 숙소 기준으로 다시 비교해 주세요."]
        status, body, attempts = self.run_responses([errors, errors])
        self.assertEqual((status, attempts), (502, 2))
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertNotIn("plan", body)


if __name__ == "__main__":
    unittest.main()