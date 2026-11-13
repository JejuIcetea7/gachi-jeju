import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError

from backend.app.routers import place_details as details_router


SAMPLE = {
    "status": "ok",
    "place_id": "P166",
    "name": "퍼시픽마리나요트공원",
    "category": "공원",
    "closed_days": "확인 안 됨",
    "spots": [{"name": "출입구", "description": "확인한 동선 설명"}],
    "accessibility_facts": [
        {
            "section": "주차장",
            "facility_name": "퍼시픽리솜 2주차장",
            "description": "장애인 주차 구역 주차면수",
            "raw_value": "5대",
        },
        {
            "section": "추천 대상",
            "facility_name": "",
            "description": "휠체어 이용인(수동)",
            "raw_value": "● 동행인 / 보호자 동반 추천",
        },
    ],
}


class PlaceDetailsApiTests(unittest.TestCase):
    def setUp(self):
        self.sample = copy.deepcopy(SAMPLE)
        self.lookup = self.enterContext(patch.object(
            details_router, "read_place_details", return_value=self.sample,
        ))

    def read(self):
        result = details_router.get_place_details("P166")
        if isinstance(result, dict):
            return 200, result
        return result.status_code, json.loads(result.body)

    def test_returns_facility_and_recommendation_originals(self):
        status, body = self.read()

        self.assertEqual(status, 200)
        self.assertEqual(body["place_id"], "P166")
        self.assertEqual(
            body["accessibility_facts"], SAMPLE["accessibility_facts"]
        )
        self.assertEqual(body["spots"], SAMPLE["spots"])
        self.assertEqual(body["closed_days"], "확인 안 됨")

    def test_empty_lists_remain_empty_without_claiming_facilities_absent(self):
        self.sample["spots"] = []
        self.sample["accessibility_facts"] = []
        status, body = self.read()

        self.assertEqual(status, 200)
        self.assertEqual(body["accessibility_facts"], [])
        self.assertEqual(body["spots"], [])
        self.assertEqual(body["name"], SAMPLE["name"])

    def test_unknown_place_returns_not_found(self):
        self.lookup.return_value = {"status": "not_found"}
        status, body = self.read()

        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "place_not_found")

    def test_database_failure_status_uses_shared_error(self):
        self.lookup.return_value = {"status": "db_error"}
        status, body = self.read()

        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "internal_error")

    def test_database_exception_uses_shared_error(self):
        self.lookup.side_effect = sqlite3.OperationalError("mock")
        status, body = self.read()

        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "internal_error")

    def test_unexpected_result_is_not_an_empty_success(self):
        for value in [None, [], {"status": "unexpected"}]:
            with self.subTest(value=value):
                self.lookup.return_value = value
                status, body = self.read()
                self.assertEqual(status, 500)
                self.assertEqual(body["error"]["code"], "internal_error")

    def test_invalid_success_body_is_rejected(self):
        for changes in [
            {"name": ""},
            {"accessibility_facts": "invalid"},
            {"accessibility_facts": [{"raw_value": 5}]},
        ]:
            with self.subTest(changes=changes):
                self.lookup.return_value = {**copy.deepcopy(SAMPLE), **changes}
                status, body = self.read()
                self.assertEqual(status, 500)

    def test_response_for_another_place_is_rejected(self):
        self.sample["place_id"] = "P001"
        status, body = self.read()

        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "internal_error")

    def test_extra_internal_fields_are_not_exposed(self):
        self.sample["database_path"] = "private.db"
        self.sample["accessibility_facts"][0]["source_file"] = "private.hwp"
        self.sample["spots"][0]["image_file"] = "private.jpg"
        status, body = self.read()

        self.assertEqual(status, 200)
        self.assertNotIn("status", body)
        self.assertNotIn("database_path", body)
        self.assertNotIn("source_file", body["accessibility_facts"][0])
        self.assertNotIn("image_file", body["spots"][0])

    def test_unknown_labels_are_not_fabricated(self):
        self.sample["category"] = None
        self.sample["spots"][0]["name"] = None
        self.sample["accessibility_facts"][0].update({
            "section": None, "facility_name": "", "description": None,
        })
        status, body = self.read()

        self.assertEqual(status, 200)
        self.assertIsNone(body["category"])
        self.assertIsNone(body["spots"][0]["name"])
        self.assertIsNone(body["accessibility_facts"][0]["description"])

    def test_place_id_is_forwarded_and_source_is_preserved(self):
        before = copy.deepcopy(self.sample)
        self.read()

        self.lookup.assert_called_once_with(place_id="P166")
        self.assertEqual(self.sample, before)

    def test_path_id_constraints(self):
        adapter = TypeAdapter(details_router.PlaceId)
        self.assertEqual(adapter.validate_python(" P166 "), "P166")
        for value in ["", " ", "A" * 81]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    adapter.validate_python(value)

    def test_get_route_is_declared(self):
        route = next(
            route for route in details_router.router.routes
            if route.path == "/places/{place_id}/details"
        )
        self.assertIn("GET", route.methods)
        self.assertNotIn("POST", route.methods)


if __name__ == "__main__":
    unittest.main()
