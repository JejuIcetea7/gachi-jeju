from contextlib import ExitStack, redirect_stdout, redirect_stderr
import copy
import csv
from datetime import date
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import evaluate_chat_api as cli


def make_case():
    return {
        "case_id": "A01", "description": "추가 질문 평가",
        "request": {"message": "여행 조건을 알려 주세요."},
        "expectation": {"outcome": "message"},
        "review_points": ["질문이 요청에 적절한지 확인합니다."],
    }


def make_record(case=None):
    case = cli.validate_case(case or make_case())
    return {
        "schema_version": 1, "case": case, "run_number": 1,
        "request": copy.deepcopy(case["request"]), "reference_date": "2026-11-16",
        "http_status": 200,
        "response": {
            "reply": "여행 날짜와 이동수단을 알려 주세요.", "options": {}, "plan": None,
            "warnings": [], "map_routes": [], "transit_routes": [],
            "meta": {"model_call_count": 1, "tool_call_counts": {}, "elapsed_seconds": 1},
        },
        "attempts": [{
            "attempt": 1, "status": "returned", "error_type": None, "capture_error": None,
            "retry_feedback": [], "chat_reply": None, "tool_trace": [], "route_calls": [],
            "metrics": {"model_call_count": 1, "tool_call_counts": {}}, "process": None,
        }],
        "elapsed_seconds": 1, "model": "recorded-model",
        "app_commit": "original", "worktree_dirty": False,
    }


class MemoryFile:
    def __init__(self):
        self.data = b""

    def write_text(self, text, encoding="utf-8", newline=None):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(
            buffer, encoding=encoding, newline="\r\n" if newline is None else newline,
        )
        count = stream.write(text)
        stream.flush()
        self.data = buffer.getvalue()
        stream.detach()
        return count


class MemoryFolder:
    def __init__(self):
        self.files = {}

    def __truediv__(self, name):
        return self.files.setdefault(name, MemoryFile())

    def __str__(self):
        return "<memory-output>"


