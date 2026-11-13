"""제주 여행 동선 주변의 숙소 후보를 카카오 장소 검색으로 조회합니다."""

import json
import math
import re
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

from .kakao_local import JEJU_RECT


MIN_LONGITUDE, MIN_LATITUDE, MAX_LONGITUDE, MAX_LATITUDE = map(float, JEJU_RECT.split(","))
MAX_RADIUS_M = 20_000
MAX_RESULTS = 10


def _jeju_coordinate(latitude, longitude):
    return (
        type(latitude) in (int, float)
        and type(longitude) in (int, float)
        and math.isfinite(latitude)
        and math.isfinite(longitude)
        and MIN_LATITUDE <= latitude <= MAX_LATITUDE
        and MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
    )


def _distance_m(latitude, longitude, other_latitude, other_longitude):
    latitude, other_latitude = map(math.radians, (latitude, other_latitude))
    latitude_difference = other_latitude - latitude
    longitude_difference = math.radians(other_longitude - longitude)
    value = (
        math.sin(latitude_difference / 2) ** 2
        + math.cos(latitude) * math.cos(other_latitude)
        * math.sin(longitude_difference / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def _text(value, maximum):
    if not isinstance(value, str) or len(value.strip()) > maximum:
        return ""
    return value.strip()


def parse_lodging_response(data, *, latitude, longitude, radius_m, max_count):
    """숙박 분류·좌표·검색 범위를 확인한 후보만 공개 필드로 반환합니다."""
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        return {"status": "api_error", "places": []}

    candidates = {}
    conflicting_ids = set()
    for document in data["documents"]:
        if not isinstance(document, dict) or document.get("category_group_code") != "AD5":
            continue
        identifier = document.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9]{1,40}", identifier):
            continue
        name = _text(document.get("place_name"), 200)
        try:
            if isinstance(document.get("y"), bool) or isinstance(document.get("x"), bool):
                continue
            candidate_latitude = float(document["y"])
            candidate_longitude = float(document["x"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not name or not _jeju_coordinate(candidate_latitude, candidate_longitude):
            continue
        distance = _distance_m(latitude, longitude, candidate_latitude, candidate_longitude)
        if distance > radius_m:
            continue
        candidate = {
            "provider_place_id": identifier,
            "name": name,
            "address": _text(document.get("road_address_name"), 300)
            or _text(document.get("address_name"), 300),
            "phone": _text(document.get("phone"), 80),
            "latitude": candidate_latitude,
            "longitude": candidate_longitude,
            "distance_m": round(distance),
            "place_url": "https://place.map.kakao.com/" + identifier,
        }
        previous = candidates.get(identifier)
        if previous is not None and previous != candidate:
            conflicting_ids.add(identifier)
        candidates[identifier] = candidate

    places = [candidate for identifier, candidate in candidates.items() if identifier not in conflicting_ids]
    places.sort(key=lambda candidate: (candidate["distance_m"], candidate["provider_place_id"]))
    places = places[:max_count]
    return {"status": "ok" if places else "not_found", "places": places}


def search_nearby_lodgings(*, latitude, longitude, api_key, radius_m=5000, max_count=5):
    """숙소의 위치 후보만 조회합니다. 가격·잔여 객실·접근성을 확인하지 않습니다.

    distance_m는 기준 좌표와 숙소 좌표 사이의 직선거리입니다.
    검색 실패와 후보 없음은 구분하며, 결과를 숙소 확정이나 예약으로 취급하지 않습니다.
    """
    if (
        not _jeju_coordinate(latitude, longitude)
        or type(radius_m) is not int or not 1 <= radius_m <= MAX_RADIUS_M
        or type(max_count) is not int or not 1 <= max_count <= MAX_RESULTS
    ):
        return {"status": "invalid_input", "places": []}
    if not isinstance(api_key, str) or not api_key.strip():
        return {"status": "auth_error", "places": []}

    parameters = urlencode({
        "category_group_code": "AD5", "x": longitude, "y": latitude,
        "radius": radius_m, "rect": JEJU_RECT, "sort": "distance", "size": max_count,
    })
    request = Request(
        "https://dapi.kakao.com/v2/local/search/category.json?" + parameters,
        headers={"Authorization": "KakaoAK " + api_key.strip()},
    )
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        with urlopen(request, context=context, timeout=20) as response:
            data = json.load(response)
    except HTTPError as error:
        if error.code == 429:
            status = "rate_limited"
        elif error.code in (401, 403):
            status = "auth_error"
        else:
            status = "api_error"
        return {"status": status, "places": []}
    except (OSError, ValueError):
        return {"status": "api_error", "places": []}

    result = parse_lodging_response(
        data, latitude=latitude, longitude=longitude, radius_m=radius_m, max_count=max_count,
    )
    if result["status"] in ("ok", "not_found"):
        result.update({
            "source": "kakao_local",
            "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "center": {"latitude": latitude, "longitude": longitude},
            "radius_m": radius_m,
        })
    return result
