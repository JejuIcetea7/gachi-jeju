"""운영시간 대응표(opening_hours_map.csv)를 만든다.

- 원문 우선순위: 한국관광공사 API 이용시간(1단계·'통과' 후보) > 허브 HWP '관람시간'
- 정리 값 형식 (여러 개는 ';'로 잇는다)
  - '상시 개방'
  - '09:00-18:00' 또는 '09:00-18:00 입장마감 17:00'
  - 계절별: '3-10월 09:00-18:00 입장마감 17:00;11-2월 09:00-17:00 입장마감 16:00'
- 규칙으로 정하는 원문: 시각이 없고 '상시 개방'만 있음 / 시간 하나(+ 입장·매표 마감 하나)이고 계절·시설 구분이 없음
- 그 외는 아래 장소별 표로 사람이 정하고, 표에도 없으면 빈칸 + status '확인필요'

입력: outputs/tour_api_candidates/candidates.csv, data/processed/hwp_hub_info/hub_<번호>.json, data/processed/place_id_map.csv,
     data/processed/places.csv (장소 이름)
결과: data/processed/opening_hours_map.csv
실행: python3 scripts/build_opening_hours_map.py  (그다음 python3 scripts/build_places.py)
"""

import csv
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIRECTORY = PROJECT_ROOT / "data" / "processed"
CANDIDATES_FILE = PROJECT_ROOT / "outputs" / "tour_api_candidates" / "candidates.csv"
HWP_HUB_INFO_DIRECTORY = PROCESSED_DIRECTORY / "hwp_hub_info"
PLACE_ID_MAP_FILE = PROCESSED_DIRECTORY / "place_id_map.csv"
PLACES_FILE = PROCESSED_DIRECTORY / "places.csv"
OPENING_HOURS_MAP_FILE = PROCESSED_DIRECTORY / "opening_hours_map.csv"

OPENING_HOURS_MAP_COLUMNS = ["place_id", "name", "source_file", "raw_text", "opening_hours", "status"]
STATUS_CONFIRMED = "확정"
STATUS_NEEDS_CHECK = "확인필요"
ALWAYS_OPEN = "상시 개방"

TIME_RANGE_PATTERN = r"(\d{1,2}):(\d{2})\s*[~\-–]\s*(\d{1,2}):(\d{2})"
LAST_ENTRY_PATTERN = r"(입장|매표)\s*마감\s*:?\s*(\d{1,2}):(\d{2})"
# 이 말이 있으면 계절·시설·점포별로 시간이 달라 규칙으로 정하지 않는다
MANUAL_KEYWORDS = ["월", "하절기", "동절기", "하계", "동계", "점포", "공연", "숙박", "["]

