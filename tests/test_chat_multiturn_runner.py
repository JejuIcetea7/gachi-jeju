import copy
from datetime import date, datetime
import json
import unittest
from unittest.mock import Mock, patch

from backend.app.routers.chat import ChatOptions
from backend.app.services import chat_api_evaluation as api_evaluation
from scripts import chat_multiturn_runner as runner
from test_chat_multiturn_evaluation import STAY, add_lodging_days, make_record


def make_scenario(*, lodging=False):
    options = copy.deepcopy(make_record()["request"]["options"])
    expected = [{"day": 1, "place_id": "TEST", "duration_minutes": 60}]
    if lodging:
        options.update(trip_days=2, lodging_status="undecided", lodging_stays=[])
        expected.append({"day": 2, "place_id": "OTHER", "duration_minutes": 60})
    followup = {
        "turn_id": "T02", "description": "체류시간 수정", "message": "90분으로 바꿔 주세요.",
        "preserve_visits": True,
        "expected_visits": [{"day": 1, "place_id": "TEST", "duration_minutes": 90}],
    }
    if lodging:
        followup = {
            "turn_id": "T02", "description": "숙소 후보 선택",
            "lodging_selection": {"night": 1, "candidate_index": 0},
            "preserve_visits": True, "expected_visits": copy.deepcopy(expected),
        }
    return {
        "case_id": "M03" if lodging else "M02", "description": "순서대로 대화 평가",
        "turns": [
            {"turn_id": "T01", "description": "최초 일정 생성",
             "request": {"message": "관광 일정을 만들어 주세요.", "options": options},
             "expectation": {"outcome": "plan"}, "expected_visits": expected},
            {**followup, "expectation": {"outcome": "plan"}},
        ],
    }


def make_lookup(query):
    return {
        "request": copy.deepcopy(query), "http_status": 200,
        "response": {
            "source": "kakao_local", "requested_at": "2026-11-17T00:00:00+00:00",
            "center": {key: query[key] for key in ("latitude", "longitude")},
            "radius_m": query["radius_m"],
            "places": [{
                **{key: STAY[key] for key in (
                    "name", "address", "latitude", "longitude", "provider_place_id", "place_url",
                )},
                "phone": "", "distance_m": 700,
            }],
        },
    }


