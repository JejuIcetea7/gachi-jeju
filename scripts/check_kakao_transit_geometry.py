import json
import ssl
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

from backend.app.config import get_kakao_api_key


def main():
    api_key = get_kakao_api_key()
    if not api_key:
        raise SystemExit("KAKAO_REST_API_KEY 설정을 확인해 주세요.")

    params = urlencode({
        "start_x": 126.488483,
        "start_y": 33.45211112,
        "end_x": 126.2396912,
        "end_y": 33.39022486,
        "input_coord": "WGS84",
        "output_coord": "WGS84",
    })
    request = Request(
        f"https://dapi.kakao.com/v2/routing/publictraffic?{params}",
        headers={"Authorization": f"KakaoAK {api_key}"},
    )
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    try:
        with urlopen(request, context=context, timeout=30) as response:
            print("HTTP:", response.status)
            data = json.load(response)
    except HTTPError as error:
        with error:
            raise SystemExit(
                f"대중교통 조회 실패: HTTP {error.code}"
            ) from None
    except (OSError, ValueError, HTTPException):
        raise SystemExit(
            "대중교통 응답을 받거나 읽지 못했습니다."
        ) from None

    if not isinstance(data, dict):
        raise SystemExit("응답 형식을 확인해 주세요.")

    print("경로 조회 상태:", data.get("status"))
    raw_routes = data.get("routes")
    if data.get("status") != "OK" or not isinstance(raw_routes, list):
        raise SystemExit("조회된 대중교통 경로가 없습니다.")

    routes = [
        route for route in raw_routes
        if isinstance(route, dict)
        and isinstance(route.get("properties"), dict)
        and route["properties"].get("type") == "BUS"
    ]
    print("버스 경로 수:", len(routes))
    if not routes:
        raise SystemExit("조회된 버스 경로가 없습니다.")

    route = routes[0]
    sample = {
        "status": data["status"],
        "properties": data.get("properties"),
        "routes": [route],
    }
    sample_path = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "sample_responses"
        / "kakao_transit_geometry_success.json"
    )
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(
        json.dumps(sample, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("샘플 저장 경로 수: 1")

    total_points = 0
    for index, step in enumerate(route.get("steps", []), 1):
        details = step.get("properties") or {}
        path = step.get("path") or {}
        points = path.get("points") if isinstance(path, dict) else None
        points = points if isinstance(points, list) else []
        total_points += len(points)

        print(
            f"구간 {index}: {details.get('type')} / "
            f"좌표 {len(points)}개 / 첫 좌표 {points[:1]}"
        )

    print("전체 좌표 수:", total_points)
    print("응답 저장:", sample_path)


if __name__ == "__main__":
    main()