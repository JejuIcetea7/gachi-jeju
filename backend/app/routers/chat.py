import json
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter
from langchain.agents.structured_output import StructuredOutputError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from ..agent.chat_planner import run_chat_agent
from ..agent.planner import TripPlan
from ..services.plan_validation import validate_plan_closed_days, validate_plan_times
from ..tools.places import read_closed_days_by_place_id
from .plan import (
    ErrorResponse,
    PlanBodyResponse,
    PlanMetaResponse,
    TravelStyle,
    build_error_response,
    build_llm_error_response,
    read_llm_error,
    verify_places,
)

from ..services.plan_routes import MapRoute, select_plan_routes
from ..services.plan_consistency import validate_plan_moves
from ..services.plan_evaluation import extract_tool_trace
from ..services.place_verification import restore_missing_place_ids

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_CHAT_MESSAGE_LENGTH = 1000
MAX_HISTORY_MESSAGES = 20

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=4000,
        ),
    ]

    @model_validator(mode="after")
    def validate_user_message_length(self):
        if self.role == "user" and len(self.content) > MAX_CHAT_MESSAGE_LENGTH:
            raise ValueError("사용자 메시지는 1,000자 이하로 작성해 주세요.")
        return self


class ChatOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    departure: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=50,
        ),
    ] | None = None

    departure_latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        allow_inf_nan=False,
    )
    departure_longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        allow_inf_nan=False,
    )

    transport: Literal["walking", "driving", "transit"] | None = None
    mobility_aid: Literal["wheelchair", "stroller", "none"] | None = None

    start_date: date | None = None
    trip_days: int | None = Field(
        default=None,
        ge=1,
        strict=True,
    )

    travel_styles: list[TravelStyle] = Field(
        default_factory=list,
        max_length=6,
    )
    requirements: list[
        Literal["step_free", "accessible_toilet", "accessible_parking"]
    ] = Field(
        default_factory=list,
        max_length=3,
    )
    return_to_origin: bool | None = None

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.departure_latitude is None) != (
            self.departure_longitude is None
        ):
            raise ValueError("출발지 위도와 경도는 함께 보내 주세요.")
        return self

    @model_validator(mode="after")
    def validate_trip_date_range(self):
        if self.start_date is not None and self.trip_days is not None:
            available_days = (date.max - self.start_date).days + 1

            if self.trip_days > available_days:
                raise ValueError(
                    "여행 마지막 날이 지원하는 날짜 범위를 벗어났습니다."
                )

        return self

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=MAX_CHAT_MESSAGE_LENGTH,
        ),
    ]
    history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_MESSAGES,
    )
    options: ChatOptions = Field(default_factory=ChatOptions)
    current_plan: PlanBodyResponse | None = None

def build_chat_messages(
    chat_request: ChatRequest,
    *,
    today: date | None = None,
) -> list[dict[str, str]]:
    """이전 대화와 현재 요청의 조건을 Agent에 전달할 메시지로 구성합니다."""
    reference_date = (
        today
        if today is not None
        else datetime.now(timezone(timedelta(hours=9))).date()
    )

    payload = {
        "reference_date": reference_date.isoformat(),
        "default_departure": {
            "name": "제주빅데이터센터",
            "address": "제주특별자치도 제주시 첨단로 241",
        },
        "options": chat_request.options.model_dump(mode="json"),
        "current_plan": (
            chat_request.current_plan.model_dump(mode="json")
            if chat_request.current_plan is not None
            else None
        ),
        "message": chat_request.message,
    }

    if payload["current_plan"] is not None:
        for day in payload["current_plan"]["days"]:
            for item in day["items"]:
                item["route_id"] = None

    messages = [
        message.model_dump()
        for message in chat_request.history
    ]
    messages.append({
        "role": "user",
        "content": json.dumps(payload, ensure_ascii=False),
    })
    return messages

class ChatAgentReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=4000,
        ),
    ] = Field(
        description="사용자에게 보여 줄 답변, 추가 질문 또는 일정 변경 요약",
    )
    options: ChatOptions = Field(
        description="이번 요청까지 반영한 현재 조건 전체. 미확정 값은 null 또는 빈 배열",
    )
    plan: TripPlan | None = Field(
        default=None,
        description="생성·수정한 전체 일정. 정보 확인 질문이나 일반 답변이면 null",
    )

class ChatResponse(BaseModel):
    reply: str
    options: ChatOptions
    plan: PlanBodyResponse | None
    map_routes: list[MapRoute] = Field(default_factory=list)
    warnings: list[str]
    meta: PlanMetaResponse

