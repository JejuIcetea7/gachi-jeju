from concurrent.futures import ThreadPoolExecutor
import inspect
import unittest
from unittest.mock import Mock, patch

import httpx2
from langchain_core.tools import StructuredTool
from openai import DefaultHttpxClient, OpenAI

from backend.app.agent import chat_planner
from backend.app.services import chat_request_agent as agent_control
from backend.app.services.chat_request_control import (
    ChatRequestControl, ChatRequestStopped, get_chat_request_control,
    get_chat_request_timeout, use_chat_request_control,
)


class ChatRequestAgentTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.control = ChatRequestControl(clock=lambda: self.now)
        self.requests = []
        self.responses = []
        self.clients = []
        self.handler = lambda request: httpx2.Response(200, json={"ok": True})
        self.client_factory = self.enterContext(patch.object(
            agent_control, "DefaultHttpxClient", side_effect=self.make_client,
        ))
        self.key = self.enterContext(patch.object(
            chat_planner, "get_openai_api_key", return_value="test-only-key",
        ))

    def make_client(self, **kwargs):
        def handle(request):
            self.requests.append(request)
            response = self.handler(request)
            self.responses.append(response)
            return response

        client = DefaultHttpxClient(
            transport=httpx2.MockTransport(handle), trust_env=False, **kwargs,
        )
        self.clients.append(client)
        return client

    def assert_stopped(self, call, reason):
        with self.assertRaises(ChatRequestStopped) as caught:
            call()
        self.assertEqual(caught.exception.reason, reason)

    def test_uncontrolled_calls_keep_original_tool_and_client_behavior(self):
        def function():
            return "test-only"

        self.assertIs(agent_control.guard_chat_tool(function, None), function)
        with agent_control.chat_model_http_client() as client:
            self.assertIsNone(client)
        self.client_factory.assert_not_called()

    def test_tool_names_signatures_and_schemas_are_preserved(self):
        functions = [
            chat_planner.find_location,
            chat_planner.search_places,
            chat_planner.search_places_by_name,
            chat_planner.get_place_details,
            chat_planner.create_driving_map_tool([]),
            chat_planner.create_bus_map_tool([]),
            chat_planner.create_walking_map_tool([]),
        ]
        for function in functions:
            with self.subTest(tool=function.__name__):
                wrapped = agent_control.guard_chat_tool(function, self.control)
                self.assertEqual(wrapped.__name__, function.__name__)
                self.assertEqual(inspect.signature(wrapped), inspect.signature(function))
                self.assertEqual(
                    StructuredTool.from_function(wrapped).args,
                    StructuredTool.from_function(function).args,
                )

    def test_cancelled_tool_never_executes(self):
        called = Mock()
        wrapped = agent_control.guard_chat_tool(called, self.control)
        self.control.cancel()
        self.assert_stopped(wrapped, "cancelled")
        called.assert_not_called()

    def test_tool_worker_receives_control_without_leaking_it(self):
        def function():
            return get_chat_request_control(), get_chat_request_timeout(120)

        self.now += 100
        wrapped = agent_control.guard_chat_tool(function, self.control)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(wrapped).result(timeout=2)
            self.assertIsNone(pool.submit(get_chat_request_control).result(timeout=2))
        self.assertIs(result[0], self.control)
        self.assertEqual(result[1], 50)

    def test_cancel_during_tool_discards_result(self):
        def function():
            self.control.cancel()
            return {"status": "ok"}

        self.assert_stopped(
            agent_control.guard_chat_tool(function, self.control), "cancelled",
        )

    def test_deadline_during_tool_discards_result(self):
        def function():
            self.now += 150
            return {"status": "ok"}

        self.assert_stopped(
            agent_control.guard_chat_tool(function, self.control), "timed_out",
        )

    def test_active_tool_error_is_preserved(self):
        def function():
            raise OSError("test-only")

        wrapped = agent_control.guard_chat_tool(function, self.control)
        with self.assertRaisesRegex(OSError, "test-only"):
            wrapped()

    def test_cancelled_tool_error_preserves_stop_reason(self):
        def function():
            self.control.cancel()
            raise OSError("test-only")

        self.assert_stopped(
            agent_control.guard_chat_tool(function, self.control), "cancelled",
        )

    def test_request_hook_recalculates_all_timeout_phases(self):
        with use_chat_request_control(self.control):
            with agent_control.chat_model_http_client() as client:
                self.now += 140
                timeout = httpx2.Timeout(connect=3, read=None, write=60, pool=2)
                client.get("https://example.invalid/first", timeout=timeout)
                self.now += 8
                client.get("https://example.invalid/second", timeout=timeout)
        self.assertEqual(self.requests[0].extensions["timeout"], {
            "connect": 3, "read": 10, "write": 10, "pool": 2,
        })
        self.assertEqual(self.requests[1].extensions["timeout"], {
            "connect": 2, "read": 2, "write": 2, "pool": 2,
        })
        self.assertIsNone(timeout.read)
        self.assertTrue(self.clients[0].is_closed)

    def test_cancel_before_send_never_reaches_transport(self):
        with use_chat_request_control(self.control):
            with agent_control.chat_model_http_client() as client:
                self.control.cancel()
                self.assert_stopped(
                    lambda: client.get("https://example.invalid/"), "cancelled",
                )
        self.assertEqual(self.requests, [])
        self.assertTrue(self.clients[0].is_closed)

    def test_deadline_before_send_never_reaches_transport(self):
        with use_chat_request_control(self.control):
            with agent_control.chat_model_http_client() as client:
                self.now += 150
                self.assert_stopped(
                    lambda: client.get("https://example.invalid/"), "timed_out",
                )
        self.assertEqual(self.requests, [])

    def test_late_response_is_closed_and_discarded(self):
        def handle(request):
            self.control.cancel()
            return httpx2.Response(200, json={"ok": True})

        self.handler = handle
        with use_chat_request_control(self.control):
            with agent_control.chat_model_http_client() as client:
                self.assert_stopped(
                    lambda: client.get("https://example.invalid/"), "cancelled",
                )
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.responses[0].is_closed)
        self.assertTrue(self.clients[0].is_closed)

    def test_sdk_retry_stops_before_second_http_send(self):
        self.handler = lambda request: httpx2.Response(
            500, json={"error": {"message": "test-only", "type": "server_error"}},
        )
        with use_chat_request_control(self.control):
            with agent_control.chat_model_http_client() as client:
                sdk = OpenAI(api_key="test-only-key", http_client=client, max_retries=2)
                with patch.object(sdk, "_sleep_for_retry", side_effect=lambda **kwargs: self.control.cancel()):
                    self.assert_stopped(
                        lambda: sdk.responses.create(model="test-model", input="test-only"),
                        "cancelled",
                    )
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.clients[0].is_closed)

    def test_active_sdk_retry_keeps_retry_and_remaining_budget(self):
        def handle(request):
            if len(self.requests) == 1:
                self.now += 100
                return httpx2.Response(500, json={
                    "error": {"message": "test-only", "type": "server_error"},
                })
            return httpx2.Response(200, json={
                "id": "resp_test", "object": "response", "created_at": 0,
                "status": "completed", "model": "test-model", "output": [],
            })

        self.handler = handle
        with use_chat_request_control(self.control):
            with agent_control.chat_model_http_client() as client:
                sdk = OpenAI(api_key="test-only-key", http_client=client, max_retries=2)
                with patch.object(sdk, "_sleep_for_retry"):
                    result = sdk.responses.create(model="test-model", input="test-only")
        self.assertEqual(result.id, "resp_test")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[1].extensions["timeout"]["read"], 50)

    def test_real_agent_uses_mock_transport_and_closes_client(self):
        self.handler = lambda request: httpx2.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 0,
            "status": "completed", "model": "gpt-6-luna",
            "output": [{
                "id": "msg_test", "type": "message", "role": "assistant",
                "status": "completed", "content": [{
                    "type": "output_text", "text": "test-only", "annotations": [],
                }],
            }],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        })
        with use_chat_request_control(self.control):
            result = chat_planner.run_chat_agent(
                [{"role": "user", "content": "test-only"}], None,
            )
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(result["model_call_count"], 1)
        self.assertIsNone(result["chat_reply"])
        self.assertTrue(self.clients[0].is_closed)

    def test_controlled_factory_requires_dedicated_model_client(self):
        with use_chat_request_control(self.control):
            with self.assertRaisesRegex(ValueError, "HTTP"):
                chat_planner.build_chat_agent(None)
        self.key.assert_not_called()

    def test_controlled_factory_passes_client_timeout_and_guarded_tools(self):
        self.now += 145
        with use_chat_request_control(self.control):
            with agent_control.chat_model_http_client() as client:
                with (
                    patch.object(chat_planner, "ChatOpenAI") as model,
                    patch.object(chat_planner, "create_agent") as factory,
                ):
                    chat_planner.build_chat_agent(None, http_client=client)
                self.assertIs(model.call_args.kwargs["http_client"], client)
                self.assertEqual(model.call_args.kwargs["timeout"], 5)
                self.assertEqual(model.call_args.kwargs["max_retries"], 2)
                tools = factory.call_args.kwargs["tools"]
                self.control.cancel()
                for function in tools:
                    with self.subTest(tool=function.__name__):
                        self.assert_stopped(function, "cancelled")

    def test_cancelled_run_skips_client_factory_and_agent(self):
        with use_chat_request_control(self.control):
            self.control.cancel()
            with patch.object(chat_planner, "build_chat_agent") as factory:
                self.assert_stopped(
                    lambda: chat_planner.run_chat_agent([], None), "cancelled",
                )
                factory.assert_not_called()
        self.client_factory.assert_not_called()

    def test_run_discards_late_result_and_closes_client(self):
        agent = Mock()

        def invoke(payload):
            self.control.cancel()
            return {"messages": []}

        agent.invoke.side_effect = invoke
        with use_chat_request_control(self.control):
            with patch.object(chat_planner, "build_chat_agent", return_value=agent):
                self.assert_stopped(
                    lambda: chat_planner.run_chat_agent([], None), "cancelled",
                )
        self.assertTrue(self.clients[0].is_closed)

    def test_agent_build_failure_also_closes_client(self):
        with use_chat_request_control(self.control):
            with patch.object(chat_planner, "build_chat_agent", side_effect=RuntimeError("test-only")):
                with self.assertRaisesRegex(RuntimeError, "test-only"):
                    chat_planner.run_chat_agent([], None)
        self.assertTrue(self.clients[0].is_closed)


if __name__ == "__main__":
    unittest.main()