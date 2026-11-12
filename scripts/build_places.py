"""무장애 여행지 원본 데이터를 places / place_spots / place_nearby 로 통합한다.

실행: python scripts/build_places.py
설계: docs/plans/build_places.md
"""

import csv
import difflib
import json
import math
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIRECTORY = PROJECT_ROOT / "data" / "raw"
HUB_DIRECTORY = RAW_DIRECTORY / "jeju_datahub"
PORTAL_DIRECTORY = RAW_DIRECTORY / "data_go_kr"
LINKS_FILE = PORTAL_DIRECTORY / "links.csv"
PROCESSED_DIRECTORY = PROJECT_ROOT / "data" / "processed"
HWP_GENERAL_INFO_DIRECTORY = PROCESSED_DIRECTORY / "hwp_general_info"
CLOSED_DAYS_MAP_FILE = PROCESSED_DIRECTORY / "closed_days_map.csv"
OUTPUT_DIRECTORY = PROJECT_ROOT / "outputs" / "build_places"

PLACES_FILE = PROCESSED_DIRECTORY / "places.csv"
SPOTS_FILE = PROCESSED_DIRECTORY / "place_spots.csv"
NEARBY_FILE = PROCESSED_DIRECTORY / "place_nearby.csv"
ACCESSIBILITY_RAW_FILE = PROCESSED_DIRECTORY / "place_accessibility_raw.csv"
HWP_HUB_INFO_DIRECTORY = PROCESSED_DIRECTORY / "hwp_hub_info"
PLACE_ID_MAP_FILE = PROCESSED_DIRECTORY / "place_id_map.csv"
DATABASE_FILE = PROCESSED_DIRECTORY / "gachi_jeju.db"

SOURCE_FILES_LOG = OUTPUT_DIRECTORY / "source_files.csv"
FAILURES_LOG = OUTPUT_DIRECTORY / "failures.csv"
CONFLICTS_LOG = OUTPUT_DIRECTORY / "conflicts.csv"
DUPLICATE_CANDIDATES_LOG = OUTPUT_DIRECTORY / "duplicate_candidates.csv"
ROW_COUNT_LOG = OUTPUT_DIRECTORY / "row_count_check.csv"
ANOMALIES_LOG = OUTPUT_DIRECTORY / "anomalies.csv"

UNKNOWN = "unknown"
DEFAULT_VISIT_MINUTES = 60
SPLIT_ROW_MERGE_DISTANCE_METERS = 10
DUPLICATE_NEAR_DISTANCE_METERS = 300
NEARBY_NAME_SIMILARITY_THRESHOLD = 0.75
NEARBY_DISTANCE_LIMIT_METERS = 10000
JEJU_LATITUDE_RANGE = (33.0, 33.6)
JEJU_LONGITUDE_RANGE = (126.1, 127.0)

# 지점 형식 우선순위 (이름·좌표 충돌 시 앞이 우선)
SPOT_FORMAT_PRIORITY = ["hub_number_spots", "place_name_50_spots", "place_name_20_spots", "course_spots"]
NEARBY_FORMATS = ["nearby_info", "nearby_etc"]

PLACES_COLUMNS = [
    "place_id", "name", "category",
    "region", "address", "latitude", "longitude",
    "wheelchair_level", "stroller_level", "has_slope_or_stairs",
    "accessible_parking", "accessible_toilet", "wheelchair_rental", "stroller_rental",
    "indoor_outdoor", "visit_minutes", "opening_hours", "closed_days", "admission_fee",
    "sources", "data_date",
]
SPOTS_COLUMNS = [
    "place_id", "order", "name", "latitude", "longitude", "description",
    "accessibility_note", "is_recommended_course", "image_file", "source_file",
]
NEARBY_COLUMNS = [
    "place_id", "facility_type", "name", "distance_m", "address", "phone",
    "access_note", "wheelchair_entry", "accessible_toilet", "source_file",
]
PLACE_ID_MAP_COLUMNS = ["source_file", "source_format", "source_place_name", "place_id", "match_status"]
ACCESSIBILITY_RAW_COLUMNS = ["place_id", "section", "facility_name", "description", "raw_value", "source_file"]

# 일반정보 HWP 상세 표 제목(공백 제거) 앞부분 -> section
ACCESSIBILITY_DETAIL_SECTIONS = [
    ("추천대상", "추천 대상"),
    ("주차장", "주차장"),
    ("장애인화장실", "장애인 화장실"),
    ("주출입구", "주출입구"),
]
# 주차장 표에서 접근성과 관계없는 구분 (일반 주차·대형버스·전기차)
EXCLUDED_PARKING_GROUPS = ["일반주차구역", "대형버스접근여부", "전기차충전소"]
# 허브 HWP 이용안내에서 가져올 항목. 주차장은 '장애인'이 들어간 경우만
HUB_ACCESSIBILITY_LABELS = ["보조기기", "편의장비", "승강기"]
HUB_PARKING_LABEL = "주차장"

COLUMN_TYPES = {
    "latitude": "REAL",
    "longitude": "REAL",
    "order": "INTEGER",
    "distance_m": "INTEGER",
    "visit_minutes": "INTEGER",
}

FACILITY_TYPE_MAP = {
    "음식점": "음식점",
    "숙박시설": "숙박",
    "숙박 시설": "숙박",
    "카페/편의점": "카페·편의점",
    "화장실": "화장실",
    "관광지": "관광지",
    "주변 관광지": "관광지",
}

INFO_CODE_MAP = {
    "1": "접근성 정보 있음 (계단, 길 상태, 경사)",
    "2": "장애인화장실",
}

# 이름에 들어 있으면 해당 카테고리로 본다 (위에서부터 먼저 맞는 것)
CATEGORY_KEYWORDS = [
    ("박물관·전시", ["박물관", "미술관", "기념관", "전시관", "갤러리", "뮤지엄", "추사관", "센터", "벙커", "공연장"]),
    ("시장", ["시장", "오일장"]),
    ("해변", ["해변", "해수욕장"]),
    ("자연", ["오름", "숲", "휴양림", "생태", "수목원", "곶자왈", "저수지", "동백", "목장",
            "폭포", "정원", "식물원", "습지", "코지", "비자림", "섬", "전망대", "경승지", "굼부리"]),
    ("공원", ["공원", "랜드", "빌리지", "테마파크"]),
    ("산책로", ["길", "해안로", "산책", "올레", "코스", "탐방로", "거리", "골목", "포구", "항", "마을",
            "해안", "도로", "환해장성"]),
]

# 일반정보 HWP(변환 결과: scripts/convert_hwp_general_info.py)의 '휴무일' 원문(공백 제거) -> closed_days 정리 값
CLOSED_DAYS_BY_RAW_TEXT = {
    "연중무휴": "연중무휴",
    "없음": "연중무휴",  # 휴무일 없음
}

# 원문이 장소 자체의 휴무일이 아닌 곳 (place_id -> closed_days 정리 값)
CLOSED_DAYS_OVERRIDES_BY_PLACE_ID = {
    "P128": "연중무휴",  # 금악리골목식당투어: 원문은 식당별 휴무, 골목 자체는 상시 개방
    "P156": "연중무휴",  # 이중섭거리: 원문은 이중섭미술관 휴무, 거리 자체는 상시 개방
    "P131": "",  # 농업생태원: 원문이 입장마감·점심시간(운영시간)이라 휴무일 정보 없음
}

# closed_days 정리 값에 쓸 수 있는 형식 (여러 개는 ';'로 잇고, 연중무휴는 단독)
# 설날·추석은 당일, 설날연휴·추석연휴는 전날·당일·다음날 3일
CLOSED_DAY_PATTERNS = [
    r"연중무휴",
    r"매주 [월화수목금토일]요일",
    r"매월 (첫째|둘째|셋째|넷째|다섯째|마지막) [월화수목금토일]요일",
    r"(1[0-2]|[1-9])월 (3[01]|[12][0-9]|[1-9])일",
    r"설날|추석|설날연휴|추석연휴",
    r"법정공휴일",
]

