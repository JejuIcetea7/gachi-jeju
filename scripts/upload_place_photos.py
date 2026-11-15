"""통합 WebP를 검증하고 --upload를 지정했을 때만 S3에 업로드합니다."""

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = Path("data/processed/place_photos_web.csv")
PHOTO_DIRECTORY = Path("data/processed/photos_web")
REPORT_DIRECTORY = Path("outputs/photo_web/cloud_upload")
MANIFEST_PATH = Path("data/processed/place_photos_cloud.csv")
PLACE_PATTERN = re.compile(r"P[0-9]{3}")
PHOTO_PATTERN = re.compile(r"P[0-9]{3}_[0-9]{4}")
CACHE_CONTROL = "public, max-age=86400"
MAX_PHOTO_BYTES = 64 * 1024 * 1024
MANIFEST_FIELDS = [
    "photo_id", "place_id", "place_ids", "web_image_path", "web_size_bytes",
    "web_sha256", "s3_key", "cdn_url", "upload_status", "verified_at",
]


@dataclass(frozen=True)
class Photo:
    photo_id: str
    place_id: str
    place_ids: tuple[str, ...]
    web_image_path: str
    size: int
    sha256: str

    @property
    def key(self):
        return f"photos_web/{self.place_id}/{self.photo_id}.webp"

    @property
    def checksum(self):
        return base64.b64encode(bytes.fromhex(self.sha256)).decode("ascii")


