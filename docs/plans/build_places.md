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

- 허브 `-1.csv`(이동 경로 좌표)는 읽지 않는다.
- 일반정보 HWP(`D관광정보` 49개)는 `scripts/convert_hwp_general_info.py`로 변환한 `data/processed/hwp_general_info/<데이터ID>.json`을 읽어 `closed_days`, `admission_fee`, 접근성 원본 표에 쓴다.
- 허브 HWP(`hub_102`~`hub_121` 20개)는 같은 스크립트로 `data/processed/hwp_hub_info/hub_<번호>.md·json`에 변환하고, 사람이 원문을 보고 정리한 `data/processed/closed_days_map.csv`(`place_id`, `name`, `source_file`, `raw_text`, `closed_days`, `status`)를 읽는다.
- 나머지 장소는 한국관광공사 국문 관광정보 API로 후보를 찾고(`scripts/match_tour_api_places.py`), 사람이 확인한 결과(`data/processed/tour_api_review.csv`)를 `scripts/apply_tour_api_closed_days.py`가 같은 대응표에 넣는다 (`source_file` = `tour_api:<콘텐츠ID>`).
  - 후보 단계: 1 이름 같음 3km 이내 또는 포함 관계 500m 이내, 2 이름 유사도 0.6 이상 1km 이내, 3 그 외. 1단계와 `통과`만 사용
  - `status`: `확정` / `확인필요`(빈칸). 실행 순서: `apply_tour_api_closed_days.py` → `build_places.py`
  - 매칭 대상은 170곳 전체 (2026-11-13부터, 이전에는 휴무일 빈칸 103곳). 이용요금 원문 `usefee_raw`도 담는다
- `admission_fee`는 매칭 결과 `outputs/tour_api_candidates/candidates.csv`의 `usefee_raw`(1단계·`통과`만)를 직접 읽는다.
- 휴무일은 같은 장소에 허브·일반정보 HWP 값이 있어도 API 값이 `확정`이면 API 값을 쓴다 (2026-11-13부터). 대응표 값이 빈칸이면 일반정보 HWP 값을 지우지 않는다.
- 운영시간 대응표 `data/processed/opening_hours_map.csv`(`place_id`, `name`, `source_file`, `raw_text`, `opening_hours`, `status`)는 `scripts/build_opening_hours_map.py`가 만든다. 원문은 API 이용시간(1단계·`통과`) > 허브 HWP `관람시간`. 실행 순서: `build_opening_hours_map.py` → `build_places.py`

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

### place_accessibility_raw.csv (접근성 특징 1행)
- `place_id`, `section`, `facility_name`, `description`, `raw_value`, `source_file`
- HWP 69곳(일반정보 49, 허브 20)의 접근성 사실을 원문 그대로 담는다. 입력은 kordoc 변환 json(`hwp_general_info/`, `hwp_hub_info/`)
- 일반정보: 기본정보(장애인 주차·화장실, 보조기기 대여), 추천 대상, 주차장(장애인·임산부 구역, 기타), 장애인 화장실, 주출입구
- 허브: 이용안내의 보조기기·편의장비·승강기, 장애인 주차 문장, 개요의 '이동약자를 위한 안내'
- 빼는 것: 관람동선 문장(`place_spots`와 겹침), 사진, 주변 시설, 일반 주차·대형버스·전기차, 관람시간·요금 등

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
- `accessible_parking`: 접근성 원본 표에서 판정, 근거 없으면 `unknown`
  - 일반정보: 장애인 주차면수 1 이상인 주차장이 있거나 '장애인 주차장 유무'가 있음·유 → `Y`, 유무 없음·무 또는 주차면수 모두 없음 → `N`
  - 허브: 이용안내 주차장 문장에 "장애인 주차구역(은/이) 없" → `N`, 장애인·주차 언급 → `Y`
