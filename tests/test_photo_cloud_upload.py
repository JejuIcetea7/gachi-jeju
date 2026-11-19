import base64
import csv
import hashlib
import io
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.upload_place_photos import (
    CATALOG_PATH, MANIFEST_PATH, Photo, check_target, cloudfront_base_url, main,
    matches_remote, publish_manifest, read_catalog, remote_photo, upload_catalog,
    upload_photo, validate_archive, validate_files,
)


BUCKET = "gachi-test-photos"
OWNER = "012345678901"
CDN = "https://dexample123.cloudfront.net"
FIELDS = ["photo_id", "place_id", "place_ids", "web_image_path", "web_size_bytes", "web_sha256"]


class ProviderError(Exception):
    def __init__(self, status, code="AccessDenied"):
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": code}}
        super().__init__("private credentials must not appear in reports")


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.heads = []
        self.bucket_checks = []
        self.denied = False
        self.corrupt_after_put = False
        self.race = None

    def head_bucket(self, **kwargs):
        self.bucket_checks.append(kwargs)
        if self.denied:
            raise ProviderError(403)

    def head_object(self, **kwargs):
        self.heads.append(kwargs)
        if self.denied:
            raise ProviderError(403)
        if kwargs["Key"] not in self.objects:
            raise ProviderError(404, "404")
        return dict(self.objects[kwargs["Key"]])

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        if self.denied:
            raise ProviderError(403)
        info = {
            "ContentLength": len(kwargs["Body"]), "ContentType": kwargs["ContentType"],
            "ChecksumSHA256": kwargs["ChecksumSHA256"], "ChecksumType": "FULL_OBJECT",
        }
        if self.race:
            self.objects[kwargs["Key"]] = info if self.race == "same" else {**info, "ChecksumSHA256": "other"}
            raise ProviderError(412, "PreconditionFailed")
        if self.corrupt_after_put:
            info["ChecksumSHA256"] = "incorrect"
        self.objects[kwargs["Key"]] = info
        return {}


class PhotoCloudUploadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = b"test photo contents"
        self.photo = Photo("P001_0001", "P001", ("P001", "P002"),
                           "data/processed/photos_web/P001/P001_0001.webp",
                           len(self.data), hashlib.sha256(self.data).hexdigest())
        path = self.root / self.photo.web_image_path
        path.parent.mkdir(parents=True)
        path.write_bytes(self.data)
        self.row = {
            "photo_id": self.photo.photo_id, "place_id": self.photo.place_id,
            "place_ids": '["P001", "P002"]', "web_image_path": self.photo.web_image_path,
            "web_size_bytes": str(self.photo.size), "web_sha256": self.photo.sha256,
        }
        self.write_catalog([self.row])
        self.client = FakeS3()

    def write_catalog(self, rows):
        path = self.root / CATALOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def remote_info(self, **updates):
        return {"ContentLength": self.photo.size, "ContentType": "image/webp",
                "ChecksumSHA256": self.photo.checksum, "ChecksumType": "FULL_OBJECT", **updates}

    def run_upload(self):
        return upload_catalog(self.client, self.root, [self.photo], bucket=BUCKET, owner=OWNER,
                              cdn_url=CDN, manifest=self.root / MANIFEST_PATH,
                              journal=self.root / "journal.jsonl", workers=1)

    def test_catalog_preserves_all_related_places_and_windows_path(self):
        self.write_catalog([{**self.row, "web_image_path": self.row["web_image_path"].replace("/", "\\")}])
        photos = read_catalog(self.root / CATALOG_PATH)
        self.assertEqual(photos, [self.photo])
        self.assertEqual(photos[0].key, "photos_web/P001/P001_0001.webp")

    def test_duplicate_ids_and_invalid_place_collections_are_rejected(self):
        for rows in ([self.row, self.row], [{**self.row, "place_ids": '"P001"'}],
                     [{**self.row, "place_ids": '["P002"]'}], [{**self.row, "place_ids": '["P001", "P001"]'}],
                     [{**self.row, "place_ids": "not json"}], [{**self.row, "place_ids": '["P001", null]'}]):
            with self.subTest(rows=rows):
                self.write_catalog(rows)
                with self.assertRaises(ValueError):
                    read_catalog(self.root / CATALOG_PATH)

    def test_traversal_wrong_owner_folder_bad_hash_and_bad_size_are_rejected(self):
        for update in ({"web_image_path": "../outside.webp"}, {"photo_id": "P002_0001"},
                       {"web_sha256": "bad"}, {"web_size_bytes": "0"},
                       {"web_size_bytes": "bad"}, {"web_size_bytes": str(65 * 1024 * 1024)}):
            with self.subTest(update=update):
                self.write_catalog([{**self.row, **update}])
                with self.assertRaises(ValueError):
                    read_catalog(self.root / CATALOG_PATH)

    def test_empty_catalog_and_missing_columns_are_rejected(self):
        self.write_catalog([])
        with self.assertRaises(ValueError):
            read_catalog(self.root / CATALOG_PATH)
        (self.root / CATALOG_PATH).write_text("photo_id\nP001_0001\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_catalog(self.root / CATALOG_PATH)

    def test_local_validation_preserves_files_and_reports_progress(self):
        progress = []
        validate_files(self.root, [self.photo], lambda *counts: progress.append(counts))
        self.assertEqual(progress, [(1, 1)])
        self.assertEqual((self.root / self.photo.web_image_path).read_bytes(), self.data)

    def test_missing_extra_changed_size_and_same_size_changed_content_fail(self):
        path = self.root / self.photo.web_image_path
        path.unlink()
        with self.assertRaises(ValueError):
            validate_files(self.root, [self.photo])
        path.write_bytes(self.data + b"x")
        with self.assertRaisesRegex(ValueError, "크기"):
            validate_files(self.root, [self.photo])
        path.write_bytes(b"x" * len(self.data))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_files(self.root, [self.photo])
        path.write_bytes(self.data)
        (path.parent / "extra.webp").write_bytes(self.data)
        with self.assertRaisesRegex(ValueError, "추가 1개"):
            validate_files(self.root, [self.photo])

    def archive(self, name, entries):
        path = self.root / name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w") as package:
                for key, data in entries:
                    package.writestr(key, data)
        return path

    def test_archive_validates_without_extraction(self):
        path = self.root / self.photo.web_image_path
        path.unlink()
        archive = self.archive("valid.zip", [(self.photo.key, self.data)])
        validate_archive(archive, [self.photo])
        self.assertFalse(path.exists())

    def test_archive_wrong_contents_and_duplicate_paths_are_rejected(self):
        cases = [[("../photo.webp", self.data)], [(self.photo.key, b"x" * len(self.data))],
                 [(self.photo.key, b"x")], [(self.photo.key, self.data), (self.photo.key, self.data)]]
        for index, entries in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_archive(self.archive(f"bad-{index}.zip", entries), [self.photo])

    def test_cloudfront_base_rejects_http_credentials_query_and_foreign_hosts(self):
        self.assertEqual(cloudfront_base_url(CDN + "/"), CDN)
        for value in ("http://dexample.cloudfront.net", "https://evil.test", CDN + "/photos_web",
                      CDN + "?key=value", CDN + "#fragment", "https://user@dexample.cloudfront.net",
                      "https://dexample.cloudfront.net:443", "https://dexample.cloudfront.net.evil.test"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cloudfront_base_url(value)

    def test_upload_target_requires_bucket_account_and_bounded_workers(self):
        self.assertEqual(check_target(BUCKET, CDN, OWNER, 4), CDN)
        for bucket, owner, workers in (("UPPERCASE", OWNER, 1), (BUCKET, "123", 1),
                                      (BUCKET, OWNER, 0), (BUCKET, OWNER, 5), (BUCKET, OWNER, True)):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                check_target(bucket, CDN, owner, workers)

    def test_missing_remote_is_distinct_from_access_denied(self):
        self.assertIsNone(remote_photo(self.client, BUCKET, OWNER, self.photo))
        self.client.denied = True
        with self.assertRaises(ProviderError):
            remote_photo(self.client, BUCKET, OWNER, self.photo)

    def test_upload_uses_checksum_condition_encryption_owner_and_no_acl(self):
        self.assertEqual(upload_photo(self.client, self.root, BUCKET, OWNER, self.photo), "uploaded")
        request = self.client.puts[0]
        self.assertEqual(request["Body"], self.data)
        self.assertEqual(request["IfNoneMatch"], "*")
        self.assertEqual(request["ContentType"], "image/webp")
        self.assertEqual(request["ExpectedBucketOwner"], OWNER)
        self.assertEqual(request["ServerSideEncryption"], "AES256")
        self.assertEqual(request["ChecksumSHA256"], base64.b64encode(hashlib.sha256(self.data).digest()).decode("ascii"))
        self.assertNotIn("ACL", request)
        self.assertTrue(all(head["ChecksumMode"] == "ENABLED" for head in self.client.heads))

    def test_identical_existing_photo_is_not_uploaded_again(self):
        self.client.objects[self.photo.key] = self.remote_info()
        self.assertEqual(upload_photo(self.client, self.root, BUCKET, OWNER, self.photo), "verified_existing")
        self.assertEqual(self.client.puts, [])

    def test_remote_conflict_unknown_checksum_and_bad_media_type_are_not_overwritten(self):
        for update in ({"ChecksumSHA256": None}, {"ContentLength": 1},
                       {"ContentType": "application/octet-stream"}, {"ChecksumType": "COMPOSITE"}):
            with self.subTest(update=update):
                self.client.objects[self.photo.key] = self.remote_info(**update)
                with self.assertRaises(ValueError):
                    upload_photo(self.client, self.root, BUCKET, OWNER, self.photo)
        self.assertEqual(self.client.puts, [])

    def test_local_change_after_preflight_prevents_put(self):
        (self.root / self.photo.web_image_path).write_bytes(b"x" * len(self.data))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            upload_photo(self.client, self.root, BUCKET, OWNER, self.photo)
        self.assertEqual(self.client.puts, [])

    def test_racing_same_upload_is_verified_and_different_upload_is_rejected(self):
        self.client.race = "same"
        self.assertEqual(upload_photo(self.client, self.root, BUCKET, OWNER, self.photo), "verified_existing")
        self.client.objects.clear()
        self.client.race = "different"
        with self.assertRaises(ValueError):
            upload_photo(self.client, self.root, BUCKET, OWNER, self.photo)

    def test_post_upload_checksum_failure_is_not_published(self):
        original = b"existing manifest contents"
        (self.root / MANIFEST_PATH).write_bytes(original)
        self.client.corrupt_after_put = True
        results = self.run_upload()
        self.assertEqual(results[self.photo.photo_id]["status"], "failed")
        self.assertEqual((self.root / MANIFEST_PATH).read_bytes(), original)

    def test_manifest_keeps_catalog_order_related_places_and_verified_urls(self):
        source_bytes = (self.root / CATALOG_PATH).read_bytes()
        results = self.run_upload()
        self.assertEqual(results[self.photo.photo_id]["status"], "uploaded")
        with (self.root / MANIFEST_PATH).open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]["cdn_url"], CDN + "/" + self.photo.key)
        self.assertEqual(json.loads(rows[0]["place_ids"]), ["P001", "P002"])
        self.assertEqual(rows[0]["web_sha256"], self.photo.sha256)
        self.assertEqual((self.root / CATALOG_PATH).read_bytes(), source_bytes)
        self.assertEqual(self.client.bucket_checks, [{"Bucket": BUCKET, "ExpectedBucketOwner": OWNER}])
        self.run_upload()
        self.assertEqual(len(self.client.puts), 1)

    def test_failed_or_incomplete_results_do_not_replace_manifest(self):
        for results in ({}, {self.photo.photo_id: {"status": "failed"}},
                        {self.photo.photo_id: {"status": "uploaded"}, "extra": {"status": "uploaded"}}):
            with self.subTest(results=results), self.assertRaises(ValueError):
                publish_manifest(self.root / MANIFEST_PATH, [self.photo], results, CDN)
        self.assertFalse((self.root / MANIFEST_PATH).exists())

    def test_partial_failure_keeps_previous_manifest_and_only_records_confirmed_success(self):
        other = Photo("P001_0002", "P001", ("P001",),
                      "data/processed/photos_web/P001/P001_0002.webp",
                      len(self.data), self.photo.sha256)
        (self.root / other.web_image_path).write_bytes(self.data)
        original = b"previous complete manifest"
        (self.root / MANIFEST_PATH).write_bytes(original)
        put = self.client.put_object

        def selective_put(**kwargs):
            if kwargs["Key"] == other.key:
                raise ProviderError(403)
            return put(**kwargs)

        with patch.object(self.client, "put_object", side_effect=selective_put):
            results = upload_catalog(
                self.client, self.root, [self.photo, other], bucket=BUCKET, owner=OWNER,
                cdn_url=CDN, manifest=self.root / MANIFEST_PATH,
                journal=self.root / "journal.jsonl", workers=2,
            )
        self.assertEqual(results[self.photo.photo_id]["status"], "uploaded")
        self.assertEqual(results[other.photo_id]["status"], "failed")
        self.assertEqual((self.root / MANIFEST_PATH).read_bytes(), original)

    def test_manifest_order_follows_catalog_even_if_completion_order_is_different(self):
        other = Photo("P001_0002", "P001", ("P001",),
                      "data/processed/photos_web/P001/P001_0002.webp",
                      len(self.data), self.photo.sha256)
        results = {other.photo_id: {"status": "uploaded", "verified_at": "2026-11-14T00:00:00+00:00"},
                   self.photo.photo_id: {"status": "verified_existing", "verified_at": "2026-11-14T00:00:00+00:00"}}
        publish_manifest(self.root / MANIFEST_PATH, [self.photo, other], results, CDN)
        with (self.root / MANIFEST_PATH).open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual([row["photo_id"] for row in rows], [self.photo.photo_id, other.photo_id])

    def test_interrupted_upload_does_not_publish_manifest(self):
        with self.assertRaises(KeyboardInterrupt):
            upload_catalog(
                self.client, self.root, [self.photo], bucket=BUCKET, owner=OWNER,
                cdn_url=CDN, manifest=self.root / MANIFEST_PATH,
                journal=self.root / "journal.jsonl", workers=1,
                progress=lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
            )
        self.assertFalse((self.root / MANIFEST_PATH).exists())

    def test_bucket_access_error_stops_before_scheduling_uploads(self):
        self.client.denied = True
        with self.assertRaises(ProviderError):
            self.run_upload()
        self.assertEqual(self.client.heads, [])
        self.assertEqual(self.client.puts, [])

    def test_journal_records_safe_error_code_without_provider_message(self):
        with patch.object(self.client, "put_object", side_effect=ProviderError(403)):
            results = self.run_upload()
        self.assertEqual(results[self.photo.photo_id]["error_code"], "AccessDenied")
        journal = (self.root / "journal.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("private credentials", journal)
        self.assertFalse((self.root / MANIFEST_PATH).exists())

    def test_default_cli_validates_without_sdk_or_aws_connection(self):
        with patch("scripts.upload_place_photos.create_s3_client") as create, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(["--root", str(self.root)]), 0)
        create.assert_not_called()
        self.assertFalse((self.root / MANIFEST_PATH).exists())
        report = json.loads((self.root / "outputs/photo_web/cloud_upload/validation.json").read_text(encoding="utf-8"))
        self.assertTrue(report["sha256_checked"])
        self.assertEqual(report["photos"], 1)

    def test_archive_cli_never_connects_to_aws(self):
        archive = self.archive("valid.zip", [(self.photo.key, self.data)])
        with patch("scripts.upload_place_photos.create_s3_client") as create, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(["--root", str(self.root), "--archive", str(archive)]), 0)
        create.assert_not_called()

    def test_invalid_local_data_stops_before_aws(self):
        (self.root / self.photo.web_image_path).write_bytes(b"changed")
        with patch("scripts.upload_place_photos.create_s3_client") as create, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(["--root", str(self.root), "--upload", "--profile", "test",
                                   "--bucket", BUCKET, "--cdn-base-url", CDN, "--expected-owner", OWNER]), 1)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
