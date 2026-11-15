import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_chat_auto_retry as helpers
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router


def make_details(place_id, parking="Y", toilet="Y", facts=None):
    return {
        "status": "ok", "place_id": place_id, "name": place_id,
        "accessibility": {
            "accessible_parking": parking,
            "accessible_toilet": toilet,
            "has_slope_or_stairs": "Y",
        },
        "accessibility_facts": [] if facts is None else facts,
    }


class ChatAccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.options = {
            **helpers.OPTIONS,
            "requirements": ["accessible_parking"],
            "mobility_aid": "none",
        }
        self.agent = self.enterContext(patch.object(chat_router, "run_chat_agent"))
        self.records = {
            "TEST_A": make_details("TEST_A"),
            "TEST_B": make_details("TEST_B"),
        }
        self.details = self.enterContext(patch.object(
            chat_router, "read_place_details",
            side_effect=lambda *, place_id: copy.deepcopy(self.records[place_id]),
        ))
        self.lookup = self.enterContext(patch.object(
            plan_router, "find_place_in_database",
            side_effect=lambda place_id: {
                "place_id": place_id, "name": place_id, "category": "공원",
                **(helpers.FIRST if place_id == "TEST_A" else helpers.SECOND),
            },
        ))
        self.enterContext(patch.object(
            chat_router, "read_closed_days_by_place_id", return_value={},
        ))
        self.enterContext(patch.object(
            chat_router, "read_opening_hours_by_place_id", return_value={},
        ))
        self.enterContext(patch.object(chat_router.logger, "exception"))
        self.request = chat_router.ChatRequest(
            message="선택한 필수 조건을 유지해 일정을 만들어 주세요.",
            options=self.options,
        )

    def result(self, route_id="route", place_id="TEST_A", *, options=None, minutes=10):
        destination = helpers.FIRST if place_id == "TEST_A" else helpers.SECOND
        return helpers.make_result(
            helpers.make_plan(route_id, place_id, minutes=minutes),
            routes=[helpers.make_route(route_id, destination)],
            options=self.options if options is None else options,
        )

    def post(self):
        response = chat_router.create_chat(self.request)
        if isinstance(response, dict):
            return 200, chat_router.ChatResponse.model_validate(
                response
            ).model_dump(mode="json")
        return response.status_code, json.loads(response.body)

    def test_confirmed_first_plan_does_not_retry(self):
        self.agent.side_effect = [self.result()]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 1)
        self.details.assert_called_once_with(place_id="TEST_A")
        self.assertEqual(body["warnings"], [])

    def test_missing_facility_retries_with_original_conditions_and_fresh_route(self):
        self.records["TEST_A"]["accessibility"]["accessible_parking"] = "N"
        self.agent.side_effect = [self.result("old"), self.result("new", "TEST_B")]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(self.details.call_count, 2)
        self.assertEqual(body["plan"]["days"][0]["items"][1]["place_id"], "TEST_B")
        self.assertEqual(
            [route["route_id"] for route in body["map_routes"]], ["new"]
        )
        retry = json.loads(
            self.agent.call_args_list[1].args[0][-1]["content"]
        )["plan_retry"]
        self.assertEqual(
            retry["locked_options"]["requirements"], ["accessible_parking"]
        )
        self.assertTrue(any(
            "장애인 주차구역" in value for value in retry["validation_errors"]
        ))
        self.assertTrue(all(
            item["route_id"] is None
            for item in retry["failed_plan"]["days"][0]["items"]
        ))

    def test_second_facility_failure_stops_after_two_calls(self):
        self.records["TEST_A"]["accessibility"]["accessible_parking"] = "N"
        self.agent.side_effect = [self.result("first"), self.result("second")]
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertEqual(self.agent.call_count, 2)
        self.assertNotIn("plan", body)

    def test_unknown_facility_is_warned_without_retry(self):
        self.records["TEST_A"]["accessibility"]["accessible_parking"] = "unknown"
        self.agent.side_effect = [self.result()]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 1)
        self.assertTrue(any(
            "장애인 주차구역" in value and "미확인" in value
            for value in body["warnings"]
        ))

    def test_explicit_toilet_absence_in_survey_causes_retry(self):
        self.options["requirements"] = ["accessible_toilet"]
        self.request = chat_router.ChatRequest(
            message="화장실 필수 조건을 유지해 주세요.", options=self.options,
        )
        self.records["TEST_A"] = make_details("TEST_A", toilet="unknown", facts=[{
            "section": "기본정보", "description": "장애인 화장실", "raw_value": "없음",
        }])
        self.agent.side_effect = [self.result("old"), self.result("new", "TEST_B")]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["plan"]["days"][0]["items"][1]["place_id"], "TEST_B")

    def test_conflicting_facility_information_is_not_confirmed(self):
        self.options["requirements"] = ["accessible_toilet"]
        self.request = chat_router.ChatRequest(
            message="일정을 만들어 주세요.", options=self.options,
        )
        self.records["TEST_A"] = make_details("TEST_A", facts=[{
            "section": "기본정보", "description": "장애인 화장실", "raw_value": "없음",
        }])
        self.agent.side_effect = [self.result()]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 1)
        self.assertTrue(any(
            "장애인 화장실" in value and "미확인" in value
            for value in body["warnings"]
        ))

    def test_database_failure_status_does_not_retry(self):
        self.details.side_effect = None
        self.details.return_value = {"status": "db_error"}
        self.agent.side_effect = [self.result()]
        status, body = self.post()
        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "internal_error")
        self.assertEqual(self.agent.call_count, 1)

    def test_database_exception_does_not_retry(self):
        self.details.side_effect = sqlite3.OperationalError("private mock details")
        self.agent.side_effect = [self.result()]
        status, body = self.post()
        self.assertEqual(status, 500)
        self.assertEqual(self.agent.call_count, 1)
        self.assertNotIn("private mock details", str(body))

    def test_invalid_lookup_result_is_not_an_empty_success(self):
        for data in (None, {"status": "unexpected"}, make_details("WRONG")):
            with self.subTest(data=data):
                self.agent.reset_mock()
                self.agent.side_effect = [self.result()]
                self.details.side_effect = None
                self.details.return_value = data
                status, _ = self.post()
                self.assertEqual(status, 500)
                self.assertEqual(self.agent.call_count, 1)

    def test_no_selected_access_options_skip_extra_lookup(self):
        options = {**self.options, "requirements": [], "mobility_aid": None}
        self.request = chat_router.ChatRequest(
            message="일정을 만들어 주세요.", options=options,
        )
        self.agent.side_effect = [self.result(options=options)]
        status, _ = self.post()
        self.assertEqual(status, 200)
        self.details.assert_not_called()

    def test_question_can_change_options_without_generating_a_plan(self):
        options = {**self.options, "requirements": []}
        self.agent.side_effect = [helpers.make_result(plan=None, options=options)]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.assertEqual(body["options"]["requirements"], [])
        self.details.assert_not_called()

    def test_time_error_skips_first_lookup(self):
        self.agent.side_effect = [
            self.result("old", minutes=9), self.result("new"),
        ]
        status, _ = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.details.assert_called_once_with(place_id="TEST_A")

    def test_accessibility_is_looked_up_again_on_retry(self):
        self.details.side_effect = [
            make_details("TEST_A", parking="N"),
            make_details("TEST_A", parking="Y"),
        ]
        self.agent.side_effect = [self.result("old"), self.result("new")]
        status, _ = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.details.call_count, 2)

    def test_first_attempt_cannot_drop_selected_conditions_or_mobility_aid(self):
        self.options["mobility_aid"] = "wheelchair"
        self.request = chat_router.ChatRequest(
            message="조건을 유지해 주세요.", options=self.options,
        )
        self.records["TEST_A"]["accessibility"]["accessible_parking"] = "N"
        first = self.result("old", options={
            **self.options, "requirements": [], "mobility_aid": "none",
        })
        before = copy.deepcopy(first["chat_reply"].model_dump(mode="json"))
        self.agent.side_effect = [first, self.result("new", "TEST_B")]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["options"]["requirements"], ["accessible_parking"])
        self.assertEqual(body["options"]["mobility_aid"], "wheelchair")
        self.assertEqual(first["chat_reply"].model_dump(mode="json"), before)

    def test_unverified_places_do_not_supply_facility_evidence(self):
        self.lookup.side_effect = None
        self.lookup.return_value = None
        self.agent.side_effect = [self.result()]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.details.assert_not_called()
        self.assertTrue(any(
            "장애인 주차구역" in value and "미확인" in value
            for value in body["warnings"]
        ))

    def test_not_found_is_not_treated_as_confirmed_absence(self):
        self.details.side_effect = None
        self.details.return_value = {"status": "not_found"}
        self.agent.side_effect = [self.result()]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 1)
        self.assertTrue(any("미확인" in value for value in body["warnings"]))

    def test_retry_cannot_drop_the_locked_conditions(self):
        self.records["TEST_A"]["accessibility"]["accessible_parking"] = "N"
        self.agent.side_effect = [
            self.result("old"),
            self.result(
                "new", "TEST_B", options={**self.options, "requirements": []},
            ),
        ]
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(all(
            detail["field"] == "options" for detail in body["error"]["details"]
        ))


if __name__ == "__main__":
    unittest.main()