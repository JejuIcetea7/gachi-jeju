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
            detail["field"] == "response" for detail in body["error"]["details"]
        ))

    def test_introduction_is_preserved_without_word_based_accessibility_gate(self):
        reply = "접근성 정보 확인을 도와드릴 수 있어요."
        result = helpers.make_result(options=self.options)
        result["chat_reply"].reply = reply
        self.request = chat_router.ChatRequest(message="어떤 도움을 줄 수 있어?", options=self.options)
        self.agent.return_value = result
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], reply)
        self.details.assert_not_called()

    def test_information_reply_with_keep_action_is_not_replaced(self):
        message = "박물관을 소개해줘. 일정은 만들지 마."
        result = helpers.make_result(options=self.options)
        result["chat_reply"].reply = "제주의 역사와 문화를 소개하는 박물관입니다."
        result["chat_reply"].request_actions = [chat_router.RequestAction(subject="itinerary", action="keep", evidence="일정은 만들지 마.")]
        self.request = chat_router.ChatRequest(message=message, options=self.options)
        self.agent.return_value = result
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], result["chat_reply"].reply)

    def test_declared_option_change_is_checked_without_reading_words(self):
        result = helpers.make_result(options={**self.options, "requirements": []})
        result["chat_reply"].changed_options = ["requirements"]
        self.agent.return_value = result
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["options"]["requirements"], [])

    def test_wrong_structured_facility_value_is_returned_as_feedback(self):
        first = helpers.make_result(options=self.options)
        first["chat_reply"].reply = "화장실은 확인되지 않았습니다."
        first["chat_reply"].accessibility_checks = [chat_router.AccessibilityCheck(
            place_name="TEST_A", topics=["accessible_toilet"],
            findings=[chat_router.FacilityFinding(topic="accessible_toilet", status="unknown")])]
        second = copy.deepcopy(first)
        second["chat_reply"].accessibility_checks[0].findings[0].status = "Y"
        second["chat_reply"].reply = "조사 자료에서 화장실이 확인됐습니다."
        self.agent.side_effect = [first, second]
        with patch.object(chat_router, "find_unique_exact_place_id", return_value="TEST_A"):
            status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], second["chat_reply"].reply)
        self.assertEqual(self.agent.call_count, 2)
        feedback = json.loads(self.agent.call_args_list[1].args[0][-1]["content"])["plan_retry"]
        self.assertTrue(any("기대값" in error and "실제값" in error for error in feedback["validation_errors"]))

    def test_unknown_external_facility_cannot_be_marked_confirmed(self):
        result = helpers.make_result(options=self.options)
        result["chat_reply"].accessibility_checks = [chat_router.AccessibilityCheck(
            place_name="외부 식당", topics=["wheelchair"],
            findings=[chat_router.FacilityFinding(topic="wheelchair", status="Y")])]
        self.agent.return_value = result
        with patch.object(chat_router, "find_unique_exact_place_id", return_value=None):
            status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")


    def test_facility_data_error_is_not_fed_back_as_model_error(self):
        for record in ({"status": "db_error"}, {**make_details("OTHER"), "status": "ok"}):
            with self.subTest(record=record):
                self.agent.reset_mock()
                self.records["TEST_A"] = record
                result = helpers.make_result(options=self.options)
                result["chat_reply"].accessibility_checks = [chat_router.AccessibilityCheck(
                    place_name="TEST_A", topics=["accessible_toilet"],
                    findings=[chat_router.FacilityFinding(topic="accessible_toilet", status="Y")])]
                self.agent.return_value = result
                with patch.object(chat_router, "find_unique_exact_place_id", return_value="TEST_A"):
                    status, body = self.post()
                self.assertEqual(status, 500)
                self.assertEqual(body["error"]["code"], "internal_error")
                self.agent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
