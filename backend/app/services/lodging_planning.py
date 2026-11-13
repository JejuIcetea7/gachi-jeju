"""숙박일별 선택 위치와 미정 숙소가 있는 관광 일정의 경계를 처리합니다."""

from copy import deepcopy
import math
from typing import Annotated, Literal
import unicodedata

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from .lodging_models import LodgingCoordinate


LodgingStatus = Literal["undecided", "provided", "not_needed"]


class LodgingStay(LodgingCoordinate):
    model_config = ConfigDict(strict=True, extra="forbid")

    night: int = Field(ge=1, strict=True, description="N일차 관광 후 머무는 N번째 숙박일")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    address: str = Field(default="", max_length=300)
    provider_place_id: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,40}$")] | None = None
    place_url: str | None = None

    @model_validator(mode="after")
    def validate_map_url(self):
        if self.place_url is not None and (
            self.provider_place_id is None
            or self.place_url != "https://place.map.kakao.com/" + self.provider_place_id
        ):
            raise ValueError("숙소의 지도 주소와 검색 식별값이 일치하지 않습니다.")
        return self


def validate_lodging_options(trip_days, lodging_status, stays):
    """이미 형식을 검사한 숙소 목록의 숙박일 중복과 여행 범위를 검사합니다."""
    nights = [stay.night for stay in stays]
    if len(nights) != len(set(nights)):
        raise ValueError("같은 숙박일에는 숙소를 하나만 선택해 주세요.")
    if trip_days is not None and any(night >= trip_days for night in nights):
        raise ValueError("숙박일은 여행 마지막 날보다 앞서야 합니다.")
    if stays and lodging_status not in ("undecided", "provided"):
        raise ValueError("선택한 숙소가 있으면 숙소 상태도 함께 보내 주세요.")


def retain_lodging_state(current, previous):
    """응답에서 숙소 상태를 빠뜨리면 이전 선택을 유지하며 명시한 변경은 존중합니다."""
    result = deepcopy(current)
    if current.get("lodging_status") is not None or previous.get("lodging_status") is None:
        return result
    result["lodging_status"] = previous["lodging_status"]
    days = current.get("trip_days")
    result["lodging_stays"] = [
        deepcopy(stay) for stay in previous.get("lodging_stays", [])
        if days is None or stay["night"] < days
    ]
    return result


def _normalized_name(value):
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", "".join(value.split())).casefold()


def _same_coordinate(first, second):
    for field in ("latitude", "longitude"):
        values = (first.get(field), second.get(field))
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            return False
        if abs(values[0] - values[1]) > 0.000001:
            return False
    return True


def _same_lodging(first, second):
    return (
        first.get("night") == second.get("night")
        and _normalized_name(first.get("name")) == _normalized_name(second.get("name"))
        and _same_coordinate(first, second)
        and first.get("provider_place_id") == second.get("provider_place_id")
    )


def validate_lodging_selections(previous, current, *, user_message, tool_trace):
    """사용자 입력 선택과 이번 요청에서 사용자가 지목한 숙소의 위치 조회만 인정합니다."""
    previous_stays = previous.get("lodging_stays") or []
    user_text = _normalized_name(user_message)
    errors = []
    for stay in current.get("lodging_stays") or []:
        if any(_same_lodging(stay, existing) for existing in previous_stays):
            continue
        confirmed = False
        for record in tool_trace if isinstance(tool_trace, list) else []:
            if not isinstance(record, dict) or record.get("tool") != "find_location":
                continue
            output = record.get("output")
            if not isinstance(output, dict) or output.get("status") != "ok":
                continue
            name = _normalized_name(output.get("name"))
            address = _normalized_name(output.get("address"))
            if not name or name != _normalized_name(stay.get("name")):
                continue
            if (
                stay.get("provider_place_id") is not None
                or not _same_coordinate(stay, output)
            ):
                continue
            # 이름 또는 확인한 전체 주소가 현재 사용자 메시지에 있어야 합니다.
            if name in user_text or (address and address in user_text):
                confirmed = True
                break
        if not confirmed:
            errors.append(
                f"{stay['night']}번째 숙박일: 숙소 '{stay['name']}'의 사용자 선택 또는 "
                "이번 요청에서 확인한 위치 조회 근거가 없습니다. 미정 숙소를 임의로 선택하지 마세요."
            )
    return errors


