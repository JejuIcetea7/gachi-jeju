import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langchain_core.tools import StructuredTool

from backend.app.agent import chat_planner
from backend.app.routers import chat
from backend.app.routers import plan as plan_router
from backend.app.services import chat_route_points as points

import test_chat_route_endpoints as fixture


def make_payload():
    return {
        "message": "숙소를 선택한 뒤 일정을 다시 계산해 주세요.",
        "reference_date": "2026-11-17", "default_departure": {"name": "센터"},
        "options": dict(fixture.OPTIONS),
    }


def detail_messages(place_id="TEST_PLACE"):
    return [
        SimpleNamespace(type="ai", tool_calls=[{
            "id": "detail-now", "name": "get_place_details",
            "args": {"place_id": place_id, "visit_dates": ["2026-11-20"]},
        }]),
        SimpleNamespace(type="tool", tool_call_id="detail-now",
                        content=json.dumps({"status": "ok", "place_id": place_id})),
    ]


class CurrentPlaceDetailTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock(return_value={"status": "ok", "place_id": "TEST_PLACE"})

        def get_place_details(*, place_id: str, visit_dates: list[str] | None = None) -> dict:
            """테스트 상세 조회입니다."""
            return self.provider(place_id=place_id, visit_dates=visit_dates)

        self.raw_details = get_place_details
        self.details, self.require = points.bind_current_place_details(get_place_details)
        self.rows = self.enterContext(patch.object(points, "read_rows", return_value=[{
            "place_id": "TEST_PLACE", "name": "테스트 관광지", **fixture.DESTINATION,
        }]))
        self.driving = Mock(return_value={"status": "ok", "route_id": "current"})
        self.order = Mock(return_value={"status": "ok"})
        self.location, self.suggest, self.routes = points.bind_chat_route_tools(
            make_payload(), location_tool=lambda **kwargs: {"status": "not_found"},
            order_tool=self.order, driving_tool=self.driving,
            bus_tool=self.driving, walking_tool=self.driving, require_details=self.require,
        )

    def test_missing_details_block_route_before_database_and_transport(self):
        result = self.routes[0](origin_ref="departure", destination_ref="place:TEST_PLACE")
        self.assertEqual(result["status"], "details_required")
        self.assertEqual(result["place_ids"], ["TEST_PLACE"])
        self.assertIn("get_place_details", result["message"])
        self.rows.assert_not_called()
        self.driving.assert_not_called()

    def test_missing_details_block_order_even_without_boundary_references(self):
        result = self.suggest(day=1, place_ids=["TEST_PLACE"])
        self.assertEqual(result["status"], "details_required")
        self.order.assert_not_called()
        self.rows.assert_not_called()

    def test_matching_current_details_unlock_route_and_order(self):
        dates = ["2026-11-20"]
        result = self.details(place_id="TEST_PLACE", visit_dates=dates)
        self.assertEqual(result, self.provider.return_value)
        self.provider.assert_called_once_with(place_id="TEST_PLACE", visit_dates=dates)
        self.assertIsNone(self.require(["TEST_PLACE"]))
        self.assertEqual(self.routes[0](
            origin_ref="departure", destination_ref="place:TEST_PLACE",
        )["status"], "ok")
        self.assertEqual(self.suggest(day=1, place_ids=["TEST_PLACE"])["status"], "ok")

    def test_failed_mismatched_or_invalid_detail_output_never_unlocks(self):
        for output in ({"status": "not_found"},
                       {"status": "db_error"},
                       {"status": "ok", "place_id": "OTHER"}, None):
            with self.subTest(output=output):
                self.provider.return_value = output
                self.details(place_id="TEST_PLACE")
                self.assertEqual(self.require(["TEST_PLACE"])["status"], "details_required")
        self.driving.assert_not_called()

    def test_success_for_one_place_does_not_confirm_another(self):
        self.details(place_id="TEST_PLACE")
        self.assertEqual(self.require(["TEST_PLACE", "OTHER", "OTHER"])["place_ids"], ["OTHER"])

    def test_next_attempt_does_not_reuse_previous_confirmation(self):
        self.details(place_id="TEST_PLACE")
        _, require = points.bind_current_place_details(self.raw_details)
        self.assertEqual(require(["TEST_PLACE"])["status"], "details_required")

    def test_wrapped_details_preserve_schema_and_original_output(self):
        original = copy.deepcopy(self.provider.return_value)
        self.details(place_id="TEST_PLACE")
        self.assertEqual(self.provider.return_value, original)
        tool = StructuredTool.from_function(self.details)
        self.assertEqual(tool.name, "get_place_details")
        schema = tool.args_schema.model_json_schema()
        self.assertIn("place_id", schema["required"])
        self.assertIn("visit_dates", schema["properties"])

    def test_final_plan_needs_successful_matching_detail_records(self):
        plan = fixture.make_plan().model_dump()
        valid = [{"tool": "get_place_details", "input": {"place_id": "TEST_PLACE"},
                  "output": {"status": "ok", "place_id": "TEST_PLACE"}}]
        self.assertEqual(points.validate_plan_place_details(plan, valid), [])
        for trace in ([], [{"tool": "search_places_by_name", "input": {},
                            "output": {"status": "ok", "places": [{"place_id": "TEST_PLACE"}]}}],
                      [{"tool": "get_driving_time", "input": {},
                        "output": {"status": "ok", "destination": {"point_ref": "place:TEST_PLACE"}}}],
                      [{**valid[0], "input": {"place_id": "OTHER"}}],
                      [{**valid[0], "output": {"status": "not_found"}}], [None]):
            with self.subTest(trace=trace):
                errors = points.validate_plan_place_details(plan, trace)
                self.assertEqual(len(errors), 1)
                self.assertIn("상세정보", errors[0])

    def test_question_empty_days_and_moves_need_no_place_details(self):
        self.assertEqual(points.validate_plan_place_details(None, []), [])
        self.assertEqual(points.validate_plan_place_details(
            {"days": [{"day": 1, "items": []},
                      {"day": 2, "items": [{"item_type": "move", "title": "이동"}]}]}, [],
        ), [])


