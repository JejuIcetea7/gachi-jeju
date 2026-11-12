import copy
import sqlite3
import unittest
from unittest.mock import patch

from backend.app.services import place_verification as verification


def make_plan(title="별빛누리공원", place_id=None, item_type="visit"):
    return {
        "days": [{
            "day": 1,
            "items": [{
                "item_type": item_type,
                "title": title,
                "place_id": place_id,
            }],
        }],
    }


def search_record(tool="search_places_by_name", place_id="P043"):
    return {
        "tool": tool,
        "input": {"keyword": "별빛누리공원"},
        "output": {
            "status": "ok",
            "places": [{"place_id": place_id}],
        },
    }


def detail_record(place_id="P043", name="별빛누리공원"):
    return {
        "tool": "get_place_details",
        "input": {"place_id": place_id},
        "output": {
            "status": "ok",
            "place_id": place_id,
            "name": name,
        },
    }


def database_row(
    place_id="P043",
    name="별빛누리공원",
    name_match="exact",
):
    return {
        "place_id": place_id,
        "name": name,
        "name_match": name_match,
    }


class PlaceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.lookup = self.enterContext(
            patch.object(
                verification,
                "search_places_by_name",
                return_value={
                    "status": "ok",
                    "places": [database_row()],
                },
            )
        )

    def restore(self, plan=None, records=None):
        return verification.restore_missing_place_ids(
            make_plan() if plan is None else plan,
            [search_record(), detail_record()]
            if records is None else records,
        )

    def test_missing_id_is_restored_from_confirmed_evidence(self):
        result = self.restore()

        self.assertEqual(
            result["days"][0]["items"][0]["place_id"],
            "P043",
        )

    def test_normalized_exact_name_is_allowed(self):
        result = self.restore(
            plan=make_plan(title=" 별빛 누리 공원 "),
        )

        self.assertEqual(
            result["days"][0]["items"][0]["place_id"],
            "P043",
        )

    def test_both_search_tools_can_supply_evidence(self):
        for tool in ["search_places", "search_places_by_name"]:
            with self.subTest(tool=tool):
                result = self.restore(
                    records=[search_record(tool), detail_record()],
                )

                self.assertEqual(
                    result["days"][0]["items"][0]["place_id"],
                    "P043",
                )

    def test_search_without_details_is_not_enough(self):
        result = self.restore(records=[search_record()])

        self.assertIsNone(result["days"][0]["items"][0]["place_id"])
        self.lookup.assert_not_called()

    def test_details_without_search_is_not_enough(self):
        result = self.restore(records=[detail_record()])

        self.assertIsNone(result["days"][0]["items"][0]["place_id"])
        self.lookup.assert_not_called()

    def test_failed_search_is_not_evidence(self):
        record = search_record()
        record["output"]["status"] = "db_error"

        result = self.restore(records=[record, detail_record()])

        self.assertIsNone(result["days"][0]["items"][0]["place_id"])
        self.lookup.assert_not_called()

    def test_failed_or_inconsistent_details_are_not_evidence(self):
        failed = detail_record()
        failed["output"]["status"] = "not_found"

        wrong = detail_record()
        wrong["input"]["place_id"] = "different"

        missing = detail_record()
        del missing["input"]

        for record in [failed, wrong, missing]:
            with self.subTest(record=record):
                result = self.restore(
                    records=[search_record(), record],
                )

                self.assertIsNone(
                    result["days"][0]["items"][0]["place_id"],
                )

        self.lookup.assert_not_called()

    def test_partial_title_is_not_auto_linked(self):
        result = self.restore(
            plan=make_plan(title="제주별빛누리공원"),
        )

        self.assertIsNone(result["days"][0]["items"][0]["place_id"])
        self.lookup.assert_not_called()

    def test_multiple_detail_ids_for_same_name_are_not_auto_linked(self):
        result = self.restore(records=[
            search_record(place_id="P043"),
            search_record(place_id="P999"),
            detail_record("P043"),
            detail_record("P999"),
        ])

        self.assertIsNone(result["days"][0]["items"][0]["place_id"])
        self.lookup.assert_not_called()

    def test_duplicate_database_names_are_not_auto_linked(self):
        self.lookup.return_value = {
            "status": "ok",
            "places": [
                database_row("P043"),
                database_row("P999"),
            ],
        }

        result = self.restore()

        self.assertIsNone(result["days"][0]["items"][0]["place_id"])

    def test_database_id_must_match_tool_evidence(self):
        self.lookup.return_value = {
            "status": "ok",
            "places": [database_row("P999")],
        }

        result = self.restore()

        self.assertIsNone(result["days"][0]["items"][0]["place_id"])

    def test_unknown_and_partial_database_matches_are_not_auto_linked(self):
        responses = [
            {"status": "not_found", "places": []},
            {
                "status": "ok",
                "places": [database_row(name_match="partial")],
            },
            {"status": "invalid_input", "places": []},
        ]

        for response in responses:
            with self.subTest(response=response):
                self.lookup.return_value = response

                result = self.restore()

                self.assertIsNone(
                    result["days"][0]["items"][0]["place_id"],
                )

    def test_existing_id_is_preserved(self):
        result = self.restore(
            plan=make_plan(place_id="previous-id"),
        )

        self.assertEqual(
            result["days"][0]["items"][0]["place_id"],
            "previous-id",
        )
        self.lookup.assert_not_called()

    def test_repeated_places_use_one_database_lookup_and_preserve_inputs(self):
        plan = make_plan()
        plan["days"].append({
            "day": 2,
            "items": [
                copy.deepcopy(plan["days"][0]["items"][0]),
            ],
        })
        records = [search_record(), detail_record()]
        before = copy.deepcopy((plan, records))

        result = self.restore(plan, records)

        self.assertTrue(
            all(
                day["items"][0]["place_id"] == "P043"
                for day in result["days"]
            )
        )
        self.assertEqual((plan, records), before)
        self.lookup.assert_called_once()

    def test_move_items_are_not_changed(self):
        plan = make_plan(item_type="move")

        result = self.restore(plan)

        self.assertEqual(result, plan)
        self.lookup.assert_not_called()

    def test_malformed_trace_is_ignored(self):
        traces = [
            None,
            {},
            "invalid",
            [
                None,
                {},
                {"tool": [], "output": {}},
                {
                    "tool": "search_places",
                    "output": {"status": "ok", "places": "invalid"},
                },
                {
                    "tool": "get_place_details",
                    "output": "invalid",
                },
            ],
        ]

        for trace in traces:
            with self.subTest(trace=trace):
                result = verification.restore_missing_place_ids(
                    make_plan(),
                    trace,
                )

                self.assertIsNone(
                    result["days"][0]["items"][0]["place_id"],
                )

        self.lookup.assert_not_called()

    def test_database_error_is_not_treated_as_no_match(self):
        self.lookup.return_value = {
            "status": "db_error",
            "places": [],
        }

        with self.assertRaises(sqlite3.Error):
            self.restore()

    def test_blank_ids_can_be_restored(self):
        for value in ["", "   "]:
            with self.subTest(value=value):
                result = self.restore(
                    plan=make_plan(place_id=value),
                )

                self.assertEqual(
                    result["days"][0]["items"][0]["place_id"],
                    "P043",
                )


if __name__ == "__main__":
    unittest.main()