import unittest
from datetime import date

from backend.app.services.closed_days import judge_closed_on_date
from backend.app.services.plan_validation import validate_plan_closed_days


class JudgeClosedOnDateTests(unittest.TestCase):
    def test_empty_is_unknown(self):
        self.assertEqual(judge_closed_on_date("", date(2026, 12, 14)), "확인 안 됨")
        self.assertEqual(judge_closed_on_date(None, date(2026, 12, 14)), "확인 안 됨")

    def test_open_all_year(self):
        self.assertEqual(judge_closed_on_date("연중무휴", date(2026, 1, 1)), "영업")

    def test_weekly(self):
        # 2026-12-14는 월요일
        self.assertEqual(judge_closed_on_date("매주 월요일", date(2026, 12, 14)), "휴무")
        self.assertEqual(judge_closed_on_date("매주 월요일", date(2026, 12, 15)), "영업")

    def test_monthly_nth_weekday(self):
        # 2026-12: 첫째 화요일 12-01, 둘째 월요일 12-14, 넷째 월요일 12-28(마지막 월요일)
        self.assertEqual(judge_closed_on_date("매월 첫째 화요일", date(2026, 12, 1)), "휴무")
        self.assertEqual(judge_closed_on_date("매월 첫째 화요일", date(2026, 12, 8)), "영업")
        closed_days = "매월 둘째 월요일;매월 넷째 월요일"
        self.assertEqual(judge_closed_on_date(closed_days, date(2026, 12, 14)), "휴무")
        self.assertEqual(judge_closed_on_date(closed_days, date(2026, 12, 21)), "영업")
        self.assertEqual(judge_closed_on_date(closed_days, date(2026, 12, 28)), "휴무")
        self.assertEqual(judge_closed_on_date("매월 마지막 월요일", date(2026, 12, 28)), "휴무")
        self.assertEqual(judge_closed_on_date("매월 마지막 월요일", date(2026, 12, 21)), "영업")

    def test_fixed_date(self):
        self.assertEqual(judge_closed_on_date("1월 1일", date(2027, 1, 1)), "휴무")
        self.assertEqual(judge_closed_on_date("4월 5일", date(2027, 4, 5)), "휴무")
        self.assertEqual(judge_closed_on_date("1월 1일", date(2027, 1, 2)), "영업")

    def test_lunar_holiday_day_and_season(self):
        # 2027 설날 2-07, 추석 2026-09-25
        self.assertEqual(judge_closed_on_date("설날", date(2027, 2, 7)), "휴무")
        self.assertEqual(judge_closed_on_date("설날", date(2027, 2, 6)), "영업")
        self.assertEqual(judge_closed_on_date("설날연휴", date(2027, 2, 6)), "휴무")
        self.assertEqual(judge_closed_on_date("설날연휴", date(2027, 2, 8)), "휴무")
        self.assertEqual(judge_closed_on_date("설날연휴", date(2027, 2, 9)), "영업")
        self.assertEqual(judge_closed_on_date("추석연휴", date(2026, 9, 24)), "휴무")

    def test_public_holiday(self):
        self.assertEqual(judge_closed_on_date("법정공휴일", date(2026, 12, 25)), "휴무")
        self.assertEqual(judge_closed_on_date("법정공휴일", date(2027, 5, 13)), "휴무")  # 부처님오신날
        self.assertEqual(judge_closed_on_date("법정공휴일", date(2027, 5, 14)), "영업")

    def test_year_outside_table_is_unknown(self):
        self.assertEqual(judge_closed_on_date("설날", date(2030, 2, 3)), "확인 안 됨")
        # 다른 항목으로 휴무가 확인되면 휴무
        self.assertEqual(judge_closed_on_date("매주 월요일;설날", date(2030, 2, 4)), "휴무")

    def test_real_museum_value(self):
        closed_days = "매주 월요일;1월 1일;설날;추석"
        self.assertEqual(judge_closed_on_date(closed_days, date(2026, 12, 14)), "휴무")
        self.assertEqual(judge_closed_on_date(closed_days, date(2026, 12, 15)), "영업")
        self.assertEqual(judge_closed_on_date(closed_days, date(2026, 9, 25)), "휴무")


def make_visit(place_id, title):
    return {"item_type": "visit", "place_id": place_id, "title": title}


class PlanClosedDaysValidationTests(unittest.TestCase):
    def setUp(self):
        self.closed_days_by_place_id = {
            "MUSEUM": "매주 월요일;1월 1일;설날;추석",
            "PARK": "연중무휴",
            "UNKNOWN": "",
        }

    def test_monday_museum_visit_is_error(self):
        plan = {"days": [{"day": 1, "items": [
            make_visit("PARK", "공원"),
            {"item_type": "move", "place_id": None, "title": "공원 → 박물관"},
            make_visit("MUSEUM", "박물관"),
        ]}]}

        errors = validate_plan_closed_days(plan, date(2026, 12, 14), self.closed_days_by_place_id)

        self.assertEqual(len(errors), 1)
        self.assertIn("1일차 3번째 일정", errors[0])
        self.assertIn("2026-12-14(월)", errors[0])

    def test_day_number_sets_date(self):
        # 2일차가 월요일이 되도록 일요일 출발
        plan = {"days": [
            {"day": 1, "items": [make_visit("MUSEUM", "박물관")]},
            {"day": 2, "items": [make_visit("MUSEUM", "박물관")]},
        ]}

        errors = validate_plan_closed_days(plan, date(2026, 12, 13), self.closed_days_by_place_id)

        self.assertEqual(len(errors), 1)
        self.assertIn("2일차", errors[0])

    def test_open_unknown_and_missing_places_pass(self):
        plan = {"days": [{"day": 1, "items": [
            make_visit("MUSEUM", "박물관"),
            make_visit("UNKNOWN", "미확인 장소"),
            make_visit("NOT_IN_DB", "DB에 없는 장소"),
        ]}]}

        errors = validate_plan_closed_days(plan, date(2026, 12, 15), self.closed_days_by_place_id)

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
