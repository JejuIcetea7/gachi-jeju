"""연속 /chat 평가를 순서대로 실행합니다. 저장은 on_record 콜백에서 처리합니다."""
from copy import deepcopy
from datetime import date
import json
import re
from time import perf_counter

from backend.app.routers.chat import ChatOptions, ChatRequest
from backend.app.services.lodging_models import LodgingCoordinate, LodgingSearchResponse
from backend.app.services.lodging_planning import LodgingStay
from scripts.chat_evaluation_runner import run_chat_request
from scripts.chat_multiturn_evaluation import evaluate_chat_turn, validate_turn_case
from scripts.chat_multiturn_state import build_followup_request
from scripts.evaluate_chat_api import CASE_FIELDS


_META_FIELDS = (CASE_FIELDS - {"case_id", "request"}) | {"expected_visits", "preserve_visits"}
_COMMON_FIELDS = _META_FIELDS | {"turn_id"}
_SELECTION_MESSAGE = "선택한 숙소를 반영해 주세요."


def _snapshot(value):
    result = deepcopy(value)
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


def _case(scenario_id, turn, request, options_patch=None):
    return {
        **{key: deepcopy(value) for key, value in turn.items() if key in _META_FIELDS},
        "case_id": scenario_id + "-" + turn["turn_id"],
        "request": deepcopy(request),
        "options_patch": deepcopy(options_patch or {}),
    }


