# build_places 설계

## 목표
- `data/raw/`의 두 출처(제주데이터허브, 공공데이터포털) 무장애 여행지 데이터를 하나의 스키마로 통합한다.
- 결과는 Agent가 이용자 조건(휠체어·유모차, 시설, 실내외 등)으로 여행지를 탐색할 때 쓴다.

## 경로
- 모듈: `scripts/build_places.py`
- 입력: `data/raw/jeju_datahub/hub_*/`, `data/raw/data_go_kr/dgk_*/`
- 출력: `data/processed/places.csv`, `place_spots.csv`, `place_nearby.csv`, `place_id_map.csv`, `gachi_jeju.db`
- 로그: `outputs/build_places/` (원본 목록, 실패 로그, 충돌 로그, 행 수 대조, 이상값 `anomalies.csv`)

## 입력
- 원본 CSV는 cp949로 읽는다.

| 형식 | 위치 | 파일 수 | 쓰임 |
| --- | --- | --- | --- |
| 허브 번호 `-2.csv` | `hub_1` ~ `hub_51` | 51 | 지점 |
| 장소명 50개형 (`위도, 경도, 장소명칭, ...`) | `hub_52` ~ `hub_121` | 50 | 지점 |
| 장소명 20개형 (`ID, lat, lon, title, ...`) | `hub_52` ~ `hub_121` | 20 | 지점 |
| `D코스` | `dgk_*` (위치정보 컬럼, group `D코스` 49 + `그 외` 1) | 50 | 지점 |
| `B주변정보` | `dgk_*` (group `B주변정보`) | 50 | 주변시설 |
| `그 외` 주변시설 | `dgk_*` (group `그 외`, `휠체어 진입` 컬럼) | 42 | 주변시설 |

- 허브 `-1.csv`(이동 경로 좌표)와 HWP는 이번에 읽지 않는다.

## 출력

### places.csv (장소 1행)
- 기본: `place_id`, `name`, `category`
- 위치: `region`, `address`, `latitude`, `longitude`
- 접근성: `wheelchair_level`, `stroller_level`, `has_slope_or_stairs`
- 시설: `accessible_parking`, `accessible_toilet`, `wheelchair_rental`, `stroller_rental`
- 일정: `indoor_outdoor`, `visit_minutes`, `opening_hours`, `closed_days`, `admission_fee`
- 출처: `sources`, `data_date`

### place_spots.csv (지점 1행)
- `place_id`, `order`, `name`, `latitude`, `longitude`, `description`, `accessibility_note`, `is_recommended_course`, `image_file`, `source_file`

### place_nearby.csv (주변시설 1행)
- `place_id`, `facility_type`, `name`, `distance_m`, `address`, `phone`, `access_note`, `wheelchair_entry`, `accessible_toilet`, `source_file`

### place_id_map.csv
- 원본 파일 ↔ `place_id` 대응표. 중복 확정 결과를 담는다.

### 공통
- 인코딩 UTF-8
- Y/N 컬럼 값은 `Y`/`N`/`unknown` (정보 없음은 `unknown`)
- 지점·주변시설 표는 모든 장소를 한 파일에 행으로 쌓고 `place_id`로 연결한다.

## 동작

### 1. 원본 목록 만들기
- 원본 CSV를 위 6개 형식으로 분류하고 파일 경로, 형식, 장소명, 행 수를 기록한다.

### 2. 지점 변환

| 원본 | name | description | accessibility_note | is_recommended_course | image_file |
| --- | --- | --- | --- | --- | --- |
| 허브 번호 `-2.csv` | 장소명 | 장소설명 | unknown | unknown | 이미지파일 |
| 장소명 50개형 | 장소명칭 | 장소상세정보 | 무장애관광정보 | 추천코스여부 | - |
| 장소명 20개형 | title | Description | info 코드(1·2 → 문장) | B 값 | Image |
| `D코스` | 구분 | 설명 | 상세정보 | 추천코스여부 | - |