def _night_choices(stays):
    choices = {}
    duplicates = set()
    for stay in stays if isinstance(stays, list) else []:
        if not isinstance(stay, dict) or type(stay.get("night")) is not int or stay["night"] < 1:
            continue
        night = stay["night"]
        if night in choices:
            duplicates.add(night)
        choices[night] = stay
    return {night: stay for night, stay in choices.items() if night not in duplicates}


UNDECIDED_LODGING_NOTE = (
    "숙소가 미정인 구간의 이동을 제외한 관광 일정입니다. "
    "현재 방문 시각은 유지했으며 숙소 선택 후 전후 이동과 시간을 다시 계산합니다."
)


def prepare_undecided_lodging_plan(plan, *, trip_days, lodging_status, lodging_stays):
    """숙소 미정 경계의 이동만 제외하며 방문 시각·내부 이동·원본은 변경하지 않습니다.

    첫날 최초 출발과 마지막 날 최종 이동, 선택한 숙소 경계는 그대로 검사합니다.
    방문 항목이 없는 날은 경계를 특정할 수 없으므로 자동 정리하지 않습니다.
    """
    prepared = deepcopy(plan)
    if not isinstance(trip_days, int) or trip_days < 2 or lodging_status not in ("undecided", "provided"):
        return prepared, []
    stays = _night_choices(lodging_stays)
    warnings = []
    for day in prepared.get("days", []):
        items = day.get("items", [])
        visits = [index for index, item in enumerate(items) if item.get("item_type") == "visit"]
        if not visits:
            continue
        number = day["day"]
        start = visits[0] if number > 1 and number - 1 not in stays else 0
        stop = visits[-1] + 1 if number < trip_days and number not in stays else len(items)
        removed = start + len(items) - stop
        if removed:
            day["items"] = items[start:stop]
            warnings.append(f"{number}일차: 숙소 미정 구간에 연결된 이동 {removed}개를 제외했습니다.")
    if warnings:
        assumptions = prepared.setdefault("assumptions", [])
        if UNDECIDED_LODGING_NOTE not in assumptions:
            assumptions.append(UNDECIDED_LODGING_NOTE)
    return prepared, warnings


def validate_lodging_boundaries(plan, *, trip_days, lodging_status, lodging_stays):
    """숙소가 미정인 구간의 임의 이동과 선택한 숙소 이동의 누락을 막습니다.

    숙소 이동은 관광지 방문 항목으로 만들지 않고 일별 첫·마지막 이동으로 처리합니다.
    기존 요청과 당일 일정, 숙박이 필요 없다고 명시한 요청에는 이 검사를 적용하지 않습니다.
    """
    if not isinstance(trip_days, int) or trip_days < 2 or lodging_status not in ("undecided", "provided"):
        return []
    stays = _night_choices(lodging_stays)
    errors = []
    for day in plan.get("days", []):
        items = day.get("items", [])
        if not items:
            continue
        number = day["day"]
        has_visit = any(item.get("item_type") == "visit" for item in items)
        first_is_move = items[0].get("item_type") == "move"
        last_is_move = items[-1].get("item_type") == "move"
        if number > 1:
            if number - 1 not in stays and first_is_move:
                errors.append(f"{number}일차: 전날 숙소가 미정이므로 숙소 출발 이동을 임의로 넣을 수 없습니다.")
            elif number - 1 in stays and has_visit and not first_is_move:
                errors.append(f"{number}일차: 선택한 전날 숙소에서 첫 방문지까지의 이동을 포함해 주세요.")
        if number < trip_days:
            if number not in stays and last_is_move:
                errors.append(f"{number}일차: 숙소가 미정이므로 숙소 도착 이동을 임의로 넣을 수 없습니다.")
            elif number in stays and has_visit and not last_is_move:
                errors.append(f"{number}일차: 마지막 방문지에서 선택한 숙소까지의 이동을 포함해 주세요.")
    return errors


def build_lodging_warnings(*, trip_days, lodging_status, lodging_stays):
    if not isinstance(trip_days, int) or trip_days < 2 or lodging_status not in ("undecided", "provided"):
        return []
    selected = sum(1 for night in _night_choices(lodging_stays) if night < trip_days)
    missing = trip_days - 1 - selected
    if missing <= 0:
        return []
    return [
        f"숙소 미정 {missing}박: 해당 숙박일의 숙소 도착 이동과 다음 날 숙소 출발 이동을 제외한 관광 일정입니다. "
        "숙소 선택 후 해당 구간의 이동과 시간을 다시 계산합니다."
    ]
