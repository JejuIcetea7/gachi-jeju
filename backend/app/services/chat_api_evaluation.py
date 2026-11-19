"""최종 /chat 응답을 마지막 시도의 근거와 대조합니다. 서비스 동작은 변경하지 않습니다."""
from copy import deepcopy
import json
import sqlite3

from pydantic import ValidationError

from .chat_outcomes import evaluate_chat_outcome
from .chat_route_points import read_confirmed_restaurants
from .plan_consistency import ALLOWED_TRAVEL_MODES, validate_plan_moves
from .plan_evaluation import (
    calculate_visit_metrics, check_day_count, check_places_in_database,
    check_times, check_transport_tools, make_check_result, read_places_by_id,
)
from .plan_retry import MAX_PLAN_RETRIES
from .route_endpoints import validate_route_endpoints
from .plan_reply_consistency import validate_plan_reply_consistency

def _record_errors(case, record):
    errors = []
    if record.get("request") != case["request"]:
        errors.append("실행한 요청과 평가 사례의 요청이 다릅니다.")
    attempts = record.get("attempts")
    if not isinstance(attempts, list):
        return errors + ["생성 시도 기록이 없습니다."]
    if len(attempts) > MAX_PLAN_RETRIES + 1:
        errors.append("허용한 생성 시도 횟수를 초과했습니다.")
    if record.get("http_status") == 200 and not attempts:
        errors.append("정상 응답의 생성 시도 기록이 없습니다.")
    for number, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict):
            errors.append(f"{number}번째 생성 기록이 객체가 아닙니다.")
            continue
        if type(attempt.get("attempt")) is not int or attempt["attempt"] != number:
            errors.append(f"{number}번째 생성 기록의 순서가 올바르지 않습니다.")
        if "capture_error" not in attempt or attempt["capture_error"] is not None:
            errors.append(f"{number}번째 생성 근거를 온전히 기록하지 못했습니다.")
        feedback = attempt.get("retry_feedback")
        if (
            not isinstance(feedback, list)
            or any(not isinstance(reason, str) or not reason.strip() for reason in feedback)
            or (number == 1 and feedback)
            or (number > 1 and not feedback)
        ):
            errors.append(f"{number}번째 재생성 피드백을 확인하지 못했습니다.")
        status = attempt.get("status")
        if status == "error":
            if (
                number != len(attempts)
                or record.get("http_status") == 200
                or not isinstance(attempt.get("error_type"), str)
                or not attempt["error_type"].strip()
            ):
                errors.append(f"{number}번째 Agent 오류와 최종 응답이 일치하지 않습니다.")
            continue
        if status != "returned":
            errors.append(f"{number}번째 Agent 실행 결과를 확인하지 못했습니다.")
            continue
        if any(key not in attempt for key in (
            "chat_reply", "tool_trace", "route_calls", "metrics", "process",
        )):
            errors.append(f"{number}번째 생성 근거의 필드가 누락됐습니다.")
            continue
        trace = attempt["tool_trace"]
        routes = attempt["route_calls"]
        if not isinstance(trace, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("tool"), str) or not item["tool"].strip()
            or not isinstance(item.get("call_id"), str) or not item["call_id"].strip()
            or not isinstance(item.get("input"), dict)
            or "output" not in item
            for item in trace
        ):
            errors.append(f"{number}번째 도구 기록의 형식이 올바르지 않습니다.")
        elif len({item["call_id"] for item in trace}) != len(trace):
            errors.append(f"{number}번째 도구 호출 ID가 중복됐습니다.")
        if not isinstance(routes, list) or any(not isinstance(item, dict) for item in routes):
            errors.append(f"{number}번째 경로 기록의 형식이 올바르지 않습니다.")
        if not isinstance(attempt["metrics"], dict) or (
            attempt["process"] is not None and not isinstance(attempt["process"], dict)
        ):
            errors.append(f"{number}번째 호출·사용량 기록의 형식이 올바르지 않습니다.")
    return errors