@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse, "description": "입력 오류"},
        429: {"model": ErrorResponse, "description": "AI 호출 한도 초과"},
        500: {"model": ErrorResponse, "description": "내부 처리 오류"},
        502: {"model": ErrorResponse, "description": "AI 응답 또는 생성 일정 오류"},
        503: {"model": ErrorResponse, "description": "AI 서버 오류 또는 잔액 부족"},
        504: {"model": ErrorResponse, "description": "AI 시간 초과"},
    },
)

def create_chat(chat_request: ChatRequest):
    """대화와 선택 옵션을 받아 질문 또는 검증한 일정을 반환합니다."""
    started_at = time.perf_counter()

    try:
        agent_result = run_chat_agent(
            build_chat_messages(chat_request),
            ChatAgentReply,
        )
    except StructuredOutputError:
        return build_error_response(
            502, "llm_invalid_response", "AI 답변 형식이 올바르지 않습니다."
        )
    except Exception as error:
        logger.exception("대화 처리 중 오류")
        status_code, provider_code = read_llm_error(error)
        return build_llm_error_response(status_code, provider_code)

    try:
        chat_reply = ChatAgentReply.model_validate(
            agent_result.get("chat_reply")
        )
    except ValidationError:
        return build_error_response(
            502, "llm_invalid_response", "AI 답변 형식이 올바르지 않습니다."
        )

    reply_text = chat_reply.reply
    plan = None
    map_routes = []
    warnings = []

    if chat_reply.plan is not None:
        missing = [
            label
            for field, label in [
                ("start_date", "출발 날짜"),
                ("trip_days", "여행 기간"),
                ("transport", "이동수단"),
            ]
            if getattr(chat_reply.options, field) is None
        ]

        if missing:
            reply_text = (
                "일정을 만들기 위해 다음 정보를 알려 주세요: "
                + ", ".join(missing)
                + "."
            )
        else:
            plan_data = chat_reply.plan.model_dump()
            errors = validate_plan_times(plan_data)
            errors.extend(
                validate_plan_moves(
                    plan_data,
                    agent_result.get("route_calls") or [],
                )
            )
            actual_days = [day.day for day in chat_reply.plan.days]

            if (
                len(actual_days) != chat_reply.options.trip_days
                or any(
                    day_number != expected
                    for expected, day_number in enumerate(actual_days, start=1)
                )
            ):
                errors.insert(
                    0,
                    f"요청한 {chat_reply.options.trip_days}일에 맞춰 "
                    "모든 일차를 순서대로 포함해야 합니다.",
                )

            if not errors:
                try:
                    tool_trace = extract_tool_trace(
                        agent_result.get("messages") or []
                    )
                    restored_plan = restore_missing_place_ids(
                        plan_data,
                        tool_trace,
                    )
                    visit_place_ids = [
                        item["place_id"]
                        for day in restored_plan["days"]
                        for item in day["items"]
                        if item.get("item_type") == "visit"
                        and item.get("place_id")
                    ]
                    closed_days_by_place_id = read_closed_days_by_place_id(
                        visit_place_ids
                    )
                except sqlite3.Error:
                    return build_error_response(
                        500, "internal_error", "장소 DB를 읽지 못했습니다."
                    )

                errors.extend(
                    validate_plan_closed_days(
                        restored_plan,
                        chat_reply.options.start_date,
                        closed_days_by_place_id,
                    )
                )

            if errors:
                return build_error_response(
                    502,
                    "plan_validation_failed",
                    "생성된 일정의 구성·시간·이동 정보에 오류가 있습니다. 다시 시도해 주세요.",
                    details=[
                        {"field": "plan", "reason": reason}
                        for reason in errors
                    ],
                )

            try:
                plan, warnings = verify_places(
                    TripPlan.model_validate(restored_plan)
                )
            except sqlite3.Error:
                return build_error_response(
                    500, "internal_error", "장소 DB를 읽지 못했습니다."
                )

    if plan is not None:
        map_routes, route_warnings = select_plan_routes(
            plan,
            agent_result.get("route_calls") or [],
        )
        warnings.extend(route_warnings)

    return {
        "reply": reply_text,
        "options": chat_reply.options,
        "plan": plan,
        "map_routes": map_routes,
        "warnings": warnings,
        "meta": {
            "model_call_count": agent_result["model_call_count"],
            "tool_call_counts": agent_result["tool_call_counts"],
            "elapsed_seconds": round(time.perf_counter() - started_at, 1),
        },
    }