class PlaceDetailProducerTests(unittest.TestCase):
    def run_agent(self, *, old_details=False, current_details=False, has_plan=True):
        messages = [{"role": "user", "content": "일정을 수정해 주세요."}]
        if old_details:
            messages = detail_messages() + messages
        reply = chat.ChatAgentReply(
            reply="일정을 확인했습니다.", options=fixture.OPTIONS,
            plan=fixture.make_plan() if has_plan else None,
        )
        new_messages = detail_messages() if current_details else []
        agent = Mock()
        agent.invoke.return_value = {
            "messages": messages + new_messages, "structured_response": reply,
        }
        with patch.object(chat_planner, "build_chat_agent", return_value=agent):
            return chat_planner.run_chat_agent(messages, chat.ChatAgentReply)

    def test_run_reports_missing_current_details_even_with_previous_messages(self):
        result = self.run_agent(old_details=True)
        self.assertEqual(len(result["place_detail_errors"]), 1)

    def test_run_accepts_current_details_and_does_not_require_them_for_question(self):
        self.assertEqual(self.run_agent(current_details=True)["place_detail_errors"], [])
        self.assertEqual(self.run_agent(has_plan=False)["place_detail_errors"], [])

    def test_real_factory_requires_details_before_reference_routes(self):
        provider = Mock(return_value={"status": "ok", "place_id": "TEST_PLACE"})
        driving = Mock(return_value={"status": "ok", "route_id": "current"})
        with (
            patch.object(chat_planner, "get_openai_api_key", return_value="test-only"),
            patch.object(chat_planner, "ChatOpenAI"),
            patch.object(chat_planner, "create_agent") as factory,
            patch.object(chat_planner, "get_place_details", provider),
            patch.object(chat_planner, "create_driving_map_tool", return_value=driving),
            patch.object(points, "read_rows", return_value=[{
                "place_id": "TEST_PLACE", "name": "테스트 관광지", **fixture.DESTINATION,
            }]),
        ):
            chat_planner.build_chat_agent(None, request_payload=make_payload())
            tools = {function.__name__: function for function in factory.call_args.kwargs["tools"]}
            result = tools["get_driving_time"](
                origin_ref="departure", destination_ref="place:TEST_PLACE",
            )
            self.assertEqual(result["status"], "details_required")
            driving.assert_not_called()
            tools["get_place_details"](place_id="TEST_PLACE", visit_dates=["2026-11-20"])
            self.assertEqual(tools["get_driving_time"](
                origin_ref="departure", destination_ref="place:TEST_PLACE",
            )["status"], "ok")


class PlaceDetailServerTests(unittest.TestCase):
    def run_responses(self, error_lists):
        def result(errors):
            return {
                "chat_reply": chat.ChatAgentReply(
                    reply="일정을 확인했습니다.", options=fixture.OPTIONS, plan=fixture.make_plan(),
                ),
                "messages": [], "route_calls": fixture.make_records(),
                "model_call_count": 1, "tool_call_counts": {},
                "place_detail_errors": errors,
            }
        with (
            patch.object(chat, "run_chat_agent",
                         side_effect=[result(errors) for errors in error_lists]) as agent,
            patch.object(plan_router, "find_place_in_database", return_value={
                "place_id": "TEST_PLACE", "name": "DB 관광지", "category": "자연",
                **fixture.DESTINATION,
            }) as lookup,
            patch.object(chat, "read_closed_days_by_place_id", return_value={}),
            patch.object(chat, "read_opening_hours_by_place_id", return_value={}),
        ):
            response = chat.create_chat(chat.ChatRequest(
                message="일정을 만들어 주세요.", options=fixture.OPTIONS,
            ))
            if isinstance(response, dict):
                return 200, response, agent.call_count, lookup.call_count
            return response.status_code, json.loads(response.body), agent.call_count, lookup.call_count

    def test_missing_details_retry_then_accepts_current_evidence(self):
        status, body, attempts, lookups = self.run_responses([
            ["1일차 관광지: get_place_details로 다시 조회해 주세요."], [],
        ])
        self.assertEqual((status, attempts, lookups), (200, 2, 1))
        self.assertIsNotNone(body["plan"])

    def test_missing_details_after_two_attempts_never_return_http_200(self):
        errors = ["1일차 관광지: get_place_details로 다시 조회해 주세요."]
        status, body, attempts, lookups = self.run_responses([errors, errors])
        self.assertEqual((status, attempts, lookups), (502, 2, 0))
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertNotIn("plan", body)


if __name__ == "__main__":
    unittest.main()