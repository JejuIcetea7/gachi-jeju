"""장소의 운영시간(opening_hours 정리 값)과 방문 날짜로 그날 닫는 시각을 찾습니다.

opening_hours 정리 값 형식 (scripts/build_places.py 의 OPENING_HOURS_ITEM_PATTERN 과 같음, 여러 개는 ';'로 이음)
- 상시 개방
- 09:00-18:00 / 09:00-18:00 입장마감 17:00
- 계절별: 3-10월 09:00-18:00 입장마감 17:00;11-2월 09:00-17:00 (달 범위는 해를 넘길 수 있음)
"""

import re
from datetime import date

ALWAYS_OPEN = "상시 개방"
OPENING_HOURS_ITEM_PATTERN = (
    r"(?:(\d{1,2})-(\d{1,2})월 )?(\d{2}:\d{2})-(\d{2}:\d{2})(?: 입장마감 (\d{2}:\d{2}))?"
)


def is_month_in_range(month: int, start_month: int, end_month: int) -> bool:
    """달 범위 안인지. 11-2월처럼 해를 넘기는 범위도 처리합니다."""
    if start_month <= end_month:
        return start_month <= month <= end_month
    return month >= start_month or month <= end_month


def find_closing_time(opening_hours: str, visit_date: date) -> str | None:
    """그날 닫는 시각(HH:MM). 빈칸·상시 개방·해당 달이 없는 값이면 None"""
    closing_time = None
    if opening_hours == "" or opening_hours == ALWAYS_OPEN:
        return closing_time

    for opening_hours_item in opening_hours.split(";"):
        item_match = re.fullmatch(OPENING_HOURS_ITEM_PATTERN, opening_hours_item.strip())
        if item_match is None:
            continue

        start_month_text, end_month_text, _, item_closing_time, _ = item_match.groups()
        if start_month_text is not None:
            start_month = int(start_month_text)
            end_month = int(end_month_text)
            if not is_month_in_range(visit_date.month, start_month, end_month):
                continue

        closing_time = item_closing_time
        break
    return closing_time
