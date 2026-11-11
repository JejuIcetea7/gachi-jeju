import json
import unittest
from copy import deepcopy
from pathlib import Path

from backend.app.services.transit_geometry import extract_transit_geometry


SAMPLE = (
    Path(__file__).parent
    / "sample_responses"
    / "kakao_transit_geometry_success.json"
)


def make_step(points, kind="BUS"):
    return {
        "properties": {"type": kind},
        "path": {"points": points},
    }


class TransitGeometryTests(unittest.TestCase):
    def test_saved_response_keeps_all_points(self):
        data = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        steps = data["routes"][0]["steps"]
        before = deepcopy(steps)

        geometry = extract_transit_geometry(steps)

        self.assertEqual(geometry["geometry_status"], "ok")
        self.assertEqual(
            [len(path) for path in geometry["paths"]],
            [15, 11, 659, 99],
        )
        self.assertEqual(sum(map(len, geometry["paths"])), 784)
        self.assertEqual(
            geometry["paths"],
            [step["path"]["points"] for step in steps],
        )
        self.assertEqual(steps, before)

    def test_missing_steps_have_no_geometry(self):
        for steps in (None, [], {}, "invalid"):
            with self.subTest(steps=steps):
                self.assertEqual(
                    extract_transit_geometry(steps),
                    {"geometry_status": "missing", "paths": []},
                )

    def test_invalid_steps_are_omitted_without_bridging(self):
        first = [[126.2, 33.3], [126.21, 33.31]]
        last = [[126.4, 33.5], [126.41, 33.51]]
        steps = [
            make_step(first),
            None,
            make_step([[126.3, 91], [126.31, 33.41]]),
            make_step(last),
        ]

        self.assertEqual(
            extract_transit_geometry(steps),
            {"geometry_status": "partial", "paths": [first, last]},
        )

    def test_invalid_paths_and_coordinates_are_not_exposed(self):
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
        invalid_steps = [
            None,
            {},
            {"path": None},
            {"path": []},
            {"path": "invalid"},
            *[make_step(points) for points in invalid_paths],
        ]

        for step in invalid_steps:
            with self.subTest(step=step):
                self.assertEqual(
                    extract_transit_geometry([step]),
                    {"geometry_status": "missing", "paths": []},
                )

    def test_bus_and_walking_paths_remain_separate(self):
        bus = [[126.2, 33.3], [126.21, 33.31]]
        walking = [[126.4, 33.5], [126.41, 33.51]]

        self.assertEqual(
            extract_transit_geometry([
                make_step(bus, "BUS"),
                make_step(walking, "WALKING"),
            ]),
            {"geometry_status": "ok", "paths": [bus, walking]},
        )

    def test_geometry_does_not_share_mutable_coordinates(self):
        steps = [
            make_step([[126.2, 33.3], [126.21, 33.31]])
        ]
        before = deepcopy(steps)

        geometry = extract_transit_geometry(steps)
        geometry["paths"][0][0][0] = 0

        self.assertEqual(steps, before)


if __name__ == "__main__":
    unittest.main()