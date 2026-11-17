"""연속 /chat 평가 CLI. 기본은 정의 검사이며 --run에서만 외부 API를 호출합니다."""
import argparse
from copy import deepcopy
import csv
from datetime import date, datetime
import io
import json
from pathlib import Path
import re
from time import perf_counter

from backend.app.config import LLM_MODEL, LLM_REASONING_EFFORT
from scripts.chat_multiturn_evaluation import evaluate_chat_turn
from scripts.chat_multiturn_runner import resolve_turn_case, run_chat_scenario, validate_scenario
from scripts.evaluate_chat_api import (
    KST, append_jsonl, build_report, read_git_state, summary_rows,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = PROJECT_ROOT / "scripts/eval_cases/chat_multiturn_cases.json"
OUTPUT_ROOT = PROJECT_ROOT / "outputs/chat_eval/multiturn"
KIND = "chat_multiturn"
SCHEMA_VERSION = 1
_GEN_FIELDS = {
    "model", "reasoning_effort", "app_commit", "worktree_dirty",
    "reference_date", "generated_at",
}
_RAW_FIELDS = {"turn_id", "case", "record", "lodging_record", "preparation_error"}


def _key(plan, turn_id):
    return plan["scenario"]["case_id"], plan["run_number"], turn_id


def load_scenarios(path, selected=None):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise ValueError("시나리오는 비어 있지 않은 목록이어야 합니다.")
    cases = [validate_scenario(item) for item in data]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("시나리오 ID가 중복됐습니다.")
    if selected is not None:
        cases = [case for case in cases if case["case_id"] == selected]
        if not cases:
            raise ValueError("선택한 시나리오를 찾지 못했습니다.")
    return cases


def load_saved(folder, selected=None):
    folder = Path(folder)
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8-sig"))
    if (
        not isinstance(metadata, dict) or metadata.get("kind") != KIND
        or type(metadata.get("schema_version")) is not int
        or metadata["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("연속 대화 평가 형식의 metadata.json이 아닙니다.")
    generation = metadata.get("generation")
    if (
        not isinstance(generation, dict) or set(generation) != _GEN_FIELDS
        or any(not isinstance(generation[key], str) or not generation[key] for key in _GEN_FIELDS - {"worktree_dirty"})
        or generation["worktree_dirty"] is not None and type(generation["worktree_dirty"]) is not bool
        or date.fromisoformat(generation["reference_date"]).isoformat() != generation["reference_date"]
        or datetime.fromisoformat(generation["generated_at"]).tzinfo is None
    ):
        raise ValueError("생성 설정 기록의 형식을 확인해 주세요.")
    plans = metadata.get("run_plan")
    if not isinstance(plans, list) or not plans:
        raise ValueError("시나리오 실행 계획이 없습니다.")
    by_run = {}
    for plan in plans:
        if (
            not isinstance(plan, dict) or set(plan) != {"scenario", "run_number"}
            or type(plan.get("run_number")) is not int or plan["run_number"] < 1
        ):
            raise ValueError("시나리오 실행 계획의 형식이 잘못됐습니다.")
        scenario = validate_scenario(plan["scenario"])
        if scenario != plan["scenario"]:
            raise ValueError("저장된 시나리오 정의가 정규 형식과 다릅니다.")
        key = (scenario["case_id"], plan["run_number"])
        if key in by_run:
            raise ValueError("시나리오와 회차가 중복됐습니다.")
        by_run[key] = plan
    counts = {key: 0 for key in by_run}
    records = []
    for line in (folder / "runs.jsonl").read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        json.dumps(row, allow_nan=False)
        required = _GEN_FIELDS | {"schema_version", "kind", "scenario", "run_number", "turn"}
        if (
            not isinstance(row, dict) or set(row) != required or row.get("kind") != KIND
            or type(row.get("schema_version")) is not int or row["schema_version"] != SCHEMA_VERSION
            or type(row.get("run_number")) is not int
            or any(row.get(key) != generation[key] for key in _GEN_FIELDS)
        ):
            raise ValueError("대화 실행 기록의 형식 또는 생성 설정이 다릅니다.")
        scenario = row.get("scenario")
        if not isinstance(scenario, dict) or not isinstance(scenario.get("case_id"), str):
            raise ValueError("원본 시나리오 ID의 형식이 잘못됐습니다.")
        key = (scenario["case_id"], row["run_number"])
        plan = by_run.get(key)
        raw = row["turn"]
        if (
            plan is None or scenario != plan["scenario"]
            or not isinstance(raw, dict) or set(raw) != _RAW_FIELDS
            or counts[key] >= len(scenario["turns"])
            or raw["turn_id"] != scenario["turns"][counts[key]]["turn_id"]
            or any(raw[field] is not None and not isinstance(raw[field], dict)
                   for field in ("case", "record", "lodging_record", "preparation_error"))
        ):
            raise ValueError("시나리오·회차·단계 순서 또는 원본 기록을 확인해 주세요.")
        counts[key] += 1
        records.append(row)
    if selected is not None:
        plans = [plan for plan in plans if plan["scenario"]["case_id"] == selected]
        if not plans:
            raise ValueError("선택한 시나리오가 원본 계획에 없습니다.")
        records = [row for row in records if row["scenario"]["case_id"] == selected]
    return deepcopy(plans), deepcopy(records), deepcopy(generation)


def _failure(outcome, reason):
    return {
        "evaluation_status": "error", "evaluation_errors": [reason],
        "outcome": outcome, "automatic_passed": False,
        "requires_manual_review": False, "review_reasons": [],
        "checks": {}, "attempt_count": None,
    }


def evaluate_records(plans, records, cached=None):
    """재채점은 저장된 기록을 재구성하며 실행 함수를 호출하지 않습니다."""
    rows = {_key(row, row["turn"]["turn_id"]): row for row in records}
    if len(rows) != len(records):
        raise ValueError("같은 단계의 원본 기록이 중복됐습니다.")
    scores = {}
    for plan in plans:
        previous = None
        for index, turn in enumerate(plan["scenario"]["turns"]):
            key = _key(plan, turn["turn_id"])
            row = rows.get(key)
            raw = row["turn"] if row else None
            if raw is None:
                score = _failure("not_recorded", "이 단계의 실행 기록이 없습니다.")
            elif isinstance(raw.get("record"), dict) and raw["record"].get("execution_error"):
                failure = raw["record"]["execution_error"]
                score = _failure("execution_error", "요청 수집 오류 유형: " + (failure if isinstance(failure, str) else type(failure).__name__))
            elif raw["preparation_error"] is not None:
                error = raw["preparation_error"]
                score = _failure(
                    "preparation_error",
                    f"요청 준비 실패: {error.get('stage')} / {error.get('error_type')}",
                )
            else:
                try:
                    case = resolve_turn_case(
                        plan["scenario"], index, previous_record=previous,
                        lodging_record=raw["lodging_record"],
                    )
                    entry = cached.get(key) if cached is not None else None
                    if entry is not None and entry["scoring_error"] is not None:
                        score = _failure("evaluation_error", "채점 오류: " + entry["scoring_error"])
                    else:
                        score = deepcopy(entry["score"]) if entry is not None else evaluate_chat_turn(
                            case, raw["record"], previous_record=previous,
                        )
                        same = raw["case"] == case
                        score["checks"]["scenario_contract"] = {
                            "passed": same,
                            "reasons": [] if same else ["저장된 단계 정의가 시나리오와 이전 결과로 재구성한 요청과 다릅니다."],
                        }
                        score["automatic_passed"] = score["automatic_passed"] and same
                        if score["automatic_passed"]:
                            reasons = score["review_reasons"] + case.get("review_points", [])
                            score["review_reasons"] = list(dict.fromkeys(reasons))
                            score["requires_manual_review"] = bool(reasons)
                        else:
                            score["review_reasons"] = []
                            score["requires_manual_review"] = False
                except Exception as error:
                    score = _failure("evaluation_error", "재구성·채점 오류: " + type(error).__name__)
            scores[key] = score
            previous = deepcopy(raw["record"]) if raw is not None else None
    return scores


def _display_data(plans, records, scores, generation):
    rows = {_key(row, row["turn"]["turn_id"]): row for row in records}
    displays, evaluations, contexts = [], [], []
    for plan in plans:
        for turn in plan["scenario"]["turns"]:
            key = _key(plan, turn["turn_id"])
            raw = rows[key]["turn"] if key in rows else None
            record = deepcopy(raw["record"]) if raw and isinstance(raw["record"], dict) else {}
            case = {
                "case_id": plan["scenario"]["case_id"] + "-" + turn["turn_id"],
                "description": turn["description"],
            }
            message = record.get("request", {}).get("message") if isinstance(record.get("request"), dict) else None
            if isinstance(message, str):
                case["description"] += "\n\n**사용자 요청**\n\n" + message
            lookup = raw.get("lodging_record") if raw else None
            if isinstance(lookup, dict):
                case["description"] += f"\n\n숙소 검색 HTTP 상태: {lookup.get('http_status')}."
            record.update(case=case, run_number=plan["run_number"], **generation)
            displays.append(record)
            evaluations.append(scores[key])
            contexts.append((plan, turn, raw, lookup))
    return displays, evaluations, contexts


def write_results(folder, plans, records, scores, settings):
    displays, evaluations, contexts = _display_data(plans, records, scores, settings["generation"])
    rows = summary_rows(displays, evaluations)
    passed = {
        (plan["scenario"]["case_id"], plan["run_number"]): all(
            scores[_key(plan, turn["turn_id"])]["automatic_passed"] for turn in plan["scenario"]["turns"]
        ) for plan in plans
    }
    for row, (plan, turn, raw, lookup) in zip(rows, contexts, strict=True):
        row.update(
            scenario_id=plan["scenario"]["case_id"], turn_id=turn["turn_id"],
            scenario_passed=passed[(plan["scenario"]["case_id"], plan["run_number"])],
            record_status="not_recorded" if raw is None else "preparation_error" if raw["preparation_error"] else "recorded",
            lodging_http_status=lookup.get("http_status") if isinstance(lookup, dict) else None,
            lodging_elapsed_seconds=lookup.get("elapsed_seconds") if isinstance(lookup, dict) else None,
        )
        if raw is None or raw["record"] is None:
            row.update(recorded_model_calls=None, recorded_tool_calls=None)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    (folder / "summary.csv").write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")
    score_lines = [
        json.dumps({
            "scenario_id": key[0], "run_number": key[1], "turn_id": key[2], "evaluation": score,
        }, ensure_ascii=False, allow_nan=False)
        for key, score in scores.items()
    ]
    (folder / "scores.jsonl").write_text("\n".join(score_lines) + "\n", encoding="utf-8")
    details = [(record, score) for record, score, context in zip(displays, evaluations, contexts, strict=True) if context[2] is not None]
    report = build_report(
        [item[0] for item in details], [item[1] for item in details],
        {**settings, "planned_runs": len(displays)},
    ).removeprefix("# 최종 /chat 평가 결과\n\n")
    header = [
        "# 연속 대화 /chat 평가 결과", "",
        "기록 없는 단계는 not_recorded로 남기며 자동 통과로 집계하지 않습니다.",
        "호출 수는 수집된 기록이며 실제 호출·과금이 없었다는 증거로 사용하지 않습니다.",
        "아래 처리 수는 기록 단계 기준이며 준비 실패는 /chat을 호출한 결과가 아닙니다.", "",
        "| 시나리오·회차 | 기록/계획 단계 | 자동 통과 | 기록 없는 단계 |",
        "|---|---|---|---|",
    ]
    for plan in plans:
        missing = [turn["turn_id"] for turn in plan["scenario"]["turns"] if _key(plan, turn["turn_id"]) not in {
            _key(row, row["turn"]["turn_id"]) for row in records
        }]
        header.append(
            f"| {plan['scenario']['case_id']} / {plan['run_number']} | "
            f"{len(plan['scenario']['turns']) - len(missing)}/{len(plan['scenario']['turns'])} | "
            f"{passed[(plan['scenario']['case_id'], plan['run_number'])]} | {', '.join(missing)} |"
        )
    (folder / "report.md").write_text("\n".join(header) + "\n\n" + report, encoding="utf-8")


def run_lodging_request(query):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from scripts.chat_evaluation_runner import _CAPTURE_LOCK

    saved = deepcopy(query)
    started = perf_counter()
    with _CAPTURE_LOCK, TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/lodgings/nearby", params=deepcopy(saved))
        body = response.json()
    return {
        "request": saved, "http_status": response.status_code, "response": body,
        "elapsed_seconds": round(perf_counter() - started, 3),
    }


def create_output_folder(label):
    folder = OUTPUT_ROOT / (datetime.now(KST).strftime("%Y%m%d-%H%M%S-%f") + "_" + label)
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="연속 대화 /chat 평가. 기본은 시나리오 검사입니다.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true", help="실제 Agent·교통·숙소 API를 호출합니다.")
    modes.add_argument("--rescore", type=Path, help="저장된 실행을 외부 호출 없이 새 폴더에 재채점합니다.")
    parser.add_argument("--cases", type=Path, help="시나리오 JSON 파일")
    parser.add_argument("--case", help="시나리오 ID 하나를 선택합니다.")
    parser.add_argument("--repeat", type=int, default=1, help="시나리오별 반복 수")
    parser.add_argument("--label", default="multiturn", help="결과 폴더 이름표")
    parser.add_argument("--reference-date", type=date.fromisoformat, help="날짜 해석 기준일 YYYY-MM-DD")
    args = parser.parse_args(argv)
    if args.repeat < 1 or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.label) is None:
        parser.error("반복 수는 1 이상, 이름표는 영문·숫자·밑줄·하이픈 1~64자여야 합니다.")
    if args.rescore and (args.cases is not None or args.reference_date is not None or args.repeat != 1):
        parser.error("--rescore에는 --cases, --reference-date, --repeat를 함께 사용하지 않습니다.")
    return args


def main(argv=None):
    args = parse_arguments(argv)
    folder = None
    try:
        if args.rescore:
            plans, records, generation = load_saved(args.rescore, args.case)
        else:
            cases = load_scenarios(args.cases or CASES_FILE, args.case)
            if not args.run:
                print(f"시나리오 {len(cases)}개 검증 통과. API 호출·Git 조회·결과 파일 생성은 하지 않았습니다.")
                for case in cases:
                    print(f"{case['case_id']}: {len(case['turns'])}단계 / {case['description']}")
                return 0
            plans = [{"scenario": case, "run_number": number}
                     for case in cases for number in range(1, args.repeat + 1)]
            records = []
        git = read_git_state()
        started = datetime.now(KST).isoformat()
        if not args.rescore:
            reference = args.reference_date or datetime.now(KST).date()
            generation = {
                "model": LLM_MODEL, "reasoning_effort": LLM_REASONING_EFFORT,
                "app_commit": git["commit"], "worktree_dirty": git["dirty"],
                "reference_date": reference.isoformat(), "generated_at": started,
            }
        settings = {
            "schema_version": SCHEMA_VERSION, "kind": KIND,
            "mode": "rescore" if args.rescore else "run", "started_at": started,
            "source_folder": str(args.rescore) if args.rescore else None,
            "scoring_commit": git["commit"], "scoring_worktree_dirty": git["dirty"],
            "generation": generation, "run_plan": plans,
        }
        folder = create_output_folder(args.label)
        (folder / "metadata.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "runs.jsonl").write_text("", encoding="utf-8")
        if args.rescore:
            for row in records:
                append_jsonl(folder / "runs.jsonl", row)
            scores = evaluate_records(plans, records)
            write_results(folder, plans, records, scores, settings)
        else:
            from backend.app.agent.chat_planner import run_chat_agent

            cached = {}
            for plan in plans:
                context = {"schema_version": SCHEMA_VERSION, "kind": KIND, **deepcopy(plan), **generation}
                def capture(raw, context=context):
                    row = {**deepcopy(context), "turn": deepcopy(raw)}
                    append_jsonl(folder / "runs.jsonl", row)
                    records.append(row)
                result = run_chat_scenario(
                    plan["scenario"], agent_runner=run_chat_agent,
                    reference_date=date.fromisoformat(generation["reference_date"]),
                    lodging_runner=run_lodging_request, on_record=capture,
                )
                for entry in result["turns"]:
                    cached[_key(plan, entry["raw"]["turn_id"])] = entry
                scores = evaluate_records(plans, records, cached)
                write_results(folder, plans, records, scores, settings)
                print(f"{plan['scenario']['case_id']} {plan['run_number']}회차: 자동 통과={result['automatic_passed']}")
        print(f"결과 저장: {folder}")
        return 0 if all(score["automatic_passed"] for score in scores.values()) else 1
    except (OSError, ValueError) as error:
        print(f"평가 준비·파일 처리 오류: {type(error).__name__}")
        return 2
    except KeyboardInterrupt:
        print(f"실행을 중단했습니다. 저장된 원본 위치: {folder}")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
