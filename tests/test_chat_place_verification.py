import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router
from backend.app.services import place_verification


OPTIONS = {
    "departure": "제주빅데이터센터",
    "start_date": "2026-11-20",
    "trip_days": 1,
    "transport": "driving",
}

DATABASE_PLACE = {
    "place_id": "P043",
    "name": "별빛누리공원",
    "category": "공원",
    "latitude": 33.44517801,
    "longitude": 126.548587,
}


def make_plan(title="별빛누리공원", place_id=None):
    return TripPlan(days=[
        DayPlan(day=1, items=[
            ScheduleItem(
                start_time="10:00",
                end_time="11:00",
                item_type="visit",
                title=title,
                place_id=place_id,
                duration_minutes=60,
            ),
        ]),
    ])


def make_messages():
    return [
        AIMessage(content="", tool_calls=[{
            "name": "search_places_by_name",
            "args": {"keyword": "제주별빛누리공원"},
            "id": "search-1",
        }]),
        ToolMessage(
            content=json.dumps(
                {
                    "status": "ok",
                    "places": [
                        {**DATABASE_PLACE, "name_match": "partial"},
                    ],
                },
                ensure_ascii=False,
            ),
            tool_call_id="search-1",
        ),
        AIMessage(content="", tool_calls=[{
            "name": "get_place_details",
            "args": {"place_id": "P043"},
            "id": "details-1",
        }]),
        ToolMessage(
            content=json.dumps(
                {
                    "status": "ok",
                    "place_id": "P043",
                    "name": "별빛누리공원",
                    "spots": [],
                },
                ensure_ascii=False,
            ),
            tool_call_id="details-1",
        ),
    ]


def make_saved_plan():
    value = make_plan(place_id="P043").model_dump()
    value["days"][0]["items"][0].update({
        "verified": True,
        "category": DATABASE_PLACE["category"],
        "latitude": DATABASE_PLACE["latitude"],
        "longitude": DATABASE_PLACE["longitude"],
    })
    return value


class ChatPlaceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(
            patch.object(chat_router, "run_chat_agent")
        )
        self.name_lookup = self.enterContext(
            patch.object(
                place_verification,
                "search_places_by_name",
                return_value={
                    "status": "ok",
                    "places": [
                        {**DATABASE_PLACE, "name_match": "exact"},
                    ],
                },
            )
        )
        self.place_lookup = self.enterContext(
            patch.object(
                plan_router,
                "find_place_in_database",
                return_value=dict(DATABASE_PLACE),
            )
        )

    def run_request(self, plan, messages, **extra):
        self.agent.return_value = {
            "chat_reply": chat_router.ChatAgentReply(
                reply="요청을 확인했습니다.",
                options=OPTIONS,
                plan=plan,
            ),
            "messages": messages,
            "route_calls": [],
            "model_call_count": 3,
            "tool_call_counts": {
                "search_places_by_name": 1,
                "get_place_details": 1,
            },
        }
        request = chat_router.ChatRequest(
            message="일정을 추천해 주세요.",
            options=OPTIONS,
            **extra,
        )
        result = chat_router.create_chat(request)

        if isinstance(result, dict):
            body = chat_router.ChatResponse.model_validate(
                result
            ).model_dump(mode="json")
            return 200, body

        return result.status_code, json.loads(result.body)

    def test_missing_id_is_restored_with_database_values(self):
        status, body = self.run_request(
            make_plan(),
            make_messages(),
        )

        self.assertEqual(status, 200)
        item = body["plan"]["days"][0]["items"][0]
        self.assertEqual(item["place_id"], "P043")
        self.assertTrue(item["verified"])
        self.assertEqual(item["title"], DATABASE_PLACE["name"])
        self.assertEqual(item["latitude"], DATABASE_PLACE["latitude"])
        self.assertEqual(item["longitude"], DATABASE_PLACE["longitude"])
        self.assertEqual(body["warnings"], [])
        self.agent.assert_called_once()

    def test_missing_id_without_current_evidence_stays_unverified(self):
        status, body = self.run_request(make_plan(), [])

        self.assertEqual(status, 200)
        item = body["plan"]["days"][0]["items"][0]
        self.assertIsNone(item["place_id"])
        self.assertFalse(item["verified"])
        self.assertIsNone(item["latitude"])
        self.assertEqual(len(body["warnings"]), 1)
        self.name_lookup.assert_not_called()
        self.place_lookup.assert_not_called()

    def test_partial_title_is_not_auto_linked(self):
        status, body = self.run_request(
            make_plan(title="제주별빛누리공원"),
            make_messages(),
        )

        self.assertEqual(status, 200)
        item = body["plan"]["days"][0]["items"][0]
        self.assertIsNone(item["place_id"])
        self.assertFalse(item["verified"])
        self.assertEqual(len(body["warnings"]), 1)
        self.name_lookup.assert_not_called()

    def test_ambiguous_database_name_stays_unverified(self):
        self.name_lookup.return_value = {
            "status": "ok",
            "places": [
                {**DATABASE_PLACE, "name_match": "exact"},
                {
                    **DATABASE_PLACE,
                    "place_id": "P999",
                    "name_match": "exact",
                },
            ],
        }

        status, body = self.run_request(
            make_plan(),
            make_messages(),
        )

        self.assertEqual(status, 200)
        self.assertFalse(
            body["plan"]["days"][0]["items"][0]["verified"],
        )
        self.assertEqual(len(body["warnings"]), 1)
        self.place_lookup.assert_not_called()

    def test_question_skips_recovery_even_with_records_and_saved_plan(self):
        status, body = self.run_request(
            None,
            make_messages(),
            current_plan=make_saved_plan(),
        )

        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.assertEqual(body["warnings"], [])
        self.name_lookup.assert_not_called()
        self.place_lookup.assert_not_called()

    def test_recovery_database_error_returns_internal_error(self):
        self.name_lookup.return_value = {
            "status": "db_error",
            "places": [],
        }

        status, body = self.run_request(
            make_plan(),
            make_messages(),
        )

        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "internal_error")
        self.assertNotIn("plan", body)
        self.place_lookup.assert_not_called()

    def test_invalid_times_skip_recovery_and_place_lookup(self):
        value = make_plan()
        value.days[0].items[0].duration_minutes = 59

        status, body = self.run_request(value, make_messages())

        self.assertEqual(status, 502)
        self.assertEqual(
            body["error"]["code"],
            "plan_validation_failed",
        )
        self.name_lookup.assert_not_called()
        self.place_lookup.assert_not_called()

    def test_history_and_saved_plan_are_not_current_evidence(self):
        status, body = self.run_request(
            make_plan(),
            [],
            current_plan=make_saved_plan(),
            history=[{
                "role": "assistant",
                "content": json.dumps(
                    {
                        "tool": "get_place_details",
                        "output": DATABASE_PLACE,
                    },
                    ensure_ascii=False,
                ),
            }],
        )

        self.assertEqual(status, 200)
        self.assertFalse(
            body["plan"]["days"][0]["items"][0]["verified"],
        )
        self.assertEqual(len(body["warnings"]), 1)
        self.name_lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()