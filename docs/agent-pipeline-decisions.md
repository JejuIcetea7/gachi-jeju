# Agent 파이프라인 결정 목록

1차 파이프라인(LLM + tool) 구현 중 미룬 것과 상의 없이 정한 것이다. 확인되면 상태를 `확인`으로 바꾼다.

## 추후 구현

| 번호 | 내용 | 이유 | 상태 |
| --- | --- | --- | --- |
| 1 | `get_place_details`에 `place_nearby`(장애인 화장실, 휠체어 출입) 넣기 | 1차는 기본만 확인 | 미정 |
| 2 | 접근성 등급 칸(`wheelchair_level` 등)으로 장소 거르기 | 170곳 모두 `unknown`, 데이터 보완 후 가능 | 미정 |
| 3 | 운영시간·휴무일·날씨 tool | 1차 파이프라인에 없어도 동작 | 미정 |
| 4 | `get_bus_trip` 설명 글 추가, LLM에 넘기는 경로 수 줄이기 | 3단계에서 진행 | 미정 |
| 5 | FastAPI, React 연결 | 터미널 실행으로 먼저 확인 | 미정 |
| 6 | 추자도 검색 | 제주도 범위 사각형에서 빠짐 | 미정 |

## 임의 결정

| 번호 | 결정 | 이유 | 상태 |
| --- | --- | --- | --- |
| 1 | 파일·함수 이름: `services/kakao_local.py`(`search_keyword_place`), `tools/location.py`(`find_location`), `tools/places.py`(`search_places`, `get_place_details`) | 기존 `services/kakao_mobility.py` + `tools/driving.py` 구조를 따름 | 미확인 |
| 2 | 제주도 범위: 경도 126.08~127.00, 위도 33.10~33.60 (`rect`) | "시청" 검색 시 제주시청이 나오는 것 확인 | 미확인 |
| 3 | `find_location`은 검색 결과 1개만 요청(`size=1`)해 이름·주소·위도·경도를 돌려줌 | 테스트 단계라 첫 결과로 충분 | 미확인 |
| 4 | 주소는 도로명 주소를 쓰고, 비어 있으면 지번 주소를 씀 | 도로명 주소가 없는 장소가 있음 | 미확인 |
| 5 | 카카오 장소 검색 오류: 401·403 → `auth_error`, 429 → `rate_limited`, 그 밖 → `api_error`, 결과 없음 → `not_found` | 기존 카카오 tool의 `status` 값과 맞춤 | 미확인 |
| 6 | `search_places`는 결과가 없으면 `not_found`, DB를 못 읽으면 `db_error` | 다른 tool처럼 `status`로 구분 | 미확인 |
| 7 | 거리는 소수 첫째 자리(km)까지 반올림 | LLM이 읽기 쉬운 정도 | 미확인 |
| 8 | DB는 읽기 전용으로 엶 | 파일이 없을 때 빈 DB가 새로 생기지 않게 | 미확인 |
| 9 | `get_place_details` 중복 기준: 지점 이름과 설명이 모두 같으면 하나만 남김. 설명이 빈 지점은 뺌 | 한림공원 예: 지점 150개 → 35개 | 미확인 |
| 10 | 테스트 샘플 `tests/sample_responses/kakao_keyword_success.json`은 "제주국제공항" 실제 응답을 한 번 받아 저장함 (스크립트 없음) | 기존 샘플처럼 실제 응답 기준으로 테스트 | 미확인 |
| 11 | 장소 tool 테스트는 실제 DB로 하되, 특정 장소 대신 성질(거리순, 반경 안, 빈 설명 없음)을 확인 | 데이터 보완 후에도 깨지지 않게 | 미확인 |