class ChatMultiturnRunnerTests(unittest.TestCase):
    def setUp(self):
        self.agent = Mock()
        self.requests = []
        self.queries = []
        self.enterContext(patch.object(api_evaluation, "read_places_by_id", return_value={
            "TEST": {"place_id": "TEST", "name": "테스트 관광지"},
            "OTHER": {"place_id": "OTHER", "name": "다른 관광지"},
        }))

    def request(self, body, *, agent_runner, reference_date):
        self.assertIs(agent_runner, self.agent)
        self.assertEqual(reference_date, date(2026, 11, 17))
        self.requests.append(copy.deepcopy(body))
        record = make_record(90 if "90" in body["message"] else 60, f"route-{len(self.requests)}")
        options = ChatOptions.model_validate(body.get("options", {})).model_dump(mode="json")
        if options["trip_days"] == 2:
            add_lodging_days(record, with_moves=options["lodging_status"] == "provided")
        record["request"] = copy.deepcopy(body)
        record["response"]["options"] = options
        return record

    def lookup(self, query):
        self.queries.append(copy.deepcopy(query))
        return make_lookup(query)

    def run_scenario(self, scenario=None, **kwargs):
        return runner.run_chat_scenario(
            make_scenario() if scenario is None else scenario,
            agent_runner=self.agent, reference_date=date(2026, 11, 17),
            request_runner=kwargs.pop("request_runner", self.request), **kwargs,
        )

    def first_lodging_record(self):
        scenario = make_scenario(lodging=True)
        case = runner.resolve_turn_case(scenario, 0)
        return scenario, self.request(
            case["request"], agent_runner=self.agent, reference_date=date(2026, 11, 17),
        )

    def test_preflight_is_repeatable_and_preserves_input(self):
        for scenario in (make_scenario(), make_scenario(lodging=True)):
            saved = copy.deepcopy(scenario)
            result = runner.validate_scenario(scenario)
            self.assertEqual(runner.validate_scenario(result), result)
            self.assertEqual(scenario, saved)

    def test_invalid_later_turn_is_rejected_before_any_request(self):
        for edit in (
            lambda s: s["turns"][1].update(unknown=True),
            lambda s: s["turns"][1].update(turn_id="T01"),
            lambda s: s["turns"][1].update(request={"message": "다른 요청"}),
            lambda s: s["turns"][1].update(message=""),
            lambda s: s["turns"][1].update(options_patch={"requirements": ["invalid"]}),
            lambda s: s.update(description=""),
            lambda s: s.update(turns=[]),
        ):
            scenario = make_scenario()
            edit(scenario)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.run_scenario(scenario)
        self.assertEqual(self.requests, [])
        self.agent.assert_not_called()

    def test_two_turns_use_real_previous_response_and_current_routes(self):
        result = self.run_scenario()
        self.assertTrue(result["automatic_passed"], result)
        self.assertEqual(result["skipped_turns"], [])
        self.assertEqual(len(self.requests), 2)
        first = result["turns"][0]["raw"]["record"]
        self.assertEqual(self.requests[1]["current_plan"], first["response"]["plan"])
        self.assertEqual(self.requests[1]["history"][-1]["content"], first["response"]["reply"])
        self.assertEqual(result["turns"][1]["score"]["outcome"], "plan")
        self.agent.assert_not_called()

    def test_question_then_answer_keeps_history_without_inventing_a_plan(self):
        scenario = make_scenario()
        scenario["turns"][0].update(expectation={"outcome": "message"})
        scenario["turns"][0].pop("expected_visits")
        scenario["turns"][1].pop("preserve_visits")
        def question_first(body, **kwargs):
            record = self.request(body, **kwargs)
            if len(self.requests) == 1:
                record["response"].update(plan=None, reply="몇 분 방문하실까요?")
            return record
        result = self.run_scenario(scenario, request_runner=question_first)
        self.assertTrue(result["automatic_passed"], result)
        self.assertIsNone(self.requests[1]["current_plan"])
        self.assertEqual(self.requests[1]["history"][-1]["content"], "몇 분 방문하실까요?")
        self.assertTrue(result["turns"][0]["score"]["requires_manual_review"])

    def test_failed_step_stops_and_lists_unexecuted_turns(self):
        scenario = make_scenario()
        scenario["turns"].append({**copy.deepcopy(scenario["turns"][1]), "turn_id": "T03"})
        def wrong_duration(body, **kwargs):
            record = self.request(body, **kwargs)
            if len(self.requests) == 2:
                record["response"]["plan"]["days"][0]["items"][1].update(
                    duration_minutes=60, end_time="10:10",
                )
            return record
        result = self.run_scenario(scenario, request_runner=wrong_duration)
        self.assertFalse(result["automatic_passed"])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result["skipped_turns"], ["T03"])

    def test_raw_record_is_emitted_before_scoring_and_survives_scoring_failure(self):
        captured = []
        with patch.object(runner, "evaluate_chat_turn", side_effect=RuntimeError("PRIVATE")):
            result = self.run_scenario(on_record=lambda raw: captured.append(copy.deepcopy(raw)))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], result["turns"][0]["raw"])
        self.assertEqual(result["turns"][0]["scoring_error"], "RuntimeError")
        self.assertEqual(result["skipped_turns"], ["T02"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertFalse(result["automatic_passed"])

    def test_callback_mutation_does_not_change_scoring_or_next_request(self):
        def mutate(raw):
            raw["record"]["response"]["options"]["requirements"] = []
            raw["case"]["request"]["message"] = "수정한 사본"
        result = self.run_scenario(on_record=mutate)
        self.assertTrue(result["automatic_passed"], result)
        self.assertEqual(self.requests[1]["options"]["requirements"], ["accessible_toilet"])

    def test_callback_storage_failure_propagates_and_stops_further_calls(self):
        with self.assertRaises(OSError):
            self.run_scenario(on_record=Mock(side_effect=OSError("저장 실패")))
        self.assertEqual(len(self.requests), 1)

    def test_request_exception_is_recorded_without_private_exception_text(self):
        result = self.run_scenario(request_runner=Mock(side_effect=RuntimeError("PRIVATE")))
        self.assertEqual(result["turns"][0]["raw"]["record"]["execution_error"], "RuntimeError")
        self.assertEqual(result["skipped_turns"], ["T02"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertFalse(result["automatic_passed"])

    def test_lodging_selection_uses_actual_candidate_and_keeps_search_snapshot(self):
        result = self.run_scenario(make_scenario(lodging=True), lodging_runner=self.lookup)
        self.assertTrue(result["automatic_passed"], result)
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(self.queries[0], {
            "latitude": 33.41, "longitude": 126.51, "radius_m": 5000, "max_count": 5,
        })
        raw = result["turns"][1]["raw"]
        stay = raw["case"]["request"]["options"]["lodging_stays"][0]
        self.assertEqual(stay, STAY)
        self.assertEqual(raw["case"]["request"]["options"]["lodging_status"], "provided")
        self.assertIn("테스트 숙소", raw["case"]["request"]["message"])
        self.assertEqual(raw["lodging_record"]["response"]["places"][0]["provider_place_id"], "123")
        self.assertNotIn("phone", stay)
        self.assertNotIn("distance_m", stay)

    def test_lodging_scenario_requires_lookup_runner_before_agent_work(self):
        with self.assertRaises(ValueError):
            self.run_scenario(make_scenario(lodging=True))
        self.assertEqual(self.requests, [])

    def test_no_candidates_keeps_lookup_result_without_sending_lodging_chat(self):
        def empty_lookup(query):
            result = make_lookup(query)
            result["response"]["places"] = []
            return result
        result = self.run_scenario(make_scenario(lodging=True), lodging_runner=empty_lookup)
        self.assertEqual(len(self.requests), 1)
        raw = result["turns"][1]["raw"]
        self.assertIsNone(raw["record"])
        self.assertEqual(raw["lodging_record"]["response"]["places"], [])
        self.assertEqual(raw["preparation_error"]["stage"], "lodging_selection")
        self.assertFalse(result["automatic_passed"])

    def test_lookup_exception_keeps_failure_stage_and_hides_private_text(self):
        result = self.run_scenario(
            make_scenario(lodging=True), lodging_runner=Mock(side_effect=ValueError("PRIVATE")),
        )
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(result["turns"][1]["raw"]["preparation_error"], {
            "stage": "lodging_http", "error_type": "ValueError",
        })
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertFalse(result["automatic_passed"])

    def test_lodging_query_never_falls_back_to_earlier_visit_or_origin(self):
        _, record = self.first_lodging_record()
        last = copy.deepcopy(record["response"]["plan"]["days"][0]["items"][-1])
        last.update(verified=False, place_id=None)
        record["response"]["plan"]["days"][0]["items"].append(last)
        with self.assertRaises(ValueError):
            runner.prepare_lodging_query(record, 1)
        last["verified"] = True
        last["latitude"] = None
        record["response"]["plan"]["days"][0]["items"][-1] = last
        with self.assertRaises(ValueError):
            runner.prepare_lodging_query(record, 1)

    def test_wrong_search_center_status_and_candidate_shape_are_rejected(self):
        scenario, record = self.first_lodging_record()
        query = runner.prepare_lodging_query(record, 1)
        for edit in (
            lambda r: r.update(http_status=503),
            lambda r: r["request"].update(latitude=33.5),
            lambda r: r["response"]["center"].update(latitude=33.5),
            lambda r: r["response"].update(radius_m=1000),
            lambda r: r["response"]["places"][0].update(place_url="https://example.com/"),
            lambda r: r["response"]["places"].append(copy.deepcopy(r["response"]["places"][0])),
        ):
            lookup = make_lookup(query)
            edit(lookup)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                runner.resolve_turn_case(scenario, 1, previous_record=record, lodging_record=lookup)

    def test_reconstructing_saved_case_never_calls_live_services(self):
        scenario, record = self.first_lodging_record()
        lookup = make_lookup(runner.prepare_lodging_query(record, 1))
        saved = copy.deepcopy((scenario, record, lookup))
        with patch.object(runner, "run_chat_request", side_effect=AssertionError("실제 호출 금지")):
            case = runner.resolve_turn_case(scenario, 1, previous_record=record, lodging_record=lookup)
        self.assertEqual(case["request"]["options"]["lodging_stays"][0], STAY)
        self.assertEqual((scenario, record, lookup), saved)

    def test_bad_run_arguments_and_selection_rules_never_call_agent(self):
        for values in (
            {"reference_date": datetime(2026, 11, 17)}, {"request_runner": 1},
            {"lodging_runner": 1}, {"on_record": 1}, {"agent_runner": 1},
        ):
            args = {"agent_runner": self.agent, **values}
            with self.subTest(values=values), self.assertRaises(ValueError):
                runner.run_chat_scenario(make_scenario(), **args)
        for selection in (
            {"night": True, "candidate_index": 0}, {"night": 2, "candidate_index": 0},
            {"night": 1, "candidate_index": -1}, {"night": 1, "candidate_index": 5},
        ):
            scenario = make_scenario(lodging=True)
            scenario["turns"][1]["lodging_selection"] = selection
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                self.run_scenario(scenario, lodging_runner=self.lookup)
        self.assertEqual(self.requests, [])
        self.agent.assert_not_called()

    def test_inputs_and_result_snapshots_are_independent(self):
        scenario = make_scenario(lodging=True)
        saved = copy.deepcopy(scenario)
        result = self.run_scenario(scenario, lodging_runner=self.lookup)
        self.assertEqual(scenario, saved)
        result["turns"][1]["raw"]["case"]["request"]["options"]["requirements"].clear()
        self.assertEqual(scenario, saved)
        self.assertEqual(self.requests[1]["options"]["requirements"], ["accessible_toilet"])


if __name__ == "__main__":
    unittest.main()
