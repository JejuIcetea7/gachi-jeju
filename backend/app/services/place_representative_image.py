"""웹 일정표와 PDF에서 함께 사용하는 장소 대표 사진을 준비합니다."""

from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
import hashlib
from http.client import HTTPException
from io import BytesIO
from math import isfinite
from pathlib import Path
import re
import sqlite3
import ssl
from threading import Lock
from time import monotonic
from urllib.error import HTTPError
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, Request, build_opener,
)

from PIL import Image, ImageOps
import truststore

from .cloud_photos import (
    PhotoCatalogError, WEB_CATALOG, read_web_catalog, validate_cdn_photo_url,
)
from .place_images import IMAGE_ROOT, PROJECT_ROOT, list_place_images

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 96 * 1024
CACHE_SECONDS = 600
CACHE_ENTRIES = 64
_cache = OrderedDict()
_cache_lock = Lock()


def select_representative_image(images):
    """확인된 지점 이름의 전경·입구를 우선하고 동순위는 기존 순서를 유지합니다."""
    def score(image):
        contexts = image.get("contexts")
        names = (
            [context.get("spot_name", "") for context in contexts
             if context.get("mapping_confirmed") is True]
            if isinstance(contexts, list) else [image.get("caption", "")]
        )
        ranks = []
        for name in names:
            if not isinstance(name, str) or not name.strip():
                continue
            if re.search("주차|화장실|계단|경사로|안내판|안내도|로고", name):
                ranks.append(3)
            elif re.search("전경|풍경|경관|외관|전망|전체 모습", name):
                ranks.append(0)
            elif re.search("입구|정문|출입구|표지석", name):
                ranks.append(1)
            else:
                ranks.append(2)
        return min(ranks, default=4)

    candidates = [image for image in images if isinstance(image, dict)]
    return deepcopy(min(candidates, key=score)) if candidates else None


@lru_cache(maxsize=1)
def _web_specs(path, signature):
    return read_web_catalog(path)


def _cloud_spec(photo_id, place_id):
    path = PROJECT_ROOT / WEB_CATALOG
    stat = path.stat()
    photo = _web_specs(str(path), (stat.st_mtime_ns, stat.st_size)).get(photo_id)
    if photo is None or place_id not in photo["places"]:
        raise PhotoCatalogError("대표 사진의 장소 연결을 확인하지 못했습니다.")
    return photo


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_cdn(url, expected_size, timeout):
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
    deadline = monotonic() + timeout
    request = Request(
        url,
        headers={"Accept": "image/*", "User-Agent": "GachiJeju/1.0"},
    )
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        error.close()
        raise

    with response:
        if response.status != 200:
            raise ValueError("사진 응답을 확인하지 못했습니다.")
        chunks = []
        length = 0
        while True:
            if monotonic() >= deadline:
                raise TimeoutError("사진 응답 대기 시간이 지났습니다.")
            chunk = response.read1(min(65536, expected_size + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > expected_size:
                raise ValueError("사진 크기가 등록 정보와 다릅니다.")
        return b"".join(chunks)


def _thumbnail(data):
    with Image.open(BytesIO(data)) as source:
        if source.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
            raise ValueError("지원하지 않는 사진 형식입니다.")
        if source.width * source.height > 40_000_000:
            raise ValueError("사진 해상도가 너무 큽니다.")

        source.seek(0)
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail((640, 400), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (640, 400), "#f8f5ef")
        position = (
            (640 - image.width) // 2,
            (400 - image.height) // 2,
        )
        canvas.paste(image, position, image.getchannel("A"))

        for quality in (80, 65, 50):
            output = BytesIO()
            canvas.save(output, format="JPEG", quality=quality, optimize=True)
            result = output.getvalue()
            if len(result) <= MAX_THUMBNAIL_BYTES:
                return result

    raise ValueError("사진을 일정표 크기로 준비하지 못했습니다.")


def get_representative_image(place_id, *, timeout=3.0):
    """등록된 사진만 읽습니다. 실패는 None으로 반환하며 디스크에 저장하지 않습니다."""
    if not isinstance(place_id, str) or not re.fullmatch(r"P[0-9]{3}", place_id):
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return None
    if not isfinite(timeout) or timeout <= 0:
        return None

    try:
        place = list_place_images(place_id)
        if place is None:
            return None
        image = select_representative_image(place["images"])
        if image is None:
            return None

        if "cdn_url" in image:
            url = validate_cdn_photo_url(
                image["cdn_url"], image.get("photo_id"),
            )
            spec = _cloud_spec(image["photo_id"], place_id)
            size = spec["size"]
            expected_hash = spec["web_sha256"]
            key = ("cdn", url, expected_hash, size)
            path = None
        else:
            path = Path(image["path"]).resolve()
            if not path.is_relative_to(IMAGE_ROOT.resolve()) or not path.is_file():
                return None
            stat = path.stat()
            size = stat.st_size
            expected_hash = None
            key = ("local", str(path), stat.st_mtime_ns, size)

        if not 0 < size <= MAX_SOURCE_BYTES:
            return None

        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None and cached[0] > monotonic():
                _cache.move_to_end(key)
                return cached[1]

        if path is None:
            data = _read_cdn(url, size, min(timeout, 3.0))
        else:
            with path.open("rb") as source:
                data = source.read(size + 1)

        if len(data) != size:
            return None
        if expected_hash is not None:
            if hashlib.sha256(data).hexdigest() != expected_hash:
                return None

        thumbnail = _thumbnail(data)
        with _cache_lock:
            _cache[key] = (monotonic() + CACHE_SECONDS, thumbnail)
            _cache.move_to_end(key)
            while len(_cache) > CACHE_ENTRIES:
                _cache.popitem(last=False)

        return thumbnail
    except (
        OSError,
        HTTPException,
        ValueError,
        SyntaxError,
        sqlite3.Error,
        Image.DecompressionBombError,
    ):
        return None