import copy
import hashlib
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from PIL import Image

from backend.app.services import place_representative_image as service


def picture(color="orange", size=(120, 80)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


def photo(name, *, confirmed=True, number=1):
    return {
        "image_id": str(number) * 64,
        "photo_id": f"P044_{number:04d}",
        "cdn_url": (
            f"https://dexample.cloudfront.net/"
            f"photos_web/P044/P044_{number:04d}.webp"
        ),
        "caption": name,
        "contexts": [{
            "spot_name": name,
            "description": "",
            "mapping_confirmed": confirmed,
        }],
    }


class RepresentativeSelectionTests(unittest.TestCase):
    def test_view_then_entrance_are_preferred_to_facilities(self):
        values = [
            photo("장애인 화장실", number=1),
            photo("정문", number=2),
            photo("수목원 전경", number=3),
        ]
        self.assertEqual(
            service.select_representative_image(values)["photo_id"],
            "P044_0003",
        )
        self.assertEqual(
            service.select_representative_image(values[:2])["photo_id"],
            "P044_0002",
        )

    def test_facility_view_does_not_outrank_the_actual_entrance(self):
        values = [photo("주차장 전경"), photo("정문", number=2)]
        self.assertEqual(
            service.select_representative_image(values)["photo_id"],
            "P044_0002",
        )

    def test_unconfirmed_description_does_not_override_confirmed_context(self):
        values = [
            photo("전체 전경", confirmed=False),
            photo("입구", number=2),
        ]
        self.assertEqual(
            service.select_representative_image(values)["photo_id"],
            "P044_0002",
        )

    def test_ties_keep_catalog_order_and_results_are_independent(self):
        values = [
            photo("관람로", number=2),
            photo("관람로", number=1),
        ]
        before = copy.deepcopy(values)
        result = service.select_representative_image(values)
        self.assertEqual(result["photo_id"], "P044_0002")
        result["contexts"][0]["spot_name"] = "수정"
        self.assertEqual(values, before)
        self.assertIsNone(service.select_representative_image([]))

    def test_legacy_captions_can_select_the_entrance(self):
        values = [{"caption": "주차장"}, {"caption": "입구"}]
        self.assertEqual(
            service.select_representative_image(values), values[1],
        )


class RepresentativeLoadingTests(unittest.TestCase):
    def setUp(self):
        service._cache.clear()
        self.data = picture()
        self.image = photo("입구")
        self.lookup = self.enterContext(patch.object(
            service,
            "list_place_images",
            return_value={
                "place_id": "P044",
                "name": "한라수목원",
                "images": [self.image],
            },
        ))
        self.spec = self.enterContext(patch.object(
            service,
            "_cloud_spec",
            return_value={
                "size": len(self.data),
                "web_sha256": hashlib.sha256(self.data).hexdigest(),
            },
        ))
        self.read = self.enterContext(patch.object(
            service, "_read_cdn", return_value=self.data,
        ))

    def tearDown(self):
        service._cache.clear()

    def test_registered_bytes_are_resized_without_stretching_and_cached(self):
        content = service.get_representative_image("P044")
        with Image.open(BytesIO(content)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (640, 400))
        self.assertLessEqual(len(content), service.MAX_THUMBNAIL_BYTES)
        self.assertEqual(
            service.get_representative_image("P044"), content,
        )
        self.read.assert_called_once()

    def test_hash_size_and_decoding_failures_never_become_cached_photos(self):
        for data in (b"bad", self.data[:-1], picture("blue")):
            with self.subTest(data_length=len(data)):
                self.read.return_value = data
                self.assertIsNone(service.get_representative_image("P044"))
                self.assertFalse(service._cache)

    def test_invalid_image_with_matching_hash_is_still_rejected(self):
        self.data = b"this is not an image"
        self.read.return_value = self.data
        self.spec.return_value = {
            "size": len(self.data),
            "web_sha256": hashlib.sha256(self.data).hexdigest(),
        }
        self.assertIsNone(service.get_representative_image("P044"))

    def test_arbitrary_urls_and_oversized_sources_are_not_fetched(self):
        for url in (
            "http://127.0.0.1/private",
            "https://example.com/a",
            self.image["cdn_url"] + "?x=1",
        ):
            with self.subTest(url=url):
                self.image["cdn_url"] = url
                self.assertIsNone(service.get_representative_image("P044"))

        self.image["cdn_url"] = photo("입구")["cdn_url"]
        self.spec.return_value["size"] = service.MAX_SOURCE_BYTES + 1
        self.assertIsNone(service.get_representative_image("P044"))
        self.read.assert_not_called()

    def test_missing_place_missing_photo_and_invalid_id_do_not_fetch(self):
        self.assertIsNone(service.get_representative_image("../private"))
        self.lookup.assert_not_called()

        self.lookup.return_value = None
        self.assertIsNone(service.get_representative_image("P044"))
        self.lookup.return_value = {"images": []}
        self.assertIsNone(service.get_representative_image("P044"))
        self.read.assert_not_called()

    def test_timeout_is_capped_and_transient_failure_is_not_cached(self):
        self.read.side_effect = TimeoutError()
        self.assertIsNone(
            service.get_representative_image("P044", timeout=0.25),
        )
        self.assertFalse(service._cache)
        self.assertEqual(self.read.call_args.args[2], 0.25)

        self.read.side_effect = None
        self.assertIsNotNone(service.get_representative_image("P044"))

    def test_changed_checksum_does_not_reuse_the_previous_photo(self):
        first = service.get_representative_image("P044")
        self.data = picture("blue")
        self.read.return_value = self.data
        self.spec.return_value = {
            "size": len(self.data),
            "web_sha256": hashlib.sha256(self.data).hexdigest(),
        }
        self.assertNotEqual(
            service.get_representative_image("P044"), first,
        )
        self.assertEqual(self.read.call_count, 2)

    def test_expired_cache_is_fetched_again_and_cache_remains_bounded(self):
        for index in range(service.CACHE_ENTRIES + 2):
            self.image["cdn_url"] = (
                "https://dexample.cloudfront.net/"
                f"photos_web/P044/P044_{index + 1:04d}.webp"
            )
            self.image["photo_id"] = f"P044_{index + 1:04d}"
            self.assertIsNotNone(service.get_representative_image("P044"))

        self.assertEqual(len(service._cache), service.CACHE_ENTRIES)
        for key, (_, content) in list(service._cache.items()):
            service._cache[key] = (-1, content)

        calls = self.read.call_count
        self.assertIsNotNone(service.get_representative_image("P044"))
        self.assertEqual(self.read.call_count, calls + 1)

    def test_invalid_time_limits_skip_network(self):
        for value in (0, -1, float("nan"), float("inf"), True, "3"):
            with self.subTest(value=value):
                self.assertIsNone(
                    service.get_representative_image("P044", timeout=value),
                )
        self.lookup.assert_not_called()

    def test_local_paths_cannot_escape_the_original_image_directory(self):
        self.lookup.return_value = {
            "images": [{
                "caption": "입구",
                "path": Path.cwd() / "private.jpg",
            }],
        }
        self.assertIsNone(service.get_representative_image("P044"))
        self.read.assert_not_called()


class RepresentativeTransportTests(unittest.TestCase):
    def test_only_https_and_no_redirect_handler_are_installed(self):
        stream = BytesIO(b"abc")
        stream.status = 200
        opener = Mock()
        opener.open.return_value = stream

        with patch.object(
            service, "build_opener", return_value=opener,
        ) as build:
            self.assertEqual(
                service._read_cdn(
                    "https://dexample.cloudfront.net/a", 3, 2,
                ),
                b"abc",
            )

        self.assertTrue(stream.closed)
        self.assertIsInstance(build.call_args.args[1], service._NoRedirect)
        self.assertIsNone(
            build.call_args.args[1].redirect_request(
                None, None, 302, "", {}, "http://localhost/",
            ),
        )
        context = build.call_args.args[0]._context
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, service.ssl.CERT_REQUIRED)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2)

    def test_http_error_closes_the_response_body(self):
        stream = BytesIO(b"error")
        error = HTTPError(
            "https://dexample.cloudfront.net/a",
            403,
            "Forbidden",
            {},
            stream,
        )
        opener = Mock()
        opener.open.side_effect = error

        with patch.object(service, "build_opener", return_value=opener):
            with self.assertRaises(HTTPError):
                service._read_cdn(
                    "https://dexample.cloudfront.net/a", 3, 2,
                )

        self.assertTrue(stream.closed)

    def test_extra_bytes_and_http_error_are_closed_and_not_accepted(self):
        for data, status in ((b"abcd", 200), (b"abc", 503)):
            stream = BytesIO(data)
            stream.status = status
            opener = Mock()
            opener.open.return_value = stream

            with patch.object(
                service, "build_opener", return_value=opener,
            ):
                with self.assertRaises(ValueError):
                    service._read_cdn(
                        "https://dexample.cloudfront.net/a", 3, 2,
                    )

            self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()