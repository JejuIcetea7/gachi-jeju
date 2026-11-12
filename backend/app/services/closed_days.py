"""장소의 휴무일(closed_days 정리 값)과 날짜로 그날 휴무인지 판정합니다.

closed_days 정리 값 형식 (scripts/build_places.py 의 CLOSED_DAY_PATTERNS 와 같음, 여러 개는 ';'로 이음)
- 연중무휴 (단독)
- 매주 월요일
- 매월 첫째 화요일 (첫째·둘째·셋째·넷째·다섯째·마지막)
- 1월 1일
- 설날, 추석 (당일) / 설날연휴, 추석연휴 (전날·당일·다음날)
- 법정공휴일
"""

import re
from datetime import date, timedelta

STATUS_CLOSED = "휴무"
STATUS_OPEN = "영업"
STATUS_UNKNOWN = "확인 안 됨"

WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]
WEEK_ORDER_NAMES = {"첫째": 1, "둘째": 2, "셋째": 3, "넷째": 4, "다섯째": 5}

# 음력 명절의 양력 날짜 (월력요항 기준, 2026-11 확인). 이 표 밖의 연도는 판정할 수 없다.
LUNAR_NEW_YEAR_DATES = {
    2026: date(2026, 2, 17),
    2027: date(2027, 2, 7),
    2028: date(2028, 1, 27),
}
CHUSEOK_DATES = {
    2026: date(2026, 9, 25),
    2027: date(2027, 9, 15),
    2028: date(2028, 10, 3),
}
BUDDHA_BIRTHDAY_DATES = {
    2026: date(2026, 5, 24),
    2027: date(2027, 5, 13),
    2028: date(2028, 5, 2),
}

# 양력 고정 공휴일 (월, 일). 대체공휴일·선거일·임시공휴일은 포함하지 않는다.
FIXED_PUBLIC_HOLIDAYS = [(1, 1), (3, 1), (5, 5), (6, 6), (8, 15), (10, 3), (10, 9), (12, 25)]


def list_holiday_season_dates(holiday_date):
    """명절 당일의 전날·당일·다음날"""
    season_dates = []
    for day_offset in [-1, 0, 1]:
        season_dates.append(holiday_date + timedelta(days=day_offset))
    return season_dates


def is_public_holiday(visit_date):
    """법정공휴일인지 (True/False). 음력 명절 날짜표에 없는 연도면 None"""
    for month, day in FIXED_PUBLIC_HOLIDAYS:
        if visit_date.month == month and visit_date.day == day:
            return True

    year = visit_date.year
    if year not in LUNAR_NEW_YEAR_DATES or year not in CHUSEOK_DATES or year not in BUDDHA_BIRTHDAY_DATES:
        return None

    lunar_holiday_dates = []
    lunar_holiday_dates.extend(list_holiday_season_dates(LUNAR_NEW_YEAR_DATES[year]))
    lunar_holiday_dates.extend(list_holiday_season_dates(CHUSEOK_DATES[year]))
    lunar_holiday_dates.append(BUDDHA_BIRTHDAY_DATES[year])
    return visit_date in lunar_holiday_dates


def is_nth_weekday_of_month(visit_date, week_order_name, weekday_index):
    if visit_date.weekday() != weekday_index:
        return False

    if week_order_name == "마지막":
        next_week_date = visit_date + timedelta(days=7)
        return next_week_date.month != visit_date.month

    week_order = WEEK_ORDER_NAMES[week_order_name]
    visit_week_order = (visit_date.day - 1) // 7 + 1
    return visit_week_order == week_order


def match_lunar_holiday(visit_date, holiday_dates_by_year, include_season):
    """음력 명절 항목이 맞는지 (True/False). 날짜표에 없는 연도면 None"""
    matched = None
    holiday_date = holiday_dates_by_year.get(visit_date.year)
    if holiday_date is None:
        return matched

    matched = visit_date == holiday_date
    if include_season:
        matched = visit_date in list_holiday_season_dates(holiday_date)
    return matched


def match_closed_day_item(closed_day_item, visit_date):
    """정리 값 항목 하나가 그날에 해당하는지 (True/False). 판정할 수 없으면 None"""
    weekly_match = re.fullmatch(r"매주 ([월화수목금토일])요일", closed_day_item)
    if weekly_match is not None:
        weekday_index = WEEKDAY_NAMES.index(weekly_match.group(1))
        return visit_date.weekday() == weekday_index

    monthly_match = re.fullmatch(r"매월 (첫째|둘째|셋째|넷째|다섯째|마지막) ([월화수목금토일])요일", closed_day_item)
    if monthly_match is not None:
        weekday_index = WEEKDAY_NAMES.index(monthly_match.group(2))
        return is_nth_weekday_of_month(visit_date, monthly_match.group(1), weekday_index)

    date_match = re.fullmatch(r"(\d{1,2})월 (\d{1,2})일", closed_day_item)
    if date_match is not None:
        month = int(date_match.group(1))
        day = int(date_match.group(2))
        return visit_date.month == month and visit_date.day == day

    if closed_day_item == "설날":
        return match_lunar_holiday(visit_date, LUNAR_NEW_YEAR_DATES, include_season=False)
    if closed_day_item == "설날연휴":
        return match_lunar_holiday(visit_date, LUNAR_NEW_YEAR_DATES, include_season=True)
    if closed_day_item == "추석":
        return match_lunar_holiday(visit_date, CHUSEOK_DATES, include_season=False)
    if closed_day_item == "추석연휴":
        return match_lunar_holiday(visit_date, CHUSEOK_DATES, include_season=True)
    if closed_day_item == "법정공휴일":
        return is_public_holiday(visit_date)

    return None


def judge_closed_on_date(closed_days, visit_date):
    """그날 '휴무' / '영업' / '확인 안 됨'

    - 빈칸(정보 없음) -> 확인 안 됨, 연중무휴 -> 영업
    - 항목 하나라도 맞으면 휴무
    - 맞는 항목이 없더라도 판정할 수 없는 항목(날짜표 밖 연도 등)이 있으면 확인 안 됨
    """
    status = STATUS_UNKNOWN
    if closed_days is None or closed_days.strip() == "":
        return status

    closed_day_items = closed_days.split(";")
    if closed_day_items == ["연중무휴"]:
        status = STATUS_OPEN
        return status

    has_unjudgeable_item = False
    for closed_day_item in closed_day_items:
        matched = match_closed_day_item(closed_day_item.strip(), visit_date)
        if matched is None:
            has_unjudgeable_item = True
            continue
        if matched:
            status = STATUS_CLOSED
            return status

    status = STATUS_OPEN
    if has_unjudgeable_item:
        status = STATUS_UNKNOWN
    return status
