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
from ..services.plan_validation import (
    validate_plan_closed_days,
    validate_plan_opening_hours,
    validate_plan_times,
)
from ..tools.places import (
    get_place_details as read_place_details,
    read_closed_days_by_place_id,
    read_opening_hours_by_place_id,
)
from ..services.plan_accessibility import validate_plan_accessibility
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
from ..services.transit_guidance import (
    TransitRouteGuidance, select_plan_transit_guidance,
)
from ..services.plan_consistency import validate_plan_moves
from ..services.route_endpoints import validate_route_endpoints
from ..services.lodging_planning import (
    LodgingStatus, LodgingStay, build_lodging_warnings, retain_lodging_state,
    UNDECIDED_LODGING_NOTE, prepare_undecided_lodging_plan,
    validate_lodging_boundaries, validate_lodging_options, validate_lodging_selections,
    prepare_lodging_retry_options,
)
from ..services.plan_evaluation import extract_tool_trace
from ..services.place_verification import restore_missing_place_ids
from ..services.plan_retry import (
    MAX_PLAN_RETRIES,
    PlanValidationFailure,
    build_plan_retry_messages,
    merge_agent_metrics,
    validate_retry_options,
)
from ..services.plan_reply_consistency import validate_plan_reply_consistency
from ..services.chat_request_control import (
    ChatRequestStopped,
    check_chat_request_control,
)

from ..services.plan_consistency import validate_plan_moves
from ..services.route_id_recovery import restore_plan_route_ids
from ..services.lodging_period import (
    prepare_lodging_period_payload,
    lodging_period_user_message,
)

router = APIRouter()
logger = logging.getLogger(__name__)

def _log_chat_schema_error(error, stage, reply=None):
    fields = []
    pending, seen = [error], set()
    while pending and len(seen) < 10:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ValidationError):
            fields.extend({
                "field": ".".join(map(str, item["loc"])) or "$",
                "type": item["type"],
                "reason": item["msg"],
            } for item in current.errors(include_url=False, include_input=False))
        pending.extend([
            getattr(current, "source", None), current.__cause__, current.__context__,
        ])
    information = {
        "stage": stage,
        "error_type": type(error).__name__,
        "fields": fields,
        "tool_name": getattr(error, "tool_name", None),
        "tool_names": getattr(error, "tool_names", None),
    }
    if stage == "response_validation":
        information["reply_type"] = type(reply).__name__
    logger.error("CHAT_SCHEMA_ERROR %s", json.dumps(information, ensure_ascii=False))

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
    lodging_status: LodgingStatus | None = Field(
        default=None,
        description="undecided: 숙소 미정, provided: 숙소 정보를 제공함, not_needed: 숙박 불필요. 관광 일정 생성은 숙소 미정이어도 가능",
    )
    lodging_stays: list[LodgingStay] = Field(
        default_factory=list,
        description="숙박일별로 사용자가 선택하거나 제공한 숙소의 확인된 위치. 검색 후보를 임의 선택하지 않음",
    )

    @model_validator(mode="after")
    def validate_lodgings(self):
        validate_lodging_options(self.trip_days, self.lodging_status, self.lodging_stays)
        return self


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

    payload = prepare_lodging_period_payload(
        payload, [message.model_dump() for message in chat_request.history],
    )
    payload["options"] = ChatOptions.model_validate(payload["options"]).model_dump(mode="json")

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
        description=(
            "이번 요청까지 반영한 현재 조건 전체. 조건 충돌 확인 질문에서는 현재 options를 유지. "
            "미확정 값은 null 또는 빈 배열"
        ),
    )
    plan: TripPlan | None = Field(
        default=None,
        description=(
            "생성·수정한 새 일정 전체. 정보 확인·조건 충돌 질문이나 일반 답변이면 반드시 null. "
            "기존 일정을 유지하며 질문할 때도 current_plan을 복사하지 않고 null을 반환하며, "
            "화면이 이전 일정과 지도를 보존함"
        ),
    )

class ChatResponse(BaseModel):
    reply: str
    options: ChatOptions
    plan: PlanBodyResponse | None
    map_routes: list[MapRoute] = Field(default_factory=list)
    transit_routes: list[TransitRouteGuidance] = Field(default_factory=list)
    warnings: list[str]
    meta: PlanMetaResponse

