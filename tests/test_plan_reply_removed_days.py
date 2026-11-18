import copy
import unittest

from backend.app.services.plan_reply_consistency import validate_plan_reply_consistency


OPTIONS = {
    "departure": "제주빅데이터센터", "trip_days": 1,
    "return_to_origin": True, "lodging_status": "not_needed",
}
REPLY = (
    "여행을 2026년 11월 20일 당일로 변경하고 숙소와 2일차를 제거했습니다. "
    "제주빅데이터센터에서 09:00에 출발해 한라수목원을 09:18~10:18 방문한 뒤, "
    "10:40에 센터로 돌아옵니다."
)


def make_plan(assumptions=None):
    return {
        "assumptions": list(assumptions or []),
        "days": [{"day": 1, "items": [
            {"item_type": "visit", "start_time": "09:18", "end_time": "10:18"},
            {"item_type": "move", "start_time": "10:18", "end_time": "10:40"},
        ]}],
    }


def check(text, *, plan=None, options=None):
    return validate_plan_reply_consistency(
        text, make_plan() if plan is None else plan,
        options=OPTIONS if options is None else options,
    )


class RemovedDayReplyTests(unittest.TestCase):
    def test_recorded_day_trip_reply_does_not_use_removed_second_day(self):
        self.assertEqual(check(REPLY), [])

    def test_explicitly_removed_day_variants_do_not_supply_return_context(self):
        for prefix in (
            "2일차를 제거했습니다.", "2일차 일정을 삭제했습니다.",
            "2일차는 제외했습니다.", "2일차를 없앴습니다.",
        ):
            with self.subTest(prefix=prefix):
                self.assertEqual(check(prefix + " 귀환은 10:40입니다."), [])

    def test_removed_day_reference_in_assumptions_is_also_ignored(self):
        self.assertEqual(check(
            "당일 일정입니다.", plan=make_plan([REPLY]),
        ), [])

    def test_wrong_time_still_fails_after_removed_day_reference(self):
        self.assertTrue(check(REPLY.replace("10:40", "11:00")))

    def test_nonexistent_day_is_rejected_without_explicit_removal(self):
        self.assertTrue(check("2일차 귀환은 10:40입니다."))

    def test_cancelled_hotel_or_visit_does_not_mean_the_day_was_removed(self):
        for prefix in (
            "2일차 숙소만 취소했습니다.", "2일차 박물관 방문을 제외했습니다.",
        ):
            with self.subTest(prefix=prefix):
                self.assertTrue(check(prefix + " 귀환은 10:40입니다."))

    def test_negative_or_question_forms_do_not_claim_completed_removal(self):
        for prefix in (
            "2일차를 제거하지 않았습니다.", "2일차는 제외하지 마세요.",
            "2일차를 제거할까요?",
        ):
            with self.subTest(prefix=prefix):
                self.assertTrue(check(prefix + " 귀환은 10:40입니다."))

    def test_day_still_in_final_plan_keeps_explicit_return_context(self):
        plan = make_plan()
        plan["days"].append({"day": 2, "items": [
            {"item_type": "move", "start_time": "14:00", "end_time": "14:30"},
        ]})
        self.assertTrue(check(
            "2일차를 제거했습니다. 귀환은 10:40입니다.",
            plan=plan, options={**OPTIONS, "trip_days": 2},
        ))

    def test_inputs_are_preserved(self):
        plan, options = make_plan(), copy.deepcopy(OPTIONS)
        before = copy.deepcopy((plan, options))
        check(REPLY, plan=plan, options=options)
        self.assertEqual((plan, options), before)


if __name__ == "__main__":
    unittest.main()