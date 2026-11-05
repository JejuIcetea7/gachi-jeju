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
    "summary": "true",
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