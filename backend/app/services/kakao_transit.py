import json
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

def parse_transit_response(data: dict) -> dict:
    provider_status = data.get("status")
    if provider_status in {"NO_RESULTS", "STARTNODES_NULL", "ENDNODES_NULL", "EQUAL_POINTS"}:
        return {"status": "no_route", "reason": provider_status}
    if provider_status != "OK":
        return {"status": "api_error", "reason": provider_status}

    raw_routes = data.get("routes")
    if not isinstance(raw_routes, list):
        return {"status": "api_error", "reason": "INVALID_RESPONSE"}

    routes = []
    for route in raw_routes:
        properties = route["properties"]
        if properties["type"] != "BUS":
            continue
        
        segments = []
        for step in route["steps"]:
            details = step["properties"]
            segment = {
                "type": details["type"],
                "distance_m": details.get("distance"),
                "duration_seconds": details.get("time"),
            }

            if details["type"] == "BUS":
                stops = details.get("stops") or []
                segment["stop_names"] = [stop["name"] for stop in stops]
                vehicles = details.get("vehicles") or []
                route_numbers = [vehicle["name"] for vehicle in vehicles]
                segment["route_numbers"] = list(dict.fromkeys(route_numbers))
                segment["boarding_stop"] = stops[0]["name"] if stops else None
                segment["alighting_stop"] = stops[-1]["name"] if len(stops) > 1 else None

            segments.append(segment)
        
        routes.append({
            "distance_m": properties.get("totalDistance"),
            "duration_seconds": properties.get("totalTime"),
            "transfers": properties.get("transfers"),
            "fare_krw": (properties.get("fare") or {}).get("value"),
            "segments": segments,
        })

    if not routes:
        return {"status": "no_route", "reason": "NO_BUS_ROUTE"}

    return {"status": "ok", "routes": routes}

def get_transit_routes(
    *,
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
    api_key: str,
) -> dict:
    api_key = api_key.strip()
    if not api_key:
        return {"status": "auth_error"}

    params = urlencode({
        "start_x": origin_longitude,
        "start_y": origin_latitude,
        "end_x": destination_longitude,
        "end_y": destination_latitude,
    })
    request = Request(
        f"https://dapi.kakao.com/v2/routing/publictraffic?{params}",
        headers={"Authorization": f"KakaoAK {api_key}"},
    )
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    try:
        with urlopen(request, context=context, timeout=20) as response:
            data = json.load(response)
    except HTTPError as error:
        with error:
            try:
                body = json.load(error)
            except (OSError, ValueError):
                body = None
        
        provider_code = body.get("code") if isinstance(body, dict) else None
        if error.code == 429 or provider_code in (-10, -11):
            return {"status": "rate_limited"}
        if error.code in (401, 403) or provider_code == -903:
            return {"status": "auth_error"}
        return {"status": "api_error"}
    except (OSError, ValueError):
        return {"status": "api_error"}

    if not isinstance(data, dict):
        return {"status": "api_error", "reason": "INVALID_RESPONSE"}

    try:
        return parse_transit_response(data)
    except (KeyError, TypeError, AttributeError):
        return {"status": "api_error", "reason": "INVALID_RESPONSE"}