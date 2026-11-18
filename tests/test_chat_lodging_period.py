import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_chat_lodging_planning as fixture
from backend.app.agent import chat_planner
from backend.app.routers import chat, plan
from backend.app.services.chat_request_control import (
    ChatRequestControl, ChatRequestStopped, use_chat_request_control,
)
from backend.app.services.lodging_period import (
    KEEP_DAY_TRIP_REPLY, PERIOD_QUESTION,
)
from scripts.chat_evaluation_runner import _capture_result


REQUEST = "1일차 숙소를 제주신라호텔로 변경하고 다음 날 숙소 출발 이동도 반영해 주세요."
HOTEL = {**fixture.stay(), "name": "제주신라호텔"}
OPTIONS = fixture.options(days=1, status="not_needed")


def history():
    return [{"role": "user", "content": REQUEST},
            {"role": "assistant", "content": PERIOD_QUESTION}]


def location_messages():
    return [
        SimpleNamespace(type="ai", tool_calls=[{
            "id": "location", "name": "find_location", "args": {"keyword": HOTEL["name"]},
        }]),
        SimpleNamespace(type="tool", tool_call_id="location", content=json.dumps({
            "status": "ok", **{key: HOTEL[key] for key in (
                "name", "address", "latitude", "longitude",
            )},
        })),
    ]


class ChatLodgingPeriodTests(unittest.TestCase):
    def post(self, message=REQUEST, **kwargs):
        request = chat.ChatRequest(message=message, options=OPTIONS, **kwargs)
        response = chat.create_chat(request)
        if isinstance(response, dict):
            return 200, chat.ChatResponse.model_validate(response).model_dump(mode="json")
        return response.status_code, json.loads(response.body)

    def test_period_question_skips_model_client_and_keeps_current_plan(self):
        old_plan, _ = fixture.make_plan(days=1)
        with patch.object(plan, "find_place_in_database", return_value={
            "place_id": "TEST_PLACE", "name": "DB 관광지", "category": "공원", **fixture.SITE,
        }):
            current, _ = plan.verify_places(old_plan)
        before = copy.deepcopy(current)
        with (
            patch.object(chat_planner, "chat_model_http_client") as client,
            patch.object(chat_planner, "build_chat_agent") as factory,
            patch.object(chat_planner, "get_openai_api_key") as key,
        ):
            status, body = self.post(current_plan=current)
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], PERIOD_QUESTION)
        self.assertEqual(body["options"], chat.ChatOptions.model_validate(OPTIONS).model_dump(mode="json"))
        self.assertIsNone(body["plan"])
        self.assertEqual(body["map_routes"], [])
        self.assertEqual(current, before)
        self.assertEqual(body["meta"]["model_call_count"], 0)
        self.assertEqual(body["meta"]["tool_call_counts"], {})
        client.assert_not_called()
        factory.assert_not_called()
        key.assert_not_called()

    def test_decline_keeps_day_trip_without_model_call(self):
        with patch.object(chat_planner, "build_chat_agent") as factory:
            status, body = self.post("아니요", history=history())
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], KEEP_DAY_TRIP_REPLY)
        self.assertEqual(body["options"]["trip_days"], 1)
        self.assertIsNone(body["plan"])
        factory.assert_not_called()

    def test_approval_payload_keeps_original_request_and_other_options(self):
        request = chat.ChatRequest(message="네", options={
            **OPTIONS, "mobility_aid": "wheelchair", "requirements": ["accessible_toilet"],
        }, history=history())
        before = request.model_dump(mode="json")
        payload = json.loads(chat.build_chat_messages(request)[-1]["content"])
        self.assertEqual(payload["options"]["trip_days"], 2)
        self.assertEqual(payload["options"]["lodging_status"], "undecided")
        self.assertEqual(payload["options"]["requirements"], ["accessible_toilet"])
        self.assertEqual(payload["options"]["mobility_aid"], "wheelchair")
        self.assertEqual(payload["lodging_period_confirmation"]["original_request"], REQUEST)
        self.assertEqual(request.model_dump(mode="json"), before)

    def test_approval_uses_model_flow_and_prepared_options(self):
        request = chat.ChatRequest(message="네", options=OPTIONS, history=history())
        messages = chat.build_chat_messages(request)
        selected = json.loads(messages[-1]["content"])["options"]
        agent = Mock()
        agent.invoke.return_value = {
            "messages": messages,
            "structured_response": chat.ChatAgentReply(
                reply="새 숙소의 정확한 이름과 주소를 확인하겠습니다.", options=selected, plan=None,
            ),
        }
        with patch.object(chat_planner, "build_chat_agent", return_value=agent) as factory:
            result = chat_planner.run_chat_agent(messages, chat.ChatAgentReply)
        self.assertEqual(result["chat_reply"].options.trip_days, 2)
        self.assertEqual(factory.call_args.kwargs["request_payload"]["options"]["trip_days"], 2)
        agent.invoke.assert_called_once()

    def hotel_result(self, trace):
        selected = fixture.options(days=2, status="provided", stays=[HOTEL])
        trip, routes = fixture.make_plan(days=2, stays=[HOTEL])
        result = fixture.agent_result(trip, routes, selected)
        result["messages"] = trace
        return result

    def test_confirmed_hotel_still_requires_fresh_location_evidence(self):
        with (
            patch.object(chat, "run_chat_agent") as agent,
            patch.object(plan, "find_place_in_database", return_value={
                "place_id": "TEST_PLACE", "name": "DB 관광지", "category": "공원", **fixture.SITE,
            }),
            patch.object(chat, "read_closed_days_by_place_id", return_value={}),
            patch.object(chat, "read_opening_hours_by_place_id", return_value={}),
        ):
            agent.side_effect = lambda *args: self.hotel_result(location_messages())
            status, body = self.post("네", history=history())
            self.assertEqual(status, 200)
            self.assertEqual(body["options"]["trip_days"], 2)
            self.assertEqual(body["options"]["lodging_stays"][0]["name"], HOTEL["name"])
            self.assertEqual(len(body["map_routes"]), 4)
            self.assertEqual(agent.call_count, 1)

            agent.reset_mock()
            agent.side_effect = lambda *args: self.hotel_result([])
            status, body = self.post("네", history=history())
            self.assertEqual(status, 502)
            self.assertEqual(body["error"]["code"], "plan_validation_failed")
            self.assertEqual(agent.call_count, 2)

    def test_local_question_keeps_evaluation_capture_at_zero_calls(self):
        messages = chat.build_chat_messages(chat.ChatRequest(message=REQUEST, options=OPTIONS))
        result = chat_planner.run_chat_agent(messages, chat.ChatAgentReply)
        captured = _capture_result(result)
        self.assertEqual(captured["metrics"]["model_call_count"], 0)
        self.assertEqual(captured["metrics"]["tool_call_counts"], {})
        self.assertEqual(captured["tool_trace"], [])
        self.assertEqual(captured["route_calls"], [])
        self.assertIsNotNone(captured["process"])

    def test_cancellation_is_checked_after_local_result_is_prepared(self):
        messages = chat.build_chat_messages(chat.ChatRequest(message=REQUEST, options=OPTIONS))
        original = chat_planner.local_lodging_period_result
        control = ChatRequestControl()

        def cancel_after_preparation(*args):
            result = original(*args)
            control.cancel()
            return result

        with use_chat_request_control(control):
            with patch.object(chat_planner, "local_lodging_period_result",
                              side_effect=cancel_after_preparation):
                with self.assertRaises(ChatRequestStopped):
                    chat_planner.run_chat_agent(messages, chat.ChatAgentReply)


if __name__ == "__main__":
    unittest.main()