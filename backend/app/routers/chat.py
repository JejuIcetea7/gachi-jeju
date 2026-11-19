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
from ..config import (
    DEFAULT_DEPARTURE_ADDRESS,
    DEFAULT_DEPARTURE_LATITUDE,
    DEFAULT_DEPARTURE_LONGITUDE,
    DEFAULT_DEPARTURE_NAME,
)
from ..services.plan_validation import (
    ScheduleChange,
    VisitOrder,
    read_fixed_trip_options,
    read_schedule_constraints,
    validate_schedule_constraints,
    validate_plan_closed_days,
    validate_plan_opening_hours,
    validate_plan_times,
)
from ..tools.places import (
    get_place_details as read_place_details,
    read_closed_days_by_place_id,
    read_opening_hours_by_place_id,
)
from ..services.plan_accessibility import (
    resolve_facility_status,
    validate_plan_accessibility,
)
from ..services.chat_route_points import read_confirmed_restaurants, restaurant_lookup_failure, validate_plan_restaurants
from ..services.opening_hours import TIME_PATTERN, find_opening_hours, check_visit_time_window, ALWAYS_OPEN
from ..services.closed_days import STATUS_CLOSED
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
    LodgingStatus, LodgingStay, build_lodging_warnings,
    validate_lodging_boundaries, validate_lodging_options, validate_lodging_selections,
    prepare_lodging_retry_options,
)
from ..services.plan_evaluation import extract_tool_trace
from ..services.place_verification import find_unique_exact_place_id, restore_missing_place_ids
from ..services.plan_retry import (
    MAX_PLAN_RETRIES,
    PlanValidationFailure,
    build_plan_retry_messages,
    find_changed_options,
    merge_agent_metrics,
    validate_retry_options,
)
from ..services.chat_request_control import (
    ChatRequestStopped,
    check_chat_request_control,
)

from ..services.route_id_recovery import restore_plan_route_ids

router = APIRouter()
logger = logging.getLogger(__name__)

def read_schema_error_fields(error):
    """구조화 응답 오류에서 필드별 형식 오류를 읽습니다. 입력 값은 포함하지 않습니다."""
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
    return fields


def read_schema_error_reasons(error):
    """같은 Agent에 돌려줄 응답 형식 오류 설명을 만듭니다."""
    reasons = []
    for field in read_schema_error_fields(error):
        reasons.append(f"응답 형식 오류 {field['field']}: {field['reason']}")
    if not reasons:
        reasons.append("응답 스키마에 맞는 구조화 응답 하나를 반환하세요.")
    return reasons


def _log_chat_schema_error(error, stage, reply=None):
    fields = read_schema_error_fields(error)
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

    options = chat_request.options.model_dump(mode="json")
    if (
        options["departure"] == DEFAULT_DEPARTURE_NAME
        and options["departure_latitude"] is None
        and options["departure_longitude"] is None
    ):
        options["departure_latitude"] = DEFAULT_DEPARTURE_LATITUDE
        options["departure_longitude"] = DEFAULT_DEPARTURE_LONGITUDE

    payload = {
        "reference_date": reference_date.isoformat(),
        "default_departure": {
            "name": DEFAULT_DEPARTURE_NAME,
            "address": DEFAULT_DEPARTURE_ADDRESS,
        },
        "options": options,
        "current_plan": (
            chat_request.current_plan.model_dump(mode="json")
            if chat_request.current_plan is not None
            else None
        ),
        "message": chat_request.message,
    }

    payload["options"] = ChatOptions.model_validate(payload["options"]).model_dump(mode="json")

    if payload["current_plan"] is not None:
        for day in payload["current_plan"]["days"]:
            for item in day["items"]:
                item["route_id"] = None
                item.pop("restaurant_ref", None)
                if item.get("restaurant_address"):
                    item["schedule_key"] = f"식당 {item['title']} ({item['restaurant_address']})"

    messages = [
        message.model_dump()
        for message in chat_request.history
    ]
    messages.append({
        "role": "user",
        "content": json.dumps(payload, ensure_ascii=False),
    })
    return messages

class VisitTimeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place_id: str = Field(min_length=1, max_length=80)
    visit_date: date
    arrival_time: str = Field(pattern=f"^{TIME_PATTERN}$")
    duration_minutes: int | None = Field(
        default=None, ge=1, le=1440, strict=True,
        description="요청이나 기존 일정에서 확인할 수 있는 관람 시간(분). 미정이면 null",
    )
    available: bool | None = Field(default=None, description=(
        "운영시간·휴무로 판단한 방문 가능 여부. 휴무·개장 전 도착·입장 마감 후 도착·폐장 후 종료면 false, "
        "운영시간 안이거나 상시 개방이면 true, 운영시간 미확인·DB에 없는 장소·상시 개방의 자정 초과 관람이면 null. 서버가 운영정보와 대조함"))


class FacilityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: Literal["accessible_toilet", "accessible_parking", "step_free", "wheelchair", "stroller", "other"]
    status: Literal["Y", "N", "unknown"]


class AccessibilityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place_name: str | None = Field(default=None, min_length=1, max_length=100,
        description="질문 대상의 정확한 장소명. 어떤 장소인지 불명확하면 null")
    topics: list[Literal["accessible_toilet", "accessible_parking", "step_free", "wheelchair", "stroller", "other"]] = Field(min_length=1, max_length=6)
    evidence_labels: list[str] = Field(default_factory=list, max_length=4,
        description="질문에 관련된 현장 지점 이름(spots.name)이나 조사 항목명(accessibility_facts.description)")
    findings: list[FacilityFinding] = Field(default_factory=list, max_length=6, description=(
        "답변에 쓴 확인 값. topics마다 하나. accessible_toilet·accessible_parking·step_free는 집계값과 시설 원문이 "
        "일치할 때만 Y·N, 그 외와 DB에 없는 장소, wheelchair·stroller·other는 unknown. 서버가 조회 자료와 대조함"))


class RequestAction(BaseModel):
    subject: Literal["meal", "lodging", "itinerary", "options"]
    action: Literal["inquire", "optional", "keep", "add", "remove", "change", "clarify"]
    evidence: str = Field(min_length=1, max_length=1000, description="이번 사용자 원문의 연속된 구절. 대상·행동·부정·가정까지 포함")
    target_name: str | None = Field(default=None, min_length=1, max_length=120,
        description="기존 식당·숙소를 지목했으면 원문에 적힌 이름, 일반적인 '점심' 또는 '숙소'는 null")


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
    visit_time_check: VisitTimeCheck | None = Field(
        default=None,
        description="일정을 바꾸지 않고 특정 장소의 특정 날짜·시각에 방문 가능한지 묻는 요청의 조건",
    )
    accessibility_checks: list[AccessibilityCheck] = Field(default_factory=list, max_length=12,
        description="장소별 시설·접근성 정보 문의. 외부 식당·숙소나 자료가 없는 장소도 기록")
    schedule_changes: list[ScheduleChange] = Field(default_factory=list, max_length=100, description=(
        "이번 요청이 바꾼 방문·시간 조건만 기록. 바꾸지 않은 고정 조건은 서버가 보존. remove·move 대상은 current_plan의 방문. "
        "식사 추가는 visit=@meal·kind=add, 식당 교체는 기존 식사 remove와 새 식사 add, 유지한 식당의 시각 변경은 시간 조건만 기록. "
        "같은 대상에 함께 지킬 수 없는 조건이 있으면 확인 질문"))
    visit_orders: list[VisitOrder] = Field(default_factory=list, max_length=30, description="사용자가 명시한 일차별 방문 순서")
    replace_visits: bool = Field(default=False, description="사용자가 기존 관광지를 전부 새로 구성하도록 요청했을 때만 true")
    needs_clarification: bool = Field(default=False, description="조건 부족·충돌 또는 모호함으로 사용자 확인이 필요한 응답")
    period_confirmed: bool = Field(default=False, description="사용자가 여행 기간 연장을 명시했거나 이전 질문에 동의한 경우 true")
    restaurant_retry_request: str | None = Field(default=None, min_length=1, max_length=1000, description=(
        "식당 조회가 api_error·rate_limited일 때 다시 보낼 사용자 식사 요청 문장 전체의 원문. auth_error·not_found·정상 조회면 null"))
    changed_options: list[Literal[
        "departure", "transport", "mobility_aid", "start_date", "trip_days",
        "travel_styles", "requirements", "return_to_origin", "lodging_status", "lodging_stays",
    ]] = Field(default_factory=list, description=(
        "이번 요청 또는 대화에서 새로 정하거나 바꾸거나 취소한 options 필드. 일정 생성에도 기록하며, 비어 있던 값을 처음 채운 경우도 포함. "
        "departure는 이름·두 좌표를 함께, 숙박 변경은 lodging_status·lodging_stays를 함께 기록. "
        "선언하지 않은 필드는 현재 options 값을 그대로 복사하며, 다르면 서버가 기대값·실제값을 돌려줌"))
    request_actions: list[RequestAction] = Field(default_factory=list, max_length=16,
        description="식사·숙박 관련 발화의 대상별 행동과 원문. 동시에 요청한 별도 일정·옵션 변경도 각각 기록")

