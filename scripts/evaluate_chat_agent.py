"""대화 Agent(/chat) 평가 세트를 실행하고 결과를 outputs/chat_eval/ 아래에 저장합니다.

실행: python -B -m scripts.evaluate_chat_agent --label baseline
재채점: python -B -m scripts.evaluate_chat_agent --rescore outputs/chat_eval/<폴더>
  (저장된 runs.jsonl을 지금 채점 함수로 다시 채점합니다. AI를 다시 부르지 않습니다.)
"""
import argparse
import csv
import json
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

from backend.app.agent.chat_planner import run_chat_agent
from backend.app.config import LLM_MODEL, LLM_REASONING_EFFORT
from backend.app.routers.chat import ChatAgentReply, ChatRequest, build_chat_messages
from backend.app.services.plan_evaluation import (
    CHECK_LABELS,
    calculate_process_metrics,
    evaluate_run,
    extract_tool_trace,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = PROJECT_ROOT / "scripts" / "eval_cases" / "chat_cases.json"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "chat_eval"

# "내일" 같은 말을 매번 같은 날짜로 해석하도록 기준 날짜를 고정합니다.
REFERENCE_DATE = date(2026, 11, 11)
RUBRIC_IDS = ["R1", "R2", "R3", "R4", "R5", "R6"]

def parse_arguments():
    parser = argparse.ArgumentParser(description="대화 Agent 평가 세트를 실행합니다.")
    parser.add_argument("--label", default=None, help="결과 폴더 이름표, 예: baseline")
    parser.add_argument("--case", default=None, help="하나만 실행할 요청 ID, 예: C01")
    parser.add_argument("--repeat", type=int, default=3, help="요청당 실행 횟수 (기본 3)")
    parser.add_argument("--rescore", default=None, help="다시 채점할 결과 폴더")
    arguments = parser.parse_args()
    if arguments.rescore is None and arguments.label is None:
        parser.error("--label 또는 --rescore 중 하나가 필요합니다.")
    return arguments

def load_cases(case_id: str | None) -> list[dict]:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if case_id is None:
        return cases

    selected_cases = []
    for case in cases:
        if case["case_id"] == case_id:
            selected_cases.append(case)
    if len(selected_cases) == 0:
        raise SystemExit(f"요청 ID {case_id}가 {CASES_FILE.name}에 없습니다.")
    return selected_cases

def create_output_folder(label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    output_folder = OUTPUT_ROOT / f"{timestamp}_{label}"
    output_folder.mkdir(parents=True, exist_ok=False)
    return output_folder

def read_commit_hash() -> str:
    commit_hash = "unknown"
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return commit_hash
    if completed.returncode == 0:
        commit_hash = completed.stdout.strip()
    return commit_hash

def summarize_route_calls(route_calls: list[dict]) -> list[dict]:
    """지도용 경로 좌표(paths)는 크기가 커서 빼고 저장합니다."""
    route_summaries = []
    for route_call in route_calls:
        route_summaries.append({
            "route_id": route_call["route_id"],
            "transport": route_call["transport"],
            "distance_m": route_call["distance_m"],
            "duration_seconds": route_call["duration_seconds"],
        })
    return route_summaries

def run_case_once(case: dict, run_number: int) -> dict:
    run_record = {
        "case_id": case["case_id"],
        "run_number": run_number,
        "description": case["description"],
        "expect_plan": case["expect_plan"],
        "time_window": case.get("time_window"),
        "request": case["request"],
        "status": "ok",
        "error": None,
        "elapsed_seconds": None,
        "chat_reply": None,
        "tool_trace": [],
        "route_calls": [],
        "evaluation": None,
        "process": None,
    }

    chat_request = ChatRequest.model_validate(case["request"])
    messages = build_chat_messages(chat_request, today=REFERENCE_DATE)

    started_at = time.perf_counter()
    try:
        agent_result = run_chat_agent(messages, ChatAgentReply)
    except Exception as error:
        run_record["status"] = "error"
        run_record["error"] = f"{type(error).__name__}: {error}"
        run_record["elapsed_seconds"] = round(time.perf_counter() - started_at, 1)
        return run_record
    run_record["elapsed_seconds"] = round(time.perf_counter() - started_at, 1)

    chat_reply = None
    if agent_result["chat_reply"] is not None:
        chat_reply = agent_result["chat_reply"].model_dump(mode="json")

    tool_trace = extract_tool_trace(agent_result["messages"])
    route_summaries = summarize_route_calls(agent_result["route_calls"])

    run_record["chat_reply"] = chat_reply
    run_record["tool_trace"] = tool_trace
    run_record["route_calls"] = route_summaries
    run_record["evaluation"] = evaluate_run(case, chat_reply, tool_trace, route_summaries)
    run_record["process"] = calculate_process_metrics(agent_result["messages"], tool_trace)
    return run_record

def append_run_record(runs_file: Path, run_record: dict):
    with runs_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(run_record, ensure_ascii=False) + "\n")

def describe_check(check_result: dict | None) -> str:
    if check_result is None:
        return "-"
    if check_result["passed"] is None:
        return "해당 없음"
    if check_result["passed"]:
        return "통과"
    return "실패"

def is_run_passed(run_record: dict) -> bool:
    if run_record["status"] != "ok":
        return False
    return run_record["evaluation"]["passed"]

def build_summary_row(run_record: dict, rubric_scores: dict) -> dict:
    row = {
        "case_id": run_record["case_id"],
        "run_number": run_record["run_number"],
        "status": run_record["status"],
        "passed": "N",
        "plan_delivered": "",
    }
    if is_run_passed(run_record):
        row["passed"] = "Y"

    failed_reasons = []
    evaluation = run_record["evaluation"]
    for check_id in CHECK_LABELS:
        check_result = None
        if evaluation is not None:
            check_result = evaluation["checks"][check_id]
        row[check_id] = describe_check(check_result)
        if check_result is not None and check_result["passed"] is False:
            for reason in check_result["reasons"]:
                failed_reasons.append(f"{check_id} {reason}")
    if evaluation is not None:
        row["plan_delivered"] = "N"
        if evaluation["plan_delivered"]:
            row["plan_delivered"] = "Y"
    if run_record["error"] is not None:
        failed_reasons.append(run_record["error"])
    row["failed_reasons"] = " | ".join(failed_reasons)

    process = run_record["process"]
    process_columns = [
        "model_call_count",
        "tool_call_count",
        "repeated_tool_call_count",
        "input_tokens",
        "output_tokens",
    ]
    for column_name in process_columns:
        row[column_name] = ""
        if process is not None:
            row[column_name] = process[column_name]
    row["elapsed_seconds"] = run_record["elapsed_seconds"]

    row["day_count"] = ""
    row["thin_day_count"] = ""
    if evaluation is not None:
        row["day_count"] = evaluation["visit_metrics"]["day_count"]
        row["thin_day_count"] = evaluation["visit_metrics"]["thin_day_count"]

    run_key = (str(run_record["case_id"]), str(run_record["run_number"]))
    saved_scores = rubric_scores.get(run_key, {})
    for rubric_id in RUBRIC_IDS:
        row[rubric_id] = saved_scores.get(rubric_id, "")
    return row

def read_rubric_scores(summary_file: Path) -> dict:
    """이미 채운 R1~R6 채점을 (요청 ID, 회차)별로 읽습니다. 파일이 없으면 빈 값입니다."""
    rubric_scores = {}
    if not summary_file.exists():
        return rubric_scores

    with summary_file.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            saved_scores = {}
            for rubric_id in RUBRIC_IDS:
                saved_scores[rubric_id] = row.get(rubric_id, "")
            rubric_scores[(row["case_id"], row["run_number"])] = saved_scores
    return rubric_scores

def write_summary_csv(summary_file: Path, run_records: list[dict]):
    rubric_scores = read_rubric_scores(summary_file)
    rows = []
    for run_record in run_records:
        rows.append(build_summary_row(run_record, rubric_scores))

    # 엑셀에서 한글이 깨지지 않도록 utf-8-sig로 저장합니다.
    with summary_file.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

def calculate_percent(count: int, total: int) -> str:
    if total == 0:
        return "-"
    percent = count / total * 100
    return f"{percent:.0f}%"

def calculate_average(values: list[float]) -> str:
    if len(values) == 0:
        return "-"
    average = sum(values) / len(values)
    return f"{average:.1f}"

def build_report(run_records: list[dict], settings: dict) -> str:
    lines = [
        f"# 대화 Agent 평가 결과: {settings['label']}",
        "",
        f"- 실행 시각: {settings['started_at']}",
        f"- 커밋: `{settings['commit_hash']}`",
        f"- 모델: `{LLM_MODEL}` (생각 강도 {LLM_REASONING_EFFORT})",
        f"- 기준 날짜: {REFERENCE_DATE.isoformat()}",
        f"- 요청 {settings['case_count']}개 × {settings['repeat']}번",
        "",
    ]

    run_count = len(run_records)
    error_count = 0
    passed_count = 0
    for run_record in run_records:
        if run_record["status"] != "ok":
            error_count += 1
        if is_run_passed(run_record):
            passed_count += 1

    day_count = 0
    thin_day_count = 0
    for run_record in run_records:
        if run_record["status"] != "ok":
            continue
        day_count += run_record["evaluation"]["visit_metrics"]["day_count"]
        thin_day_count += run_record["evaluation"]["visit_metrics"]["thin_day_count"]

    case_ids = []
    for run_record in run_records:
        if run_record["case_id"] not in case_ids:
            case_ids.append(run_record["case_id"])

    case_rows = []
    all_passed_case_count = 0
    for case_id in case_ids:
        case_run_count = 0
        case_passed_count = 0
        failed_check_ids = []
        for run_record in run_records:
            if run_record["case_id"] != case_id:
                continue
            case_run_count += 1
            if is_run_passed(run_record):
                case_passed_count += 1
            if run_record["status"] != "ok":
                if "오류" not in failed_check_ids:
                    failed_check_ids.append("오류")
                continue
            for check_id, check_result in run_record["evaluation"]["checks"].items():
                if check_result["passed"] is False and check_id not in failed_check_ids:
                    failed_check_ids.append(check_id)
        if case_passed_count == case_run_count:
            all_passed_case_count += 1
        failed_text = "-"
        if len(failed_check_ids) > 0:
            failed_text = ", ".join(failed_check_ids)
        case_rows.append(f"| {case_id} | {case_passed_count} / {case_run_count} | {failed_text} |")

    lines.extend([
        "## 전체",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 실행 수 | {run_count} |",
        f"| 실행 오류 | {error_count} |",
        f"| 필수 조건 모두 통과 | {passed_count} / {run_count} ({calculate_percent(passed_count, run_count)}) |",
        f"| 모든 회차를 통과한 요청 | {all_passed_case_count} / {len(case_ids)} |",
        f"| 하루 방문 1곳 이하인 일차 (기록용) | {thin_day_count} / {day_count} |",
        "",
        "## 필수 조건별 위반",
        "| ID | 항목 | 위반 | 해당 실행 |",
        "| --- | --- | --- | --- |",
    ])

    for check_id, check_label in CHECK_LABELS.items():
        failed_count = 0
        applicable_count = 0
        for run_record in run_records:
            if run_record["status"] != "ok":
                continue
            check_result = run_record["evaluation"]["checks"][check_id]
            if check_result["passed"] is None:
                continue
            applicable_count += 1
            if check_result["passed"] is False:
                failed_count += 1
        lines.append(f"| {check_id} | {check_label} | {failed_count} | {applicable_count} |")

    lines.extend([
        "",
        "## 요청별",
        "| 요청 | 통과 | 위반 항목 |",
        "| --- | --- | --- |",
    ])
    lines.extend(case_rows)

    model_call_counts = []
    tool_call_counts = []
    repeated_tool_call_counts = []
    input_token_counts = []
    output_token_counts = []
    elapsed_seconds_values = []
    for run_record in run_records:
        if run_record["status"] != "ok":
            continue
        process = run_record["process"]
        model_call_counts.append(process["model_call_count"])
        tool_call_counts.append(process["tool_call_count"])
        repeated_tool_call_counts.append(process["repeated_tool_call_count"])
        input_token_counts.append(process["input_tokens"])
        output_token_counts.append(process["output_tokens"])
        elapsed_seconds_values.append(run_record["elapsed_seconds"])

    lines.extend([
        "",
        "## 과정 지표 (오류 없는 실행의 평균)",
        "| 모델 호출 | 도구 호출 | 같은 입력 재호출 | 입력 토큰 | 출력 토큰 | 걸린 시간(초) |",
        "| --- | --- | --- | --- | --- | --- |",
        (
            f"| {calculate_average(model_call_counts)} | {calculate_average(tool_call_counts)} "
            f"| {calculate_average(repeated_tool_call_counts)} | {calculate_average(input_token_counts)} "
            f"| {calculate_average(output_token_counts)} | {calculate_average(elapsed_seconds_values)} |"
        ),
    ])

    coverage_totals = {}
    for run_record in run_records:
        if run_record["status"] != "ok":
            continue
        for requirement, counts in run_record["evaluation"]["requirement_coverage"].items():
            if requirement not in coverage_totals:
                coverage_totals[requirement] = {"visit_count": 0, "evidence_count": 0}
            coverage_totals[requirement]["visit_count"] += counts["visit_count"]
            coverage_totals[requirement]["evidence_count"] += counts["evidence_count"]

    lines.extend([
        "",
        "## 데이터 충족도 (필수 시설을 DB로 확인할 수 있는 방문 장소)",
        "| 필수 시설 | 방문 장소 | DB 근거 있음 | 비율 |",
        "| --- | --- | --- | --- |",
    ])
    if len(coverage_totals) == 0:
        lines.append("| - | - | - | - |")
    for requirement, counts in coverage_totals.items():
        percent_text = calculate_percent(counts["evidence_count"], counts["visit_count"])
        lines.append(
            f"| {requirement} | {counts['visit_count']} | {counts['evidence_count']} | {percent_text} |"
        )

    lines.extend([
        "",
        "## 품질 채점표",
        "`summary.csv`의 R1~R6 칸을 사람이 채운 뒤 집계합니다.",
        "",
    ])
    return "\n".join(lines)

def build_case_from_record(run_record: dict, cases_by_id: dict[str, dict]) -> dict:
    """기록에 저장된 요청을 쓰고, 기록에 없는 칸은 지금 요청 파일에서 채웁니다."""
    current_case = cases_by_id.get(run_record["case_id"], {})
    case = {
        "case_id": run_record["case_id"],
        "description": run_record["description"],
        "request": run_record["request"],
        "expect_plan": current_case.get("expect_plan"),
        "time_window": current_case.get("time_window"),
    }
    if "expect_plan" in run_record:
        case["expect_plan"] = run_record["expect_plan"]
    if "time_window" in run_record:
        case["time_window"] = run_record["time_window"]
    return case

def rescore_output_folder(output_folder: Path):
    runs_file = output_folder / "runs.jsonl"
    cases_by_id = {}
    for case in load_cases(None):
        cases_by_id[case["case_id"]] = case

    run_records = []
    for line in runs_file.read_text(encoding="utf-8").splitlines():
        run_record = json.loads(line)
        case = build_case_from_record(run_record, cases_by_id)
        run_record["expect_plan"] = case["expect_plan"]
        run_record["time_window"] = case["time_window"]
        if run_record["status"] == "ok":
            run_record["evaluation"] = evaluate_run(
                case,
                run_record["chat_reply"],
                run_record["tool_trace"],
                run_record["route_calls"],
            )
        run_records.append(run_record)

    with runs_file.open("w", encoding="utf-8") as file:
        for run_record in run_records:
            file.write(json.dumps(run_record, ensure_ascii=False) + "\n")

    case_ids = []
    for run_record in run_records:
        if run_record["case_id"] not in case_ids:
            case_ids.append(run_record["case_id"])
    settings = {
        "label": f"{output_folder.name} (재채점)",
        "started_at": f"재채점 {datetime.now().isoformat(timespec='seconds')}",
        "commit_hash": read_commit_hash(),
        "case_count": len(case_ids),
        "repeat": len(run_records) // len(case_ids),
    }
    write_summary_csv(output_folder / "summary.csv", run_records)
    report_text = build_report(run_records, settings)
    (output_folder / "report.md").write_text(report_text, encoding="utf-8")
    print(f"재채점 저장: {output_folder}")

def main():
    arguments = parse_arguments()
    if arguments.rescore is not None:
        rescore_output_folder(Path(arguments.rescore))
        return
    cases = load_cases(arguments.case)
    output_folder = create_output_folder(arguments.label)
    runs_file = output_folder / "runs.jsonl"

    settings = {
        "label": arguments.label,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "commit_hash": read_commit_hash(),
        "case_count": len(cases),
        "repeat": arguments.repeat,
    }

    run_records = []
    total_run_count = len(cases) * arguments.repeat
    current_run_count = 0
    for case in cases:
        for run_number in range(1, arguments.repeat + 1):
            current_run_count += 1
            print(f"[{current_run_count}/{total_run_count}] {case['case_id']} {run_number}회차 실행 중...")
            run_record = run_case_once(case, run_number)
            append_run_record(runs_file, run_record)
            run_records.append(run_record)
            print(
                f"    상태 {run_record['status']}, 통과 {is_run_passed(run_record)}, "
                f"{run_record['elapsed_seconds']}초"
            )

    write_summary_csv(output_folder / "summary.csv", run_records)
    report_text = build_report(run_records, settings)
    (output_folder / "report.md").write_text(report_text, encoding="utf-8")
    print(f"결과 저장: {output_folder}")

if __name__ == "__main__":
    main()
