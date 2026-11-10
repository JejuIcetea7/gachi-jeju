import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.agent import chat_planner
from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.routers.chat import ChatAgentReply


class ChatAgentFactoryTests(unittest.TestCase):
    def test_build_uses_requested_schema_without_real_api(self):
        with (
            patch.object(
                chat_planner,
                "get_openai_api_key",
                return_value="test-only-key",
            ),
            patch.object(chat_planner, "ChatOpenAI") as model_class,
            patch.object(chat_planner, "create_agent") as create_agent,
        ):
            agent = chat_planner.build_chat_agent(ChatAgentReply)

            self.assertIs(agent, create_agent.return_value)
            model_class.assert_called_once()
            create_agent.assert_called_once()

            arguments = create_agent.call_args.kwargs
            self.assertIs(arguments["model"], model_class.return_value)
            self.assertIs(arguments["response_format"], ChatAgentReply)
            self.assertEqual(
                arguments["system_prompt"],
                chat_planner.CHAT_SYSTEM_PROMPT,
            )


class ChatAgentTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {"role": "user", "content": "부모님과 여행하고 싶어요."},
            {"role": "assistant", "content": "언제 출발하시나요?"},
            {"role": "user", "content": "11월 20일에 출발해요."},
        ]
        self.prefix = [
            SimpleNamespace(type="human"),
            SimpleNamespace(type="ai", tool_calls=[]),
            SimpleNamespace(type="human"),
        ]
        self.agent = Mock()
        self.factory = self.enterContext(
            patch.object(
                chat_planner,
                "build_chat_agent",
                return_value=self.agent,
            )
        )

    def test_question_reply_and_messages_are_forwarded(self):
        reply = ChatAgentReply(
            reply="여행 기간과 이동수단을 알려 주세요.",
            options={"start_date": "2026-11-20"},
        )
        self.agent.invoke.return_value = {
            "messages": self.prefix + [
                SimpleNamespace(type="ai", tool_calls=[])
            ],
            "structured_response": reply,
        }

        result = chat_planner.run_chat_agent(self.messages, ChatAgentReply)

        self.assertIs(result["chat_reply"], reply)
        self.assertIsNone(result["chat_reply"].plan)
        self.factory.assert_called_once_with(
            ChatAgentReply,
            route_calls=result["route_calls"],
        )
        self.agent.invoke.assert_called_once_with(
            {"messages": self.messages}
        )

    def test_generated_plan_is_forwarded(self):
        plan = TripPlan(days=[
            DayPlan(day=1, items=[
                ScheduleItem(
                    start_time="10:00",
                    end_time="11:00",
                    item_type="visit",
                    title="테스트 여행지",
                    duration_minutes=60,
                )
            ])
        ])
        reply = ChatAgentReply(
            reply="당일 일정을 추천했습니다.",
            options={
                "start_date": "2026-11-20",
                "trip_days": 1,
                "transport": "driving",
            },
            plan=plan,
        )
        self.agent.invoke.return_value = {
            "messages": self.prefix + [
                SimpleNamespace(type="ai", tool_calls=[])
            ],
            "structured_response": reply,
        }

        result = chat_planner.run_chat_agent(self.messages, ChatAgentReply)

        self.assertEqual(
            result["chat_reply"].plan.model_dump(),
            plan.model_dump(),
        )

    def test_counts_only_calls_from_current_execution(self):
        self.prefix[1].tool_calls = [{"name": "previous_tool"}]
        self.agent.invoke.return_value = {
            "messages": self.prefix + [
                SimpleNamespace(
                    type="ai",
                    tool_calls=[
                        {"name": "search_places"},
                        {"name": "search_places"},
                        {"name": "get_driving_time"},
                    ],
                ),
                SimpleNamespace(type="tool"),
                SimpleNamespace(type="tool"),
                SimpleNamespace(type="tool"),
                SimpleNamespace(type="ai", tool_calls=[]),
            ],
        }

        result = chat_planner.run_chat_agent(self.messages, ChatAgentReply)

        self.assertEqual(result["model_call_count"], 2)
        self.assertEqual(
            result["tool_call_counts"],
            {"search_places": 2, "get_driving_time": 1},
        )

    def test_missing_structured_response_returns_none(self):
        self.agent.invoke.return_value = {
            "messages": self.prefix + [
                SimpleNamespace(type="ai", tool_calls=[])
            ],
        }

        result = chat_planner.run_chat_agent(self.messages, ChatAgentReply)

        self.assertIsNone(result["chat_reply"])

    def test_agent_error_is_not_swallowed(self):
        self.agent.invoke.side_effect = RuntimeError("test agent error")

        with self.assertRaisesRegex(RuntimeError, "test agent error"):
            chat_planner.run_chat_agent(self.messages, ChatAgentReply)

        self.agent.invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()