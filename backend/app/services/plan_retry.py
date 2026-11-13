import json
from copy import deepcopy


MAX_PLAN_RETRIES = 1
MAX_FEEDBACK_ERRORS = 20
MAX_FEEDBACK_LENGTH = 500

OPTION_LABELS = {
    "departure": "출발지",
    "departure_latitude": "출발지 위도",
    "departure_longitude": "출발지 경도",
    "start_date": "출발 날짜",
    "trip_days": "여행 기간",
    "transport": "이동수단",
    "mobility_aid": "이동 보조기구",
    "travel_styles": "여행 종류",
    "requirements": "필수 이용 조건",
    "return_to_origin": "출발지 귀환",
}


class PlanValidationFailure(Exception):
    def __init__(
        self,
        errors: list[str],
        *,
        message: str = (
            "생성된 일정의 구성·시간·이동 정보에 오류가 있습니다. "
            "다시 시도해 주세요."
        ),
    ):
        super().__init__(message)
        self.errors = list(errors)
        self.message = message


def build_plan_retry_messages(
    messages: list[dict[str, str]],
    *,
    failed_plan: dict,
    options: dict,
    errors: list[str],
) -> list[dict[str, str]]:
    """원래 요청을 유지하면서 검증 실패 정보를 추가합니다."""
    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(messages[-1], dict)
        or messages[-1].get("role") != "user"
    ):
        raise ValueError("재생성할 사용자 요청을 확인하지 못했습니다.")

    if not isinstance(failed_plan, dict) or not isinstance(options, dict):
        raise ValueError("실패한 일정과 여행 조건은 객체여야 합니다.")

    if (
        not isinstance(errors, list)
        or not errors
        or any(not isinstance(reason, str) or not reason.strip() for reason in errors)
    ):
        raise ValueError("재생성에는 검증 실패 이유가 필요합니다.")

    retry_messages = deepcopy(messages)
    try:
        payload = json.loads(retry_messages[-1]["content"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("원래 사용자 요청이 JSON 형식이 아닙니다.") from error

    if not isinstance(payload, dict) or "plan_retry" in payload:
        raise ValueError("자동 재생성 요청은 한 번만 만들 수 있습니다.")

    failed_copy = deepcopy(failed_plan)
    for day in failed_copy.get("days", []):
        for item in day.get("items", []):
            item["route_id"] = None
            for field in ("verified", "category", "latitude", "longitude"):
                item.pop(field, None)

    payload["plan_retry"] = {
        "attempt": 1,
        "locked_options": deepcopy(options),
        "validation_errors": [
            reason[:MAX_FEEDBACK_LENGTH]
            for reason in errors[:MAX_FEEDBACK_ERRORS]
        ],
        "failed_plan": failed_copy,
    }
    retry_messages[-1]["content"] = json.dumps(payload, ensure_ascii=False)
    return retry_messages


def validate_retry_options(expected: dict, actual: dict) -> list[str]:
    """첫 시도에서 정리한 조건을 재생성 결과가 유지하는지 검사합니다."""
    errors = []
    for field, label in OPTION_LABELS.items():
        expected_value = expected.get(field)
        actual_value = actual.get(field)
        if field in ("travel_styles", "requirements"):
            if isinstance(expected_value, list) and isinstance(actual_value, list):
                expected_value = sorted(expected_value)
                actual_value = sorted(actual_value)
        if expected_value != actual_value:
            errors.append(f"자동 재생성에서 {label} 조건을 변경할 수 없습니다.")
    return errors


def merge_agent_metrics(results: list[dict]) -> dict:
    """응답을 받은 각 시도의 모델·도구 호출 횟수를 합산합니다."""
    model_call_count = 0
    tool_call_counts = {}

    for result in results:
        count = result.get("model_call_count")
        if type(count) is int and count >= 0:
            model_call_count += count

        calls = result.get("tool_call_counts")
        if not isinstance(calls, dict):
            continue
        for name, count in calls.items():
            if (
                isinstance(name, str)
                and name
                and type(count) is int
                and count >= 0
            ):
                tool_call_counts[name] = tool_call_counts.get(name, 0) + count

    return {
        "model_call_count": model_call_count,
        "tool_call_counts": tool_call_counts,
    }