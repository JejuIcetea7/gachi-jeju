"""웹용 사본을 만들거나 --recompress-large로 큰 사진만 품질 85로 교체한다."""

import argparse
import csv
import hashlib
import io
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, __version__, features

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHOTOS_DIRECTORY = PROJECT_ROOT / "data/processed/photos"
WEB_DIRECTORY = PROJECT_ROOT / "data/processed/photos_web"
INPUT_FILE = PROJECT_ROOT / "data/processed/place_photos.csv"
OUTPUT_FILE = PROJECT_ROOT / "data/processed/place_photos_web.csv"
REPORT_DIRECTORY = PROJECT_ROOT / "outputs/photo_web"
MAXIMUM_EDGE = 1600
WEB_COLUMNS = [
    "web_image_path", "web_width", "web_height", "web_size_bytes", "web_sha256",
    "web_resized", "web_size_difference_bytes", "web_lossless_verified",
    "web_source_frames", "web_selected_frame", "web_source_orientation",
    "web_source_mode", "web_mode", "web_icc_preserved", "web_color_conversion",
    "web_compression", "web_quality", "web_decodable_verified",
]


def calculate_hash(data):
    return hashlib.sha256(data).hexdigest()


def prepare_web_image(source_image):
    source_image.seek(0)
    source_mode = source_image.mode
    source_profile = source_image.info.get("icc_profile")
    oriented_image = ImageOps.exif_transpose(source_image)
    conversion = "none"
    profile = source_profile
    if source_mode == "CMYK" and source_profile:
        input_profile = ImageCms.ImageCmsProfile(io.BytesIO(source_profile))
        output_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        image = ImageCms.profileToProfile(oriented_image, input_profile, output_profile, outputMode="RGB")
        profile = output_profile.tobytes()
        conversion = "CMYK_ICC_to_sRGB"
    else:
        mode = "RGB"
        if oriented_image.mode in {"RGBA", "LA"} or "transparency" in oriented_image.info:
            mode = "RGBA"
        image = oriented_image.convert(mode)
        if source_mode != mode:
            conversion = source_mode + "_to_" + mode
    original_size = image.size
    image.thumbnail((MAXIMUM_EDGE, MAXIMUM_EDGE), resample=Image.Resampling.LANCZOS, reducing_gap=None)
    return image, profile, original_size != image.size, conversion


def verify_web_pixels(data, expected_image, expected_profile):
    with Image.open(io.BytesIO(data)) as decoded_image:
        if decoded_image.format != "WEBP":
            raise ValueError("웹용 파일이 WebP 형식이 아닙니다.")
        if decoded_image.size != expected_image.size:
            raise ValueError("웹용 해상도가 기준과 다릅니다.")
        pixels = decoded_image.convert(expected_image.mode)
        if pixels.tobytes() != expected_image.tobytes():
            raise ValueError("무손실 압축 후 픽셀 내용이 일치하지 않습니다.")
        if decoded_image.info.get("icc_profile") != expected_profile:
            raise ValueError("웹용 색상 프로필이 기준과 다릅니다.")
        if decoded_image.getexif().get(274, 1) != 1:
            raise ValueError("방향이 이미 적용된 파일에 추가 회전 정보가 있습니다.")


def process_photo(row, project_root=PROJECT_ROOT, photos_directory=PHOTOS_DIRECTORY, web_directory=WEB_DIRECTORY):
    source_path = (project_root / row["image_path"]).resolve()
    if not source_path.is_relative_to(photos_directory.resolve()):
        raise ValueError("입력 사진 경로가 원본 사진 폴더 밖에 있습니다.")
    relative_path = source_path.relative_to(photos_directory.resolve())
    if relative_path.stem != row["photo_id"]:
        raise ValueError("사진 ID와 입력 파일명이 다릅니다.")
    destination = web_directory / relative_path.with_suffix(".webp")
    if not destination.resolve().is_relative_to(web_directory.resolve()):
        raise ValueError("출력 경로가 웹용 사진 폴더 밖에 있습니다.")
    source_data = source_path.read_bytes()
    if calculate_hash(source_data) != row["sha256"]:
        raise ValueError("원본 사진 내용이 CSV의 내용 검사값과 다릅니다.")
    with Image.open(io.BytesIO(source_data)) as source_image:
        if source_image.size != (int(row["width"]), int(row["height"])):
            raise ValueError("원본 해상도가 CSV와 다릅니다.")
        frame_count = getattr(source_image, "n_frames", 1)
        orientation = source_image.getexif().get(274, 1)
        source_mode = source_image.mode
        source_profile = source_image.info.get("icc_profile")
        web_image, profile, resized, conversion = prepare_web_image(source_image)
        if destination.exists():
            data = destination.read_bytes()
        else:
            buffer = io.BytesIO()
            options = {"format": "WEBP", "lossless": True, "quality": 100, "method": 4, "exact": True}
            if profile:
                options["icc_profile"] = profile
            web_image.save(buffer, **options)
            data = buffer.getvalue()
        verify_web_pixels(data, web_image, profile)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output_file:
                output_file.write(data)
        width, height = web_image.size
    return {
        "web_image_path": str(destination.relative_to(project_root)),
        "web_width": width, "web_height": height,
        "web_size_bytes": len(data), "web_sha256": calculate_hash(data),
        "web_resized": resized, "web_size_difference_bytes": len(data) - len(source_data),
        "web_lossless_verified": True, "web_source_frames": frame_count,
        "web_selected_frame": 0, "web_source_orientation": orientation,
        "web_source_mode": source_mode, "web_mode": web_image.mode,
        "web_icc_preserved": source_profile == profile, "web_color_conversion": conversion,
        "web_compression": "lossless", "web_quality": 100, "web_decodable_verified": True,
    }


