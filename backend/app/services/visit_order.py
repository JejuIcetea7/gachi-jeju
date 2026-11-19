"""확인된 좌표로 방문 순서 후보를 계산하며 시간표나 실제 경로를 만들지 않습니다."""

import math


MAX_VISIT_COUNT = 12
MIN_IMPROVEMENT_M = 1.0


def _coordinate(value):
    if not isinstance(value, dict):
        raise ValueError("위도와 경도를 함께 보내 주세요.")
    coordinates = []
    for field, minimum, maximum in (
        ("latitude", -90, 90), ("longitude", -180, 180),
    ):
        number = value.get(field)
        if type(number) not in (int, float):
            raise ValueError("좌표는 유한한 숫자여야 합니다.")
        if not minimum <= number <= maximum:
            raise ValueError("좌표가 지원 범위를 벗어났습니다.")
        coordinates.append(float(number))
    return tuple(coordinates)


def validate_visit_order_request(place_ids, *, origin=None, destination=None, fixed_place_ids=None):
    if not isinstance(place_ids, list) or not 1 <= len(place_ids) <= MAX_VISIT_COUNT:
        raise ValueError(f"방문 장소는 1~{MAX_VISIT_COUNT}개로 보내 주세요.")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 80 for value in place_ids):
        raise ValueError("유효한 장소 ID를 보내 주세요.")
    if len(set(place_ids)) != len(place_ids):
        raise ValueError("한 번의 순서 비교에는 같은 장소 ID를 중복할 수 없습니다.")
    if fixed_place_ids is not None:
        if not isinstance(fixed_place_ids, list) or any(
            not isinstance(value, str) or value not in place_ids for value in fixed_place_ids
        ):
            raise ValueError("고정 장소 ID는 방문 목록에 포함되어야 합니다.")
        if len(set(fixed_place_ids)) != len(fixed_place_ids):
            raise ValueError("고정 장소 ID를 중복할 수 없습니다.")
    for point in (origin, destination):
        if point is not None:
            _coordinate(point)


def _distance_m(first, second):
    first_latitude, first_longitude = map(math.radians, first)
    second_latitude, second_longitude = map(math.radians, second)
    value = (
        math.sin((second_latitude - first_latitude) / 2) ** 2
        + math.cos(first_latitude) * math.cos(second_latitude)
        * math.sin((second_longitude - first_longitude) / 2) ** 2
    )
    value = min(1.0, max(0.0, value))
    return 2 * 6_371_000.0 * math.asin(math.sqrt(value))


def _path_distance(order, distances, origin_costs, destination_costs):
    if not order:
        return 0.0
    total = origin_costs[order[0]] + destination_costs[order[-1]]
    for first, second in zip(order, order[1:]):
        total += distances[first][second]
    return total


def _shortest_segment(indices, distances, origin_costs, destination_costs):
    if len(indices) < 2:
        return list(indices)
    # 상태는 방문한 장소의 비트 집합과 마지막 장소이며, 동률이면 원래 인덱스 순으로 정합니다.
    states = {}
    for position, index in enumerate(indices):
        states[(1 << position, position)] = (origin_costs[index], (index,))
    for mask in range(1, 1 << len(indices)):
        for last_position, last_index in enumerate(indices):
            state = states.get((mask, last_position))
            if state is None:
                continue
            cost, order = state
            for next_position, next_index in enumerate(indices):
                if mask & (1 << next_position):
                    continue
                key = (mask | (1 << next_position), next_position)
                candidate = (cost + distances[last_index][next_index], order + (next_index,))
                if key not in states or candidate < states[key]:
                    states[key] = candidate
    completed = []
    full_mask = (1 << len(indices)) - 1
    for position, index in enumerate(indices):
        cost, order = states[(full_mask, position)]
        completed.append((cost + destination_costs[index], order))
    best_cost, best_order = min(completed)
    original_cost = _path_distance(indices, distances, origin_costs, destination_costs)
    if original_cost - best_cost < MIN_IMPROVEMENT_M:
        return list(indices)
    return list(best_order)


def calculate_visit_order(places, *, origin=None, destination=None, fixed_place_ids=None):
    """고정 장소 사이 구간별 직선거리 합을 비교합니다. 입력과 장소 구성은 변경하지 않습니다."""
    if not isinstance(places, list) or any(not isinstance(place, dict) for place in places):
        raise ValueError("장소 목록을 보내 주세요.")
    place_ids = [place.get("place_id") for place in places]
    validate_visit_order_request(
        place_ids, origin=origin, destination=destination, fixed_place_ids=fixed_place_ids,
    )
    coordinates = [_coordinate(place) for place in places]
    origin_point = _coordinate(origin) if origin is not None else None
    destination_point = _coordinate(destination) if destination is not None else None
    distances = []
    for first in coordinates:
        distances.append([_distance_m(first, second) for second in coordinates])
    origin_costs = []
    destination_costs = []
    for point in coordinates:
        origin_costs.append(_distance_m(origin_point, point) if origin_point is not None else 0.0)
        destination_costs.append(_distance_m(point, destination_point) if destination_point is not None else 0.0)
    fixed = set(fixed_place_ids or [])
    boundaries = [-1]
    boundaries.extend(index for index, place_id in enumerate(place_ids) if place_id in fixed)
    boundaries.append(len(places))
    suggested = []
    for first, last in zip(boundaries, boundaries[1:]):
        segment_origin = origin_costs if first == -1 else distances[first]
        segment_destination = destination_costs if last == len(places) else distances[last]
        suggested.extend(_shortest_segment(
            list(range(first + 1, last)), distances, segment_origin, segment_destination,
        ))
        if last < len(places):
            suggested.append(last)
    original = list(range(len(places)))
    original_distance = _path_distance(original, distances, origin_costs, destination_costs)
    suggested_distance = _path_distance(suggested, distances, origin_costs, destination_costs)
    return {
        "basis": "straight_line_distance",
        "original_place_ids": list(place_ids),
        "suggested_place_ids": [place_ids[index] for index in suggested],
        "fixed_place_ids": [place_id for place_id in place_ids if place_id in fixed],
        "changed": suggested != original,
        "original_straight_line_distance_m": round(original_distance, 1),
        "suggested_straight_line_distance_m": round(suggested_distance, 1),
        "distance_reduction_m": round(max(0.0, original_distance - suggested_distance), 1),
        "note": (
            "직선거리 기준의 순서 후보입니다. 실제 이동시간·접근성의 최적 경로가 아닙니다. "
            "방문일·고정 시각·휴무·운영시간·필수 접근 조건을 유지하고 실제 이동을 다시 조회하세요."
        ),
    }
