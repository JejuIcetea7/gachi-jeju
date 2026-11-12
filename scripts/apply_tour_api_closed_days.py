"""한국관광공사 API 후보 확인 결과를 휴무일 대응표(closed_days_map.csv)에 넣는다.

- 입력: outputs/tour_api_candidates/candidates.csv (scripts/match_tour_api_places.py 결과, review 열 포함)
- 사용: 1단계이거나 review가 '통과'인 후보의 휴무 원문 -> 정리 값
  - 원문이 정확히 '연중무휴'면 그대로, 나머지는 아래 장소별 표로 정한다.
- 그 외(보류·틀림·원문 없음): closed_days 빈칸, status '확인필요'
- 대응표에서 source_file이 'tour_api'로 시작하는 줄만 다시 만들고, 나머지 줄(허브 HWP)은 그대로 둔다.

실행: python3 scripts/apply_tour_api_closed_days.py  (그다음 python3 scripts/build_places.py)
"""

import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_FILE = PROJECT_ROOT / "outputs" / "tour_api_candidates" / "candidates.csv"
CLOSED_DAYS_MAP_FILE = PROJECT_ROOT / "data" / "processed" / "closed_days_map.csv"

CLOSED_DAYS_MAP_COLUMNS = ["place_id", "name", "source_file", "raw_text", "closed_days", "status"]
TOUR_API_SOURCE_PREFIX = "tour_api"
STATUS_CONFIRMED = "확정"
STATUS_NEEDS_CHECK = "확인필요"

MONDAY_NEW_YEAR_HOLIDAYS = "매주 월요일;1월 1일;설날;추석"
MONDAY_NEW_YEAR_HOLIDAY_SEASONS = "매주 월요일;1월 1일;설날연휴;추석연휴"

# 원문이 '연중무휴' 한 단어가 아닌 곳의 정리 값 (사람이 원문을 보고 정함, 빈칸은 확인필요)
# 형식에 없는 조건은 원문(raw_text)에만 남긴다: '월요일이 공휴일이면 다음 비공휴일 휴무', '훈증 소독기간'
CLOSED_DAYS_BY_PLACE_ID = {
    "P011": "연중무휴",  # 비양도: 연중무휴 + 배 운항 시간 확인
    "P083": "연중무휴",  # 서귀포매일올레시장: 연중무휴 + 점포별 상이
    "P051": "연중무휴",  # 사려니숲길: 연중무휴 + 우천·폭설 통제
    "P072": "연중무휴",  # 사려니숲길 무장애나눔길: 같은 숲
    "P044": "연중무휴",  # 한라수목원: 야외 연중무휴, 실내시설만 설·추석 당일 휴무
    "P054": "연중무휴",  # 새별오름: 휴무 빈칸, 이용시간 '상시 개방'
    "P015": "매월 셋째 월요일",  # 항공우주박물관
    "P023": "1월 1일;설날연휴;추석연휴",  # 감귤박물관
    "P040": MONDAY_NEW_YEAR_HOLIDAYS,  # 돌문화공원
    "P041": MONDAY_NEW_YEAR_HOLIDAYS,  # 민속자연사박물관 (+ 훈증 소독기간)
    "P043": MONDAY_NEW_YEAR_HOLIDAYS,  # 별빛누리공원
    "P091": MONDAY_NEW_YEAR_HOLIDAYS,  # 제주추사관
    "P092": MONDAY_NEW_YEAR_HOLIDAYS,  # 국립제주박물관
    "P095": MONDAY_NEW_YEAR_HOLIDAYS,  # 제주도립김창열미술관
    "P101": MONDAY_NEW_YEAR_HOLIDAYS,  # 제주도립미술관
    "P086": MONDAY_NEW_YEAR_HOLIDAY_SEASONS,  # 해녀박물관
    "P099": MONDAY_NEW_YEAR_HOLIDAY_SEASONS,  # 제주현대미술관
    "P081": "매월 둘째 월요일;매월 넷째 월요일;1월 1일;설날연휴;추석연휴",  # 너븐숭이4.3기념관
    "P096": "매주 월요일",  # 그리스신화박물관
    "P012": "",  # 생각하는정원: 휴무 원문 없음
    "P014": "",  # 오설록: 휴무 원문 없음
    "P100": "",  # 동문시장: '점포별로 상이함'
}


