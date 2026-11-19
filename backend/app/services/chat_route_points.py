"""대화 요청의 장소 참조를 확인된 좌표로 바꾸어 이동 도구에 전달합니다."""

from copy import deepcopy
import inspect
import json
from threading import Lock
from uuid import uuid4
import sqlite3

from .chat_request_control import check_chat_request_control
from .kakao_local import RESTAURANT_CATEGORY_CODE
from .lodging_planning import LodgingStay, validate_lodging_options, _night_choices
from .route_endpoints import _coordinate
from ..tools.places import normalize_place_name, read_rows
from functools import wraps

def _matching_details(place_id, output):
    return (
        isinstance(place_id, str) and bool(place_id.strip()) and len(place_id) <= 80
        and isinstance(output, dict) and output.get("status") == "ok"
        and output.get("place_id") == place_id
    )


def bind_current_place_details(details_tool):
    """각 Agent 실행에서 성공한 상세 조회만 이동·순서 비교의 선행 근거로 사용합니다."""
    confirmed = set()
    lock = Lock()

    @wraps(details_tool)
    def get_place_details(*, place_id: str, visit_dates: list[str] | None = None) -> dict:
        result = details_tool(place_id=place_id, visit_dates=visit_dates)
        if _matching_details(place_id, result):
            with lock:
                confirmed.add(place_id)
        return result

    def require_details(place_ids):
        if not isinstance(place_ids, list):
            return None
        with lock:
            missing = list(dict.fromkeys(
                place_id for place_id in place_ids
                if isinstance(place_id, str) and place_id.strip()
                and place_id not in confirmed
            ))
        if not missing:
            return None
        return {
            "status": "details_required",
            "place_ids": missing,
            "message": "이번 생성 시도에서 get_place_details를 먼저 호출하고 방문 날짜를 확인한 뒤 다시 요청해 주세요.",
        }

    return get_place_details, require_details


def read_confirmed_restaurants(tool_trace):
    """이번 생성 시도에서 카카오가 음식점으로 반환한 위치만 읽습니다."""
    restaurants = {}
    for record in tool_trace:
        if not isinstance(record, dict) or record.get("tool") != "find_location":
            continue
        output = record.get("output")
        if (
            not isinstance(output, dict) or output.get("status") != "ok"
            or output.get("category_group_code") != RESTAURANT_CATEGORY_CODE
            or output.get("lookup_performed") is False
        ):
            continue
        reference = output.get("point_ref")
        name = output.get("name")
        address = output.get("address")
        coordinates = _coordinate(output)
        if (
            isinstance(reference, str) and reference.startswith("found:") and 6 < len(reference) <= 80
            and isinstance(name, str) and name.strip()
            and isinstance(address, str) and address.strip()
            and coordinates is not None
        ):
            restaurants[reference] = {
                "name": name, "address": address,
                "latitude": coordinates[0], "longitude": coordinates[1],
            }
    return restaurants


def validate_plan_restaurants(plan, restaurants):
    errors = []
    for day in plan["days"]:
        for item in day["items"]:
            reference = item.get("restaurant_ref")
            if reference is None:
                continue
            if item["item_type"] != "visit" or item.get("place_id") is not None or reference not in restaurants:
                errors.append(
                    f"{day['day']}일차 '{item['title']}': 식사 방문에는 이번 시도에서 확인한 음식점 참조만 사용하고 DB 장소 ID는 비워 주세요."
                )
    return errors


def validate_plan_place_details(plan, tool_trace):
    """이전 대화·실패한 시도·좌표 조회만으로 상세 조회를 대신하지 않습니다."""
    if plan is None:
        return []
    confirmed = set()
    for record in tool_trace if isinstance(tool_trace, list) else []:
        if not isinstance(record, dict) or record.get("tool") != "get_place_details":
            continue
        arguments = record.get("input")
        if isinstance(arguments, dict) and _matching_details(
            arguments.get("place_id"), record.get("output"),
        ):
            confirmed.add(arguments["place_id"])
    return [
        f"{day['day']}일차 '{item['title']}': 이번 요청에서 장소 상세정보를 다시 확인해 주세요."
        for day in plan["days"] for item in day["items"]
        if item.get("item_type") == "visit" and item.get("restaurant_ref") is None
        and item.get("place_id") not in confirmed
    ]

