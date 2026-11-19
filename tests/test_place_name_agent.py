import unittest
from unittest.mock import patch

from langchain_core.tools import tool

from backend.app.agent import chat_planner, planner
from backend.app.routers.chat import ChatAgentReply
from backend.app.services.plan_evaluation import (
    check_places_from_search,
    collect_searched_place_ids,
)
from backend.app.tools.places import search_places_by_name


def make_plan(place_id="P043"):
    return {
        "days": [{
            "day": 1,
            "items": [{
                "item_type": "visit",
                "place_id": place_id,
                "title": "별빛누리공원",
            }],
        }],
    }


def search_record(name, place_id="P043"):
    return {
        "tool": name,
        "output": {
            "status": "ok",
            "places": [{"place_id": place_id}],
        },
    }


class PlaceNameAgentTests(unittest.TestCase):
    def assert_name_tool_registered(self, module, build):
        with (
            patch.object(
                module,
                "get_openai_api_key",
                return_value="test-only-key",
            ),
            patch.object(module, "ChatOpenAI"),
            patch.object(module, "create_agent") as factory,
        ):
            build()
            tools = factory.call_args.kwargs["tools"]
            self.assertEqual(tools.count(search_places_by_name), 1)

    def test_chat_agent_registers_name_search(self):
        self.assert_name_tool_registered(
            chat_planner,
            lambda: chat_planner.build_chat_agent(ChatAgentReply),
        )

    def test_plan_agent_registers_name_search(self):
        self.assert_name_tool_registered(
            planner,
            planner.build_planner_agent,
        )

    def test_tool_schema_exposes_name_and_result_limit(self):
        properties = tool(search_places_by_name).args

        self.assertEqual(set(properties), {"keyword", "max_count"})
        self.assertEqual(properties["keyword"]["type"], "string")
        self.assertEqual(properties["max_count"]["type"], "integer")

    def test_name_search_is_accepted_as_search_evidence(self):
        result = check_places_from_search(
            make_plan(),
            [search_record("search_places_by_name")],
        )

        self.assertTrue(result["passed"])

    def test_existing_radius_search_still_counts(self):
        result = check_places_from_search(
            make_plan(),
            [search_record("search_places")],
        )

        self.assertTrue(result["passed"])

    def test_both_search_tools_are_combined(self):
        ids = collect_searched_place_ids([
            search_record("search_places", "P092"),
            search_record("search_places_by_name", "P043"),
        ])

        self.assertEqual(ids, {"P092", "P043"})

    def test_unknown_id_is_not_added_from_name_search(self):
        result = check_places_from_search(
            make_plan("P999"),
            [search_record("search_places_by_name")],
        )

        self.assertFalse(result["passed"])
        self.assertEqual(len(result["reasons"]), 1)

    def test_details_lookup_alone_is_not_search_evidence(self):
        result = check_places_from_search(
            make_plan(),
            [{
                "tool": "get_place_details",
                "output": {
                    "status": "ok",
                    "place_id": "P043",
                    "name": "별빛누리공원",
                },
            }],
        )

        self.assertFalse(result["passed"])

    def test_name_search_without_results_is_not_evidence(self):
        outputs = [
            {"status": "not_found", "places": []},
            {"status": "db_error"},
            None,
            "검색 오류",
        ]

        for output in outputs:
            with self.subTest(output=output):
                result = check_places_from_search(
                    make_plan(),
                    [{
                        "tool": "search_places_by_name",
                        "output": output,
                    }],
                )

                self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()