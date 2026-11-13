import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageOps

from scripts.build_web_photos import (
    encode_lossy_webp, prepare_web_image, process_photo, select_large_rows, verify_web_pixels,
)


def encode_lossless(image, profile=None):
    buffer = io.BytesIO()
    options = {"lossless": True, "quality": 100, "method": 4, "exact": True}
    if profile:
        options["icc_profile"] = profile
    image.save(buffer, format="WEBP", **options)
    return buffer.getvalue()


class WebPhotoTests(unittest.TestCase):
    def test_lossy_targets_use_original_resolution(self):
        rows = [
            {"photo_id": "large", "width": "2592", "height": "1944", "web_width": "1600", "web_height": "1200"},
            {"photo_id": "small", "width": "261", "height": "196", "web_width": "261", "web_height": "196"},
            {"photo_id": "boundary", "width": "1600", "height": "1200", "web_width": "1600", "web_height": "1200"},
        ]
        selected = select_large_rows(rows)
        self.assertEqual([row["photo_id"] for row in selected], ["large"])

    def test_lossy_recompression_keeps_dimensions_profile_and_alpha(self):
        original = Image.new("RGBA", (31, 17), (25, 77, 165, 128))
        original.putpixel((0, 0), (201, 28, 58, 0))
        source_data = encode_lossless(original, b"sample-profile")
        compressed = encode_lossy_webp(source_data)
        with Image.open(io.BytesIO(compressed)) as decoded:
            decoded.load()
            self.assertEqual(decoded.format, "WEBP")
            self.assertEqual(decoded.size, original.size)
            self.assertEqual(decoded.info.get("icc_profile"), b"sample-profile")
            self.assertEqual(decoded.convert("RGBA").getchannel("A").tobytes(), original.getchannel("A").tobytes())
        with Image.open(io.BytesIO(source_data)) as decoded_original:
            self.assertEqual(decoded_original.tobytes(), original.tobytes())

    def test_small_photo_keeps_size_and_pixels(self):
        original = Image.new("RGB", (261, 196), (23, 142, 69))
        processed, profile, resized, conversion = prepare_web_image(original)
        self.assertFalse(resized)
        self.assertEqual(processed.size, original.size)
        self.assertEqual(processed.tobytes(), original.tobytes())
        verify_web_pixels(encode_lossless(processed), processed, profile)

    def test_landscape_photo_preserves_ratio(self):
        original = Image.new("RGB", (2592, 1944))
        processed, profile, resized, conversion = prepare_web_image(original)
        self.assertTrue(resized)
        self.assertEqual(processed.size, (1600, 1200))

    def test_portrait_photo_preserves_ratio(self):
        original = Image.new("RGB", (1200, 2400))
        processed, profile, resized, conversion = prepare_web_image(original)
        self.assertTrue(resized)
        self.assertEqual(processed.size, (800, 1600))

    def test_orientation_is_applied_to_pixels(self):
        original = Image.new("RGB", (12, 8))
        original.putpixel((0, 0), (255, 0, 0))
        original.getexif()[274] = 6
        expected = ImageOps.exif_transpose(original)
        processed, profile, resized, conversion = prepare_web_image(original)
        self.assertEqual(processed.size, (8, 12))
        self.assertEqual(processed.tobytes(), expected.tobytes())

    def test_transparent_pixel_rgb_is_not_discarded(self):
        original = Image.new("RGBA", (3, 2), (22, 144, 233, 0))
        original.putpixel((1, 0), (177, 22, 97, 128))
        processed, profile, resized, conversion = prepare_web_image(original)
        data = encode_lossless(processed)
        verify_web_pixels(data, processed, profile)
        with Image.open(io.BytesIO(data)) as decoded:
            self.assertEqual(decoded.convert("RGBA").tobytes(), original.tobytes())

    def test_pixel_mismatch_fails_verification(self):
        expected = Image.new("RGB", (2, 2), (255, 0, 0))
        other = Image.new("RGB", (2, 2), (0, 255, 0))
        with self.assertRaisesRegex(ValueError, "픽셀"):
            verify_web_pixels(encode_lossless(other), expected, None)

    def test_cmyk_is_converted_to_web_rgb(self):
        original = Image.new("CMYK", (3, 2), (12, 34, 56, 78))
        processed, profile, resized, conversion = prepare_web_image(original)
        self.assertEqual(processed.mode, "RGB")
        self.assertEqual(conversion, "CMYK_to_RGB")
        verify_web_pixels(encode_lossless(processed), processed, profile)

    def test_profile_is_preserved(self):
        original = Image.new("RGB", (3, 2))
        original.info["icc_profile"] = b"sample-profile"
        processed, profile, resized, conversion = prepare_web_image(original)
        self.assertEqual(profile, b"sample-profile")
        verify_web_pixels(encode_lossless(processed, profile), processed, profile)

    def test_worker_preserves_original_and_reuses_verified_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            destination = root / "photos_web"
            (photos / "P001").mkdir(parents=True)
            source_path = photos / "P001/P001_0001.png"
            Image.new("RGB", (13, 7), (43, 73, 98)).save(source_path)
            original_data = source_path.read_bytes()
            row = {
                "photo_id": "P001_0001", "image_path": "photos/P001/P001_0001.png",
                "sha256": hashlib.sha256(original_data).hexdigest(), "width": "13", "height": "7",
            }
            first = process_photo(row, root, photos, destination)
            second = process_photo(row, root, photos, destination)
            self.assertEqual(first, second)
            self.assertEqual(source_path.read_bytes(), original_data)
            self.assertTrue(first["web_lossless_verified"])
            self.assertEqual(first["web_image_path"], "photos_web/P001/P001_0001.webp")
            row["sha256"] = "invalid"
            with self.assertRaisesRegex(ValueError, "원본 사진 내용"):
                process_photo(row, root, photos, destination)


if __name__ == "__main__":
    unittest.main()
