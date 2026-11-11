import json
import math

from ..tools.places import read_rows
from .plan_validation import validate_plan_times

CHECK_LABELS = {
    "H1": "일정 생성 여부",
    "H2": "일차 수",
    "H3": "시간",
    "H4": "DB 장소",
    "H5": "검색 근거",
    "H6": "이동시간 근거",
    "H7": "이동수단",
    "H8": "조건 유지",
    "H9": "빈 일차",
}

# 하루 방문이 이 수 이하이면 "얇은 일차"로 셉니다 (기록용, 실패 아님).
THIN_DAY_VISIT_COUNT = 1

# 서버(/chat)는 이 옵션이 비어 있으면 일정을 전달하지 않습니다.
REQUIRED_PLAN_OPTIONS = ["start_date", "trip_days", "transport"]

MOVEMENT_TOOL_NAMES = ["get_driving_time", "get_walking_route", "get_bus_route_summary"]

# 요청한 이동수단에서 부르면 안 되는 이동 도구
FORBIDDEN_MOVEMENT_TOOLS = {
    "walking": ["get_driving_time", "get_bus_route_summary"],
    "driving": ["get_bus_route_summary"],
    "transit": ["get_driving_time"],
}

DURATION_TOLERANCE_MINUTES = 1

# 필수 시설 조건별 DB 칸과 근거로 인정하는 값
REQUIREMENT_EVIDENCE = {
    "step_free": ("has_slope_or_stairs", "N"),
    "accessible_toilet": ("accessible_toilet", "Y"),
    "accessible_parking": ("accessible_parking", "Y"),
}

# ---------- 도구 기록 ----------

def parse_tool_output(content):
    """도구 결과 글이 JSON이면 풀고, 아니면 글 그대로 돌려줍니다."""
    tool_output = content
    if isinstance(content, str):
        try:
            tool_output = json.loads(content)
        except ValueError:
            tool_output = content
    return tool_output

def extract_tool_trace(messages: list) -> list[dict]:
    """Agent 메시지에서 도구 호출과 그 결과를 순서대로 짝지어 꺼냅니다."""
    tool_outputs = {}
    for message in messages:
        if message.type == "tool":
            tool_outputs[message.tool_call_id] = parse_tool_output(message.content)

    tool_trace = []
    for message in messages:
        if message.type != "ai":
            continue
        for tool_call in message.tool_calls:
            call_id = tool_call["id"]
            tool_trace.append({
                "call_id": call_id,
                "tool": tool_call["name"],
                "input": tool_call["args"],
                "output": tool_outputs.get(call_id),
            })
    return tool_trace

# ---------- 공통 ----------

def make_check_result(passed: bool | None, reasons: list[str]) -> dict:
    return {"passed": passed, "reasons": reasons}

def read_request_options(case: dict) -> dict:
    request = case["request"]
    options = request.get("options")
    if options is None:
        options = {}
    return options

def read_delivered_plan(chat_reply: dict | None) -> dict | None:
    """서버와 같이, 필수 옵션이 빠진 일정은 전달되지 않은 것으로 봅니다."""
    if chat_reply is None:
        return None

    plan = chat_reply.get("plan")
    if plan is None:
        return None

    options = chat_reply["options"]
    for option_name in REQUIRED_PLAN_OPTIONS:
        if options.get(option_name) is None:
            return None
    return plan

def list_items_by_type(plan: dict, item_type: str) -> list[tuple[int, dict]]:
    items = []
    for day in plan["days"]:
        for item in day["items"]:
            if item["item_type"] == item_type:
                items.append((day["day"], item))
    return items

def read_places_by_id(plan: dict | None) -> dict[str, dict]:
    places_by_id = {}
    if plan is None:
        return places_by_id

    query = (
        "SELECT place_id, name, has_slope_or_stairs, accessible_toilet, accessible_parking "
        "FROM places WHERE place_id = ?"
    )
    for day_number, item in list_items_by_type(plan, "visit"):
        place_id = item["place_id"]
        if not place_id or place_id in places_by_id:
            continue
        rows = read_rows(query, (place_id,))
        if len(rows) > 0:
            places_by_id[place_id] = dict(rows[0])
    return places_by_id

def convert_seconds_to_minutes(duration_seconds: float) -> int:
    duration_minutes = math.ceil(duration_seconds / 60)
    return duration_minutes

# ---------- 필수 조건 H1~H8 ----------

