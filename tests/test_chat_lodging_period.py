import json
import unittest
from unittest.mock import patch

from backend.app.routers import chat


import test_chat_lodging_planning as fixture
from types import SimpleNamespace
REQUEST = "1일차 숙소를 제주신라호텔로 변경하고 다음 날 숙소 출발 이동도 반영해 주세요."
HOTEL = {**fixture.stay(), "name": "제주신라호텔"}
OPTIONS = fixture.options(days=1, status="not_needed")


def history():
    return [{"role": "user", "content": REQUEST},
            {"role": "assistant", "content": "현재 여행은 당일 일정입니다. 숙박을 포함하도록 1박 2일로 늘릴까요?"}]


def location_messages():
    return [
        SimpleNamespace(type="ai", tool_calls=[{"id": "location", "name": "find_location", "args": {"keyword": HOTEL["name"]}}]),
        SimpleNamespace(type="tool", tool_call_id="location", content=json.dumps({"status": "ok", **{key: HOTEL[key] for key in ("name", "address", "latitude", "longitude")}})),
    ]


class ChatLodgingPeriodTests(unittest.TestCase):
    def test_model_question_is_preserved(self):
        message = "호텔에서 자고 싶어."
        reply = chat.ChatAgentReply(reply="1박 2일로 늘릴까요?", options=OPTIONS,
            needs_clarification=True,
            request_actions=[chat.RequestAction(subject="lodging", action="add", evidence=message)])
        with patch.object(chat, "run_chat_agent", return_value={"chat_reply": reply}) as agent:
            body = chat.create_chat(chat.ChatRequest(message=message, options=OPTIONS))
        self.assertEqual(body["reply"], reply.reply)
        self.assertIsNone(body["plan"])
        agent.assert_called_once()

    def test_approval_is_decided_from_history_by_the_same_agent(self):
        history = [{"role": "user", "content": "호텔에서 자고 싶어."},
                   {"role": "assistant", "content": "1박 2일로 늘릴까요?"}]
        reply = chat.ChatAgentReply(reply="기간을 변경했습니다.", options={**OPTIONS, "trip_days": 2},
            changed_options=["trip_days"], period_confirmed=True)
        with patch.object(chat, "run_chat_agent", return_value={"chat_reply": reply}) as agent:
            body = chat.create_chat(chat.ChatRequest(message="응", options=OPTIONS, history=history))
        self.assertEqual(body["options"].trip_days, 2)
        self.assertEqual(agent.call_args.args[0][:2], history)

    def test_period_confirmation_declares_the_trip_days_change(self):
        # 실제 실행 C49: 동의는 이해했지만 changed_options에 trip_days를 빠뜨린 응답
        history = [{"role": "user", "content": "호텔에서 자고 싶어."},
                   {"role": "assistant", "content": "1박 2일로 늘릴까요?"}]
        reply = chat.ChatAgentReply(reply="기간을 변경했습니다.", options={**OPTIONS, "trip_days": 2},
            changed_options=[], period_confirmed=True)
        with patch.object(chat, "run_chat_agent", return_value={"chat_reply": reply}) as agent:
            body = chat.create_chat(chat.ChatRequest(message="응", options=OPTIONS, history=history))
        self.assertEqual(body["options"].trip_days, 2)
        agent.assert_called_once()

    def test_trip_days_change_without_confirmation_is_still_fed_back(self):
        reply = chat.ChatAgentReply(reply="기간을 변경했습니다.", options={**OPTIONS, "trip_days": 2})
        with patch.object(chat, "run_chat_agent", return_value={"chat_reply": reply}) as agent:
            result = chat.create_chat(chat.ChatRequest(message="호텔에서 자고 싶어.", options=OPTIONS))
        self.assertEqual(result.status_code, 502)
        self.assertEqual(agent.call_count, 2)

    def test_declined_extension_without_new_plan_is_accepted(self):
        # 실제 실행 C49-decline: 거절을 이해해 일정을 그대로 두고 숙소 제외 행동을 기록한 응답
        message = "아니, 기간은 늘리지 말고 당일로 그냥 둘래."
        reply = chat.ChatAgentReply(reply="당일 일정으로 유지합니다.", options=OPTIONS, request_actions=[
            chat.RequestAction(subject="lodging", action="remove", evidence=message, target_name="제주신라호텔")])
        with patch.object(chat, "run_chat_agent", return_value={"chat_reply": reply}) as agent:
            body = chat.create_chat(chat.ChatRequest(message=message, options=OPTIONS))
        self.assertEqual(body["reply"], reply.reply)
        self.assertIsNone(body["plan"])
        agent.assert_called_once()

    def test_unsupported_source_is_returned_to_agent(self):
        invalid = chat.ChatAgentReply(reply="변경했습니다.", options=OPTIONS, request_actions=[
            chat.RequestAction(subject="lodging", action="add", evidence="원문에 없는 말")])
        corrected = chat.ChatAgentReply(reply="요금은 미확인입니다.", options=OPTIONS)
        with patch.object(chat, "run_chat_agent", side_effect=[{"chat_reply": invalid}, {"chat_reply": corrected}]) as agent:
            body = chat.create_chat(chat.ChatRequest(message="숙박 비용이 궁금해.", options=OPTIONS))
        feedback = json.loads(agent.call_args_list[1].args[0][-1]["content"])["plan_retry"]
        self.assertTrue(feedback["validation_errors"])
        self.assertEqual(body["reply"], corrected.reply)

    def test_overlapping_valid_quotes_do_not_trigger_semantic_rejection(self):
        message = "숙소와 관광지는 유지하고 날짜만 바꿔줘."
        reply = chat.ChatAgentReply(reply="바꿀 날짜를 알려주세요.", options=OPTIONS,
            needs_clarification=True, request_actions=[
                chat.RequestAction(subject="lodging", action="keep", evidence="숙소와 관광지는 유지하고"),
                chat.RequestAction(subject="itinerary", action="change", evidence=message)])
        with patch.object(chat, "run_chat_agent", return_value={"chat_reply": reply}) as agent:
            body = chat.create_chat(chat.ChatRequest(message=message, options=OPTIONS))
        self.assertEqual(body["reply"], reply.reply)
        agent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
