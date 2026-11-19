import json
import sqlite3
import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import FileResponse

from backend.app.routers import place_images


class PlaceImageApiTests(unittest.TestCase):
    def setUp(self):
        self.read = self.enterContext(
            patch.object(place_images, "list_place_images")
        )
        self.image_id = "a" * 64
        self.path = Path("_virtual_image_test/photo.jpg")
        self.result = {
            "place_id": "P001",
            "name": "테스트 장소",
            "images": [{
                "image_id": self.image_id,
                "path": self.path,
                "media_type": "image/jpeg",
                "caption": "입구",
                "alt": "테스트 장소 · 입구",
            }],
        }
        self.read.return_value = self.result

    def test_list_exposes_urls_without_local_paths(self):
        result = place_images.get_place_images("P001")
        self.assertEqual(result, {
            "place_id": "P001",
            "name": "테스트 장소",
            "images": [{
                "image_id": self.image_id,
                "url": f"/places/P001/images/{self.image_id}",
                "caption": "입구",
                "alt": "테스트 장소 · 입구",
            }],
        })
        place_images.PlaceImagesResponse.model_validate(result)
        self.assertNotIn("_virtual_image_test", json.dumps(result))

    def test_missing_photos_return_empty_list(self):
        self.result["images"] = []
        result = place_images.get_place_images("P001")
        self.assertEqual(result["images"], [])
        self.assertEqual(result["name"], "테스트 장소")

    def test_unknown_place_is_not_found(self):
        self.read.return_value = None
        result = place_images.get_place_images("missing")
        self.assertEqual(result.status_code, 404)
        self.assertEqual(
            json.loads(result.body)["error"]["code"], "place_not_found"
        )

    def test_database_error_uses_shared_error_format(self):
        self.read.side_effect = sqlite3.OperationalError("test")

        for operation in [
            lambda: place_images.get_place_images("P001"),
            lambda: place_images.get_place_image("P001", self.image_id),
        ]:
            result = operation()
            self.assertEqual(result.status_code, 500)
            self.assertEqual(
                json.loads(result.body)["error"]["code"], "internal_error"
            )

    def test_file_response_uses_confirmed_path_and_media_type(self):
        image_stat = SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_size=123,
            st_mtime=0,
        )
        with patch.object(Path, "stat", return_value=image_stat):
            result = place_images.get_place_image("P001", self.image_id)

        self.assertIsInstance(result, FileResponse)
        self.assertEqual(result.path, self.path)
        self.assertEqual(result.media_type, "image/jpeg")
        self.assertEqual(result.headers["x-content-type-options"], "nosniff")
        self.assertEqual(result.headers["content-length"], "123")

    def test_unknown_image_is_not_found(self):
        result = place_images.get_place_image("P001", "missing")
        self.assertEqual(result.status_code, 404)
        self.assertEqual(
            json.loads(result.body)["error"]["code"], "image_not_found"
        )

    def test_disappeared_file_and_directory_are_not_found(self):
        for change in [
            {"side_effect": FileNotFoundError()},
            {"return_value": SimpleNamespace(st_mode=stat.S_IFDIR)},
        ]:
            with patch.object(Path, "stat", **change):
                result = place_images.get_place_image("P001", self.image_id)

            self.assertEqual(result.status_code, 404)

    def test_both_get_routes_are_registered(self):
        app = FastAPI()
        app.include_router(place_images.router)
        paths = app.openapi()["paths"]

        for path in [
            "/places/{place_id}/images",
            "/places/{place_id}/images/{image_id}",
        ]:
            self.assertIn("get", paths[path])


if __name__ == "__main__":
    unittest.main()