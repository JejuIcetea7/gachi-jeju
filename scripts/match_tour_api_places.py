"""휴무일 빈칸 장소를 한국관광공사 API(KorService2)에서 찾아 후보 1개씩 고르고 3단계로 나눈다.

- 후보 목록: 제주(법정동 시도코드 50)의 관광지(12)·문화시설(14)·레포츠(28)·쇼핑(38) 전체를
  지역기반 조회(areaBasedList2)로 한 번 받아 두고 이 안에서 고른다.
  (이름 검색은 띄어쓰기가 달라도 못 찾고, 위치기반 조회는 반경 안 장소를 빠뜨려서 쓰지 않음)
- 단계 (거리 기준 1km)
  1. 완전 일치: 정규화한 이름(괄호 속 별칭 포함)이 같고 3km 이내, 또는 한쪽 이름이 다른 쪽에 통째로 들어 있고 500m 이내
  2. 같은 장소로 봄: 이름이 비슷하고(유사도 0.6 이상) 1km 이내
  3. 불확실: 그 외
- 고른 후보의 소개정보(detailIntro2)에서 휴무일·이용시간 원문을 가져온다.

결과: outputs/tour_api_candidates/candidates.csv
원본: outputs/tour_api_candidates/jeju_catalog.json (제주 전체 목록), responses/<place_id>.json (소개정보)
     두 파일이 있으면 다시 받지 않고 그대로 쓴다 (API 하루 한도 절약)
실행: python3 scripts/match_tour_api_places.py
필요: .env 의 DATA_GO_KR_API_KEY
"""

import csv
import difflib
import json
import re
from pathlib import Path

from check_tour_api import calculate_distance_meters, call_api, extract_items, read_api_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLACES_FILE = PROJECT_ROOT / "data" / "processed" / "places.csv"
OUTPUT_DIRECTORY = PROJECT_ROOT / "outputs" / "tour_api_candidates"
RESPONSES_DIRECTORY = OUTPUT_DIRECTORY / "responses"
CATALOG_FILE = OUTPUT_DIRECTORY / "jeju_catalog.json"
CANDIDATES_FILE = OUTPUT_DIRECTORY / "candidates.csv"
REVIEW_FILE = PROJECT_ROOT / "data" / "processed" / "tour_api_review.csv"  # 사람이 확인한 결과 (통과/틀림)

JEJU_REGION_CODE = 50
CATALOG_PAGE_SIZE = 1000
SAME_PLACE_DISTANCE_METERS = 1000
CONTAINED_NAME_DISTANCE_METERS = 500  # 한쪽 이름이 다른 쪽에 들어 있으면 이 거리 안에서 완전 일치로 본다
EXACT_NAME_DISTANCE_METERS = 3000  # 이름이 완전히 같으면 이 거리까지 완전 일치로 본다 (마을·농원처럼 넓은 장소)
NEARBY_COUNT_DISTANCE_METERS = 3000
SIMILAR_NAME_THRESHOLD = 0.6

STAGE_EXACT = 1
STAGE_SIMILAR = 2
STAGE_UNCERTAIN = 3

# 콘텐츠 유형 -> (휴무일 칸, 이용시간 칸)
INTRO_FIELDS_BY_CONTENT_TYPE = {
    "12": ("restdate", "usetime"),  # 관광지
    "14": ("restdateculture", "usetimeculture"),  # 문화시설
    "28": ("restdateleports", "usetimeleports"),  # 레포츠
    "38": ("restdateshopping", "opentime"),  # 쇼핑
}

CANDIDATE_COLUMNS = [
    "place_id", "name", "category", "stage", "candidate_title", "distance_m", "name_similarity",
    "content_id", "content_type_id", "nearby_count", "review", "restdate_raw", "usetime_raw", "error",
]


def load_reviews():
    """(place_id, content_id) -> 확인 결과. 후보가 바뀌면 이전 확인 결과를 쓰지 않도록 content_id까지 맞춘다"""
    review_by_key = {}
    if not REVIEW_FILE.exists():
        return review_by_key

    with open(REVIEW_FILE, encoding="utf-8", newline="") as review_file:
        for row in csv.DictReader(review_file):
            review_key = (row["place_id"], row["content_id"])
            review_by_key[review_key] = row["review"]
    return review_by_key


def normalize_name(name):
    """공백·괄호 내용·'제주특별자치도'·'제주'·기호를 지운 이름"""
    normalized_name = re.sub(r"\(.*?\)", "", name)
    normalized_name = normalized_name.replace("제주특별자치도", "")
    normalized_name = normalized_name.replace("제주", "")
    normalized_name = re.sub(r"[^가-힣A-Za-z0-9]", "", normalized_name)
    return normalized_name


def list_candidate_names(candidate_title):
    """후보 이름과 괄호 속 별칭을 정규화한 목록. 예: '자구리문화예술공원(자구리공원)' -> 2개"""
    candidate_names = [normalize_name(candidate_title)]
    for alias in re.findall(r"\((.*?)\)", candidate_title):
        normalized_alias = normalize_name(alias)
        if normalized_alias != "":
            candidate_names.append(normalized_alias)
    return candidate_names


