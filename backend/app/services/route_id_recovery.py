"""이번 시도의 유일한 경로 근거로 누락·불일치 경로 ID를 연결합니다."""

from collections import Counter
from copy import deepcopy

from .lodging_planning import _night_choices
from .plan_routes import normalize_transport_mode
from .route_endpoints import _coordinate, _same_point


def _place_coordinates(tool_trace):
    coordinates, conflicting, detailed = {}, set(), set()
    for record in tool_trace if isinstance(tool_trace, list) else []:
        if not isinstance(record, dict):
            continue
        output = record.get("output")
        if not isinstance(output, dict) or output.get("status") != "ok":
            continue
        if record.get("tool") in ("search_places", "search_places_by_name"):
            places = output.get("places")
            for place in places if isinstance(places, list) else []:
                if not isinstance(place, dict):
                    continue
                place_id = place.get("place_id")
                if not isinstance(place_id, str) or not place_id.strip():
                    continue
                point = _coordinate(place)
                if point is None or (
                    place_id in coordinates and not _same_point(coordinates[place_id], point)
                ):
                    conflicting.add(place_id)
                elif place_id not in coordinates:
                    coordinates[place_id] = point
        elif record.get("tool") == "get_place_details":
            arguments = record.get("input")
            place_id = output.get("place_id")
            if (
                isinstance(place_id, str) and isinstance(arguments, dict)
                and arguments.get("place_id") == place_id
            ):
                detailed.add(place_id)
    return {
        place_id: point for place_id, point in coordinates.items()
        if place_id in detailed and place_id not in conflicting
    }


def _matches_move(item, record, origin, destination):
    mode = normalize_transport_mode(item.get("transport"))
    seconds = record.get("duration_seconds")
    minutes = item.get("duration_minutes")
    return (
        mode is not None and record.get("transport") == mode
        and type(seconds) is int and seconds >= 0
        and type(minutes) is int and minutes >= 1
        and abs(minutes - (seconds + 59) // 60) <= 1
        and _same_point(origin, _coordinate(record.get("origin")))
        and _same_point(destination, _coordinate(record.get("destination")))
    )


def restore_plan_route_ids(
    plan, route_calls, *, tool_trace=None, departure=None,
    return_to_origin=False, lodging_stays=None, restaurants=None, daily_departure=False,
):
    """양 끝점·이동수단·시간이 일치하는 이번 시도의 미사용 경로가 하나일 때 보완합니다.

    방문좌표는 이번 검색과 동일 ID의 상세 조회가 성공한 장소만 사용합니다.
    기존 ID는 변경하지 않으며, 모호한 기록과 알 수 없는 경계는 보완하지 않습니다.
    원래 시간·장소·조건과 후속 검증은 유지합니다.
    """
    restored = deepcopy(plan)
    records = []
    for record in route_calls if isinstance(route_calls, list) else []:
        if isinstance(record, dict):
            route_id = record.get("route_id")
            if isinstance(route_id, str) and route_id.strip() and len(route_id) <= 80:
                records.append(record)
    counts = Counter(record["route_id"] for record in records)
    available = [record for record in records if counts[record["route_id"]] == 1]
    used = {
        item["route_id"] for day in restored["days"] for item in day["items"]
        if item.get("item_type") == "move"
        and isinstance(item.get("route_id"), str) and item["route_id"] in counts
    }
    coordinates = _place_coordinates(tool_trace)
    if restaurants is None:
        restaurants = {}
    origin_point = _coordinate(departure)
    stays = _night_choices(lodging_stays)
    days = restored["days"]
    for day_index, day in enumerate(days):
        items = day["items"]
        for index, item in enumerate(items):
            if item.get("item_type") != "move":
                continue
            route_id = item.get("route_id")
            if isinstance(route_id, str) and route_id in counts:
                continue
            previous = items[index - 1] if index > 0 else None
            following = items[index + 1] if index + 1 < len(items) else None
            origin = None
            destination = None
            if previous is not None and previous.get("item_type") == "visit":
                origin = coordinates.get(previous.get("place_id"))
                if previous.get("restaurant_ref") is not None:
                    origin = _coordinate(restaurants.get(previous["restaurant_ref"]))
            if following is not None and following.get("item_type") == "visit":
                destination = coordinates.get(following.get("place_id"))
                if following.get("restaurant_ref") is not None:
                    destination = _coordinate(restaurants.get(following["restaurant_ref"]))
            if index == 0:
                origin = (
                    origin_point if day["day"] == 1 or daily_departure
                    else _coordinate(stays.get(day["day"] - 1))
                )
            if index == len(items) - 1:
                if return_to_origin and (day_index == len(days) - 1 or daily_departure):
                    destination = origin_point
                elif day_index < len(days) - 1:
                    destination = _coordinate(stays.get(day["day"]))
            if origin is None or destination is None:
                continue
            candidates = [
                record for record in available
                if record["route_id"] not in used and _matches_move(item, record, origin, destination)
            ]
            if len(candidates) == 1:
                item["route_id"] = candidates[0]["route_id"]
                used.add(item["route_id"])
    return restored
