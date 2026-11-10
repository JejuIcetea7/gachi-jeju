import logging
import sqlite3
import time
from datetime import date
from typing import Annotated, Literal

import openai
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from langchain.agents.structured_output import StructuredOutputError
from pydantic import BaseModel, Field, StringConstraints, model_validator

from ..agent.planner import TripPlan, plan_trip
from ..tools.places import read_rows

router = APIRouter()
logger = logging.getLogger(__name__)

TRANSPORT_LABELS = {
    "rental_car": "렌터카",
    "taxi": "택시",
    "bus": "버스",
    "driving": "자동차",
    "walking": "도보",
    "transit": "대중교통(버스·도보)",
}
MOBILITY_AID_LABELS = {
    "wheelchair": "휠체어",
    "stroller": "유모차",
    "none": "사용 안 함",
    None: "미선택",
}
TravelStyle = Literal["자연", "해변", "박물관·전시", "산책로", "공원", "시장"]
TimeText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=5,
        max_length=5,
        pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$",
    ),
]

# ---------- 요청 · 응답 모델 ----------

class PlanRequest(BaseModel):
    departure: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    ] = Field(description="출발지 이름")

    departure_latitude: float | None = Field(
        default=None, ge=-90, le=90, allow_inf_nan=False,
        description="확정한 출발지 위도",
    )
    departure_longitude: float | None = Field(
        default=None, ge=-180, le=180, allow_inf_nan=False,
        description="확정한 출발지 경도",
    )

    transport: Literal[
        "rental_car", "taxi", "bus", "driving", "walking", "transit"
    ] = Field(description="이동수단")

    mobility_aid: Literal["wheelchair", "stroller", "none"] | None = Field(
        default=None, description="이동 보조기구. 선택하지 않았다면 null",
    )

    start_date: date = Field(description="여행 날짜")
    trip_days: int = Field(ge=1, le=3, description="여행 일수. 현재 화면은 1일")
    start_time: TimeText | None = Field(default=None, description="시작 시각 HH:MM")
    end_time: TimeText | None = Field(default=None, description="종료 시각 HH:MM")

    travel_styles: list[TravelStyle] = Field(
        min_length=1, description="원하는 여행 스타일",
    )
    requirements: list[
        Literal["step_free", "accessible_toilet", "accessible_parking"]
    ] = Field(default_factory=list, description="필수 이용 조건")
    rest_minutes: int = Field(default=0, ge=0, description="중간 휴식 1회, 분")
    return_to_origin: bool = Field(default=False, description="출발지로 돌아오기")

    message: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=1000),
    ] | None = Field(default=None, description="추가 요청을 합친 글, 최대 1,000자")

    @model_validator(mode="after")
    def validate_related_fields(self):
        if (self.departure_latitude is None) != (self.departure_longitude is None):
            raise ValueError("출발지 위도와 경도는 함께 보내 주세요.")

        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("시작 시각과 종료 시각은 함께 보내 주세요.")

        if self.start_time is not None and self.end_time is not None:
            if self.end_time <= self.start_time:
                raise ValueError("종료 시각은 시작 시각보다 늦어야 합니다.")

            start_hour, start_minute = map(int, self.start_time.split(":"))
            end_hour, end_minute = map(int, self.end_time.split(":"))
            available_minutes = (
                end_hour * 60 + end_minute
                - start_hour * 60 - start_minute
            )
            if self.rest_minutes >= available_minutes:
                raise ValueError("휴식 시간은 전체 여행 시간보다 짧아야 합니다.")

        return self

class PlanItemResponse(BaseModel):
    start_time: str
    end_time: str
    item_type: Literal["visit", "move"]
    title: str
    place_id: str | None
    verified: bool | None = Field(description="visit: DB에 있는 장소면 true, 없으면 false. move: null")
    category: str | None
    latitude: float | None
    longitude: float | None
    transport: str | None
    duration_minutes: int | None
    distance_km: float | None
    accessibility_notes: list[str]