def validate_plan_visit_order(plan, options, tool_trace):
    """하루 전체 비교 중 최종 선택 위치를 반영한 근거가 있는지 확인합니다.

    작은 구간으로 나눈 비교와 순서 비교를 사용하지 않은 기존 응답에는 적용하지 않습니다.
    실제 이동시간·고정 시각 때문에 제안 순서와 최종 순서가 다른 것은 허용합니다.
    """
    if plan is None:
        return []
    day_places = {
        day["day"]: [
            item.get("place_id") for item in day["items"]
            if item.get("item_type") == "visit" and item.get("restaurant_ref") is None
        ]
        for day in plan["days"]
    }
    comparisons = {}
    for record in tool_trace if isinstance(tool_trace, list) else []:
        if not isinstance(record, dict) or record.get("tool") != "suggest_visit_order":
            continue
        arguments, output = record.get("input"), record.get("output")
        if (
            not isinstance(arguments, dict) or not isinstance(output, dict)
            or output.get("status") != "ok"
        ):
            continue
        day = arguments.get("day")
        ids = arguments.get("place_ids")
        expected_ids = day_places.get(day) if type(day) is int else None
        if (
            expected_ids is None or len(expected_ids) < 2
            or not isinstance(ids, list) or any(not isinstance(value, str) for value in ids)
            or len(ids) != len(expected_ids) or len(set(ids)) != len(ids)
            or set(ids) != set(expected_ids)
            or type(output.get("day")) is not int or output["day"] != day
        ):
            continue
        comparisons.setdefault(day, []).append(output)

    stays = _night_choices(options.get("lodging_stays"))
    departure = _coordinate({
        "latitude": options.get("departure_latitude"),
        "longitude": options.get("departure_longitude"),
    })
    final_day = max(day_places, default=0)
    errors = []
    daily_departure = options.get("lodging_status") == "not_needed"
    for day, outputs in comparisons.items():
        origin = departure if day == 1 or daily_departure else _coordinate(stays.get(day - 1))
        if day < final_day and not daily_departure:
            destination = _coordinate(stays.get(day))
        else:
            destination = departure if options.get("return_to_origin") is True else None
        last_errors = []
        for output in outputs:
            candidate_errors = []
            for field, expected, label in (
                ("origin", origin, "출발"), ("destination", destination, "도착"),
            ):
                actual_value = output.get(field)
                actual = _coordinate(actual_value)
                if expected is None:
                    matches = actual_value is None
                else:
                    matches = actual is not None and all(
                        abs(first - second) <= 0.000001
                        for first, second in zip(expected, actual)
                    )
                if not matches:
                    candidate_errors.append(
                        f"{day}일차: 방문 순서 비교의 {label} 위치가 최종 여행 조건과 "
                        "일치하지 않습니다. 현재 선택한 출발지·숙소·귀환 조건으로 다시 비교해 주세요."
                    )
            if not candidate_errors:
                last_errors = []
                break
            last_errors = candidate_errors
        errors.extend(last_errors)
    return errors


def read_chat_route_payload(messages):
    """마지막 서버 요청만 읽으며 대화 기록과 이전 일정은 위치 근거로 사용하지 않습니다."""
    try:
        if not messages or messages[-1].get("role") != "user":
            return None
        payload = json.loads(messages[-1]["content"])
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("message"), str)
            or not isinstance(payload.get("options"), dict)
            or not isinstance(payload.get("default_departure"), dict)
            or not isinstance(payload.get("reference_date"), str)
        ):
            return None
        return payload
    except (ValueError, TypeError, AttributeError, KeyError):
        return None


