from collections import Counter

from .plan_evaluation import convert_seconds_to_minutes, is_close_minutes
from .plan_routes import normalize_transport_mode


def validate_plan_moves(plan: dict, route_calls: list[dict] | None) -> list[str]:
    """새 일정의 이동을 이번 실행의 조회 기록과 대조합니다."""
    records = {}
    duplicate_ids = set()

    for record in route_calls if isinstance(route_calls, list) else []:
        if not isinstance(record, dict):
            continue

        route_id = record.get("route_id")
        if not isinstance(route_id, str) or not route_id.strip():
            continue

        if route_id in records:
            duplicate_ids.add(route_id)
        records[route_id] = record

    moves = [
        (day["day"], index, item)
        for day in plan["days"]
        for index, item in enumerate(day["items"], start=1)
        if item["item_type"] == "move"
    ]
    reference_counts = Counter(
        item["route_id"]
        for _, _, item in moves
        if isinstance(item.get("route_id"), str)
    )

    errors = []
    for day_number, index, item in moves:
        label = f"{day_number}일차 {index}번째 일정 '{item['title']}'"
        route_id = item.get("route_id")

        if not isinstance(route_id, str) or not route_id.strip():
            errors.append(f"{label}: 이동 경로 ID가 없습니다.")
            continue

        if route_id not in records:
            errors.append(
                f"{label}: 이번 요청에서 조회한 이동 경로를 확인하지 못했습니다."
            )
            continue

        if route_id in duplicate_ids:
            errors.append(
                f"{label}: 조회 기록의 경로 ID가 중복돼 결과를 확인할 수 없습니다."
            )
            continue

        if reference_counts[route_id] != 1:
            errors.append(
                f"{label}: 하나의 경로 ID를 여러 이동에 사용할 수 없습니다."
            )
            continue

        record = records[route_id]
        mode = normalize_transport_mode(item.get("transport"))

        if mode is None or record.get("transport") != mode:
            errors.append(
                f"{label}: 이동수단 확인 실패 "
                f"(일정 표기={item.get('transport')!r}, "
                f"해석 결과={mode!r}, 조회 수단={record.get('transport')!r})."
            )
            continue

        seconds = record.get("duration_seconds")
        minutes = item.get("duration_minutes")

        if type(seconds) is not int or seconds < 0:
            errors.append(f"{label}: 조회된 이동시간 형식이 올바르지 않습니다.")
            continue

        if type(minutes) is not int or minutes < 1:
            errors.append(
                f"{label}: 일정의 이동시간은 1분 이상의 정수여야 합니다."
            )
            continue

        try:
            expected_minutes = convert_seconds_to_minutes(seconds)
        except (OverflowError, ValueError):
            errors.append(
                f"{label}: 조회된 이동시간을 분 단위로 변환하지 못했습니다."
            )
            continue

        if not is_close_minutes(minutes, expected_minutes):
            errors.append(
                f"{label}: 일정 {minutes}분, 조회 결과 올림 "
                f"{expected_minutes}분으로 허용 오차를 벗어났습니다."
            )

    return errors