class ChatResponse(BaseModel):
    reply: str
    options: ChatOptions
    plan: PlanBodyResponse | None
    map_routes: list[MapRoute] = Field(default_factory=list)
    transit_routes: list[TransitRouteGuidance] = Field(default_factory=list)
    warnings: list[str]
    meta: PlanMetaResponse
    restaurant_retry_request: str | None = Field(default=None, min_length=1, max_length=1000, exclude_if=lambda value: value is None)


SERVER_CHECKED_FACILITY_TOPICS = ("accessible_toilet", "accessible_parking", "step_free")
TEMPORARY_RESTAURANT_FAILURES = ("api_error", "rate_limited")
STOPPED_RESTAURANT_FAILURES = ("api_error", "rate_limited", "auth_error")


def format_findings(findings):
    parts = []
    for topic, status in findings.items():
        parts.append(f"{topic}={status}")
    if not parts:
        return "없음"
    return ", ".join(parts)


def read_facility_details(place_name):
    """정확히 하나로 확인되는 DB 장소의 상세 정보를 읽고, 없으면 None을 반환합니다."""
    place_id = find_unique_exact_place_id(place_name)
    if place_id is None:
        return None
    details = read_place_details(place_id=place_id)
    status = details.get("status") if isinstance(details, dict) else None
    if status == "not_found":
        return None
    if status != "ok" or details.get("place_id") != place_id:
        raise sqlite3.OperationalError("시설 검증 자료를 읽지 못했습니다.")
    return details


def validate_accessibility_checks(chat_reply):
    """시설 문의의 findings를 DB 조회 값과 대조합니다. 답변 문장은 바꾸지 않습니다."""
    errors = []
    for check in chat_reply.accessibility_checks:
        if check.place_name is None:
            if not chat_reply.needs_clarification:
                errors.append("시설 문의 장소가 미정이면 needs_clarification=true로 어떤 장소인지 확인하세요.")
            continue

        details = read_facility_details(check.place_name)
        expected = {}
        for topic in check.topics:
            status = "unknown"
            if topic in SERVER_CHECKED_FACILITY_TOPICS and details is not None:
                status = resolve_facility_status(details, topic)
            expected[topic] = status

        actual = {}
        duplicated_topics = []
        for finding in check.findings:
            if finding.topic in actual:
                duplicated_topics.append(finding.topic)
            actual[finding.topic] = finding.status
        if duplicated_topics:
            errors.append(f"{check.place_name}: findings에 같은 topic을 두 번 기록했습니다: {duplicated_topics}")

        if actual != expected:
            source = "DB 조회 자료" if details is not None else "DB에서 확인되지 않은 장소"
            errors.append(
                f"{check.place_name}: findings는 topics마다 하나씩 {source} 기준으로 기록해야 합니다. "
                f"기대값 {format_findings(expected)}, 실제값 {format_findings(actual)}. "
                "이 값에 맞게 findings와 답변을 수정하세요. wheelchair·stroller·other의 전체 통행은 unknown입니다."
            )
    return errors


