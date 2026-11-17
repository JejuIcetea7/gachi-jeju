"""현재 요청의 숙소 선택과 일치하는 위치 조회에서 전달된 좌표를 재사용합니다."""

from copy import deepcopy
import json
import unicodedata

from .lodging_planning import (
    LodgingStay, SELECTED_LODGING_SOURCE, validate_lodging_options,
)


def _key(value):
    return unicodedata.normalize("NFC", "".join(value.split())).casefold()


def bind_selected_lodging_location(location_tool, messages):
    """마지막 요청의 선택만 사용하며 이전 대화와 실패한 일정은 근거로 읽지 않습니다."""
    try:
        if not messages or messages[-1].get("role") != "user":
            return location_tool
        payload = json.loads(messages[-1]["content"])
        options = payload["options"]
        status = options.get("lodging_status")
        if status not in ("provided", "undecided"):
            return location_tool
        raw_stays = options.get("lodging_stays")
        if not isinstance(raw_stays, list) or not raw_stays:
            return location_tool
        days = options.get("trip_days")
        if days is not None and (type(days) is not int or days < 1):
            return location_tool
        stays = [LodgingStay.model_validate(stay) for stay in raw_stays]
        validate_lodging_options(days, status, stays)
    except (KeyError, TypeError, ValueError, AttributeError):
        return location_tool

    locations = {}
    ambiguous = set()
    for stay in stays:
        location = stay.model_dump(exclude={"night"})
        keys = [stay.name]
        if stay.address.strip():
            keys += [stay.address, stay.name + " " + stay.address,
                     stay.name + ", " + stay.address]
        for text in keys:
            key = _key(text)
            if key in locations and locations[key] != location:
                ambiguous.add(key)
            else:
                locations[key] = location
    for key in ambiguous:
        locations.pop(key, None)
    if not locations:
        return location_tool

    def find_location(*, keyword: str, refresh: bool = False) -> dict:
        """제주도 장소의 이름·주소·위도·경도를 확인합니다.

        현재 선택한 숙소와 이름·주소가 정확히 일치하면 외부 검색 없이 선택 정보를
        반환하며 source는 selected_lodging, lookup_performed는 false입니다.
        다른 숙소로 변경하거나 주소 정정·새 숙박일 추가 등 새 위치 조회 근거가
        필요하면 refresh=true로 검색합니다. 선택 정보 재사용은 새 조회 근거가 아닙니다.
        """
        key = _key(keyword) if isinstance(keyword, str) else None
        if refresh is False and key in locations:
            return {
                "status": "ok", "source": SELECTED_LODGING_SOURCE,
                "lookup_performed": False, **deepcopy(locations[key]),
            }
        return location_tool(keyword=keyword)

    return find_location
