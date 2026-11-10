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
    "origin": "126.488483,33.45211112",
    "destination": "126.2396912,33.39022486",
    "summary": "false",
})
request = Request(
    f"https://apis-navi.kakaomobility.com/v1/directions?{params}",
    headers={"Authorization": f"KakaoAK {api_key}"},
)
context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

try:
    with urlopen(request, context=context, timeout=20) as response:
        print("HTTP:", response.status)
        data = json.load(response)
except HTTPError as error:
    raise SystemExit(f"HTTP {error.code}: {error.reason}") from None

routes = data.get("routes", [])
if not routes:
    raise SystemExit("응답에 경로가 없습니다.")

route = routes[0]
print("길찾기 결과 코드 : ", route["result_code"])
if route["result_code"] != 0:
    raise SystemExit(route.get("result_msg", "경로를 찾지 못했습니다."))

summary = route["summary"]
print("이동 거리(m) : ", summary["distance"])
print("예상 소요 시간(초) : ", summary["duration"])

sections = route.get("sections") or []
roads = [
    road
    for section in sections
    for road in (section.get("roads") or [])
]
point_count = sum(
    len(road.get("vertexes") or []) // 2
    for road in roads
)

print("상세 구간 수:", len(sections))
print("도로 정보 수:", len(roads))
print("경로 좌표쌍 수:", point_count)

if point_count < 2:
    raise SystemExit("선을 그릴 경로 좌표가 충분하지 않습니다.")

sample_path = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "sample_responses"
    / "kakao_driving_geometry_success.json"
)
sample_path.parent.mkdir(parents=True, exist_ok=True)
sample_path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print("상세 응답 저장:", sample_path)