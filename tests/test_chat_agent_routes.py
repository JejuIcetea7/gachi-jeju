import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.agent import chat_planner
from backend.app.routers.chat import ChatAgentReply


class ChatAgentRouteTests(unittest.TestCase):
    def test_factory_connects_map_tool_to_supplied_records(self):
        records = []

        with (
            patch.object(
                chat_planner,
                "get_openai_api_key",
                return_value="test-only-key",
            ),
            patch.object(chat_planner, "ChatOpenAI"),
            patch.object(chat_planner, "create_agent") as create_agent,
            patch.object(
                chat_planner, "create_driving_map_tool"
            ) as create_tool,
        ):
            chat_planner.build_chat_agent(
                ChatAgentReply,
                route_calls=records,
            )

            create_tool.assert_called_once_with(records)
            self.assertIn(
                create_tool.return_value,
                create_agent.call_args.kwargs["tools"],
            )

    def test_route_records_are_scoped_to_each_run(self):
        messages = [{"role": "user", "content": "제주 여행을 추천해 주세요."}]
        collected_lists = []

        def fake_factory(response_format, *, route_calls):
            self.assertIs(response_format, ChatAgentReply)
            collected_lists.append(route_calls)
            execution_number = len(collected_lists)
            agent = Mock()

            def invoke(payload):
                self.assertEqual(payload, {"messages": messages})
                route_calls.append({"execution": execution_number})
                return {
                    "messages": [
                        SimpleNamespace(type="human"),
                        SimpleNamespace(type="ai", tool_calls=[]),
                    ],
                }

            agent.invoke.side_effect = invoke
            return agent

        with patch.object(
            chat_planner,
            "build_chat_agent",
            side_effect=fake_factory,
        ):
            first = chat_planner.run_chat_agent(messages, ChatAgentReply)
            second = chat_planner.run_chat_agent(messages, ChatAgentReply)

        self.assertIsNot(collected_lists[0], collected_lists[1])
        self.assertIs(first["route_calls"], collected_lists[0])
        self.assertIs(second["route_calls"], collected_lists[1])
        self.assertEqual(first["route_calls"], [{"execution": 1}])
        self.assertEqual(second["route_calls"], [{"execution": 2}])


if __name__ == "__main__":
    unittest.main()