"""대화 요청의 장소 참조를 확인된 좌표로 바꾸어 이동 도구에 전달합니다."""

from copy import deepcopy
import inspect
import json
from threading import Lock
from typing import Literal
from uuid import uuid4
import sqlite3

from .chat_request_control import check_chat_request_control
from ..config import get_kakao_api_key
from .kakao_local import RESTAURANT_CATEGORY_CODE, search_nearby_restaurants
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
        if not isinstance(output, dict) or output.get("status") != "ok" or output.get("lookup_performed") is False:
            continue
        places = output.get("places", [output])
        if not isinstance(places, list):
            continue
        for place in places:
            if not isinstance(place, dict) or place.get("category_group_code") != RESTAURANT_CATEGORY_CODE:
                continue
            reference = place.get("point_ref")
            name = place.get("name")
            address = place.get("address")
            coordinates = _coordinate(place)
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


def restaurant_lookup_failure(tool_trace):
    """식사 장소를 찾기 위해 실행한 조회의 마지막 결과를 읽는다."""
    if read_confirmed_restaurants(tool_trace):
        return None
    for record in reversed(tool_trace):
        arguments = record.get("input")
        if record.get("tool") != "find_location" or not isinstance(arguments, dict) or arguments.get("purpose") != "restaurant":
            continue
        output = record.get("output")
        if not isinstance(output, dict) or output.get("lookup_performed") is False:
            continue
        status = output.get("status")
        if status in ("not_found", "api_error", "rate_limited", "auth_error"):
            return status
        if status == "ok" and output.get("category_group_code") != RESTAURANT_CATEGORY_CODE:
            return "not_found"
        return None
    return None


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

    def register_location(location):
        reference = "found:" + str(uuid4())
        resolved = point(reference, location.get("name"), location)
        if resolved is not None:
            with lock:
                points[reference] = resolved
            location["point_ref"] = reference

    def find_location(
        *, keyword: str = "", refresh: bool = False, purpose: Literal["location", "restaurant"] = "location",
        center_ref: str | None = None,
    ) -> dict:
        """위치나 식당을 찾아 이번 실행에서만 쓰는 point_ref를 반환합니다. 상황에 맞는 인자 조합 하나를 고릅니다.

        상황별 사용법:
        1. 출발지·숙소·관광지 위치: keyword=이름이나 주소. purpose는 기본값 "location".
        2. 이름을 아는 식당: keyword=식당 이름, purpose="restaurant".
        3. 특정 장소 주변 식당: purpose="restaurant", center_ref=기준 장소 참조(place:장소ID, departure, stay:N, 조회한 point_ref).
           keyword는 비우고, 사용자가 음식 종류를 지정했을 때만 그 종류를 넣습니다. 거리순 최대 5곳을 반환합니다.
        4. 선택한 숙소의 다른 지점·주소 정정: keyword=새 이름이나 주소, refresh=true.

        결과 읽기:
        - lookup_performed가 false이면 새로 조회하지 않고 기존 좌표를 재사용한 결과입니다.
        - 검색어가 좌표가 있는 출발지 이름과 같으면 기존 departure를 반환합니다. 같은 이름의 다른 지점은 주소나 입구 이름을 넣습니다.
        - 일정에 남은 식당을 다시 찾으면 기존 주소와 대조하고, 다르면 not_found입니다.
        """
        if center_ref is not None:
            if purpose != "restaurant":
                return {"status": "invalid_input", "message": "center_ref는 주변 식당 조회에 사용합니다."}
            center, error = resolve(center_ref)
            if error is not None:
                return {**error, "lookup_performed": False}
            check_chat_request_control()
            result = search_nearby_restaurants(
                latitude=center["latitude"], longitude=center["longitude"],
                keyword=keyword, api_key=get_kakao_api_key(),
            )
            for candidate in result.get("places", []):
                register_location(candidate)
            return result
        if not keyword.strip():
            return {"status": "invalid_input", "message": "장소 이름이나 주변 식당 검색의 center_ref가 필요합니다."}
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
        if purpose == "restaurant" and isinstance(result, dict) and result.get("status") in ("api_error", "rate_limited", "auth_error"):
            result["message"] = "식당 검색 서비스를 사용할 수 없습니다. 검색을 중단하고 기존 일정과 식사 요청을 유지해 주세요."
        if isinstance(result, dict) and result.get("status") == "ok":
            if previous_restaurant is not None and not _matches_saved_restaurant(result, *previous_restaurant):
                return {"status": "not_found"}
            register_location(result)
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