- 제공된 지점은 모두 넣는다 (길 안내, 사진만 있는 행 포함).
- 이름·좌표가 거의 같은 연속 행(사진 행 + 설명 행)은 1행으로 합친다.
- `order`는 원본 행 순서를 쓴다.
- 장소명 20개형에서 이름·좌표 없이 `etc` 범례만 있는 행은 뺀다.

### 3. 주변시설 변환

| 원본 | facility_type | distance_m | access_note | wheelchair_entry / accessible_toilet |
| --- | --- | --- | --- | --- |
| `B주변정보` | 주변시설종류 | 반경(m) | 이용정보 | unknown |
| `그 외` | 구분 | 거리(km) × 1000 | - | ● → Y, 빈칸 → unknown |

- `facility_type`은 음식점, 숙박, 카페·편의점, 화장실, 관광지로 통일한다.
- `B주변정보`는 제목 번호(`-41-`)로 `links.csv`의 장소명 폴더에 연결한다.
- `그 외`는 파일 안에 관광지명이 없어 `meta.json` 제목의 장소명으로 연결한다.
- 주변시설은 새 장소를 만들지 않고 기존 장소에 붙인다.

### 4. 장소 후보와 중복 확정
- 지점 원본 파일마다 장소 후보 1개(이름, 대표 좌표)를 만든다.
- 대표 좌표: 출발 지점(예: `주차장[시작]`), 없으면 첫 번째 지점
- 이름 정규화(띄어쓰기·특수문자·괄호 제거)와 대표 좌표 거리로 중복 후보 목록을 출력한다.
- `place_id_map.csv`가 없으면 초안을 만들고, 있으면 그대로 읽는다 (사람이 고친 내용 유지).
- 중복으로 확정한 원본은 `match_status = excluded`, `place_id`에 중복 대상 장소를 적고 결과에서 뺀다.
- 사람이 후보를 확정하고 `place_id_map.csv`에 반영한다.
- `place_id`는 순번 `P001`부터 매긴다.

### 5. places 집계
- `has_slope_or_stairs`: 지점 중 하나라도 계단·경사 정보가 있으면 `Y`
- `accessible_toilet`: 지점 중 하나라도 장애인화장실이 있으면 `Y`
- `wheelchair_level`, `stroller_level`: `unknown`
- `category`: 기본 목록(자연, 박물관, 시장, 해변, 산책로 등)으로 분류
- `visit_minutes`: 60
- `region`, `address`, `opening_hours`, `closed_days`, `admission_fee`: 빈칸

### 6. 출처 간 충돌
- 지점·주변시설: 출처별로 모두 넣고 `source_file`로 구분한다.
- Y/N 컬럼: 한 출처라도 `Y`면 `Y`
- 이름·좌표: 허브 번호 폴더 > 장소명 폴더 > 포털
- 충돌한 값은 충돌 로그에 남긴다.

### 7. SQLite 적재
- CSV 3개를 `gachi_jeju.db`의 표 3개로 넣는다. DB는 CSV에서 다시 만들 수 있다.

## 실패 처리
- 파일 읽기 실패, 예상과 다른 컬럼은 실패 로그에 파일 경로와 사유를 남기고 다음 파일로 넘어간다.
- 원본과 결과의 행 수가 다르면 사유와 함께 기록한다.

## 실행
- `python3 scripts/build_places.py` (표준 라이브러리만 사용, 옵션 없음)
- 대응표를 고친 뒤 다시 실행하면 결과 CSV와 DB를 모두 다시 만든다.

