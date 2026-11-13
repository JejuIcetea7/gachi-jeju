# 같이 가는 제주 API

> 현재 메인 화면은 POST /chat을 사용하는 대화형 UI입니다.
> 최신 실행 방법과 검증 상태는 [대화형 여행 플래너](chat-planner.md)를 참고합니다.
> 장소 상세 조회와 /chat의 버스 안내(transit_routes)는 위 최신 문서에 정리돼 있습니다.
> 아래 상세 요청·응답 설명은 단계형 UI와 기존 POST /plan 연동을 기준으로 작성되었습니다.

최종 검증일(기존 /plan): 2026-11-10

여행 조건과 추가 요청을 받아 추천 일정을 JSON으로 반환합니다.

## 1. 개발 실행과 접속

Python 3.12 가상환경 `.venv`가 있는 개발 환경을 기준으로 합니다.
프로젝트 루트에서 각각 별도의 터미널로 실행합니다.

**백엔드**

```cmd
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

**프론트엔드**

```cmd
npm --prefix frontend ci
npm --prefix frontend run dev
```

백엔드는 루트 `.env`의 `KAKAO_REST_API_KEY`와 `OPENAI_API_KEY`를 사용합니다.
현재 설정 로더에 맞춰 값은 따옴표 없이 입력합니다.
실제 키는 Git에 포함하지 않습니다.

자동 API 명세는 http://127.0.0.1:8000/docs 에서 확인합니다.

| 기능 | 백엔드 주소 | 프론트 개발 서버 경유 주소 |
|---|---|---|
| 상태 확인 | GET /health | GET /api/health |
| 대화형 일정 생성·수정 | POST /chat | POST /api/chat |
| 장소 상세 조회 | GET /places/{place_id}/details | GET /api/places/{place_id}/details |
| 장소 사진 목록 | GET /places/{place_id}/images | GET /api/places/{place_id}/images |
| 장소 사진 파일 | GET /places/{place_id}/images/{image_id} | GET /api/places/{place_id}/images/{image_id} |
| 기존 일정 생성 | POST /plan | POST /api/plan |

정상 상태 확인 응답은 `{"status":"ok"}`입니다.

Vite 개발 서버는 `/api` 접두사를 제거하여 8000 포트로 전달합니다.
이 프록시는 개발용 설정이며, 배포 환경의 API 연결은 별도로 구성해야 합니다.

백엔드 CORS 허용 주소는 `http://localhost:3000`과 `http://localhost:5173`입니다.

## 2. 일정 생성 요청

**요청 형식 예시**

```json
{
  "departure": "한림공원",
  "departure_latitude": 33.39022486,
  "departure_longitude": 126.2396912,
  "transport": "walking",
  "mobility_aid": null,
  "start_date": "2026-11-20",
  "trip_days": 1,
  "start_time": "13:00",
  "end_time": "17:00",
  "travel_styles": ["자연", "산책로", "공원"],
  "requirements": ["step_free", "accessible_toilet"],
  "rest_minutes": 20,
  "return_to_origin": true,
  "message": "천천히 둘러보고 싶어요."
}
```

| 필드 | 필수 여부·기본값 | 내용 |
|---|---|---|
| departure | 필수 | 출발지 이름. 공백 제거 후 1~50자 |
| departure_latitude | 선택, null | 위도. -90~90 범위의 유한한 수 |
| departure_longitude | 선택, null | 경도. -180~180 범위의 유한한 수 |
| transport | 필수 | 화면은 walking, driving, transit 사용 |
| mobility_aid | 선택, null | wheelchair, stroller, none 또는 null |
| start_date | 필수 | 여행 날짜, YYYY-MM-DD |
| trip_days | 필수 | 1~3. 현재 화면에서는 1을 전달 |
| start_time | 선택, null | 시작 시각, HH:MM |
| end_time | 선택, null | 종료 시각, HH:MM |
| travel_styles | 필수 | 자연, 해변, 박물관·전시, 산책로, 공원, 시장 중 1개 이상 |
| requirements | 선택, 빈 배열 | step_free, accessible_toilet, accessible_parking |
| rest_minutes | 선택, 0 | 중간 휴식 1회의 분 단위 길이. 0 이상의 정수 |
| return_to_origin | 선택, false | 출발지 귀환 여부 |
| message | 선택, null | 추가 요청을 합친 문자열. 최대 1,000자 |

