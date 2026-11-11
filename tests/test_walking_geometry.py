import json
import unittest
from copy import deepcopy
from pathlib import Path

from backend.app.services.kakao_walking import parse_walking_response
from backend.app.services.walking_geometry import extract_walking_geometry


SAMPLE = (
    Path(__file__).parent
    / "sample_responses"
    / "kakao_walking_success.json"
)


class WalkingGeometryTests(unittest.TestCase):
    def test_saved_response_keeps_all_points(self):
        data = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        result = parse_walking_response(data)
        before = deepcopy(result)

        geometry = extract_walking_geometry(result["steps"])

        self.assertEqual(geometry["geometry_status"], "ok")
        self.assertEqual(len(geometry["paths"]), 6)
        self.assertEqual(sum(map(len, geometry["paths"])), 21)
        self.assertEqual(
            geometry["paths"][0][0],
            result["steps"][0]["path_points"][0],
        )
        self.assertEqual(result, before)

    def test_missing_steps_have_no_geometry(self):
        for steps in (None, [], {}, "invalid"):
            with self.subTest(steps=steps):
                self.assertEqual(
                    extract_walking_geometry(steps),
                    {"geometry_status": "missing", "paths": []},
                )

    def test_invalid_steps_are_omitted_without_bridging(self):
        first = [[126.2, 33.3], [126.21, 33.31]]
        last = [[126.4, 33.5], [126.41, 33.51]]
        steps = [
            {"path_points": first},
            None,
            {"path_points": [[126.3, 91], [126.31, 33.41]]},
            {"path_points": last},
        ]

        self.assertEqual(
            extract_walking_geometry(steps),
            {"geometry_status": "partial", "paths": [first, last]},
        )

    def test_invalid_coordinates_are_not_exposed(self):
        invalid_paths = [
            [],
            [[126.2, 33.3]],
            [[True, 33.3], [126.21, 33.31]],
            [["126.2", 33.3], [126.21, 33.31]],
            [[float("nan"), 33.3], [126.21, 33.31]],
            [[126.2, float("inf")], [126.21, 33.31]],
            [[181, 33.3], [126.21, 33.31]],
            [[126.2, -91], [126.21, 33.31]],
            [[126.2, 33.3, 0], [126.21, 33.31]],
        ]

        for points in invalid_paths:
            with self.subTest(points=points):
                self.assertEqual(
                    extract_walking_geometry([
                        {"path_points": points}
                    ]),
                    {"geometry_status": "missing", "paths": []},
                )

    def test_separate_steps_remain_separate_paths(self):
        first = [[126.2, 33.3], [126.21, 33.31]]
        second = [[126.4, 33.5], [126.41, 33.51]]

        self.assertEqual(
            extract_walking_geometry([
                {"path_points": first},
                {"path_points": second},
            ]),
            {"geometry_status": "ok", "paths": [first, second]},
        )

    def test_geometry_does_not_share_mutable_coordinates(self):
        steps = [
            {"path_points": [[126.2, 33.3], [126.21, 33.31]]}
        ]
        before = deepcopy(steps)

        geometry = extract_walking_geometry(steps)
        geometry["paths"][0][0][0] = 0

        self.assertEqual(steps, before)


if __name__ == "__main__":
    unittest.main()