from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from backend.app.services import (
    jeju_bus, kakao_local, kakao_mobility, kakao_transit, kakao_walking,
)
from backend.app.services.chat_request_control import (
    ChatRequestControl, ChatRequestStopped, use_chat_request_control,
)


SAMPLES = Path(__file__).parent / "sample_responses"
ROUTE_INPUT = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
    "api_key": "test-key",
}
PROVIDERS = [
    (kakao_local, kakao_local.search_keyword_place,
     {"keyword": "제주국제공항", "api_key": "test-key"},
     "kakao_keyword_success.json"),
    (kakao_mobility, kakao_mobility.get_driving_route,
     ROUTE_INPUT, "kakao_driving_success.json"),
    (kakao_walking, kakao_walking.get_walking_route,
     ROUTE_INPUT, "kakao_walking_success.json"),
    (kakao_transit, kakao_transit.get_transit_routes,
     ROUTE_INPUT, "kakao_transit_success.json"),
    (jeju_bus, jeju_bus.get_bus_arrivals, {"station_id": "test-station"}, None),
]


class Response(BytesIO):
    def __init__(self, body, on_read=None):
        super().__init__(body)
        self.on_read = on_read
        self.read_count = 0

    def read(self, *args):
        self.read_count += 1
        if self.on_read is not None:
            self.on_read()
        return super().read(*args)


class ChatRequestTransportTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.control = ChatRequestControl(clock=lambda: self.now)

    def expire(self):
        self.now += 150

    def response(self, sample, on_read=None):
        body = (SAMPLES / sample).read_bytes() if sample else b"[]"
        return Response(body, on_read)

    def assert_stopped(self, call, reason):
        with self.assertRaises(ChatRequestStopped) as caught:
            call()
        self.assertEqual(caught.exception.reason, reason)

    def assert_success(self, module, result):
        if module is jeju_bus:
            self.assertEqual(result, [])
        else:
            self.assertEqual(result["status"], "ok")

    def test_uncontrolled_calls_keep_provider_results_and_timeout(self):
        for module, call, arguments, sample in PROVIDERS:
            with self.subTest(provider=module.__name__):
                response = self.response(sample)
                with patch.object(module, "urlopen", return_value=response) as http:
                    self.assert_success(module, call(**arguments))
                self.assertEqual(http.call_args.kwargs["timeout"], 20)
                self.assertTrue(response.closed)

    def test_provider_timeout_uses_remaining_budget(self):
        self.now += 146.5
        with use_chat_request_control(self.control):
            for module, call, arguments, sample in PROVIDERS:
                with self.subTest(provider=module.__name__):
                    with patch.object(module, "urlopen", return_value=self.response(sample)) as http:
                        self.assert_success(module, call(**arguments))
                    self.assertEqual(http.call_args.kwargs["timeout"], 3.5)

    def test_cancelled_request_never_opens_another_url(self):
        with use_chat_request_control(self.control):
            self.control.cancel()
            for module, call, arguments, _ in PROVIDERS:
                with self.subTest(provider=module.__name__):
                    with patch.object(module, "urlopen") as http:
                        self.assert_stopped(lambda: call(**arguments), "cancelled")
                        http.assert_not_called()

    def test_expired_request_never_opens_another_url(self):
        with use_chat_request_control(self.control):
            self.expire()
            for module, call, arguments, _ in PROVIDERS:
                with self.subTest(provider=module.__name__):
                    with patch.object(module, "urlopen") as http:
                        self.assert_stopped(lambda: call(**arguments), "timed_out")
                        http.assert_not_called()

    def check_interrupted_response(self, stop_during_read, reason):
        for module, call, arguments, sample in PROVIDERS:
            with self.subTest(provider=module.__name__):
                self.control = ChatRequestControl(clock=lambda: self.now)
                stop = self.control.cancel if reason == "cancelled" else self.expire
                response = self.response(sample, stop if stop_during_read else None)

                def open_response(*args, **kwargs):
                    if not stop_during_read:
                        stop()
                    return response

                with use_chat_request_control(self.control):
                    with patch.object(module, "urlopen", side_effect=open_response):
                        self.assert_stopped(lambda: call(**arguments), reason)
                self.assertTrue(response.closed)
                self.assertEqual(response.read_count, 1 if stop_during_read else 0)

    def test_cancel_during_open_closes_response_before_reading(self):
        self.check_interrupted_response(False, "cancelled")

    def test_deadline_during_open_closes_response_before_reading(self):
        self.check_interrupted_response(False, "timed_out")

    def test_cancel_during_body_read_discards_response(self):
        self.check_interrupted_response(True, "cancelled")

    def test_deadline_during_body_read_discards_response(self):
        self.check_interrupted_response(True, "timed_out")

    def test_cancel_is_not_converted_to_provider_network_error(self):
        for module, call, arguments, _ in PROVIDERS:
            with self.subTest(provider=module.__name__):
                self.control = ChatRequestControl(clock=lambda: self.now)

                def fail(*args, **kwargs):
                    self.control.cancel()
                    raise URLError("test-only")

                with use_chat_request_control(self.control):
                    with patch.object(module, "urlopen", side_effect=fail) as http:
                        self.assert_stopped(lambda: call(**arguments), "cancelled")
                        http.assert_called_once()

    def test_cancel_closes_http_error_body_and_preserves_stop_reason(self):
        for module, call, arguments, _ in PROVIDERS:
            with self.subTest(provider=module.__name__):
                self.control = ChatRequestControl(clock=lambda: self.now)
                body = BytesIO(b"{}")
                error = HTTPError("https://example.invalid/", 401, "test-only", None, body)

                def fail(*args, **kwargs):
                    self.control.cancel()
                    raise error

                with use_chat_request_control(self.control):
                    with patch.object(module, "urlopen", side_effect=fail):
                        self.assert_stopped(lambda: call(**arguments), "cancelled")
                self.assertTrue(body.closed)

    def test_active_http_errors_keep_existing_provider_mapping(self):
        for module, call, arguments, _ in PROVIDERS:
            with self.subTest(provider=module.__name__):
                body = BytesIO(b"{}")
                error = HTTPError("https://example.invalid/", 401, "test-only", None, body)
                with use_chat_request_control(self.control):
                    with patch.object(module, "urlopen", side_effect=error):
                        if module is jeju_bus:
                            with self.assertRaises(jeju_bus.JejuBusAPIError):
                                call(**arguments)
                        else:
                            self.assertEqual(call(**arguments), {"status": "auth_error"})
                error.close()

    def test_cancel_during_invalid_json_is_not_reported_as_provider_error(self):
        for module, call, arguments, _ in PROVIDERS:
            with self.subTest(provider=module.__name__):
                self.control = ChatRequestControl(clock=lambda: self.now)
                response = Response(b"not-json", self.control.cancel)
                with use_chat_request_control(self.control):
                    with patch.object(module, "urlopen", return_value=response):
                        self.assert_stopped(lambda: call(**arguments), "cancelled")
                self.assertTrue(response.closed)

    def test_active_invalid_json_keeps_existing_provider_mapping(self):
        for module, call, arguments, _ in PROVIDERS:
            with self.subTest(provider=module.__name__):
                response = Response(b"not-json")
                with use_chat_request_control(self.control):
                    with patch.object(module, "urlopen", return_value=response):
                        if module is jeju_bus:
                            with self.assertRaises(jeju_bus.JejuBusAPIError):
                                call(**arguments)
                        else:
                            self.assertEqual(call(**arguments), {"status": "api_error"})
                self.assertTrue(response.closed)

    def test_active_network_error_keeps_existing_provider_mapping(self):
        for module, call, arguments, _ in PROVIDERS:
            with self.subTest(provider=module.__name__):
                with use_chat_request_control(self.control):
                    with patch.object(module, "urlopen", side_effect=URLError("test-only")):
                        if module is jeju_bus:
                            with self.assertRaises(jeju_bus.JejuBusAPIError):
                                call(**arguments)
                        else:
                            self.assertEqual(call(**arguments), {"status": "api_error"})

    def test_cancel_between_bus_calls_prevents_second_url(self):
        with use_chat_request_control(self.control):
            with patch.object(jeju_bus, "urlopen", return_value=BytesIO(b"[]")) as http:
                self.assertEqual(jeju_bus.get_bus_arrivals("test-station"), [])
                self.control.cancel()
                self.assert_stopped(jeju_bus.get_bus_positions, "cancelled")
                self.assertEqual(http.call_count, 1)

    def test_cancelled_request_does_not_stop_an_independent_request(self):
        self.control.cancel()
        other = ChatRequestControl(clock=lambda: self.now)
        with use_chat_request_control(other):
            for module, call, arguments, sample in PROVIDERS:
                with self.subTest(provider=module.__name__):
                    with patch.object(module, "urlopen", return_value=self.response(sample)):
                        self.assert_success(module, call(**arguments))
        self.assertEqual(other.state, "active")


if __name__ == "__main__":
    unittest.main()