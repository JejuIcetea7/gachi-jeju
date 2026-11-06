import json
import ssl
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

NO_ROUTE_STATUSES = {
    "SAME_POINT",
    "START_LINK_NOT_FOUND",
    "END_LINK_NOT_FOUND",
    "TOO_FAR_AWAY",
    "ROUTE_RESULT_NOT_FOUND",
}

def parse_walking_response(data: dict) -> dict:
    if not isinstance(data, dict):
        return {"status": "api_error", "reason": "INVALID_RESPONSE"}

    try:
        provider_status = data.get("status")
        if provider_status in NO_ROUTE_STATUSES:
            return {"status": "no_route", "reason": provider_status}
        if provider_status != "OK":
            return {
                "status": "api_error",
                "reason": provider_status or "INVALID_RESPONSE",
            }

        route = data["route"]
        properties = route["properties"]
        distance = properties["totalDistance"]
        duration = properties["totalTime"]

        if (
            type(distance) is not int
            or type(duration) is not int
            or distance < 0
            or duration < 0
        ):
            return {"status": "api_error", "reason": "INVALID_RESPONSE"}

        steps = []
        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                details = step["properties"]
                steps.append({
                    "distance_m": details.get("distance"),
                    "duration_seconds": details.get("time"),
                    "guidance": details.get("guidance"),
                    "path_points": step.get("path", {}).get("points", []),
                })

        return {
            "status": "ok",
            "distance_m": distance,
            "duration_seconds": duration,
            "landing_url": properties.get("landingUrl"),
            "steps": steps,
        }

    except (KeyError, TypeError, AttributeError):
        return {"status": "api_error", "reason": "INVALID_RESPONSE"}

def get_walking_route(
    *,
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
    api_key: str,
    route_mode: str = "BROAD_FIRST",
) -> dict:
    api_key = api_key.strip()
    if not api_key:
        return {"status": "auth_error"}

    params = urlencode({
        "start_x": origin_longitude,
        "start_y": origin_latitude,
        "end_x": destination_longitude,
        "end_y": destination_latitude,
        "route_mode": route_mode,
    })
    request = Request(
        f"https://dapi.kakao.com/v2/routing/walk?{params}",
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
            except (OSError, ValueError, HTTPException):
                body = None

        provider_code = body.get("code") if isinstance(body, dict) else None
        if error.code == 429 or provider_code in (-10, -11):
            return {"status": "rate_limited"}
        if error.code in (401, 403) or provider_code == -903:
            return {"status": "auth_error"}
        return {"status": "api_error"}
    except (OSError, ValueError, HTTPException):
        return {"status": "api_error"}

    return parse_walking_response(data)