def check_plan_expected(case: dict, chat_reply: dict | None, plan: dict | None) -> dict:
    """H1: 일정이 나왔는지(또는 되물었는지)가 기대 결과와 같은지 봅니다."""
    if chat_reply is None:
        return make_check_result(False, ["AI 답을 받지 못했습니다."])

    reasons = []
    has_plan = plan is not None
    if chat_reply.get("plan") is not None and plan is None:
        reasons.append("일정은 만들었지만 날짜·일수·이동수단 중 빈 값이 있어 서버가 전달하지 않습니다.")

    expect_plan = case["expect_plan"]
    passed = has_plan == expect_plan
    if not passed:
        if expect_plan:
            reasons.append("일정을 기대했지만 일정이 없습니다.")
        else:
            reasons.append("되묻기를 기대했지만 일정을 만들었습니다.")
    return make_check_result(passed, reasons)

def check_day_count(case: dict, chat_reply: dict | None, plan: dict | None) -> dict:
    """H2: 1일차부터 요청 일수까지 빠짐없이 순서대로 있는지 봅니다."""
    if plan is None:
        return make_check_result(None, [])

    trip_days = read_request_options(case).get("trip_days")
    if trip_days is None:
        trip_days = chat_reply["options"]["trip_days"]

    expected_days = []
    for day_number in range(1, trip_days + 1):
        expected_days.append(day_number)

    actual_days = []
    for day in plan["days"]:
        actual_days.append(day["day"])

    if actual_days == expected_days:
        return make_check_result(True, [])
    return make_check_result(False, [f"일차가 {actual_days}입니다. 기대 값은 {expected_days}입니다."])

def check_times(case: dict, plan: dict | None) -> dict:
    """H3: 시각 형식·겹침·소요시간·지정 시간 범위를 봅니다."""
    if plan is None:
        return make_check_result(None, [])

    start_time = None
    end_time = None
    time_window = case.get("time_window")
    if time_window is not None:
        start_time = time_window["start"]
        end_time = time_window["end"]

    time_errors = validate_plan_times(plan, start_time=start_time, end_time=end_time)
    passed = len(time_errors) == 0
    return make_check_result(passed, time_errors)

def check_places_in_database(plan: dict | None, places_by_id: dict[str, dict]) -> dict:
    """H4: 모든 방문 장소의 place_id가 DB에 있는지 봅니다."""
    if plan is None:
        return make_check_result(None, [])

    reasons = []
    for day_number, item in list_items_by_type(plan, "visit"):
        place_id = item["place_id"]
        if not place_id:
            reasons.append(f"{day_number}일차 '{item['title']}': place_id가 없습니다.")
        elif place_id not in places_by_id:
            reasons.append(f"{day_number}일차 '{item['title']}': {place_id}가 DB에 없습니다.")

    passed = len(reasons) == 0
    return make_check_result(passed, reasons)

def collect_searched_place_ids(tool_trace: list[dict]) -> set[str]:
    searched_place_ids = set()
    for tool_record in tool_trace:
        if tool_record["tool"] != "search_places":
            continue
        tool_output = tool_record["output"]
        if not isinstance(tool_output, dict):
            continue
        places = tool_output.get("places")
        if places is None:
            continue
        for place in places:
            searched_place_ids.add(place["place_id"])
    return searched_place_ids

def check_places_from_search(plan: dict | None, tool_trace: list[dict]) -> dict:
    """H5: 모든 방문 장소가 이번 실행의 search_places 결과에 있었는지 봅니다."""
    if plan is None:
        return make_check_result(None, [])

    searched_place_ids = collect_searched_place_ids(tool_trace)
    reasons = []
    for day_number, item in list_items_by_type(plan, "visit"):
        place_id = item["place_id"]
        if place_id not in searched_place_ids:
            reasons.append(f"{day_number}일차 '{item['title']}': 이번 실행의 장소 검색 결과에 없습니다.")

    passed = len(reasons) == 0
    return make_check_result(passed, reasons)

def collect_movement_minutes(tool_trace: list[dict]) -> list[int]:
    movement_minutes = []
    for tool_record in tool_trace:
        if tool_record["tool"] not in MOVEMENT_TOOL_NAMES:
            continue
        tool_output = tool_record["output"]
        if not isinstance(tool_output, dict):
            continue
        duration_seconds = tool_output.get("duration_seconds")
        if duration_seconds is None:
            continue
        movement_minutes.append(convert_seconds_to_minutes(duration_seconds))
    return movement_minutes

