import copy
import unittest

from backend.app.services.plan_reply_consistency import validate_plan_reply_consistency


OPTIONS = {
    "departure": "제주빅데이터센터", "trip_days": 2,
    "lodging_status": "undecided", "return_to_origin": True,
}


def make_plan(assumptions=None):
    return {
        "assumptions": list(assumptions or []),
        "days": [
            {"day": 1, "items": [
                {"item_type": "visit", "start_time": "09:18", "end_time": "10:18"},
            ]},
            {"day": 2, "items": [
                {"item_type": "visit", "start_time": "10:03", "end_time": "11:03"},
                {"item_type": "move", "start_time": "11:03", "end_time": "11:25"},
            ]},
        ],
    }


def check(reply, *, plan=None, options=None):
    return validate_plan_reply_consistency(
        reply, make_plan() if plan is None else plan,
        options=OPTIONS if options is None else options,
    )


class OriginArrivalReplyTests(unittest.TestCase):
    def test_recorded_destination_before_time_conflict_is_detected(self):
        errors = check(
            "2일차는 호텔 위치에서 출발해 박물관을 10:03~11:03 방문하고, "
            "제주빅데이터센터에 11:37 도착합니다."
        )
        self.assertTrue(errors)
        self.assertIn("11:37", errors[0])
        self.assertIn("11:25", errors[0])

    def test_matching_arrival_in_both_word_orders_is_allowed(self):
        for reply in (
            "2일차 제주빅데이터센터에 11:25 도착합니다.",
            "2일차 11:25에 제주빅데이터센터에 도착합니다.",
            "2일차 센터에는 11:25에 도착해요.",
            "2일차 같은 센터에 오전 11시 25분 도착합니다.",
            "2일차 원래 출발지로 11:25에 도착할 예정입니다.",
            "2일차 제주빅데이터센터에 11:03~11:25 도착 예정입니다.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(check(reply), [])

    def test_wrong_arrival_in_both_word_orders_is_detected(self):
        for reply in (
            "2일차 제주빅데이터센터에 11:37 도착합니다.",
            "2일차 11:37에 제주빅데이터센터에 도착합니다.",
            "2일차 같은 센터에 오전 11시 37분 도착합니다.",
        ):
            with self.subTest(reply=reply):
                self.assertTrue(check(reply))

    def test_arrival_in_assumptions_is_checked(self):
        errors = check("일정입니다.", plan=make_plan([
            "2일차 제주빅데이터센터에 11:37 도착합니다.",
        ]))
        self.assertTrue(errors)
        self.assertIn("assumptions[0]", errors[0])

    def test_other_destinations_and_unrelated_center_are_ignored(self):
        for reply in (
            "호텔에 11:37 도착합니다.",
            "박물관에 11:37 도착합니다.",
            "다른 센터에 11:37 도착합니다.",
            "다른   센터에 11:37 도착합니다.",
            "11:37에 다른 센터에 도착합니다.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(check(reply), [])
        self.assertEqual(check(
            "센터에 11:37 도착합니다.",
            options={**OPTIONS, "departure": "제주버스터미널"},
        ), [])

    def test_boundaries_negatives_and_departures_are_not_arrivals(self):
        for reply in (
            "센터에 11:37 이전에 도착합니다.",
            "센터에 11:37 이후에 도착합니다.",
            "11:37 이전에 센터에 도착합니다.",
            "센터에 11:37 도착하지 않습니다.",
            "센터에 11:37 도착할 예정이 아닙니다.",
            "센터에서 11:37 출발합니다.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(check(reply), [])

    def test_unselected_return_does_not_infer_origin_arrival(self):
        self.assertEqual(check(
            "센터에 11:37 도착합니다.",
            options={**OPTIONS, "return_to_origin": False},
        ), [])

    def test_explicit_day_is_checked_and_ambiguous_daily_returns_are_skipped(self):
        plan = make_plan()
        plan["days"][0]["items"].append({
            "item_type": "move", "start_time": "10:18", "end_time": "10:40",
        })
        options = {**OPTIONS, "lodging_status": "not_needed"}
        self.assertEqual(check("1일차 센터에 10:40 도착합니다.", plan=plan, options=options), [])
        self.assertTrue(check("2일차 센터에 10:40 도착합니다.", plan=plan, options=options))
        self.assertEqual(check("센터에 11:37 도착합니다.", plan=plan, options=options), [])

    def test_special_characters_in_departure_name_are_literal(self):
        options = {**OPTIONS, "departure": "제주센터(본관)"}
        self.assertEqual(check("제주센터(본관)에 11:25 도착합니다.", options=options), [])
        self.assertTrue(check("제주센터(본관)에 11:37 도착합니다.", options=options))
        self.assertEqual(check("제주센터본관에 11:37 도착합니다.", options=options), [])

    def test_inputs_are_preserved(self):
        plan, options = make_plan(), copy.deepcopy(OPTIONS)
        before = copy.deepcopy((plan, options))
        check("센터에 11:37 도착합니다.", plan=plan, options=options)
        self.assertEqual((plan, options), before)


if __name__ == "__main__":
    unittest.main()