def select_large_rows(rows):
    selected = []
    for row in rows:
        if max(int(row["width"]), int(row["height"])) > MAXIMUM_EDGE:
            selected.append(row)
    return selected


def encode_lossy_webp(data):
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        original_size = image.size
        original_profile = image.info.get("icc_profile")
        original_alpha = None
        if image.mode == "RGBA":
            original_alpha = image.getchannel("A").tobytes()
        options = {"format": "WEBP", "lossless": False, "quality": 85, "method": 4}
        if original_profile:
            options["icc_profile"] = original_profile
        buffer = io.BytesIO()
        image.save(buffer, **options)
    encoded = buffer.getvalue()
    with Image.open(io.BytesIO(encoded)) as decoded:
        decoded.load()
        if decoded.format != "WEBP" or decoded.size != original_size:
            raise ValueError("손실 압축 후 형식 또는 해상도가 다릅니다.")
        if decoded.info.get("icc_profile") != original_profile:
            raise ValueError("손실 압축 후 색상 프로필이 다릅니다.")
        if original_alpha is not None and decoded.convert("RGBA").getchannel("A").tobytes() != original_alpha:
            raise ValueError("손실 압축 후 투명도가 다릅니다.")
    return encoded


def stage_lossy_photo(row, staging_directory):
    source = PROJECT_ROOT / row["web_image_path"]
    if not source.resolve().is_relative_to(WEB_DIRECTORY.resolve()):
        raise ValueError("대상 경로가 웹용 폴더 밖에 있습니다.")
    data = source.read_bytes()
    if calculate_hash(data) != row["web_sha256"]:
        raise ValueError("현재 웹용 파일이 CSV의 검사값과 다릅니다.")
    encoded = encode_lossy_webp(data)
    staged = staging_directory / (row["photo_id"] + ".webp")
    with staged.open("xb") as output:
        output.write(encoded)
    return {
        "web_size_bytes": len(encoded), "web_sha256": calculate_hash(encoded),
        "web_size_difference_bytes": len(encoded) - int(row["size_bytes"]),
        "web_lossless_verified": False, "web_compression": "lossy",
        "web_quality": 85, "web_decodable_verified": True,
    }