# 원본 파일 이름의 장소명 오타로 이름 대조가 안 되는 일반정보 HWP (데이터ID -> place_id)
HWP_PLACE_ID_BY_DATA_ID = {
    "15097477": "P142",  # 삼양환해장성과벌랑포구 해안길 (장소: 벌낭포구)
    "15097482": "P146",  # 수산저주지와물메둑길 (장소: 수산저수지)
}


# ---------------------------------------------------------------------------
# 파일 읽기 / 저장
# ---------------------------------------------------------------------------

def read_csv_file(file_path, encoding):
    with open(file_path, encoding=encoding, newline="") as csv_file:
        reader = csv.reader(csv_file)
        raw_rows = list(reader)

    if len(raw_rows) == 0:
        return [], []

    header = []
    for column_name in raw_rows[0]:
        clean_name = column_name.replace("﻿", "").strip()
        header.append(clean_name)

    rows = []
    for raw_row in raw_rows[1:]:
        if len(raw_row) == 0:
            continue
        row = {}
        for column_index, column_name in enumerate(header):
            value = ""
            if column_index < len(raw_row):
                value = raw_row[column_index].strip()
            row[column_name] = value
        has_value = False
        for value in row.values():
            if value != "":
                has_value = True
                break
        if not has_value:
            continue
        rows.append(row)
    return header, rows


def save_csv_file(file_path, columns, rows):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_json_file(file_path):
    with open(file_path, encoding="utf-8") as json_file:
        return json.load(json_file)


