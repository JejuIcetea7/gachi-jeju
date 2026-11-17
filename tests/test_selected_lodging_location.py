import copy
import json
import unicodedata
import unittest
from unittest.mock import Mock, patch

from langchain_core.tools import StructuredTool

from backend.app.agent import chat_planner
from backend.app.services.chat_request_agent import guard_chat_tool
from backend.app.services.chat_request_control import (
    ChatRequestControl, ChatRequestStopped, use_chat_request_control,
)
from backend.app.services.lodging_planning import (
    SELECTED_LODGING_SOURCE, validate_lodging_selections,
)
from backend.app.services.selected_lodging_location import bind_selected_lodging_location


STAY = {
    "night": 1, "name": "테스트 숙소 A", "address": "제주시 테스트로 1",
    "latitude": 33.47, "longitude": 126.48,
    "provider_place_id": None, "place_url": None,
}


def messages(stays=None, status="provided", days=3):
    return [{"role": "user", "content": json.dumps({
        "message": "선택한 숙소의 이동을 반영해 주세요.",
        "options": {"trip_days": days, "lodging_status": status,
                    "lodging_stays": [copy.deepcopy(STAY)] if stays is None else stays},
    }, ensure_ascii=False)}]


class SelectedLodgingLocationTests(unittest.TestCase):
    def test_selected_name_address_and_combined_queries_do_not_search(self):
        provider = Mock(side_effect=AssertionError("provider must not run"))
        selected = {**STAY, "provider_place_id": "100", "place_url": "https://place.map.kakao.com/100"}
        tool = bind_selected_lodging_location(provider, messages([selected]))
        for keyword in (selected["name"], selected["address"],
                        selected["name"] + " " + selected["address"],
                        selected["name"] + ", " + selected["address"],
                        "  테스트숙소 a  ", unicodedata.normalize("NFD", selected["name"])):
            with self.subTest(keyword=keyword):
                result = tool(keyword=keyword)
                self.assertEqual(result["source"], SELECTED_LODGING_SOURCE)
                self.assertFalse(result["lookup_performed"])
                self.assertEqual({key: result[key] for key in selected if key != "night"},
                                 {key: value for key, value in selected.items() if key != "night"})
        provider.assert_not_called()

    def test_other_names_corrected_addresses_and_partial_names_use_provider(self):
        expected = {"status": "not_found"}
        provider = Mock(return_value=expected)
        tool = bind_selected_lodging_location(provider, messages())
        for keyword in ("다른 숙소 B", STAY["name"] + " 제주시 다른로 2", "테스트 숙소"):
            with self.subTest(keyword=keyword):
                self.assertIs(tool(keyword=keyword), expected)
                provider.assert_called_with(keyword=keyword)
        self.assertEqual(provider.call_count, 3)

    def test_refresh_performs_a_fresh_search_for_the_same_name(self):
        expected = {"status": "ok", "name": STAY["name"], "address": STAY["address"],
                    "latitude": 33.49, "longitude": 126.51}
        provider = Mock(return_value=expected)
        tool = bind_selected_lodging_location(provider, messages())
        self.assertIs(tool(keyword=STAY["name"], refresh=True), expected)
        provider.assert_called_once_with(keyword=STAY["name"])

    def test_provider_error_is_preserved(self):
        provider = Mock(side_effect=RuntimeError("test-only"))
        tool = bind_selected_lodging_location(provider, messages())
        with self.assertRaisesRegex(RuntimeError, "test-only"):
            tool(keyword="새 숙소")

    def test_missing_invalid_or_historical_selections_do_not_bind(self):
        provider = Mock()
        history_only = messages() + [{"role": "user", "content": "일정을 바꿔 주세요."}]
        invalid = messages([{**STAY, "latitude": float("nan")}])
        duplicate = messages([STAY, STAY])
        for value in ([], history_only, messages([]), messages(status="not_needed"),
                      invalid, duplicate, messages(days=True),
                      [{"role": "assistant", "content": messages()[0]["content"]}],
                      [{"role": "user", "content": "[]"}]):
            with self.subTest(value=value):
                self.assertIs(bind_selected_lodging_location(provider, value), provider)
        provider.assert_not_called()

    def test_ambiguous_names_and_addresses_are_not_chosen(self):
        provider = Mock(return_value={"status": "not_found"})
        other = {**STAY, "night": 2, "latitude": 33.49}
        self.assertIs(bind_selected_lodging_location(provider, messages([STAY, other])), provider)

    def test_same_location_on_multiple_nights_is_not_ambiguous(self):
        provider = Mock()
        tool = bind_selected_lodging_location(provider, messages([STAY, {**STAY, "night": 2}]))
        self.assertEqual(tool(keyword=STAY["name"])["latitude"], STAY["latitude"])
        provider.assert_not_called()

    def test_request_snapshot_results_and_other_requests_are_independent(self):
        original = messages(status="undecided")
        before = copy.deepcopy(original)
        provider = Mock()
        first = bind_selected_lodging_location(provider, original)
        second_stay = {**STAY, "latitude": 33.49}
        second = bind_selected_lodging_location(provider, messages([second_stay]))
        self.assertEqual(original, before)
        original[0]["content"] = "{}"
        result = first(keyword=STAY["name"])
        result["latitude"] = 33.5
        self.assertEqual(first(keyword=STAY["name"])["latitude"], STAY["latitude"])
        self.assertEqual(second(keyword=STAY["name"])["latitude"], second_stay["latitude"])
        self.assertEqual(json.loads(before[0]["content"])["options"]["lodging_status"], "undecided")
        provider.assert_not_called()

    def test_reused_selection_is_not_new_night_or_changed_location_evidence(self):
        cached = bind_selected_lodging_location(Mock(), messages())(keyword=STAY["name"])
        trace = [{"tool": "find_location", "output": cached}]
        previous = {"lodging_stays": [copy.deepcopy(STAY)]}
        before = copy.deepcopy((previous, trace))
        for changed in ({**STAY, "night": 2}, {**STAY, "latitude": 33.49}):
            errors = validate_lodging_selections(
                previous, {"lodging_stays": [changed]},
                user_message="테스트 숙소 A에서 묵겠습니다.", tool_trace=trace,
            )
            self.assertEqual(len(errors), 1)
        self.assertEqual(validate_lodging_selections(
            previous, previous, user_message="그 숙소를 유지합니다.", tool_trace=trace,
        ), [])
        self.assertEqual((previous, trace), before)

    def test_fresh_lookup_can_support_an_explicit_new_night(self):
        output = {"status": "ok", "name": STAY["name"], "address": STAY["address"],
                  "latitude": STAY["latitude"], "longitude": STAY["longitude"]}
        provider = Mock(return_value=output)
        tool = bind_selected_lodging_location(provider, messages())
        fresh = tool(keyword=STAY["name"], refresh=True)
        self.assertEqual(validate_lodging_selections(
            {"lodging_stays": [STAY]}, {"lodging_stays": [{**STAY, "night": 2}]},
            user_message="2일차도 테스트 숙소 A에서 묵겠습니다.",
            tool_trace=[{"tool": "find_location", "output": fresh}],
        ), [])

    def test_structured_tool_exposes_refresh_and_reuses_coordinates(self):
        provider = Mock(return_value={"status": "not_found"})
        tool = StructuredTool.from_function(bind_selected_lodging_location(provider, messages()))
        self.assertEqual(tool.name, "find_location")
        schema = tool.args_schema.model_json_schema()
        self.assertEqual(schema["properties"]["refresh"]["default"], False)
        self.assertEqual(tool.invoke({"keyword": STAY["name"]})["source"], SELECTED_LODGING_SOURCE)
        provider.assert_not_called()
        self.assertEqual(tool.invoke({"keyword": STAY["name"], "refresh": True})["status"], "not_found")
        provider.assert_called_once()

    def test_cancellation_still_stops_a_reused_location_result(self):
        provider = Mock()
        control = ChatRequestControl()
        with use_chat_request_control(control):
            tool = guard_chat_tool(bind_selected_lodging_location(provider, messages()), control)
            self.assertEqual(tool(keyword=STAY["name"])["source"], SELECTED_LODGING_SOURCE)
            control.cancel()
            with self.assertRaises(ChatRequestStopped):
                tool(keyword=STAY["name"])
        provider.assert_not_called()

    def test_agent_factory_installs_the_request_specific_location_tool(self):
        request_messages = messages()
        captured = []
        with (
            patch.object(chat_planner, "get_openai_api_key", return_value="test-only"),
            patch.object(chat_planner, "ChatOpenAI"),
            patch.object(chat_planner, "create_agent") as factory,
            patch.object(chat_planner, "find_location") as provider,
        ):
            def invoke(value):
                self.assertEqual(value, {"messages": request_messages})
                location = next(tool for tool in factory.call_args.kwargs["tools"]
                                if getattr(tool, "__name__", None) == "find_location")
                captured.append(location(keyword=STAY["name"]))
                return {"messages": request_messages, "structured_response": None}
            factory.return_value.invoke.side_effect = invoke
            chat_planner.run_chat_agent(request_messages, None)
        self.assertEqual(captured[0]["source"], SELECTED_LODGING_SOURCE)
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