def judge_visit_availability(details, check):
    """운영시간·휴무 자료로 방문 가능 여부를 판정하고 (기대값, 근거)를 반환합니다."""
    if details.get("status") == "not_found":
        return None, "장소를 DB에서 찾지 못함"

    visit_date = check.visit_date.isoformat()
    closed_status = details.get("closed_day_checks", {}).get(visit_date)
    if closed_status == STATUS_CLOSED:
        return False, f"{visit_date} 휴무"

    opening_hours = details.get("opening_hours")
    hours = find_opening_hours(opening_hours, check.visit_date)
    if hours is None and opening_hours != ALWAYS_OPEN:
        return None, f"{visit_date} 운영시간 미확인"

    hour, minute = map(int, check.arrival_time.split(":"))
    arrival_minutes = hour * 60 + minute
    end_time = None
    if check.duration_minutes is not None:
        end_minutes = arrival_minutes + check.duration_minutes
        if end_minutes > 1440:
            if hours is None:
                return None, "상시 개방이지만 자정을 넘는 관람은 날짜별 운영정보 미확인"
            return False, "관람이 자정을 넘어 폐장 이후에 끝남"
        end_time = "24:00"
        if end_minutes < 1440:
            end_time = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"

    if hours is None:
        return True, "상시 개방"

    before_opening, after_last_entry, after_closing = check_visit_time_window(
        hours, check.arrival_time, end_time,
    )
    reasons = []
    if before_opening:
        reasons.append(f"개장 {hours['opening_time']} 전 도착")
    if after_last_entry:
        reasons.append(f"입장 마감 {hours['last_entry_time']} 이후 도착")
    if after_closing:
        reasons.append(f"폐장 {hours['closing_time']} 이후 종료")
    if reasons:
        return False, ", ".join(reasons)
    return True, "운영시간 안"


def validate_visit_time_check(chat_reply):
    """방문 가능 여부 문의의 available 값을 운영정보와 대조합니다."""
    check = chat_reply.visit_time_check
    if check is None:
        return []
    details = read_place_details(place_id=check.place_id, visit_dates=[check.visit_date.isoformat()])
    status = details.get("status") if isinstance(details, dict) else None
    if status not in ("ok", "not_found"):
        raise sqlite3.OperationalError("운영시간 검증 자료를 읽지 못했습니다.")
    if status == "ok" and details.get("place_id") != check.place_id:
        raise sqlite3.OperationalError("운영시간 검증 자료의 장소가 일치하지 않습니다.")

    expected, reason = judge_visit_availability(details, check)
    if check.available == expected:
        return []
    closed_status = details.get("closed_day_checks", {}).get(check.visit_date.isoformat())
    return [
        f"visit_time_check.available의 기대값은 {expected}, 실제값은 {check.available}입니다. "
        f"근거: {reason}. 운영시간 {details.get('opening_hours')}, 휴무 판정 {closed_status}, "
        f"도착 {check.arrival_time}, 관람 {check.duration_minutes}분. 이 근거에 맞게 available과 답변을 수정하세요."
    ]


def validate_restaurant_failure(chat_reply, tool_trace):
    """식당 조회 실패 상태와 응답의 plan·재시도 요청을 대조합니다."""
    failure = restaurant_lookup_failure(tool_trace)
    errors = []
    if failure in STOPPED_RESTAURANT_FAILURES and chat_reply.plan is not None:
        errors.append(
            f"식당 조회가 {failure}로 실패했습니다. 일정을 확정하지 말고 plan=null로 "
            "기존 일정과 식사 요청을 유지한다고 원인과 함께 안내하세요."
        )
    if failure in TEMPORARY_RESTAURANT_FAILURES:
        if chat_reply.restaurant_retry_request is None:
            errors.append(
                f"식당 조회가 일시적으로 실패했습니다({failure}). "
                "restaurant_retry_request에 다시 보낼 사용자 식사 요청 원문을 넣고 원인을 안내하세요."
            )
    elif chat_reply.restaurant_retry_request is not None:
        latest = failure if failure is not None else "정상 조회 또는 식당 조회 없음"
        errors.append(
            f"일시적인 식당 조회 오류가 없으므로(최근 상태: {latest}) restaurant_retry_request는 null이어야 합니다."
        )
    return errors