- `accessible_toilet`: 지점 중 하나라도 장애인화장실이 있으면 `Y`
- `wheelchair_level`, `stroller_level`: `unknown`
- `category`: 기본 목록(자연, 박물관, 시장, 해변, 산책로 등)으로 분류
- `visit_minutes`: 60
- `region`, `address`: 빈칸
- `opening_hours`: 운영시간 대응표의 정리 값, `opening_hours_raw`: 같은 표의 원문
  - 정리 값 형식: `상시 개방` / `09:00-18:00` / `09:00-18:00 입장마감 17:00`, 계절별은 `3-10월 …`을 앞에 붙여 `;`로 잇는다
  - 규칙: 시각 없이 `상시 개방`만 있음, 또는 시간 하나(+ 입장·매표 마감 하나)이고 계절·시설·점포 구분이 없음. 그 외는 스크립트의 장소별 표로 사람이 정함
  - 시설이 여럿이면 대표 시설 하나, 계절 달이 원문에 없으면 더 이른 마감. 형식에 안 맞는 정리 값은 빈칸 + 실패 로그
- `admission_fee`: 원문에 최소 정리만 한다 (줄바꿈·공백 정리, `※ 자세한 …` 안내 삭제, 값 전체가 `없음`·`무료`면 `무료`)
  - 우선순위: 한국관광공사 API `usefee_raw` > 허브 HWP > 일반정보 HWP 첫 표 `관람료` 행
  - 허브 HWP: 표의 `관람료` 행(세로로 합쳐진 칸은 아래 행에도 채움) 또는 `관람료 :` 문단과 다음 항목(보조기기·문의 등) 전까지의 문단
  - 장소별 예외: P156 이중섭거리 `무료` (원문은 이중섭미술관 요금)
- `closed_days`: 일반정보 HWP 첫 표의 `휴무일` 원문을 정리 값으로 바꿔 넣고, 없으면 빈칸
  - 정리 값 형식 6가지를 `;`로 잇는다: `연중무휴`(단독), `매주 월요일`, `매월 첫째 화요일`, `1월 1일`, `설날`·`추석`(당일)·`설날연휴`·`추석연휴`(전날~다음날 3일), `법정공휴일`
  - 일반정보 HWP: 원문 → 정리 값 대응표와 장소별 예외표(P128, P156, P131)로 처리, 표에 없는 원문은 빈칸 + 실패 로그
  - 허브 HWP: `closed_days_map.csv` 값이 일반정보 값보다 우선. 형식에 안 맞는 정리 값은 넣지 않고 실패 로그
  - HWP ↔ `place_id`: 파일 이름 속 장소명(공백 제거)을 대응표의 원본 파일 이름과 맞추고, 오타 2건은 데이터ID로 직접 연결

### 6. 출처 간 충돌
- 지점·주변시설: 출처별로 모두 넣고 `source_file`로 구분한다.
- Y/N 컬럼: 한 출처라도 `Y`면 `Y`
- 이름·좌표: 허브 번호 폴더 > 장소명 폴더 > 포털
- 충돌한 값은 충돌 로그에 남긴다.

