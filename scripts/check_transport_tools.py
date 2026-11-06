import json

from backend.app.tools.bus import get_bus_trip
from backend.app.tools.driving import get_driving_time

ROUTE_INPUT = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
}

def main():
    driving = get_driving_time(**ROUTE_INPUT)
    print("자동차 결과 : ", driving)

    trip = get_bus_trip(**ROUTE_INPUT)
    print("버스 전체 상태 : ", trip["status"])
    routes = trip.get("routes", [])
    print("버스 경로 수 : ", len(routes))

    for index, route in enumerate(routes, start=1):
        infos = [
            segment["bus_info"]
            for segment in route["segments"]
            if segment["type"] == "BUS" and "bus_info" in segment
        ]
        print(
            f"경로 {index}",
            "매칭 : ", [info["status"] for info in infos],
            "실시간 : ", [info["realtime_status"] for info in infos],
        )

    if not routes:
        print("버스 상세 : ", trip)
        return

    route = routes[0]
    print("첫 경로 소요시간(초) : ", route["duration_seconds"])
    print("첫 경로 환승 횟수 : ", route["transfers"])

    for segment in route["segments"]:
        if segment["type"] != "BUS":
            continue

        print(segment["boarding_stop"], "→", segment["alighting_stop"])
        info = segment.get("bus_info", {})
        print("구간 상태 : ", info.get("status"), info.get("realtime_status"))

        for vehicle in info.get("vehicles", []):
            print(json.dumps({
                "route_number": vehicle["route_number"],
                "low_floor": vehicle["low_floor"],
                "arrival_minutes": vehicle["arrival_minutes"],
                "remaining_stops": vehicle["remaining_stops"],
                "last_stop_name": vehicle["last_stop_name"],
                "position": vehicle["position"],
            }, ensure_ascii=False))

    if "reason" in trip:
        print("상세 사유 : ", trip["reason"])

if __name__ == "__main__":
    main()