# Agent 파이프라인 결정 목록

1차 파이프라인(LLM + tool) 구현 중 미룬 것과 상의 없이 정한 것이다. 확인되면 상태를 `확인`으로 바꾼다.

## 추후 구현

| 번호 | 내용 | 이유 | 상태 |
| --- | --- | --- | --- |
| 1 | `get_place_details`에 `place_nearby`(장애인 화장실, 휠체어 출입) 넣기 | 1차는 기본만 확인 | 미정 |
| 2 | 접근성 등급 칸(`wheelchair_level` 등)으로 장소 거르기 | 170곳 모두 `unknown`, 데이터 보완 후 가능 | 미정 |
| 3 | 운영시간·휴무일·날씨 tool | 1차 파이프라인에 없어도 동작 | 미정 |
| 4 | `get_bus_trip` 설명 글 추가, LLM에 넘기는 경로 수 줄이기 | `get_bus_route_summary`로 임시 해결 (공항→한림공원 18,343자 → 350자) | 완료 |
| 5 | FastAPI, React 연결 | 조건 전달, 생성 상태·오류 안내, 일정표와 결과 복원 연결. 2026-11-10 자동차 당일 일정 수동 확인 | 완료 |
| 6 | 추자도 검색 | 제주도 범위 사각형에서 빠짐 | 미정 |
| 7 | 버스 경로를 저상버스 여부로 고르기 | 1차는 환승·소요시간만 봄. 저상버스 데이터 확인 필요 | 미정 |
| 8 | 대화로 일정 수정하기 | 1차는 요청 1개 → 일정 1개 | 미정 |
| 9 | Gemini 호출 오류(한도 초과 등) 처리 | API에서 429·503·504·502로 구분해 돌려줌 (임의 결정 32, 33) | 완료 |
| 10 | Agent 실제 실행 확인 (5단계, 예시 요청 3개) | 2026-11-09 `gpt-6-luna`로 바꾼 뒤 `POST /plan` 휠체어·렌터카·1일 요청 1개 성공 (200, 30.3초, 장소 3곳 모두 `verified`). 버스·유모차·2~3일은 아직. 이전 기록: Gemini 3.8 Flash 503·504, 3.6 Flash 하루 20번 한도 | 진행 중 |
| 11 | 503이 계속될 때 다른 모델로 넘어가기, 재시도 대상에서 429 빼기 | 1차는 재시도만 둠 | 취소 (OpenAI로 변경) |
| 12 | 요청·tool 호출·응답 기록 남기기 | 1차에서 뺌 (사용자 결정) | 미정 |
| 13 | 일정 수정용 대화 ID | 1차는 요청 1개 → 일정 1개 | 미정 |
| 14 | 하루 한도(429 일일)와 1분 한도 구분해 안내 | 1차는 같은 문구로 안내 | 취소 (OpenAI로 변경) |
| 15 | Gemini 무료 한도 늘리기(결제 등록) 검토 | `gemini-3.6-flash` 무료 하루 20번 → 일정 2~3개/일 (2026-11-09 서버 오류로 확인) | 취소 (OpenAI로 변경) |
| 16 | 일정 시간 검증: 항목의 시각·여행 가능 시간·겹침·duration_minutes 일치, assumptions의 시각 설명 대조 | 2026-11-10 시간표 검증과 API 오류 반환 구현·자동 테스트 통과. assumptions의 자연어 설명 대조는 남음 | 부분 완료 |
| 17 | 이동시간이 tool 결과와 같은지 확인할 방법 | 다시 조회하면 카카오 값이 달라짐(예: 한라수목원→삼양 일정 38분·14.7km, 재조회 29분·19.3km). tool 결과 기록이 없어 비교 불가 (추후 구현 12번과 관련) | 미정 |

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
| 12 | 버스 함수 이름 `get_bus_route_summary`, 내부 함수 `_build_bus_trip`, `_select_best_route`, `_summarize_segment` | 기존 `get_bus_trip`을 바꾸지 않고 LLM용만 추가 | 미확인 |
| 13 | 버스 경로 고르기: 환승이 가장 적은 경로, 같으면 소요시간이 짧은 경로 1개 | 이동 약자는 환승이 적은 게 중요. 임시 기준 | 미확인 |
| 14 | 버스 요약에 남기는 칸: 총 소요시간·거리·환승·요금, 구간별 버스 번호·탑승/하차 정류장·정류장 수·실시간 상태, 도착 예정 버스(번호, 저상버스, 남은 분, 남은 정류장 수) | 일정 짜는 데 필요한 것만. `matches`, 정류장 이름 전체, 버스 위치는 뺌 | 미확인 |
| 15 | `config.py`의 `.env` 읽기를 `read_env_value(name)`로 묶고, 카카오·Gemini 키가 같이 씀 | 같은 코드 중복 방지. 카카오 키 읽기 동작은 같음 | 미확인 |
| 16 | `langchain` 1.4.9 추가 설치, `create_agent` 사용 | `create_react_agent`가 사용 중단 예정 (사용자 선택) | 확인 |
| 17 | 모델 설정을 `config.py`에 둠: `LLM_MODEL = "gpt-6-luna"`, `LLM_REASONING_EFFORT = "low"` (Gemini 3.8 → 3.6 Flash → GPT-6 Luna로 변경) | Gemini 3.8 Flash 503·504, 3.6 Flash 무료 하루 20번 한도. GPT-6 Luna는 유료(입력 0.10, 출력 0.50달러/100만 토큰) (사용자 결정, 2026-11-09) | 확인 |
| 18 | Agent 파일 `backend/app/agent/planner.py`, 실행 `python -m backend.app.agent.planner "요청 문장"` | 터미널에서 바로 확인 | 미확인 |
| 19 | 시스템 프롬프트: 좌표·장소·이동시간은 tool 결과만 쓰기, 접근성 불확실하면 적기, 말하지 않은 조건은 일반값으로 정하고 맨 앞에 적기 | 지어낸 정보를 막기 위해 | 미확인 |
| 20 | ~~일정 형식: `## N일차` + 표~~ → JSON(`TripPlan`: assumptions, days[].items[])으로 바꿈 | 화면에서 칸별로 쓰기 위해 (사용자 결정, 2026-11-09) | 확인 |
| 21 | 실행 결과에 Gemini 호출 횟수와 tool별 호출 횟수를 함께 출력 | 무료 한도 확인용 | 미확인 |
| 22 | LLM 재시도: `max_retries=2`(처음 1번 + 재시도 2번), 한 번에 최대 120초(`timeout=120`). OpenAI 도구 기본 규칙: 0.5초부터 2배(최대 8초) × 무작위 0.75~1, 408·409·429·5xx 재시도, 서버가 알려 준 대기 시간(120초 이하)은 따름 | Gemini 때와 시도 횟수를 같게 맞춤 | 미확인 |
| 23 | 다른 모델로 넘어가기(503 시 `gemini-3.5-flash` 등)는 넣지 않음 | 모델 선택은 사용자 결정 (2026-11-09) | 취소 (OpenAI로 변경) |
| 24 | API 구성: `POST /plan` 처리 순서는 `build_user_message` → `plan_trip` → `validate_plan_times` → `verify_places`. 서버 확인은 `GET /health` | 프론트가 한 번 호출하고 서버가 생성 결과의 시간을 검사하도록 구성 | 확인 |
| 25 | 파일: `backend/app/main.py`(앱, CORS, 오류 처리), `backend/app/routers/plan.py`(요청·응답 모델, 시간 검증과 출력 정리, API) | `CLAUDE.md` 디렉터리 구조의 `routers/` | 미확인 |
| 26 | 요청 규격은 [API 명세](api.md) 기준. 좌표·walking/driving/transit·보조기구 미선택·시간·필수 시설·휴식·귀환을 지원하며 기존 이동수단 값도 유지 | 화면의 선택 조건을 전달하도록 확장(2026-11-10) | 확인 |
| 27 | trip_days 1~3(현재 화면은 1일), departure 50자 이하, message 합산 1,000자 이하(기존 300자에서 변경) | 화면과 API의 요청 길이 제한 통일(2026-11-10) | 확인 |
| 28 | 선택 항목을 "출발지: ... / 이동수단: 렌터카 / ..." 한국어 줄 글로 바꿔 Gemini에 넘김 | 사람이 읽는 글이 LLM에도 잘 읽힘 | 미확인 |
| 29 | 일정 칸: `start_time`, `end_time`, `item_type`(`visit`/`move`), `title`, `place_id`, `transport`, `duration_minutes`, `distance_km`, `accessibility_notes` + 출력 정리에서 `verified`, `category`, `latitude`, `longitude` 추가 | 화면 표시와 지도 표시 대비 | 미확인 |
| 30 | DB에 없는 장소는 항목을 남기고 `verified: false` + `warnings`에 이유 | 항목을 빼면 일정 시간이 빔 | 미확인 |
| 31 | 응답에 `meta`(Gemini 호출 횟수, tool 호출 횟수, 걸린 시간) 포함 | 무료 한도 확인용 | 미확인 |
| 32 | 오류 코드: 400 `invalid_request`, 429 `rate_limited`, 500 `internal_error`, 502 `llm_invalid_response`·`plan_validation_failed`, 503 `llm_unavailable`(5xx·연결 실패), 503 `llm_quota_exhausted`(OpenAI `insufficient_quota`), 504 `llm_timeout`. FastAPI 기본 422는 400으로 바꿈. 입력 오류 이유는 한국어 | 화면이 오류를 구분해 안내하게. 잔액 부족은 기다려도 안 풀려 따로 구분 | 미확인 |
| 33 | LLM 오류는 예외의 원인(`__cause__`)을 따라가 `openai.APITimeoutError`(504), `openai.APIConnectionError`(503), `openai.APIStatusError`(`status_code`, `code`)로 구분 | LangChain이 오류를 감쌀 때도 찾기 위해 | 미확인 |
| 34 | CORS 허용: `http://localhost:3000`, `http://localhost:5173` | React 개발 서버 기본 포트. 프론트와 확인 필요 | 미확인 |
| 35 | 처리 못 한 오류는 서버 화면(콘솔)에 출력 (`logger.exception`) | 원인 찾기용. 파일 기록은 아님 | 미확인 |
| 36 | `fastapi` 0.162.2, `uvicorn` 0.54.0 추가 | 설치 시 최신 버전 | 미확인 |
| 37 | `langchain-google-genai` 삭제, `langchain-openai` 1.7.4 추가 (`openai` 3.46.0이 함께 설치됨). `google-genai`는 `jeju` 환경에 남아 있음(코드에서는 안 씀) | 사용자 결정 | 확인 |
| 38 | `.env.example`의 `GEMINI_API_KEY`를 `OPENAI_API_KEY`로 바꿈 | 키 이름은 `langchain-openai` 관례 | 미확인 |
| 39 | 안내 문구와 API 문서의 "Gemini"를 "AI" 또는 "LLM"으로 바꿈 | 모델이 바뀌어도 문구를 고치지 않게 | 미확인 |
| 40 | 루트 `package.json`에 `npm run dev`(백엔드+프론트 동시 실행) 추가. `concurrently` 10.0.7 고정, 루트 `/node_modules/`는 `.gitignore`에 추가 | 명령 하나로 개발 서버 실행(사용자 결정). Python 경로는 PC마다 달라 적지 않고, 실행 전 Python 환경을 켜 두는 방식 | 확인 (Mac) |