### 7. SQLite 적재
- CSV 4개를 `gachi_jeju.db`의 표 4개(`places`, `place_spots`, `place_nearby`, `place_accessibility_raw`)로 넣는다. DB는 CSV에서 다시 만들 수 있다.

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
- `load_hwp_closed_days_by_place_id`: 일반정보 HWP 변환 결과에서 `closed_days` 정리 값 만들기 (`load_place_id_by_name_token`, `extract_place_name_token`, `read_closed_days_raw_text`)
- `load_closed_days_map`, `is_valid_closed_days`: 휴무일 대응표 읽기, 정리 값 형식 검사
- `build_places_rows`, `record_place_conflicts`: places 집계, 충돌 기록
- `verify_outputs`: 행 수 대조, `place_id` 참조 확인
- `find_anomalies`: 이상값 기록 (값은 고치지 않음)
- `build_accessibility_raw_rows`: 접근성 원본 표 (`extract_general_info_accessibility_rows`, `extract_hub_info_accessibility_rows`, 표 격자 해석 `list_origin_cells`)
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
- 확인함: 일반정보 HWP 49개 모두 `place_id`와 연결, `closed_days` 48곳 `연중무휴`, P131 빈칸, 나머지 칸 변화 없음 (2026-11-12)
- 확인함: 허브 HWP 20개 `git lfs fetch`로 받음(크기·해시 일치), 20개 모두 `이용안내`에 관람시간·휴무 정보. `closed_days` 19곳 채움, P108 빈칸(휴무 언급 없음). 합계 67곳 채움, 103곳 빈칸 (2026-11-12)
- 확인함: 한국관광공사 API로 103곳 중 74곳 채움(P108 포함). 합계 141곳 채움, 29곳 `확인필요` 빈칸. 대응표 정리 값 형식 위반 0건, `places.csv`에서 바뀐 칸은 `closed_days`뿐 (2026-11-12)
- 확인 안 함: 1단계 59곳 후보 전수 대조 (규칙만으로 통과), 형식에서 뺀 조건("월요일이 공휴일이면 다음 비공휴일 휴무" 등)의 일정 영향
- 확인함: 접근성 원본 표 69곳 3,145줄 (장애인 화장실 1,501, 기본정보 441, 주출입구 440, 추천 대상 392, 주차장 284, 이용안내 87). 퍼시픽마리나요트공원(장애인 주차 5대, 내부 출입문 폭 94cm, 추천 대상 8줄)·가문동해안길(장애인 주차 없음)을 원본과 대조. 기존 CSV 3개 변화 없음 (2026-11-12)
- 확인 안 함: 69곳 전수 대조, 허브 HWP의 서술 구간(사진 설명·지점별 접근성 문단)
- 확인함: `accessible_parking` Y 31, N 32, unknown 107. 허브 14곳 문장 판정을 원문과 대조(P109·P111·P119 N, P118 Y 등) (2026-11-12)
- 확인 안 함: HWP가 없는 101곳의 장애인 주차 (`place_spots` 지점 설명 키워드는 아직 쓰지 않음)
- 확인함: 관광공사 API 매칭 170곳으로 확대, 새 67곳 중 1단계 24 자동 통과, 2·3단계 11곳 사람 확인(통과 8, 틀림 3), 나머지 3단계 32곳 틀림. 사용 109곳 (2026-11-13)
- 확인함: `admission_fee` 82곳 (무료 55, 금액 27). 금액 27곳 원문 출력해 확인, `places.csv`에서 바뀐 칸은 `admission_fee`뿐 (2026-11-13)
- 확인함: 휴무일 API 우선 적용으로 4곳 바뀜 (P104 연중무휴 → 1월 1일·설날·추석, P115·P118 연휴 표기, P120 매주 수요일 → 매주 월요일). 합계 141곳 그대로 (2026-11-13)
- 확인함: `opening_hours` 109곳 (상시 개방 49, 시각 60), 확인필요 2곳(P100 동문시장, P133 도두봉). 형식 위반 0건, 정리 값 60곳을 원문과 함께 출력해 확인 (2026-11-13)
- 확인 안 함: 허브 HWP 휴무일·운영시간 중 API로 바뀐 값의 실제 최신 여부, 원문에만 남긴 조건(공연 회차, 시설별 시간, 사전 예약)
- 확인 안 함: 허브 HWP 요금의 최신 여부 (P107 아르떼뮤지엄 원문은 2020년 오픈특가), 관광지 유형(12) API는 요금 칸이 없어 88곳 빈칸

## 이후 개선

### 확정 대기
- 중복 아님으로 확정한 쌍을 기록할 곳이 없음 → 후보 목록이 매번 같은 쌍을 다시 보여 줌
- 임의 결정 목록(`docs/build_places-assumptions.md`) 27건 검토·상태 갱신

### 값 채우기 기준
- `wheelchair_level`, `stroller_level`: 허브 장소설명(경사 각도·노면), `고도`, HWP 장애 유형별 추천 여부로 기준 마련 (장애 유형 컬럼은 그때 확정)
- `has_slope_or_stairs`: 모든 지점이 `특이사항없음`인 장소를 `N`으로 볼지 결정 ("계단 없는 곳" 조건에 필요)
- `accessible_parking`(HWP 없는 101곳), `wheelchair_rental`, `stroller_rental`: 허브 장소설명 키워드("장애인주차", "대여")로 채울지 결정
- `indoor_outdoor`: 카테고리 추정 또는 HWP로 채울지 결정
- `region`, `address`: HWP로 채우고, 못 채운 주소는 카카오 좌표→주소 API
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
