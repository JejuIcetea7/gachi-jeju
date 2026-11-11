import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services import place_images


class PlaceImageTests(unittest.TestCase):
    def setUp(self):
        self.project = (Path.cwd() / "_virtual_image_test").resolve()
        self.root = self.project / "data" / "raw" / "jeju_datahub"
        self.source = "data/raw/jeju_datahub/hub_1/001-2.csv"
        self.photo = self.root / "hub_1" / "photo.jpg"
        self.existing = {self.photo}

        self.enterContext(patch.object(place_images, "PROJECT_ROOT", self.project))
        self.enterContext(patch.object(place_images, "IMAGE_ROOT", self.root))
        self.enterContext(patch.object(
            Path,
            "is_file",
            autospec=True,
            side_effect=lambda path: path in self.existing,
        ))
        self.read = self.enterContext(patch.object(place_images, "read_rows"))

    def test_existing_image_is_returned(self):
        self.assertEqual(
            place_images.resolve_image_path(self.source, "photo.jpg"),
            self.photo,
        )

    def test_missing_image_is_omitted(self):
        self.assertIsNone(
            place_images.resolve_image_path(self.source, "missing.jpg")
        )

    def test_unsupported_file_is_omitted(self):
        self.existing.add(self.photo.with_suffix(".html"))
        self.assertIsNone(
            place_images.resolve_image_path(self.source, "photo.html")
        )

    def test_paths_outside_original_folder_are_rejected(self):
        outside = self.project / "private.jpg"
        other_place = self.root / "hub_2" / "photo.jpg"
        self.existing.update({outside, other_place})

        for source, image in [
            (self.source, str(outside)),
            (self.source, "../hub_2/photo.jpg"),
            ("private.csv", "photo.jpg"),
        ]:
            with self.subTest(source=source, image=image):
                self.assertIsNone(
                    place_images.resolve_image_path(source, image)
                )

    def test_unknown_place_returns_none(self):
        self.read.return_value = []
        self.assertIsNone(place_images.list_place_images("missing"))
        self.read.assert_called_once()

    def test_place_without_available_photos_keeps_its_name(self):
        self.read.side_effect = [
            [{"place_id": "P001", "name": "테스트 장소"}],
            [{
                "name": "입구",
                "source_file": self.source,
                "image_file": "missing.jpg",
            }],
        ]

        self.assertEqual(place_images.list_place_images("P001"), {
            "place_id": "P001",
            "name": "테스트 장소",
            "images": [],
        })

    def test_duplicate_photos_are_listed_once(self):
        row = {
            "name": "입구",
            "source_file": self.source,
            "image_file": "photo.jpg",
        }
        self.read.side_effect = [
            [{"place_id": "P001", "name": "테스트 장소"}],
            [row, dict(row)],
        ]

        result = place_images.list_place_images("P001")
        self.assertEqual(len(result["images"]), 1)

        image = result["images"][0]
        self.assertEqual(image["path"], self.photo)
        self.assertEqual(image["media_type"], "image/jpeg")
        self.assertEqual(image["alt"], "테스트 장소 · 입구")
        self.assertRegex(image["image_id"], r"^[0-9a-f]{64}$")

    def test_query_uses_place_id_parameter(self):
        self.read.side_effect = [
            [{"place_id": "P001", "name": "테스트 장소"}],
            [],
        ]

        place_images.list_place_images("P001")

        for call in self.read.call_args_list:
            self.assertEqual(call.args[1], ("P001",))


if __name__ == "__main__":
    unittest.main()