def read_csv_rows(file_path, encoding):
    with open(file_path, encoding=encoding, newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    return rows


def is_accepted_candidate(candidate_row):
    accepted = False
    if candidate_row["stage"] == "1" and candidate_row["review"] == "":
        accepted = True
    if candidate_row["review"] == "통과":
        accepted = True
    return accepted


def build_tour_api_map_row(candidate_row, unknown_raw_texts):
    place_id = candidate_row["place_id"]
    restdate_raw = candidate_row["restdate_raw"].replace("\n", " ").strip()
    map_row = {
        "place_id": place_id,
        "name": candidate_row["name"],
        "source_file": f"{TOUR_API_SOURCE_PREFIX}:{candidate_row['content_id']}",
        "raw_text": restdate_raw,
        "closed_days": "",
        "status": STATUS_NEEDS_CHECK,
    }

    if not is_accepted_candidate(candidate_row):
        review = candidate_row["review"]
        if review == "":
            review = "미확인"
        map_row["source_file"] = f"{TOUR_API_SOURCE_PREFIX}:{review}"
        map_row["raw_text"] = ""
        return map_row

    closed_days = None
    if place_id in CLOSED_DAYS_BY_PLACE_ID:
        closed_days = CLOSED_DAYS_BY_PLACE_ID[place_id]
    elif restdate_raw == "연중무휴":
        closed_days = "연중무휴"
    else:
        unknown_raw_texts.append((place_id, candidate_row["name"], restdate_raw))
        closed_days = ""

    map_row["closed_days"] = closed_days
    if closed_days != "":
        map_row["status"] = STATUS_CONFIRMED
    return map_row


def main():
    existing_rows = read_csv_rows(CLOSED_DAYS_MAP_FILE, "utf-8")
    kept_rows = []
    kept_place_ids = set()
    for existing_row in existing_rows:
        if existing_row["source_file"].startswith(TOUR_API_SOURCE_PREFIX):
            continue
        kept_rows.append(existing_row)
        kept_place_ids.add(existing_row["place_id"])

    blank_kept_place_ids = set()
    for kept_row in kept_rows:
        if kept_row["closed_days"] == "":
            blank_kept_place_ids.add(kept_row["place_id"])

    candidate_rows = read_csv_rows(CANDIDATES_FILE, "utf-8-sig")
    tour_api_rows = []
    replaced_place_ids = set()
    unknown_raw_texts = []
    for candidate_row in candidate_rows:
        place_id = candidate_row["place_id"]
        map_row = build_tour_api_map_row(candidate_row, unknown_raw_texts)
        if place_id in kept_place_ids:
            # 기존 줄(허브 HWP)이 빈칸이고 API 값이 확정이면 API 값으로 바꾼다 (예: P108)
            if place_id not in blank_kept_place_ids or map_row["status"] != STATUS_CONFIRMED:
                continue
            replaced_place_ids.add(place_id)
        tour_api_rows.append(map_row)

    remaining_kept_rows = []
    for kept_row in kept_rows:
        if kept_row["place_id"] in replaced_place_ids:
            continue
        remaining_kept_rows.append(kept_row)

    all_rows = remaining_kept_rows + tour_api_rows
    all_rows.sort(key=lambda row: row["place_id"])
    with open(CLOSED_DAYS_MAP_FILE, "w", encoding="utf-8", newline="") as map_file:
        writer = csv.DictWriter(map_file, fieldnames=CLOSED_DAYS_MAP_COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)

    confirmed_count = 0
    for map_row in tour_api_rows:
        if map_row["status"] == STATUS_CONFIRMED:
            confirmed_count += 1
    print(f"한국관광공사 API 줄 {len(tour_api_rows)}개: 확정 {confirmed_count}, 확인필요 {len(tour_api_rows) - confirmed_count}")
    for place_id, name, restdate_raw in unknown_raw_texts:
        print(f"정리 값을 정하지 않은 원문 (확인필요로 둠): {place_id} {name} | {restdate_raw}")


if __name__ == "__main__":
    main()
