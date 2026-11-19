import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from backend.app.agent import chat_planner
from backend.app.routers.chat import ChatRequest, ChatAgentReply, build_chat_messages


class LodgingContextTests(unittest.TestCase):
    def test_short_approval_is_forwarded_with_original_history(self):
        history = [
            {"role": "user", "content": "첫날 호텔에 묵고 싶어."},
            {"role": "assistant", "content": "1박 2일로 늘릴까요?"},
        ]
        request = ChatRequest(message="응", options={"trip_days": 1}, history=history)
        messages = build_chat_messages(request)
        self.assertEqual(messages[:2], history)
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(payload["message"], "응")
        self.assertEqual(payload["options"]["trip_days"], 1)
        self.assertNotIn("lodging_period_confirmation", payload)

    def test_agent_runs_without_separate_intent_classifier(self):
        messages = build_chat_messages(ChatRequest(message="호텔 위치만 알려줘.", options={"trip_days": 1}))
        reply = ChatAgentReply(reply="위치를 확인했습니다.", options={})
        with patch.object(chat_planner, "build_chat_agent") as build:
            build.return_value.invoke.return_value = {
                "messages": [*messages, SimpleNamespace(type="ai", tool_calls=[])],
                "structured_response": reply,
            }
            result = chat_planner.run_chat_agent(messages, ChatAgentReply)
        build.assert_called_once()
        self.assertIs(result["chat_reply"], reply)
        self.assertEqual(result["model_call_count"], 1)


if __name__ == "__main__":
    unittest.main()
