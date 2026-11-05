import json
import ssl
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import urlopen

import truststore

BASE_URL = "https://bus.jeju.go.kr/api"

class JejuBusAPIError(RuntimeError):
    """제주 BIS 요청에 실패했거나 응답 형식이 잘못된 경우."""

def _get_rows(endpoint: str, **params: int | str) -> list[dict]:
    url = f"{BASE_URL}/{endpoint}"
    if params:
        url = f"{url}?{urlencode(params)}"

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    try:
        with urlopen(url, context=context, timeout=20) as response:
            rows = json.load(response)
    except (OSError, ValueError, HTTPException) as error:
        raise JejuBusAPIError(f"제주 BIS 조회 실패 : {endpoint}") from error

    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise JejuBusAPIError(f"제주 BIS 응답 형식 오류 : {endpoint}")

    return rows

def get_bus_routes() -> list[dict]:
    return _get_rows("searchBusRouteList.do")

def get_bus_route_stops(route_id: int | str) -> list[dict]:
    return _get_rows("searchBusRouteStationList.do", route_id=route_id)

def get_bus_arrivals(station_id: int | str) -> list[dict]:
    return _get_rows("searchArrivalInfoList.do", station_id=station_id)

def get_bus_positions() -> list[dict]:
    return _get_rows("searchBusAllocList.do")

def _read_position(row: dict) -> dict[str, float] | None:
    try:
        longitude = float(row["LOCAL_X"])
        latitude = float(row["LOCAL_Y"])
    except (KeyError, TypeError, ValueError):
        return None

    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    if (longitude, latitude) == (0, 0):
        return None

    return {"longitude": longitude, "latitude": latitude}

def merge_bus_vehicle_data(
    route_ids: set[str],
    arrivals: list[dict],
    positions: list[dict],
) -> list[dict]:
    positions_by_vehicle = {
        (str(row["ROUTE_ID"]), str(row["VH_ID"])): row
        for row in positions
        if row.get("ROUTE_ID") and row.get("VH_ID")
    }

    vehicles = []
    for arrival in arrivals:
        route_id = str(arrival.get("ROUTE_ID"))
        if route_id not in route_ids:
            continue

        vehicle_id = arrival.get("VH_ID")
        position_row = positions_by_vehicle.get((route_id, str(vehicle_id)), {})
        low_floor_code = str(arrival.get("LOW_PLATE_TP", "")).strip().upper()

        vehicles.append({
            "route_id": route_id,
            "route_number": arrival.get("ROUTE_NUM"),
            "vehicle_id": str(vehicle_id) if vehicle_id is not None else None,
            "low_floor": {"Y": True, "N": False}.get(low_floor_code),
            "arrival_minutes": arrival.get("PREDICT_TRAV_TM"),
            "remaining_stops": arrival.get("REMAIN_STATION"),
            "last_stop_name": arrival.get("CURR_STATION_NM"),
            "position": _read_position(position_row),
        })
    
    return vehicles