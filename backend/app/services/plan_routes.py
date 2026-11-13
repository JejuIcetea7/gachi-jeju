import re
from collections import Counter
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

Longitude = Annotated[
    float, Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
]
Latitude = Annotated[
    float, Field(ge=-90, le=90, allow_inf_nan=False, strict=True)
]
MapPath = Annotated[list[tuple[Longitude, Latitude]], Field(min_length=2)]


class MapCoordinate(BaseModel):
    latitude: Latitude
    longitude: Longitude


class MapRoute(BaseModel):
    route_id: str = Field(min_length=1, max_length=80)
    transport: Literal["driving", "walking", "transit"]
    origin: MapCoordinate
    destination: MapCoordinate
    distance_m: int = Field(ge=0, strict=True)
    duration_seconds: int = Field(ge=0, strict=True)
    geometry_status: Literal["ok", "partial", "missing"]
    paths: list[MapPath]
    requested_at: AwareDatetime

def canonicalize_transport_label(value):
    """노선 번호와 승하차 설명이 붙은 명확한 버스 표기만 일반 이름으로 정리합니다."""
    if not isinstance(value, str):
        return value
    text = "".join(value.split()).lower()
    stem, marker, tail = text.partition("(")
    if not re.fullmatch(r"(?:버스|bus)[0-9]+(?:-[0-9]+)?번?", stem):
        return value
    if not marker:
        return "버스"

    annotation = "(" + tail
    if "승차" not in annotation or "하차" not in annotation:
        return value
    depth = 0
    for index, character in enumerate(annotation):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if depth < 0 or (depth == 0 and index != len(annotation) - 1):
            return value
    return "버스" if depth == 0 else value


def normalize_transport_mode(value):
    aliases = {
        "driving": "driving", "자동차": "driving", "자가용": "driving",
        "렌터카": "driving", "렌트카": "driving", "택시": "driving",
        "차량": "driving", "렌터카또는자동차": "driving",
        "rental_car": "driving", "taxi": "driving",
        "walking": "walking", "도보": "walking",
        "transit": "transit", "대중교통": "transit", "버스": "transit",
        "bus": "transit", "대중교통(버스·도보)": "transit",
    }
    value = canonicalize_transport_label(value)
    text = "".join(value.split()).lower() if isinstance(value, str) else ""
    return aliases.get(text)

def select_plan_routes(
    plan: dict,
    route_calls: list[dict],
) -> tuple[list[dict], list[str]]:
    """최종 이동 항목이 참조한 이번 실행의 지도 경로만 반환합니다."""
    available = {}
    duplicate_ids = set()

    for record in route_calls:
        if not isinstance(record, dict):
            continue

        route_id = record.get("route_id")
        if not isinstance(route_id, str) or not route_id:
            continue

        if route_id in available:
            duplicate_ids.add(route_id)

        available[route_id] = record

    moves = [
        (day["day"], item)
        for day in plan["days"]
        for item in day["items"]
        if item["item_type"] == "move"
    ]
    reference_counts = Counter(item.get("route_id") for _, item in moves)
    selected = []
    warnings = []

    for day_number, item in moves:
        route_id = item.get("route_id")
        prefix = f"{day_number}일차 '{item['title']}': "

        if (
            not route_id
            or route_id not in available
            or route_id in duplicate_ids
            or reference_counts[route_id] != 1
        ):
            warnings.append(
                prefix + "이동 경로를 확인하지 못해 지도에는 장소 위치만 표시합니다."
            )
            continue

        try:
            route = MapRoute.model_validate(available[route_id])
        except ValidationError:
            warnings.append(
                prefix + "이동 경로 좌표를 확인하지 못해 장소 위치만 표시합니다."
            )
            continue

        mode = normalize_transport_mode(item.get("transport"))
        if mode is not None and route.transport != mode:
            warnings.append(
                prefix + "일정과 조회 경로의 이동수단이 달라 경로선을 표시하지 않습니다."
            )
            continue

        if route.geometry_status == "missing" or not route.paths:
            warnings.append(
                prefix + "경로 좌표가 없어 지도에는 장소 위치만 표시합니다."
            )
            continue

        selected.append(route.model_dump(mode="json"))

        if route.geometry_status == "partial":
            warnings.append(
                prefix + "일부 이동 구간의 좌표가 없어 확인된 구간만 표시합니다."
            )

    return selected, warnings