## 함수 구성
- `list_source_files`: 원본 CSV를 형식별로 분류 (`load_hub_number_names`, `detect_source_format`)
- `read_csv_file`, `save_csv_file`: CSV 읽기·저장
- `convert_hub_number_spots`, `convert_place_name_50_spots`, `convert_place_name_20_spots`, `convert_course_spots`: 형식별 지점 변환
- `convert_nearby_info`, `convert_nearby_etc`: 형식별 주변시설 변환
- `merge_split_spot_rows`: 사진 행·설명 행 합치기
- `choose_representative_spot`: 대표 좌표 고르기
- `find_duplicate_candidates`: 중복 후보 목록 출력
- `create_draft_place_id_map`, `load_place_id_map`: 대응표 초안 만들기·읽기
- `build_places_rows`, `record_place_conflicts`: places 집계, 충돌 기록
- `verify_outputs`: 행 수 대조, `place_id` 참조 확인
- `find_anomalies`: 이상값 기록 (값은 고치지 않음)
- `build_sqlite_database`, `load_csv_to_sqlite`: SQLite 적재

## 검증 상태
- 확인함: 원본 CSV 313개 분류, 실패 0건 (지점 170, 주변시설 92, 이동 경로 50 제외)
- 확인함: places 170행, place_spots 5,076행, place_nearby 1,031행, places에 없는 `place_id` 참조 0건
- 확인함: 원본 6,408행 → 결과 6,107행. 차이 301행 = 사진 행·설명 행 합침 262 (허브 번호 254, 장소명 50개형 8) + 범례만 있는 행 4 + 중복 제외 35 (`hub_37`)
- 확인함: `B주변정보` 50개 모두 제목 번호로 연결, 이름이 다른 10건은 이름 목록을 출력해 Claude가 같은 장소인지 판단 (좌표·내용 대조는 안 함)
- 확인함: SQLite `accessible_toilet = 'Y'` 조회 73곳
- 확인함: 사려니숲길 3쌍 - `hub_37`·`hub_51` 좌표 전부 같음(지점 35/35, 경로 111/111) → `hub_37` 제외. `hub_72`와는 입구 6~8지점만 겹침 → 별개
- 확인함: 중복 후보 19쌍 사용자 확정 (2026-11-05) - 중복 1쌍(`hub_37`·`hub_51`), 나머지 18쌍은 별개 장소 (싱계물해양공원/싱게물공원과용당포구 포함)
- 확인 안 함: 합친 262행 전수 대조, 카테고리 분류 정확도
- 확인 안 함: HWP 내용 (로컬 파일은 Git LFS 포인터)

## 이후 개선

### 확정 대기
- 중복 아님으로 확정한 쌍을 기록할 곳이 없음 → 후보 목록이 매번 같은 쌍을 다시 보여 줌
- 임의 결정 목록(`docs/build_places-assumptions.md`) 27건 검토·상태 갱신

### 값 채우기 기준
- `wheelchair_level`, `stroller_level`: 허브 장소설명(경사 각도·노면), `고도`, HWP 장애 유형별 추천 여부로 기준 마련 (장애 유형 컬럼은 그때 확정)
- `has_slope_or_stairs`: 모든 지점이 `특이사항없음`인 장소를 `N`으로 볼지 결정 ("계단 없는 곳" 조건에 필요)
- `accessible_parking`, `wheelchair_rental`, `stroller_rental`: 허브 장소설명 키워드("장애인주차", "대여")로 채울지 결정
- `indoor_outdoor`: 카테고리 추정 또는 HWP로 채울지 결정
- `region`, `address`, 운영시간: `git lfs pull` 후 HWP로 채우고, 못 채운 주소는 카카오 좌표→주소 API
- 관광공사 API 연결 키는 필요할 때 추가

### 규칙 보완
- 대표 좌표: 추천코스 지점 우선 여부 결정 (빛의벙커는 추천코스 밖 제2주차장이 잡힘)
- `data_date`: 가장 최근 날짜라 지점 정보가 실제보다 최신처럼 보임 → 지점 원본 날짜를 따로 둘지 결정
- 주변정보 연결 검증: 주변정보 주소와 장소 대표 좌표 비교 검사 추가

### 문서 · 검증
- `category` 값 목록을 실제 값(박물관·전시, 시장, 해변, 자연, 공원, 산책로, 기타)과 맞추고 키워드 표 추가
- 합친 지점 262행 대조, 카테고리 분류 정확도 확인

### 기타
- DB·CSV 결과물의 git 포함 여부 결정 후 커밋
