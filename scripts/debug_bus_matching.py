import json
from pathlib import Path

from backend.app.config import get_kakao_api_key
from backend.app.db.bus_static import load_bus_data
from backend.app.services.bus_matching import match_bus_segment
from backend.app.services.kakao_transit import get_transit_routes

def main():
    result = get_transit_routes(
        origin_latitude=33.45211112,
        origin_longitude=126.488483,
        destination_latitude=33.39022486,
        destination_longitude=126.2396912,
        api_key=get_kakao_api_key(),
    )
    if result["status"] != "ok":
        raise SystemExit(str(result))

    snapshot = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "sample_responses"
        / "bus_matching_input.json"
    )
    snapshot.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("비교용 경로 저장 : ", snapshot)

    catalog, stops = load_bus_data()
    for index, route in enumerate(result["routes"], start=1):
        for segment in route["segments"]:
            if segment["type"] != "BUS":
                continue
            if match_bus_segment(segment, catalog, stops):
                continue

            print("매칭 실패 경로 : ", index)
            print("노선 번호 : ", segment["route_numbers"])
            print("승하차 : ", segment["boarding_stop"], "→", segment["alighting_stop"])
            print("카카오 정류장 순서 : ", segment["stop_names"])

if __name__ == "__main__":
    main()