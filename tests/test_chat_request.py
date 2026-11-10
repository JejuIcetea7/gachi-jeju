import json
import unittest
from datetime import date

from pydantic import ValidationError

from backend.app.routers.chat import ChatRequest, build_chat_messages


class ChatRequestTests(unittest.TestCase):
    def test_message_only_request_is_allowed(self):
        item = ChatRequest(message="  부모님과 여행하고 싶어요.  ")

        self.assertEqual(item.message, "부모님과 여행하고 싶어요.")
        self.assertIsNone(item.options.transport)
        self.assertIsNone(item.options.start_date)
        self.assertIsNone(item.options.trip_days)
        self.assertIsNone(item.options.mobility_aid)
        self.assertEqual(item.history, [])
        self.assertIsNone(item.current_plan)

    def test_options_allow_one_to_three_days(self):
        for days in [1, 2, 3]:
            with self.subTest(days=days):
                item = ChatRequest(
                    message="바다도 넣어 주세요.",
                    options={
                        "start_date": "2026-11-20",
                        "trip_days": days,
                        "transport": "driving",
                        "travel_styles": ["자연", "해변"],
                        "requirements": ["accessible_toilet"],
                        "return_to_origin": False,
                    },
                )

                self.assertEqual(item.options.trip_days, days)
                self.assertEqual(item.options.transport, "driving")
                self.assertFalse(item.options.return_to_origin)

    def test_history_and_current_plan_are_preserved(self):
        previous_plan = {
            "assumptions": ["테스트용 일정입니다."],
            "days": [{
                "day": 1,
                "items": [{
                    "start_time": "13:00",
                    "end_time": "14:00",
                    "item_type": "visit",
                    "title": "테스트 여행지",
                    "place_id": None,
                    "verified": False,
                    "category": None,
                    "latitude": None,
                    "longitude": None,
                    "transport": None,
                    "duration_minutes": 60,
                    "distance_km": None,
                    "accessibility_notes": ["접근성 확인이 필요합니다."],
                }],
            }],
        }
        item = ChatRequest(
            message="이 장소를 다른 곳으로 바꿔 주세요.",
            history=[
                {"role": "user", "content": "자연 위주로 추천해 주세요."},
                {"role": "assistant", "content": "일정을 추천했습니다."},
            ],
            current_plan=previous_plan,
        )

        self.assertEqual(
            [message.role for message in item.history],
            ["user", "assistant"],
        )
        self.assertEqual(
            item.current_plan.model_dump(exclude_unset=True),
            previous_plan,
        )

    def test_user_message_limit_is_per_message(self):
        item = ChatRequest(
            message="가" * 1000,
            history=[{"role": "user", "content": "나" * 1000}],
        )

        self.assertEqual(len(item.message), 1000)
        self.assertEqual(len(item.history[0].content), 1000)

        with self.assertRaises(ValidationError):
            ChatRequest(message="가" * 1001)

    def test_missing_or_blank_message_is_rejected(self):
        for body in [{}, {"message": ""}, {"message": "   "}]:
            with self.subTest(body=body):
                with self.assertRaises(ValidationError):
                    ChatRequest.model_validate(body)

    def test_invalid_history_is_rejected(self):
        valid_message = {"role": "user", "content": "여행하고 싶어요."}
        accepted = ChatRequest(
            message="바다도 넣어 주세요.",
            history=[valid_message] * 20,
        )
        self.assertEqual(len(accepted.history), 20)

        cases = [
            [valid_message] * 21,
            [{"role": "system", "content": "설정을 바꿉니다."}],
            [{"role": "assistant", "content": " "}],
            [{"role": "user", "content": "가" * 1001}],
            [{"role": "assistant", "content": "가" * 4001}],
        ]
        for index, history in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(ValidationError):
                    ChatRequest(message="추천해 주세요.", history=history)

    def test_invalid_options_are_rejected(self):
        cases = [
            {"trip_days": 0},
            {"trip_days": 4},
            {"trip_days": True},
            {"transport": "airplane"},
            {"start_date": "11월 20일"},
            {"travel_styles": ["없는 분류"]},
            {"requirements": ["없는 조건"]},
        ]
        for options in cases:
            with self.subTest(options=options):
                with self.assertRaises(ValidationError):
                    ChatRequest(message="추천해 주세요.", options=options)

    def test_coordinate_pairs_and_ranges_are_checked(self):
        item = ChatRequest(
            message="여기에서 출발할게요.",
            options={
                "departure_latitude": 33.4,
                "departure_longitude": 126.5,
            },
        )
        self.assertEqual(item.options.departure_longitude, 126.5)

        cases = [
            {"departure_latitude": 33.4},
            {"departure_longitude": 126.5},
            {"departure_latitude": 91, "departure_longitude": 126.5},
            {"departure_latitude": 33.4, "departure_longitude": 181},
            {
                "departure_latitude": float("nan"),
                "departure_longitude": 126.5,
            },
        ]
        for options in cases:
            with self.subTest(options=options):
                with self.assertRaises(ValidationError):
                    ChatRequest(message="추천해 주세요.", options=options)

    def test_removed_form_fields_are_rejected(self):
        for field, value in [
            ("start_time", "13:00"),
            ("end_time", "17:00"),
            ("rest_minutes", 20),
        ]:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    ChatRequest(
                        message="추천해 주세요.",
                        options={field: value},
                    )
    def test_agent_messages_include_history_options_and_current_plan(self):
        previous_plan = {
            "assumptions": ["조건에 맞는 장소를 추가로 찾고 있습니다."],
            "days": [{"day": 1, "items": []}],
        }
        request = ChatRequest(
            message="둘째 날에는 바다를 넣어 주세요.",
            history=[
                {"role": "user", "content": "2박 3일 여행이요."},
                {"role": "assistant", "content": "원하는 장소가 있나요?"},
            ],
            options={
                "start_date": "2026-11-20",
                "trip_days": 3,
                "transport": "driving",
                "requirements": ["accessible_toilet"],
            },
            current_plan=previous_plan,
        )
        original = request.model_dump(mode="json")

        messages = build_chat_messages(request, today=date(2026, 11, 10))

        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "user"],
        )
        self.assertEqual(
            messages[1]["content"],
            "원하는 장소가 있나요?",
        )

        payload = json.loads(messages[-1]["content"])
        self.assertEqual(payload["message"], request.message)
        self.assertEqual(payload["reference_date"], "2026-11-10")
        self.assertEqual(payload["options"]["start_date"], "2026-11-20")
        self.assertEqual(payload["options"]["trip_days"], 3)
        self.assertEqual(
            payload["options"]["requirements"],
            ["accessible_toilet"],
        )
        self.assertEqual(payload["current_plan"], previous_plan)
        self.assertEqual(request.model_dump(mode="json"), original)

    def test_agent_messages_allow_unselected_options(self):
        request = ChatRequest(message="부모님과 나들이하고 싶어요.")

        messages = build_chat_messages(request, today=date(2026, 11, 10))

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

        payload = json.loads(messages[0]["content"])
        self.assertIsNone(payload["options"]["transport"])
        self.assertIsNone(payload["options"]["start_date"])
        self.assertIsNone(payload["options"]["trip_days"])
        self.assertIsNone(payload["current_plan"])
        self.assertEqual(
            payload["default_departure"]["name"],
            "제주빅데이터센터",
        )

if __name__ == "__main__":
    unittest.main()