# 규칙으로 정할 수 없는 원문의 정리 값 (사람이 원문을 보고 정함, 빈칸은 확인필요)
# 시설이 여럿이면 장소 입장 기준이 되는 대표 시설 하나, 계절 달이 원문에 없으면 더 이른 마감을 쓴다
OPENING_HOURS_BY_PLACE_ID = {
    "P001": "2-5월 09:00-17:00;6-8월 09:00-17:30;9-10월 09:00-17:00;11-1월 09:00-16:30",  # 한림공원
    "P002": "09:00-17:00 입장마감 16:30",  # 더마파크: 카트·승마 기준 (공연 10:30~17:30)
    "P016": "3-10월 08:30-18:30 입장마감 17:30;11-2월 08:30-18:00 입장마감 17:00",  # 원문 하절기 3~11월·동절기 11~2월이 겹쳐 11월은 동절기
    "P017": "09:00-18:00 입장마감 17:30",  # 여미지식물원: 동절기 17시 입장 권장은 원문에만
    "P024": "4-10월 08:00-16:00;11-3월 09:00-16:00",  # 사전 예약 필요는 원문에만
    "P025": "3-9월 09:30-18:00;10-2월 09:30-17:00",  # 제주민속촌
    "P027": "3-9월 09:00-19:00 입장마감 18:00;10-2월 09:00-18:00 입장마감 17:00",  # 카페·식당 시간 제외
    "P032": "08:30-19:30",  # 파크 운영시간 (스카이파크 등 시설별 시간 제외)
    "P039": "3-10월 07:00-18:00;11-2월 07:00-17:00",
    "P042": "3-10월 09:00-18:00 입장마감 17:00;11-2월 09:00-17:00 입장마감 16:00",
    "P043": "4-9월 15:00-23:00 입장마감 21:30;10-3월 14:00-22:00 입장마감 20:30",  # 별빛누리공원: 마감 1시간 30분 전 입장
    "P044": ALWAYS_OPEN,  # 한라수목원: 야외 상시 개방 (실내시설 09:00~18:00/17:00은 원문에만)
    "P048": "3-6월 09:00-18:40 입장마감 18:00;7-8월 09:00-17:40 입장마감 17:00;9-10월 09:00-18:40 입장마감 18:00;11-2월 09:00-17:40 입장마감 17:00",
    "P051": "4-9월 09:00-17:00 입장마감 14:00;10-3월 09:00-17:00 입장마감 12:00",  # 사려니숲길: 입산 통제 시각을 입장마감으로
    "P072": "4-9월 09:00-17:00 입장마감 14:00;10-3월 09:00-17:00 입장마감 12:00",  # 사려니숲길 무장애나눔길
    "P071": "3-10월 08:00-18:00 입장마감 17:00;11-2월 09:00-17:00 입장마감 16:00",  # 숙박·야영 시간 제외
    "P083": "07:00-20:00",  # 서귀포매일올레시장: 하절기 21:00·동절기 20:00, 달이 원문에 없어 이른 마감
    "P095": "7-9월 09:00-19:00 입장마감 18:30;10-6월 09:00-18:00 입장마감 17:30",  # 종료 30분 전 입장 마감
    "P097": "09:00-18:00 입장마감 17:00",  # 휴양림 (숙박 제외)
    "P099": "09:00-18:00",  # 제주현대미술관 본관 (공공수장고 09:15~17:30 제외)
    "P101": "7-9월 09:00-20:00 입장마감 19:30;10-6월 09:00-18:00 입장마감 17:30",
    "P108": "2-11월 09:00-19:00 입장마감 18:00;12-1월 09:00-18:00 입장마감 17:00",
    "P118": "3-10월 09:00-18:00 입장마감 17:30;11-2월 09:00-17:00 입장마감 16:30",
    "P119": "10:00-17:00 입장마감 16:00",  # 입장마감 하계 16:30·동계 16:00, 달이 원문에 없어 이른 마감
    "P120": "3-6월 09:30-18:00 입장마감 17:30;7-8월 09:30-18:30 입장마감 18:00;9-10월 09:30-18:00 입장마감 17:30;11-2월 09:30-17:00 입장마감 16:30",
    "P121": "09:40-15:30",  # 아리랑공연장: 공연 3회(09:40/11:00/14:30)는 원문에만
    "P100": "",  # 동문시장: 점포별로 상이함
    "P133": "",  # 도두봉: 출입 통제 안내만 있음
    # 허브 HWP (API 이용시간이 없는 곳)
    "P111": "10:30-18:00 입장마감 17:00",  # 낚고락고: 주말·동절기 변동은 원문에만
    "P116": "09:00-21:00 입장마감 20:00",  # 수목원테마파크: 아이스뮤지엄 기준 (LED 공원 09:00~22:00 제외)
}


