"""연속 대화의 요청 연결, 조건 유지와 단계별 방문 결과를 채점합니다."""
from collections import Counter
from copy import deepcopy
import json
import re

from backend.app.routers.chat import ChatOptions, ChatRequest, ChatResponse
from backend.app.services.chat_api_evaluation import evaluate_chat_record
from backend.app.services.lodging_planning import validate_lodging_boundaries
from backend.app.services.plan_evaluation import make_check_result
from scripts.chat_multiturn_state import build_followup_request
from scripts.evaluate_chat_api import CASE_FIELDS, validate_case


_EXTRA_FIELDS = {"options_patch", "expected_visits", "preserve_visits"}
_VISIT_FIELDS = {"day", "place_id", "duration_minutes", "start_time", "end_time"}
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")


def validate_turn_case(case):
    """단계별 사례를 검사하며 expected_visits는 전체 방문 목록을 뜻합니다."""
    if not isinstance(case, dict) or set(case) - CASE_FIELDS - _EXTRA_FIELDS:
        raise ValueError("연속 대화 사례의 필드를 확인해 주세요.")
    try:
        json.dumps(case, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("연속 대화 사례는 유효한 JSON이어야 합니다.") from error
    result = validate_case({key: value for key, value in case.items() if key in CASE_FIELDS})
    patch = case.get("options_patch", {})
    if not isinstance(patch, dict) or set(patch) - set(ChatOptions.model_fields):
        raise ValueError("선택 옵션 변경의 필드를 확인해 주세요.")
    result["options_patch"] = deepcopy(patch)
    preserve = case.get("preserve_visits", False)
    if type(preserve) is not bool:
        raise ValueError("preserve_visits는 참 또는 거짓이어야 합니다.")
    result["preserve_visits"] = preserve
    visits = case.get("expected_visits")
    if "expected_visits" in case and not isinstance(visits, list):
        raise ValueError("expected_visits는 전체 방문 목록이어야 합니다.")
    seen = set()
    for visit in visits or []:
        if (
            not isinstance(visit, dict) or set(visit) - _VISIT_FIELDS
            or type(visit.get("day")) is not int or visit["day"] < 1
            or not isinstance(visit.get("place_id"), str) or not visit["place_id"].strip()
        ):
            raise ValueError("방문 기대값에는 일차와 장소 ID가 필요합니다.")
        key = (visit["day"], visit["place_id"])
        if key in seen:
            raise ValueError("같은 일차와 장소 ID의 방문 기대값이 중복됐습니다.")
        seen.add(key)
        minutes = visit.get("duration_minutes")
        if "duration_minutes" in visit and (type(minutes) is not int or minutes < 1):
            raise ValueError("방문 소요시간은 1분 이상의 정수여야 합니다.")
        for field in ("start_time", "end_time"):
            if field in visit and (
                not isinstance(visit[field], str) or _CLOCK.fullmatch(visit[field]) is None
            ):
                raise ValueError("방문 시각은 HH:MM 형식이어야 합니다.")
    if "expected_visits" in case:
        result["expected_visits"] = deepcopy(visits)
    return result


def _visits(plan):
    return [
        (day["day"], item)
        for day in plan["days"] for item in day["items"]
        if item["item_type"] == "visit"
    ]


def _visit_errors(case, request, plan):
    if plan is None:
        return ["방문 결과를 검사할 새 일정이 없습니다."]
    actual = _visits(plan)
    actual_keys = Counter((day, item.get("place_id")) for day, item in actual)
    errors = []
    wanted = case.get("expected_visits")
    if wanted is not None:
        wanted_keys = Counter((item["day"], item["place_id"]) for item in wanted)
        if actual_keys != wanted_keys:
            errors.append("전체 방문의 일차·장소 ID·횟수가 기대 목록과 다릅니다.")
        for expected in wanted:
            matches = [
                item for day, item in actual
                if day == expected["day"] and item.get("place_id") == expected["place_id"]
            ]
            if len(matches) != 1:
                continue
            for field in ("duration_minutes", "start_time", "end_time"):
                if field in expected and matches[0].get(field) != expected[field]:
                    errors.append(
                        f"{expected['day']}일차 {expected['place_id']}: {field}가 기대 값과 다릅니다."
                    )
    if case["preserve_visits"]:
        previous = request.current_plan
        if previous is None:
            errors.append("유지 여부를 검사할 이전 일정이 없습니다.")
        else:
            previous_visits = _visits(previous.model_dump(mode="json"))
            if any(not item.get("place_id") for _, item in previous_visits):
                errors.append("이전 방문의 장소 ID가 미확인이어서 유지 여부를 판정할 수 없습니다.")
            previous_keys = Counter(
                (day, item.get("place_id")) for day, item in previous_visits
            )
            if actual_keys != previous_keys:
                errors.append("이전 일정의 방문 일차·장소 ID·횟수가 유지되지 않았습니다.")
    return errors


def evaluate_chat_turn(case, record, *, previous_record=None):
    """기존 API 채점을 유지하고 연결·방문·숙소 검사를 추가합니다."""
    case = validate_turn_case(case)
    if previous_record is None and case["options_patch"]:
        raise ValueError("첫 단계의 선택 옵션은 request.options에 지정해 주세요.")
    working_case = {key: deepcopy(value) for key, value in case.items() if key in CASE_FIELDS}
    link = make_check_result(None, [])
    if previous_record is not None:
        try:
            expected = build_followup_request(
                previous_record, case["request"]["message"],
                options_patch=case["options_patch"],
            )
        except (TypeError, ValueError):
            link = make_check_result(False, ["이전 HTTP 결과에서 다음 요청을 구성할 수 없습니다."])
        else:
            same = case["request"] == expected
            link = make_check_result(
                same, [] if same else ["다음 요청의 대화·옵션·기존 일정이 이전 결과와 연결되지 않았습니다."],
            )
        request = ChatRequest.model_validate(case["request"])
        working_case["expected_options"] = {
            key: value for key, value in request.options.model_dump(mode="json").items()
            if value is not None
        } | case.get("expected_options", {})

    result = deepcopy(evaluate_chat_record(working_case, record))
    result["checks"]["turn_link"] = link
    result["checks"]["turn_visits"] = make_check_result(None, [])
    result["checks"]["turn_lodging"] = make_check_result(None, [])
    body = None
    if record.get("http_status") == 200:
        try:
            body = ChatResponse.model_validate(record.get("response"))
        except (TypeError, ValueError):
            pass
    if case.get("expected_visits") is not None or case["preserve_visits"]:
        request = ChatRequest.model_validate(case["request"])
        errors = _visit_errors(
            case, request, body.plan.model_dump(mode="json") if body and body.plan is not None else None,
        )
        result["checks"]["turn_visits"] = make_check_result(not errors, errors)
    if body is not None and body.plan is not None:
        errors = validate_lodging_boundaries(
            body.plan.model_dump(mode="json"),
            trip_days=body.options.trip_days,
            lodging_status=body.options.lodging_status,
            lodging_stays=[stay.model_dump(mode="json") for stay in body.options.lodging_stays],
        )
        result["checks"]["turn_lodging"] = make_check_result(not errors, errors)
    result["automatic_passed"] = result["automatic_passed"] and all(
        check["passed"] is not False for check in result["checks"].values()
    )
    if not result["automatic_passed"]:
        result["requires_manual_review"] = False
        result["review_reasons"] = []
    result["checked_option_fields"] = sorted(working_case.get("expected_options", {}))
    return result
