import json
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

def parse_driving_response(data: dict) -> dict[str, str | int]:
    """카카오 길찾기 응답에서 이동 거리와 예상 소요시간을 읽습니다."""
    routes = data.get("routes")

    if not isinstance(routes, list):
        return {"status": "api_error"}
    if not routes:
        return {"status": "no_route"}

    try:
        route = routes[0]
        result_code = route["result_code"]

        if result_code in (1, 101, 102, 103, 104, 105, 106, 107):
            return {"status": "no_route"}
        if result_code != 0:
            return {"status": "api_error"}

        summary = route["summary"]
        return {
            "status": "ok",
            "distance_m": summary["distance"],
            "duration_seconds": summary["duration"],
        }
    except (KeyError, TypeError):
        return {"status": "api_error"}

def get_driving_route(
    *,
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
    api_key: str,
) -> dict[str, str | int]:
    api_key = api_key.strip()
    if not api_key:
        return {"status": "auth_error"}

    params = urlencode({
        "origin": f"{origin_longitude},{origin_latitude}",
        "destination": f"{destination_longitude},{destination_latitude}",
        "summary": "true",
    })
    request = Request(
        f"https://apis-navi.kakaomobility.com/v1/directions?{params}",
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
        if error.code == 429 or provider_code == -10:
            return {"status": "rate_limited"}
        if error.code in (401, 403) or provider_code == -903:
            return {"status": "auth_error"}
        return {"status": "api_error"}
    except (OSError, ValueError):
        return {"status": "api_error"}

    if not isinstance(data, dict):
        return {"status": "api_error"}
    return parse_driving_response(data)