def relative_path(file_path):
    return str(Path(file_path).relative_to(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 1. 원본 목록 만들기
# ---------------------------------------------------------------------------

def load_hub_number_names():
    """links.csv에서 hub_1~hub_51 폴더의 장소명을 읽는다. 예: '_9.가파도' -> '가파도'"""
    hub_number_names = {}
    _, link_rows = read_csv_file(LINKS_FILE, "utf-8")
    for link_row in link_rows:
        local_folder = link_row["local_folder"]
        if link_row["provide_type"] != "허브연결":
            continue
        if not local_folder.startswith("hub_"):
            continue
        title_tail = link_row["title"].split("_")[-1]
        place_name = re.sub(r"^\d+[.\-]\s*", "", title_tail)
        hub_number_names[local_folder] = place_name
    return hub_number_names


def load_portal_number_folders():
    """links.csv에서 포털 직접 제공 A번호 -> 장소명 폴더. 예: '_41-제주국립박물관' -> {'41': 'hub_92'}"""
    folder_by_number = {}
    _, link_rows = read_csv_file(LINKS_FILE, "utf-8")
    for link_row in link_rows:
        if link_row["provide_type"] != "공공데이터포털":
            continue
        if not link_row["local_folder"].startswith("hub_"):
            continue
        number_match = re.search(r"_(\d+)-", link_row["title"])
        if number_match is None:
            continue
        folder_by_number[number_match.group(1)] = link_row["local_folder"]
    return folder_by_number


def extract_portal_number(source_file):
    """'...주변정보-41-국립제주박물관_20201230.csv' -> '41'"""
    portal_number = ""
    number_match = re.search(r"주변정보-(\d+)-", source_file)
    if number_match is not None:
        portal_number = number_match.group(1)
    return portal_number


def extract_place_name_from_portal_title(title):
    """'제주특별자치도_가문동해안길_무장애여행코스' -> '가문동해안길'"""
    place_name = title
    while place_name.startswith("제주특별자치도_"):
        place_name = place_name[len("제주특별자치도_"):]
    place_name = re.sub(r"_무장애[^_]*$", "", place_name)
    return place_name


def detect_source_format(file_name, header):
    source_format = None
    if re.match(r"^\d{3}-2\.csv$", file_name):
        source_format = "hub_number_spots"
    elif re.match(r"^\d{3}-1\.csv$", file_name):
        source_format = "hub_number_track"
    elif "무장애관광정보" in header:
        source_format = "place_name_50_spots"
    elif ("title" in header or "Title" in header) and "info" in header:
        source_format = "place_name_20_spots"
    elif "순서" in header and "상세정보" in header:
        source_format = "course_spots"
    elif "관광지명" in header:
        source_format = "nearby_info"
    elif "휠체어 진입" in header:
        source_format = "nearby_etc"
    return source_format


def find_latest_data_date(rows):
    latest_data_date = ""
    for row in rows:
        data_date = row.get("데이터기준일자", "")
        if data_date > latest_data_date:
            latest_data_date = data_date
    return latest_data_date


def list_source_files(failures):
    """원본 CSV를 읽고 형식을 분류한다. 반환: 원본 정보 목록 (rows 포함)"""
    hub_number_names = load_hub_number_names()
    sources = []

    hub_csv_paths = sorted(HUB_DIRECTORY.glob("hub_*/*.csv"))
    portal_csv_paths = sorted(PORTAL_DIRECTORY.glob("dgk_*/*.csv"))
    all_csv_paths = hub_csv_paths + portal_csv_paths

    for csv_path in all_csv_paths:
        source_file = relative_path(csv_path)
        try:
            header, rows = read_csv_file(csv_path, "cp949")
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            failures.append({"source_file": source_file, "step": "read", "reason": str(error)})
            continue

        source_format = detect_source_format(csv_path.name, header)
        if source_format is None:
            failures.append({"source_file": source_file, "step": "detect_format", "reason": "알 수 없는 컬럼: " + ",".join(header)})
            continue

        folder_name = csv_path.parent.name
        place_name = ""
        if source_format in ("hub_number_spots", "hub_number_track"):
            place_name = hub_number_names.get(folder_name, "")
        elif csv_path.parent.parent == HUB_DIRECTORY:
            place_name = csv_path.stem.replace("제주특별자치도_무장애여행정보_", "")
        elif source_format == "nearby_info" and len(rows) > 0:
            place_name = rows[0]["관광지명"]
        else:
            meta = read_json_file(csv_path.parent / "meta.json")
            place_name = extract_place_name_from_portal_title(meta["title"])

        if place_name == "":
            failures.append({"source_file": source_file, "step": "place_name", "reason": "장소명을 찾지 못함"})

        source = {
            "source_file": source_file,
            "source_format": source_format,
            "folder": folder_name,
            "place_name": place_name,
            "row_count": len(rows),
            "data_date": find_latest_data_date(rows),
            "rows": rows,
        }
        sources.append(source)
    return sources


def save_source_list(sources):
    log_rows = []
    for source in sources:
        log_row = {
            "source_file": source["source_file"],
            "source_format": source["source_format"],
            "place_name": source["place_name"],
            "row_count": source["row_count"],
            "data_date": source["data_date"],
        }
        log_rows.append(log_row)
    columns = ["source_file", "source_format", "place_name", "row_count", "data_date"]
    save_csv_file(SOURCE_FILES_LOG, columns, log_rows)


# ---------------------------------------------------------------------------
# 2. 지점 변환 (형식별)
# ---------------------------------------------------------------------------

def parse_float(text):
    value = None
    try:
        value = float(text)
    except ValueError:
        pass
    return value


def make_spot(name, latitude_text, longitude_text, description, accessibility_note,
              is_recommended_course, image_file, source_file):
    spot = {
        "place_id": "",
        "order": 0,
        "name": name,
        "latitude": parse_float(latitude_text),
        "longitude": parse_float(longitude_text),
        "description": description,
        "accessibility_note": accessibility_note,
        "is_recommended_course": is_recommended_course,
        "image_file": image_file,
        "source_file": source_file,
    }
    return spot


def convert_yes_no(text):
    value = UNKNOWN
    if text == "Y":
        value = "Y"
    elif text == "N":
        value = "N"
    return value


def convert_hub_number_spots(source):
    spots = []
    for row in source["rows"]:
        image_file = row.get("이미지파일명", row.get("이미지파일", ""))
        spot = make_spot(
            name=row.get("장소명", ""),
            latitude_text=row["위도"],
            longitude_text=row["경도"],
            description=row.get("장소설명", ""),
            accessibility_note=UNKNOWN,
            is_recommended_course=UNKNOWN,
            image_file=image_file,
            source_file=source["source_file"],
        )
        spots.append(spot)
    return spots


def convert_place_name_50_spots(source):
    spots = []
    for row in source["rows"]:
        spot = make_spot(
            name=row["장소명칭"],
            latitude_text=row["위도"],
            longitude_text=row["경도"],
            description=row["장소상세정보"],
            accessibility_note=row["무장애관광정보"],
            is_recommended_course=convert_yes_no(row["추천코스여부"]),
            image_file="",
            source_file=source["source_file"],
        )
        spots.append(spot)
    return spots


def is_legend_only_row(row):
    """이름·좌표 없이 etc 범례만 있는 행. 지점이 4개보다 적은 파일에 범례용으로 붙어 있다."""
    title = row.get("title", row.get("Title", ""))
    if title == "" and row["lat"] == "" and row["lon"] == "" and row["etc"] != "":
        return True
    return False


def convert_place_name_20_spots(source):
    spots = []
    for row in source["rows"]:
        if is_legend_only_row(row):
            continue
        info_code = row["info"]
        accessibility_note = INFO_CODE_MAP.get(info_code, info_code)

        is_recommended_course = "N"
        if row["B"] == "B":
            is_recommended_course = "Y"

        spot = make_spot(
            name=row.get("title", row.get("Title", "")),
            latitude_text=row["lat"],
            longitude_text=row["lon"],
            description=row["Description"],
            accessibility_note=accessibility_note,
            is_recommended_course=is_recommended_course,
            image_file=row["Image"],
            source_file=source["source_file"],
        )
        spots.append(spot)
    return spots


def convert_course_spots(source):
    spots = []
    for row in source["rows"]:
        spot = make_spot(
            name=row["구분"],
            latitude_text=row["위도"],
            longitude_text=row["경도"],
            description=row["설명"],
            accessibility_note=row["상세정보"],
            is_recommended_course=convert_yes_no(row["추천코스여부"]),
            image_file="",
            source_file=source["source_file"],
        )
        spots.append(spot)
    return spots


def convert_spots(source):
    source_format = source["source_format"]
    spots = []
    if source_format == "hub_number_spots":
        spots = convert_hub_number_spots(source)
    elif source_format == "place_name_50_spots":
        spots = convert_place_name_50_spots(source)
    elif source_format == "place_name_20_spots":
        spots = convert_place_name_20_spots(source)
    elif source_format == "course_spots":
        spots = convert_course_spots(source)
    return spots


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


def is_split_spot_row(previous_spot, current_spot):
    """같은 지점이 사진 행 + 설명 행으로 나뉜 경우인지 본다."""
    if previous_spot["name"] == "" or previous_spot["name"] != current_spot["name"]:
        return False
    if previous_spot["latitude"] is None or current_spot["latitude"] is None:
        return False

    distance_meters = calculate_distance_meters(
        previous_spot["latitude"], previous_spot["longitude"],
        current_spot["latitude"], current_spot["longitude"],
    )
    if distance_meters > SPLIT_ROW_MERGE_DISTANCE_METERS:
        return False

    # 한쪽에만 값이 있는 칸끼리만 합친다 (둘 다 값이 다르면 다른 지점으로 본다)
    for column_name in ["description", "image_file"]:
        previous_value = previous_spot[column_name]
        current_value = current_spot[column_name]
        if previous_value != "" and current_value != "" and previous_value != current_value:
            return False
    return True


def merge_split_spot_rows(spots):
    merged_spots = []
    for spot in spots:
        if len(merged_spots) > 0 and is_split_spot_row(merged_spots[-1], spot):
            previous_spot = merged_spots[-1]
            for column_name in ["description", "image_file"]:
                if previous_spot[column_name] == "":
                    previous_spot[column_name] = spot[column_name]
            continue
        merged_spots.append(dict(spot))

    for spot_index, spot in enumerate(merged_spots):
        spot["order"] = spot_index + 1
    return merged_spots


# ---------------------------------------------------------------------------
# 3. 주변시설 변환 (형식별)
# ---------------------------------------------------------------------------

def normalize_facility_type(raw_facility_type, source_file, failures):
    facility_type = raw_facility_type
    if raw_facility_type in FACILITY_TYPE_MAP:
        facility_type = FACILITY_TYPE_MAP[raw_facility_type]
    else:
        failures.append({"source_file": source_file, "step": "facility_type", "reason": "통일 목록에 없는 값: " + raw_facility_type})
    return facility_type


def convert_nearby_info(source, failures):
    nearby_rows = []
    for row in source["rows"]:
        distance_text = row.get("반경(m)", row.get("반경(미터)", ""))
        nearby_row = {
            "place_id": "",
            "facility_type": normalize_facility_type(row["주변시설종류"], source["source_file"], failures),
            "name": row["명칭"],
            "distance_m": distance_text,
            "address": row["주소"],
            "phone": row["연락처"],
            "access_note": row["이용정보"],
            "wheelchair_entry": UNKNOWN,
            "accessible_toilet": UNKNOWN,
            "source_file": source["source_file"],
        }
        nearby_rows.append(nearby_row)
    return nearby_rows


def convert_marker_to_yes(text):
    value = UNKNOWN
    if text == "●":
        value = "Y"
    return value


def convert_nearby_etc(source, failures):
    nearby_rows = []
    for row in source["rows"]:
        distance_meters = ""
        distance_kilometers = parse_float(row["거리(km)"])
        if distance_kilometers is not None:
            distance_meters = round(distance_kilometers * 1000)

        nearby_row = {
            "place_id": "",
            "facility_type": normalize_facility_type(row["구분"], source["source_file"], failures),
            "name": row["명칭"],
            "distance_m": distance_meters,
            "address": row["주소"],
            "phone": row["전화번호"],
            "access_note": "",
            "wheelchair_entry": convert_marker_to_yes(row["휠체어 진입"]),
            "accessible_toilet": convert_marker_to_yes(row["장애인 화장실"]),
            "source_file": source["source_file"],
        }
        nearby_rows.append(nearby_row)
    return nearby_rows


def convert_nearby(source, failures):
    nearby_rows = []
    if source["source_format"] == "nearby_info":
        nearby_rows = convert_nearby_info(source, failures)
    elif source["source_format"] == "nearby_etc":
        nearby_rows = convert_nearby_etc(source, failures)
    return nearby_rows


# ---------------------------------------------------------------------------
# 4. 장소 후보와 중복 후보
# ---------------------------------------------------------------------------

def normalize_place_name(name):
    """띄어쓰기, 특수문자, 괄호를 지운다. 예: '세계자동차＆피아노박물관' -> '세계자동차피아노박물관'"""
    normalized_name = re.sub(r"[^0-9A-Za-z가-힣]", "", name)
    return normalized_name


def choose_representative_spot(spots):
    """출발 지점(이름·설명에 '출발', 이름에 '[시작]', 이름이 '시작'으로 시작), 없으면 좌표가 있는 첫 번째 지점"""
    spots_with_coordinates = []
    for spot in spots:
        if spot["latitude"] is not None and spot["longitude"] is not None:
            spots_with_coordinates.append(spot)

    if len(spots_with_coordinates) == 0:
        return None

    representative_spot = spots_with_coordinates[0]
    for spot in spots_with_coordinates:
        is_start_spot = False
        if "출발" in spot["name"] or "출발" in spot["description"]:
            is_start_spot = True
        if "[시작]" in spot["name"] or spot["name"].startswith("시작"):
            is_start_spot = True
        if is_start_spot:
            representative_spot = spot
            break
    return representative_spot


def find_duplicate_candidates(spot_sources, representative_spots):
    candidates = []
    for index_a in range(len(spot_sources)):
        for index_b in range(index_a + 1, len(spot_sources)):
            source_a = spot_sources[index_a]
            source_b = spot_sources[index_b]
            name_a = normalize_place_name(source_a["place_name"])
            name_b = normalize_place_name(source_b["place_name"])

            reasons = []
            if name_a == name_b:
                reasons.append("same_name")
            elif len(name_a) >= 2 and len(name_b) >= 2 and (name_a in name_b or name_b in name_a):
                reasons.append("name_contains")

            distance_text = ""
            spot_a = representative_spots[source_a["source_file"]]
            spot_b = representative_spots[source_b["source_file"]]
            if spot_a is not None and spot_b is not None:
                distance_meters = calculate_distance_meters(
                    spot_a["latitude"], spot_a["longitude"], spot_b["latitude"], spot_b["longitude"],
                )
                distance_text = round(distance_meters)
                if distance_meters <= DUPLICATE_NEAR_DISTANCE_METERS:
                    reasons.append("near")

            if len(reasons) == 0:
                continue

            candidate = {
                "source_file_a": source_a["source_file"],
                "place_name_a": source_a["place_name"],
                "source_file_b": source_b["source_file"],
                "place_name_b": source_b["place_name"],
                "reasons": ";".join(reasons),
                "distance_m": distance_text,
            }
            candidates.append(candidate)
    return candidates


def sort_spot_sources_by_priority(spot_sources):
    def priority_key(source):
        format_rank = SPOT_FORMAT_PRIORITY.index(source["source_format"])
        folder_number_text = re.sub(r"\D", "", source["folder"])
        folder_number = int(folder_number_text)
        return (format_rank, folder_number)

    sorted_sources = sorted(spot_sources, key=priority_key)
    return sorted_sources


def find_most_similar_place(place_name, place_names_by_id):
    """정규화 이름이 같은 장소, 없으면 가장 비슷한 장소(임계값 이상)를 찾는다."""
    normalized_name = normalize_place_name(place_name)
    best_place_id = ""
    best_ratio = 0.0
    for place_id, candidate_name in place_names_by_id.items():
        normalized_candidate = normalize_place_name(candidate_name)
        if normalized_candidate == normalized_name:
            return place_id, "auto_exact"
        ratio = difflib.SequenceMatcher(None, normalized_name, normalized_candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_place_id = place_id

    if best_ratio >= NEARBY_NAME_SIMILARITY_THRESHOLD:
        return best_place_id, "auto_similar"
    return "", "unmatched"


def create_draft_place_id_map(spot_sources, nearby_sources):
    """정규화 이름이 같은 지점 원본은 한 장소로 묶고, 주변시설은 이름으로 장소에 붙인 초안을 만든다."""
    map_rows = []
    place_id_by_normalized_name = {}
    place_names_by_id = {}

    place_id_by_folder = {}
    sorted_spot_sources = sort_spot_sources_by_priority(spot_sources)
    for source in sorted_spot_sources:
        normalized_name = normalize_place_name(source["place_name"])
        match_status = "auto_exact"
        if normalized_name not in place_id_by_normalized_name:
            new_place_id = "P{:03d}".format(len(place_id_by_normalized_name) + 1)
            place_id_by_normalized_name[normalized_name] = new_place_id
            place_names_by_id[new_place_id] = source["place_name"]
            match_status = "new"

        map_row = {
            "source_file": source["source_file"],
            "source_format": source["source_format"],
            "source_place_name": source["place_name"],
            "place_id": place_id_by_normalized_name[normalized_name],
            "match_status": match_status,
        }
        map_rows.append(map_row)
        place_id_by_folder[source["folder"]] = map_row["place_id"]

    folder_by_number = load_portal_number_folders()
    for source in nearby_sources:
        place_id = ""
        match_status = "unmatched"
        portal_number = extract_portal_number(source["source_file"])
        number_folder = folder_by_number.get(portal_number, "")
        if source["source_format"] == "nearby_info" and number_folder in place_id_by_folder:
            place_id = place_id_by_folder[number_folder]
            match_status = "auto_number"
        else:
            place_id, match_status = find_most_similar_place(source["place_name"], place_names_by_id)
        map_row = {
            "source_file": source["source_file"],
            "source_format": source["source_format"],
            "source_place_name": source["place_name"],
            "place_id": place_id,
            "match_status": match_status,
        }
        map_rows.append(map_row)
    return map_rows


def load_place_id_map():
    _, map_rows = read_csv_file(PLACE_ID_MAP_FILE, "utf-8")
    place_id_by_source_file = {}
    for map_row in map_rows:
        place_id_by_source_file[map_row["source_file"]] = map_row["place_id"]
    return place_id_by_source_file


def load_excluded_source_files():
    """대응표에서 match_status가 excluded인 원본 (다른 원본과 중복이라 넣지 않음). place_id는 중복 대상 장소"""
    _, map_rows = read_csv_file(PLACE_ID_MAP_FILE, "utf-8")
    excluded_source_files = set()
    for map_row in map_rows:
        if map_row["match_status"] == "excluded":
            excluded_source_files.add(map_row["source_file"])
    return excluded_source_files


# ---------------------------------------------------------------------------
# 5. places 집계
# ---------------------------------------------------------------------------

def classify_category(place_name):
    category = "기타"
    for category_name, keywords in CATEGORY_KEYWORDS:
        for keyword in keywords:
            if keyword in place_name:
                return category_name
    return category


def aggregate_flag_from_spots(spots, keyword):
    """지점 중 하나라도 keyword가 있으면 Y, 없으면 unknown"""
    flag = UNKNOWN
    for spot in spots:
        if keyword in spot["accessibility_note"]:
            flag = "Y"
            break
    return flag


def extract_place_name_token(file_name):
    """'제주특별자치도_<장소명>_무장애여행...' 파일 이름에서 공백을 지운 장소명을 꺼낸다. 형식이 다르면 None"""
    place_name_token = None
    name_match = re.match(r"제주특별자치도_(.+?)_무장애여행", file_name)
    if name_match is not None:
        place_name_token = re.sub(r"\s+", "", name_match.group(1))
    return place_name_token


def load_place_id_by_name_token():
    """대응표의 원본 파일 이름 속 장소명 -> place_id"""
    _, map_rows = read_csv_file(PLACE_ID_MAP_FILE, "utf-8")
    place_id_by_name_token = {}
    for map_row in map_rows:
        file_name = Path(map_row["source_file"]).name
        place_name_token = extract_place_name_token(file_name)
        if place_name_token is None:
            continue
        if place_name_token in place_id_by_name_token:
            continue
        place_id_by_name_token[place_name_token] = map_row["place_id"]
    return place_id_by_name_token


def read_closed_days_raw_text(json_file):
    """kordoc 변환 json의 첫 표에서 '휴무일' 행의 값을 읽는다. 없으면 None"""
    raw_text = None
    with open(json_file, encoding="utf-8") as opened_file:
        document = json.load(opened_file)

    for block in document["blocks"]:
        if block["type"] != "table":
            continue
        for row in block["table"]["cells"]:
            label = re.sub(r"\s+", "", row[0]["text"])
            if label == "휴무일":
                raw_text = row[1]["text"]
        break
    return raw_text


def load_hwp_closed_days_by_place_id(failures):
    """일반정보 HWP 변환 결과에서 place_id별 closed_days 정리 값을 만든다"""
    place_id_by_name_token = load_place_id_by_name_token()
    closed_days_by_place_id = {}

    for json_file in sorted(HWP_GENERAL_INFO_DIRECTORY.glob("*.json")):
        source_file = relative_path(json_file)
        data_id = json_file.stem
        hwp_files = sorted((PORTAL_DIRECTORY / f"dgk_{data_id}").glob("*.hwp"))
        if len(hwp_files) == 0:
            failures.append({"source_file": source_file, "step": "closed_days", "reason": "원본 hwp 없음"})
            continue

        place_id = HWP_PLACE_ID_BY_DATA_ID.get(data_id)
        if place_id is None:
            place_name_token = extract_place_name_token(hwp_files[0].name)
            place_id = place_id_by_name_token.get(place_name_token)
        if place_id is None:
            failures.append({"source_file": source_file, "step": "closed_days", "reason": "place_id 없음"})
            continue

        if place_id in CLOSED_DAYS_OVERRIDES_BY_PLACE_ID:
            closed_days_by_place_id[place_id] = CLOSED_DAYS_OVERRIDES_BY_PLACE_ID[place_id]
            continue

        raw_text = read_closed_days_raw_text(json_file)
        if raw_text is None:
            failures.append({"source_file": source_file, "step": "closed_days", "reason": "휴무일 행 없음"})
            continue

        normalized_raw_text = re.sub(r"\s+", "", raw_text)
        closed_days = CLOSED_DAYS_BY_RAW_TEXT.get(normalized_raw_text)
        if closed_days is None:
            failures.append({"source_file": source_file, "step": "closed_days", "reason": "정리 규칙에 없는 휴무일: " + raw_text})
            continue

        closed_days_by_place_id[place_id] = closed_days
    return closed_days_by_place_id


def is_valid_closed_days(closed_days):
    """정리 값이 CLOSED_DAY_PATTERNS 형식만으로 이루어졌는지. 빈칸(정보 없음)도 허용"""
    if closed_days == "":
        return True

    closed_day_items = closed_days.split(";")
    if "연중무휴" in closed_day_items and len(closed_day_items) > 1:
        return False

    for closed_day_item in closed_day_items:
        matched = False
        for pattern in CLOSED_DAY_PATTERNS:
            if re.fullmatch(pattern, closed_day_item):
                matched = True
                break
        if not matched:
            return False
    return True


def load_closed_days_map(failures):
    """사람이 원문을 보고 정리한 휴무일 대응표 (place_id -> closed_days). HWP 일반정보 값보다 우선"""
    closed_days_by_place_id = {}
    if not CLOSED_DAYS_MAP_FILE.exists():
        return closed_days_by_place_id

    source_file = relative_path(CLOSED_DAYS_MAP_FILE)
    _, map_rows = read_csv_file(CLOSED_DAYS_MAP_FILE, "utf-8")
    for map_row in map_rows:
        closed_days = map_row["closed_days"]
        if not is_valid_closed_days(closed_days):
            failures.append({"source_file": source_file, "step": "closed_days", "reason": map_row["place_id"] + " 형식에 없는 정리 값: " + closed_days})
            continue
        closed_days_by_place_id[map_row["place_id"]] = closed_days
    return closed_days_by_place_id


# ---------------------------------------------------------------------------
# 접근성 원본 표 (HWP 변환 결과 -> place_accessibility_raw)
# ---------------------------------------------------------------------------

def remove_spaces(text):
    return re.sub(r"\s+", "", text)


def list_origin_cells(cells):
    """kordoc 표 격자에서 합쳐진 칸에 덮인 자리를 빼고, 행마다 (열 번호, 글자, 행 개수)를 남긴다"""
    covered_positions = set()
    origin_rows = []
    for row_index, row in enumerate(cells):
        origin_row = []
        for column_index, cell in enumerate(row):
            if (row_index, column_index) in covered_positions:
                continue
            for row_offset in range(cell["rowSpan"]):
                for column_offset in range(cell["colSpan"]):
                    if row_offset == 0 and column_offset == 0:
                        continue
                    covered_positions.add((row_index + row_offset, column_index + column_offset))
            origin_cell = (column_index, cell["text"].strip(), cell["rowSpan"])
            origin_row.append(origin_cell)
        origin_rows.append(origin_row)
    return origin_rows


def read_cell_text_at(origin_row, column_index):
    """그 열에서 시작하는 칸의 글자. 없으면 빈칸"""
    cell_text = ""
    for origin_column, text, _ in origin_row:
        if origin_column == column_index:
            cell_text = text
    return cell_text


def is_image_row(origin_row):
    for _, text, _ in origin_row:
        if text.startswith("![image]"):
            return True
    return False


def make_accessibility_row(place_id, section, facility_name, description, raw_value, source_file):
    return {
        "place_id": place_id,
        "section": section,
        "facility_name": facility_name,
        "description": description,
        "raw_value": raw_value,
        "source_file": source_file,
    }


def split_table_title(title):
    """'주차장 1 – 리사무소' -> (section, '주차장 1 – 리사무소'의 시설명). 상세 표가 아니면 (None, '')"""
    normalized_title = remove_spaces(title)
    section = None
    for title_prefix, section_name in ACCESSIBILITY_DETAIL_SECTIONS:
        if normalized_title.startswith(title_prefix):
            section = section_name
    if section is None or "사진" in normalized_title:
        return None, ""

    facility_name = ""
    title_parts = re.split(r"\s+[–-]\s+|–", title, maxsplit=1)
    if len(title_parts) == 2:
        facility_name = title_parts[1].strip()
    number_match = re.search(r"(\d+)", title_parts[0])
    if number_match is not None and facility_name != "":
        facility_name = number_match.group(1) + ". " + facility_name
    return section, facility_name


def extract_basic_info_rows(origin_rows, place_id, source_file):
    """기본정보 표: 주차장·화장실 3행 묶음(소제목 행, 값 행, 비고 행)과 보조기기 대여"""
    rows = []
    for row_index, origin_row in enumerate(origin_rows):
        if len(origin_row) == 0 or origin_row[0][0] != 0:
            continue
        block_label = remove_spaces(origin_row[0][1])

        if block_label.startswith("보조기기") and len(origin_row) > 1:
            rows.append(make_accessibility_row(place_id, "기본정보", "", "보조기기 대여", origin_row[1][1], source_file))
            continue
        if block_label not in ("주차장", "화장실"):
            continue

        header_row = origin_row[1:]
        value_row = origin_rows[row_index + 1]
        for header_column, header_text, _ in header_row:
            description = header_text
            if block_label not in remove_spaces(header_text):
                description = block_label + " " + header_text
            raw_value = read_cell_text_at(value_row, header_column)
            rows.append(make_accessibility_row(place_id, "기본정보", "", description, raw_value, source_file))

        note_row = origin_rows[row_index + 2]
        if len(note_row) >= 2:
            note_label = note_row[0][1]
            description = note_label
            if block_label not in remove_spaces(note_label):
                description = block_label + " " + note_label
            rows.append(make_accessibility_row(place_id, "기본정보", "", description, note_row[1][1], source_file))
    return rows


def extract_recommendation_rows(origin_rows, place_id, source_file):
    """추천 대상 표: 유형(없으면 구분)별 한 줄, 값은 추천여부 + 비고"""
    rows = []
    header_columns = {}
    for header_column, header_text, _ in origin_rows[1]:
        header_columns[remove_spaces(header_text)] = header_column

    current_group = ""
    for origin_row in origin_rows[2:]:
        group = read_cell_text_at(origin_row, header_columns["구분"])
        if group != "":
            current_group = group
        target_type = read_cell_text_at(origin_row, header_columns["유형"])
        recommendation = read_cell_text_at(origin_row, header_columns["추천여부"])
        note = read_cell_text_at(origin_row, header_columns["비고"])

        description = target_type
        if description == "":
            description = current_group
        raw_value = recommendation
        if note != "":
            raw_value = (raw_value + " " + note).strip()
        rows.append(make_accessibility_row(place_id, "추천 대상", "", description, raw_value, source_file))
    return rows


def extract_detail_rows(origin_rows, section, facility_name, place_id, source_file):
    """주차장·장애인 화장실·주출입구 표: 구분+항목 -> 내용+단위. 사진 행부터는 읽지 않는다"""
    rows = []
    header_columns = {}
    for header_column, header_text, _ in origin_rows[1]:
        normalized_header = remove_spaces(header_text)
        if normalized_header != "":
            header_columns[normalized_header] = header_column

    current_group = ""
    for origin_row in origin_rows[2:]:
        if is_image_row(origin_row):
            break
        group = read_cell_text_at(origin_row, header_columns["구분"])
        if group != "":
            current_group = group
        if section == "주차장" and remove_spaces(current_group) in EXCLUDED_PARKING_GROUPS:
            continue

        if remove_spaces(current_group) == "기타":
            other_text = read_cell_text_at(origin_row, header_columns["항목"])
            if other_text != "":
                rows.append(make_accessibility_row(place_id, section, facility_name, "기타", other_text, source_file))
            continue

        item = read_cell_text_at(origin_row, header_columns["항목"])
        content = read_cell_text_at(origin_row, header_columns["내용"])
        unit = ""
        if "단위" in header_columns:
            unit = read_cell_text_at(origin_row, header_columns["단위"])

        description = (current_group + " " + item).strip()
        raw_value = content
        if unit not in ("", "-") and content not in ("", "-"):
            raw_value = content + unit
        rows.append(make_accessibility_row(place_id, section, facility_name, description, raw_value, source_file))
    return rows


def extract_general_info_accessibility_rows(json_file, place_id):
    """일반정보 HWP 변환 json -> 접근성 원본 줄 목록"""
    source_file = relative_path(json_file)
    with open(json_file, encoding="utf-8") as opened_file:
        document = json.load(opened_file)

    tables = []
    for block in document["blocks"]:
        if block["type"] == "table":
            tables.append(list_origin_cells(block["table"]["cells"]))

    rows = extract_basic_info_rows(tables[0], place_id, source_file)
    for origin_rows in tables[1:]:
        title = origin_rows[0][0][1]
        section, facility_name = split_table_title(title)
        if section is None:
            continue
        if section == "추천 대상":
            rows.extend(extract_recommendation_rows(origin_rows, place_id, source_file))
        else:
            rows.extend(extract_detail_rows(origin_rows, section, facility_name, place_id, source_file))
    return rows


def read_hub_label(text):
    """'보조기기', '편의장비', '승강기', '주차장' 중 하나로 시작하면 그 이름"""
    normalized_text = remove_spaces(text)
    for label in HUB_ACCESSIBILITY_LABELS + [HUB_PARKING_LABEL]:
        if normalized_text.startswith(label):
            return label
    return None


def keep_hub_value(label, raw_value):
    if raw_value == "":
        return False
    if label == HUB_PARKING_LABEL and "장애인" not in raw_value:
        return False
    return True


def extract_hub_info_accessibility_rows(json_file, place_id):
    """허브 HWP 변환 json -> '이동약자를 위한 안내' 문단 + 이용안내의 보조기기·편의장비·승강기(·장애인 주차) 줄"""
    source_file = relative_path(json_file)
    with open(json_file, encoding="utf-8") as opened_file:
        document = json.load(opened_file)

    rows = []
    in_mobility_guide = False
    mobility_guide_found = False
    for block in document["blocks"]:
        # 개요의 '이동약자를 위한 안내'는 문단이 아닌 블록(제목·표·사진)이나 '이용안내'가 나오면 끝난다
        if block["type"] != "paragraph":
            in_mobility_guide = False

        if block["type"] == "paragraph":
            text = block.get("text", "").strip()
            normalized_text = remove_spaces(text)
            if normalized_text.startswith("이동약자를위한안내") and not mobility_guide_found:
                mobility_guide_found = True
                in_mobility_guide = True
                inline_text = re.sub(r"^\s*이동약자\s*를\s*위한\s*안내\s*:?", "", text).strip()
                if inline_text != "":
                    rows.append(make_accessibility_row(place_id, "이용안내", "", "이동약자 안내", inline_text, source_file))
                continue
            if normalized_text == "이용안내":
                in_mobility_guide = False
                continue
            if in_mobility_guide and text != "":
                rows.append(make_accessibility_row(place_id, "이용안내", "", "이동약자 안내", text, source_file))
                continue

            paragraph_match = re.match(r"^\s*(보조기기|편의장비|승강기|주\s*차\s*장)\s*:\s*(.*)$", text)
            if paragraph_match is not None:
                label = read_hub_label(paragraph_match.group(1))
                raw_value = paragraph_match.group(2).strip()
                if keep_hub_value(label, raw_value):
                    rows.append(make_accessibility_row(place_id, "이용안내", "", label, raw_value, source_file))
            continue

        if block["type"] != "table":
            continue
        current_label = None
        for origin_row in list_origin_cells(block["table"]["cells"]):
            if len(origin_row) == 0:
                continue
            first_column, first_text, _ = origin_row[0]
            value_cells = origin_row
            if first_column == 0:
                current_label = read_hub_label(first_text)
                value_cells = origin_row[1:]
            if current_label is None:
                continue

            value_texts = []
            for _, text, _ in value_cells:
                if text != "":
                    value_texts.append(text)
            raw_value = " ".join(value_texts)
            if keep_hub_value(current_label, raw_value):
                rows.append(make_accessibility_row(place_id, "이용안내", "", current_label, raw_value, source_file))
    return rows


def load_place_id_by_hub_folder():
    """대응표의 허브 폴더 이름(hub_N) -> place_id"""
    _, map_rows = read_csv_file(PLACE_ID_MAP_FILE, "utf-8")
    place_id_by_hub_folder = {}
    for map_row in map_rows:
        folder_match = re.search(r"jeju_datahub/(hub_\d+)/", map_row["source_file"])
        if folder_match is None:
            continue
        place_id_by_hub_folder[folder_match.group(1)] = map_row["place_id"]
    return place_id_by_hub_folder


def build_accessibility_raw_rows(failures):
    """일반정보 HWP 49곳 + 허브 HWP 20곳의 접근성 원본 줄"""
    accessibility_rows = []

    place_id_by_name_token = load_place_id_by_name_token()
    for json_file in sorted(HWP_GENERAL_INFO_DIRECTORY.glob("*.json")):
        data_id = json_file.stem
        place_id = HWP_PLACE_ID_BY_DATA_ID.get(data_id)
        if place_id is None:
            hwp_files = sorted((PORTAL_DIRECTORY / f"dgk_{data_id}").glob("*.hwp"))
            if len(hwp_files) > 0:
                place_name_token = extract_place_name_token(hwp_files[0].name)
                place_id = place_id_by_name_token.get(place_name_token)
        if place_id is None:
            failures.append({"source_file": relative_path(json_file), "step": "accessibility", "reason": "place_id 없음"})
            continue
        accessibility_rows.extend(extract_general_info_accessibility_rows(json_file, place_id))

    place_id_by_hub_folder = load_place_id_by_hub_folder()
    for json_file in sorted(HWP_HUB_INFO_DIRECTORY.glob("*.json")):
        place_id = place_id_by_hub_folder.get(json_file.stem)
        if place_id is None:
            failures.append({"source_file": relative_path(json_file), "step": "accessibility", "reason": "place_id 없음"})
            continue
        accessibility_rows.extend(extract_hub_info_accessibility_rows(json_file, place_id))

    accessibility_rows.sort(key=lambda row: row["place_id"])
    return accessibility_rows


def has_positive_count(raw_value):
    """'5대', '4면', '3' 처럼 1 이상의 숫자가 있으면 True"""
    number_match = re.search(r"\d+", raw_value)
    if number_match is None:
        return False
    return int(number_match.group(0)) > 0


def decide_accessible_parking_by_place_id(accessibility_rows):
    """접근성 원본 표 -> place_id별 accessible_parking (Y / N). 근거가 없는 장소는 빠진다

    - 일반정보 HWP: 장애인 주차면수가 1 이상인 주차장이 있거나 '장애인 주차장 유무'가 있음·유면 Y,
      유무가 없음·무이거나 주차면수가 모두 없음이면 N
    - 허브 HWP: 이용안내 주차장 문장에 '장애인 주차구역 없음' 류 표현이 있으면 N, 장애인 주차 언급이 있으면 Y
    """
    has_parking_by_place_id = {}
    no_parking_by_place_id = {}
    for accessibility_row in accessibility_rows:
        place_id = accessibility_row["place_id"]
        description = remove_spaces(accessibility_row["description"])
        raw_value = accessibility_row["raw_value"].strip()
        normalized_value = remove_spaces(raw_value)

        if description == "장애인주차장유무":
            if normalized_value in ("있음", "유"):
                has_parking_by_place_id[place_id] = True
            if normalized_value in ("없음", "무"):
                no_parking_by_place_id[place_id] = True

        if accessibility_row["section"] == "주차장" and description == "장애인주차구역주차면수":
            if has_positive_count(raw_value):
                has_parking_by_place_id[place_id] = True
            if normalized_value == "없음":
                no_parking_by_place_id[place_id] = True

        if accessibility_row["section"] == "이용안내" and description == "주차장":
            if re.search(r"장애인(전용)?주차(구역|장)(은|이)?없", normalized_value):
                no_parking_by_place_id[place_id] = True
            elif "장애인" in normalized_value and "주차" in normalized_value:
                # 예: '장애인, 임산부 주차구역이 1개씩 있으며'
                has_parking_by_place_id[place_id] = True

    accessible_parking_by_place_id = {}
    for place_id in no_parking_by_place_id:
        accessible_parking_by_place_id[place_id] = "N"
    for place_id in has_parking_by_place_id:
        accessible_parking_by_place_id[place_id] = "Y"
    return accessible_parking_by_place_id


def build_places_rows(place_ids, sources_by_place_id, spots_by_place_id, representative_spots, closed_days_by_place_id,
                      accessible_parking_by_place_id, conflicts):
    places_rows = []
    for place_id in place_ids:
        place_sources = sources_by_place_id[place_id]
        spot_place_sources = []
        for source in place_sources:
            if source["source_format"] in SPOT_FORMAT_PRIORITY:
                spot_place_sources.append(source)
        spot_place_sources = sort_spot_sources_by_priority(spot_place_sources)

        main_source = place_sources[0]
        if len(spot_place_sources) > 0:
            main_source = spot_place_sources[0]

        latitude = ""
        longitude = ""
        main_spot = representative_spots.get(main_source["source_file"])
        if main_spot is not None:
            latitude = main_spot["latitude"]
            longitude = main_spot["longitude"]

        record_place_conflicts(place_id, main_source, spot_place_sources, representative_spots, conflicts)

        source_files = []
        data_date = ""
        for source in place_sources:
            source_files.append(source["source_file"])
            if source["data_date"] > data_date:
                data_date = source["data_date"]

        place_spots = spots_by_place_id.get(place_id, [])
        place_row = {
            "place_id": place_id,
            "name": main_source["place_name"],
            "category": classify_category(main_source["place_name"]),
            "region": "",
            "address": "",
            "latitude": latitude,
            "longitude": longitude,
            "wheelchair_level": UNKNOWN,
            "stroller_level": UNKNOWN,
            "has_slope_or_stairs": aggregate_flag_from_spots(place_spots, "접근성"),
            "accessible_parking": accessible_parking_by_place_id.get(place_id, UNKNOWN),
            "accessible_toilet": aggregate_flag_from_spots(place_spots, "장애인화장실"),
            "wheelchair_rental": UNKNOWN,
            "stroller_rental": UNKNOWN,
            "indoor_outdoor": UNKNOWN,
            "visit_minutes": DEFAULT_VISIT_MINUTES,
            "opening_hours": "",
            "closed_days": closed_days_by_place_id.get(place_id, ""),
            "admission_fee": "",
            "sources": ";".join(source_files),
            "data_date": data_date,
        }
        places_rows.append(place_row)
    return places_rows


def record_place_conflicts(place_id, main_source, spot_place_sources, representative_spots, conflicts):
    main_spot = representative_spots.get(main_source["source_file"])
    for source in spot_place_sources:
        if source is main_source:
            continue
        if source["place_name"] != main_source["place_name"]:
            conflicts.append({
                "place_id": place_id, "column": "name",
                "chosen_value": main_source["place_name"], "chosen_source": main_source["source_file"],
                "other_value": source["place_name"], "other_source": source["source_file"],
            })
        other_spot = representative_spots.get(source["source_file"])
        if main_spot is None or other_spot is None:
            continue
        distance_meters = calculate_distance_meters(
            main_spot["latitude"], main_spot["longitude"], other_spot["latitude"], other_spot["longitude"],
        )
        if distance_meters > DUPLICATE_NEAR_DISTANCE_METERS:
            conflicts.append({
                "place_id": place_id, "column": "latitude,longitude",
                "chosen_value": "{},{}".format(main_spot["latitude"], main_spot["longitude"]),
                "chosen_source": main_source["source_file"],
                "other_value": "{},{} ({}m)".format(other_spot["latitude"], other_spot["longitude"], round(distance_meters)),
                "other_source": source["source_file"],
            })


# ---------------------------------------------------------------------------
# 6. 검증
# ---------------------------------------------------------------------------

def verify_outputs(sources, output_counts, excluded_source_files, places_rows, spot_rows, nearby_rows, failures):
    row_count_rows = []
    for source in sources:
        source_file = source["source_file"]
        if source["source_format"] == "hub_number_track":
            continue
        output_count = output_counts.get(source_file, 0)
        difference = source["row_count"] - output_count
        reason = ""
        if difference > 0 and source["source_format"] in SPOT_FORMAT_PRIORITY:
            reason = "사진 행·설명 행 합침"
        if difference > 0 and source["source_format"] == "place_name_20_spots":
            reason = "범례만 있는 행 제외"
        if source_file not in output_counts:
            reason = "place_id 없음 (대응표 누락 또는 미연결)"
        if source_file in excluded_source_files:
            reason = "다른 원본과 중복이라 제외 (대응표 excluded)"
        row_count_rows.append({
            "source_file": source_file,
            "source_format": source["source_format"],
            "raw_rows": source["row_count"],
            "output_rows": output_count,
            "difference": difference,
            "reason": reason,
        })
    columns = ["source_file", "source_format", "raw_rows", "output_rows", "difference", "reason"]
    save_csv_file(ROW_COUNT_LOG, columns, row_count_rows)

    place_id_set = set()
    for place_row in places_rows:
        place_id_set.add(place_row["place_id"])

    missing_reference_count = 0
    for row in spot_rows + nearby_rows:
        if row["place_id"] not in place_id_set:
            missing_reference_count += 1
            failures.append({"source_file": row["source_file"], "step": "verify", "reason": "places에 없는 place_id: " + row["place_id"]})

    print("[검증] places {}행, place_spots {}행, place_nearby {}행".format(len(places_rows), len(spot_rows), len(nearby_rows)))
    print("[검증] places에 없는 place_id 참조: {}건".format(missing_reference_count))


def is_outside_jeju(latitude, longitude):
    if latitude is None or longitude is None:
        return False
    if latitude < JEJU_LATITUDE_RANGE[0] or latitude > JEJU_LATITUDE_RANGE[1]:
        return True
    if longitude < JEJU_LONGITUDE_RANGE[0] or longitude > JEJU_LONGITUDE_RANGE[1]:
        return True
    return False


def find_anomalies(spot_rows, nearby_rows):
    """값은 고치지 않고 이상값만 기록한다."""
    anomalies = []

    for spot in spot_rows:
        if is_outside_jeju(spot["latitude"], spot["longitude"]):
            anomalies.append({
                "table": "place_spots", "place_id": spot["place_id"], "name": spot["name"],
                "check": "제주 범위 밖 좌표", "value": "{},{}".format(spot["latitude"], spot["longitude"]),
                "source_file": spot["source_file"],
            })

    seen_spot_keys = set()
    for spot in spot_rows:
        spot_key = (spot["place_id"], spot["name"], spot["latitude"], spot["longitude"], spot["description"])
        if spot_key in seen_spot_keys:
            anomalies.append({
                "table": "place_spots", "place_id": spot["place_id"], "name": spot["name"],
                "check": "완전히 같은 지점 행", "value": "{},{}".format(spot["latitude"], spot["longitude"]),
                "source_file": spot["source_file"],
            })
        seen_spot_keys.add(spot_key)

    for nearby_row in nearby_rows:
        distance_value = parse_float(str(nearby_row["distance_m"]))
        if distance_value is None:
            continue
        if distance_value > NEARBY_DISTANCE_LIMIT_METERS:
            anomalies.append({
                "table": "place_nearby", "place_id": nearby_row["place_id"], "name": nearby_row["name"],
                "check": "주변시설 거리 10km 초과", "value": nearby_row["distance_m"],
                "source_file": nearby_row["source_file"],
            })

    columns = ["table", "place_id", "name", "check", "value", "source_file"]
    save_csv_file(ANOMALIES_LOG, columns, anomalies)
    print("[이상값] {}건 -> {}".format(len(anomalies), relative_path(ANOMALIES_LOG)))
    return anomalies


# ---------------------------------------------------------------------------
# 7. SQLite 적재
# ---------------------------------------------------------------------------

def load_csv_to_sqlite(connection, table_name, csv_path):
    header, rows = read_csv_file(csv_path, "utf-8")

    column_definitions = []
    for column_name in header:
        column_type = COLUMN_TYPES.get(column_name, "TEXT")
        column_definitions.append('"{}" {}'.format(column_name, column_type))

    quoted_columns = []
    for column_name in header:
        quoted_columns.append('"{}"'.format(column_name))
    placeholders = ",".join(["?"] * len(header))

    connection.execute('DROP TABLE IF EXISTS "{}"'.format(table_name))
    connection.execute('CREATE TABLE "{}" ({})'.format(table_name, ", ".join(column_definitions)))

    insert_sql = 'INSERT INTO "{}" ({}) VALUES ({})'.format(table_name, ",".join(quoted_columns), placeholders)
    for row in rows:
        values = []
        for column_name in header:
            value = row[column_name]
            if value == "":
                value = None
            values.append(value)
        connection.execute(insert_sql, values)


def build_sqlite_database():
    DATABASE_FILE.unlink(missing_ok=True)
    connection = sqlite3.connect(DATABASE_FILE)
    load_csv_to_sqlite(connection, "places", PLACES_FILE)
    load_csv_to_sqlite(connection, "place_spots", SPOTS_FILE)
    load_csv_to_sqlite(connection, "place_nearby", NEARBY_FILE)
    load_csv_to_sqlite(connection, "place_accessibility_raw", ACCESSIBILITY_RAW_FILE)
    connection.execute('CREATE INDEX idx_place_spots_place_id ON place_spots(place_id)')
    connection.execute('CREATE INDEX idx_place_nearby_place_id ON place_nearby(place_id)')
    connection.execute('CREATE INDEX idx_place_accessibility_raw_place_id ON place_accessibility_raw(place_id)')
    connection.commit()

    cursor = connection.execute("SELECT COUNT(*) FROM places WHERE accessible_toilet = 'Y'")
    toilet_place_count = cursor.fetchone()[0]
    print("[SQLite] accessible_toilet = 'Y' 장소: {}곳".format(toilet_place_count))
    connection.close()


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def main():
    failures = []
    conflicts = []

    # 1. 원본 목록
    sources = list_source_files(failures)
    save_source_list(sources)
    print("[원본] CSV {}개 분류".format(len(sources)))

    spot_sources = []
    nearby_sources = []
    for source in sources:
        if source["source_format"] in SPOT_FORMAT_PRIORITY:
            spot_sources.append(source)
        elif source["source_format"] in NEARBY_FORMATS:
            nearby_sources.append(source)

    # 2. 지점 변환, 3. 주변시설 변환
    spots_by_source_file = {}
    representative_spots = {}
    for source in spot_sources:
        converted_spots = convert_spots(source)
        merged_spots = merge_split_spot_rows(converted_spots)
        spots_by_source_file[source["source_file"]] = merged_spots
        representative_spots[source["source_file"]] = choose_representative_spot(merged_spots)

    nearby_by_source_file = {}
    for source in nearby_sources:
        nearby_by_source_file[source["source_file"]] = convert_nearby(source, failures)

    # 4. 중복 후보
    candidates = find_duplicate_candidates(spot_sources, representative_spots)
    candidate_columns = ["source_file_a", "place_name_a", "source_file_b", "place_name_b", "reasons", "distance_m"]
    save_csv_file(DUPLICATE_CANDIDATES_LOG, candidate_columns, candidates)
    print("[중복] 후보 {}쌍 -> {}".format(len(candidates), relative_path(DUPLICATE_CANDIDATES_LOG)))

    # 5. 대응표 (없으면 초안 생성, 있으면 사람이 확정한 것을 그대로 사용)
    if not PLACE_ID_MAP_FILE.exists():
        draft_map_rows = create_draft_place_id_map(spot_sources, nearby_sources)
        save_csv_file(PLACE_ID_MAP_FILE, PLACE_ID_MAP_COLUMNS, draft_map_rows)
        print("[대응표] 초안 생성 -> {} (확인 후 match_status를 confirmed로 수정)".format(relative_path(PLACE_ID_MAP_FILE)))
    place_id_by_source_file = load_place_id_map()
    excluded_source_files = load_excluded_source_files()

    # 6. place_id 부여
    sources_by_place_id = {}
    spots_by_place_id = {}
    spot_rows = []
    nearby_rows = []
    output_counts = {}
    for source in spot_sources + nearby_sources:
        source_file = source["source_file"]
        if source_file in excluded_source_files:
            continue
        place_id = place_id_by_source_file.get(source_file, "")
        if place_id == "":
            failures.append({"source_file": source_file, "step": "place_id", "reason": "대응표에 place_id 없음"})
            continue

        if place_id not in sources_by_place_id:
            sources_by_place_id[place_id] = []
        sources_by_place_id[place_id].append(source)

        converted_rows = spots_by_source_file.get(source_file)
        target_rows = spot_rows
        if converted_rows is None:
            converted_rows = nearby_by_source_file[source_file]
            target_rows = nearby_rows
        else:
            if place_id not in spots_by_place_id:
                spots_by_place_id[place_id] = []
            spots_by_place_id[place_id].extend(converted_rows)

        for row in converted_rows:
            row["place_id"] = place_id
            target_rows.append(row)
        output_counts[source_file] = len(converted_rows)

    # 장소는 지점 원본이 있는 place_id만 만든다 (주변시설은 기존 장소에 붙음)
    place_ids = sorted(spots_by_place_id.keys())

    # 7. 접근성 원본 표 (HWP 변환 결과)
    accessibility_rows = build_accessibility_raw_rows(failures)
    save_csv_file(ACCESSIBILITY_RAW_FILE, ACCESSIBILITY_RAW_COLUMNS, accessibility_rows)
    accessibility_place_ids = set()
    for accessibility_row in accessibility_rows:
        accessibility_place_ids.add(accessibility_row["place_id"])
    print("[접근성 원본] {}줄, {}곳 -> {}".format(
        len(accessibility_rows), len(accessibility_place_ids), relative_path(ACCESSIBILITY_RAW_FILE)
    ))
    accessible_parking_by_place_id = decide_accessible_parking_by_place_id(accessibility_rows)

    # 7-1. places 집계 (휴무일: 일반정보 HWP 변환 결과, 그 위에 사람이 정리한 대응표 / 장애인 주차: 접근성 원본 표)
    closed_days_by_place_id = load_hwp_closed_days_by_place_id(failures)
    closed_days_map = load_closed_days_map(failures)
    for place_id, closed_days in closed_days_map.items():
        closed_days_by_place_id[place_id] = closed_days
    print("[휴무일] 일반정보 HWP + 대응표 {}곳".format(len(closed_days_by_place_id)))
    places_rows = build_places_rows(
        place_ids, sources_by_place_id, spots_by_place_id, representative_spots, closed_days_by_place_id,
        accessible_parking_by_place_id, conflicts
    )

    save_csv_file(PLACES_FILE, PLACES_COLUMNS, places_rows)
    save_csv_file(SPOTS_FILE, SPOTS_COLUMNS, spot_rows)
    save_csv_file(NEARBY_FILE, NEARBY_COLUMNS, nearby_rows)

    # 8. 검증
    verify_outputs(sources, output_counts, excluded_source_files, places_rows, spot_rows, nearby_rows, failures)
    find_anomalies(spot_rows, nearby_rows)

    conflict_columns = ["place_id", "column", "chosen_value", "chosen_source", "other_value", "other_source"]
    save_csv_file(CONFLICTS_LOG, conflict_columns, conflicts)
    save_csv_file(FAILURES_LOG, ["source_file", "step", "reason"], failures)
    print("[로그] 실패 {}건, 충돌 {}건 -> {}".format(len(failures), len(conflicts), relative_path(OUTPUT_DIRECTORY)))

    # 9. SQLite 적재
    build_sqlite_database()
    print("[완료] {}".format(relative_path(DATABASE_FILE)))


if __name__ == "__main__":
    main()
