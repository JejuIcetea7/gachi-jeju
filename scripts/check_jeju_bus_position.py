import json
import ssl
from urllib.request import urlopen

import truststore

BASE_URL = "https://bus.jeju.go.kr/api"
VERIFIED_ROUTE_IDS = {"405320213", "405320237", "405320239"}

def get_rows(path, context):
    with urlopen(f"{BASE_URL}/{path}", context=context, timeout=30) as response:
        return json.load(response)

def vehicle_key(row):
    return str(row["ROUTE_ID"]), str(row["VH_ID"])

def main():
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    arrivals = get_rows("searchArrivalInfoList.do?station_id=405001248", context)
    candidates = [
        row for row in arrivals
        if str(row.get("ROUTE_ID")) in VERIFIED_ROUTE_IDS and row.get("VH_ID")
    ]

    print("확인된 노선의 도착 차량 수 : ", len(candidates))
    if not candidates:
        return

    positions = {
        vehicle_key(row): row
        for row in get_rows("searchBusAllocList.do", context)
        if row.get("ROUTE_ID") and row.get("VH_ID")
    }

    for bus in candidates:
        position = positions.get(vehicle_key(bus))
        print(json.dumps({
            "ROUTE_ID": bus["ROUTE_ID"],
            "VH_ID": bus["VH_ID"],
            "LOW_PLATE_TP": bus.get("LOW_PLATE_TP"),
            "PREDICT_TRAV_TM": bus.get("PREDICT_TRAV_TM"),
            "CURR_STATION_NM": bus.get("CURR_STATION_NM"),
            "GPS_MATCHED": position is not None,
            "LOCAL_X": position.get("LOCAL_X") if position else None,
            "LOCAL_Y": position.get("LOCAL_Y") if position else None,
        }, ensure_ascii=False))

if __name__ == "__main__":
    main()