def compare_names(place_name, candidate_title):
    """(가장 높은 이름 유사도, 한쪽 이름이 다른 쪽에 통째로 들어 있는지)"""
    normalized_place_name = normalize_name(place_name)
    best_similarity = 0.0
    is_contained = False
    # 'OO점'은 지점 상점이라 포함 관계로 보지 않는다 (예: 동문시장 -> 다이소 제주동문시장점)
    is_branch_store = candidate_title.strip().endswith("점")

    for candidate_name in list_candidate_names(candidate_title):
        name_similarity = difflib.SequenceMatcher(None, normalized_place_name, candidate_name).ratio()
        if name_similarity > best_similarity:
            best_similarity = name_similarity
        if normalized_place_name == "" or candidate_name == "" or is_branch_store:
            continue
        if normalized_place_name in candidate_name or candidate_name in normalized_place_name:
            is_contained = True
    return best_similarity, is_contained


def load_unfilled_places():
    places = []
    with open(PLACES_FILE, encoding="utf-8", newline="") as places_file:
        for row in csv.DictReader(places_file):
            if row["closed_days"] == "":
                places.append(row)
    return places


def download_jeju_catalog(api_key):
    """제주 관광지·문화시설·레포츠·쇼핑 전체 목록. 받을 수 없으면 종료"""
    catalog = []
    for content_type_id in INTRO_FIELDS_BY_CONTENT_TYPE:
        page_number = 1
        while True:
            params = {
                "lDongRegnCd": JEJU_REGION_CODE,
                "contentTypeId": content_type_id,
                "numOfRows": CATALOG_PAGE_SIZE,
                "pageNo": page_number,
            }
            data, error = call_api("areaBasedList2", api_key, params)
            if error is not None:
                raise SystemExit(f"제주 목록 받기 실패 (유형 {content_type_id}, {page_number}쪽): {error}")

            items = extract_items(data)
            catalog.extend(items)
            total_count = data["response"]["body"]["totalCount"]
            print(f"[목록] 유형 {content_type_id} {page_number}쪽 {len(items)}건 / 전체 {total_count}")
            if page_number * CATALOG_PAGE_SIZE >= total_count:
                break
            page_number += 1
    return catalog


def decide_stage(name_similarity, is_contained, distance_meters):
    stage = STAGE_UNCERTAIN
    if distance_meters is None:
        return stage
    if name_similarity == 1.0 and distance_meters <= EXACT_NAME_DISTANCE_METERS:
        stage = STAGE_EXACT
        return stage
    if distance_meters > SAME_PLACE_DISTANCE_METERS:
        return stage
    if name_similarity == 1.0:
        stage = STAGE_EXACT
    elif is_contained and distance_meters <= CONTAINED_NAME_DISTANCE_METERS:
        stage = STAGE_EXACT
    elif name_similarity >= SIMILAR_NAME_THRESHOLD:
        stage = STAGE_SIMILAR
    return stage


def list_exact_name_candidates(place, catalog):
    """거리와 상관없이 이름(별칭 포함)이 완전히 같은 후보 목록"""
    exact_name_candidates = []
    for candidate in catalog:
        name_similarity, _ = compare_names(place["name"], candidate["title"])
        if name_similarity == 1.0:
            exact_name_candidates.append(candidate)
    return exact_name_candidates


def choose_best_candidate(place, catalog):
    """단계가 낮을수록, 이름 유사도가 높을수록, 거리가 가까울수록 앞. (후보, 단계, 유사도, 거리, 3km 안 후보 수)
    이름이 완전히 같은 후보가 있으면 거리가 멀어도 그 안에서만 고른다 (예: 마라도 -> '마라도가는여객선'이 아닌 '마라도')"""
    best = None
    best_sort_key = None
    nearby_count = 0
    place_latitude = float(place["latitude"])
    place_longitude = float(place["longitude"])

    selectable_candidates = list_exact_name_candidates(place, catalog)
    if len(selectable_candidates) == 0:
        selectable_candidates = catalog

    for candidate in catalog:
        if candidate.get("mapy") and candidate.get("mapx"):
            distance_meters = calculate_distance_meters(
                place_latitude, place_longitude, float(candidate["mapy"]), float(candidate["mapx"])
            )
            if distance_meters <= NEARBY_COUNT_DISTANCE_METERS:
                nearby_count += 1

    for candidate in selectable_candidates:
        distance_meters = None
        if candidate.get("mapy") and candidate.get("mapx"):
            distance_meters = calculate_distance_meters(
                place_latitude, place_longitude, float(candidate["mapy"]), float(candidate["mapx"])
            )

        name_similarity, is_contained = compare_names(place["name"], candidate["title"])
        stage = decide_stage(name_similarity, is_contained, distance_meters)

        sort_distance = distance_meters
        if sort_distance is None:
            sort_distance = float("inf")
        sort_key = (stage, -name_similarity, sort_distance)
        if best_sort_key is None or sort_key < best_sort_key:
            best = (candidate, stage, name_similarity, distance_meters)
            best_sort_key = sort_key
    return best, nearby_count


