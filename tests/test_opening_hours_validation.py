import copy
import unittest
from datetime import date

from backend.app.services.opening_hours import find_opening_hours
from backend.app.services.plan_validation import (
    validate_plan_opening_hours,
    validate_plan_times,
)


DAY = date(2026, 11, 20)
HOURS = {"TEST": "09:00-18:00 입장마감 17:00"}


def make_plan(start="09:00", end="10:00"):
    return {"days": [{"day": 1, "items": [{
        "item_type": "visit",
        "place_id": "TEST",
        "title": "검증용 장소",
        "start_time": start,
        "end_time": end,
    }]}]}


class OpeningHoursParserTests(unittest.TestCase):
    def test_reads_opening_closing_and_last_entry(self):
        self.assertEqual(find_opening_hours(HOURS["TEST"], DAY), {
            "opening_time": "09:00",
            "closing_time": "18:00",
            "last_entry_time": "17:00",
        })

    def test_missing_last_entry_is_not_invented(self):
        hours = find_opening_hours("09:00-18:00", DAY)
        self.assertIsNone(hours["last_entry_time"])

    def test_seasonal_month_boundaries_and_year_crossing(self):
        text = (
            "3-10월 09:00-18:00 입장마감 17:00;"
            "11-2월 08:00-17:00 입장마감 16:00"
        )
        for year, month, opening, closing, entry in [
            (2026, 3, "09:00", "18:00", "17:00"),
            (2026, 10, "09:00", "18:00", "17:00"),
            (2026, 11, "08:00", "17:00", "16:00"),
            (2026, 12, "08:00", "17:00", "16:00"),
            (2027, 1, "08:00", "17:00", "16:00"),
            (2027, 2, "08:00", "17:00", "16:00"),
        ]:
            with self.subTest(year=year, month=month):
                self.assertEqual(find_opening_hours(text, date(year, month, 1)), {
                    "opening_time": opening,
                    "closing_time": closing,
                    "last_entry_time": entry,
                })

    def test_unknown_values_do_not_produce_times(self):
        for text in (None, "", " ", "상시 개방", "확인 안 됨", "unknown", 123):
            with self.subTest(text=text):
                self.assertIsNone(find_opening_hours(text, DAY))

    def test_uncovered_month_remains_unknown(self):
        self.assertIsNone(find_opening_hours("3-10월 09:00-18:00", DAY))

    def test_invalid_clock_values_are_not_used(self):
        for text in (
            "25:00-26:00",
            "09:70-18:00",
            "09:00-24:30",
            "24:00-24:00",
            "09:00-18:00 입장마감 99:00",
        ):
            with self.subTest(text=text):
                self.assertIsNone(find_opening_hours(text, DAY))

    def test_invalid_months_are_not_used(self):
        for text in ("0-12월 09:00-18:00", "1-13월 09:00-18:00"):
            with self.subTest(text=text):
                self.assertIsNone(find_opening_hours(text, DAY))

    def test_overnight_and_zero_length_ranges_are_not_guessed(self):
        for text in ("22:00-06:00", "09:00-09:00"):
            with self.subTest(text=text):
                self.assertIsNone(find_opening_hours(text, DAY))

    def test_last_entry_outside_operating_hours_is_not_used(self):
        for text in (
            "09:00-18:00 입장마감 08:00",
            "09:00-18:00 입장마감 19:00",
        ):
            with self.subTest(text=text):
                self.assertIsNone(find_opening_hours(text, DAY))

    def test_closing_at_midnight_is_supported(self):
        self.assertEqual(
            find_opening_hours("09:00-24:00", DAY)["closing_time"], "24:00"
        )

    def test_whitespace_around_seasonal_parts_is_accepted(self):
        text = " 3-10월 09:00-18:00 ; 11-2월 10:00-17:00 "
        self.assertEqual(find_opening_hours(text, DAY)["opening_time"], "10:00")


