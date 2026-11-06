import json
import ssl
from itertools import islice
from urllib.parse import urlencode
from urllib.request import urlopen

import truststore

def iter_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_objects(child)

params = urlencode({"station_id": "405000153"})
url = f"https://bus.jeju.go.kr/api/searchArrivalInfoList.do?{params}"
context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

with urlopen(url, context=context, timeout=20) as response:
    print("HTTP:", response.status)
    data = json.load(response)

fields = ("ROUTE_NUM", "PREDICT_TRAV_TM", "LOW_PLATE_TP")
rows = [
    {field: row.get(field) for field in fields}
    for row in iter_objects(data)
    if "ROUTE_NUM" in row or "LOW_PLATE_TP" in row
]

print("확인한 도착 정보 건수:", len(rows))
for row in rows[:3]:
    print(json.dumps(row, ensure_ascii=False))

if not rows:
    print("응답 객체의 필드명:", [list(row) for row in islice(iter_objects(data), 5)])