좌표는 두 값을 함께 전달하거나 둘 다 생략합니다.
좌표가 제공되면 Agent는 해당 위치를 사용하도록 지시받습니다.
좌표가 없을 때는 출발지 이름을 검색합니다.

시작·종료 시각도 함께 전달합니다.
종료 시각은 시작 시각보다 늦어야 하며,
휴식 시간은 전체 여행 가능 시간보다 짧아야 합니다.

`mobility_aid: null`은 미선택이고, `none`은 보조기구를 사용하지 않음을 뜻합니다.
현재 화면은 별도 보조기구 선택 없이 null을 전달합니다.
시설 선택만으로 휠체어·유모차 사용 여부를 추정하지 않습니다.

기존 요청과의 호환을 위해 transport의 rental_car, taxi, bus도 허용합니다.

**화면 여행 종류 변환**

| 화면 선택 | travel_styles |
|---|---|
| 자연·산책 | 자연, 산책로, 공원 |
| 바다·풍경 | 해변 |
| 문화·관람 | 박물관·전시 |
| 시장 둘러보기 | 시장 |
| 골고루 추천 | 전체 6개 카테고리 |

등록한 사용자 요청은 앞뒤 공백과 빈 요청을 정리한 뒤 줄바꿈으로 합칩니다.
합산 길이에는 요청 사이의 줄바꿈도 포함됩니다.

## 3. 응답 구조

| 필드 | 내용 |
|---|---|
| plan.assumptions | 계획의 전제, 가정, 확인이 필요한 사항 |
| plan.days[].day | 여행 일차 |
| plan.days[].items | 시간순 방문·이동 항목 |
| warnings | 자료에서 확인하지 못한 장소 등에 대한 알림 |
| meta | 모델·도구 호출 횟수와 전체 처리 시간 |

**일정 항목**

| 필드 | 내용 |
|---|---|
| item_type | visit: 방문, move: 이동 |
| start_time, end_time | 항목의 시작·종료 시각 |
| title | 여행지 이름 또는 이동 구간 |
| place_id | 여행지 식별자. 없으면 null |
| verified | 방문 장소가 DB에 있으면 true, 확인되지 않으면 false. 이동 항목은 null |
| category, latitude, longitude | DB에서 확인한 분류·좌표. 확인되지 않으면 null |
| transport | 이동수단. 값이 없으면 null |
| duration_minutes | 체류·이동 시간(분). 성공 응답의 일정 항목에서는 시작·종료 시각의 차이와 일치하는 양의 정수 |
| distance_km | 이동 거리, km 단위. 값이 없으면 null |
| accessibility_notes | 접근 조건, 편의시설, 주의점과 미확인 사항 |

`verified`는 여행지 자료 등록 여부이며, 접근성 충족을 보장하는 값이 아닙니다.

휴식은 별도 item_type으로 만들지 않고 방문 체류시간에 포함하며,
해당 방문 항목의 accessibility_notes에 표시하도록 지시합니다.

조건에 맞는 일정이 없으면 해당 일차의 items가 빈 배열일 수 있습니다.
프론트는 빈 일정 안내와 plan.assumptions를 표시합니다.

**생성 일정의 시간 검사**

Agent가 생성한 일정을 장소 DB 확인 전에 코드로 검사합니다.

| 검사 항목 | 오류로 처리하는 경우 |
|---|---|
| 시각 형식과 순서 | HH:MM 형식이 아니거나 종료 시각이 시작 시각보다 늦지 않은 경우 |
| 여행 가능 시간 | 지정한 시작 시각보다 이르거나 종료 시각보다 늦은 항목이 있는 경우 |
| 항목 간 관계 | 앞선 일정과 시간이 겹치거나 시간순으로 배치되지 않은 경우 |
| 소요시간 | 값이 없거나 시작·종료 시각의 차이와 일치하지 않는 경우 |

검사는 일차별로 수행하며 원본 일정을 수정하지 않습니다.
여행 가능 시간을 생략한 기존 요청도 시각 형식·겹침·소요시간은 검사합니다.

종료 제한보다 일찍 마치는 일정, 항목 사이의 빈 시간,
조건에 맞는 일정을 찾지 못해 items가 빈 배열인 응답은 허용합니다.