def is_close_minutes(first_minutes: int, second_minutes: int) -> bool:
    difference = abs(first_minutes - second_minutes)
    return difference <= DURATION_TOLERANCE_MINUTES

def check_move_durations(plan: dict | None, tool_trace: list[dict], route_calls: list[dict]) -> dict:
    """H6: 이동시간이 이번 실행의 도구 결과와 같은지 봅니다 (±1분 허용).
    route_id가 있으면 그 조회 결과와, 없으면 이번 실행의 이동 도구 결과 전체와 비교합니다."""
    if plan is None:
        return make_check_result(None, [])

    minutes_by_route_id = {}
    for route_call in route_calls:
        minutes_by_route_id[route_call["route_id"]] = convert_seconds_to_minutes(route_call["duration_seconds"])
    movement_minutes = collect_movement_minutes(tool_trace)

    reasons = []
    for day_number, item in list_items_by_type(plan, "move"):
        label = f"{day_number}일차 '{item['title']}'"
        planned_minutes = item["duration_minutes"]
        if planned_minutes is None:
            reasons.append(f"{label}: 이동시간이 비어 있습니다.")
            continue

        route_id = item.get("route_id")
        if route_id:
            if route_id not in minutes_by_route_id:
                reasons.append(f"{label}: route_id가 이번 실행의 조회 결과에 없습니다.")
                continue
            tool_minutes = minutes_by_route_id[route_id]
            if not is_close_minutes(planned_minutes, tool_minutes):
                reasons.append(f"{label}: 일정 {planned_minutes}분, 도구 결과 {tool_minutes}분입니다.")
            continue

        found_matching_minutes = False
        for tool_minutes in movement_minutes:
            if is_close_minutes(planned_minutes, tool_minutes):
                found_matching_minutes = True
        if not found_matching_minutes:
            reasons.append(f"{label}: 일정 {planned_minutes}분과 같은 도구 결과가 없습니다.")

    passed = len(reasons) == 0
    return make_check_result(passed, reasons)

def check_transport_tools(case: dict, tool_trace: list[dict]) -> dict:
    """H7: 요청한 이동수단과 다른 이동 도구를 부르지 않았는지 봅니다."""
    transport = read_request_options(case).get("transport")
    if transport is None:
        return make_check_result(None, [])

    forbidden_tool_names = FORBIDDEN_MOVEMENT_TOOLS[transport]
    reasons = []
    for tool_record in tool_trace:
        if tool_record["tool"] in forbidden_tool_names:
            reasons.append(f"이동수단 {transport} 요청인데 {tool_record['tool']}를 불렀습니다.")

    passed = len(reasons) == 0
    return make_check_result(passed, reasons)

def check_conditions_kept(case: dict, chat_reply: dict | None) -> dict:
    """H8: 요청한 보조기구·필수 시설을 AI가 임의로 빼지 않았는지 봅니다."""
    if chat_reply is None:
        return make_check_result(False, ["AI 답을 받지 못했습니다."])

    request_options = read_request_options(case)
    reply_options = chat_reply["options"]
    reasons = []

    requested_mobility_aid = request_options.get("mobility_aid")
    if requested_mobility_aid is not None:
        if reply_options.get("mobility_aid") != requested_mobility_aid:
            reasons.append(
                f"보조기구가 {requested_mobility_aid}에서 {reply_options.get('mobility_aid')}로 바뀌었습니다."
            )

    requested_requirements = request_options.get("requirements")
    if requested_requirements is None:
        requested_requirements = []
    reply_requirements = reply_options.get("requirements")
    if reply_requirements is None:
        reply_requirements = []
    for requirement in requested_requirements:
        if requirement not in reply_requirements:
            reasons.append(f"필수 시설 {requirement}가 빠졌습니다.")

    passed = len(reasons) == 0
    return make_check_result(passed, reasons)

def count_visits_by_day(plan: dict) -> list[int]:
    visit_counts = []
    for day in plan["days"]:
        visit_count = 0
        for item in day["items"]:
            if item["item_type"] == "visit":
                visit_count += 1
        visit_counts.append(visit_count)
    return visit_counts