def _place_lookup_errors(plan, trace):
    restaurants = read_confirmed_restaurants(trace)
    known_ids = set()
    for item in trace:
        output = item["output"]
        if not isinstance(output, dict) or output.get("status") != "ok":
            continue
        if item["tool"] in {"search_places", "search_places_by_name"}:
            places = output.get("places")
            if isinstance(places, list):
                known_ids.update(
                    place["place_id"] for place in places
                    if isinstance(place, dict) and isinstance(place.get("place_id"), str)
                )
        elif item["tool"] == "get_place_details":
            place_id = output.get("place_id")
            if isinstance(place_id, str) and item["input"].get("place_id") == place_id:
                known_ids.add(place_id)
    errors = []
    for day in plan["days"]:
        for item in day["items"]:
            if item["item_type"] != "visit":
                continue
            reference = item.get("restaurant_ref")
            if reference is not None:
                restaurant = restaurants.get(reference)
                if (
                    restaurant is None or item.get("place_id") is not None
                    or item.get("verified") is not False
                    or item["title"] != restaurant["name"]
                    or item.get("restaurant_address") != restaurant["address"]
                    or item.get("latitude") != restaurant["latitude"]
                    or item.get("longitude") != restaurant["longitude"]
                ):
                    errors.append(f"{day['day']}일차 '{item['title']}': 마지막 시도의 식당 조회 결과와 일치하지 않습니다.")
            elif item.get("place_id") not in known_ids:
                errors.append(f"{day['day']}일차 '{item['title']}': 마지막 시도의 장소 조회 근거가 없습니다.")
    return errors


def _option_errors(request_options, expected_options, actual_options, *, has_plan):
    fields = {
        key for key, value in request_options.items()
        if has_plan and value is not None and value != []
    } | set(expected_options)
    errors = []
    for field in fields:
        wanted = expected_options.get(field, request_options.get(field))
        actual = actual_options.get(field)
        if field == "requirements" and field not in expected_options:
            same = set(wanted or []).issubset(actual or [])
        elif field in {"requirements", "travel_styles", "lodging_stays"}:
            same = {json.dumps(item, sort_keys=True) for item in (wanted or [])} == {
                json.dumps(item, sort_keys=True) for item in (actual or [])
            }
        else:
            same = actual == wanted
        if not same:
            errors.append(f"응답 options의 {field} 조건이 기대 값과 다릅니다.")
    return errors


