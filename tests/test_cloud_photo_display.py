import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.responses import RedirectResponse

from backend.app.routers import place_images as router
from backend.app.services import cloud_photos, place_images
from scripts.export_photo_cloud_urls import export_urls, main


CDN = "https://dexample123.cloudfront.net"


class CloudPhotoDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.web = self.root / cloud_photos.WEB_CATALOG
        self.cloud = self.root / cloud_photos.CLOUD_CATALOG
        self.row = {
            "photo_id": "P001_0001", "place_id": "P001", "place_ids": '["P001", "P002"]',
            "web_image_path": "data/processed/photos_web/P001/P001_0001.webp",
            "web_size_bytes": "123", "web_sha256": "a" * 64,
            "description": "다른 장소를 섞은 요약이나 내부 경로는 공개하지 않습니다.",
            "sources": json.dumps([
                {"place_id": "P001", "spot_name": "입구", "description": "  원문\n설명입니다.  ",
                 "mapping_status": "table_caption", "source_file": "data/raw/private-document.hwp"},
                {"place_id": "P002", "spot_name": "주차장", "description": "두 번째 장소의 설명",
                 "mapping_status": "unconfirmed"},
            ], ensure_ascii=False),
        }
        self.write_web([self.row])
        export_urls(self.root, CDN)
        cloud_photos._catalog_index.cache_clear()
        self.addCleanup(cloud_photos._catalog_index.cache_clear)

    def write_web(self, rows):
        self.web.parent.mkdir(parents=True, exist_ok=True)
        with self.web.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=list(self.row))
            writer.writeheader()
            writer.writerows(rows)

    def cloud_rows(self):
        with self.cloud.open(encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source))

    def write_cloud(self, rows):
        with self.cloud.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=cloud_photos.MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def images(self, place_id="P001"):
        return cloud_photos.list_cloud_images(self.root, place_id, "테스트 장소")

    def test_export_generates_urls_without_claiming_remote_verification(self):
        original = self.web.read_bytes()
        rows = self.cloud_rows()
        self.assertEqual(rows[0]["cdn_url"], CDN + "/photos_web/P001/P001_0001.webp")
        self.assertEqual(rows[0]["upload_status"], "url_generated")
        self.assertEqual(rows[0]["verified_at"], "")
        self.assertEqual(json.loads(rows[0]["place_ids"]), ["P001", "P002"])
        self.assertEqual(self.web.read_bytes(), original)
        self.assertFalse((self.root / "data/processed/photos_web").exists())

    def test_shared_photo_uses_place_ids_and_only_the_requested_places_source(self):
        first, second = self.images("P001")[0], self.images("P002")[0]
        self.assertEqual(first["cdn_url"], second["cdn_url"])
        self.assertEqual(first["image_id"], second["image_id"])
        self.assertRegex(first["image_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(first["contexts"], [{"spot_name": "입구", "description": "  원문\n설명입니다.  ", "mapping_confirmed": True}])
        self.assertEqual(second["contexts"], [{"spot_name": "주차장", "description": "두 번째 장소의 설명", "mapping_confirmed": False}])
        self.assertNotIn("두 번째 장소", str(first))
        self.assertNotIn("data/raw/", str(first))
        self.assertNotIn("요약", str(first))

    def test_unconfirmed_alt_and_missing_place_source_are_not_filled_from_other_places(self):
        self.assertIn("장소 연결 미확인", self.images("P002")[0]["alt"])
        self.write_web([{**self.row, "sources": json.dumps([{"place_id": "P001", "spot_name": "입구", "description": "P001만의 설명"}])}])
        image = self.images("P002")[0]
        self.assertEqual(image["contexts"], [{"spot_name": "", "description": "", "mapping_confirmed": False}])
        self.assertNotIn("P001만의 설명", str(image))

    def test_unknown_or_malformed_mapping_labels_are_unconfirmed(self):
        for status in ("new_unknown_status", None, ["table_caption"], {"status": "table_caption"}):
            with self.subTest(status=status):
                self.write_web([{**self.row, "sources": json.dumps([
                    {"place_id": "P001", "spot_name": "", "description": "", "mapping_status": status},
                ])}])
                cloud_photos._catalog_index.cache_clear()
                self.assertFalse(self.images()[0]["contexts"][0]["mapping_confirmed"])

    def test_mixed_contexts_keep_their_own_confirmation_and_duplicates_are_removed(self):
        contexts = [
            {"place_id": "P001", "spot_name": "입구", "description": "확인한 설명", "mapping_status": "table_caption"},
            {"place_id": "P001", "spot_name": "입구", "description": "확인한 설명", "mapping_status": "table_caption"},
            {"place_id": "P001", "spot_name": "근처 지점", "description": "미확인 설명", "mapping_status": "unconfirmed"},
        ]
        self.write_web([{**self.row, "sources": json.dumps(contexts)}])
        image = self.images()[0]
        self.assertEqual(len(image["contexts"]), 2)
        self.assertEqual([value["mapping_confirmed"] for value in image["contexts"]], [True, False])

    def test_cached_results_are_independent_and_catalog_change_is_reflected(self):
        image = self.images()[0]
        image["contexts"][0]["description"] = "changed by caller"
        self.assertEqual(self.images()[0]["contexts"][0]["description"], "  원문\n설명입니다.  ")
        sources = json.loads(self.row["sources"])
        sources[0]["description"] = "갱신된 설명입니다."
        self.write_web([{**self.row, "sources": json.dumps(sources)}])
        signature = self.web.stat()
        os.utime(self.web, ns=(signature.st_atime_ns, signature.st_mtime_ns + 1_000_000))
        self.assertEqual(self.images()[0]["contexts"][0]["description"], "갱신된 설명입니다.")

    def test_no_cloud_catalog_uses_legacy_and_known_place_without_photos_is_empty(self):
        self.assertEqual(self.images("P003"), [])
        self.cloud.unlink()
        self.assertIsNone(self.images())

    def test_bad_urls_and_photo_paths_are_rejected(self):
        valid = self.cloud_rows()[0]
        for url in ("http://dexample123.cloudfront.net/photos_web/P001/P001_0001.webp",
                    "https://example.invalid/photos_web/P001/P001_0001.webp",
                    CDN + "/photos_web/P002/P002_0001.webp", valid["cdn_url"] + "?token=private",
                    valid["cdn_url"] + "#fragment", "https://user@dexample123.cloudfront.net/photos_web/P001/P001_0001.webp",
                    "https://["):
            with self.subTest(url=url):
                self.write_cloud([{**valid, "cdn_url": url}])
                cloud_photos._catalog_index.cache_clear()
                with self.assertRaises(cloud_photos.PhotoCatalogError):
                    self.images()

    def test_stale_hash_size_mapping_status_and_place_connections_are_rejected(self):
        valid = self.cloud_rows()[0]
        for update in ({"web_sha256": "b" * 64}, {"web_size_bytes": "9"},
                       {"place_ids": '["P001"]'}, {"s3_key": "../secret"},
                       {"upload_status": "failed"}, {"web_image_path": "../secret.webp"}):
            with self.subTest(update=update):
                self.write_cloud([{**valid, **update}])
                cloud_photos._catalog_index.cache_clear()
                with self.assertRaises(cloud_photos.PhotoCatalogError):
                    self.images()

    def test_duplicate_unknown_and_missing_photo_ids_are_rejected(self):
        valid = self.cloud_rows()[0]
        for rows in ([valid, valid], [{**valid, "photo_id": "P001_0099"}], []):
            with self.subTest(rows=rows):
                self.write_cloud(rows)
                cloud_photos._catalog_index.cache_clear()
                with self.assertRaises(cloud_photos.PhotoCatalogError):
                    self.images()

    def test_missing_web_catalog_is_an_error_instead_of_empty_success(self):
        self.web.unlink()
        with self.assertRaises(cloud_photos.PhotoCatalogError):
            self.images()

    def test_invalid_export_preserves_previous_manifest(self):
        previous = self.cloud.read_bytes()
        self.write_web([{**self.row, "web_image_path": "../outside.webp"}])
        with self.assertRaises(cloud_photos.PhotoCatalogError):
            export_urls(self.root, CDN)
        self.assertEqual(self.cloud.read_bytes(), previous)

    def test_export_cli_is_offline(self):
        with patch("scripts.upload_place_photos.create_s3_client") as create, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(["--root", str(self.root), "--cdn-base-url", CDN]), 0)
        create.assert_not_called()

    def test_service_uses_database_place_name_and_does_not_query_legacy_spots(self):
        with patch.object(place_images, "PROJECT_ROOT", self.root), patch.object(place_images, "read_rows") as read:
            read.return_value = [{"place_id": "P001", "name": "DB의 장소 이름"}]
            result = place_images.list_place_images("P001")
        read.assert_called_once()
        self.assertEqual(result["name"], "DB의 장소 이름")
        self.assertIn("DB의 장소 이름", result["images"][0]["alt"])

    def test_api_exposes_cdn_urls_and_contexts_without_private_metadata(self):
        image = self.images()[0]
        with patch.object(router, "list_place_images", return_value={"place_id": "P001", "name": "테스트 장소", "images": [image]}):
            result = router.get_place_images("P001")
        payload = router.PlaceImagesResponse.model_validate(result).model_dump(exclude_none=True)
        self.assertEqual(payload["images"][0]["url"], CDN + "/photos_web/P001/P001_0001.webp")
        self.assertEqual(payload["images"][0]["photo_id"], "P001_0001")
        self.assertIn("contexts", payload["images"][0])
        self.assertNotIn("data/raw/", json.dumps(payload))
        self.assertNotIn("data/processed/", json.dumps(payload))
        self.assertNotIn("sources", payload["images"][0])

    def test_old_client_file_request_redirects_to_the_same_cloud_photo(self):
        image = self.images()[0]
        with patch.object(router, "list_place_images", return_value={"place_id": "P001", "name": "테스트 장소", "images": [image]}):
            result = router.get_place_image("P001", image["image_id"])
        self.assertIsInstance(result, RedirectResponse)
        self.assertEqual(result.status_code, 307)
        self.assertEqual(result.headers["location"], image["cdn_url"])

    def test_bad_catalog_uses_shared_api_error_for_both_routes(self):
        with patch.object(router, "list_place_images", side_effect=cloud_photos.PhotoCatalogError("private path")):
            for result in (router.get_place_images("P001"), router.get_place_image("P001", "a" * 64)):
                self.assertEqual(result.status_code, 500)
                self.assertEqual(json.loads(result.body)["error"]["code"], "internal_error")
                self.assertNotIn("private path", result.body.decode())


if __name__ == "__main__":
    unittest.main()
