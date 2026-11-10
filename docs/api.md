# 같이 가는 제주 API

최종 검증일: 2026-11-10

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
| 일정 생성 | POST /plan | POST /api/plan |

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
| duration_minutes | 체류·이동 시간, 분 단위. 값이 없으면 null |
| distance_km | 이동 거리, km 단위. 값이 없으면 null |
| accessibility_notes | 접근 조건, 편의시설, 주의점과 미확인 사항 |

`verified`는 여행지 자료 등록 여부이며, 접근성 충족을 보장하는 값이 아닙니다.

휴식은 별도 item_type으로 만들지 않고 방문 체류시간에 포함하며,
해당 방문 항목의 accessibility_notes에 표시하도록 지시합니다.

조건에 맞는 일정이 없으면 해당 일차의 items가 빈 배열일 수 있습니다.
프론트는 빈 일정 안내와 plan.assumptions를 표시합니다.

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
| 503 | llm_unavailable | AI 서버 오류 또는 연결 실패 |
| 503 | llm_quota_exhausted | AI 사용 잔액 부족 |
| 504 | llm_timeout | AI 요청 시간 초과 |

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
| 자동 테스트 | 전체 Python 테스트 58개 통과. 입력 검증·조건 전달·오류 처리 테스트 포함 |
| 프론트 검사 | ESLint와 프로덕션 빌드 통과 |
| 서버 연결 | 직접 /health 및 개발 프록시 경유 /api/health 모두 200 OK |
| 실제 생성 | 자동차·당일 일정의 생성과 시간표 표시 확인 |
| 브라우저 동작 | 생성 중 표시·입력 잠금·새로고침 후 결과 유지 확인 |

도보·대중교통을 사용한 실제 Agent 일정 생성과 2~3일 일정은 이번 검증에 포함하지 않았습니다.

입력의 시간 관계와 휴식 길이는 코드에서 검사합니다.
생성 결과의 시간 충돌, 도구 조회값과의 일치, 접근 조건 충족 근거를
코드로 다시 검증하는 단계는 아직 구현하지 않았습니다.

사용자 추가 요청은 모아서 일정 생성에 전달합니다.
기존 일정을 대화로 부분 수정하는 기능은 아직 구현하지 않았습니다.