def validate_scenario(scenario):
    """모든 단계의 구조를 외부 호출 전에 검사합니다."""
    if not isinstance(scenario, dict) or set(scenario) != {"case_id", "description", "turns"}:
        raise ValueError("시나리오에는 case_id, description, turns가 필요합니다.")
    try:
        result = _snapshot(scenario)
    except (TypeError, ValueError) as error:
        raise ValueError("시나리오는 유효한 JSON이어야 합니다.") from error
    identifier = result["case_id"]
    if not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier) is None:
        raise ValueError("시나리오 ID의 형식을 확인해 주세요.")
    if not isinstance(result["description"], str) or not result["description"].strip():
        raise ValueError("시나리오 설명이 필요합니다.")
    turns = result["turns"]
    if not isinstance(turns, list) or not turns:
        raise ValueError("시나리오에는 하나 이상의 단계가 필요합니다.")
    seen = set()
    normalized = []
    options = {}
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise ValueError("대화 단계는 객체여야 합니다.")
        turn_id = turn.get("turn_id")
        if (
            not isinstance(turn_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", turn_id) is None
            or turn_id in seen
        ):
            raise ValueError("단계 ID의 형식 또는 중복을 확인해 주세요.")
        seen.add(turn_id)
        selection = "lodging_selection" in turn
        action_fields = (
            {"request"} if index == 0 else
            {"lodging_selection"} if selection else {"message", "options_patch"}
        )
        if set(turn) - _COMMON_FIELDS - action_fields:
            raise ValueError("첫 단계의 request와 후속 단계의 동작 필드를 구분해 주세요.")
        if index == 0:
            request = ChatRequest.model_validate(turn.get("request")).model_dump(mode="json")
            options = request["options"]
            probe = turn["request"]
        else:
            patch = {} if selection else turn.get("options_patch", {})
            if not isinstance(patch, dict):
                raise ValueError("선택 옵션 변경은 객체여야 합니다.")
            if selection:
                choice = turn["lodging_selection"]
                if (
                    not isinstance(choice, dict) or set(choice) != {"night", "candidate_index"}
                    or type(choice["night"]) is not int or choice["night"] < 1
                    or type(choice["candidate_index"]) is not int
                    or not 0 <= choice["candidate_index"] < 5
                ):
                    raise ValueError("숙박일과 0~4 범위의 숙소 후보 순번이 필요합니다.")
                if options.get("trip_days") is not None and choice["night"] >= options["trip_days"]:
                    raise ValueError("숙박일은 여행 마지막 날보다 앞서야 합니다.")
            probe = {
                "message": _SELECTION_MESSAGE if selection else turn.get("message"),
                "options": {**options, **patch},
            }
        validated = validate_turn_case(_case(
            identifier, turn, probe,
            {} if index == 0 or selection else turn.get("options_patch", {}),
        ))
        options = ChatOptions.model_validate({
            **probe.get("options", {}), **validated.get("expected_options", {}),
        }).model_dump(mode="json")
        item = {key: value for key, value in validated.items() if key in _META_FIELDS}
        item["turn_id"] = turn_id
        if index == 0:
            item["request"] = deepcopy(turn["request"])
        elif selection:
            item["lodging_selection"] = deepcopy(turn["lodging_selection"])
        else:
            item.update(message=turn["message"], options_patch=deepcopy(turn.get("options_patch", {})))
        normalized.append(item)
    result["turns"] = normalized
    return result


def prepare_lodging_query(previous_record, night):
    """화면처럼 해당 숙박일의 마지막 확인된 방문지만 검색 기준으로 사용합니다."""
    state = build_followup_request(previous_record, _SELECTION_MESSAGE)
    days = state["options"]["trip_days"]
    if type(night) is not int or days is None or not 1 <= night < days:
        raise ValueError("선택할 숙박일과 여행 기간을 확인해 주세요.")
    plan = state["current_plan"]
    matches = [day for day in plan["days"] if day["day"] == night] if plan else []
    visits = [item for item in matches[0]["items"] if item["item_type"] == "visit"] if len(matches) == 1 else []
    if not visits or visits[-1]["verified"] is not True:
        raise ValueError("숙박일의 마지막 방문지를 확인할 수 없습니다.")
    center = LodgingCoordinate.model_validate({
        key: visits[-1][key] for key in ("latitude", "longitude")
    }).model_dump(mode="json")
    return {**center, "radius_m": 5000, "max_count": 5}


def _lodging_request(previous_record, choice, lookup):
    query = prepare_lodging_query(previous_record, choice["night"])
    if (
        not isinstance(lookup, dict) or lookup.get("request") != query
        or type(lookup.get("http_status")) is not int or lookup["http_status"] != 200
    ):
        raise ValueError("숙소 검색 요청과 정상 HTTP 결과를 확인할 수 없습니다.")
    response = LodgingSearchResponse.model_validate(lookup.get("response"))
    if (
        response.center.model_dump() != {key: query[key] for key in ("latitude", "longitude")}
        or response.radius_m != query["radius_m"]
        or len(response.places) > query["max_count"]
        or choice["candidate_index"] >= len(response.places)
    ):
        raise ValueError("검색 위치·반경 또는 선택할 숙소 후보를 확인할 수 없습니다.")
    candidate = response.places[choice["candidate_index"]].model_dump(mode="json")
    stay = LodgingStay.model_validate({
        "night": choice["night"],
        **{key: candidate[key] for key in (
            "name", "address", "latitude", "longitude", "provider_place_id", "place_url",
        )},
    }).model_dump(mode="json")
    state = build_followup_request(previous_record, _SELECTION_MESSAGE)
    options = state["options"]
    stays = sorted([
        *[item for item in options["lodging_stays"] if item["night"] != stay["night"]],
        stay,
    ], key=lambda item: item["night"])
    patch = {
        "lodging_status": "provided" if len(stays) == options["trip_days"] - 1 else "undecided",
        "lodging_stays": stays,
    }
    message = (
        f"{stay['night']}일차 관광 후에는 숙소 '{stay['name']}'에서 묵겠습니다. "
        "선택한 숙소 위치를 사용하고 기존 관광지와 여행 조건은 가능한 한 유지해 주세요. "
        "해당 숙박일의 숙소 도착 이동과 다음 날 숙소 출발 이동을 조회해 전체 일정의 시간을 다시 계산해 주세요."
    )
    return build_followup_request(previous_record, message, options_patch=patch), patch


def resolve_turn_case(scenario, turn_index, *, previous_record=None, lodging_record=None):
    """저장된 기록만으로도 다음 요청을 재구성합니다. 외부 API는 호출하지 않습니다."""
    scenario = validate_scenario(scenario)
    if type(turn_index) is not int or not 0 <= turn_index < len(scenario["turns"]):
        raise ValueError("대화 단계의 순번을 확인해 주세요.")
    turn = scenario["turns"][turn_index]
    patch = {}
    if turn_index == 0:
        if previous_record is not None or lodging_record is not None:
            raise ValueError("첫 단계에는 이전 결과나 숙소 검색 기록을 넣지 않습니다.")
        request = turn["request"]
    elif "lodging_selection" in turn:
        request, patch = _lodging_request(previous_record, turn["lodging_selection"], lodging_record)
    else:
        if lodging_record is not None:
            raise ValueError("일반 후속 대화에는 숙소 검색 기록을 넣지 않습니다.")
        patch = turn.get("options_patch", {})
        request = build_followup_request(previous_record, turn["message"], options_patch=patch)
    return validate_turn_case(_case(scenario["case_id"], turn, request, patch))


def run_chat_scenario(
    scenario, *, agent_runner, reference_date=None, request_runner=None,
    lodging_runner=None, on_record=None,
):
    """순차 실행하고 채점 전에 원본을 전달합니다. 실패한 단계 뒤에는 호출하지 않습니다."""
    scenario = validate_scenario(scenario)
    if not callable(agent_runner) or (request_runner is not None and not callable(request_runner)):
        raise ValueError("Agent와 요청 실행 함수가 필요합니다.")
    if reference_date is not None and type(reference_date) is not date:
        raise ValueError("기준 날짜는 datetime.date 형식이어야 합니다.")
    if on_record is not None and not callable(on_record):
        raise ValueError("원본 기록 콜백은 호출할 수 있는 함수여야 합니다.")
    if lodging_runner is not None and not callable(lodging_runner):
        raise ValueError("숙소 조회 실행 함수가 필요합니다.")
    if any("lodging_selection" in turn for turn in scenario["turns"]) and lodging_runner is None:
        raise ValueError("숙소 선택 시나리오에는 숙소 조회 실행 함수를 지정해 주세요.")
    request_runner = run_chat_request if request_runner is None else request_runner
    entries = []
    previous = None
    for index, turn in enumerate(scenario["turns"]):
        raw = {
            "turn_id": turn["turn_id"], "case": None, "record": None,
            "lodging_record": None, "preparation_error": None,
        }
        stage = "request"
        try:
            if "lodging_selection" in turn:
                stage = "lodging_query"
                query = prepare_lodging_query(previous, turn["lodging_selection"]["night"])
                stage = "lodging_http"
                raw["lodging_record"] = _snapshot(lodging_runner(deepcopy(query)))
                stage = "lodging_selection"
            raw["case"] = resolve_turn_case(
                scenario, index, previous_record=previous, lodging_record=raw["lodging_record"],
            )
        except Exception as error:
            raw["preparation_error"] = {"stage": stage, "error_type": type(error).__name__}
        if raw["preparation_error"] is None:
            started_at = perf_counter()
            request = raw["case"]["request"]
            try:
                raw["record"] = _snapshot(request_runner(
                    deepcopy(request), agent_runner=agent_runner, reference_date=reference_date,
                ))
            except Exception as error:
                raw["record"] = {
                    "request": deepcopy(request), "http_status": None, "response": None,
                    "attempts": [], "execution_error": type(error).__name__,
                    "reference_date": reference_date.isoformat() if reference_date else None,
                    "elapsed_seconds": round(perf_counter() - started_at, 3),
                }
        entry = {"raw": _snapshot(raw), "score": None, "scoring_error": None}
        entries.append(entry)
        if on_record is not None:
            on_record(deepcopy(entry["raw"]))
        if raw["preparation_error"] is not None:
            break
        try:
            entry["score"] = evaluate_chat_turn(
                raw["case"], raw["record"], previous_record=previous,
            )
        except Exception as error:
            entry["scoring_error"] = type(error).__name__
            break
        if entry["score"]["automatic_passed"] is not True:
            break
        previous = deepcopy(entry["raw"]["record"])
    return {
        "scenario": deepcopy(scenario), "turns": entries,
        "automatic_passed": len(entries) == len(scenario["turns"]) and all(
            entry["score"] is not None and entry["score"]["automatic_passed"] is True
            for entry in entries
        ),
        "skipped_turns": [turn["turn_id"] for turn in scenario["turns"][len(entries):]],
    }
