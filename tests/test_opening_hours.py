import unittest
from datetime import date

from backend.app.services.opening_hours import find_closing_time
from backend.app.services.plan_validation import validate_plan_opening_hours

SEASONAL_HOURS = "3-10월 09:00-18:00 입장마감 17:00;11-2월 09:00-17:00 입장마감 16:00"


def make_visit_plan(place_id, start_time, end_time):
    return {"days": [{"day": 1, "items": [{
        "item_type": "visit",
        "title": "테스트 장소",
        "place_id": place_id,
        "start_time": start_time,
        "end_time": end_time,
    }]}]}


class FindClosingTimeTests(unittest.TestCase):
    def test_single_time(self):
        self.assertEqual(find_closing_time("09:00-18:00 입장마감 17:30", date(2026, 12, 1)), "18:00")

    def test_seasonal_time_by_month(self):
        self.assertEqual(find_closing_time(SEASONAL_HOURS, date(2026, 5, 1)), "18:00")
        # 11-2월은 해를 넘기는 범위
        self.assertEqual(find_closing_time(SEASONAL_HOURS, date(2027, 1, 10)), "17:00")

    def test_always_open_and_blank_have_no_closing_time(self):
        self.assertIsNone(find_closing_time("상시 개방", date(2026, 12, 1)))
        self.assertIsNone(find_closing_time("", date(2026, 12, 1)))


class ValidatePlanOpeningHoursTests(unittest.TestCase):
    def test_visit_ending_after_closing_time_is_error(self):
        plan = make_visit_plan("P_TEST", "16:30", "17:30")

        errors = validate_plan_opening_hours(plan, date(2026, 12, 1), {"P_TEST": SEASONAL_HOURS})

        self.assertEqual(errors, ["1일차 1번째 일정: 테스트 장소은(는) 2026-12-01 17:00에 닫는데 17:30에 끝납니다."])

    def test_visit_ending_at_closing_time_is_allowed(self):
        plan = make_visit_plan("P_TEST", "16:00", "17:00")

        errors = validate_plan_opening_hours(plan, date(2026, 12, 1), {"P_TEST": SEASONAL_HOURS})

        self.assertEqual(errors, [])

    def test_unknown_or_always_open_place_is_skipped(self):
        plan = make_visit_plan("P_TEST", "22:00", "23:00")

        self.assertEqual(validate_plan_opening_hours(plan, date(2026, 12, 1), {"P_TEST": "상시 개방"}), [])
        self.assertEqual(validate_plan_opening_hours(plan, date(2026, 12, 1), {}), [])


if __name__ == "__main__":
    unittest.main()
