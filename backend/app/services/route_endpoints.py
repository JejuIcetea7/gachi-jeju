import math

from .lodging_planning import _night_choices


EARTH_RADIUS_M = 6_371_000.0
ENDPOINT_TOLERANCE_M = 100.0


def _coordinate(value: dict | None) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None

    latitude = value.get("latitude")
    longitude = value.get("longitude")
    if (
        type(latitude) not in (int, float)
        or type(longitude) not in (int, float)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None

    return float(latitude), float(longitude)


def _visit_coordinate(item: dict | None) -> tuple[float, float] | None:
    if (
        not isinstance(item, dict)
        or item.get("item_type") != "visit"
        or item.get("verified") is not True
    ):
        return None
    return _coordinate(item)


def _distance_m(first: tuple, second: tuple) -> float:
    latitude_1, longitude_1 = map(math.radians, first)
    latitude_2, longitude_2 = map(math.radians, second)
    value = (
        math.sin((latitude_2 - latitude_1) / 2) ** 2
        + math.cos(latitude_1)
        * math.cos(latitude_2)
        * math.sin((longitude_2 - longitude_1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(
        math.sqrt(min(1.0, max(0.0, value)))
    )


def validate_route_endpoints(
    plan: dict,
    route_calls: list[dict] | None,
    *,
    departure: dict | None = None,
    return_to_origin: bool = False,
    lodging_stays: list[dict] | None = None,
) -> list[str]:
    """DB 검증 후의 일정과 길찾기에 전달한 좌표를 대조합니다.

    확인된 방문 장소의 바로 앞뒤 이동과 연속 이동의 연결을 검사합니다.
    출발지는 1일차 첫 이동, 귀환은 마지막 일차의 마지막 이동에만 적용합니다.
    선택한 숙소는 해당 일차 마지막 이동의 도착과 다음 일차 첫 이동의 출발에 대조합니다.
    비교 기준 좌표가 없으면 해당 끝점은 검사하지 않습니다.
    도로선 paths는 사용하지 않으며, 입력은 변경하지 않습니다.
    """
    records = {}
    duplicate_ids = set()
    for record in route_calls if isinstance(route_calls, list) else []:
        if not isinstance(record, dict):
            continue
        route_id = record.get("route_id")
        if not isinstance(route_id, str) or not route_id.strip():
            continue
        if route_id in records:
            duplicate_ids.add(route_id)
        records[route_id] = record

    def find_record(item):
        route_id = item.get("route_id")
        if not isinstance(route_id, str) or route_id in duplicate_ids:
            return None
        return records.get(route_id)

    departure_coordinate = _coordinate(departure)
    stay_choices = _night_choices(lodging_stays)
    days = plan["days"]
    errors = []

    for day_index, day in enumerate(days):
        items = day["items"]
        for index, item in enumerate(items):
            if item.get("item_type") != "move":
                continue

            label = f"{day['day']}일차 {index + 1}번째 일정 '{item['title']}'"
            record = find_record(item)
            if record is None:
                errors.append(f"{label}: 좌표를 대조할 경로 기록을 확인하지 못했습니다.")
                continue

            previous = items[index - 1] if index > 0 else None
            following = items[index + 1] if index + 1 < len(items) else None
            origin = _visit_coordinate(previous)
            destination = _visit_coordinate(following)

            if index == 0:
                origin = (
                    departure_coordinate if day.get("day") == 1
                    else _coordinate(stay_choices.get(day["day"] - 1))
                )
            if index == len(items) - 1 and day_index < len(days) - 1:
                destination = _coordinate(stay_choices.get(day["day"]))
            if (
                return_to_origin is True
                and day_index == len(days) - 1
                and index == len(items) - 1
            ):
                destination = departure_coordinate

            for field, expected, endpoint_label in (
                ("origin", origin, "출발"),
                ("destination", destination, "도착"),
            ):
                if expected is None:
                    continue

                actual = _coordinate(record.get(field))
                if actual is None:
                    errors.append(
                        f"{label}: 조회 경로의 {endpoint_label} 좌표가 올바르지 않습니다."
                    )
                    continue

                distance = _distance_m(expected, actual)
                if distance > ENDPOINT_TOLERANCE_M:
                    errors.append(
                        f"{label}: 조회 경로의 {endpoint_label} 위치가 "
                        f"일정의 위치와 {distance:.0f}m 떨어져 있습니다. "
                        f"허용 범위는 {ENDPOINT_TOLERANCE_M:.0f}m입니다."
                    )

            if previous is not None and previous.get("item_type") == "move":
                previous_record = find_record(previous)
                if previous_record is None:
                    continue
                previous_destination = _coordinate(
                    previous_record.get("destination")
                )
                current_origin = _coordinate(record.get("origin"))
                if previous_destination is None or current_origin is None:
                    errors.append(f"{label}: 연속 이동의 연결 좌표가 올바르지 않습니다.")
                elif (
                    _distance_m(previous_destination, current_origin)
                    > ENDPOINT_TOLERANCE_M
                ):
                    errors.append(
                        f"{label}: 직전 이동의 도착 위치와 이번 이동의 "
                        "출발 위치가 일치하지 않습니다."
                    )

    return errors