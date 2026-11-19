import copy
import csv
from datetime import date
import io
import json
from pathlib import Path
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from backend.app.routers.chat import ChatOptions
from backend.app.services import chat_api_evaluation as api_evaluation
from scripts import chat_multiturn_runner as scenario_runner
from scripts import evaluate_chat_multiturn as cli
from test_chat_multiturn_evaluation import STAY, add_lodging_days, make_record
from test_chat_multiturn_runner import make_lookup, make_scenario


class ChatMultiturnCliTests(unittest.TestCase):
    def setUp(self):
        self.files = {}
        self.folders = []
        self.requests = []
        self.queries = []
        self.cases = [make_scenario(), make_scenario(lodging=True)]
        self.files[str(cli.CASES_FILE)] = json.dumps(self.cases, ensure_ascii=False).encode("utf-8")
        self.enterContext(patch.object(Path, "read_text", lambda path, *args, **kwargs: self.read_text(path, *args, **kwargs)))
        self.enterContext(patch.object(Path, "write_text", lambda path, *args, **kwargs: self.write_text(path, *args, **kwargs)))
        self.enterContext(patch.object(cli, "append_jsonl", self.append_jsonl))
        self.folder_factory = self.enterContext(patch.object(cli, "create_output_folder", side_effect=self.new_folder))
        self.git = self.enterContext(patch.object(cli, "read_git_state", return_value={"commit": "generation", "dirty": False}))
        self.run = self.enterContext(patch.object(cli, "run_chat_scenario", side_effect=self.run_scenario))
        self.lookup = self.enterContext(patch.object(cli, "run_lodging_request", side_effect=self.lookup_request))
        self.enterContext(patch.object(api_evaluation, "read_places_by_id", return_value={
            "TEST": {"place_id": "TEST", "name": "테스트 관광지"},
            "OTHER": {"place_id": "OTHER", "name": "다른 관광지"},
        }))

    def read_text(self, path, encoding="utf-8", **kwargs):
        key = str(path)
        if key not in self.files:
            raise FileNotFoundError(key)
        return self.files[key].decode(encoding).replace("\r\n", "\n")

    def write_text(self, path, data, encoding="utf-8", **kwargs):
        self.files[str(path)] = data.encode(encoding)
        return len(data)

    def new_folder(self, label):
        path = Path("memory-results") / (str(len(self.folders) + 1) + "_" + label)
        self.folders.append(path)
        return path

    def append_jsonl(self, path, value):
        self.files[str(path)] = self.files.get(str(path), b"") + (
            json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")

    def request(self, body, *, agent_runner, reference_date):
        self.assertEqual(reference_date, date(2026, 11, 17))
        self.requests.append(copy.deepcopy(body))
        record = make_record(90 if "90" in body["message"] else 60, f"r{len(self.requests)}")
        options = ChatOptions.model_validate(body["options"]).model_dump(mode="json")
        if options["trip_days"] == 2:
            add_lodging_days(record, with_moves=options["lodging_status"] == "provided")
        record["request"] = copy.deepcopy(body)
        record["response"]["options"] = options
        return record

    def lookup_request(self, query):
        self.queries.append(copy.deepcopy(query))
        return make_lookup(query)

    def run_scenario(self, scenario, **kwargs):
        return scenario_runner.run_chat_scenario(scenario, request_runner=self.request, **kwargs)

    def invoke(self, args):
        self.stdout = io.StringIO()
        with redirect_stdout(self.stdout), redirect_stderr(io.StringIO()):
            return cli.main(args)

    def live(self, *args):
        return self.invoke(["--run", "--reference-date", "2026-11-17", *args])

    def payload(self, folder, name):
        return json.loads(self.files[str(folder / name)].decode("utf-8-sig"))

    def score_rows(self, folder):
        return [json.loads(line) for line in self.read_text(folder / "scores.jsonl").splitlines()]

    def test_default_mode_only_validates_and_never_writes_or_queries_git(self):
        saved = copy.deepcopy(self.files)
        self.assertEqual(self.invoke([]), 0)
        self.assertEqual(self.files, saved)
        self.folder_factory.assert_not_called()
        self.git.assert_not_called()
        self.run.assert_not_called()
        self.lookup.assert_not_called()

    def test_live_mode_writes_original_before_scores_and_uses_all_planned_turns(self):
        self.assertEqual(self.live(), 0)
        folder = self.folders[0]
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(len(self.read_text(folder / "runs.jsonl").splitlines()), 4)
        self.assertEqual(len(self.score_rows(folder)), 4)
        self.assertTrue(all(row["evaluation"]["automatic_passed"] for row in self.score_rows(folder)))
        metadata = self.payload(folder, "metadata.json")
        self.assertEqual(metadata["kind"], "chat_multiturn")
        self.assertEqual(metadata["generation"]["reference_date"], "2026-11-17")
        self.assertIn("테스트 숙소", self.read_text(folder / "report.md"))

    def test_repeat_starts_each_conversation_from_its_original_request(self):
        self.assertEqual(self.live("--case", "M02", "--repeat", "2"), 0)
        self.assertEqual(len(self.requests), 4)
        self.assertNotIn("history", self.requests[0])
        self.assertNotIn("history", self.requests[2])
        plans, rows, _ = cli.load_saved(self.folders[0])
        self.assertEqual(len(plans), 2)
        self.assertEqual([row["run_number"] for row in rows], [1, 1, 2, 2])

    def test_rescore_preserves_source_and_generation_and_never_calls_services(self):
        self.assertEqual(self.live("--case", "M02"), 0)
        source = self.folders[0]
        saved = {key: value for key, value in self.files.items() if key.startswith(str(source))}
        original = self.files[str(source / "runs.jsonl")]
        self.git.return_value = {"commit": "new-scoring", "dirty": True}
        with (
            patch.object(cli, "run_chat_scenario", side_effect=AssertionError("실행 금지")),
            patch.object(cli, "run_lodging_request", side_effect=AssertionError("조회 금지")),
            patch.object(cli, "LLM_MODEL", "changed-model"),
        ):
            self.assertEqual(self.invoke(["--rescore", str(source), "--label", "rescore"]), 0)
        target = self.folders[1]
        self.assertEqual(self.files[str(target / "runs.jsonl")], original)
        self.assertEqual({key: self.files[key] for key in saved}, saved)
        metadata = self.payload(target, "metadata.json")
        self.assertEqual(metadata["scoring_commit"], "new-scoring")
        self.assertEqual(metadata["generation"]["app_commit"], "generation")
        self.assertNotEqual(metadata["generation"]["model"], "changed-model")

    def test_rescore_reconstructs_lodging_selection_from_saved_lookup(self):
        self.assertEqual(self.live("--case", "M03"), 0)
        source = self.folders[0]
        self.assertEqual(self.invoke(["--rescore", str(source)]), 0)
        self.assertEqual(len(self.queries), 1)
        self.assertTrue(all(row["evaluation"]["automatic_passed"] for row in self.score_rows(self.folders[1])))

    def test_missing_tail_is_not_recorded_and_never_reported_as_zero_actual_calls(self):
        self.assertEqual(self.live("--case", "M02"), 0)
        folder = self.folders[0]
        lines = self.read_text(folder / "runs.jsonl").splitlines()
        self.files[str(folder / "runs.jsonl")] = (lines[0] + "\n").encode("utf-8")
        self.assertEqual(self.invoke(["--rescore", str(folder)]), 1)
        target = self.folders[1]
        scores = self.score_rows(target)
        self.assertEqual(scores[1]["evaluation"]["outcome"], "not_recorded")
        rows = list(csv.DictReader(io.StringIO(self.read_text(target / "summary.csv", encoding="utf-8-sig"))))
        self.assertEqual(rows[1]["recorded_model_calls"], "")
        self.assertEqual(rows[1]["scenario_passed"], "False")

    def test_empty_run_log_preserves_plan_and_cannot_pass(self):
        self.assertEqual(self.live("--case", "M02"), 0)
        source = self.folders[0]
        self.files[str(source / "runs.jsonl")] = b""
        self.assertEqual(self.invoke(["--rescore", str(source)]), 1)
        scores = self.score_rows(self.folders[1])
        self.assertEqual(len(scores), 2)
        self.assertTrue(all(row["evaluation"]["outcome"] == "not_recorded" for row in scores))

    def test_tampered_saved_case_fails_even_when_actual_response_is_valid(self):
        self.assertEqual(self.live("--case", "M02"), 0)
        source = self.folders[0]
        rows = [json.loads(line) for line in self.read_text(source / "runs.jsonl").splitlines()]
        rows[1]["turn"]["case"]["request"]["history"] = []
        self.files[str(source / "runs.jsonl")] = b""
        for row in rows:
            self.append_jsonl(source / "runs.jsonl", row)
        self.assertEqual(self.invoke(["--rescore", str(source)]), 1)
        result = self.score_rows(self.folders[1])[1]["evaluation"]
        self.assertFalse(result["checks"]["scenario_contract"]["passed"])

    def test_bad_source_schema_duplicates_and_order_fail_before_new_output(self):
        self.assertEqual(self.live("--case", "M02"), 0)
        source = self.folders[0]
        original = self.files[str(source / "runs.jsonl")]
        lines = original.decode().splitlines()
        variants = [
            (lines[0] + "\n" + lines[0] + "\n").encode(),
            (lines[1] + "\n").encode(),
            json.dumps({**json.loads(lines[0]), "kind": "old_api"}).encode(),
            json.dumps({**json.loads(lines[0]), "scenario": {"case_id": []}}).encode(),
        ]
        for data in variants:
            self.files[str(source / "runs.jsonl")] = data
            before = len(self.folders)
            self.assertEqual(self.invoke(["--rescore", str(source)]), 2)
            self.assertEqual(len(self.folders), before)
        self.files[str(source / "runs.jsonl")] = original

    def test_collector_and_grader_failures_keep_raw_records_and_skip_followup(self):
        with patch.object(self, "request", side_effect=RuntimeError("PRIVATE")):
            self.assertEqual(self.live("--case", "M02"), 1)
        folder = self.folders[0]
        self.assertEqual(len(self.read_text(folder / "runs.jsonl").splitlines()), 1)
        self.assertEqual(self.score_rows(folder)[0]["evaluation"]["outcome"], "execution_error")
        self.assertNotIn("PRIVATE", self.read_text(folder / "report.md"))
        with patch.object(scenario_runner, "evaluate_chat_turn", side_effect=RuntimeError("PRIVATE")):
            self.assertEqual(self.live("--case", "M02"), 1)
        folder = self.folders[1]
        self.assertEqual(len(self.read_text(folder / "runs.jsonl").splitlines()), 1)
        self.assertEqual(self.score_rows(folder)[0]["evaluation"]["outcome"], "evaluation_error")

    def test_csv_uses_utf8_bom_and_has_no_extra_windows_blank_lines(self):
        self.assertEqual(self.live("--case", "M02"), 0)
        data = self.files[str(self.folders[0] / "summary.csv")]
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\r\n", data)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))), 2)

    def test_filtering_rescore_uses_only_saved_selected_scenario(self):
        self.assertEqual(self.live(), 0)
        source = self.folders[0]
        self.assertEqual(self.invoke(["--rescore", str(source), "--case", "M03"]), 0)
        plan, rows, _ = cli.load_saved(self.folders[1])
        self.assertEqual([item["scenario"]["case_id"] for item in plan], ["M03"])
        self.assertTrue(all(row["scenario"]["case_id"] == "M03" for row in rows))

    def test_unknown_case_and_unsafe_cli_arguments_never_start_work(self):
        self.assertEqual(self.invoke(["--case", "missing"]), 2)
        for args in (
            ["--run", "--rescore", "source"], ["--repeat", "0"],
            ["--label", "../outside"], ["--rescore", "source", "--cases", "cases"],
            ["--rescore", "source", "--reference-date", "2026-11-17"],
            ["--rescore", "source", "--repeat", "2"],
        ):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.invoke(args)
        self.run.assert_not_called()
        self.folder_factory.assert_not_called()


class ChatMultiturnLodgingHttpTests(unittest.TestCase):
    def test_lodging_http_collector_uses_real_route_with_mock_provider(self):
        from backend.app.routers import lodgings
        query = {"latitude": 33.41, "longitude": 126.51, "radius_m": 5000, "max_count": 5}
        response = make_lookup(query)["response"]
        provider = {"status": "ok", **copy.deepcopy(response)}
        with (
            patch.object(lodgings, "get_kakao_api_key", return_value="fake"),
            patch.object(lodgings, "search_nearby_lodgings", return_value=provider) as search,
        ):
            result = cli.run_lodging_request(query)
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["request"], query)
        self.assertEqual(result["response"]["places"][0]["provider_place_id"], STAY["provider_place_id"])
        self.assertEqual(search.call_count, 1)
        self.assertNotIn("api_key", result["response"])

    def test_lodging_http_error_keeps_public_status_and_body(self):
        from backend.app.routers import lodgings
        query = {"latitude": 33.41, "longitude": 126.51, "radius_m": 5000, "max_count": 5}
        with (
            patch.object(lodgings, "get_kakao_api_key", return_value="fake"),
            patch.object(lodgings, "search_nearby_lodgings", return_value={"status": "auth_error"}),
        ):
            result = cli.run_lodging_request(query)
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(result["response"]["error"]["code"], "lodging_search_unavailable")


if __name__ == "__main__":
    unittest.main()