오류가 발견되면 HTTP 502와 plan_validation_failed를 반환합니다.
잘못된 일정은 성공 응답으로 제공하지 않으며,
검증 실패를 이유로 Agent를 자동 재실행하지 않습니다.

## 4. 오류와 응답 대기

**입력 오류 예시**

```json
{
  "error": {
    "code": "invalid_request",
    "message": "입력값을 확인해 주세요.",
    "details": [
      {
        "field": "message",
        "reason": "1000자 이하로 입력해 주세요."
      }
    ]
  }
}
```

details는 필요한 경우에만 제공됩니다.
여러 입력의 관계를 검사하다 발생한 오류는 field가 body일 수 있습니다.

| HTTP 상태 | code | 의미 |
|---|---|---|
| 400 | invalid_request | 필수값·형식·범위·입력 조건 오류 |
| 429 | rate_limited | AI 호출 한도 초과 |
| 500 | internal_error | 내부 처리 오류 |
| 502 | llm_invalid_response | AI 응답을 일정 형식으로 받지 못함 |
| 502 | plan_validation_failed | 생성 일정의 시간 범위·순서·겹침·소요시간 검증 실패 |
| 503 | llm_unavailable | AI 서버 오류 또는 연결 실패 |
| 503 | llm_quota_exhausted | AI 사용 잔액 부족 |
| 504 | llm_timeout | AI 요청 시간 초과 |

plan_validation_failed의 details는 field가 plan이며,
reason에 문제가 발생한 일차·항목과 이유를 제공합니다.
프론트는 해당 내용을 ‘생성된 일정’으로 표시합니다.

프론트 요청 함수는 다음 오류도 구분합니다.

| 프론트 오류 code | 의미 |
|---|---|
| network_error | 서버 연결 실패 |
| invalid_response | JSON 또는 일정 응답 형식 오류 |
| client_timeout | 프론트의 3분 응답 대기 제한 초과 |

3분 제한은 클라이언트의 응답 대기 기준이며 서버 작업 취소를 보장하지 않습니다.
프론트는 자동으로 재요청하지 않습니다.

일정 생성 중에는 생성·수정·삭제 버튼과 추가 요청 입력을 잠급니다.
오류 발생 후에는 안내를 표시하고 사용자가 다시 시도할 수 있습니다.

## 5. 검증 상태와 한계

2026-11-10 기준 확인 결과입니다.

| 구분 | 확인 내용 |
|---|---|
| 자동 테스트 | 전체 Python 테스트 73개 통과. 일정 API·입력 전달·시간 검증 관련 27개 포함 |
| 프론트 검사 | ESLint와 프로덕션 빌드 통과 |
| 서버 연결 | 기존 연동 검증에서 직접 /health 및 프록시 경유 /api/health 모두 200 OK |
| 실제 생성 | 시간 검증 추가 전 자동차·당일 일정의 생성과 시간표 표시 확인 |
| 브라우저 동작 | 기존 연동 검증에서 생성 중 표시·입력 잠금·새로고침 후 결과 유지 확인 |

시간 검증 함수의 정상·오류 사례를 자동 테스트했습니다.
Agent를 mock으로 대체한 API 테스트에서는 시간 오류의 HTTP 502 반환,
장소 DB 확인 전 차단, Agent 자동 재실행 방지를 확인했습니다.
일찍 종료하는 정상 일정과 설명이 포함된 빈 일정의 성공 응답도 확인했습니다.

시간 검증 추가 후 실제 LLM을 통한 일정 생성과
시간 검증 오류 화면의 브라우저 확인은 아직 수행하지 않았습니다.
도보·대중교통의 실제 Agent 일정 생성과 2~3일 일정도 이번 검증에 포함하지 않았습니다.

현재 검증은 시간표 내부의 일관성과 지정한 시간 범위를 확인합니다.
이동시간과 원래 도구 조회값의 일치, 귀환 장소·휴식 반영·운영시간 준수,
필수 접근 조건의 충족 근거는 아직 코드로 검증하지 않습니다.
assumptions에 적힌 자연어 설명과 시간표의 일치도 검사하지 않습니다.

사용자 추가 요청은 모아서 일정 생성에 전달합니다.
기존 일정을 대화로 부분 수정하는 기능은 아직 구현하지 않았습니다.
