"""운영시간 정리 값에서 방문 날짜의 개장·폐장·입장 마감을 읽습니다."""

import re
from datetime import date


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