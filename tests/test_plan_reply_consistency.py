import copy
import unittest

from backend.app.services.plan_reply_consistency import validate_plan_reply_consistency


OPTIONS = {"trip_days": 1, "return_to_origin": True, "lodging_status": None}


def make_plan(end="12:56", assumptions=None):
    return {
        "assumptions": list(assumptions or []),
        "days": [{
            "day": 1,
            "items": [
                {"item_type": "visit", "start_time": "10:27", "end_time": "11:27"},
                {"item_type": "move", "start_time": "11:27", "end_time": end},
            ],
        }],
    }


def check(reply, plan=None, options=None, user_message="관광 일정을 만들어 주세요."):
    return validate_plan_reply_consistency(
        reply, make_plan() if plan is None else plan,
        options=OPTIONS if options is None else options,
        user_message=user_message,
    )


class PlanReplyConsistencyTests(unittest.TestCase):
    def test_reported_transit_return_conflict_is_rejected(self):
        errors = check("버스 소요시간을 고려하면 귀환은 약 14:30입니다.")
        self.assertEqual(len(errors), 1)
        self.assertIn("12:56", errors[0])
        self.assertIn("14:30", errors[0])

    def test_matching_return_and_operating_hours_are_allowed(self):
        self.assertEqual(check("운영시간은 09:00~18:00입니다. 귀환은 12:56입니다."), [])

    def test_time_before_return_word_is_checked(self):
        self.assertEqual(check("12:56에 귀환합니다."), [])
        self.assertTrue(check("14:30에 귀환합니다."))

    def test_return_range_uses_arrival_time(self):
        self.assertEqual(check("귀환편은 11:27~12:56입니다."), [])
        self.assertTrue(check("귀환편은 11:27~14:30입니다."))

    def test_korean_clock_and_meridiem_are_checked(self):
        self.assertEqual(check("귀환 예정 시각은 오후 12시 56분입니다."), [])
        self.assertTrue(check("귀환은 오후 2시 30분입니다."))
        self.assertTrue(check("귀환은 오전 12:56입니다."))

    def test_invalid_clock_is_rejected(self):
        self.assertTrue(check("귀환은 25:30입니다."))
        self.assertTrue(check("귀환은 12:99입니다."))

    def test_boundaries_and_negative_statements_are_not_guessed(self):
        for reply in (
            "귀환은 14:30 이전입니다.",
            "귀환은 14:30이 아닙니다.",
            "박물관은 14:30에 입장할 수 없습니다.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(check(reply), [])

    def test_question_without_plan_is_skipped(self):
        self.assertEqual(validate_plan_reply_consistency(
            "귀환은 14:30으로 할까요?", None, options=OPTIONS,
        ), [])

    def test_return_not_selected_is_not_inferred(self):
        self.assertEqual(check(
            "귀환은 14:30입니다.", options={**OPTIONS, "return_to_origin": False},
        ), [])

    def test_empty_day_cannot_supply_return_time(self):
        plan = {"assumptions": [], "days": [{"day": 1, "items": []}]}
        self.assertTrue(check("귀환은 14:30입니다.", plan))

    def test_assumption_return_time_is_also_checked(self):
        plan = make_plan(assumptions=["귀환은 14:30입니다."])
        self.assertIn("assumptions[0]", check("일정을 만들었습니다.", plan)[0])

    def test_explicit_days_and_ambiguous_multiday_reply(self):
        plan = make_plan()
        plan["days"].append({"day": 2, "items": [{
            "item_type": "move", "start_time": "14:00", "end_time": "14:30",
        }]})
        options = {**OPTIONS, "trip_days": 2, "lodging_status": "not_needed"}
        self.assertEqual(check("1일차 귀환은 12:56입니다.\n2일차 귀환은 14:30입니다.", plan, options), [])
        self.assertTrue(check("1일차 귀환은 14:30입니다.", plan, options))
        self.assertEqual(check("마지막 날 귀환은 14:30입니다.", plan, options), [])
        self.assertEqual(check("귀환은 15:00입니다.", plan, options), [])

    def test_day_trip_lodging_memo_is_rejected(self):
        for memo in (
            "숙소 정보가 제공되지 않아 숙소는 미정으로 두었습니다.",
            "숙소 정보가 없어 숙박은 미정으로 두었습니다. 당일 일정입니다.",
        ):
            with self.subTest(memo=memo):
                self.assertTrue(check(memo))
                self.assertTrue(check("일정입니다.", make_plan(assumptions=[memo])))

    def test_explicit_lodging_question_is_preserved(self):
        self.assertEqual(check(
            "숙소는 미정입니다.", user_message="숙소 상태도 알려 주세요.",
        ), [])

    def test_multiday_undecided_lodging_is_allowed(self):
        self.assertEqual(check(
            "숙소는 미정이며 선택 후 이동을 다시 계산합니다.",
            options={**OPTIONS, "trip_days": 2, "lodging_status": "undecided"},
        ), [])

    def test_not_needed_lodging_rejects_undecided_note(self):
        self.assertTrue(check(
            "숙소는 미정입니다.",
            options={**OPTIONS, "trip_days": 2, "lodging_status": "not_needed"},
        ))

    def test_accessibility_warning_and_inputs_are_preserved(self):
        plan = make_plan(assumptions=["장애인 화장실과 휠체어 동선은 미확인입니다."])
        options = copy.deepcopy(OPTIONS)
        before = copy.deepcopy((plan, options))
        self.assertEqual(check("귀환은 12:56입니다.", plan, options), [])
        self.assertEqual((plan, options), before)

    def test_departure_time_is_not_the_visit_start_time(self):
        plan = {"assumptions": [
            "방문 시작 시각은 지정되지 않아 10:00으로 제안했습니다.",
        ], "days": [{"day": 1, "items": [
            {"item_type": "move", "start_time": "10:00", "end_time": "10:05"},
            {"item_type": "visit", "start_time": "10:05", "end_time": "10:35"},
        ]}]}
        errors = check("일정입니다.", plan, {**OPTIONS, "return_to_origin": False})
        self.assertEqual(len(errors), 1)
        self.assertIn("10:05", errors[0])
        self.assertIn("10:00", errors[0])

    def test_correct_departure_and_visit_labels_are_allowed(self):
        plan = make_plan(assumptions=[
            "출발 시각은 지정되지 않아 10:00으로 제안했습니다.",
            "방문 시작 시각은 10:27입니다.",
            "방문 시작 시각은 지정되지 않아 10:00 출발로 제안했습니다.",
        ])
        self.assertEqual(check("일정입니다.", plan), [])

    def test_ambiguous_visit_and_explicit_day_are_distinguished(self):
        plan = make_plan()
        plan["days"][0]["items"].append({
            "item_type": "visit", "start_time": "14:00", "end_time": "15:00",
        })
        self.assertEqual(check("방문 시작 시각은 10:00입니다.", plan), [])
        self.assertEqual(check("방문 시작 시각은 10:00~11:00입니다.", plan), [])
        plan["days"][0]["items"].pop()
        plan["days"].append({"day": 2, "items": [{
            "item_type": "visit", "start_time": "14:00", "end_time": "15:00",
        }]})
        options = {**OPTIONS, "trip_days": 2, "return_to_origin": False}
        self.assertEqual(check(
            "1일차 방문 시작 시각은 10:27입니다.\n2일차 방문 시작 시각은 14:00입니다.",
            plan, options,
        ), [])
        self.assertTrue(check("1일차 방문 시작 시각은 10:00입니다.", plan, options))

    def test_excluded_visit_time_is_not_treated_as_a_scheduled_visit(self):
        plan = {"assumptions": [], "days": [{"day": 1, "items": []}]}
        self.assertEqual(check(
            "방문 시작 시각은 17:40이지만 입장 마감 이후라 제외했습니다.", plan,
        ), [])

    def test_origin_return_phrase_uses_arrival_not_departure(self):
        plan = {"assumptions": [], "days": [
            {"day": 1, "items": [{"item_type": "visit", "start_time": "09:21",
                                 "end_time": "10:21"}]},
            {"day": 2, "items": [
                {"item_type": "visit", "start_time": "10:00", "end_time": "11:00"},
                {"item_type": "move", "start_time": "11:00", "end_time": "11:24"},
            ]},
        ]}
        options = {**OPTIONS, "departure": "제주빅데이터센터", "trip_days": 2,
                   "lodging_status": "undecided"}
        reply = "2일차에는 10:00에 박물관 관람을 시작해 11:00에 센터로 돌아옵니다."
        errors = check(reply, plan, options)
        self.assertEqual(len(errors), 1)
        self.assertIn("11:00", errors[0])
        self.assertIn("11:24", errors[0])
        self.assertEqual(check(reply.replace("11:00에", "11:24에"), plan, options), [])

    def test_origin_return_phrase_checks_explicit_names_and_korean_clocks(self):
        options = {**OPTIONS, "departure": "제주빅데이터센터"}
        for reply in (
            "12:56에 제주빅데이터센터로 돌아옵니다.",
            "12:56에 같은 센터로 돌아와요.",
            "오후 12시 56분에 출발지로 돌아오겠습니다.",
            "12:56경 센터로 돌아올 예정입니다.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(check(reply, options=options), [])
                wrong = reply.replace("12:56", "14:30").replace("12시 56분", "2시 30분")
                self.assertTrue(check(wrong, options=options))

    def test_origin_return_phrase_does_not_guess_boundaries_negatives_or_departures(self):
        options = {**OPTIONS, "departure": "제주빅데이터센터"}
        for reply in (
            "14:30 이전에 센터로 돌아옵니다.",
            "14:30 이후에 센터로 돌아옵니다.",
            "14:30에 센터로 돌아오지 않습니다.",
            "14:30에 센터로 돌아올 예정이 아닙니다.",
            "14:30에 센터로 돌아오기 위해 출발합니다.",
            "14:30에 센터로 돌아오는 이동을 시작합니다.",
            "박물관에 14:30에 도착합니다.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(check(reply, options=options), [])

    def test_origin_return_phrase_ignores_other_destinations_and_center_shorthand(self):
        options = {**OPTIONS, "departure": "제주버스터미널"}
        for reply in (
            "14:30에 숙소로 돌아옵니다.",
            "14:30에 다른 센터로 돌아옵니다.",
            "14:30에 센터로 돌아옵니다.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(check(reply, options=options), [])
        self.assertTrue(check("14:30에 제주버스터미널로 돌아옵니다.", options=options))
        self.assertTrue(check("14:30에 출발지로 돌아옵니다.", options=options))

    def test_origin_return_phrase_escapes_special_characters_in_place_name(self):
        options = {**OPTIONS, "departure": "제주센터(본관)"}
        self.assertEqual(check("12:56에 제주센터(본관)으로 돌아옵니다.", options=options), [])
        self.assertTrue(check("14:30에 제주센터(본관)으로 돌아옵니다.", options=options))
        self.assertEqual(check("14:30에 제주센터본관으로 돌아옵니다.", options=options), [])

    def test_origin_return_phrase_respects_explicit_days_and_ambiguity(self):
        plan = make_plan()
        plan["days"].append({"day": 2, "items": [
            {"item_type": "move", "start_time": "14:00", "end_time": "14:30"},
        ]})
        options = {**OPTIONS, "trip_days": 2, "lodging_status": "not_needed"}
        self.assertEqual(check(
            "1일차 12:56에 출발지로 돌아옵니다.\n2일차 14:30에 출발지로 돌아옵니다.",
            plan, options,
        ), [])
        self.assertTrue(check("1일차 14:30에 출발지로 돌아옵니다.", plan, options))
        self.assertEqual(check("14:00에 출발지로 돌아옵니다.", plan, options), [])

    def test_origin_return_phrase_checks_assumptions_without_changing_inputs(self):
        plan = make_plan(assumptions=["14:30에 출발지로 돌아옵니다."])
        options = copy.deepcopy(OPTIONS)
        before = copy.deepcopy((plan, options))
        errors = check("일정입니다.", plan, options)
        self.assertTrue(errors)
        self.assertIn("assumptions[0]", errors[0])
        self.assertEqual((plan, options), before)
        self.assertEqual(check("일정입니다.", plan, {**options, "return_to_origin": False}), [])


if __name__ == "__main__":
    unittest.main()