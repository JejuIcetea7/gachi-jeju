import json
import ssl
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
        "start_x": 126.2396912,
        "start_y": 33.39022486,
        "end_x": 126.2390,
        "end_y": 33.3945,
        "route_mode": "BROAD_FIRST",
    })
    request = Request(
        f"https://dapi.kakao.com/v2/routing/walk?{params}",
        headers={"Authorization": f"KakaoAK {api_key}"},
    )
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    try:
        with urlopen(request, context=context, timeout=20) as response:
            print("HTTP : ", response.status)
            data = json.load(response)
    except HTTPError as error:
        with error:
            try:
                body = json.load(error)
            except ValueError:
                body = {}

        print("HTTP : ", error.code)
        print(
            "카카오 오류 코드 : ",
            body.get("code") if isinstance(body, dict) else None,
        )
        raise SystemExit(1) from None

    print("도보 조회 상태 : ", data.get("status"))
    if data.get("status") != "OK":
        raise SystemExit(1) from None

    print("도보 조회 상태 : ", data.get("status"))
    if data.get("status") != "OK":
        raise SystemExit("도보 경로가 반환되지 않았습니다.")

    route = data["route"]
    properties = route["properties"]
    steps = [
        step
        for leg in route.get("legs", [])
        for step in leg.get("steps", [])
    ]

    print("도보 거리(m) : ", properties["totalDistance"])
    print("예상 소요시간(초) : ", properties["totalTime"])
    print("안내 단계 수 : ", len(steps))
    print(
        "경로 좌표 수 : ",
        sum(len(step.get("path", {}).get("points", [])) for step in steps),
    )
    for step in steps[:3]:
        print("안내 : ", step.get("properties", {}).get("guidance"))

    sample_path = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "sample_responses"
        / "kakao_walking_success.json"
    )
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("응답 저장 : ", sample_path)

if __name__ == "__main__":
    main()