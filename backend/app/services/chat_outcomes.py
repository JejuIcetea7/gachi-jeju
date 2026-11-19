"""최종 응답 유형과 명시적인 기대 조건을 평가합니다. 설명의 타당성은 추정하지 않습니다."""
from datetime import date


EXPECTED_OUTCOMES = {"plan", "message", "empty_plan", "partial_plan", "error"}
_EXPECTATION_FIELDS = {"outcome", "http_status", "error_code", "empty_days"}


def _has_text(value):
    return isinstance(value, str) and bool(value.strip())


def _plan_visits(body):
    options = body["options"]
    start_date = options.get("start_date")
    if not isinstance(start_date, str):
        raise ValueError("출발 날짜가 없습니다.")
    if date.fromisoformat(start_date).isoformat() != start_date:
        raise ValueError("출발 날짜 형식이 올바르지 않습니다.")
    if type(options.get("trip_days")) is not int or options["trip_days"] < 1:
        raise ValueError("여행 일수가 올바르지 않습니다.")
    if options.get("transport") not in ("driving", "walking", "transit"):
        raise ValueError("이동수단이 올바르지 않습니다.")

    plan = body["plan"]
    if not isinstance(plan, dict):
        raise ValueError("일정은 객체여야 합니다.")
    days = plan.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("일차 목록이 없습니다.")
    seen = set()
    visits = []
    for day in days:
        if not isinstance(day, dict):
            raise ValueError("일차 형식이 올바르지 않습니다.")
        number = day.get("day")
        if type(number) is not int or number < 1 or number in seen:
            raise ValueError("일차 번호가 올바르지 않습니다.")
        seen.add(number)
        items = day.get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) or item.get("item_type") not in ("visit", "move")
            for item in items
        ):
            raise ValueError("일정 항목 형식이 올바르지 않습니다.")
        visits.append((number, sum(item["item_type"] == "visit" for item in items)))
    return visits


def classify_chat_response(http_status, body):
    """응답의 기본 구조로 분류합니다. 시간·장소·설명의 정합성 검사는 별도입니다."""
    if type(http_status) is not int or not isinstance(body, dict):
        return "invalid_response"
    if 400 <= http_status <= 599:
        error = body.get("error")
        if (
            isinstance(error, dict)
            and _has_text(error.get("code"))
            and _has_text(error.get("message"))
        ):
            return "error"
        return "invalid_response"
    if (
        http_status != 200
        or not _has_text(body.get("reply"))
        or not isinstance(body.get("options"), dict)
        or "plan" not in body
    ):
        return "invalid_response"
    if body["plan"] is None:
        return "message"
    try:
        visits = _plan_visits(body)
    except (KeyError, TypeError, ValueError, OverflowError):
        return "invalid_response"
    if all(count == 0 for _, count in visits):
        return "empty_plan"
    if any(count == 0 for _, count in visits):
        return "partial_plan"
    return "plan"


def validate_outcome_expectation(expectation):
    """실행 전에 평가 조건의 오타와 모순을 거부하고 정규화한 사본을 반환합니다."""
    if not isinstance(expectation, dict) or set(expectation) - _EXPECTATION_FIELDS:
        raise ValueError("평가 기대 조건의 필드를 확인해 주세요.")
    outcome = expectation.get("outcome")
    if not isinstance(outcome, str) or outcome not in EXPECTED_OUTCOMES:
        raise ValueError("평가할 응답 유형이 올바르지 않습니다.")
    status = expectation.get("http_status", 200)
    if type(status) is not int:
        raise ValueError("기대 HTTP 상태는 정수여야 합니다.")
    code = expectation.get("error_code")
    if outcome == "error":
        if not 400 <= status <= 599 or not _has_text(code):
            raise ValueError("오류 기대에는 HTTP 오류 상태와 오류 코드가 필요합니다.")
        if code != code.strip():
            raise ValueError("기대 오류 코드의 앞뒤 공백을 제거해 주세요.")
    elif status != 200 or code is not None:
        raise ValueError("정상 응답 기대에는 HTTP 200과 빈 오류 코드를 사용합니다.")

    empty_days = expectation.get("empty_days", [])
    if (
        not isinstance(empty_days, list)
        or any(type(day) is not int or day < 1 for day in empty_days)
        or len(empty_days) != len(set(empty_days))
    ):
        raise ValueError("허용할 빈 일차는 중복 없는 양의 정수 목록이어야 합니다.")
    if (outcome in {"empty_plan", "partial_plan"}) != bool(empty_days):
        raise ValueError("빈 일정·일부 빈 일정 기대에만 허용할 빈 일차를 지정합니다.")
    return {
        "outcome": outcome,
        "http_status": status,
        "error_code": code,
        "empty_days": sorted(empty_days),
    }


def evaluate_chat_outcome(http_status, body, expectation):
    """유형·상태·오류 코드·빈 일차를 평가합니다. 내용 검토와 일정 검증은 별도입니다."""
    expected = validate_outcome_expectation(expectation)
    outcome = classify_chat_response(http_status, body)
    has_plan = outcome in {"plan", "empty_plan", "partial_plan"}
    empty_days = sorted(
        number for number, count in _plan_visits(body) if count == 0
    ) if has_plan else []

    def check(passed, reason):
        return {"passed": passed, "reasons": [reason] if passed is False else []}

    checks = {
        "response_shape": check(
            outcome != "invalid_response", "응답의 기본 구조를 확인하지 못했습니다.",
        ),
        "http_status": check(
            type(http_status) is int and http_status == expected["http_status"],
            f"HTTP 상태가 기대 값 {expected['http_status']}와 다릅니다: {http_status}",
        ),
        "outcome": check(
            outcome == expected["outcome"],
            f"응답 유형이 기대 값 {expected['outcome']}와 다릅니다: {outcome}",
        ),
        "empty_days": check(
            empty_days == expected["empty_days"]
            if has_plan or expected["empty_days"] else None,
            f"빈 일차가 기대 값 {expected['empty_days']}와 다릅니다: {empty_days}",
        ),
        "error_code": check(
            outcome == "error" and body["error"]["code"] == expected["error_code"]
            if expected["outcome"] == "error" else None,
            f"기대 오류 코드 {expected['error_code']}를 확인하지 못했습니다.",
        ),
    }
    automatic_passed = all(item["passed"] is not False for item in checks.values())
    review_reasons = []
    if automatic_passed and outcome in {"empty_plan", "partial_plan"}:
        review_reasons.append("빈 일차의 제외 사유가 요청과 DB 근거에 맞는지 확인해야 합니다.")
    if automatic_passed and outcome == "message":
        review_reasons.append("질문이나 안내 내용이 요청에 적절한지 확인해야 합니다.")
    return {
        "outcome": outcome,
        "empty_days": empty_days,
        "automatic_passed": automatic_passed,
        "requires_manual_review": bool(review_reasons),
        "review_reasons": review_reasons,
        "checks": checks,
    }