def recompress_large_photos():
    run_directory = REPORT_DIRECTORY / "recompression-85"
    run_directory.mkdir()
    staging_directory = run_directory / "staged"
    staging_directory.mkdir()
    original_csv_data = INPUT_FILE.read_bytes()
    web_csv_data = OUTPUT_FILE.read_bytes()
    with (run_directory / "previous_place_photos_web.csv").open("xb") as output:
        output.write(web_csv_data)
    with (run_directory / "previous_verification.json").open("xb") as output:
        output.write((REPORT_DIRECTORY / "verification.json").read_bytes())
    with io.StringIO(web_csv_data.decode("utf-8-sig"), newline="") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        columns = reader.fieldnames
    selected = select_large_rows(rows)
    if len(selected) != 1304 or len(rows) != 3834:
        raise ValueError("합의한 사진 목록과 대상 개수가 다릅니다.")
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for row in selected:
            futures[executor.submit(stage_lossy_photo, row, staging_directory)] = row["photo_id"]
        for future in as_completed(futures):
            photo_id = futures[future]
            results[photo_id] = future.result()
            if len(results) % 100 == 0 or len(results) == len(selected):
                print(f"품질 85 시험 압축: {len(results)}/{len(selected)}", flush=True)
    # 교체 전에 원본·비대상 파일까지 모두 확인한다.
    for row in rows:
        if calculate_hash((PROJECT_ROOT / row["image_path"]).read_bytes()) != row["sha256"]:
            raise ValueError("원본 사진이 변경됐습니다.")
        if calculate_hash((PROJECT_ROOT / row["web_image_path"]).read_bytes()) != row["web_sha256"]:
            raise ValueError("시험 압축 중 웹용 사진이 변경됐습니다.")
    if INPUT_FILE.read_bytes() != original_csv_data or OUTPUT_FILE.read_bytes() != web_csv_data:
        raise ValueError("시험 압축 중 CSV가 변경됐습니다.")
    with (run_directory / "replacement_manifest.json").open("x", encoding="utf-8") as output:
        json.dump(results, output, ensure_ascii=False, indent=2)
    before_bytes = sum(int(row["web_size_bytes"]) for row in rows)
    selected_before_bytes = sum(int(row["web_size_bytes"]) for row in selected)
    for row in rows:
        row["web_compression"] = "lossless"
        row["web_quality"] = "100"
        row["web_decodable_verified"] = "True"
        result = results.get(row["photo_id"])
        if result is not None:
            staged = staging_directory / (row["photo_id"] + ".webp")
            if calculate_hash(staged.read_bytes()) != result["web_sha256"]:
                raise ValueError("교체 준비 파일의 내용이 다릅니다.")
            staged.replace(PROJECT_ROOT / row["web_image_path"])
            row.update(result)
    for row in rows:
        path = PROJECT_ROOT / row["web_image_path"]
        if calculate_hash(path.read_bytes()) != row["web_sha256"]:
            raise ValueError("교체 후 웹용 파일 내용이 다릅니다.")
        with Image.open(path) as image:
            image.load()
            if image.format != "WEBP" or image.size != (int(row["web_width"]), int(row["web_height"])):
                raise ValueError("교체 후 해상도 또는 형식이 다릅니다.")
    if INPUT_FILE.read_bytes() != original_csv_data:
        raise ValueError("원본 CSV가 변경됐습니다.")
    for column in ["web_compression", "web_quality", "web_decodable_verified"]:
        if column not in columns:
            columns.append(column)
    temporary_csv = run_directory / "next_place_photos_web.csv"
    with temporary_csv.open("x", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary_csv.replace(OUTPUT_FILE)
    after_bytes = sum(int(row["web_size_bytes"]) for row in rows)
    selected_after_bytes = sum(results[row["photo_id"]]["web_size_bytes"] for row in selected)
    report = {
        "photo_count": len(rows), "recompressed": len(selected), "unchanged": len(rows) - len(selected),
        "original_bytes": sum(int(row["size_bytes"]) for row in rows),
        "previous_web_bytes": before_bytes, "web_bytes": after_bytes,
        "reduction_from_previous_percent": round((1 - after_bytes / before_bytes) * 100, 2),
        "target_previous_bytes": selected_before_bytes, "target_web_bytes": selected_after_bytes,
        "target_reduction_percent": round((1 - selected_after_bytes / selected_before_bytes) * 100, 2),
        "lossless_verified": len(rows) - len(selected), "lossy_decodable_verified": len(selected),
        "originals_verified_unchanged": len(rows), "source_csv_sha256": calculate_hash(original_csv_data),
        "policy": {"maximum_edge": MAXIMUM_EDGE, "lossy_when_original_edge_above": MAXIMUM_EDGE,
                   "large_photo_quality": 85, "other_photos": "unchanged_lossless", "method": 4},
    }
    for path in [run_directory / "verification.json", REPORT_DIRECTORY / "verification.json"]:
        with path.open("w", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def read_input_rows():
    data = INPUT_FILE.read_bytes()
    with io.StringIO(data.decode("utf-8-sig"), newline="") as source_file:
        reader = csv.DictReader(source_file)
        rows = list(reader)
        columns = reader.fieldnames
    identifiers = set()
    for row in rows:
        if row["photo_id"] in identifiers:
            raise ValueError("입력 목록의 사진 ID가 중복됩니다.")
        identifiers.add(row["photo_id"])
    if set(columns).intersection(WEB_COLUMNS):
        raise ValueError("입력 목록에 이미 웹용 컬럼이 있습니다.")
    return rows, columns, calculate_hash(data)


def build_report(rows, results, input_hash, elapsed_seconds):
    if calculate_hash(INPUT_FILE.read_bytes()) != input_hash:
        raise ValueError("가공 중 원본 CSV가 변경됐습니다.")
    disk_files = set()
    for path in WEB_DIRECTORY.rglob("*"):
        if path.is_file():
            disk_files.add(path)
    expected_files = set()
    original_bytes = 0
    web_bytes = 0
    increased = []
    resized = 0
    frame_counts = Counter()
    orientation_counts = Counter()
    for row in rows:
        result = results[row["photo_id"]]
        source_data = (PROJECT_ROOT / row["image_path"]).read_bytes()
        if calculate_hash(source_data) != row["sha256"]:
            raise ValueError("가공 후 원본 사진 내용이 달라졌습니다.")
        destination = PROJECT_ROOT / result["web_image_path"]
        if calculate_hash(destination.read_bytes()) != result["web_sha256"]:
            raise ValueError("저장된 웹용 파일의 내용이 달라졌습니다.")
        expected_files.add(destination)
        original_bytes += len(source_data)
        web_bytes += result["web_size_bytes"]
        resized += result["web_resized"]
        frame_counts[str(result["web_source_frames"])] += 1
        orientation_counts[str(result["web_source_orientation"])] += 1
        if result["web_size_difference_bytes"] > 0:
            increased.append({"photo_id": row["photo_id"], "original_bytes": len(source_data), "web_bytes": result["web_size_bytes"]})
    if disk_files != expected_files:
        raise ValueError("웹용 폴더 파일 목록과 CSV가 일치하지 않습니다.")
    return {
        "photo_count": len(rows), "place_folders": len({path.parent for path in expected_files}),
        "resized": resized, "size_unchanged": len(rows) - resized,
        "original_bytes": original_bytes, "web_bytes": web_bytes,
        "saved_bytes": original_bytes - web_bytes,
        "reduction_percent": round((original_bytes - web_bytes) / original_bytes * 100, 2),
        "increased_files": increased, "source_frame_counts": dict(frame_counts),
        "source_orientation_counts": dict(orientation_counts),
        "lossless_verified": len(rows), "originals_verified_unchanged": len(rows),
        "source_csv_sha256": input_hash, "elapsed_seconds": round(elapsed_seconds, 2),
        "policy": {"maximum_edge": MAXIMUM_EDGE, "lossless": True, "quality": 100, "method": 4, "exact": True, "pillow": __version__},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true", help="중단 시 기존 WebP를 검증해 재사용합니다.")
    parser.add_argument("--recompress-large", action="store_true", help="원본 긴 변이 1600px를 넘는 1304장의 웹용 파일만 품질 85로 교체합니다.")
    arguments = parser.parse_args()
    if not features.check("webp"):
        raise RuntimeError("Pillow가 WebP를 지원하지 않습니다.")
    if arguments.recompress_large:
        recompress_large_photos()
        return
    if OUTPUT_FILE.exists():
        raise FileExistsError("기존 웹용 CSV는 덮어쓰지 않습니다.")
    if WEB_DIRECTORY.exists() and not arguments.resume:
        raise FileExistsError("웹용 폴더가 이미 있습니다. 중단 후 재개라면 --resume을 지정해 주세요.")
    rows, columns, input_hash = read_input_rows()
    REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()
    results = {}
    failures = []
    progress_path = REPORT_DIRECTORY / "progress.jsonl"
    with progress_path.open("a", encoding="utf-8") as progress_file:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for row in rows:
                futures[executor.submit(process_photo, row)] = row["photo_id"]
            for future in as_completed(futures):
                photo_id = futures[future]
                try:
                    result = future.result()
                    results[photo_id] = result
                    record = {"photo_id": photo_id, **result}
                except Exception as error:
                    record = {"photo_id": photo_id, "error": str(error)}
                    failures.append(record)
                progress_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                progress_file.flush()
                completed = len(results) + len(failures)
                if completed % 100 == 0 or completed == len(rows):
                    print(f"WebP: {completed}/{len(rows)} / 오류 {len(failures)}", flush=True)
    if failures:
        with (REPORT_DIRECTORY / "failures.json").open("w", encoding="utf-8") as output_file:
            json.dump(failures, output_file, ensure_ascii=False, indent=2)
        raise RuntimeError(f"변환 실패 {len(failures)}개. 원인 확인 후 --resume으로 재개하세요.")
    report = build_report(rows, results, input_hash, time.monotonic() - started_at)
    temporary_path = OUTPUT_FILE.with_suffix(".csv.tmp")
    with temporary_path.open("x", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=columns + WEB_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, **results[row["photo_id"]]})
    temporary_path.replace(OUTPUT_FILE)
    with (REPORT_DIRECTORY / "verification.json").open("w", encoding="utf-8") as output_file:
        json.dump(report, output_file, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in report.items() if key != "increased_files"}, ensure_ascii=False, indent=2))
    print(f"용량 증가 파일: {len(report['increased_files'])}개")


if __name__ == "__main__":
    main()