class PlanDayResponse(BaseModel):
    day: int
    items: list[PlanItemResponse]

class PlanBodyResponse(BaseModel):
    assumptions: list[str]
    days: list[PlanDayResponse]

class PlanMetaResponse(BaseModel):
    model_call_count: int
    tool_call_counts: dict[str, int]
    elapsed_seconds: float

class PlanResponse(BaseModel):
    plan: PlanBodyResponse
    warnings: list[str]
    meta: PlanMetaResponse

class ErrorDetail(BaseModel):
    field: str
    reason: str

class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None

class ErrorResponse(BaseModel):
    error: ErrorBody

# ---------- ① 입력 정리 ----------

def build_user_message(plan_request: PlanRequest) -> str:
    """화면의 선택 조건과 추가 요청을 Agent에 전달할 문장으로 바꿉니다."""
    requirement_labels = {
        "step_free": "계단 피하기",
        "accessible_toilet": "장애인 화장실",
        "accessible_parking": "장애인 주차구역",
    }

    lines = [
        f"출발지: {plan_request.departure}",
        f"이동수단: {TRANSPORT_LABELS[plan_request.transport]}",
        f"이동 보조기구: {MOBILITY_AID_LABELS[plan_request.mobility_aid]}",
        f"출발 날짜: {plan_request.start_date.isoformat()}",
        f"여행 일수: {plan_request.trip_days}일",
        f"여행 스타일: {', '.join(plan_request.travel_styles)}",
    ]

    if (
        plan_request.departure_latitude is not None
        and plan_request.departure_longitude is not None
    ):
        lines.append(
            "확정한 출발지 좌표: "
            f"위도 {plan_request.departure_latitude}, "
            f"경도 {plan_request.departure_longitude}"
        )

    if plan_request.start_time is not None and plan_request.end_time is not None:
        lines.append(
            f"여행 가능 시간: {plan_request.start_time} ~ {plan_request.end_time}"
        )

    requirements = [
        requirement_labels[item] for item in plan_request.requirements
    ]
    lines.append(
        "필수 이용 조건: "
        + (", ".join(requirements) if requirements else "별도 지정 없음")
    )

    if plan_request.rest_minutes > 0:
        lines.append(f"중간 휴식: {plan_request.rest_minutes}분, 1회")
    else:
        lines.append("중간 휴식: 없음")

    lines.append(
        "여행 종료: "
        + (
            "출발지로 돌아오기"
            if plan_request.return_to_origin
            else "마지막 여행지에서 마치기"
        )
    )

    if plan_request.message:
        lines.append(f"추가 요청: {plan_request.message}")

    return "\n".join(lines)

# ---------- ③ 출력 정리 ----------

def find_place_in_database(place_id: str) -> dict | None:
    query = "SELECT place_id, name, category, latitude, longitude FROM places WHERE place_id = ?"
    rows = read_rows(query, (place_id,))

    place = None
    if len(rows) > 0:
        place = dict(rows[0])
    return place

def verify_places(trip_plan: TripPlan) -> tuple[dict, list[str]]:
    """visit 항목의 place_id를 DB에서 확인하고, 이름·카테고리·좌표를 DB 값으로 바꿉니다."""
    plan = trip_plan.model_dump()
    warnings = []

    for day in plan["days"]:
        for item in day["items"]:
            item["verified"] = None
            item["category"] = None
            item["latitude"] = None
            item["longitude"] = None
            if item["item_type"] != "visit":
                continue

            item["verified"] = False
            place_id = item["place_id"]
            if not place_id:
                warnings.append(f"{day['day']}일차 '{item['title']}': place_id가 없어 DB에서 확인하지 못했습니다.")
                continue

            place = find_place_in_database(place_id)
            if place is None:
                warnings.append(f"{day['day']}일차 '{item['title']}': place_id {place_id}가 DB에 없습니다.")
                continue

            item["verified"] = True
            item["title"] = place["name"]
            item["category"] = place["category"]
            item["latitude"] = place["latitude"]
            item["longitude"] = place["longitude"]

    return plan, warnings

