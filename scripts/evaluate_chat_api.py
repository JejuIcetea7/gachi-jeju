"""최종 /chat 평가. 기본은 사례 검증이며 --run에서만 실제 Agent를 호출합니다."""
import argparse
from copy import deepcopy
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import re
import subprocess
from time import perf_counter

from pydantic import ValidationError

from backend.app.config import LLM_MODEL, LLM_REASONING_EFFORT
from backend.app.routers.chat import ChatOptions, ChatRequest
from backend.app.services.chat_api_evaluation import evaluate_chat_record
from backend.app.services.chat_outcomes import validate_outcome_expectation
from backend.app.services.plan_validation import validate_plan_times
from scripts.chat_evaluation_runner import run_chat_request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = PROJECT_ROOT / "scripts" / "eval_cases" / "chat_api_cases.json"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "chat_eval" / "api"
KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = 1
CASE_FIELDS = {
    "case_id", "description", "request", "expectation",
    "time_window", "expected_options", "review_points",
}


def validate_case(case):
    if not isinstance(case, dict) or set(case) - CASE_FIELDS:
        raise ValueError("평가 사례의 필드를 확인해 주세요.")
    result = deepcopy(case)
    case_id = result.get("case_id")
    if not isinstance(case_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", case_id) is None:
        raise ValueError("사례 ID는 영문·숫자·밑줄·하이픈 1~64자로 작성해 주세요.")
    if not isinstance(result.get("description"), str) or not result["description"].strip():
        raise ValueError(f"{case_id}: 사례 설명이 필요합니다.")
    if not isinstance(result.get("request"), dict):
        raise ValueError(f"{case_id}: 요청은 객체여야 합니다.")
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    result["expectation"] = validate_outcome_expectation(result.get("expectation"))
    overrides = result.get("expected_options", {})
    if not isinstance(overrides, dict) or set(overrides) - set(ChatOptions.model_fields):
        raise ValueError(f"{case_id}: 기대 options의 필드를 확인해 주세요.")
    try:
        request = ChatRequest.model_validate(result["request"])
    except ValidationError as error:
        if result["expectation"]["http_status"] != 400 or overrides:
            raise ValueError(f"{case_id}: 요청 또는 기대 options의 형식이 올바르지 않습니다.") from error
    else:
        try:
            expected = ChatOptions.model_validate({
                **request.options.model_dump(mode="json"), **overrides,
            }).model_dump(mode="json")
        except ValidationError as error:
            raise ValueError(f"{case_id}: 기대 options의 형식이 올바르지 않습니다.") from error
        result["expected_options"] = {key: expected[key] for key in overrides}
    window = result.get("time_window")
    if window is not None:
        if (
            not isinstance(window, dict) or set(window) != {"start", "end"}
            or not all(isinstance(window[key], str) for key in ("start", "end"))
        ):
            raise ValueError(f"{case_id}: 시간 범위에는 start와 end가 필요합니다.")
        validate_plan_times({"days": []}, start_time=window["start"], end_time=window["end"])
    points = result.get("review_points", [])
    if not isinstance(points, list) or any(
        not isinstance(point, str) or not point.strip() for point in points
    ):
        raise ValueError(f"{case_id}: 내용 검토 항목은 비어 있지 않은 문장 목록이어야 합니다.")
    result["review_points"] = points
    return result


def load_cases(path, case_id=None):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise ValueError("평가 사례는 비어 있지 않은 목록이어야 합니다.")
    cases = [validate_case(case) for case in data]
    ids = [case["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("평가 사례 ID가 중복됐습니다.")
    if case_id is not None:
        cases = [case for case in cases if case["case_id"] == case_id]
        if not cases:
            raise ValueError(f"평가 사례 {case_id}를 찾지 못했습니다.")
    return cases


def load_records(folder, case_id=None):
    records = []
    seen = set()
    for line in (Path(folder) / "runs.jsonl").read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if (
            not isinstance(record, dict) or type(record.get("schema_version")) is not int
            or record["schema_version"] != SCHEMA_VERSION
        ):
            raise ValueError("새 /chat 평가 형식의 실행 기록이 아닙니다.")
        json.dumps(record, ensure_ascii=False, allow_nan=False)
        case = validate_case(record.get("case"))
        number = record.get("run_number")
        if type(number) is not int or number < 1:
            raise ValueError("실행 회차가 올바르지 않습니다.")
        key = (case["case_id"], number)
        if key in seen:
            raise ValueError("실행 기록의 사례 ID·회차가 중복됐습니다.")
        seen.add(key)
        if case_id is None or case["case_id"] == case_id:
            records.append(record)
    if not records:
        raise ValueError("재채점할 실행 기록이 없습니다.")
    return records


def score_record(record):
    failure = record.get("execution_error")
    if not failure:
        try:
            result = deepcopy(evaluate_chat_record(record["case"], record))
            if result["automatic_passed"]:
                reasons = result["review_reasons"] + record["case"].get("review_points", [])
                result["review_reasons"] = list(dict.fromkeys(reasons))
                result["requires_manual_review"] = bool(reasons)
            return result
        except Exception as error:
            failure = type(error).__name__
    return {
        "evaluation_status": "error", "evaluation_errors": [f"평가 실행 오류: {failure}"],
        "outcome": "execution_error" if record.get("execution_error") else "evaluation_error",
        "automatic_passed": False, "requires_manual_review": False,
        "review_reasons": [], "checks": {},
        "attempt_count": len(record["attempts"]) if isinstance(record.get("attempts"), list) else 0,
    }


def read_git_state():
    state = {"commit": "unknown", "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, check=False,
        )
        if commit.returncode == 0:
            state["commit"] = commit.stdout.strip()
        if status.returncode == 0:
            state["dirty"] = bool(status.stdout.strip())
    except OSError:
        pass
    return state


def create_output_folder(label):
    folder = OUTPUT_ROOT / (datetime.now(KST).strftime("%Y%m%d-%H%M%S-%f") + "_" + label)
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def append_jsonl(path, value):
    line = json.dumps(value, ensure_ascii=False, allow_nan=False)
    with Path(path).open("a", encoding="utf-8", newline="\n") as file:
        file.write(line + "\n")


def _recorded_calls(record):
    models = tools = 0
    attempts = record.get("attempts")
    for attempt in attempts if isinstance(attempts, list) else []:
        if not isinstance(attempt, dict) or not isinstance(attempt.get("metrics"), dict):
            continue
        metrics = attempt["metrics"]
        count = metrics.get("model_call_count")
        if type(count) is int and count >= 0:
            models += count
        counts = metrics.get("tool_call_counts")
        for count in counts.values() if isinstance(counts, dict) else []:
            if type(count) is int and count >= 0:
                tools += count
    return models, tools


def summary_rows(records, scores):
    rows = []
    for record, score in zip(records, scores, strict=True):
        models, tools = _recorded_calls(record)
        reasons = [
            reason for check in score["checks"].values() for reason in check["reasons"]
        ] + score["evaluation_errors"]
        rows.append({
            "case_id": record["case"]["case_id"], "run_number": record["run_number"],
            "model": record.get("model"), "app_commit": record.get("app_commit"),
            "worktree_dirty": record.get("worktree_dirty"),
            "http_status": record.get("http_status"), "outcome": score["outcome"],
            "automatic_passed": score["automatic_passed"],
            "requires_manual_review": score["requires_manual_review"],
            "evaluation_status": score["evaluation_status"],
            "attempt_count": score["attempt_count"],
            "recorded_model_calls": models, "recorded_tool_calls": tools,
            "elapsed_seconds": record.get("elapsed_seconds"),
            "failed_reasons": " | ".join(dict.fromkeys(reasons)),
            "review_reasons": " | ".join(score["review_reasons"]),
        })
    return rows


def _md(value):
    return str(value).replace("|", r"\|").replace("\r", " ").replace("\n", " ")


def build_report(records, scores, settings):
    rows = summary_rows(records, scores)
    lines = [
        "# 최종 /chat 평가 결과", "",
        f"- 채점 커밋: {settings['scoring_commit']} (미커밋 변경: {settings['scoring_worktree_dirty']})",
        f"- 실행 방식: {settings['mode']}",
        f"- 처리한 실행 수: {len(rows)} / {settings['planned_runs']}",
        f"- 자동 검사 통과: {sum(row['automatic_passed'] for row in rows)}",
        f"- 내용 검토 대기: {sum(row['requires_manual_review'] for row in rows)}",
        f"- 평가 실행 오류: {sum(row['evaluation_status'] == 'error' for row in rows)}", "",
        "자동 검사 통과와 내용 검토 완료는 구분합니다.",
        "호출 수는 반환받은 시도의 기록 합계이며, 예외 발생 중 추가 호출은 포함되지 않을 수 있습니다.", "",
        "| 사례·회차 | HTTP | 응답 유형 | 자동 통과 | 내용 검토 | 초 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_id']} / {row['run_number']} | {row['http_status']} | "
            f"{row['outcome']} | {row['automatic_passed']} | "
            f"{row['requires_manual_review']} | {row['elapsed_seconds']} |"
        )
    for index, (record, score) in enumerate(zip(records, scores, strict=True), start=1):
        case = record["case"]
        body = record.get("response")
        body = body if isinstance(body, dict) else {}
        plan = body.get("plan")
        contract_ok = score["checks"].get("response_contract", {}).get("passed") is True
        plan = plan if contract_ok and record.get("http_status") == 200 and isinstance(plan, dict) else {}
        error = body.get("error")
        error = error if isinstance(error, dict) else {}
        reply = body.get("reply") or error.get("message")
        reply = reply if isinstance(reply, str) else "표시할 답변이 없습니다. runs.jsonl에서 원문을 확인해 주세요."
        lines.extend([
            "", "─" * 40, "", f"## {index}. {case['case_id']} / {record['run_number']}회차", "",
            case["description"], "",
            f"- 생성 코드: {record.get('app_commit', 'unknown')} (미커밋 변경: {record.get('worktree_dirty')})",
            f"- 모델: {record.get('model', 'unknown')} / 기준일: {record.get('reference_date')}",
            "", "**최종 답변**", "", reply,
            "", "**검사 실패·내용 검토 항목**", "",
        ])
        reasons = [
            reason for check in score["checks"].values() for reason in check["reasons"]
        ] + score["evaluation_errors"] + score["review_reasons"]
        lines.extend("- " + _md(reason) for reason in dict.fromkeys(reasons))
        if not reasons:
            lines.append("자동 검사에서 발견한 문제와 지정된 내용 검토 항목이 없습니다.")
        for title, values in (("계획 확인사항", plan.get("assumptions", [])),
                              ("서버 경고", body.get("warnings", []))):
            if isinstance(values, list) and values:
                lines.extend(["", f"**{title}**", ""])
                lines.extend("- " + _md(value) for value in values)
        if plan:
            lines.extend(["", "| 일차 | 시간 | 유형 | 장소·이동 | 분 |",
                          "|---|---|---|---|---|"])
            for day in plan["days"]:
                for item in day["items"]:
                    lines.append(
                        f"| {day['day']} | {_md(item['start_time'])}~{_md(item['end_time'])} | "
                        f"{item['item_type']} | {_md(item['title'])} | {item['duration_minutes']} |"
                    )
    return "\n".join(lines) + "\n"


def write_reports(folder, records, scores, settings):
    rows = summary_rows(records, scores)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    (folder / "summary.csv").write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")
    (folder / "report.md").write_text(build_report(records, scores, settings), encoding="utf-8")


def process_record(folder, record, records, scores, settings):
    append_jsonl(folder / "runs.jsonl", record)
    score = score_record(record)
    append_jsonl(folder / "scores.jsonl", {
        "case_id": record["case"]["case_id"], "run_number": record["run_number"],
        "evaluation": score,
    })
    records.append(record)
    scores.append(score)
    write_reports(folder, records, scores, settings)
    print(
        f"{record['case']['case_id']} {record['run_number']}회차: "
        f"{score['outcome']}, 자동 통과={score['automatic_passed']}"
    )


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="최종 /chat 응답을 평가합니다. 기본은 사례 검증입니다.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true", help="실제 Agent·이동 API를 호출합니다.")
    modes.add_argument("--rescore", type=Path, help="저장된 실행을 외부 API 호출 없이 새 폴더에 재채점합니다.")
    parser.add_argument("--cases", type=Path, help="평가 사례 JSON 파일")
    parser.add_argument("--case", help="하나만 선택할 사례 ID")
    parser.add_argument("--repeat", type=int, default=1, help="실제 실행의 사례별 반복 수, 기본 1")
    parser.add_argument("--label", default="api", help="결과 폴더 이름표")
    parser.add_argument("--reference-date", type=date.fromisoformat, help="상대 날짜 해석 기준일 YYYY-MM-DD")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat는 1 이상이어야 합니다.")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.label) is None:
        parser.error("--label은 영문·숫자·밑줄·하이픈 1~64자로 작성해 주세요.")
    if args.rescore and (args.cases is not None or args.reference_date is not None or args.repeat != 1):
        parser.error("--rescore에는 --cases, --reference-date, --repeat를 함께 사용하지 않습니다.")
    return args


def main(argv=None):
    args = parse_arguments(argv)
    try:
        saved = load_records(args.rescore, args.case) if args.rescore else None
        cases = [] if saved is not None else load_cases(args.cases or CASES_FILE, args.case)
        if not args.run and saved is None:
            print(f"평가 사례 {len(cases)}개 검증 통과. 실제 API 호출과 결과 파일 생성은 하지 않았습니다.")
            for case in cases:
                print(f"{case['case_id']}: {case['expectation']['outcome']} / {case['description']}")
            return 0
        reference_date = (
            args.reference_date or datetime.now(KST).date()
        ) if saved is None else None
        folder = create_output_folder(args.label)
        git_state = read_git_state()
        settings = {
            "schema_version": SCHEMA_VERSION, "mode": "rescore" if saved is not None else "run",
            "scoring_commit": git_state["commit"], "scoring_worktree_dirty": git_state["dirty"],
            "started_at": datetime.now(KST).isoformat(),
            "planned_runs": len(saved) if saved is not None else len(cases) * args.repeat,
            "reference_date": reference_date.isoformat() if reference_date else None,
            "source_folder": str(args.rescore) if args.rescore else None,
        }
        (folder / "metadata.json").write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        records, scores = [], []
        if saved is not None:
            for record in saved:
                process_record(folder, record, records, scores, settings)
        else:
            from backend.app.agent.chat_planner import run_chat_agent

            for case in cases:
                for number in range(1, args.repeat + 1):
                    started_at = perf_counter()
                    try:
                        record = run_chat_request(
                            case["request"], agent_runner=run_chat_agent,
                            reference_date=reference_date,
                        )
                    except Exception as error:
                        record = {
                            "request": deepcopy(case["request"]), "http_status": None,
                            "response": None, "attempts": [], "execution_error": type(error).__name__,
                            "reference_date": reference_date.isoformat(),
                            "elapsed_seconds": round(perf_counter() - started_at, 3),
                        }
                    record.update({
                        "schema_version": SCHEMA_VERSION, "case": deepcopy(case), "run_number": number,
                        "model": LLM_MODEL, "reasoning_effort": LLM_REASONING_EFFORT,
                        "app_commit": settings["scoring_commit"], "worktree_dirty": settings["scoring_worktree_dirty"],
                    })
                    process_record(folder, record, records, scores, settings)
        print(f"결과 저장: {folder}")
        return 0 if all(score["automatic_passed"] for score in scores) else 1
    except (OSError, ValueError) as error:
        print(f"평가를 진행하지 못했습니다: {error}")
        return 2
    except KeyboardInterrupt:
        print("실행을 중단했습니다. 저장된 runs.jsonl은 재채점할 수 있습니다.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())