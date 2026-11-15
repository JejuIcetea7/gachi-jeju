"""통합 사진 CSV의 장소 연결과 CloudFront 주소를 읽습니다. AWS에 접속하지 않습니다."""

import csv
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit


WEB_CATALOG = Path("data/processed/place_photos_web.csv")
CLOUD_CATALOG = Path("data/processed/place_photos_cloud.csv")
MANIFEST_FIELDS = [
    "photo_id", "place_id", "place_ids", "web_image_path", "web_size_bytes",
    "web_sha256", "s3_key", "cdn_url", "upload_status", "verified_at",
]
CONFIRMED_MAPPINGS = {
    "csv_filename", "section_context", "picture_paragraph", "table_caption", "table_context",
}


class PhotoCatalogError(ValueError):
    pass


def normalize_cdn_base(value):
    if not isinstance(value, str):
        raise PhotoCatalogError("CloudFront 기본 주소 형식이 올바르지 않습니다.")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise PhotoCatalogError("CloudFront 기본 주소 형식이 올바르지 않습니다.") from error
    if (parsed.scheme != "https" or not re.fullmatch(r"d[a-z0-9]+\.cloudfront\.net", parsed.netloc)
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise PhotoCatalogError("CloudFront 기본 주소 형식이 올바르지 않습니다.")
    return f"https://{parsed.netloc}"


def photo_key(photo_id):
    if not isinstance(photo_id, str) or not re.fullmatch(r"P[0-9]{3}_[0-9]{4}", photo_id):
        raise PhotoCatalogError("사진 ID 형식이 올바르지 않습니다.")
    return f"photos_web/{photo_id[:4]}/{photo_id}.webp"


def validate_cdn_photo_url(value, photo_id):
    if not isinstance(value, str):
        raise PhotoCatalogError("사진 주소가 없습니다.")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise PhotoCatalogError("사진 주소 형식이 올바르지 않습니다.") from error
    base = normalize_cdn_base(f"{parsed.scheme}://{parsed.netloc}")
    if value != f"{base}/{photo_key(photo_id)}":
        raise PhotoCatalogError("사진 ID와 CloudFront 사진 경로가 일치하지 않습니다.")
    return value


def _read_csv(path, fields):
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            columns = reader.fieldnames or []
            if not set(fields).issubset(columns) or len(set(columns)) != len(columns):
                raise PhotoCatalogError("사진 CSV의 필수 컬럼이 없거나 중복되었습니다.")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        raise PhotoCatalogError("사진 CSV를 읽지 못했습니다.") from error
    if not rows:
        raise PhotoCatalogError("사진 CSV가 비어 있습니다.")
    return rows


def _place_ids(value):
    try:
        result = json.loads(value)
    except (TypeError, ValueError) as error:
        raise PhotoCatalogError("관련 장소 목록을 읽지 못했습니다.") from error
    if (not isinstance(result, list) or not result
            or any(not isinstance(item, str) or not re.fullmatch(r"P[0-9]{3}", item) for item in result)
            or len(set(result)) != len(result)):
        raise PhotoCatalogError("관련 장소 목록이 올바르지 않습니다.")
    return result


def read_web_catalog(path):
    rows = _read_csv(path, MANIFEST_FIELDS[:6] + ["sources"])
    photos = {}
    for row in rows:
        photo_id = row["photo_id"]
        key = photo_key(photo_id)
        places = _place_ids(row["place_ids"])
        if photo_id in photos or row["place_id"] != photo_id[:4] or row["place_id"] not in places:
            raise PhotoCatalogError("사진 ID 또는 대표 장소 연결이 중복되거나 올바르지 않습니다.")
        if (row["web_image_path"] or "").replace("\\", "/") != f"data/processed/{key}":
            raise PhotoCatalogError("웹용 사진 경로가 사진 ID와 일치하지 않습니다.")
        if not re.fullmatch(r"[0-9a-f]{64}", row["web_sha256"] or ""):
            raise PhotoCatalogError("웹용 사진 SHA-256 값이 올바르지 않습니다.")
        try:
            size = int(row["web_size_bytes"])
            sources = json.loads(row["sources"])
        except (TypeError, ValueError) as error:
            raise PhotoCatalogError("사진 크기 또는 출처 연결을 읽지 못했습니다.") from error
        if size <= 0 or not isinstance(sources, list) or any(not isinstance(item, dict) for item in sources):
            raise PhotoCatalogError("사진 크기 또는 출처 연결 형식이 올바르지 않습니다.")
        photos[photo_id] = {**row, "places": places, "size": size, "sources": sources, "s3_key": key}
    return photos


def build_cloud_rows(web_catalog, cdn_base):
    """주소 목록을 생성합니다. 원격 파일의 존재나 체크섬을 검증한 결과가 아닙니다."""
    cdn_base = normalize_cdn_base(cdn_base)
    photos = read_web_catalog(web_catalog)
    return [{
        **{field: photo[field] for field in MANIFEST_FIELDS[:6]},
        "web_image_path": f"data/processed/{photo['s3_key']}",
        "s3_key": photo["s3_key"], "cdn_url": f"{cdn_base}/{photo['s3_key']}",
        "upload_status": "url_generated", "verified_at": "",
    } for photo in photos.values()]


def _contexts(photo, place_id):
    result = []
    seen = set()
    for source in photo["sources"]:
        if source.get("place_id") != place_id:
            continue
        name = source.get("spot_name")
        description = source.get("description")
        name = name if isinstance(name, str) else ""
        description = description if isinstance(description, str) else ""
        status = source.get("mapping_status")
        confirmed = isinstance(status, str) and status in CONFIRMED_MAPPINGS
        key = (name, description, confirmed)
        if key in seen:
            continue
        seen.add(key)
        result.append({"spot_name": name, "description": description, "mapping_confirmed": confirmed})
    # 다른 장소의 설명을 가져와 빈 연결을 채우지 않습니다.
    return result or [{"spot_name": "", "description": "", "mapping_confirmed": False}]


@lru_cache(maxsize=1)
def _catalog_index(web_path, web_signature, cloud_path, cloud_signature):
    photos = read_web_catalog(web_path)
    rows = _read_csv(cloud_path, MANIFEST_FIELDS)
    rows_by_id = {}
    for row in rows:
        photo_id = row["photo_id"]
        if photo_id not in photos or photo_id in rows_by_id:
            raise PhotoCatalogError("사진 주소 목록에 알 수 없거나 중복된 사진 ID가 있습니다.")
        photo = photos[photo_id]
        try:
            same_size = int(row["web_size_bytes"]) == photo["size"]
        except (TypeError, ValueError):
            same_size = False
        if (row["place_id"] != photo["place_id"]
                or set(_place_ids(row["place_ids"])) != set(photo["places"])
                or (row["web_image_path"] or "").replace("\\", "/") != f"data/processed/{photo['s3_key']}"
                or row["s3_key"] != photo["s3_key"] or not same_size
                or row["web_sha256"] != photo["web_sha256"]
                or row["upload_status"] not in {"url_generated", "uploaded", "verified_existing"}):
            raise PhotoCatalogError("사진 주소 목록이 웹용 사진 목록과 일치하지 않습니다.")
        validate_cdn_photo_url(row["cdn_url"], photo_id)
        rows_by_id[photo_id] = row
    if set(rows_by_id) != set(photos):
        raise PhotoCatalogError("사진 주소 목록에 누락된 사진이 있습니다.")
    index = {}
    for photo_id, photo in photos.items():
        for place_id in photo["places"]:
            index.setdefault(place_id, []).append({
                "image_id": hashlib.sha256(f"web-photo\0{photo_id}".encode("utf-8")).hexdigest(),
                "photo_id": photo_id, "cdn_url": rows_by_id[photo_id]["cdn_url"],
                "contexts": _contexts(photo, place_id),
            })
    return index


def list_cloud_images(project_root, place_id, place_name):
    root = Path(project_root)
    cloud = root / CLOUD_CATALOG
    if not cloud.is_file():
        return None
    web = root / WEB_CATALOG
    try:
        web_stat, cloud_stat = web.stat(), cloud.stat()
        index = _catalog_index(
            str(web), (web_stat.st_mtime_ns, web_stat.st_size),
            str(cloud), (cloud_stat.st_mtime_ns, cloud_stat.st_size),
        )
    except OSError as error:
        raise PhotoCatalogError("사진 연결 목록을 읽지 못했습니다.") from error
    result = []
    for image in index.get(place_id, []):
        contexts = [dict(context) for context in image["contexts"]]
        caption = next((context["spot_name"] for context in contexts if context["spot_name"].strip()), place_name)
        alt = f"{place_name} · {caption}"
        if all(not context["mapping_confirmed"] for context in contexts):
            alt = f"{place_name} 사진 · 장소 연결 미확인"
        result.append({**image, "contexts": contexts, "caption": caption, "alt": alt})
    return result