# ---------- 실패 응답 ----------

def build_error_response(status_code: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    error_body = {"code": code, "message": message}
    if details is not None:
        error_body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error_body})

def read_llm_error(error: Exception) -> tuple[int | None, str | None]:
    """LLM(OpenAI) 호출 오류에서 HTTP 상태 코드와 OpenAI 오류 코드를 찾습니다. 시간 초과는 504로 봅니다."""
    status_code = None
    provider_error_code = None
    current_error = error
    while current_error is not None:
        if isinstance(current_error, openai.APITimeoutError):
            status_code = 504
            break
        if isinstance(current_error, openai.APIConnectionError):
            status_code = 503
            break
        if isinstance(current_error, openai.APIStatusError):
            status_code = current_error.status_code
            provider_error_code = current_error.code
            break
        current_error = current_error.__cause__
    return status_code, provider_error_code

def build_llm_error_response(status_code: int | None, provider_error_code: str | None) -> JSONResponse:
    if provider_error_code == "insufficient_quota":
        return build_error_response(503, "llm_quota_exhausted", "AI 사용 잔액이 부족합니다. 관리자에게 알려 주세요.")
    if status_code == 429:
        return build_error_response(429, "rate_limited", "AI 호출 한도를 넘었습니다. 잠시 뒤 다시 시도해 주세요.")
    if status_code is not None and status_code >= 500 and status_code != 504:
        return build_error_response(503, "llm_unavailable", "AI 서버가 응답하지 않습니다. 잠시 뒤 다시 시도해 주세요.")
    if status_code == 504:
        return build_error_response(504, "llm_timeout", "AI 응답이 너무 늦어 중단했습니다. 잠시 뒤 다시 시도해 주세요.")
    return build_error_response(500, "internal_error", "일정을 만들지 못했습니다.")

# ---------- API ----------

@router.post(
    "/plan",
    response_model=PlanResponse,
    responses={
        400: {"model": ErrorResponse, "description": "입력 오류"},
        429: {"model": ErrorResponse, "description": "AI 호출 한도 초과"},
        500: {"model": ErrorResponse, "description": "그 밖의 오류"},
        502: {"model": ErrorResponse, "description": "AI 답이 일정 형식에 맞지 않음"},
        503: {"model": ErrorResponse, "description": "AI 서버 오류 또는 사용 잔액 부족"},
        504: {"model": ErrorResponse, "description": "AI 시간 초과"},
    },
)
def create_plan(plan_request: PlanRequest):
    """선택 항목과 추가 요청을 받아 이동 약자용 제주 여행 일정을 만듭니다. 20~30초 걸립니다."""
    started_at = time.time()
    user_message = build_user_message(plan_request)

    try:
        agent_result = plan_trip(user_message)
    except StructuredOutputError:
        return build_error_response(502, "llm_invalid_response", "AI 답이 일정 형식에 맞지 않습니다.")
    except Exception as error:
        logger.exception("일정 생성 중 오류")
        status_code, provider_error_code = read_llm_error(error)
        return build_llm_error_response(status_code, provider_error_code)

    trip_plan = agent_result["trip_plan"]
    if trip_plan is None:
        return build_error_response(502, "llm_invalid_response", "AI 답이 일정 형식에 맞지 않습니다.")

    try:
        plan, warnings = verify_places(trip_plan)
    except sqlite3.Error:
        return build_error_response(500, "internal_error", "장소 DB를 읽지 못했습니다.")

    elapsed_seconds = round(time.time() - started_at, 1)
    return {
        "plan": plan,
        "warnings": warnings,
        "meta": {
            "model_call_count": agent_result["model_call_count"],
            "tool_call_counts": agent_result["tool_call_counts"],
            "elapsed_seconds": elapsed_seconds,
        },
    }
