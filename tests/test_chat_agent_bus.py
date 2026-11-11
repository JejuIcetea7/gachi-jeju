import unittest
from unittest.mock import patch

from backend.app.agent import chat_planner
from backend.app.routers.chat import ChatAgentReply


class ChatAgentBusTests(unittest.TestCase):
    def test_factory_connects_bus_tool_to_request_records(self):
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
                chat_planner,
                "create_bus_map_tool",
            ) as create_tool,
        ):
            chat_planner.build_chat_agent(
                ChatAgentReply,
                route_calls=records,
            )

            create_tool.assert_called_once_with(records)
            self.assertIs(create_tool.call_args.args[0], records)
            self.assertIn(
                create_tool.return_value,
                create_agent.call_args.kwargs["tools"],
            )


if __name__ == "__main__":
    unittest.main()