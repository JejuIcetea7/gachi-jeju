"""운영시간 정리 값에서 방문 날짜의 개장·폐장·입장 마감을 읽습니다."""

import re
from datetime import date

from .closed_days import STATUS_CLOSED, STATUS_OPEN


ALWAYS_OPEN = "상시 개방"
OPENING_HOURS_ITEM_PATTERN = (
    r"(?:(\d{1,2})-(\d{1,2})월 )?(\d{2}:\d{2})-(\d{2}:\d{2})(?: 입장마감 (\d{2}:\d{2}))?"
)
TIME_PATTERN = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]"


def is_month_in_range(month: int, start_month: int, end_month: int) -> bool:
    """11-2월처럼 해를 넘기는 달 범위도 처리합니다."""
    if start_month <= end_month:
        return start_month <= month <= end_month
    return month >= start_month or month <= end_month


def _valid_time(value: str, *, closing: bool = False) -> bool:
    return (
        re.fullmatch(TIME_PATTERN, value) is not None
        or (closing and value == "24:00")
    )


def find_opening_hours(
    opening_hours: str | None,
    visit_date: date,
) -> dict[str, str | None] | None:
    """해당 날짜의 첫 유효 구간을 읽고, 확인할 수 없으면 None을 반환합니다.

    상시 개방·빈 값·해당 월 정보 없음·잘못된 값은 시각을 추정하지 않습니다.
    날짜를 넘기는 야간 운영은 이 형식만으로 해석하지 않습니다.
    폐장의 24:00은 해당 날짜가 끝나는 시각으로 허용합니다.
    """
    if not isinstance(opening_hours, str):
        return None
    if opening_hours.strip() in ("", ALWAYS_OPEN):
        return None

    for part in opening_hours.split(";"):
        match = re.fullmatch(OPENING_HOURS_ITEM_PATTERN, part.strip())
        if match is None:
            continue
        start_month, end_month, opening, closing, last_entry = match.groups()
        if start_month is not None:
            first, last = int(start_month), int(end_month)
            if not 1 <= first <= 12 or not 1 <= last <= 12:
                continue
            if not is_month_in_range(visit_date.month, first, last):
                continue

        if not _valid_time(opening) or not _valid_time(closing, closing=True):
            continue
        if opening >= closing:
            continue
        if last_entry is not None and (
            not _valid_time(last_entry)
            or not opening <= last_entry <= closing
        ):
            continue

        return {
            "opening_time": opening,
            "closing_time": closing,
            "last_entry_time": last_entry,
        }

    return None


def find_closing_time(opening_hours: str, visit_date: date) -> str | None:
    """기존 호출부에서 사용하는 폐장 시각 반환 형식을 유지합니다."""
    hours = find_opening_hours(opening_hours, visit_date)
    return None if hours is None else hours["closing_time"]


def check_visit_time_window(hours, start_time, end_time):
    """방문 시작·종료 시각을 개장, 입장 마감, 폐장과 각각 대조합니다."""
    last_entry = hours["last_entry_time"]
    closing = hours["closing_time"]
    before_opening = start_time < hours["opening_time"]
    after_last_entry = last_entry is not None and start_time > last_entry
    after_closing = start_time >= closing if end_time is None else end_time > closing
    return before_opening, after_last_entry, after_closing


def _clock_minutes(time_text: str) -> int:
    hour, minute = map(int, time_text.split(":"))
    return hour * 60 + minute


def _format_clock(total_minutes: int) -> str:
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def build_visit_time_reply(
    place_name: str, visit_date: date, arrival_time: str, duration_minutes: int | None,
    *, opening_hours: str | None, closed_day_status: str | None,
) -> str:
    """확인된 운영시간으로 방문 가능 여부를 판단하고 유효한 대안만 안내합니다."""
    date_label = f"{visit_date.month}월 {visit_date.day}일"
    if closed_day_status == STATUS_CLOSED:
        return f"{place_name}은(는) {date_label} 휴무로 등록되어 있습니다. 다른 날짜를 확인해 주세요."

    hours = find_opening_hours(opening_hours, visit_date)
    if hours is None and opening_hours != ALWAYS_OPEN:
        return f"{place_name}의 {date_label} 운영시간을 확인하지 못해 {arrival_time}에 방문할 수 있는지 판단할 수 없습니다."

    arrival_minutes = _clock_minutes(arrival_time)
    end_time = None
    if duration_minutes is not None:
        end_time = _format_clock(arrival_minutes + duration_minutes)

    if hours is None and end_time is not None and end_time > "24:00":
        return f"{place_name}은(는) 상시 개방으로 등록되어 있지만, 다음 날까지 이어지는 방문은 날짜별 운영 정보를 확인해 주세요."

    messages = [f"{place_name}의 {date_label} 등록 운영시간 기준입니다."]
    before_opening = after_last_entry = after_closing = False
    if hours is not None:
        before_opening, after_last_entry, after_closing = check_visit_time_window(
            hours, arrival_time, end_time,
        )

    if not (before_opening or after_last_entry or after_closing):
        if duration_minutes is None:
            messages.append(f"{arrival_time}에 입장할 수 있습니다.")
        else:
            messages.append(f"{arrival_time}부터 {duration_minutes}분 관람할 수 있습니다.")
    else:
        opening = hours["opening_time"]
        last_entry = hours["last_entry_time"]
        closing = hours["closing_time"]
        if before_opening:
            messages.append(f"{arrival_time}은 개장 시각 {opening} 전입니다.")
        if after_last_entry:
            messages.append(f"{arrival_time}은 입장 마감 {last_entry} 이후입니다.")
        if after_closing:
            if arrival_time >= closing:
                messages.append(f"{arrival_time}은 폐장 시각 {closing} 이후입니다.")
            else:
                messages.append(f"{arrival_time}부터 {duration_minutes}분 관람하면 폐장 시각 {closing}을 넘깁니다.")

        closing_minutes = _clock_minutes(closing)
        latest_arrival = closing_minutes - (duration_minutes or 1)
        if last_entry is not None:
            latest_arrival = min(latest_arrival, _clock_minutes(last_entry))

        if latest_arrival >= _clock_minutes(opening):
            latest_time = _format_clock(latest_arrival)
            purpose = "입장하려면"
            if duration_minutes is not None:
                purpose = f"{duration_minutes}분 관람을 유지하려면"
            messages.append(f"{purpose} {opening}부터 {latest_time}까지 도착해 주세요.")

        remaining_minutes = closing_minutes - arrival_minutes
        if after_closing and not before_opening and not after_last_entry and remaining_minutes > 0:
            messages.append(f"도착 시각을 유지하려면 관람 시간을 {remaining_minutes}분 이내로 줄여야 합니다.")

    if closed_day_status != STATUS_OPEN:
        messages.append("해당 날짜의 휴무 여부는 확인이 필요합니다.")
    return " ".join(messages)
