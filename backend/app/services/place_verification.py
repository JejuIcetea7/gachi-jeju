from copy import deepcopy
import sqlite3

from ..tools.places import normalize_place_name, search_places_by_name


def _name_key(value):
    return normalize_place_name(value) if isinstance(value, str) else ""


def _valid_place_id(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 80


def _successful_records(tool_trace, tool_names):
    for record in tool_trace if isinstance(tool_trace, list) else []:
        if not isinstance(record, dict):
            continue

        tool_name = record.get("tool")
        output = record.get("output")

        if (
            isinstance(tool_name, str)
            and tool_name in tool_names
            and isinstance(output, dict)
            and output.get("status") == "ok"
        ):
            yield record, output


def _unique_exact_place_id(name):
    result = search_places_by_name(keyword=name, max_count=10)

    if result.get("status") == "db_error":
        raise sqlite3.OperationalError(
            "장소 이름 검증을 위한 DB 조회에 실패했습니다."
        )

    if result.get("status") != "ok":
        return None

    rows = result.get("places")
    if not isinstance(rows, list):
        return None

    name_key = _name_key(name)
    exact = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("name_match") == "exact"
        and _name_key(row.get("name")) == name_key
    ]

    if len(exact) != 1:
        return None

    place_id = exact[0].get("place_id")
    return place_id if _valid_place_id(place_id) else None


def restore_missing_place_ids(plan: dict, tool_trace: list[dict] | None) -> dict:
    """이번 실행의 검색·상세 조회와 DB의 유일한 이름 일치로 누락 ID만 보완합니다."""
    restored = deepcopy(plan)
    searched_ids = set()

    for _, output in _successful_records(
        tool_trace,
        {"search_places", "search_places_by_name"},
    ):
        rows = output.get("places")
        if not isinstance(rows, list):
            continue

        for row in rows:
            if isinstance(row, dict) and _valid_place_id(row.get("place_id")):
                searched_ids.add(row["place_id"])

    detailed_ids = {}

    for record, output in _successful_records(
        tool_trace,
        {"get_place_details"},
    ):
        place_id = output.get("place_id")
        name_key = _name_key(output.get("name"))
        arguments = record.get("input")

        if (
            not _valid_place_id(place_id)
            or place_id not in searched_ids
            or not name_key
            or not isinstance(arguments, dict)
            or arguments.get("place_id") != place_id
        ):
            continue

        detailed_ids.setdefault(name_key, set()).add(place_id)

    resolved_ids = {}

    for day in restored["days"]:
        for item in day["items"]:
            if item.get("item_type") != "visit":
                continue

            current_id = item.get("place_id")
            if current_id is not None and (
                not isinstance(current_id, str) or current_id.strip()
            ):
                continue

            name_key = _name_key(item.get("title"))
            candidates = detailed_ids.get(name_key, set())

            if len(candidates) != 1:
                continue

            if name_key not in resolved_ids:
                resolved_ids[name_key] = _unique_exact_place_id(item["title"])

            place_id = resolved_ids[name_key]
            if place_id in candidates:
                item["place_id"] = place_id

    return restored