def check_no_empty_day(plan: dict | None) -> dict:
    """H9: 방문이 하나도 없는 일차가 없는지 봅니다."""
    if plan is None:
        return make_check_result(None, [])

    reasons = []
    visit_counts = count_visits_by_day(plan)
    for index, visit_count in enumerate(visit_counts):
        if visit_count == 0:
            day_number = plan["days"][index]["day"]
            reasons.append(f"{day_number}일차에 방문 장소가 없습니다.")

    passed = len(reasons) == 0
    return make_check_result(passed, reasons)

# ---------- 기록 지표 ----------

def calculate_visit_metrics(plan: dict | None) -> dict:
    """일차별 방문 수와 얇은 일차(방문 1곳 이하) 수를 셉니다."""
    visit_metrics = {"day_count": 0, "thin_day_count": 0, "visit_counts_by_day": []}
    if plan is None:
        return visit_metrics

    visit_counts = count_visits_by_day(plan)
    thin_day_count = 0
    for visit_count in visit_counts:
        if visit_count <= THIN_DAY_VISIT_COUNT:
            thin_day_count += 1

    visit_metrics["day_count"] = len(visit_counts)
    visit_metrics["thin_day_count"] = thin_day_count
    visit_metrics["visit_counts_by_day"] = visit_counts
    return visit_metrics

def calculate_requirement_coverage(case: dict, plan: dict | None, places_by_id: dict[str, dict]) -> dict:
    """필수 시설 조건마다 DB 칸으로 확인되는 방문 장소 수를 셉니다."""
    coverage = {}
    if plan is None:
        return coverage

    requirements = read_request_options(case).get("requirements")
    if requirements is None:
        requirements = []

    for requirement in requirements:
        column_name, evidence_value = REQUIREMENT_EVIDENCE[requirement]
        visit_count = 0
        evidence_count = 0
        for day_number, item in list_items_by_type(plan, "visit"):
            place = places_by_id.get(item["place_id"])
            if place is None:
                continue
            visit_count += 1
            if place[column_name] == evidence_value:
                evidence_count += 1
        coverage[requirement] = {"visit_count": visit_count, "evidence_count": evidence_count}
    return coverage

def calculate_process_metrics(messages: list, tool_trace: list[dict]) -> dict:
    """모델 호출 수, 도구별 호출 수, 같은 입력 재호출 수, 토큰 수를 셉니다."""
    model_call_count = 0
    input_tokens = 0
    output_tokens = 0
    for message in messages:
        if message.type != "ai":
            continue
        model_call_count += 1
        usage = getattr(message, "usage_metadata", None)
        if usage:
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)

    tool_call_counts = {}
    seen_calls = set()
    repeated_tool_call_count = 0
    for tool_record in tool_trace:
        tool_name = tool_record["tool"]
        previous_count = tool_call_counts.get(tool_name, 0)
        tool_call_counts[tool_name] = previous_count + 1

        input_text = json.dumps(tool_record["input"], ensure_ascii=False, sort_keys=True)
        call_key = (tool_name, input_text)
        if call_key in seen_calls:
            repeated_tool_call_count += 1
        seen_calls.add(call_key)

    return {
        "model_call_count": model_call_count,
        "tool_call_count": len(tool_trace),
        "tool_call_counts": tool_call_counts,
        "repeated_tool_call_count": repeated_tool_call_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }

# ---------- 실행 1번 평가 ----------

def evaluate_run(case: dict, chat_reply: dict | None, tool_trace: list[dict], route_calls: list[dict]) -> dict:
    """실행 1번의 필수 조건 H1~H8과 데이터 충족도를 계산합니다."""
    plan = read_delivered_plan(chat_reply)
    places_by_id = read_places_by_id(plan)

    checks = {
        "H1": check_plan_expected(case, chat_reply, plan),
        "H2": check_day_count(case, chat_reply, plan),
        "H3": check_times(case, plan),
        "H4": check_places_in_database(plan, places_by_id),
        "H5": check_places_from_search(plan, tool_trace),
        "H6": check_move_durations(plan, tool_trace, route_calls),
        "H7": check_transport_tools(case, tool_trace),
        "H8": check_conditions_kept(case, chat_reply),
        "H9": check_no_empty_day(plan),
    }

    passed = True
    for check_result in checks.values():
        if check_result["passed"] is False:
            passed = False

    return {
        "plan_delivered": plan is not None,
        "passed": passed,
        "checks": checks,
        "requirement_coverage": calculate_requirement_coverage(case, plan, places_by_id),
        "visit_metrics": calculate_visit_metrics(plan),
    }