class OpeningHoursValidationTests(unittest.TestCase):
    def test_opening_last_entry_and_closing_boundaries(self):
        for start, end, count, fragment in [
            ("08:59", "09:59", 1, "09:00에 여는데"),
            ("09:00", "10:00", 0, ""),
            ("16:59", "17:59", 0, ""),
            ("17:00", "18:00", 0, ""),
            ("17:01", "17:31", 1, "입장 마감은 17:00"),
            ("17:00", "18:01", 1, "18:00에 닫는데"),
            ("17:30", "18:30", 2, "입장 마감은 17:00"),
            ("08:00", "19:00", 2, "09:00에 여는데"),
        ]:
            with self.subTest(start=start, end=end):
                errors = validate_plan_opening_hours(
                    make_plan(start, end), DAY, HOURS
                )
                self.assertEqual(len(errors), count)
                if fragment:
                    self.assertTrue(any(fragment in error for error in errors))

    def test_absent_last_entry_is_not_invented(self):
        self.assertEqual(validate_plan_opening_hours(
            make_plan("17:30", "18:00"), DAY, {"TEST": "09:00-18:00"},
        ), [])

    def test_each_day_uses_its_own_month(self):
        plan = make_plan("17:00", "17:30")
        plan["days"].append({
            "day": 2, "items": copy.deepcopy(plan["days"][0]["items"]),
        })
        hours = {
            "TEST": (
                "3-10월 09:00-18:00 입장마감 17:00;"
                "11-2월 09:00-17:00 입장마감 16:00"
            )
        }
        errors = validate_plan_opening_hours(plan, date(2026, 10, 31), hours)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(
            "2일차" in error and "2026-11-01" in error for error in errors
        ))

    def test_year_boundary_keeps_winter_hours(self):
        plan = make_plan()
        plan["days"].append({
            "day": 2, "items": copy.deepcopy(plan["days"][0]["items"]),
        })
        self.assertEqual(validate_plan_opening_hours(
            plan, date(2026, 12, 31),
            {"TEST": "3-10월 10:00-18:00;11-2월 09:00-17:00"},
        ), [])

    def test_unknown_or_always_open_hours_are_skipped(self):
        for hours in (
            {}, {"TEST": ""}, {"TEST": None}, {"TEST": "상시 개방"},
            {"TEST": "확인 안 됨"}, {"TEST": "잘못된 값"},
            {"TEST": "3-10월 09:00-18:00"},
        ):
            with self.subTest(hours=hours):
                self.assertEqual(validate_plan_opening_hours(
                    make_plan("22:00", "23:00"), DAY, hours,
                ), [])

    def test_visit_before_midnight_closing_is_allowed(self):
        self.assertEqual(validate_plan_opening_hours(
            make_plan("23:00", "23:59"), DAY, {"TEST": "09:00-24:00"},
        ), [])

    def test_invalid_schedule_times_are_rejected_by_time_validation(self):
        for start, end in (
            (None, "10:00"), ("9:00", "10:00"),
            ("09:00", "25:00"), ("09:00", None),
        ):
            with self.subTest(start=start, end=end):
                plan = make_plan(start, end)
                self.assertTrue(validate_plan_times(plan))
                self.assertEqual(validate_plan_opening_hours(plan, DAY, HOURS), [])

    def test_moves_and_empty_days_need_no_facility_times(self):
        plan = {"days": [
            {"day": 1, "items": [{"item_type": "move"}]},
            {"day": 2, "items": []},
        ]}
        self.assertEqual(validate_plan_opening_hours(plan, DAY, HOURS), [])

    def test_inputs_are_preserved(self):
        plan = make_plan("17:30", "18:30")
        hours = copy.deepcopy(HOURS)
        before = copy.deepcopy((plan, hours))
        validate_plan_opening_hours(plan, DAY, hours)
        self.assertEqual((plan, hours), before)


if __name__ == "__main__":
    unittest.main()