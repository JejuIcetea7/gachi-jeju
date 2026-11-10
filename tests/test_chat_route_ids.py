import unittest

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.routers.chat import ChatRequest
from backend.app.routers.plan import PlanBodyResponse, verify_places


def make_move_plan(route_id=None):
    return TripPlan(days=[
        DayPlan(day=1, items=[
            ScheduleItem(
                start_time="09:00",
                end_time="09:10",
                item_type="move",
                title="출발지 → 도착지",
                transport="자동차",
                duration_minutes=10,
                distance_km=1,
                route_id=route_id,
            )
        ])
    ])


class ChatRouteIdTests(unittest.TestCase):
    def test_route_id_survives_plan_response_conversion(self):
        plan, warnings = verify_places(make_move_plan("test-route-001"))
        response = PlanBodyResponse.model_validate(plan)

        self.assertEqual(warnings, [])
        self.assertEqual(
            response.days[0].items[0].route_id,
            "test-route-001",
        )
        self.assertEqual(
            response.model_dump()["days"][0]["items"][0]["route_id"],
            "test-route-001",
        )

    def test_saved_plan_without_route_id_is_still_accepted(self):
        plan, _ = verify_places(make_move_plan())
        plan["days"][0]["items"][0].pop("route_id")

        request = ChatRequest(
            message="이 일정을 수정해 주세요.",
            current_plan=plan,
        )

        self.assertIsNone(request.current_plan.days[0].items[0].route_id)


if __name__ == "__main__":
    unittest.main()