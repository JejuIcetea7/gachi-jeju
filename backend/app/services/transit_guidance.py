from collections import Counter
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from .plan_routes import normalize_transport_mode

NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(ge=1, strict=True)]


class TransitSegment(BaseModel):
    type: Literal["BUS", "WALKING", "UNKNOWN"]
    distance_m: NonNegativeInt | None = None
    duration_seconds: NonNegativeInt | None = None
    route_numbers: list[str] = Field(default_factory=list)
    boarding_stop: str | None = None
    alighting_stop: str | None = None
    ride_stop_count: PositiveInt | None = Field(
        default=None,
        description="경로 정류장 목록에서 승차지는 제외하고 하차지는 포함한 이동 정거장 수",
    )


class TransitRouteGuidance(BaseModel):
    route_id: str = Field(min_length=1, max_length=80)
    segments: list[TransitSegment] = Field(min_length=1)
    requested_at: AwareDatetime


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _nonnegative_int(value):
    return value if type(value) is int and value >= 0 else None


def _ride_stop_count(segment, boarding, alighting):
    names = segment.get("stop_names")
    if (
        not isinstance(names, list)
        or len(names) < 2
        or not boarding
        or not alighting
        or any(_text(name) is None for name in names)
    ):
        return None

    if _text(names[0]) != boarding or _text(names[-1]) != alighting:
        return None

    return len(names) - 1


def build_transit_segments(raw_segments) -> list[dict]:
    """선택 경로의 구간을 원래 순서대로 정리하며 없는 정보는 추정하지 않습니다."""
    if not isinstance(raw_segments, list):
        return []

    segments = []
    for raw in raw_segments:
        segment = raw if isinstance(raw, dict) else {}
        mode = segment.get("type")
        values = {
            "type": mode if mode in ("BUS", "WALKING") else "UNKNOWN",
            "distance_m": _nonnegative_int(segment.get("distance_m")),
            "duration_seconds": _nonnegative_int(segment.get("duration_seconds")),
        }

        if mode == "BUS":
            numbers = segment.get("route_numbers")
            if not isinstance(numbers, list):
                numbers = []
            boarding = _text(segment.get("boarding_stop"))
            alighting = _text(segment.get("alighting_stop"))
            values.update({
                "route_numbers": list(dict.fromkeys(
                    number for value in numbers
                    if (number := _text(value)) is not None
                )),
                "boarding_stop": boarding,
                "alighting_stop": alighting,
                "ride_stop_count": _ride_stop_count(segment, boarding, alighting),
            })

        segments.append(TransitSegment.model_validate(values).model_dump())

    return segments


def select_plan_transit_guidance(plan: dict, route_calls: list[dict]) -> list[dict]:
    """최종 일정의 버스 이동과 일치하는 이번 실행의 안내만 반환합니다."""
    records = route_calls if isinstance(route_calls, list) else []
    available = {}
    duplicates = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        route_id = record.get("route_id")
        if not isinstance(route_id, str) or not route_id:
            continue
        if route_id in available:
            duplicates.add(route_id)
        available[route_id] = record

    days = plan.get("days") if isinstance(plan, dict) else []
    days = days if isinstance(days, list) else []
    moves = []
    for day in days:
        items = day.get("items") if isinstance(day, dict) else []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("item_type") == "move":
                moves.append(item)

    references = Counter(
        item["route_id"] for item in moves
        if isinstance(item.get("route_id"), str)
    )
    selected = []
    for item in moves:
        route_id = item.get("route_id")
        if (
            not isinstance(route_id, str)
            or not route_id
            or route_id in duplicates
            or references[route_id] != 1
            or normalize_transport_mode(item.get("transport")) != "transit"
        ):
            continue
        record = available.get(route_id)
        if not record or record.get("transport") != "transit":
            continue
        try:
            guidance = TransitRouteGuidance.model_validate({
                "route_id": route_id,
                "segments": record.get("transit_segments"),
                "requested_at": record.get("requested_at"),
            })
        except ValidationError:
            continue
        selected.append(guidance.model_dump(mode="json"))

    return selected
