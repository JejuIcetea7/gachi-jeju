import copy
import sqlite3
import unicodedata
import unittest
from unittest.mock import patch

from backend.app.tools import places


def make_place(place_id="P043", name="별빛누리공원"):
    return {
        "place_id": place_id,
        "name": name,
        "category": "공원",
        "latitude": 33.44517801,
        "longitude": 126.548587,
    }


class PlaceNameSearchTests(unittest.TestCase):
    def setUp(self):
        self.read = self.enterContext(
            patch.object(
                places,
                "read_rows",
                return_value=[make_place()],
            )
        )

    def test_exact_match_keeps_database_values(self):
        result = places.search_places_by_name(keyword="별빛누리공원")

        self.assertEqual(
            result,
            {
                "status": "ok",
                "places": [
                    {**make_place(), "name_match": "exact"},
                ],
            },
        )

    def test_partial_name_is_only_a_candidate(self):
        result = places.search_places_by_name(keyword="제주별빛누리공원")

        self.assertEqual(result["places"][0]["name_match"], "partial")
        self.assertEqual(result["places"][0]["place_id"], "P043")

    def test_whitespace_case_and_unicode_normalization(self):
        cases = [
            ("별빛누리공원", "  별빛 누리 공원 "),
            ("Jeju Park", " JEJU  park "),
            (
                "별빛누리공원",
                unicodedata.normalize("NFD", "별빛누리공원"),
            ),
        ]

        for name, keyword in cases:
            with self.subTest(name=name, keyword=keyword):
                self.read.return_value = [make_place(name=name)]

                result = places.search_places_by_name(keyword=keyword)

                self.assertEqual(
                    result["places"][0]["name_match"],
                    "exact",
                )

    def test_exact_match_is_ranked_before_partial_matches(self):
        self.read.return_value = [
            make_place("partial", "제주별빛누리공원"),
            make_place("exact"),
        ]

        result = places.search_places_by_name(keyword="별빛누리공원")

        self.assertEqual(
            [item["place_id"] for item in result["places"]],
            ["exact", "partial"],
        )

    def test_same_names_are_not_merged_or_auto_selected(self):
        self.read.return_value = [
            make_place("second"),
            make_place("first"),
        ]

        result = places.search_places_by_name(keyword="별빛누리공원")

        self.assertCountEqual(
            [item["place_id"] for item in result["places"]],
            ["first", "second"],
        )

    def test_result_limit_is_applied(self):
        self.read.return_value = [
            make_place(str(index), f"공원{index}")
            for index in range(8)
        ]

        result = places.search_places_by_name(
            keyword="공원",
            max_count=3,
        )

        self.assertEqual(len(result["places"]), 3)

    def test_empty_or_unknown_database_returns_not_found(self):
        for rows in [[], [make_place()]]:
            with self.subTest(rows=rows):
                self.read.return_value = rows

                result = places.search_places_by_name(
                    keyword="없는장소",
                )

                self.assertEqual(
                    result,
                    {"status": "not_found", "places": []},
                )

    def test_invalid_keyword_skips_database_lookup(self):
        for keyword in [None, 12, "", "  ", "x" * 101]:
            with self.subTest(keyword=keyword):
                self.read.reset_mock()

                result = places.search_places_by_name(
                    keyword=keyword,
                )

                self.assertEqual(result["status"], "invalid_input")
                self.read.assert_not_called()

    def test_invalid_result_limit_skips_database_lookup(self):
        for count in [0, -1, 11, True, 1.5, "5"]:
            with self.subTest(count=count):
                self.read.reset_mock()

                result = places.search_places_by_name(
                    keyword="별빛",
                    max_count=count,
                )

                self.assertEqual(result["status"], "invalid_input")
                self.read.assert_not_called()

    def test_sql_wildcards_are_literal_characters(self):
        for keyword in ["%", "_", "' OR 1=1 --"]:
            with self.subTest(keyword=keyword):
                result = places.search_places_by_name(
                    keyword=keyword,
                )

                self.assertEqual(result["places"], [])

    def test_database_error_is_reported(self):
        self.read.side_effect = sqlite3.OperationalError("test only")

        result = places.search_places_by_name(keyword="별빛")

        self.assertEqual(
            result,
            {"status": "db_error", "places": []},
        )

    def test_invalid_rows_are_omitted(self):
        self.read.return_value = [
            make_place(name=None),
            make_place(name=" "),
            make_place(place_id=None),
            make_place(place_id=""),
            make_place(),
        ]

        result = places.search_places_by_name(keyword="별빛")

        self.assertEqual(len(result["places"]), 1)

    def test_input_rows_are_preserved(self):
        rows = [
            make_place("second"),
            make_place("first"),
        ]
        self.read.return_value = rows
        before = copy.deepcopy(rows)

        places.search_places_by_name(keyword="별빛")

        self.assertEqual(rows, before)


if __name__ == "__main__":
    unittest.main()