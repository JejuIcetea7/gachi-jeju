import copy
import json
import unittest
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError

from backend.app.routers import lodgings as lodging_router


CENTER = {"latitude": 33.4, "longitude": 126.5}
SAMPLE = {
    "status": "ok", "source": "kakao_local", "requested_at": "2026-11-13T05:00:00+00:00",
    "center": CENTER, "radius_m": 5000,
    "places": [{
        "provider_place_id": "100", "name": "테스트 숙소", "address": "제주시 테스트 도로 1",
        "phone": "064-000-0000", "latitude": 33.401, "longitude": 126.501,
        "distance_m": 145, "place_url": "https://place.map.kakao.com/100",
    }],
}


class LodgingApiTests(unittest.TestCase):
    def setUp(self):
        self.sample = copy.deepcopy(SAMPLE)
        self.lookup = self.enterContext(patch.object(
            lodging_router, "search_nearby_lodgings", return_value=self.sample,
        ))
        self.key = self.enterContext(patch.object(
            lodging_router, "get_kakao_api_key", return_value="test-only-key",
        ))

    def read(self, **changes):
        result = lodging_router.get_nearby_lodgings(**{**CENTER, **changes})
        if isinstance(result, dict):
            return 200, result
        return result.status_code, json.loads(result.body)

    def test_search_parameters_and_key_are_only_forwarded_to_service(self):
        status, body = self.read()
        self.assertEqual(status, 200)
        self.lookup.assert_called_once_with(**CENTER, radius_m=5000, max_count=5, api_key="test-only-key")
        self.assertEqual(body["places"], SAMPLE["places"])
        self.assertEqual(body["source"], "kakao_local")
        self.assertEqual(body["center"], CENTER)
        self.assertNotIn("test-only-key", json.dumps(body))
        self.assertEqual(self.sample, SAMPLE)

    def test_empty_results_are_success_without_discarding_search_context(self):
        self.sample.update(status="not_found", places=[])
        status, body = self.read()
        self.assertEqual(status, 200)
        self.assertEqual(body["places"], [])
        self.assertEqual(body["center"], CENTER)
        self.assertEqual(body["radius_m"], 5000)
        self.assertIn("requested_at", body)

    def test_query_radius_and_result_limit_are_forwarded(self):
        self.sample["radius_m"] = 2000
        self.assertEqual(self.read(radius_m=2000, max_count=2)[0], 200)
        self.lookup.assert_called_once_with(**CENTER, radius_m=2000, max_count=2, api_key="test-only-key")

    def test_internal_fields_and_unconfirmed_facility_claims_are_not_exposed(self):
        self.sample.update(api_key="test-only-key", local_path="private", status="ok")
        self.sample["places"][0].update(available=True, accessible=True, price=1000, place_id="P001")
        status, body = self.read()
        self.assertEqual(status, 200)
        self.assertNotIn("status", body)
        self.assertNotIn("api_key", body)
        self.assertNotIn("local_path", body)
        self.assertEqual(set(body["places"][0]), set(SAMPLE["places"][0]))

    def test_errors_keep_shared_format_and_distinguish_provider_failures(self):
        for provider_status, http_status, code in (
            ("auth_error", 503, "lodging_search_unavailable"),
            ("rate_limited", 429, "lodging_search_rate_limited"),
            ("api_error", 502, "lodging_search_failed"),
            ("invalid_input", 400, "invalid_request"),
        ):
            with self.subTest(status=provider_status):
                self.lookup.return_value = {"status": provider_status, "places": []}
                status, body = self.read()
                self.assertEqual(status, http_status)
                self.assertEqual(body["error"]["code"], code)
                self.assertNotIn("test-only-key", json.dumps(body))

    def test_unexpected_response_is_not_an_empty_success(self):
        for value in (None, [], {}, {"status": "unknown"}, {"status": "ok", "places": []}):
            with self.subTest(value=value):
                self.lookup.return_value = value
                self.assertEqual(self.read()[0], 502)

    def test_different_search_context_is_rejected(self):
        for changes in ({"center": {**CENTER, "latitude": 33.5}}, {"radius_m": 1000}):
            with self.subTest(changes=changes):
                self.lookup.return_value = {**copy.deepcopy(SAMPLE), **changes}
                self.assertEqual(self.read()[0], 502)

    def test_result_count_must_not_exceed_requested_limit(self):
        second = {**self.sample["places"][0], "provider_place_id": "101",
                  "place_url": "https://place.map.kakao.com/101"}
        self.sample["places"].append(second)
        self.assertEqual(self.read(max_count=1)[0], 502)

    def test_unsafe_or_mismatched_map_url_is_rejected(self):
        for url in ("javascript:invalid", "https://example.com/100", "https://place.map.kakao.com/101"):
            with self.subTest(url=url):
                self.sample["places"][0]["place_url"] = url
                self.assertEqual(self.read()[0], 502)

    def test_duplicate_ids_are_rejected(self):
        self.sample["places"].append(copy.deepcopy(self.sample["places"][0]))
        self.assertEqual(self.read()[0], 502)

    def test_invalid_public_fields_are_rejected(self):
        for changes in ({"name": " "}, {"latitude": float("nan")}, {"latitude": 37.5},
                        {"distance_m": -1}, {"distance_m": True}, {"distance_m": "145"},
                        {"phone": None}, {"provider_place_id": "P001"}):
            with self.subTest(changes=changes):
                changed = copy.deepcopy(SAMPLE)
                changed["places"][0].update(changes)
                self.lookup.return_value = changed
                self.assertEqual(self.read()[0], 502)

    def test_timestamp_must_be_aware_and_source_must_match_provider(self):
        for changes in ({"requested_at": "invalid"}, {"requested_at": "2026-11-13T05:00:00"},
                        {"source": "unknown"}):
            with self.subTest(changes=changes):
                self.lookup.return_value = {**copy.deepcopy(SAMPLE), **changes}
                self.assertEqual(self.read()[0], 502)

    def test_status_and_presence_of_candidates_must_agree(self):
        self.sample["status"] = "not_found"
        self.assertEqual(self.read()[0], 502)
        self.sample.update(status="ok", places=[])
        self.assertEqual(self.read()[0], 502)

    def test_query_constraints_are_declared(self):
        for field, good, invalid in (
            (lodging_router.Latitude, 33.4, [0, 37.5, float("nan"), float("inf")]),
            (lodging_router.Longitude, 126.5, [0, 180, float("nan")]),
            (lodging_router.Radius, 5000, [0, -1, 20001]),
            (lodging_router.ResultCount, 5, [0, -1, 11]),
        ):
            adapter = TypeAdapter(field)
            self.assertEqual(adapter.validate_python(good), good)
            for value in invalid:
                with self.subTest(value=value):
                    with self.assertRaises(ValidationError):
                        adapter.validate_python(value)

    def test_only_get_search_route_is_declared(self):
        route = next(route for route in lodging_router.router.routes if route.path == "/lodgings/nearby")
        self.assertEqual(route.methods, {"GET"})
        self.assertIs(route.response_model, lodging_router.LodgingSearchResponse)


if __name__ == "__main__":
    unittest.main()
