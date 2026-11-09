# 같이 가는 제주 API

프론트에서 선택 항목과 추가 요청을 보내면 이동 약자용 제주 여행 일정을 JSON으로 돌려준다.

## 실행

- 프로젝트 루트에서 실행한다. `.env`에 `KAKAO_REST_API_KEY`, `OPENAI_API_KEY`(결제 등록된 키)가 있어야 한다.
  ```
  python -m pip install -r requirements.txt
  uvicorn backend.app.main:app --reload --port 8000
  ```
- 자동 명세 화면: `http://localhost:8000/docs` (여기서 직접 요청을 보내 볼 수 있다)
- 허용한 화면 주소(CORS): `http://localhost:3000`, `http://localhost:5173`. 다른 주소는 `backend/app/main.py`의 `ALLOWED_ORIGINS`에 추가한다.

## 주의

- 일정 1개에 20~30초 걸린다. 화면에 "만드는 중" 표시가 필요하다.
- LLM은 OpenAI `gpt-6-luna`(유료)다. 일정 1개에 LLM을 약 6번 부른다. 사용 한도는 OpenAI 계정 등급을 따른다(가장 낮은 Build 등급: 1분 5,000번, 1분 200만 토큰).

## 주소 목록

| 주소 | 하는 일 |
| --- | --- |
| `GET /health` | 서버가 켜져 있는지 확인. `{"status": "ok"}` |
| `POST /plan` | 일정 만들기 |

## `POST /plan`

### 요청

```json
{
  "departure": "제주국제공항",
  "transport": "rental_car",
  "mobility_aid": "wheelchair",
  "start_date": "2026-11-20",
  "trip_days": 1,
  "travel_styles": ["자연", "해변"],
  "message": "오전에는 덜 걷는 곳으로"
}
```

| 칸 | 필수 | 값 |
| --- | --- | --- |
| `departure` | O | 출발지 이름. 빈 글 불가, 50자 이하 |
| `transport` | O | `rental_car`(렌터카), `taxi`(택시), `bus`(버스) |
| `mobility_aid` | O | `wheelchair`(휠체어), `stroller`(유모차), `none`(사용 안 함) |
| `start_date` | O | `YYYY-MM-DD` |
| `trip_days` | O | 1~3 |
| `travel_styles` | O | 1개 이상: `자연`, `해변`, `박물관·전시`, `산책로`, `공원`, `시장` |
| `message` | X | 추가 요청, 300자 이하 |

### 응답 200 (예시, 실제 응답 아님)

```json
{
  "plan": {
    "assumptions": ["10:00 제주국제공항 출발"],
    "days": [
      {
        "day": 1,
        "items": [
          {
            "start_time": "10:00", "end_time": "10:20", "item_type": "move",
            "title": "제주국제공항 → 한라수목원", "place_id": null, "verified": null,
            "category": null, "latitude": null, "longitude": null,
            "transport": "렌터카", "duration_minutes": 19, "distance_km": 6.2,
            "accessibility_notes": []
          },
          {
            "start_time": "10:20", "end_time": "12:00", "item_type": "visit",
            "title": "한라수목원", "place_id": "P044", "verified": true,
            "category": "자연", "latitude": 33.47008467, "longitude": 126.4915112,
            "transport": null, "duration_minutes": 100, "distance_km": null,
            "accessibility_notes": ["장애인 주차장(승용차 5대, 대형 1대)", "주차장 옆 장애인 화장실(남녀 구분)"]
          }
        ]
      }
    ]
  },
  "warnings": [],
  "meta": {"model_call_count": 5, "tool_call_counts": {"search_places": 2, "get_driving_time": 4}, "elapsed_seconds": 21.1}
}
```

| 칸 | 설명 |
| --- | --- |
| `plan.assumptions` | 사용자가 정하지 않아 LLM이 정한 조건 |
| `items[].item_type` | `visit`(장소 방문), `move`(장소 사이 이동) |
| `items[].title` | `visit`이면 장소 이름(DB에 있으면 DB 이름), `move`이면 "출발지 → 도착지" |
| `items[].verified` | `visit`: DB에 있는 장소면 `true`, 없으면 `false`. `move`: `null` |
| `items[].category`, `latitude`, `longitude` | `verified`가 `true`일 때 DB 값. 그 밖에는 `null` |
| `items[].transport`, `distance_km` | `move`일 때만 값이 있음 |
| `items[].duration_minutes` | 머무는 시간 또는 이동 시간(분) |
| `items[].accessibility_notes` | 주차, 경사·동선, 화장실, 대여 등 접근성 메모 |
| `warnings` | DB에서 확인하지 못한 장소 등 알림 |
| `meta` | 확인용(LLM 호출 횟수, tool 호출 횟수, 걸린 시간). 화면에 보여 줄 필요 없음 |

## 실패 응답

모양:

```json
{"error": {"code": "invalid_request", "message": "입력값을 확인해 주세요.", "details": [{"field": "message", "reason": "300자 이하로 입력해 주세요."}]}}
```

| 상태 코드 | `code` | 상황 |
| --- | --- | --- |
| 400 | `invalid_request` | 필수 칸 없음, 빈 글, 고를 수 없는 값, 날짜 형식 틀림, 일수 범위 밖, 글자 수 초과. `details`에 칸과 이유 |
| 429 | `rate_limited` | AI 호출 한도 초과 (잠시 뒤 다시 시도) |
| 500 | `internal_error` | 그 밖의 오류 |
| 502 | `llm_invalid_response` | AI 답이 일정 형식에 맞지 않음 |
| 503 | `llm_unavailable` | AI 서버 오류 또는 연결 실패 |
| 503 | `llm_quota_exhausted` | AI 사용 잔액 부족 (관리자 확인 필요) |
| 504 | `llm_timeout` | AI 시간 초과 |
