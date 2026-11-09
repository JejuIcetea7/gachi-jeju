from ..config import get_kakao_api_key
from ..db.bus_static import load_bus_data
from ..services.bus_matching import match_bus_segment
from ..services.kakao_transit import get_transit_routes
from datetime import datetime, timezone

from ..services.jeju_bus import (
    JejuBusAPIError,
    get_bus_arrivals,
    get_bus_positions,
    merge_bus_vehicle_data,
)

def _resolve_segment(segment, catalog, stops_by_route):
    numbers = set(segment.get("route_numbers") or [])
    candidates = [
        route for route in catalog
        if str(route.get("ROUTE_NUM", "")).strip() in numbers
    ]
    found_numbers = {
        str(route["ROUTE_NUM"]).strip() for route in candidates
    }
    missing_route_ids = sorted({
        str(route["ROUTE_ID"]) for route in candidates
        if str(route["ROUTE_ID"]) not in stops_by_route
    })

    matches = match_bus_segment(segment, catalog, stops_by_route)
    info = {
        "status": "matched",
        "matches": matches,
        "missing_route_ids": missing_route_ids,
        "missing_route_numbers": sorted(numbers - found_numbers),
        "realtime_status": "not_requested",
        "vehicles": [],
    }

    pairs = {
        (match["boarding_station_id"], match["alighting_station_id"])
        for match in matches
    }
    repeated_route = len(matches) != len({
        match["route_id"] for match in matches
    })

    if len(pairs) > 1 or repeated_route:
        info["status"] = "ambiguous_match"
    elif missing_route_ids or info["missing_route_numbers"]:
        info["status"] = "static_data_missing"
    elif not matches:
        info["status"] = "mapping_failed"

    return info

def get_bus_trip(
    *,
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> dict:
    result = get_transit_routes(
        origin_latitude=origin_latitude,
        origin_longitude=origin_longitude,
        destination_latitude=destination_latitude,
        destination_longitude=destination_longitude,
        api_key=get_kakao_api_key(),
    )
    if result["status"] != "ok":
        return result

    return _build_bus_trip(result)

def _build_bus_trip(result: dict) -> dict:
    try:
        catalog, stops_by_route = load_bus_data()
    except (OSError, ValueError):
        result.update(status="partial", reason="static_data_error")
        return result

    for route in result["routes"]:
        for segment in route["segments"]:
            if segment["type"] != "BUS":
                continue

            try:
                info = _resolve_segment(segment, catalog, stops_by_route)
            except (KeyError, TypeError, ValueError, AttributeError):
                info = {
                    "status": "static_data_error",
                    "realtime_status": "not_requested",
                    "vehicles": [],
                }

            segment["bus_info"] = info
            if info["status"] != "matched":
                result["status"] = "partial"

    return _attach_realtime(result)

def _attach_realtime(result: dict) -> dict:
    arrivals_cache = {}
    positions = None
    positions_failed = False
    positions_requested_at = None

    for route in result["routes"]:
        for segment in route["segments"]:
            info = segment.get("bus_info")
            if not info or info["status"] != "matched":
                continue

            matches = info["matches"]
            station_id = matches[0]["boarding_station_id"]
            route_ids = {match["route_id"] for match in matches}

            if station_id not in arrivals_cache:
                requested_at = datetime.now(timezone.utc).isoformat()
                try:
                    arrivals = get_bus_arrivals(station_id)
                except JejuBusAPIError:
                    arrivals = None
                arrivals_cache[station_id] = (arrivals, requested_at)

            arrivals, requested_at = arrivals_cache[station_id]
            info["arrivals_requested_at"] = requested_at

            if arrivals is None:
                info["realtime_status"] = "arrival_error"
                result["status"] = "partial"
                continue

            vehicles = merge_bus_vehicle_data(route_ids, arrivals, [])
            if not vehicles:
                info["realtime_status"] = "no_arrivals"
                continue

            if positions is None:
                positions_requested_at = datetime.now(timezone.utc).isoformat()
                try:
                    positions = get_bus_positions()
                except JejuBusAPIError:
                    positions = []
                    positions_failed = True

            info["positions_requested_at"] = positions_requested_at
            info["vehicles"] = merge_bus_vehicle_data(
                route_ids, arrivals, positions
            )

            if positions_failed:
                info["realtime_status"] = "position_error"
                result["status"] = "partial"
            elif any(vehicle["position"] is None for vehicle in info["vehicles"]):
                info["realtime_status"] = "position_missing"
                result["status"] = "partial"
            else:
                info["realtime_status"] = "ok"

    return result
def _select_best_route(routes: list[dict]) -> dict:
    """환승 횟수가 가장 적은 경로를 고르고, 같으면 소요시간이 짧은 경로를 고릅니다."""
    best_route = routes[0]
    for route in routes[1:]:
        route_transfers = route["transfers"] or 0
        best_transfers = best_route["transfers"] or 0
        if route_transfers < best_transfers:
            best_route = route
            continue

        same_transfers = route_transfers == best_transfers
        route_duration = route["duration_seconds"] or 0
        best_duration = best_route["duration_seconds"] or 0
        if same_transfers and route_duration < best_duration:
            best_route = route

    return best_route

def _summarize_segment(segment: dict) -> dict:
    summary = {
        "type": segment["type"],
        "distance_m": segment["distance_m"],
        "duration_seconds": segment["duration_seconds"],
    }
    if segment["type"] != "BUS":
        return summary

    stop_names = segment.get("stop_names") or []
    info = segment.get("bus_info") or {}

    arriving_buses = []
    for vehicle in info.get("vehicles") or []:
        arriving_bus = {
            "route_number": vehicle["route_number"],
            "low_floor": vehicle["low_floor"],
            "arrival_minutes": vehicle["arrival_minutes"],
            "remaining_stops": vehicle["remaining_stops"],
        }
        arriving_buses.append(arriving_bus)

    summary["route_numbers"] = segment.get("route_numbers")
    summary["boarding_stop"] = segment.get("boarding_stop")
    summary["alighting_stop"] = segment.get("alighting_stop")
    summary["stop_count"] = len(stop_names)
    summary["realtime_status"] = info.get("realtime_status", "not_requested")
    summary["arriving_buses"] = arriving_buses
    return summary

def get_bus_route_summary(
    *,
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> dict:
    """두 지점 사이의 버스 경로 1개(환승이 가장 적고, 같으면 빠른 경로)를 돌려줍니다.
    구간별 버스 번호, 탑승·하차 정류장, 소요시간(초)과 실시간 도착 예정 버스(저상버스 여부, 남은 분)를 포함합니다."""
    result = get_transit_routes(
        origin_latitude=origin_latitude,
        origin_longitude=origin_longitude,
        destination_latitude=destination_latitude,
        destination_longitude=destination_longitude,
        api_key=get_kakao_api_key(),
    )
    if result["status"] != "ok":
        return result

    best_route = _select_best_route(result["routes"])
    result["routes"] = [best_route]
    trip = _build_bus_trip(result)
    route = trip["routes"][0]

    segments = []
    for segment in route["segments"]:
        segment_summary = _summarize_segment(segment)
        segments.append(segment_summary)

    return {
        "status": trip["status"],
        "duration_seconds": route["duration_seconds"],
        "distance_m": route["distance_m"],
        "transfers": route["transfers"],
        "fare_krw": route["fare_krw"],
        "segments": segments,
    }
