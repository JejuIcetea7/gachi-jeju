import copy
import json
import unittest
from unittest.mock import patch

from backend.app.agent.planner import TripPlan
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router
from backend.app.services import chat_api_evaluation as evaluation


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
SITE = {"latitude": 33.41, "longitude": 126.51}
OPTIONS = {
    "departure": "테스트 출발지", "departure_latitude": ORIGIN["latitude"],
    "departure_longitude": ORIGIN["longitude"], "start_date": "2026-11-20",
    "trip_days": 1, "transport": "driving", "mobility_aid": "none",
    "requirements": [], "return_to_origin": True,
}


def make_result(prefix="current-", reply="귀환은 10:20입니다.", notes=None, question=False):
    items = [
        {"item_type": "move", "title": "출발지 → 방문지", "transport": "자동차",
         "start_time": "09:00", "end_time": "09:10", "duration_minutes": 10,
         "route_id": prefix + "out"},
        {"item_type": "visit", "title": "DB 장소", "place_id": "TEST",
         "start_time": "09:10", "end_time": "10:10", "duration_minutes": 60},
        {"item_type": "move", "title": "방문지 → 출발지", "transport": "자동차",
         "start_time": "10:10", "end_time": "10:20", "duration_minutes": 10,
         "route_id": prefix + "back"},
    ]
    routes = [{
        "route_id": prefix + suffix, "transport": "driving",
        "origin": dict(origin), "destination": dict(destination),
        "duration_seconds": 600, "distance_m": 1000, "geometry_status": "ok",
        "paths": [[[origin["longitude"], origin["latitude"]],
                   [destination["longitude"], destination["latitude"]]]],
        "requested_at": "2026-11-16T00:00:00+00:00",
    } for suffix, origin, destination in (
        ("out", ORIGIN, SITE), ("back", SITE, ORIGIN),
    )]
    plan = None if question else TripPlan.model_validate({
        "assumptions": notes or [], "days": [{"day": 1, "items": items}],
    })
    return {
        "chat_reply": chat_router.ChatAgentReply(reply=reply, options=OPTIONS, plan=plan),
        "route_calls": [] if question else routes, "messages": [],
        "model_call_count": 1,
        "tool_call_counts": {} if question else {"get_driving_time": 2},
    }


def saved_fixture(reply="귀환은 10:20입니다.", notes=None, question=False):
    result = make_result(reply=reply, notes=notes, question=question)
    body = result["chat_reply"].model_dump(mode="json")
    if body["plan"] is not None:
        for item in body["plan"]["days"][0]["items"]:
            item.update(verified=None, category=None, latitude=None, longitude=None)
        body["plan"]["days"][0]["items"][1].update(
            verified=True, category="공원", **SITE,
        )
    metrics = {"model_call_count": 1, "tool_call_counts": result["tool_call_counts"]}
    body.update(
        warnings=[], map_routes=[], transit_routes=[],
        meta={**metrics, "elapsed_seconds": 0.1},
    )
    request = {"message": "당일 관광 일정을 만들어 주세요.", "options": copy.deepcopy(OPTIONS)}
    trace = [] if question else [{
        "call_id": "place", "tool": "get_place_details", "input": {"place_id": "TEST"},
        "output": {"status": "ok", "place_id": "TEST"},
    }]
    trace.extend({
        "call_id": route["route_id"], "tool": "get_driving_time", "input": {},
        "output": {"status": "ok", "route_id": route["route_id"], "duration_seconds": 600},
    } for route in result["route_calls"])
    case = {
        "case_id": "REPLY01", "description": "답변과 최종 일정 일치",
        "request": request, "expectation": {"outcome": "message" if question else "plan"},
    }
    record = {
        "request": copy.deepcopy(request), "http_status": 200, "response": body,
        "elapsed_seconds": 0.1, "attempts": [{
            "attempt": 1, "status": "returned", "error_type": None,
            "capture_error": None, "retry_feedback": [],
            "chat_reply": result["chat_reply"].model_dump(mode="json"),
            "tool_trace": trace, "route_calls": result["route_calls"],
            "metrics": metrics, "process": None,
        }],
    }
    return case, record


class ChatReplyConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(patch.object(chat_router, "run_chat_agent"))
        self.enterContext(patch.object(
            plan_router, "find_place_in_database",
            return_value={"place_id": "TEST", "name": "DB 장소", "category": "공원", **SITE},
        ))
        self.enterContext(patch.object(chat_router, "read_closed_days_by_place_id", return_value={}))
        self.enterContext(patch.object(chat_router, "read_opening_hours_by_place_id", return_value={}))

    def post(self, message="당일 관광 일정을 만들어 주세요."):
        result = chat_router.create_chat(chat_router.ChatRequest(message=message, options=OPTIONS))
        if isinstance(result, dict):
            return 200, chat_router.ChatResponse.model_validate(result).model_dump(mode="json")
        return result.status_code, json.loads(result.body)













    def test_model_reply_is_preserved_including_facility_and_clock_words(self):
        reply = "09:10에 방문을 시작합니다. 접근성 정보는 확인이 필요합니다."
        self.agent.return_value = make_result(reply=reply)
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], reply)
        self.assertEqual(body["plan"]["days"][0]["items"][1]["start_time"], "09:10")

    def test_valid_question_does_not_invoke_schedule_generation_again(self):
        result = make_result()
        result["chat_reply"] = result["chat_reply"].model_copy(update={"plan": None, "reply": "원하는 방문 시각을 알려주세요.", "needs_clarification": True})
        self.agent.return_value = result
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], result["chat_reply"].reply)
        self.assertIsNone(body["plan"])
        self.agent.assert_called_once()


class ReplyConsistencyEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.lookup = self.enterContext(patch.object(
            evaluation, "read_places_by_id",
            return_value={"TEST": {"place_id": "TEST", "name": "DB 장소", **SITE}},
        ))

    def test_saved_response_uses_same_check(self):
        for reply, notes, passed in (
            ("귀환은 10:20입니다.", [], True),
            ("귀환은 14:30입니다.", [], False),
            ("10:20에 출발지로 돌아옵니다.", [], True),
            ("10:10에 출발지로 돌아옵니다.", [], False),
            ("귀환은 10:20입니다.", ["숙소는 미정입니다."], False),
            ("귀환은 10:20입니다.", ["방문 시작 시각은 09:10입니다."], True),
            ("귀환은 10:20입니다.", ["방문 시작 시각은 09:00입니다."], False),
            ("귀환은 10:20입니다.", ["방문 시작 시각은 지정되지 않아 09:00 출발로 제안했습니다."], True),
        ):
            with self.subTest(reply=reply, notes=notes):
                case, record = saved_fixture(reply, notes)
                before = copy.deepcopy((case, record))
                result = evaluation.evaluate_chat_record(case, record)
                self.assertEqual(result["automatic_passed"], passed, result["checks"])
                self.assertIs(result["checks"]["reply_consistency"]["passed"], passed)
                self.assertEqual((case, record), before)

    def test_message_keeps_consistency_check_unapplied(self):
        case, record = saved_fixture(reply="귀환은 14:30으로 할까요?", question=True)
        result = evaluation.evaluate_chat_record(case, record)
        self.assertTrue(result["automatic_passed"], result["checks"])
        self.assertIsNone(result["checks"]["reply_consistency"]["passed"])
        self.lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
