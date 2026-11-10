import re


def _to_minutes(value: str) -> int:
    if not isinstance(value, str) or re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value
    ) is None:
        raise ValueError("시각은 HH:MM 형식이어야 합니다.")

    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_plan_times(
    plan: dict,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[str]:
    """일정을 수정하지 않고 시간 검증에서 발견한 문제를 반환합니다."""
    if (start_time is None) != (end_time is None):
        raise ValueError("시작 시각과 종료 시각은 함께 지정해야 합니다.")

    window_start = _to_minutes(start_time) if start_time is not None else None
    window_end = _to_minutes(end_time) if end_time is not None else None

    if (
        window_start is not None
        and window_end is not None
        and window_end <= window_start
    ):
        raise ValueError("종료 시각은 시작 시각보다 늦어야 합니다.")

    errors = []

    for day in plan["days"]:
        latest_end = None

        for index, item in enumerate(day["items"], start=1):
            label = f"{day['day']}일차 {index}번째 일정"

            try:
                item_start = _to_minutes(item.get("start_time"))
                item_end = _to_minutes(item.get("end_time"))
            except ValueError:
                errors.append(
                    f"{label}: 시각은 00:00~23:59 범위의 HH:MM 형식이어야 합니다."
                )
                continue

            if item_end <= item_start:
                errors.append(
                    f"{label}: 종료 시각은 시작 시각보다 늦어야 합니다."
                )
                continue

            # 지정한 여행 가능 시간을 벗어나는지 확인합니다.
            if (
                window_start is not None
                and window_end is not None
                and (item_start < window_start or item_end > window_end)
            ):
                errors.append(
                    f"{label}: 여행 가능 시간({start_time}~{end_time})을 벗어났습니다."
                )

            # 앞선 모든 일정의 종료 시각과 비교합니다.
            if latest_end is not None and item_start < latest_end:
                errors.append(
                    f"{label}: 앞선 일정과 시간이 겹치거나 "
                    "시간순으로 배치되지 않았습니다."
                )

            latest_end = (
                max(latest_end, item_end)
                if latest_end is not None
                else item_end
            )

            # 표시한 소요시간이 실제 시간표의 길이와 일치하는지 확인합니다.
            duration = item.get("duration_minutes")

            if type(duration) is not int or duration != item_end - item_start:
                errors.append(
                    f"{label}: 소요시간은 시작·종료 시각의 차이인 "
                    f"{item_end - item_start}분과 일치해야 합니다."
                )

    return errors