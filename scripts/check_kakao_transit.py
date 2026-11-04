import json
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

env_path = Path(__file__).resolve().parents[1] / ".env"
api_key = ""

for line in env_path.read_text(encoding="utf-8-sig").splitlines():
    name, _, value = line.partition("=")
    if name.strip() == "KAKAO_REST_API_KEY":
        api_key = value.strip()
        break
    
if not api_key:
    raise SystemExit(".env에 KAKAO_REST_API_KEY 값을 입력해 주세요.")

params = urlencode({
    "start_x": "126.488483",
    "start_y": "33.45211112",
    "end_x": "126.2396912",
    "end_y": "33.39022486",
})
request = Request(
    f"https://dapi.kakao.com/v2/routing/publictraffic?{params}",
    headers={"Authorization": f"KakaoAK {api_key}"},
)
context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

try:
    with urlopen(request, context=context, timeout=20) as response:
        print("HTTP:", response.status)
        data = json.load(response)
except HTTPError as error:
    raise SystemExit(f"HTTP {error.code}: {error.reason}") from None

print("경로 조회 상태:", data.get("status"))
routes = data.get("routes", [])
print("경로 수:", len(routes))

if data.get("status") != "OK" or not routes:
    raise SystemExit("조회된 대중교통 경로가 없습니다.")

route = routes[0]
properties = route["properties"]
print("첫 번째 경로 예상 소요 시간(초):", properties["totalTime"])
print("환승 횟수:", properties["transfers"])

for step in route["steps"]:
    details = step["properties"]
    if details["type"] == "WALKING":
        print("도보 거리(m):", details["distance"])
    elif details["type"] == "BUS":
        bus_names = [vehicle["name"] for vehicle in details.get("vehicles", [])]
        print("버스 노선:", ", ".join(bus_names) or "미제공")
        stops = details.get("stops", [])
        if stops:
            print("승하차 정류장:", stops[0]["name"], "→", stops[-1]["name"])