def _find_saved_restaurant(current_plan, keyword):
    if not isinstance(current_plan, dict) or not isinstance(keyword, str):
        return None
    keyword_key = normalize_place_name(keyword)
    matches = {}
    for day in current_plan.get("days", []):
        for item in day["items"]:
            address = item.get("restaurant_address")
            if item["item_type"] != "visit" or not address:
                continue
            name = item["title"]
            full_name = normalize_place_name(f"{name} {address}")
            if keyword_key in (normalize_place_name(name), full_name, normalize_place_name(f"{name}, {address}")):
                matches[full_name] = (name, address)
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def _matches_saved_restaurant(result, name, address):
    return (
        result.get("category_group_code") == RESTAURANT_CATEGORY_CODE
        and isinstance(result.get("name"), str) and isinstance(result.get("address"), str)
        and normalize_place_name(result["name"]) == normalize_place_name(name)
        and normalize_place_name(result["address"]) == normalize_place_name(address)
    )


def bind_chat_route_tools(
    payload, *, location_tool, order_tool, driving_tool, bus_tool, walking_tool,
    require_details=None,
):
    """한 Agent 실행 안에서만 장소·선택 숙소·위치 조회 결과를 공유합니다."""
    options = deepcopy(payload["options"])
    points = {}
    lock = Lock()
    has_refresh = "refresh" in inspect.signature(location_tool).parameters

    def point(reference, name, value):
        coordinate = _coordinate(value)
        if coordinate is None or not isinstance(name, str) or not name.strip():
            return None
        return {
            "point_ref": reference, "name": name,
            "latitude": coordinate[0], "longitude": coordinate[1],
        }

    departure = point("departure", options.get("departure"), {
        "latitude": options.get("departure_latitude"),
        "longitude": options.get("departure_longitude"),
    })
    departure_name = None
    if departure is not None:
        points["departure"] = departure
        departure_name = normalize_place_name(departure["name"])
    try:
        stays = [LodgingStay.model_validate(value)
                 for value in options.get("lodging_stays", [])]
        days = options.get("trip_days")
        if days is not None and (type(days) is not int or days < 1):
            raise ValueError("invalid trip_days")
        validate_lodging_options(days, options.get("lodging_status"), stays)
    except (ValueError, TypeError):
        stays = []
    for stay in stays:
        reference = f"stay:{stay.night}"
        points[reference] = point(reference, stay.name, stay.model_dump())

    def resolve(reference):
        if not isinstance(reference, str) or not reference or len(reference) > 100:
            return None, {"status": "invalid_input", "message": "유효한 point_ref를 사용해 주세요."}
        with lock:
            if reference in points:
                return deepcopy(points[reference]), None
            if not reference.startswith("place:") or not reference[6:] or len(reference[6:]) > 80:
                return None, {
                    "status": "not_found",
                    "message": "이번 요청에서 확인한 위치 참조가 없습니다. 필요한 위치를 조회해 주세요.",
                }
            place_id = reference[6:]
            if require_details is not None:
                error = require_details([place_id])
                if error is not None:
                    return None, error
            try:
                rows = read_rows(
                    "SELECT place_id, name, latitude, longitude FROM places WHERE place_id = ?",
                    (place_id,),
                )
            except sqlite3.Error:
                return None, {"status": "db_error"}
            if not isinstance(rows, list):
                return None, {"status": "invalid_data"}
            if not rows:
                return None, {"status": "not_found"}
            if len(rows) != 1:
                return None, {"status": "invalid_data"}
            try:
                row = dict(rows[0])
                resolved = point(reference, row.get("name"), row)
            except (ValueError, TypeError):
                return None, {"status": "invalid_data"}
            if row.get("place_id") != place_id or resolved is None:
                return None, {"status": "invalid_data"}
            points[reference] = resolved
            return deepcopy(resolved), None

    def endpoints(origin_ref, destination_ref):
        arguments, labels = {}, {}
        for field, reference in (("origin", origin_ref), ("destination", destination_ref)):
            if reference is None:
                labels[field] = None
                continue
            resolved, error = resolve(reference)
            if error is not None:
                return None, None, error
            labels[field] = resolved
            arguments[field + "_latitude"] = resolved["latitude"]
            arguments[field + "_longitude"] = resolved["longitude"]
        return arguments, labels, None

    def find_location(*, keyword: str, refresh: bool = False) -> dict:
        """이름·주소로 위치를 확인하고 이번 실행에서만 사용할 point_ref를 반환합니다.

        검색어가 좌표가 있는 출발지 이름과 일치하면 기존 departure를 반환합니다.
        같은 이름의 다른 지점으로 바꾸려면 주소나 입구 이름을 검색어에 넣어 주세요.
        일정에 남은 식당을 다시 찾으면 이름으로 조회한 뒤 기존 주소와 대조합니다.
        선택한 숙소와 다른 위치 또는 주소 정정의 새 조회 근거가 필요하면 refresh=true입니다.
        lookup_performed가 false이면 기존 좌표를 재사용한 결과입니다.
        """
        if (
            departure_name is not None
            and isinstance(keyword, str)
            and normalize_place_name(keyword) == departure_name
        ):
            return {"status": "ok", "lookup_performed": False, **departure}

        previous_restaurant = _find_saved_restaurant(payload.get("current_plan"), keyword)
        search_keyword = keyword
        if previous_restaurant is not None:
            search_keyword = previous_restaurant[0]
        arguments = {"keyword": search_keyword}
        if has_refresh:
            arguments["refresh"] = refresh
        result = deepcopy(location_tool(**arguments))
        if isinstance(result, dict) and result.get("status") == "ok":
            if previous_restaurant is not None and not _matches_saved_restaurant(result, *previous_restaurant):
                return {"status": "not_found"}
            reference = "found:" + str(uuid4())
            resolved = point(reference, result.get("name"), result)
            if resolved is not None:
                with lock:
                    points[reference] = resolved
                result["point_ref"] = reference
        return result

    def suggest_visit_order(
        *, day: int, place_ids: list[str], fixed_place_ids: list[str] | None = None,
        origin_ref: str | None = None, destination_ref: str | None = None,
    ) -> dict:
        """해당 일차에 배치할 장소만 비교합니다. 여러 날의 장소를 한 번에 넣지 않습니다.

        day는 1부터 시작하는 일차입니다. 경계는 departure, stay:N, found:... 참조입니다.
        숙소가 미정인 경계는 null이며 센터나 전날 관광지로 대신하지 않습니다.
        결과는 직선거리 비교이며 실제 이동시간과 접근성은 별도로 확인합니다.
        """
        if type(day) is not int or day < 1:
            return {"status": "invalid_input", "message": "day는 1 이상의 정수입니다."}
        if require_details is not None:
            error = require_details(place_ids)
            if error is not None:
                return error
        arguments, labels, error = endpoints(origin_ref, destination_ref)
        if error is not None:
            return error
        check_chat_request_control()
        result = order_tool(
            place_ids=place_ids, fixed_place_ids=fixed_place_ids, **arguments,
        )
        return {**deepcopy(result), "day": day, **labels}

    def route(tool, origin_ref, destination_ref, **extra):
        if origin_ref is None or destination_ref is None:
            return {"status": "invalid_input", "message": "이동의 출발·도착 참조가 모두 필요합니다."}
        arguments, labels, error = endpoints(origin_ref, destination_ref)
        if error is not None:
            return error
        check_chat_request_control()
        return {**deepcopy(tool(**arguments, **extra)), **labels}

    def get_driving_time(*, origin_ref: str, destination_ref: str) -> dict:
        """출발·도착 참조로 자동차 경로를 조회합니다. place:DB장소ID, departure, stay:N, found:...을 사용합니다."""
        return route(driving_tool, origin_ref, destination_ref)

    def get_bus_route_summary(*, origin_ref: str, destination_ref: str) -> dict:
        """출발·도착 참조로 버스 전체 경로를 조회합니다. place:DB장소ID, departure, stay:N, found:...을 사용합니다."""
        return route(bus_tool, origin_ref, destination_ref)

    def get_walking_route(
        *, origin_ref: str, destination_ref: str, route_mode: str = "BROAD_FIRST",
    ) -> dict:
        """출발·도착 참조로 도보 경로를 조회합니다. ACCESSIBLE도 실제 휠체어 통행을 보장하지 않습니다."""
        return route(walking_tool, origin_ref, destination_ref, route_mode=route_mode)

    return find_location, suggest_visit_order, [
        get_driving_time, get_bus_route_summary, get_walking_route,
    ]