def read_catalog(path):
    """경로는 CSV에 나온 값을 그대로 신뢰하지 않고 ID로 다시 구성합니다."""
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"photo_id", "place_id", "place_ids", "web_image_path", "web_size_bytes", "web_sha256"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("웹용 사진 CSV의 필수 컬럼이 없습니다.")
        photos = []
        seen = set()
        for line, row in enumerate(reader, 2):
            photo_id, place_id = row["photo_id"], row["place_id"]
            try:
                place_ids = json.loads(row["place_ids"])
                size = int(row["web_size_bytes"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"CSV {line}행의 장소 목록 또는 파일 크기가 올바르지 않습니다.") from error
            if (not PHOTO_PATTERN.fullmatch(photo_id or "")
                    or not PLACE_PATTERN.fullmatch(place_id or "")
                    or not photo_id.startswith(place_id + "_")):
                raise ValueError(f"CSV {line}행의 사진 ID와 대표 장소 ID를 확인해 주세요.")
            if photo_id in seen:
                raise ValueError(f"사진 ID가 중복되었습니다: {photo_id}")
            if (not isinstance(place_ids, list) or not place_ids
                    or any(not isinstance(item, str) or not PLACE_PATTERN.fullmatch(item) for item in place_ids)
                    or len(set(place_ids)) != len(place_ids) or place_id not in place_ids):
                raise ValueError(f"관련 장소 ID 목록이 올바르지 않습니다: {photo_id}")
            expected_path = f"data/processed/photos_web/{place_id}/{photo_id}.webp"
            if (row["web_image_path"] or "").replace("\\", "/") != expected_path:
                raise ValueError(f"사진 ID와 웹용 파일 경로가 일치하지 않습니다: {photo_id}")
            digest = (row["web_sha256"] or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or not 0 < size <= MAX_PHOTO_BYTES:
                raise ValueError(f"파일 크기 또는 SHA-256 값이 올바르지 않습니다: {photo_id}")
            photos.append(Photo(photo_id, place_id, tuple(place_ids), expected_path, size, digest))
            seen.add(photo_id)
    if not photos:
        raise ValueError("웹용 사진 CSV가 비어 있습니다.")
    return photos


def check_content(photo, data):
    if len(data) != photo.size:
        raise ValueError(f"CSV와 파일 크기가 다릅니다: {photo.photo_id}")
    if hashlib.sha256(data).hexdigest() != photo.sha256:
        raise ValueError(f"CSV와 SHA-256 값이 다릅니다: {photo.photo_id}")


def photo_path(root, photo):
    root = Path(root).resolve()
    directory = (root / PHOTO_DIRECTORY).resolve()
    path = (root / photo.web_image_path).resolve()
    if (not directory.is_relative_to(root) or not path.is_relative_to(directory)
            or not path.is_file()):
        raise ValueError(f"사진이 없거나 사진 폴더 밖을 가리킵니다: {photo.photo_id}")
    return path


def validate_files(root, photos, progress=None):
    expected = {photo.web_image_path for photo in photos}
    directory = Path(root) / PHOTO_DIRECTORY
    if not directory.is_dir():
        raise ValueError("data/processed/photos_web 폴더가 없습니다. ZIP 안의 photos_web 폴더를 data/processed 아래에 풀어 주세요.")
    actual = {path.relative_to(root).as_posix() for path in directory.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError(f"CSV와 사진 목록이 다릅니다. 누락 {len(expected - actual)}개, 추가 {len(actual - expected)}개")
    for index, photo in enumerate(photos, 1):
        path = photo_path(root, photo)
        if path.stat().st_size != photo.size:
            raise ValueError(f"CSV와 파일 크기가 다릅니다: {photo.photo_id}")
        check_content(photo, path.read_bytes())
        if progress:
            progress(index, len(photos))


def validate_archive(archive, photos, progress=None):
    """ZIP 내용만 읽고 검사하며 파일을 추출하지 않습니다."""
    expected = {photo.key: photo for photo in photos}
    with zipfile.ZipFile(archive) as package:
        files = [item for item in package.infolist() if not item.is_dir()]
        names = [item.filename for item in files]
        if len(set(names)) != len(names) or set(names) != set(expected):
            raise ValueError("ZIP의 사진 목록이 CSV와 다르거나 같은 경로가 중복되었습니다.")
        for index, item in enumerate(files, 1):
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("ZIP에 심볼릭 링크가 포함되어 있습니다.")
            photo = expected[item.filename]
            if item.file_size != photo.size:
                raise ValueError(f"ZIP과 CSV의 파일 크기가 다릅니다: {photo.photo_id}")
            check_content(photo, package.read(item))
            if progress:
                progress(index, len(files))


def cloudfront_base_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not re.fullmatch(r"d[a-z0-9]+\.cloudfront\.net", parsed.netloc)
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("CDN 주소는 https://d....cloudfront.net 형식으로 입력해 주세요.")
    return f"https://{parsed.netloc}"


def check_target(bucket, cdn_url, owner, workers):
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
        raise ValueError("버킷 이름은 소문자, 숫자, 하이픈을 사용한 3~63자로 입력해 주세요.")
    if not isinstance(owner, str) or not re.fullmatch(r"[0-9]{12}", owner):
        raise ValueError("버킷 소유 AWS 계정 ID 12자리를 입력해 주세요.")
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("동시 업로드 수는 1~4로 지정해 주세요.")
    return cloudfront_base_url(cdn_url)


def remote_photo(client, bucket, owner, photo):
    try:
        return client.head_object(
            Bucket=bucket, Key=photo.key, ExpectedBucketOwner=owner, ChecksumMode="ENABLED",
        )
    except Exception as error:
        response = getattr(error, "response", {})
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = response.get("Error", {}).get("Code")
        if status == 404 or (status is None and code in {"404", "NoSuchKey", "NotFound"}):
            return None
        raise


def matches_remote(info, photo):
    return (isinstance(info, dict)
            and info.get("ContentLength") == photo.size
            and info.get("ContentType") == "image/webp"
            and info.get("ChecksumSHA256") == photo.checksum
            and info.get("ChecksumType", "FULL_OBJECT") == "FULL_OBJECT")


def upload_photo(client, root, bucket, owner, photo):
    existing = remote_photo(client, bucket, owner, photo)
    if existing is not None:
        if not matches_remote(existing, photo):
            raise ValueError(f"같은 S3 경로에 다른 내용 또는 확인할 수 없는 파일이 있습니다: {photo.photo_id}")
        return "verified_existing"
    data = photo_path(root, photo).read_bytes()
    check_content(photo, data)
    try:
        client.put_object(
            Bucket=bucket, Key=photo.key, ExpectedBucketOwner=owner,
            Body=data, ContentLength=photo.size, ContentType="image/webp",
            CacheControl=CACHE_CONTROL, ServerSideEncryption="AES256",
            ChecksumAlgorithm="SHA256", ChecksumSHA256=photo.checksum,
            Metadata={"photo-id": photo.photo_id, "sha256": photo.sha256}, IfNoneMatch="*",
        )
    except Exception as error:
        response = getattr(error, "response", {})
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
        if matches_remote(remote_photo(client, bucket, owner, photo), photo):
            return "verified_existing"
        raise ValueError(f"다른 업로드가 같은 S3 경로를 먼저 사용했습니다: {photo.photo_id}") from error
    if not matches_remote(remote_photo(client, bucket, owner, photo), photo):
        raise ValueError(f"업로드 후 S3 파일의 크기·SHA-256·형식을 확인하지 못했습니다: {photo.photo_id}")
    return "uploaded"


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_manifest(path, photos, results, cdn_url):
    if set(results) != {photo.photo_id for photo in photos} or any(
        result.get("status") not in {"uploaded", "verified_existing"} for result in results.values()
    ):
        raise ValueError("전체 사진의 업로드 검증이 끝나지 않아 연결 CSV를 갱신하지 않습니다.")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
    writer.writeheader()
    for photo in photos:
        result = results[photo.photo_id]
        writer.writerow({
            "photo_id": photo.photo_id, "place_id": photo.place_id,
            "place_ids": json.dumps(photo.place_ids, ensure_ascii=False),
            "web_image_path": photo.web_image_path, "web_size_bytes": photo.size,
            "web_sha256": photo.sha256, "s3_key": photo.key,
            "cdn_url": f"{cdn_url}/{photo.key}", "upload_status": result["status"],
            "verified_at": result["verified_at"],
        })
    atomic_text(path, buffer.getvalue())


def upload_catalog(client, root, photos, *, bucket, owner, cdn_url, manifest, journal, workers=4, progress=None):
    cdn_url = check_target(bucket, cdn_url, owner, workers)
    # 만료된 로그인이나 잘못된 버킷으로 모든 사진을 반복 요청하지 않습니다.
    client.head_bucket(Bucket=bucket, ExpectedBucketOwner=owner)
    results = {}
    journal = Path(journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    # 원본 예외 메시지에는 인증 정보나 요청 주소가 포함될 수 있어 오류 유형만 기록합니다.
    with journal.open("a", encoding="utf-8", newline="") as log:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(upload_photo, client, root, bucket, owner, photo): photo for photo in photos}
            try:
                for future in as_completed(futures):
                    photo = futures[future]
                    try:
                        result = {"photo_id": photo.photo_id, "status": future.result()}
                    except Exception as error:
                        result = {"photo_id": photo.photo_id, "status": "failed", "error_type": type(error).__name__}
                        code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
                        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code):
                            result["error_code"] = code
                    result["verified_at"] = datetime.now(timezone.utc).isoformat()
                    results[photo.photo_id] = result
                    log.write(json.dumps(result, ensure_ascii=False) + "\n")
                    log.flush()
                    if progress:
                        progress(len(results), len(photos))
            except BaseException:
                for pending in futures:
                    pending.cancel()
                raise
    if any(result["status"] == "failed" for result in results.values()):
        return results
    publish_manifest(manifest, photos, results, cdn_url)
    return results


def create_s3_client(profile, region):
    # 검증 모드에서는 SDK 설치나 AWS 자격 증명이 필요하지 않습니다.
    import boto3
    from botocore.config import Config

    session = boto3.Session(profile_name=profile, region_name=region)
    return session.client("s3", config=Config(
        connect_timeout=5, read_timeout=30, retries={"mode": "standard", "total_max_attempts": 3},
    ))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--archive", type=Path, help="ZIP을 추출하지 않고 내용과 CSV를 검증합니다.")
    parser.add_argument("--upload", action="store_true", help="검증 후 실제 S3 업로드를 실행합니다.")
    parser.add_argument("--bucket")
    parser.add_argument("--cdn-base-url")
    parser.add_argument("--expected-owner")
    parser.add_argument("--profile")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if args.upload:
        if args.archive or not args.profile:
            parser.error("업로드에는 추출된 사진과 --profile이 필요하며 --archive를 함께 사용할 수 없습니다.")
        try:
            check_target(args.bucket, args.cdn_base_url, args.expected_owner, args.workers)
        except (TypeError, ValueError) as error:
            parser.error(str(error))
    root = args.root.resolve()

    def progress(current, total):
        if current == 1 or current % 250 == 0 or current == total:
            print(f"처리: {current:,}/{total:,}", flush=True)

    try:
        photos = read_catalog(root / CATALOG_PATH)
        print("사진 크기와 SHA-256을 검증합니다.", flush=True)
        if args.archive:
            validate_archive(args.archive, photos, progress)
        else:
            validate_files(root, photos, progress)
        summary = {
            "status": "validated", "photos": len(photos),
            "place_folders": len({photo.place_id for photo in photos}),
            "related_places": len({place_id for photo in photos for place_id in photo.place_ids}),
            "total_bytes": sum(photo.size for photo in photos), "sha256_checked": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_text(root / REPORT_DIRECTORY / "validation.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        if not args.upload:
            print("검증 모드입니다. AWS 접속과 업로드는 실행하지 않았습니다.")
            return 0
        client = create_s3_client(args.profile, args.region)
        results = upload_catalog(
            client, root, photos, bucket=args.bucket, owner=args.expected_owner,
            cdn_url=args.cdn_base_url, manifest=root / MANIFEST_PATH,
            journal=root / REPORT_DIRECTORY / "upload-results.jsonl", workers=args.workers, progress=progress,
        )
        failed = [photo_id for photo_id, result in results.items() if result["status"] == "failed"]
        print(f"업로드 검증 통과: {len(results) - len(failed):,}개, 실패: {len(failed):,}개")
        if failed:
            print("실패한 사진 ID: " + ", ".join(failed[:20]))
            print("연결 CSV는 갱신하지 않았습니다. outputs/photo_web/cloud_upload/upload-results.jsonl을 확인해 주세요.")
            return 1
        print("연결 CSV: data/processed/place_photos_cloud.csv")
        return 0
    except KeyboardInterrupt:
        print("작업을 중단했습니다. 다시 실행하면 S3에서 내용이 같은 사진을 확인한 뒤 건너뜁니다.")
        return 130
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"검증 실패: {error}")
        return 1
    except Exception as error:
        print(f"작업을 중단했습니다: {type(error).__name__}. AWS 로그인·권한·네트워크 상태를 확인해 주세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
