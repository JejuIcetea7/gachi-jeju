"""한국관광공사 국문 관광정보 API(KorService2)로 휴무일·이용시간을 채울 수 있는지 표본 10곳으로 확인한다.

- 장소 이름으로 검색(searchKeyword2) -> 우리 좌표와 가장 가까운 후보 1개 -> 소개정보(detailIntro2)
- 같은 장소인지는 출력된 이름·거리를 보고 사람이 판단한다.
- 원본 응답은 outputs/check_tour_api/<place_id>.json 에 저장한다.

실행: python3 scripts/check_tour_api.py
필요: .env 의 DATA_GO_KR_API_KEY (공공데이터포털 일반 인증키, Decoding)
"""

import csv
import json
import math
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import truststore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
PLACES_FILE = PROJECT_ROOT / "data" / "processed" / "places.csv"
OUTPUT_DIRECTORY = PROJECT_ROOT / "outputs" / "check_tour_api"

API_BASE_URL = "https://apis.data.go.kr/B551011/KorService2"
SAMPLE_PLACE_IDS = ["P001", "P016", "P018", "P041", "P053", "P054", "P086", "P092", "P100", "P108"]
SEARCH_RESULT_COUNT = 20

# 콘텐츠 유형 -> (휴무일 칸, 이용시간 칸)
INTRO_FIELDS_BY_CONTENT_TYPE = {
    "12": ("restdate", "usetime"),  # 관광지
    "14": ("restdateculture", "usetimeculture"),  # 문화시설
    "28": ("restdateleports", "usetimeleports"),  # 레포츠
    "38": ("restdateshopping", "opentime"),  # 쇼핑
}


def read_api_key():
    api_key = ""
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "DATA_GO_KR_API_KEY":
            api_key = value.strip()
            break
    return api_key


def load_sample_places():
    places = []
    with open(PLACES_FILE, encoding="utf-8", newline="") as places_file:
        for row in csv.DictReader(places_file):
            if row["place_id"] in SAMPLE_PLACE_IDS:
                places.append(row)
    return places


def call_api(operation, api_key, extra_params):
    """API를 호출해 (응답 dict, 오류 메시지)를 돌려준다"""
    params = {
        "serviceKey": api_key,
        "MobileOS": "ETC",
        "MobileApp": "gachi-jeju",
        "_type": "json",
    }
    params.update(extra_params)
    url = f"{API_BASE_URL}/{operation}?{urlencode(params)}"
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    try:
        with urlopen(url, context=context, timeout=20) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        return None, f"HTTP {error.code}: {error.reason}"
    except URLError as error:
        return None, f"연결 실패: {error.reason}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, "JSON 아님: " + body[:200]
    return data, None


def extract_items(data):
    """응답 body.items.item 을 항상 리스트로 꺼낸다 (결과 없으면 items가 빈 문자열)"""
    items = []
    body = data.get("response", {}).get("body", {})
    items_value = body.get("items")
    if not isinstance(items_value, dict):
        return items

    item_value = items_value.get("item", [])
    if isinstance(item_value, dict):
        items.append(item_value)
    else:
        items.extend(item_value)
    return items


def calculate_distance_meters(latitude_a, longitude_a, latitude_b, longitude_b):
    earth_radius_meters = 6371000
    latitude_a_radian = math.radians(latitude_a)
    latitude_b_radian = math.radians(latitude_b)
    latitude_difference = math.radians(latitude_b - latitude_a)
    longitude_difference = math.radians(longitude_b - longitude_a)

    half_chord = math.sin(latitude_difference / 2) ** 2
    half_chord += math.cos(latitude_a_radian) * math.cos(latitude_b_radian) * math.sin(longitude_difference / 2) ** 2
    angular_distance = 2 * math.atan2(math.sqrt(half_chord), math.sqrt(1 - half_chord))
    distance_meters = earth_radius_meters * angular_distance
    return distance_meters


def choose_nearest_item(items, place):
    nearest_item = None
    nearest_distance = None
    place_latitude = float(place["latitude"])
    place_longitude = float(place["longitude"])

    for item in items:
        if not item.get("mapy") or not item.get("mapx"):
            continue
        distance = calculate_distance_meters(place_latitude, place_longitude, float(item["mapy"]), float(item["mapx"]))
        if nearest_distance is None or distance < nearest_distance:
            nearest_item = item
            nearest_distance = distance
    return nearest_item, nearest_distance


def check_place(place, api_key):
    result = {
        "place_id": place["place_id"],
        "name": place["name"],
        "found_title": "",
        "distance_m": "",
        "content_type_id": "",
        "candidate_count": 0,
        "restdate": "",
        "usetime": "",
        "error": "",
    }
    responses = {}

    search_params = {"keyword": place["name"], "numOfRows": SEARCH_RESULT_COUNT}
    search_data, search_error = call_api("searchKeyword2", api_key, search_params)
    responses["searchKeyword2"] = search_data
    if search_error is not None:
        result["error"] = "검색 " + search_error
        return result, responses

    items = extract_items(search_data)
    result["candidate_count"] = len(items)
    nearest_item, nearest_distance = choose_nearest_item(items, place)
    if nearest_item is None:
        result["error"] = "검색 결과 없음"
        return result, responses

    content_type_id = str(nearest_item["contenttypeid"])
    result["found_title"] = nearest_item["title"]
    result["distance_m"] = round(nearest_distance)
    result["content_type_id"] = content_type_id

    intro_params = {"contentId": nearest_item["contentid"], "contentTypeId": content_type_id}
    intro_data, intro_error = call_api("detailIntro2", api_key, intro_params)
    responses["detailIntro2"] = intro_data
    if intro_error is not None:
        result["error"] = "소개정보 " + intro_error
        return result, responses

    intro_items = extract_items(intro_data)
    if len(intro_items) == 0:
        result["error"] = "소개정보 없음"
        return result, responses

    intro_fields = INTRO_FIELDS_BY_CONTENT_TYPE.get(content_type_id)
    if intro_fields is None:
        result["error"] = "다루지 않는 콘텐츠 유형"
        return result, responses

    restdate_field, usetime_field = intro_fields
    result["restdate"] = intro_items[0].get(restdate_field, "")
    result["usetime"] = intro_items[0].get(usetime_field, "")
    return result, responses


def main():
    api_key = read_api_key()
    if not api_key:
        raise SystemExit(".env에 DATA_GO_KR_API_KEY 값을 입력해 주세요.")

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    places = load_sample_places()

    for place in places:
        result, responses = check_place(place, api_key)

        response_file = OUTPUT_DIRECTORY / f"{place['place_id']}.json"
        response_file.write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")

        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
