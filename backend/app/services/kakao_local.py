import json
import math
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

from .chat_request_transport import open_chat_url

# 제주도 범위 (경도 최소, 위도 최소, 경도 최대, 위도 최대). 추자도는 빠진다.
JEJU_RECT = "126.08,33.10,127.00,33.60"
RESTAURANT_CATEGORY_CODE = "FD6"

MIN_LONGITUDE, MIN_LATITUDE, MAX_LONGITUDE, MAX_LATITUDE = map(float, JEJU_RECT.split(","))
NEARBY_RESTAURANT_RADIUS_M = 2000
NEARBY_RESTAURANT_LIMIT = 5


def is_jeju_coordinate(latitude, longitude):
    return (
        type(latitude) in (int, float)
        and type(longitude) in (int, float)
        and math.isfinite(latitude)
        and math.isfinite(longitude)
        and MIN_LATITUDE <= latitude <= MAX_LATITUDE
        and MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
    )


def distance_meters(latitude, longitude, other_latitude, other_longitude):
    latitude, other_latitude = map(math.radians, (latitude, other_latitude))
    latitude_difference = other_latitude - latitude
    longitude_difference = math.radians(other_longitude - longitude)
    value = (
        math.sin(latitude_difference / 2) ** 2
        + math.cos(latitude) * math.cos(other_latitude)
        * math.sin(longitude_difference / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def parse_keyword_response(data: dict) -> dict:
    """카카오 키워드 장소 검색 응답에서 첫 번째 장소의 이름, 주소, 좌표를 읽습니다."""
    documents = data.get("documents")

    if not isinstance(documents, list):
        return {"status": "api_error"}
    if not documents:
        return {"status": "not_found"}

    return _read_keyword_place(documents[0])


def _read_keyword_place(first_place):
    try:
        address = first_place["road_address_name"]
        if not address:
            address = first_place["address_name"]

        location = {
            "status": "ok",
            "name": first_place["place_name"],
            "address": address,
            "latitude": float(first_place["y"]),
            "longitude": float(first_place["x"]),
        }
        if first_place.get("category_group_code") == RESTAURANT_CATEGORY_CODE:
            location["category_group_code"] = RESTAURANT_CATEGORY_CODE
        return location
    except (KeyError, TypeError, ValueError):
        return {"status": "api_error"}

def _request_places(endpoint, parameters, api_key):
    api_key = api_key.strip()
    if not api_key:
        return {"status": "auth_error"}

    params = urlencode(parameters)
    request = Request(
        f"https://dapi.kakao.com/v2/local/search/{endpoint}.json?{params}",
        headers={"Authorization": f"KakaoAK {api_key}"},
    )
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    try:
        with open_chat_url(urlopen, request, context=context, timeout=20) as response:
            data = json.load(response)
    except HTTPError as error:
        if error.code == 429:
            return {"status": "rate_limited"}
        if error.code in (401, 403):
            return {"status": "auth_error"}
        return {"status": "api_error"}
    except (OSError, ValueError):
        return {"status": "api_error"}

    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        return {"status": "api_error"}
    return {"status": "ok", "documents": data["documents"]}


def search_keyword_place(*, keyword: str, api_key: str) -> dict:
    result = _request_places("keyword", {"query": keyword, "rect": JEJU_RECT, "size": 1}, api_key)
    if result["status"] != "ok":
        return result
    return parse_keyword_response(result)


def search_nearby_restaurants(*, latitude, longitude, keyword, api_key):
    if not is_jeju_coordinate(latitude, longitude):
        return {"status": "invalid_input"}
    parameters = {
        "x": longitude, "y": latitude, "radius": NEARBY_RESTAURANT_RADIUS_M,
        "category_group_code": RESTAURANT_CATEGORY_CODE,
        "sort": "distance", "size": NEARBY_RESTAURANT_LIMIT,
    }
    endpoint = "category"
    if keyword.strip():
        parameters["query"] = keyword.strip()
        endpoint = "keyword"
    result = _request_places(endpoint, parameters, api_key)
    if result["status"] != "ok":
        return result

    candidates = {}
    for document in result["documents"]:
        if not isinstance(document, dict) or document.get("category_group_code") != RESTAURANT_CATEGORY_CODE:
            continue
        location = _read_keyword_place(document)
        if location["status"] != "ok" or not is_jeju_coordinate(location["latitude"], location["longitude"]):
            continue
        if not all(isinstance(location[key], str) and location[key].strip() for key in ("name", "address")):
            continue
        distance = distance_meters(latitude, longitude, location["latitude"], location["longitude"])
        if distance > NEARBY_RESTAURANT_RADIUS_M:
            continue
        location.pop("status")
        location["distance_m"] = round(distance)
        location["category_name"] = document.get("category_name") or ""
        candidates[(location["name"], location["address"])] = location
    places = sorted(candidates.values(), key=lambda place: place["distance_m"])[:NEARBY_RESTAURANT_LIMIT]
    if not places:
        if result["documents"]:
            return {"status": "api_error"}
        return {"status": "not_found"}
    return {"status": "ok", "places": places}
