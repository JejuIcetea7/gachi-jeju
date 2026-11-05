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