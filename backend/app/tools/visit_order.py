"""DB 여행지의 좌표로 이동 순서 후보를 비교하는 Agent 도구입니다."""

import sqlite3

from ..services.visit_order import calculate_visit_order, validate_visit_order_request
from .places import read_rows


def suggest_visit_order(
    *,
    place_ids: list[str],
    origin_latitude: float | None = None,
    origin_longitude: float | None = None,
    destination_latitude: float | None = None,
    destination_longitude: float | None = None,
    fixed_place_ids: list[str] | None = None,
) -> dict:
    """여행지 1~12곳의 방문 순서를 출발·도착 좌표와 직선거리로 비교합니다.

    place_ids는 이번 검색·상세 조회에서 확인한 DB 장소 ID를 현재 순서로 보냅니다.
    fixed_place_ids는 고정 시각·순서 때문에 위치를 바꿀 수 없는 장소입니다.
    고정 장소는 원래 인덱스를 유지하고 다른 장소도 해당 고정 장소를 넘어 이동하지 않습니다.
    출발·도착 좌표는 확인된 출발지·선택 숙소·귀환지이며 미정이면 둘 다 생략합니다.
    직선거리 후보이므로 실제 이동시간·도로·버스·접근성의 최적 경로를 보장하지 않습니다.
    결과를 시간표로 쓰지 말고 운영시간·필수 조건을 확인한 뒤 실제 이동 도구를 호출합니다.
    """
    origin = None
    if origin_latitude is not None or origin_longitude is not None:
        origin = {"latitude": origin_latitude, "longitude": origin_longitude}
    destination = None
    if destination_latitude is not None or destination_longitude is not None:
        destination = {"latitude": destination_latitude, "longitude": destination_longitude}
    try:
        validate_visit_order_request(
            place_ids, origin=origin, destination=destination, fixed_place_ids=fixed_place_ids,
        )
    except ValueError as error:
        return {"status": "invalid_input", "message": str(error)}
    placeholders = ", ".join(["?"] * len(place_ids))
    try:
        rows = read_rows(
            "SELECT place_id, name, latitude, longitude FROM places "
            f"WHERE place_id IN ({placeholders})",
            tuple(place_ids),
        )
    except sqlite3.Error:
        return {"status": "db_error"}
    places_by_id = {}
    for row in rows:
        place = dict(row)
        place_id = place.get("place_id")
        if place_id not in place_ids or place_id in places_by_id:
            return {"status": "invalid_data"}
        places_by_id[place_id] = place
    missing = [place_id for place_id in place_ids if place_id not in places_by_id]
    if missing:
        return {"status": "not_found", "missing_place_ids": missing}
    try:
        result = calculate_visit_order(
            [places_by_id[place_id] for place_id in place_ids],
            origin=origin, destination=destination, fixed_place_ids=fixed_place_ids,
        )
    except ValueError as error:
        return {"status": "invalid_data", "message": str(error)}
    return {"status": "ok", **result}