def _build_chat_result(
    chat_reply: ChatAgentReply, agent_result: dict, *, request_options=None, user_message="",
):
    if request_options is not None and chat_reply.options.lodging_stays:
        selection_errors = validate_lodging_selections(
            request_options, chat_reply.options.model_dump(),
            user_message=user_message,
            tool_trace=extract_tool_trace(agent_result.get("messages") or []),
        )
        if selection_errors:
            if chat_reply.plan is not None:
                raise PlanValidationFailure(selection_errors)
            return build_error_response(
                502, "plan_validation_failed", "숙소 선택 정보를 확인하지 못했습니다.",
                details=[{"field": "options.lodging_stays", "reason": reason} for reason in selection_errors],
            )
    reply_text = chat_reply.reply
    plan = None
    map_routes = []
    transit_routes = []
    warnings = []
    omitted_lodging_warnings = []

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
            plan_data, omitted_lodging_warnings = prepare_undecided_lodging_plan(
                chat_reply.plan.model_dump(),
                trip_days=chat_reply.options.trip_days,
                lodging_status=chat_reply.options.lodging_status,
                lodging_stays=[stay.model_dump() for stay in chat_reply.options.lodging_stays],
            )
            errors = validate_plan_times(plan_data)
            errors.extend(agent_result.get("place_detail_errors") or [])
            errors.extend(agent_result.get("visit_order_errors") or [])
            if not errors:
                plan_data = restore_plan_route_ids(
                    plan_data, agent_result.get("route_calls") or [],
                    tool_trace=extract_tool_trace(agent_result.get("messages") or []),
                    departure={
                        "latitude": chat_reply.options.departure_latitude,
                        "longitude": chat_reply.options.departure_longitude,
                    },
                    return_to_origin=chat_reply.options.return_to_origin is True,
                    lodging_stays=[stay.model_dump() for stay in chat_reply.options.lodging_stays],
                )
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
                errors.extend(validate_lodging_boundaries(
                    plan_data,
                    trip_days=chat_reply.options.trip_days,
                    lodging_status=chat_reply.options.lodging_status,
                    lodging_stays=[stay.model_dump() for stay in chat_reply.options.lodging_stays],
                ))

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
                    opening_hours_by_place_id = read_opening_hours_by_place_id(
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
                errors.extend(
                    validate_plan_opening_hours(
                        restored_plan,
                        chat_reply.options.start_date,
                        opening_hours_by_place_id,
                    )
                )

            if errors:
                raise PlanValidationFailure(errors)

            try:
                plan, warnings = verify_places(
                    TripPlan.model_validate(restored_plan)
                )
            except sqlite3.Error:
                return build_error_response(
                    500, "internal_error", "장소 DB를 읽지 못했습니다."
                )

    if plan is not None:
        endpoint_errors = validate_route_endpoints(
            plan,
            agent_result.get("route_calls") or [],
            departure={
                "latitude": chat_reply.options.departure_latitude,
                "longitude": chat_reply.options.departure_longitude,
            },
            return_to_origin=chat_reply.options.return_to_origin is True,
            lodging_stays=[stay.model_dump() for stay in chat_reply.options.lodging_stays],
        )
        if endpoint_errors:
            raise PlanValidationFailure(
                endpoint_errors,
                message=(
                    "생성된 일정과 이동 경로의 위치가 일치하지 않습니다. "
                    "다시 시도해 주세요."
                ),
            )

        if (
            chat_reply.options.requirements
            or chat_reply.options.mobility_aid in ("wheelchair", "stroller")
        ):
            places_by_id = {}
            place_ids = dict.fromkeys(
                item["place_id"]
                for day in plan["days"]
                for item in day["items"]
                if item["item_type"] == "visit"
                and item.get("verified") is True
                and item.get("place_id")
            )
            try:
                for place_id in place_ids:
                    details = read_place_details(place_id=place_id)
                    if (
                        not isinstance(details, dict)
                        or details.get("status") not in ("ok", "not_found")
                        or (
                            details.get("status") == "ok"
                            and details.get("place_id") != place_id
                        )
                    ):
                        return build_error_response(
                            500, "internal_error",
                            "장소 접근성 정보를 읽지 못했습니다.",
                        )
                    if details["status"] == "ok":
                        places_by_id[place_id] = details
            except sqlite3.Error:
                return build_error_response(
                    500, "internal_error",
                    "장소 접근성 정보를 읽지 못했습니다.",
                )

            access_errors, access_warnings = validate_plan_accessibility(
                plan,
                places_by_id,
                requirements=chat_reply.options.requirements,
                mobility_aid=chat_reply.options.mobility_aid,
            )
            if access_errors:
                raise PlanValidationFailure(
                    access_errors,
                    message=(
                        "생성된 일정에 필수 접근 조건을 충족하지 못하는 "
                        "장소가 있습니다. 다시 시도해 주세요."
                    ),
                )
            warnings.extend(access_warnings)
        map_routes, route_warnings = select_plan_routes(
            plan,
            agent_result.get("route_calls") or [],
        )
        warnings.extend(route_warnings)
        warnings.extend(omitted_lodging_warnings)
        if omitted_lodging_warnings:
            reply_text = (
                reply_text[:4000 - len(UNDECIDED_LODGING_NOTE) - 2].rstrip()
                + "\n\n" + UNDECIDED_LODGING_NOTE
            )
        warnings.extend(build_lodging_warnings(
            trip_days=chat_reply.options.trip_days,
            lodging_status=chat_reply.options.lodging_status,
            lodging_stays=[stay.model_dump() for stay in chat_reply.options.lodging_stays],
        ))
        transit_routes = select_plan_transit_guidance(
            plan,
            agent_result.get("route_calls") or [],
        )
        reply_errors = validate_plan_reply_consistency(
            reply_text, plan,
            options=chat_reply.options.model_dump(mode="json"),
            user_message=user_message,
        )
        if reply_errors:
            raise PlanValidationFailure(
                reply_errors,
                message="생성된 일정과 답변의 설명이 일치하지 않습니다. 다시 시도해 주세요.",
            )

    return {
        "reply": reply_text,
        "options": chat_reply.options,
        "plan": plan,
        "map_routes": map_routes,
        "transit_routes": transit_routes,
        "warnings": warnings,
    }


def create_chat(chat_request: ChatRequest):
    """검증에 실패한 일정은 조건을 유지하며 최대 한 번 다시 생성합니다."""
    check_chat_request_control()
    started_at = time.perf_counter()
    validation_message = lodging_period_user_message(
        chat_request.message, chat_request.options.model_dump(mode="json"),
        [message.model_dump() for message in chat_request.history],
    )
    original_messages = build_chat_messages(chat_request)
    messages = original_messages
    attempt_results = []
    locked_options = None
    lodging_recheck_nights = []

    for attempt in range(MAX_PLAN_RETRIES + 1):
        check_chat_request_control()
        try:
            agent_result = run_chat_agent(messages, ChatAgentReply)
        except ChatRequestStopped:
            raise
        except StructuredOutputError as error:
            _log_chat_schema_error(error, "agent")
            return build_error_response(
                502, "llm_invalid_response", "AI 답변 형식이 올바르지 않습니다."
            )
        except Exception as error:
            check_chat_request_control()
            logger.exception("대화 처리 중 오류")
            status_code, provider_code = read_llm_error(error)
            return build_llm_error_response(status_code, provider_code)

        check_chat_request_control()
        attempt_results.append(agent_result)
        try:
            chat_reply = ChatAgentReply.model_validate(
                agent_result.get("chat_reply")
            )
        except ValidationError as error:
            _log_chat_schema_error(
                error, "response_validation", agent_result.get("chat_reply"),
            )
            return build_error_response(
                502, "llm_invalid_response", "AI 답변 형식이 올바르지 않습니다."
            )

        if chat_reply.options.lodging_status is None and chat_request.options.lodging_status is not None:
            chat_reply.options = ChatOptions.model_validate(retain_lodging_state(
                chat_reply.options.model_dump(), chat_request.options.model_dump(),
            ))

        if locked_options is None and chat_reply.plan is not None:
            selected_requirements = list(dict.fromkeys([
                *chat_request.options.requirements,
                *chat_reply.options.requirements,
            ]))
            selected_aid = chat_request.options.mobility_aid
            chat_reply = chat_reply.model_copy(update={
                "options": chat_reply.options.model_copy(update={
                    "requirements": selected_requirements,
                    "mobility_aid": (
                        selected_aid if selected_aid is not None
                        else chat_reply.options.mobility_aid
                    ),
                }),
            })

        if locked_options is not None:
            option_errors = validate_retry_options(
                locked_options,
                chat_reply.options.model_dump(mode="json"),
                lodging_recheck_nights=lodging_recheck_nights,
            )
            if option_errors:
                return build_error_response(
                    502,
                    "plan_validation_failed",
                    "자동 재생성에서 여행 조건이 변경되어 일정을 제공하지 못했습니다.",
                    details=[
                        {"field": "options", "reason": reason}
                        for reason in option_errors
                    ],
                )

        try:
            body = _build_chat_result(
                chat_reply, agent_result,
                request_options=chat_request.options.model_dump(), user_message=validation_message,
            )
        except PlanValidationFailure as failure:
            check_chat_request_control()
            if attempt >= MAX_PLAN_RETRIES:
                return build_error_response(
                    502,
                    "plan_validation_failed",
                    failure.message,
                    details=[
                        {"field": "plan", "reason": reason}
                        for reason in failure.errors
                    ],
                )

            locked_options, lodging_recheck_nights = prepare_lodging_retry_options(
                chat_request.options.model_dump(mode="json"),
                chat_reply.options.model_dump(mode="json"),
                user_message=validation_message,
                tool_trace=extract_tool_trace(agent_result.get("messages") or []),
            )
            messages = build_plan_retry_messages(
                original_messages,
                failed_plan=chat_reply.plan.model_dump(mode="json"),
                options=locked_options,
                errors=failure.errors,
                lodging_recheck_nights=lodging_recheck_nights,
            )
            continue

        if isinstance(body, dict):
            body["meta"] = {
                **merge_agent_metrics(attempt_results),
                "elapsed_seconds": round(time.perf_counter() - started_at, 1),
            }
        check_chat_request_control()
        return body