def read_csv_rows(file_path, encoding):
    with open(file_path, encoding=encoding, newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    return rows


def remove_spaces(text):
    return re.sub(r"\s+", "", text)


def is_accepted_candidate(candidate_row):
    accepted = False
    if candidate_row["stage"] == "1" and candidate_row["review"] == "":
        accepted = True
    if candidate_row["review"] == "통과":
        accepted = True
    return accepted


def format_time(hour_text, minute_text):
    return f"{int(hour_text):02d}:{minute_text}"


def decide_opening_hours_by_rule(raw_text):
    """규칙으로 정한 정리 값. 정할 수 없으면 None"""
    time_ranges = re.findall(TIME_RANGE_PATTERN, raw_text)
    if len(time_ranges) == 0:
        if ALWAYS_OPEN in raw_text:
            return ALWAYS_OPEN
        return None

    for keyword in MANUAL_KEYWORDS:
        if keyword in raw_text:
            return None
    if len(time_ranges) > 1:
        return None

    open_hour, open_minute, close_hour, close_minute = time_ranges[0]
    opening_hours = format_time(open_hour, open_minute) + "-" + format_time(close_hour, close_minute)

    last_entries = re.findall(LAST_ENTRY_PATTERN, raw_text)
    if len(last_entries) > 1:
        return None
    if len(last_entries) == 1:
        _, entry_hour, entry_minute = last_entries[0]
        opening_hours = opening_hours + " 입장마감 " + format_time(entry_hour, entry_minute)
    return opening_hours


def read_hub_opening_hours_raw_text(json_file):
    """허브 HWP 변환 json의 '관람시간' 표 행 값 또는 '관람시간 :' 문단. 없으면 빈칸"""
    raw_text = ""
    with open(json_file, encoding="utf-8") as opened_file:
        document = json.load(opened_file)

    for block in document["blocks"]:
        if block["type"] == "paragraph":
            paragraph_match = re.match(r"^\W*관\s*람\s*시\s*간\s*:\s*(.*)$", block.get("text", ""), re.DOTALL)
            if paragraph_match is not None:
                raw_text = paragraph_match.group(1).strip()
                break
            continue
        if block["type"] != "table":
            continue
        for row in block["table"]["cells"]:
            if not remove_spaces(row[0]["text"]).startswith("관람시간"):
                continue
            value_texts = []
            for cell in row[1:]:
                if cell["text"] != "":
                    value_texts.append(cell["text"])
            raw_text = " ".join(value_texts)
            break
        if raw_text != "":
            break
    return raw_text


def load_place_id_by_hub_folder():
    place_id_by_hub_folder = {}
    for map_row in read_csv_rows(PLACE_ID_MAP_FILE, "utf-8"):
        folder_match = re.search(r"jeju_datahub/(hub_\d+)/", map_row["source_file"])
        if folder_match is None:
            continue
        place_id_by_hub_folder[folder_match.group(1)] = map_row["place_id"]
    return place_id_by_hub_folder


def make_map_row(place_id, name, source_file, raw_text, unknown_raw_texts):
    cleaned_raw_text = re.sub(r"<br\s*/?>", " ", raw_text)
    cleaned_raw_text = re.sub(r"\s+", " ", cleaned_raw_text).strip()

    opening_hours = None
    if place_id in OPENING_HOURS_BY_PLACE_ID:
        opening_hours = OPENING_HOURS_BY_PLACE_ID[place_id]
    else:
        opening_hours = decide_opening_hours_by_rule(cleaned_raw_text)
    if opening_hours is None:
        unknown_raw_texts.append((place_id, name, cleaned_raw_text))
        opening_hours = ""

    status = STATUS_NEEDS_CHECK
    if opening_hours != "":
        status = STATUS_CONFIRMED
    map_row = {
        "place_id": place_id,
        "name": name,
        "source_file": source_file,
        "raw_text": cleaned_raw_text,
        "opening_hours": opening_hours,
        "status": status,
    }
    return map_row


def main():
    map_rows = []
    covered_place_ids = set()
    unknown_raw_texts = []

    for candidate_row in read_csv_rows(CANDIDATES_FILE, "utf-8-sig"):
        if not is_accepted_candidate(candidate_row):
            continue
        if candidate_row["usetime_raw"].strip() == "":
            continue
        place_id = candidate_row["place_id"]
        source_file = "tour_api:" + candidate_row["content_id"]
        map_row = make_map_row(place_id, candidate_row["name"], source_file, candidate_row["usetime_raw"], unknown_raw_texts)
        map_rows.append(map_row)
        covered_place_ids.add(place_id)

    name_by_place_id = {}
    for place_row in read_csv_rows(PLACES_FILE, "utf-8"):
        name_by_place_id[place_row["place_id"]] = place_row["name"]

    place_id_by_hub_folder = load_place_id_by_hub_folder()
    for json_file in sorted(HWP_HUB_INFO_DIRECTORY.glob("hub_*.json")):
        place_id = place_id_by_hub_folder.get(json_file.stem)
        if place_id is None or place_id in covered_place_ids:
            continue
        raw_text = read_hub_opening_hours_raw_text(json_file)
        if raw_text == "":
            continue
        source_file = json_file.relative_to(PROJECT_ROOT).as_posix()
        map_row = make_map_row(place_id, name_by_place_id[place_id], source_file, raw_text, unknown_raw_texts)
        map_rows.append(map_row)

    map_rows.sort(key=lambda row: row["place_id"])
    with open(OPENING_HOURS_MAP_FILE, "w", encoding="utf-8", newline="") as map_file:
        writer = csv.DictWriter(map_file, fieldnames=OPENING_HOURS_MAP_COLUMNS)
        writer.writeheader()
        writer.writerows(map_rows)

    confirmed_count = 0
    for map_row in map_rows:
        if map_row["status"] == STATUS_CONFIRMED:
            confirmed_count += 1
    print(f"운영시간 대응표 {len(map_rows)}곳: 확정 {confirmed_count}, 확인필요 {len(map_rows) - confirmed_count}")
    for place_id, name, raw_text in unknown_raw_texts:
        print(f"정리 값을 정하지 않은 원문 (확인필요로 둠): {place_id} {name} | {raw_text}")
    print(f"결과: {OPENING_HOURS_MAP_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