def _build_chat_result(
    chat_reply: ChatAgentReply, agent_result: dict, *, request_options=None, previous_plan=None, schedule=None,
):
    tool_trace = extract_tool_trace(agent_result.get("messages") or [])
    try:
        fact_errors = validate_accessibility_checks(chat_reply)
        fact_errors.extend(validate_visit_time_check(chat_reply))
    except sqlite3.Error:
        return build_error_response(500, "internal_error", "장소 검증 자료를 읽지 못했습니다.")
    fact_errors.extend(validate_restaurant_failure(chat_reply, tool_trace))
    if fact_errors:
        raise PlanValidationFailure(fact_errors)

    if request_options is not None and chat_reply.options.lodging_stays:
        errors = validate_lodging_selections(
            request_options, chat_reply.options.model_dump(), tool_trace=tool_trace,
        )
        if errors:
            raise PlanValidationFailure(errors)

    reply_text = chat_reply.reply
    plan = None
    map_routes, transit_routes, warnings = [], [], []
    if chat_reply.plan is None:
        result = {"reply": reply_text, "options": chat_reply.options, "plan": None, "warnings": []}
        if restaurant_lookup_failure(tool_trace) in TEMPORARY_RESTAURANT_FAILURES:
            result["restaurant_retry_request"] = chat_reply.restaurant_retry_request
        return result

    missing = []
    for field, label in (("start_date", "출발 날짜"), ("trip_days", "여행 기간"), ("transport", "이동수단")):
        if getattr(chat_reply.options, field) is None:
            missing.append(label)
    if missing:
        raise PlanValidationFailure([
            "일정의 필수 조건이 없습니다: " + ", ".join(missing)
            + ". 사용자 대화에서 확인했으면 options와 changed_options에 기록하고, 아니면 plan=null로 질문하세요."
        ])

    plan_data = chat_reply.plan.model_dump()
    restaurants = read_confirmed_restaurants(tool_trace)
    errors = validate_plan_restaurants(plan_data, restaurants)
    errors.extend(validate_plan_times(plan_data))
    errors.extend(agent_result.get("place_detail_errors") or [])
    errors.extend(agent_result.get("visit_order_errors") or [])
    if not errors:
        plan_data = restore_plan_route_ids(
            plan_data, agent_result.get("route_calls") or [],
            tool_trace=tool_trace,
            restaurants=restaurants,
            departure={
                "latitude": chat_reply.options.departure_latitude,
                "longitude": chat_reply.options.departure_longitude,
            },
            return_to_origin=chat_reply.options.return_to_origin is True,
            daily_departure=chat_reply.options.lodging_status == "not_needed",
            lodging_stays=[stay.model_dump() for stay in chat_reply.options.lodging_stays],
        )
    errors.extend(
        validate_plan_moves(
            plan_data,
            agent_result.get("route_calls") or [],
            transport=chat_reply.options.transport,
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
            TripPlan.model_validate(restored_plan), restaurants=restaurants,
        )
    except sqlite3.Error:
        return build_error_response(
            500, "internal_error", "장소 DB를 읽지 못했습니다."
        )

    schedule_errors = validate_schedule_constraints(previous_plan or {}, plan, schedule)
    if schedule_errors:
        raise PlanValidationFailure(schedule_errors)
    if schedule.limits:
        plan["schedule_limits"] = schedule.limits
    lodging_stays = [stay.model_dump() for stay in chat_reply.options.lodging_stays]
    lodging_errors = validate_lodging_boundaries(
        plan, trip_days=chat_reply.options.trip_days,
        lodging_status=chat_reply.options.lodging_status, lodging_stays=lodging_stays,
    )
    if lodging_errors:
        raise PlanValidationFailure(lodging_errors)
    endpoint_errors = validate_route_endpoints(
        plan,
        agent_result.get("route_calls") or [],
        departure={
            "latitude": chat_reply.options.departure_latitude,
            "longitude": chat_reply.options.departure_longitude,
        },
        return_to_origin=chat_reply.options.return_to_origin is True,
        daily_departure=chat_reply.options.lodging_status == "not_needed",
        lodging_stays=lodging_stays,
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
    warnings.extend(build_lodging_warnings(
        trip_days=chat_reply.options.trip_days,
        lodging_status=chat_reply.options.lodging_status,
        lodging_stays=lodging_stays,
    ))
    transit_routes = select_plan_transit_guidance(
        plan,
        agent_result.get("route_calls") or [],
    )

    return {
        "reply": reply_text,
        "options": chat_reply.options,
        "plan": plan,
        "map_routes": map_routes,
        "transit_routes": transit_routes,
        "warnings": warnings,
    }


def read_user_sources(chat_request):
    """구조화 근거(evidence)를 찾을 실제 사용자 발화 목록입니다."""
    sources = [chat_request.message]
    for message in chat_request.history:
        if message.role == "user":
            sources.append(message.content)
    return sources


def is_in_user_sources(text, sources):
    for source in sources:
        if text in source:
            return True
    return False


def validate_evidence(chat_reply, sources):
    errors = []
    for action in chat_reply.request_actions:
        if not is_in_user_sources(action.evidence, sources):
            errors.append(f"request_actions의 근거가 사용자 입력에 없습니다: {action.evidence}")
    for change in chat_reply.schedule_changes:
        if not is_in_user_sources(change.evidence, sources):
            errors.append(f"schedule_changes의 근거가 사용자 입력에 없습니다: {change.evidence}")
    for order in chat_reply.visit_orders:
        if not is_in_user_sources(order.evidence, sources):
            errors.append(f"visit_orders의 근거가 사용자 입력에 없습니다: {order.evidence}")
    retry_request = chat_reply.restaurant_retry_request
    if retry_request is not None and not is_in_user_sources(retry_request, sources):
        errors.append("restaurant_retry_request는 실제 사용자 요청의 연속 원문이어야 합니다.")
    return errors


def validate_chat_reply(chat_reply, base_options, sources):
    """응답의 구조화 해석을 실제 사용자 발화·현재 조건과 대조합니다. 의미는 다시 해석하지 않습니다."""
    errors = validate_evidence(chat_reply, sources)
    actual = chat_reply.options.model_dump(mode="json")
    declared_fields = list(chat_reply.changed_options)
    # 기간 연장 동의(period_confirmed)는 여행 기간 변경의 선언으로 본다.
    if chat_reply.period_confirmed and "trip_days" not in declared_fields:
        declared_fields.append("trip_days")
    expected = read_fixed_trip_options(base_options, actual, declared_fields)
    for difference in find_changed_options(expected, actual):
        errors.append(
            f"{difference['label']}({difference['field']})이(가) 현재 {difference['expected']}에서 "
            f"{difference['actual']}(으)로 바뀌었지만 changed_options에 선언하지 않았습니다. "
            "사용자가 이번 대화에서 새로 정하거나 바꾼 값이면 changed_options에 기록하고, 아니면 현재 값을 유지하세요."
        )
    if chat_reply.plan is not None and chat_reply.needs_clarification:
        errors.append("확인 질문과 확정 일정을 동시에 반환했습니다. 확인이 필요하면 plan=null로 반환하세요.")
    old_days = base_options.get("trip_days")
    new_days = chat_reply.options.trip_days
    if old_days == 1 and new_days is not None and new_days > 1 and not chat_reply.period_confirmed:
        errors.append(
            "당일 여행의 기간 연장은 사용자 확인이 필요합니다. 기간 변경을 명시하거나 대화에서 동의했으면 "
            "period_confirmed=true로 기록하고, 아니면 기간을 유지하고 확인 질문을 하세요."
        )
    return errors


def normalize_structured_conditions(conditions):
    """근거 문장을 뺀 조건 값을 순서와 무관하게 비교할 수 있도록 정렬합니다."""
    normalized = []
    for condition in conditions:
        values = condition.model_dump(mode="json", exclude={"evidence"})
        normalized.append(json.dumps(values, sort_keys=True, ensure_ascii=False))
    normalized.sort()
    return normalized


def keeps_all_conditions(locked, current):
    """재시도 조건이 고정한 조건을 하나도 빼거나 바꾸지 않았는지 확인합니다. 근거 있는 조건 추가는 허용합니다."""
    remaining = list(current)
    for condition in locked:
        if condition not in remaining:
            return False
        remaining.remove(condition)
    return True


def build_retry_lock(base_options, chat_reply, tool_trace):
    """일정·정보 검증에 실패한 첫 해석을 고정합니다. 위치 근거가 없는 숙박일만 다시 확인하게 둡니다."""
    locked_options, recheck_nights = prepare_lodging_retry_options(
        base_options, chat_reply.options.model_dump(mode="json"), tool_trace=tool_trace,
    )
    schedule_changes = []
    for change in chat_reply.schedule_changes:
        schedule_changes.append(change.model_dump(mode="json"))
    visit_orders = []
    for order in chat_reply.visit_orders:
        visit_orders.append(order.model_dump(mode="json"))
    return {
        "options": locked_options,
        "recheck_nights": recheck_nights,
        "schedule_changes": schedule_changes,
        "visit_orders": visit_orders,
        "normalized_changes": normalize_structured_conditions(chat_reply.schedule_changes),
        "normalized_orders": normalize_structured_conditions(chat_reply.visit_orders),
        "replace_visits": chat_reply.replace_visits,
    }


def validate_retry_lock(chat_reply, lock):
    """재시도 응답이 고정한 조건 값을 유지하는지 확인합니다. 확인 질문으로 바꾼 응답은 조건을 적용하지 않으므로 제외합니다."""
    if chat_reply.plan is None and chat_reply.needs_clarification:
        return []
    errors = validate_retry_options(
        lock["options"], chat_reply.options.model_dump(mode="json"),
        lodging_recheck_nights=lock["recheck_nights"],
    )
    current_changes = normalize_structured_conditions(chat_reply.schedule_changes)
    if not keeps_all_conditions(lock["normalized_changes"], current_changes):
        errors.append(
            "재시도에서 처음 해석한 schedule_changes의 조건 값(일차·대상·종류·분·개수)을 빼거나 바꿀 수 없습니다. "
            "plan_retry.schedule_changes를 모두 유지하고, 빠진 사용자 조건만 근거와 함께 추가하세요. "
            "조건끼리 충돌하면 needs_clarification=true로 질문하세요."
        )
    current_orders = normalize_structured_conditions(chat_reply.visit_orders)
    if not keeps_all_conditions(lock["normalized_orders"], current_orders):
        errors.append("재시도에서 처음 해석한 visit_orders를 빼거나 바꿀 수 없습니다. 빠진 순서 조건만 추가하세요.")
    if chat_reply.replace_visits != lock["replace_visits"]:
        errors.append("재시도에서 처음 해석한 replace_visits를 바꿀 수 없습니다.")
    return errors


def build_chat_retry_messages(original_messages, *, failed_plan, base_options, errors, lock):
    """같은 Agent에 실패 이유와 고정 조건을 전달할 메시지를 만듭니다."""
    options = base_options
    recheck_nights = None
    if lock is not None:
        options = lock["options"]
        recheck_nights = lock["recheck_nights"]
    messages = build_plan_retry_messages(
        original_messages, failed_plan=failed_plan, options=options,
        errors=errors, lodging_recheck_nights=recheck_nights,
    )
    retry_payload = json.loads(messages[-1]["content"])
    retry = retry_payload["plan_retry"]
    retry["constraints_locked"] = lock is not None
    if lock is not None:
        retry["schedule_changes"] = lock["schedule_changes"]
        retry["visit_orders"] = lock["visit_orders"]
        retry["replace_visits"] = lock["replace_visits"]
    messages[-1]["content"] = json.dumps(retry_payload, ensure_ascii=False)
    return messages


def read_reply_interpretation_errors(chat_reply, base_options, sources, lock, previous_plan):
    """구조화 해석 단계의 오류와 일정 조건을 반환합니다. 일정이 없으면 조건을 만들지 않습니다."""
    try:
        errors = validate_chat_reply(chat_reply, base_options, sources)
    except ValueError as error:
        return [str(error)], None
    if lock is not None:
        errors.extend(validate_retry_lock(chat_reply, lock))
    if errors or chat_reply.plan is None:
        return errors, None

    changes = []
    for change in chat_reply.schedule_changes:
        changes.append(change.model_dump())
    orders = []
    for order in chat_reply.visit_orders:
        orders.append(order.model_dump())
    try:
        schedule = read_schedule_constraints(
            previous_plan, changes, trip_days=chat_reply.options.trip_days,
            orders=orders, replace_visits=chat_reply.replace_visits,
        )
    except ValueError as error:
        return [str(error)], None
    return [], schedule


def create_chat(chat_request: ChatRequest):
    """검증 실패를 같은 Agent에 돌려줘 제한 횟수 안에서 수정하게 합니다."""
    check_chat_request_control()
    started_at = time.perf_counter()
    original_messages = build_chat_messages(chat_request)
    payload = json.loads(original_messages[-1]["content"])
    previous_plan = payload["current_plan"] or {}
    base_options = payload["options"]
    sources = read_user_sources(chat_request)
    messages = original_messages
    attempt_results = []
    lock = None

    for attempt in range(MAX_PLAN_RETRIES + 1):
        check_chat_request_control()
        errors = []
        error_code = "plan_validation_failed"
        error_message = "요청 조건에 맞는 응답을 확인하지 못했습니다."
        chat_reply = None
        try:
            agent_result = run_chat_agent(messages, ChatAgentReply)
        except ChatRequestStopped:
            raise
        except StructuredOutputError as error:
            _log_chat_schema_error(error, "agent")
            errors = read_schema_error_reasons(error)
            error_code = "llm_invalid_response"
            error_message = "AI 답변 형식이 올바르지 않습니다."
        except Exception as error:
            check_chat_request_control()
            logger.exception("대화 처리 중 오류")
            status_code, provider_code = read_llm_error(error)
            return build_llm_error_response(status_code, provider_code)
        check_chat_request_control()

        if not errors:
            attempt_results.append(agent_result)
            try:
                chat_reply = ChatAgentReply.model_validate(agent_result.get("chat_reply"))
            except ValidationError as error:
                _log_chat_schema_error(error, "response_validation", agent_result.get("chat_reply"))
                errors = read_schema_error_reasons(error)
                error_code = "llm_invalid_response"
                error_message = "AI 답변 형식이 올바르지 않습니다."

        body = None
        if not errors:
            errors, schedule = read_reply_interpretation_errors(
                chat_reply, base_options, sources, lock, previous_plan,
            )
        if not errors:
            try:
                body = _build_chat_result(
                    chat_reply, agent_result, request_options=base_options,
                    previous_plan=previous_plan, schedule=schedule,
                )
            except PlanValidationFailure as failure:
                errors = failure.errors
                if lock is None:
                    tool_trace = extract_tool_trace(agent_result.get("messages") or [])
                    lock = build_retry_lock(base_options, chat_reply, tool_trace)

        if not errors:
            if isinstance(body, dict):
                body["meta"] = {
                    **merge_agent_metrics(attempt_results),
                    "elapsed_seconds": round(time.perf_counter() - started_at, 1),
                }
            check_chat_request_control()
            return body

        if attempt >= MAX_PLAN_RETRIES:
            return build_error_response(
                502, error_code, error_message,
                details=[{"field": "response", "reason": reason} for reason in errors],
            )
        failed_plan = None
        if chat_reply is not None and chat_reply.plan is not None:
            failed_plan = chat_reply.plan.model_dump(mode="json")
        messages = build_chat_retry_messages(
            original_messages, failed_plan=failed_plan, base_options=base_options,
            errors=errors, lock=lock,
        )
