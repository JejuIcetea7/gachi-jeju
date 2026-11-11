# Agent 파이프라인 결정 목록

1차 파이프라인(LLM + tool) 구현 중 미룬 것과 상의 없이 정한 것이다. 확인되면 상태를 `확인`으로 바꾼다.

최신 구현·검증 현황은 [대화형 여행 플래너와 이동 경로 지도](chat-planner.md)를 참고합니다.
아래 표에는 기존 결정과 아직 확인하지 않은 항목도 함께 남아 있습니다.

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
| 8 | 대화로 일정 수정하기 | 2026-11-11 기준 `POST /chat`에서 이전 대화·선택 조건·기존 일정을 반영해 전체 일정을 수정합니다. 장소 제외, 방문 시간 변경, 경로 표시와 새로고침 복원을 수동 확인했습니다. | 완료 |
| 9 | Gemini 호출 오류(한도 초과 등) 처리 | API에서 429·503·504·502로 구분해 돌려줌 (임의 결정 32, 33) | 완료 |
| 10 | Agent 실제 실행 확인 (5단계, 예시 요청 3개) | 2026-11-11 기준 `/chat`에서 자동차·도보·버스 경로 표시, 후속 수정·복원, 2박 3일 날짜 전환을 확인했습니다. 유모차·숙박·야간 이동은 미확인입니다. 2026-11-11 평가 세트 기준선: 필수 조건 통과 15/24, [실험 0001](experiments/0001-chat-agent-baseline-eval.md). 이전 기록: 2026-11-09 `gpt-6-luna`의 `/plan` 휠체어·렌터카·1일 요청 성공(200, 30.3초, 장소 3곳 `verified`). Gemini 3.8 Flash 503·504, 3.6 Flash 하루 20번 한도 | 진행 중 |
| 11 | 503이 계속될 때 다른 모델로 넘어가기, 재시도 대상에서 429 빼기 | 1차는 재시도만 둠 | 취소 (OpenAI로 변경) |
| 12 | 요청·tool 호출·응답 기록 남기기 | 평가 스크립트 실행 때만 `outputs/chat_eval/.../runs.jsonl`에 기록 (2026-11-11). 서버(`/chat`)의 평소 요청 기록은 아직 없음 | 부분 완료 |
| 13 | 일정 수정용 대화 ID | 현재 `/chat`은 요청의 `history`와 `current_plan`으로 후속 수정을 지원합니다. 별도 서버 대화 ID와 사용자별 영구 저장은 구현하지 않았습니다. | 미정 |
| 14 | 하루 한도(429 일일)와 1분 한도 구분해 안내 | 1차는 같은 문구로 안내 | 취소 (OpenAI로 변경) |
| 15 | Gemini 무료 한도 늘리기(결제 등록) 검토 | `gemini-3.6-flash` 무료 하루 20번 → 일정 2~3개/일 (2026-11-09 서버 오류로 확인) | 취소 (OpenAI로 변경) |
| 16 | 일정 시간 검증: 항목의 시각·여행 가능 시간·겹침·duration_minutes 일치, assumptions의 시각 설명 대조 | 2026-11-10 시간표 검증과 API 오류 반환 구현·자동 테스트 통과. assumptions의 자연어 설명 대조는 남음 | 부분 완료 |
| 17 | 이동시간이 tool 결과와 같은지 확인할 방법 | `/chat`의 `map_routes`에 선택된 경로의 조회 거리·시간을 보존합니다. 평가 스크립트의 H6이 일정표 값과 도구 결과를 자동 대조합니다(2026-11-11, [실험 0001](experiments/0001-chat-agent-baseline-eval.md)). 서버 응답 단계의 대조와 `/plan`의 조회 기록은 아직 없습니다. | 부분 완료 |

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
| 41 | 평가 파일: 요청 `scripts/eval_cases/chat_cases.json`, 채점 함수 `backend/app/services/plan_evaluation.py`, 실행 `python -B -m scripts.evaluate_chat_agent --label 이름표`, 결과 `outputs/chat_eval/<날짜-시각>_<이름표>/`(`runs.jsonl`, `summary.csv`, `report.md`) | 채점 함수는 테스트하기 쉽게 `plan_validation.py` 옆에 둠. 결과 폴더는 git 제외, 실험 문서에 쓴 폴더만 `git add -f` (사용자 결정) | 확인 |
| 42 | `run_chat_agent` 반환값에 `messages`(이번 실행의 Agent 메시지) 추가 | 평가에서 도구 호출·결과를 꺼내기 위해. 기존 값은 그대로라 `/chat` 응답 영향 없음 | 확인 |
| 43 | H1: 서버(`/chat`)처럼 날짜·일수·이동수단 중 빈 값이 있는 일정은 "전달 안 됨"으로 봄 | 사용자가 실제로 받는 결과 기준 | 미확인 |
| 44 | H6: 초→분 올림 후 ±1분 허용. `route_id`가 있으면 그 조회 결과와, 없으면 이번 실행 이동 도구 결과 중 같은 값이 있는지 비교. 없는 `route_id`는 실패 | 반올림 차이로 인한 오탐 방지. 지어낸 `route_id`는 지도 경로가 안 그려짐 | 미확인 |
| 45 | H7 금지 조합: 도보→자동차·버스 도구, 자동차→버스 도구, 대중교통→자동차 도구. 자동차·대중교통의 도보 도구는 허용 | 짧은 도보 구간은 자연스러움 | 미확인 |
| 46 | 데이터 충족도 근거 값: 계단 피하기 = `has_slope_or_stairs`가 `N`, 장애인 화장실·주차 = 각 칸이 `Y` | DB 칸 의미 그대로. 지금 `N` 값이 없어 계단 피하기는 0% | 미확인 |
| 47 | 평가 요청 출발지 좌표는 카카오 장소 검색 결과(제주국제공항, 제주시청). C05·C06은 출발지를 비워 기본 출발지 사용. `runs.jsonl`에는 지도 경로 좌표(`paths`)를 빼고 저장 | 화면처럼 좌표를 보내는 경우와 비워 두는 경우를 모두 시험. 좌표는 크기가 커서 뺌 | 미확인 |
| 48 | H9(빈 일차): 일정이 전달됐는데 방문 0곳인 일차가 있으면 실패. "하루 방문 1곳 이하" 일차 수는 기록용 지표(실패 아님) | 기준선에서 방문 0곳 일정이 H1~H8을 모두 통과함 ([실험 0001](experiments/0001-chat-agent-baseline-eval.md)). 얇은 일정의 기준은 아직 정하지 않음 | 확인 |
| 49 | C05·C06 숙소를 정확한 주소로 바꿈(제주시청·서귀포시청 제1청사 주소를 숙소 주소로 사용). 흐릿한 숙소("서귀포 시내")는 C09로 분리하고 기대 결과를 되묻기로 둠 | 한 요청이 여러 날 일정과 되묻기를 동시에 시험하지 않게 (사용자 결정) | 확인 |
| 50 | `--rescore <폴더>`: 저장된 `runs.jsonl`을 지금 채점 함수로 다시 채점해 `runs.jsonl`의 평가 칸, `summary.csv`, `report.md`를 덮어씀. 채운 R1~R6 값은 유지. 기록에 없는 기대 결과·시간 범위는 지금 요청 파일에서 채움 | AI를 다시 부르지 않고 점검 항목 변경을 반영 | 미확인 |
