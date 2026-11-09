import json
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

# 제주도 범위 (경도 최소, 위도 최소, 경도 최대, 위도 최대). 추자도는 빠진다.
JEJU_RECT = "126.08,33.10,127.00,33.60"

def parse_keyword_response(data: dict) -> dict:
    """카카오 키워드 장소 검색 응답에서 첫 번째 장소의 이름, 주소, 좌표를 읽습니다."""
    documents = data.get("documents")

    if not isinstance(documents, list):
        return {"status": "api_error"}
    if not documents:
        return {"status": "not_found"}

    try:
        first_place = documents[0]
        address = first_place["road_address_name"]
        if not address:
            address = first_place["address_name"]

        return {
            "status": "ok",
            "name": first_place["place_name"],
            "address": address,
            "latitude": float(first_place["y"]),
            "longitude": float(first_place["x"]),
        }
    except (KeyError, TypeError, ValueError):
        return {"status": "api_error"}

def search_keyword_place(*, keyword: str, api_key: str) -> dict:
    api_key = api_key.strip()
    if not api_key:
        return {"status": "auth_error"}

    params = urlencode({
        "query": keyword,
        "rect": JEJU_RECT,
        "size": 1,
    })
    request = Request(
        f"https://dapi.kakao.com/v2/local/search/keyword.json?{params}",
        headers={"Authorization": f"KakaoAK {api_key}"},
    )
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    try:
        with urlopen(request, context=context, timeout=20) as response:
            data = json.load(response)
    except HTTPError as error:
        if error.code == 429:
            return {"status": "rate_limited"}
        if error.code in (401, 403):
            return {"status": "auth_error"}
        return {"status": "api_error"}
    except (OSError, ValueError):
        return {"status": "api_error"}

    if not isinstance(data, dict):
        return {"status": "api_error"}
    return parse_keyword_response(data)