def evaluate_chat_record(case, record):
    """응답 유형·최종 일정·현재 근거를 평가하며 내용 검토 대기는 별도로 반환합니다."""
    if (
        not isinstance(case, dict) or not isinstance(case.get("request"), dict)
        or not isinstance(record, dict)
    ):
        raise ValueError("평가 사례와 실행 기록의 형식을 확인해 주세요.")

    from ..routers.chat import ChatOptions, ChatRequest, ChatResponse
    from ..routers.plan import ErrorResponse

    overrides = case.get("expected_options", {})
    if not isinstance(overrides, dict) or set(overrides) - set(ChatOptions.model_fields):
        raise ValueError("기대 options의 필드를 확인해 주세요.")
    outcome = evaluate_chat_outcome(
        record.get("http_status"), record.get("response"), case.get("expectation"),
    )
    checks = deepcopy(outcome["checks"])
    record_errors = _record_errors(case, record)
    checks["capture"] = make_check_result(not record_errors, record_errors)
    body = record.get("response")
    schema_ok = False
    try:
        model = ChatResponse if record.get("http_status") == 200 else ErrorResponse
        body = model.model_validate(body).model_dump(mode="json")
        schema_ok = True
    except (ValidationError, TypeError, ValueError):
        pass
    checks["response_contract"] = make_check_result(
        schema_ok, [] if schema_ok else ["최종 응답이 API 응답 모델과 일치하지 않습니다."],
    )
    for name in ("H2", "H3", "H4", "H5", "H6", "H7", "H8", "endpoints", "reply_consistency"):
        checks[name] = make_check_result(None, [])

    evaluation_errors = list(record_errors)
    visit_metrics = {"day_count": 0, "thin_day_count": 0, "visit_counts_by_day": []}
    if schema_ok and not record_errors and outcome["outcome"] not in {"error", "invalid_response"}:
        has_plan = body["plan"] is not None
        try:
            request = ChatRequest.model_validate(case["request"])
            expected_options = ChatOptions.model_validate({
                **request.options.model_dump(mode="json"), **overrides,
            }).model_dump(mode="json")
        except ValidationError:
            checks["H8"] = make_check_result(False, ["요청 또는 기대 options의 형식이 올바르지 않습니다."])
        else:
            request_options = request.options.model_dump(mode="json")
            normalized_overrides = {key: expected_options[key] for key in overrides}
            checks["H8"] = make_check_result(
                not (option_errors := _option_errors(
                    request_options, normalized_overrides, body["options"], has_plan=has_plan,
                )), option_errors,
            )
            working_case = deepcopy(case)
            working_case["request"]["options"] = {
                **expected_options,
                "transport": expected_options.get("transport") or body["options"]["transport"],
            }
            if not has_plan and not overrides:
                checks["H8"] = make_check_result(None, [])
            if has_plan:
                plan = body["plan"]
                last = record["attempts"][-1]
                visit_metrics = calculate_visit_metrics(plan)
                checks["H2"] = check_day_count(working_case, body, plan)
                checks["H3"] = check_times(working_case, plan)
                reply_errors = validate_plan_reply_consistency(
                    body["reply"], plan,
                    options=body["options"], user_message=case["request"]["message"],
                )
                checks["reply_consistency"] = make_check_result(
                    not reply_errors, reply_errors,
                )
                checks["H7"] = check_transport_tools(working_case, last["tool_trace"])
                selected_ids = {
                    item.get("route_id") for day in plan["days"] for item in day["items"]
                    if item["item_type"] == "move"
                }
                allowed_modes = ALLOWED_TRAVEL_MODES[working_case["request"]["options"]["transport"]]
                for route in last["route_calls"]:
                    if route.get("route_id") in selected_ids and route.get("transport") not in allowed_modes:
                        checks["H7"]["reasons"].append("선택한 경로의 이동수단이 요청 조건과 다릅니다.")
                checks["H7"]["passed"] = not checks["H7"]["reasons"]
                try:
                    places = read_places_by_id(plan)
                except sqlite3.Error:
                    evaluation_errors.append("평가용 장소 DB를 읽지 못했습니다.")
                    checks["H4"] = make_check_result(False, ["평가용 장소 DB를 읽지 못했습니다."])
                else:
                    tourism_plan = {"days": [
                        {**day, "items": [item for item in day["items"] if item.get("restaurant_ref") is None]}
                        for day in plan["days"]
                    ]}
                    checks["H4"] = check_places_in_database(tourism_plan, places)
                    unverified = [
                        f"{day['day']}일차 '{item['title']}': DB 확인 표시가 없습니다."
                        for day in tourism_plan["days"] for item in day["items"]
                        if item["item_type"] == "visit" and item.get("verified") is not True
                    ]
                    checks["H4"]["reasons"].extend(unverified)
                    checks["H4"]["passed"] = not checks["H4"]["reasons"]
                lookup_errors = _place_lookup_errors(plan, last["tool_trace"])
                checks["H5"] = make_check_result(not lookup_errors, lookup_errors)
                move_errors = validate_plan_moves(plan, last["route_calls"])
                checks["H6"] = make_check_result(not move_errors, move_errors)
                endpoint_errors = validate_route_endpoints(
                    plan, last["route_calls"],
                    departure={
                        "latitude": body["options"]["departure_latitude"],
                        "longitude": body["options"]["departure_longitude"],
                    },
                    return_to_origin=body["options"]["return_to_origin"] is True,
                    daily_departure=body["options"].get("lodging_status") == "not_needed",
                    lodging_stays=body["options"]["lodging_stays"],
                )
                checks["endpoints"] = make_check_result(not endpoint_errors, endpoint_errors)

    automatic_passed = not evaluation_errors and all(
        check["passed"] is not False for check in checks.values()
    )
    return {
        "evaluation_status": "error" if evaluation_errors else "ok",
        "evaluation_errors": evaluation_errors,
        "outcome": outcome["outcome"],
        "automatic_passed": automatic_passed,
        "requires_manual_review": automatic_passed and outcome["requires_manual_review"],
        "review_reasons": outcome["review_reasons"] if automatic_passed else [],
        "checks": checks,
        "visit_metrics": visit_metrics,
        "attempt_count": len(record["attempts"]) if isinstance(record.get("attempts"), list) else 0,
    }
