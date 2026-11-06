STOP_NAME_ALIASES = {
    ("102", "405002811", "제주버스터미널(가상정류소)"): "제주버스터미널",
}

def _normalize_stop_name(name: str) -> str:
    return "".join(name.split())

def match_bus_segment(
    segment: dict,
    routes: list[dict],
    stops_by_route: dict[str, list[dict]],
) -> list[dict]:
    expected_names = [
        _normalize_stop_name(name)
        for name in segment.get("stop_names") or []
    ]
    if len(expected_names) < 2:
        return []

    route_numbers = set(segment.get("route_numbers") or [])
    size = len(expected_names)
    matches = {}

    for route in routes:
        route_number = str(route.get("ROUTE_NUM", "")).strip()
        if route_number not in route_numbers:
            continue

        route_id = str(route["ROUTE_ID"])
        stops = sorted(
            stops_by_route.get(route_id, []),
            key=lambda stop: int(stop["STATION_ORD"]),
        )
        names = []
        for stop in stops:
            key = (route_number, str(stop["STATION_ID"]), stop["STATION_NM"])
            name = STOP_NAME_ALIASES.get(key, stop["STATION_NM"])
            names.append(_normalize_stop_name(name))
        orders = [int(stop["STATION_ORD"]) for stop in stops]

        for start in range(len(stops) - size + 1):
            end = start + size
            if names[start:end] != expected_names:
                continue

            expected_orders = list(range(orders[start], orders[start] + size))
            if orders[start:end] != expected_orders:
                continue

            boarding = stops[start]
            alighting = stops[end - 1]
            key = (route_id, orders[start], orders[end - 1])
            matches[key] = {
                "route_id": route_id,
                "route_number": route_number,
                "boarding_station_id": str(boarding["STATION_ID"]),
                "alighting_station_id": str(alighting["STATION_ID"]),
                "boarding_order": orders[start],
                "alighting_order": orders[end - 1],
            }
    
    return list(matches.values())