def load_saved_intro(place_id, content_id):
    """이전 실행에서 저장한 같은 후보의 소개정보 응답. 없거나 다른 후보면 None"""
    saved_intro_data = None
    response_file = RESPONSES_DIRECTORY / f"{place_id}.json"
    if not response_file.exists():
        return saved_intro_data

    intro_data = json.loads(response_file.read_text(encoding="utf-8"))
    if not isinstance(intro_data, dict):
        return saved_intro_data
    intro_items = extract_items(intro_data)
    if len(intro_items) > 0 and str(intro_items[0].get("contentid")) == content_id:
        saved_intro_data = intro_data
    return saved_intro_data


def fetch_intro(place_id, candidate, api_key):
    """(휴무일 원문, 이용시간 원문, 오류, 응답). 같은 후보의 저장된 응답이 있으면 API를 부르지 않는다"""
    content_type_id = str(candidate["contenttypeid"])
    content_id = str(candidate["contentid"])
    restdate_field, usetime_field = INTRO_FIELDS_BY_CONTENT_TYPE[content_type_id]

    intro_data = load_saved_intro(place_id, content_id)
    if intro_data is None:
        intro_params = {"contentId": content_id, "contentTypeId": content_type_id}
        intro_data, intro_error = call_api("detailIntro2", api_key, intro_params)
        if intro_error is not None:
            return "", "", "소개정보 " + intro_error, intro_data

    intro_items = extract_items(intro_data)
    if len(intro_items) == 0:
        return "", "", "소개정보 없음", intro_data

    restdate_raw = intro_items[0].get(restdate_field, "")
    usetime_raw = intro_items[0].get(usetime_field, "")
    return restdate_raw, usetime_raw, "", intro_data


def match_place(place, catalog, api_key):
    row = {
        "place_id": place["place_id"],
        "name": place["name"],
        "category": place["category"],
        "stage": STAGE_UNCERTAIN,
        "candidate_title": "",
        "distance_m": "",
        "name_similarity": "",
        "content_id": "",
        "content_type_id": "",
        "nearby_count": 0,
        "review": "",
        "restdate_raw": "",
        "usetime_raw": "",
        "error": "",
    }

    best, nearby_count = choose_best_candidate(place, catalog)
    row["nearby_count"] = nearby_count
    if best is None:
        row["error"] = "후보 없음"
        return row, None

    candidate, stage, name_similarity, distance_meters = best
    row["stage"] = stage
    row["candidate_title"] = candidate["title"]
    if distance_meters is not None:
        row["distance_m"] = round(distance_meters)
    row["name_similarity"] = round(name_similarity, 2)
    row["content_id"] = str(candidate["contentid"])
    row["content_type_id"] = str(candidate["contenttypeid"])

    restdate_raw, usetime_raw, intro_error, intro_data = fetch_intro(place["place_id"], candidate, api_key)
    row["restdate_raw"] = restdate_raw
    row["usetime_raw"] = usetime_raw
    row["error"] = intro_error
    return row, intro_data


def main():
    api_key = read_api_key()
    if not api_key:
        raise SystemExit(".env에 DATA_GO_KR_API_KEY 값을 입력해 주세요.")

    RESPONSES_DIRECTORY.mkdir(parents=True, exist_ok=True)
    if CATALOG_FILE.exists():
        catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        print(f"[목록] 저장된 제주 목록 {len(catalog)}건 사용 (새로 받으려면 {CATALOG_FILE.name} 삭제)")
    else:
        catalog = download_jeju_catalog(api_key)
        CATALOG_FILE.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    places = load_unfilled_places()
    review_by_key = load_reviews()
    rows = []
    for index, place in enumerate(places, start=1):
        row, intro_data = match_place(place, catalog, api_key)
        review_key = (row["place_id"], row["content_id"])
        row["review"] = review_by_key.get(review_key, "")
        rows.append(row)

        response_file = RESPONSES_DIRECTORY / f"{place['place_id']}.json"
        response_file.write_text(json.dumps(intro_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{index}/{len(places)}] {place['place_id']} {place['name']} -> {row['stage']}단계 {row['candidate_title']}")

    rows.sort(key=lambda row: (row["stage"], row["place_id"]))
    with open(CANDIDATES_FILE, "w", encoding="utf-8-sig", newline="") as candidates_file:
        writer = csv.DictWriter(candidates_file, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    stage_counts = {STAGE_EXACT: 0, STAGE_SIMILAR: 0, STAGE_UNCERTAIN: 0}
    for row in rows:
        stage_counts[row["stage"]] += 1
    print(f"1단계 {stage_counts[STAGE_EXACT]}, 2단계 {stage_counts[STAGE_SIMILAR]}, 3단계 {stage_counts[STAGE_UNCERTAIN]}")
    print(f"결과: {CANDIDATES_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
