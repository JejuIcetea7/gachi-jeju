import re
from datetime import date, timedelta

from .closed_days import STATUS_CLOSED, WEEKDAY_NAMES, judge_closed_on_date
from .opening_hours import check_visit_time_window, find_opening_hours


_MEAL_CLOCK = r"(?:오전|오후|낮)?\s*\d{1,2}(?:\s*:\s*\d{2}|\s*시(?:\s*\d{1,2}\s*분)?)"
_MEAL_WINDOW = re.compile(rf"(?P<start>{_MEAL_CLOCK})\s*(?:부터|[~–-])\s*(?P<end>{_MEAL_CLOCK})(?:\s*까지)?")
_MEAL_WORD = r"(?:점심|저녁|아침|식사)"


def _read_meal_clock(value, previous_period=None):
    match = re.fullmatch(
        r"\s*(오전|오후|낮)?\s*(\d{1,2})(?:\s*:\s*(\d{2})|\s*시(?:\s*(\d{1,2})\s*분)?)",
        value,
    )
    period, hour, colon_minutes, written_minutes = match.groups()
    hour = int(hour)
    minute = int(colon_minutes or written_minutes or 0)
    if period is None and colon_minutes is None and 1 <= hour <= 12:
        period = previous_period
    if period and 1 <= hour <= 12:
        hour %= 12
        if period != "오전":
            hour += 12
    if hour > 23 or minute > 59:
        return None, period
    return f"{hour:02d}:{minute:02d}", period


def apply_requested_meal_window(plan: dict, message: str) -> list[str]:
    """식사에 바로 붙은 시각 범위 하나를 식사 항목 하나에 적용합니다."""
    windows = list(_MEAL_WINDOW.finditer(message))
    if len(windows) != 1:
        return []
    window = windows[0]
    before = message[:window.start()]
    after = message[window.end():]
    if not (
        re.search(rf"{_MEAL_WORD}(?:\s*(?:식사|시간))?\s*[은는을를만도]?\s*$", before)
        or re.match(rf"\s*(?:에\s*)?{_MEAL_WORD}", after)
    ):
        return []
    start, period = _read_meal_clock(window["start"])
    end, _ = _read_meal_clock(window["end"], period)
    if start is None or end is None or end <= start:
        return []

    meals = []
    for day in plan["days"]:
        for index, item in enumerate(day["items"]):
            if item.get("restaurant_ref") is not None:
                meals.append((day["items"], index))
    if len(meals) != 1:
        return []
    items, index = meals[0]
    if index >= 2:
        arrival = items[index - 2]
        waiting = items[index - 1]
        if (
            arrival["item_type"] == "move" and arrival.get("route_id")
            and waiting["item_type"] == "move" and waiting.get("route_id") is None
            and waiting["title"].strip().endswith("대기") and "→" not in waiting["title"]
            and waiting["start_time"] == arrival["end_time"]
            and waiting["end_time"] == start
        ):
            # 식당까지의 이동은 유지하고 일찍 도착한 뒤의 대기만 빈 구간으로 둡니다.
            items.pop(index - 1)
            index -= 1
    if (
        (index > 0 and items[index - 1]["end_time"] > start)
        or (index + 1 < len(items) and items[index + 1]["start_time"] < end)
    ):
        return [f"요청한 식사 시각 {start}~{end}이 앞뒤 일정과 겹칩니다. 이 식사 시각을 유지하고 이동·방문 시각을 다시 계산해 주세요."]

    meal = items[index]
    meal["start_time"] = start
    meal["end_time"] = end
    meal["duration_minutes"] = _to_minutes(end) - _to_minutes(start)
    return []


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
                reason = (
                    f"{label}: 소요시간은 시작·종료 시각의 차이인 "
                    f"{item_end - item_start}분과 일치해야 합니다."
                )
                if type(duration) is int and duration > 0:
                    reason += (
                        f" 현재 {item['start_time']}~{item['end_time']}에 "
                        f"duration_minutes={duration}를 적었습니다."
                    )
                    calculated_end = item_start + duration
                    if calculated_end < 24 * 60:
                        clock = f"{calculated_end // 60:02d}:{calculated_end % 60:02d}"
                        reason += (
                            f" 계산 참고: {item['start_time']} + {duration}분 = {clock}."
                        )
                    reason += (
                        " 사용자 고정 시각과 이번에 조회한 이동시간을 지키면서 "
                        "시작·종료 시각과 소요시간을 다시 계산하세요."
                    )
                errors.append(reason)

    return errors


def validate_plan_closed_days(
    plan: dict,
    start_date: date,
    closed_days_by_place_id: dict,
) -> list[str]:
    """일정을 수정하지 않고 휴무일에 방문하는 장소를 찾아 반환합니다.
    N일차 날짜는 start_date + (N - 1)일이고, 판정은 services.closed_days 와 같습니다."""
    errors = []

    for day in plan["days"]:
        visit_date = start_date + timedelta(days=day["day"] - 1)
        weekday_name = WEEKDAY_NAMES[visit_date.weekday()]

        for index, item in enumerate(day["items"], start=1):
            if item.get("item_type") != "visit":
                continue
            place_id = item.get("place_id")
            if place_id not in closed_days_by_place_id:
                continue

            closed_days = closed_days_by_place_id[place_id]
            status = judge_closed_on_date(closed_days, visit_date)
            if status != STATUS_CLOSED:
                continue

            label = f"{day['day']}일차 {index}번째 일정"
            errors.append(
                f"{label}: {item.get('title')}은(는) "
                f"{visit_date.isoformat()}({weekday_name}) 휴무일({closed_days})입니다."
            )

    return errors


def validate_plan_opening_hours(
    plan: dict,
    start_date: date,
    opening_hours_by_place_id: dict,
) -> list[str]:
    """개장 전 방문·입장 마감 이후 도착·폐장 이후 종료를 검사합니다.

    N일차 날짜의 달로 운영시간을 선택하며 입력 일정은 변경하지 않습니다.
    개장·입장 마감·폐장 정각은 허용하고, 미확인·상시 개방은 검사하지 않습니다.
    """
    errors = []

    for day in plan["days"]:
        visit_date = start_date + timedelta(days=day["day"] - 1)

        for index, item in enumerate(day["items"], start=1):
            if item.get("item_type") != "visit":
                continue
            place_id = item.get("place_id")
            hours = find_opening_hours(
                opening_hours_by_place_id.get(place_id), visit_date
            )
            if hours is None:
                continue

            start_time = item.get("start_time")
            end_time = item.get("end_time")
            try:
                _to_minutes(start_time)
                _to_minutes(end_time)
            except ValueError:
                continue

            opening_time = hours["opening_time"]
            closing_time = hours["closing_time"]
            last_entry_time = hours["last_entry_time"]
            label = (
                f"{day['day']}일차 {index}번째 일정: "
                f"{item.get('title')}은(는) {visit_date.isoformat()}"
            )
            before_opening, after_last_entry, after_closing = check_visit_time_window(
                hours, start_time, end_time,
            )
            if before_opening:
                errors.append(
                    f"{label} {opening_time}에 여는데 "
                    f"{start_time}에 방문을 시작합니다."
                )
            if after_last_entry:
                errors.append(
                    f"{label} 입장 마감은 {last_entry_time}인데 "
                    f"{start_time}에 방문을 시작합니다."
                )
            if after_closing:
                errors.append(
                    f"{label} {closing_time}에 닫는데 {end_time}에 끝납니다."
                )

    return errors