class ChatEvaluationCliTests(unittest.TestCase):
    def setUp(self):
        self.case = cli.validate_case(make_case())
        self.record = make_record(self.case)
        self.folder = MemoryFolder()
        self.settings = {
            "mode": "run", "scoring_commit": "test", "scoring_worktree_dirty": False,
            "planned_runs": 1,
        }

    def run_main(self, arguments, *, cases=None, saved=None, runner=None):
        events = []
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(io.StringIO()))
            load_cases = stack.enter_context(patch.object(
                cli, "load_cases", return_value=cases if cases is not None else [self.case],
            ))
            load_records = stack.enter_context(patch.object(
                cli, "load_records", return_value=saved,
            ))
            create = stack.enter_context(patch.object(
                cli, "create_output_folder", return_value=self.folder,
            ))
            git = stack.enter_context(patch.object(
                cli, "read_git_state", return_value={"commit": "new", "dirty": True},
            ))
            run = stack.enter_context(patch.object(
                cli, "run_chat_request", side_effect=runner or (
                    lambda *args, **kwargs: copy.deepcopy(self.record)
                ),
            ))
            stack.enter_context(patch.object(
                cli, "append_jsonl",
                side_effect=lambda path, value: events.append(copy.deepcopy(value)),
            ))
            code = cli.main(arguments)
        return code, events, load_cases, load_records, create, git, run

    def test_default_mode_only_validates_without_api_git_or_output(self):
        code, events, _, _, create, git, run = self.run_main([])
        self.assertEqual(code, 0)
        self.assertEqual(events, [])
        create.assert_not_called()
        git.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.folder.files, {})

    def test_live_mode_saves_case_snapshot_and_generation_metadata(self):
        code, events, _, _, _, _, run = self.run_main([
            "--run", "--case", "A01", "--reference-date", "2026-11-16",
        ])
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["reference_date"], date(2026, 11, 16))
        raw = events[0]
        self.assertEqual(raw["case"], self.case)
        self.assertEqual(raw["app_commit"], "new")
        self.assertTrue(raw["worktree_dirty"])
        self.assertTrue(events[1]["evaluation"]["requires_manual_review"])

    def test_raw_record_is_saved_before_scoring_failure(self):
        with patch.object(cli, "evaluate_chat_record", side_effect=RuntimeError("private-score-detail")):
            code, events, *_ = self.run_main(["--run"])
        self.assertEqual(code, 1)
        self.assertIn("response", events[0])
        self.assertEqual(events[1]["evaluation"]["outcome"], "evaluation_error")
        self.assertNotIn("private-score-detail", json.dumps(events))

    def test_request_collection_failure_is_recorded_and_next_run_continues(self):
        runner = Mock(side_effect=[TimeoutError("private-provider-detail"), self.record])
        code, events, *_ = self.run_main(["--run", "--repeat", "2"], runner=runner)
        self.assertEqual(code, 1)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(events[0]["execution_error"], "TimeoutError")
        self.assertEqual(events[2]["run_number"], 2)
        self.assertNotIn("private-provider-detail", json.dumps(events))

    def test_rescore_uses_recorded_case_without_live_calls_or_source_changes(self):
        before = copy.deepcopy(self.record)
        code, events, load_cases, _, _, _, run = self.run_main(
            ["--rescore", "old-folder", "--label", "rescore"], saved=[self.record],
        )
        self.assertEqual(code, 0)
        load_cases.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.record, before)
        self.assertEqual(events[0]["app_commit"], "original")
        self.assertEqual(events[0]["model"], "recorded-model")

    def test_result_csv_round_trips_korean_on_windows_without_blank_lines(self):
        self.record["case"]["case_id"] = "A01"
        self.record["response"]["reply"] = "날짜를 알려 주세요."
        score = cli.score_record(self.record)
        cli.write_reports(self.folder, [self.record], [score], self.settings)
        data = self.folder.files["summary.csv"].data
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\r\n", data)
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))
        self.assertEqual(len(rows), 1)
        self.assertIn("질문", rows[0]["review_reasons"])

    def test_invalid_response_body_is_reported_without_losing_raw_data(self):
        for body in (["invalid"], {"plan": "invalid"}, {"reply": 123}):
            record = copy.deepcopy(self.record)
            record["response"] = body
            score = cli.score_record(record)
            report = cli.build_report([record], [score], self.settings)
            self.assertFalse(score["automatic_passed"])
            self.assertIn("invalid_response", report)
            self.assertEqual(record["response"], body)

    def test_error_response_extra_plan_fields_do_not_break_report_generation(self):
        self.record["http_status"] = 500
        self.record["response"] = {
            "error": {"code": "internal_error", "message": "오류 안내"},
            "plan": {"unexpected": True},
        }
        score = cli.score_record(self.record)
        report = cli.build_report([self.record], [score], self.settings)
        self.assertFalse(score["automatic_passed"])
        self.assertIn("오류 안내", report)

    def test_recorded_call_counts_ignore_missing_or_invalid_metrics(self):
        record = copy.deepcopy(self.record)
        record["attempts"] += [
            None, {"status": "error"}, {"metrics": {
                "model_call_count": True, "tool_call_counts": {"bad": -1, "known": 2},
            }},
        ]
        score = cli.score_record(self.record)
        rows = cli.summary_rows([record], [score])
        self.assertEqual(rows[0]["recorded_model_calls"], 1)
        self.assertEqual(rows[0]["recorded_tool_calls"], 2)

    def test_preflight_preserves_input_and_accepts_expected_invalid_request(self):
        original = make_case()
        before = copy.deepcopy(original)
        cli.validate_case(original)
        self.assertEqual(original, before)
        invalid = {
            **original, "request": {"message": ""},
            "expectation": {"outcome": "error", "http_status": 400, "error_code": "invalid_request"},
        }
        self.assertEqual(cli.validate_case(invalid)["request"], {"message": ""})

    def test_bad_case_fields_options_windows_and_json_numbers_are_rejected(self):
        invalid_cases = []
        for update in (
            {"typo": True}, {"case_id": "../bad"}, {"description": ""},
            {"request": {"message": ""}},
            {"expected_options": {"unknown": True}},
            {"time_window": {"start": "10:00"}},
            {"time_window": {"start": "15:00", "end": "10:00"}},
            {"review_points": [""]},
        ):
            invalid_cases.append({**make_case(), **update})
        invalid_cases.append({**make_case(), "request": {"message": "여행", "bad": float("nan")}})
        for case in invalid_cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                cli.validate_case(case)

    def test_case_file_selection_checks_unknown_and_duplicate_ids(self):
        with patch.object(Path, "read_text", return_value=json.dumps([make_case()])):
            self.assertEqual(cli.load_cases("cases.json", "A01")[0]["case_id"], "A01")
            with self.assertRaises(ValueError):
                cli.load_cases("cases.json", "missing")
        with patch.object(Path, "read_text", return_value=json.dumps([make_case(), make_case()])):
            with self.assertRaises(ValueError):
                cli.load_cases("cases.json")

    def test_saved_record_requires_new_schema_and_unique_case_run_keys(self):
        for records in (
            [{"case_id": "legacy"}],
            [{**self.record, "schema_version": True}],
            [self.record, self.record],
        ):
            text = "\n".join(json.dumps(record) for record in records)
            with patch.object(Path, "read_text", return_value=text), self.assertRaises(ValueError):
                cli.load_records("old-folder")
        with patch.object(Path, "read_text", return_value=json.dumps(self.record)):
            self.assertEqual(cli.load_records("old-folder")[0]["case"], self.record["case"])

    def test_cli_rejects_conflicting_modes_and_unsafe_labels(self):
        examples = (
            ["--run", "--rescore", "old"], ["--repeat", "0"], ["--label", "../bad"],
            ["--rescore", "old", "--cases", "cases.json"],
            ["--rescore", "old", "--reference-date", "2026-11-16"],
        )
        for arguments in examples:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.parse_arguments(arguments)

    def test_git_metadata_does_not_run_commands_other_than_read_only_queries(self):
        results = [
            SimpleNamespace(returncode=0, stdout="abcdef\n"),
            SimpleNamespace(returncode=0, stdout=" M scripts/evaluate_chat_api.py\n"),
        ]
        with patch.object(cli.subprocess, "run", side_effect=results) as run:
            self.assertEqual(cli.read_git_state(), {"commit": "abcdef", "dirty": True})
        self.assertEqual(run.call_args_list[0].args[0], ["git", "rev-parse", "--short", "HEAD"])
        self.assertEqual(run.call_args_list[1].args[0], ["git", "status", "--porcelain"])


if __name__ == "__main__":
    unittest.main()