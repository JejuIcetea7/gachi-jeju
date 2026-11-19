import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .closed_days import STATUS_CLOSED, WEEKDAY_NAMES, judge_closed_on_date
from .opening_hours import check_visit_time_window, find_opening_hours


def _to_minutes(value: str) -> int:
    if not isinstance(value, str) or re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value
    ) is None:
        raise ValueError("시각은 HH:MM 형식이어야 합니다.")

    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_plan_times(
    plan: dict,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[str]:
    """일정을 수정하지 않고 시간 검증에서 발견한 문제를 반환합니다."""
    if (start_time is None) != (end_time is None):
        raise ValueError("시작 시각과 종료 시각은 함께 지정해야 합니다.")

    window_start = _to_minutes(start_time) if start_time is not None else None
    window_end = _to_minutes(end_time) if end_time is not None else None

    if (
        window_start is not None
        and window_end is not None
        and window_end <= window_start
    ):
        raise ValueError("종료 시각은 시작 시각보다 늦어야 합니다.")

    errors = []

    for day in plan["days"]:
        latest_end = None

        for index, item in enumerate(day["items"], start=1):
            label = f"{day['day']}일차 {index}번째 일정"

            try:
                item_start = _to_minutes(item.get("start_time"))
                item_end = _to_minutes(item.get("end_time"))
            except ValueError:
                errors.append(
                    f"{label}: 시각은 00:00~23:59 범위의 HH:MM 형식이어야 합니다."
                )
                continue

            if item_end <= item_start:
                errors.append(
                    f"{label}: 종료 시각은 시작 시각보다 늦어야 합니다."
                )
                continue

            # 지정한 여행 가능 시간을 벗어나는지 확인합니다.
            if (
                window_start is not None
                and window_end is not None
                and (item_start < window_start or item_end > window_end)
            ):
                errors.append(
                    f"{label}: 여행 가능 시간({start_time}~{end_time})을 벗어났습니다."
                )

            # 앞선 모든 일정의 종료 시각과 비교합니다.
            if latest_end is not None and item_start < latest_end:
                errors.append(
                    f"{label}: 앞선 일정과 시간이 겹치거나 "
                    "시간순으로 배치되지 않았습니다."
                )

            latest_end = (
                max(latest_end, item_end)
                if latest_end is not None
                else item_end
            )

            # 표시한 소요시간이 실제 시간표의 길이와 일치하는지 확인합니다.
            duration = item.get("duration_minutes")
            rest = item.get("rest_minutes")
            if rest is not None and (item.get("item_type") != "visit" or rest > item_end - item_start):
                errors.append(f"{label}: 휴식은 방문 체류시간 안에 포함해 주세요.")

            if type(duration) is not int or duration != item_end - item_start:
                reason = (
                    f"{label}: 소요시간은 시작·종료 시각의 차이인 "
                    f"{item_end - item_start}분과 일치해야 합니다."
                )
                if type(duration) is int and duration > 0:
                    reason += (
                        f" 현재 {item['start_time']}~{item['end_time']}에 "
                        f"duration_minutes={duration}를 적었습니다."
                    )
                    calculated_end = item_start + duration
                    if calculated_end < 24 * 60:
                        clock = f"{calculated_end // 60:02d}:{calculated_end % 60:02d}"
                        reason += (
                            f" 계산 참고: {item['start_time']} + {duration}분 = {clock}."
                        )
                    reason += (
                        " 사용자 고정 시각과 이번에 조회한 이동시간을 지키면서 "
                        "시작·종료 시각과 소요시간을 다시 계산하세요."
                    )
                errors.append(reason)

    return errors


_MEAL_TARGET = "@meal"
_ALL_VISITS = "@visits"

CLOCK_KINDS = ("start", "end", "earliest", "latest")
LENGTH_KINDS = ("duration", "minimum", "maximum")
LOWER_CLOCK_KINDS = ("start", "earliest")
UPPER_CLOCK_KINDS = ("end", "latest")

# 새 조건이 같은 대상의 기존 조건 중 무엇을 대신하는지 정한다.
# 최소·최대 체류처럼 함께 쓸 수 있는 조건은 서로 지우지 않는다.
REPLACED_KINDS = {
    "start": ("start", "earliest"),
    "earliest": ("start", "earliest"),
    "end": ("end", "latest"),
    "latest": ("end", "latest"),
    "duration": ("duration", "minimum", "maximum"),
    "minimum": ("duration", "minimum"),
    "maximum": ("duration", "maximum"),
    "rest": ("rest",),
}

RULE_LABELS = {
    "earliest": "시작 하한",
    "latest": "종료 상한",
    "start": "시작",
    "end": "종료",
    "duration": "체류시간",
    "minimum": "최소 체류시간",
    "maximum": "최대 체류시간",
    "rest": "휴식 시간",
}


def _visit_key(item):
    if item.get("restaurant_address"):
        return f"식당 {item['title']} ({item['restaurant_address']})"
    return item.get("place_id") or item["title"]


def _is_meal(item):
    return bool(item.get("restaurant_address"))


def _format_clock(minutes):
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}:{minute:02d}"


def _format_rule_value(kind, minutes):
    if kind in CLOCK_KINDS:
        return _format_clock(minutes)
    return f"{minutes}분"


class ScheduleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int | None = Field(default=None, ge=1, strict=True,
        description="적용 일차. 모든 일차면 null. 여러 일차에 있는 방문을 move하면 원래 일차")
    visit: str | None = Field(default=None, max_length=400,
        description="관광지 DB ID, 기존 식당의 schedule_key, 모든 식사 @meal, 모든 관광지 @visits. 하루 전체의 시작·끝과 불특정 관광지 추가는 null")
    kind: Literal["add", "remove", "move", "start", "end", "earliest", "latest", "duration", "minimum", "maximum", "rest", "release"] = Field(
        description="add·remove·move: 방문 추가·삭제·일차 이동. start·end: 정확한 시작·종료 시각. earliest·latest: 시작 하한·종료 상한. "
                    "duration·minimum·maximum: 체류시간. rest: 체류에 포함한 휴식. release: 기존 고정 조건 해제")
    minutes: int | None = Field(default=None, ge=0, le=1440, strict=True,
        description="시각은 자정 이후 분(0~1439, 11시는 660), 체류·휴식은 1 이상의 분")
    destination_day: int | None = Field(default=None, ge=1, strict=True, description="move의 새 일차")
    count: int = Field(default=1, ge=1, le=100, strict=True, description="visit=null 불특정 관광지나 @meal 추가의 개수")
    category: str | None = Field(default=None, max_length=80, description="visit=null 불특정 관광지 추가의 분류")
    evidence: str = Field(min_length=1, max_length=1000, description="해당 조건의 사용자 원문 연속 구절")

    @model_validator(mode="after")
    def validate_values(self):
        if self.kind in CLOCK_KINDS or self.kind in LENGTH_KINDS or self.kind == "rest":
            if self.minutes is None:
                raise ValueError("시간 조건에는 minutes가 필요합니다.")
        if self.kind in CLOCK_KINDS and self.minutes > 1439:
            raise ValueError("시각 조건의 minutes는 0~1439(00:00~23:59)여야 합니다.")
        if self.kind in LENGTH_KINDS or self.kind == "rest":
            if self.minutes < 1:
                raise ValueError("체류·휴식 시간은 1분 이상이어야 합니다.")
        if self.kind == "move" and (self.visit is None or self.destination_day is None):
            raise ValueError("방문일 변경에는 visit과 destination_day가 필요합니다.")
        if self.kind in ("remove", "move") and self.visit == _ALL_VISITS:
            raise ValueError("관광지 전체 교체는 @visits 대신 replace_visits=true로 기록하세요.")
        if self.kind == "remove" and self.visit is None:
            raise ValueError("방문 삭제에는 visit이 필요합니다.")
        if self.kind == "add" and self.visit == _ALL_VISITS:
            raise ValueError("불특정 관광지 추가는 visit=null과 count·category로 기록하세요.")
        return self


class VisitOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1, strict=True)
    place_ids: list[str] = Field(min_length=1, max_length=100, description="사용자가 정한 방문 순서의 DB 장소 ID. 중복 없음")
    evidence: str = Field(min_length=1, max_length=1000, description="순서를 정한 사용자 원문 연속 구절")

    @model_validator(mode="after")
    def validate_unique_places(self):
        if len(self.place_ids) != len(set(self.place_ids)):
            raise ValueError("방문 순서에 같은 장소를 두 번 넣을 수 없습니다.")
        return self


@dataclass
class ScheduleConstraints:
    limits: list[dict]
    removed: set
    additions: set
    additional_counts: dict
    destinations: dict
    changed_durations: set
    replace_visits: bool
    meal_additions: dict
    orders: list[dict] = field(default_factory=list)


def read_fixed_trip_options(options, proposed, changed_fields):
    """선언한 변경 필드만 제안 값으로 바꾸고 나머지는 현재 값을 그대로 둡니다.

    현재 값이 비어 있어도 선언하지 않은 필드는 채우지 않습니다.
    """
    fixed = dict(options)
    fields = set(changed_fields)
    if "departure" in fields:
        fields.add("departure_latitude")
        fields.add("departure_longitude")
    for name in fields:
        if name not in proposed:
            raise ValueError(f"변경 항목 {name}의 값이 없습니다.")
        fixed[name] = proposed[name]
    return fixed


def read_previous_visit_days(previous):
    """기존 일정의 방문 키마다 방문한 일차 목록을 읽습니다."""
    visit_days = {}
    for day in previous.get("days", []):
        for item in day["items"]:
            if item["item_type"] != "visit":
                continue
            key = _visit_key(item)
            days = visit_days.setdefault(key, [])
            if day["day"] not in days:
                days.append(day["day"])
    return visit_days


def read_previous_meal_keys(previous, day_number):
    """기존 일정에서 해당 일차(None이면 전체)의 식사 키를 읽습니다."""
    meal_keys = []
    for day in previous.get("days", []):
        if day_number is not None and day["day"] != day_number:
            continue
        for item in day["items"]:
            if item["item_type"] == "visit" and _is_meal(item):
                meal_keys.append((day["day"], _visit_key(item)))
    return meal_keys


def check_change_days(change, trip_days):
    if trip_days is None:
        return
    for number in (change.day, change.destination_day):
        if number is not None and number > trip_days:
            raise ValueError(
                f"변경 조건의 일차 {number}가 실제 여행 기간 {trip_days}일을 벗어납니다: {change.evidence}"
            )


def read_removed_targets(change, previous, visit_days):
    """삭제 조건의 대상이 기존 일정에 실제로 있는지 확인하고 삭제 키를 반환합니다."""
    if change.visit == _MEAL_TARGET:
        meal_keys = read_previous_meal_keys(previous, change.day)
        if len(meal_keys) == 0:
            raise ValueError(f"삭제할 식사가 기존 일정에 없습니다: {change.evidence}")
        targets = []
        for _, key in meal_keys:
            targets.append((change.day, key))
        return targets

    days = visit_days.get(change.visit, [])
    if len(days) == 0:
        raise ValueError(f"삭제 대상 {change.visit}이(가) 기존 일정에 없습니다: {change.evidence}")
    if change.day is not None and change.day not in days:
        raise ValueError(
            f"삭제 대상 {change.visit}은(는) 기존 {days}일차에 있고 {change.day}일차에는 없습니다: {change.evidence}"
        )
    return [(change.day, change.visit)]


def read_move_source_day(change, visit_days):
    """방문일 변경의 원래 일차를 하나로 정합니다."""
    days = visit_days.get(change.visit, [])
    if len(days) == 0:
        raise ValueError(f"방문일을 옮길 대상 {change.visit}이(가) 기존 일정에 없습니다: {change.evidence}")
    if change.day is None:
        if len(days) > 1:
            raise ValueError(
                f"{change.visit}은(는) 기존 {days}일차에 모두 있으므로 옮길 원래 일차를 day로 지정하세요: {change.evidence}"
            )
        return days[0]
    if change.day not in days:
        raise ValueError(
            f"{change.visit}은(는) 기존 {days}일차에 있고 {change.day}일차에는 없습니다: {change.evidence}"
        )
    return change.day


def find_limit_conflicts(limits):
    """같은 대상의 시각·체류 조건끼리 동시에 지킬 수 없는 경우를 찾습니다."""
    groups = {}
    for (day, visit, kind), minutes in limits.items():
        groups.setdefault((day, visit), {})[kind] = minutes

    conflicts = []
    for (day, visit), kinds in groups.items():
        scope = "모든 일차" if day is None else f"{day}일차"
        target = "하루 일정" if visit is None else visit
        label = f"{scope} {target}"

        lower = None
        for kind in LOWER_CLOCK_KINDS:
            if kind in kinds:
                lower = kinds[kind]
        upper = None
        for kind in UPPER_CLOCK_KINDS:
            if kind in kinds:
                upper = kinds[kind]
        if lower is not None and upper is not None and lower >= upper:
            conflicts.append(
                f"{label}: 시작 {_format_clock(lower)}이 종료 {_format_clock(upper)}보다 늦거나 같습니다."
            )

        shortest = kinds.get("minimum")
        longest = kinds.get("maximum")
        exact = kinds.get("duration")
        if shortest is not None and longest is not None and shortest > longest:
            conflicts.append(f"{label}: 최소 체류 {shortest}분이 최대 체류 {longest}분보다 깁니다.")
        if exact is not None and shortest is not None and exact < shortest:
            conflicts.append(f"{label}: 체류 {exact}분이 최소 체류 {shortest}분보다 짧습니다.")
        if exact is not None and longest is not None and exact > longest:
            conflicts.append(f"{label}: 체류 {exact}분이 최대 체류 {longest}분보다 깁니다.")

        needed = exact
        if needed is None:
            needed = shortest
        if visit is not None and lower is not None and upper is not None and needed is not None:
            if lower + needed > upper:
                conflicts.append(
                    f"{label}: {_format_clock(lower)}~{_format_clock(upper)} 안에 {needed}분 체류를 넣을 수 없습니다."
                )
    return conflicts


def read_schedule_constraints(previous, changes, *, trip_days=None, orders=(), replace_visits=False):
    """LLM이 구조화한 조건을 보존된 일정 조건과 병합합니다. 사용자 문장은 해석하지 않습니다.

    대상이 기존 일정에 없거나, 일차가 기간을 벗어나거나, 조건끼리 충돌하면 ValueError를 냅니다.
    """
    if not isinstance(changes, list):
        raise ValueError("시간·방문 변경은 구조화된 목록이어야 합니다.")

    limits = {}
    for limit in previous.get("schedule_limits", []):
        key = (limit["day"], limit["visit"], limit["kind"])
        limits[key] = limit["minutes"]

    if replace_visits:
        kept_limits = {}
        for key, minutes in limits.items():
            if key[1] is None:
                kept_limits[key] = minutes
        limits = kept_limits

    if trip_days is not None:
        kept_limits = {}
        for key, minutes in limits.items():
            if key[0] is None or key[0] <= trip_days:
                kept_limits[key] = minutes
        limits = kept_limits

    visit_days = read_previous_visit_days(previous)
    removed = set()
    additions = set()
    changed_durations = set()
    additional_counts = {}
    destinations = {}
    meal_additions = {}
    requested_limits = {}

    for value in changes:
        change = ScheduleChange.model_validate(value)
        check_change_days(change, trip_days)
        day = change.day
        visit = change.visit
        kind = change.kind

        if kind == "add":
            if visit == _MEAL_TARGET:
                meal_additions[day] = meal_additions.get(day, 0) + change.count
            elif visit is None:
                slot = (day, change.category)
                additional_counts[slot] = additional_counts.get(slot, 0) + change.count
            else:
                additions.add((day, visit))
            continue

        if kind == "remove":
            for target in read_removed_targets(change, previous, visit_days):
                removed.add(target)
            continue

        if kind == "move":
            source_day = read_move_source_day(change, visit_days)
            destinations[(source_day, visit)] = change.destination_day
            continue

        if kind == "release":
            kept_limits = {}
            for key, minutes in limits.items():
                same_visit = key[1] == visit
                same_day = day is None or key[0] == day
                if not (same_visit and same_day):
                    kept_limits[key] = minutes
            limits = kept_limits
            continue

        key = (day, visit, kind)
        if key in requested_limits and requested_limits[key] != change.minutes:
            raise ValueError(
                f"같은 대상에 서로 다른 {RULE_LABELS[kind]} 조건 "
                f"{_format_rule_value(kind, requested_limits[key])}와 "
                f"{_format_rule_value(kind, change.minutes)}를 기록했습니다: {change.evidence}"
            )
        requested_limits[key] = change.minutes
        for old_kind in REPLACED_KINDS[kind]:
            old_key = (day, visit, old_kind)
            if old_key not in requested_limits:
                limits.pop(old_key, None)
        limits[key] = change.minutes
        if kind in LENGTH_KINDS or kind == "end":
            changed_durations.add((day, visit))

    moved_limits = {}
    for key, minutes in limits.items():
        day, visit, kind = key
        if (day, visit) in removed or (None, visit) in removed:
            continue
        new_day = day
        if day is not None and (day, visit) in destinations:
            new_day = destinations[(day, visit)]
        moved_limits[(new_day, visit, kind)] = minutes
    limits = moved_limits

    conflicts = find_limit_conflicts(limits)
    if conflicts:
        raise ValueError(
            "시간 조건끼리 동시에 지킬 수 없습니다. 사용자 조건이 충돌하면 needs_clarification=true로 확인 질문을 하세요: "
            + " ".join(conflicts)
        )

    parsed_orders = []
    for order in orders:
        parsed = VisitOrder.model_validate(order)
        if trip_days is not None and parsed.day > trip_days:
            raise ValueError(f"방문 순서의 일차 {parsed.day}가 실제 여행 기간 {trip_days}일을 벗어납니다.")
        parsed_orders.append(parsed.model_dump())

    limit_rows = []
    for (day, visit, kind), minutes in limits.items():
        limit_rows.append({"day": day, "visit": visit, "kind": kind, "minutes": minutes})

    return ScheduleConstraints(
        limit_rows, removed, additions, additional_counts, destinations,
        changed_durations, replace_visits, meal_additions, parsed_orders,
    )


def group_visits_by_day(plan):
    """(일차, 방문 키)마다 방문 항목 목록을 묶습니다."""
    groups = {}
    for day in plan.get("days", []):
        for item in day["items"]:
            if item["item_type"] != "visit":
                continue
            key = (day["day"], _visit_key(item))
            groups.setdefault(key, []).append(item)
    return groups


def is_subsequence(values, sequence):
    """values가 sequence 안에 같은 순서로 모두 들어 있는지 확인합니다."""
    position = 0
    for value in sequence:
        if position < len(values) and values[position] == value:
            position += 1
    return position == len(values)


def is_duration_changed(constraints, key, day_number, is_meal):
    targets = [key]
    if is_meal:
        targets.append(_MEAL_TARGET)
    else:
        targets.append(_ALL_VISITS)
    for scope in (None, day_number):
        for target in targets:
            if (scope, target) in constraints.changed_durations:
                return True
    return False


def validate_previous_visits(previous_groups, actual, constraints, last_day):
    """기존 방문의 유지·삭제·이동·체류시간을 검사하고, 기간 축소로 빠진 방문 키를 반환합니다."""
    errors = []
    dropped_keys = set()
    for (day, key), items in previous_groups.items():
        title = items[0]["title"]
        is_meal = _is_meal(items[0])
        if constraints.replace_visits and not is_meal:
            continue

        destination_day = constraints.destinations.get((day, key), day)
        visits = actual.get((destination_day, key), [])
        if (day, key) in constraints.removed or (None, key) in constraints.removed:
            if visits:
                errors.append(f"{day}일차 {title}: 사용자가 제외한 방문이 남아 있습니다.")
            continue

        if destination_day > last_day:
            dropped_keys.add(key)
            continue

        more_meals = is_meal and len(visits) > len(items)
        if len(visits) != len(items) and not more_meals:
            errors.append(
                f"{day}일차 {title}: 기존 일정의 방문일({destination_day}일차)과 방문 횟수({len(items)}회)를 유지해 주세요. "
                f"실제 {len(visits)}회입니다."
            )
            continue

        if is_duration_changed(constraints, key, destination_day, is_meal):
            continue

        old_minutes = []
        for item in items:
            old_minutes.append(item["duration_minutes"])
        new_minutes = []
        for visit in visits:
            new_minutes.append(visit["duration_minutes"])
        if more_meals and is_subsequence(old_minutes, new_minutes):
            continue
        if old_minutes != new_minutes:
            errors.append(
                f"{day}일차 {title}: 기존 일정의 체류시간 {old_minutes}분을 유지해 주세요. 실제 {new_minutes}분입니다."
            )
    return errors, dropped_keys


def take_count_slot(remaining, day, category):
    for slot in ((day, category), (None, category), (day, None), (None, None)):
        if remaining.get(slot, 0) > 0:
            remaining[slot] -= 1
            return True
    return False


def take_meal_slot(meal_slots, day):
    for scope in (day, None):
        if meal_slots.get(scope, 0) > 0:
            meal_slots[scope] -= 1
            return True
    return False


def validate_added_visits(previous_groups, actual, constraints, dropped_keys):
    """요청 근거가 있는 방문만 추가됐는지, 요청한 추가가 모두 들어갔는지 검사합니다."""
    errors = []
    has_previous_plan = len(previous_groups) > 0
    permitted = set()
    for day, key in previous_groups:
        permitted.add((constraints.destinations.get((day, key), day), key))
    remaining = dict(constraints.additional_counts)
    meal_slots = dict(constraints.meal_additions)

    for (day, key), items in actual.items():
        if _is_meal(items[0]):
            previous_count = len(previous_groups.get((day, key), []))
            for _ in range(len(items) - previous_count):
                if take_meal_slot(meal_slots, day):
                    continue
                if has_previous_plan:
                    errors.append(f"{day}일차 {items[0]['title']}: 추가 요청이 없는 식사 방문입니다.")
            continue

        if not has_previous_plan or constraints.replace_visits:
            continue
        if (day, key) in permitted or key in dropped_keys:
            continue
        if (day, key) in constraints.additions or (None, key) in constraints.additions:
            continue
        for item in items:
            if item.get("verified") and take_count_slot(remaining, day, item.get("category")):
                continue
            errors.append(f"{day}일차 {item['title']}: 요청 근거 없이 추가한 방문입니다.")

    if not constraints.replace_visits:
        for count in remaining.values():
            if count > 0:
                errors.append("추가를 요청한 종류와 개수에 맞게 확인된 여행지를 포함해 주세요.")
                break
    for count in meal_slots.values():
        if count > 0:
            errors.append("사용자가 추가를 요청한 식사 방문을 해당 일정에 포함해 주세요.")
            break

    for day, key in constraints.additions:
        found = False
        for number, location in actual:
            if (day is None or day == number) and location == key:
                found = True
        if not found:
            errors.append(f"추가를 요청한 방문({key})이 일정에 없습니다.")
    return errors


def item_matches_target(item, target):
    if item["item_type"] != "visit":
        return False
    if target == _MEAL_TARGET:
        return _is_meal(item)
    if target == _ALL_VISITS:
        return not _is_meal(item)
    return _visit_key(item) == target


def read_rule_value(kind, rows):
    if kind == "rest":
        total = 0
        for item in rows:
            total += item.get("rest_minutes") or 0
        return total
    if kind in LENGTH_KINDS:
        return rows[0]["duration_minutes"]
    if kind in LOWER_CLOCK_KINDS:
        return _to_minutes(rows[0]["start_time"])
    return _to_minutes(rows[-1]["end_time"])


def is_rule_satisfied(kind, value, expected):
    if kind in ("earliest", "minimum"):
        return value >= expected
    if kind in ("latest", "maximum"):
        return value <= expected
    return value == expected


def has_day_specific_rule(rules, rule, day_number):
    for other in rules:
        if other["day"] == day_number and other["visit"] == rule["visit"] and other["kind"] == rule["kind"]:
            return True
    return False


def validate_schedule_rules(plan, rules):
    """사용자 시각·체류·휴식 조건을 최종 일정의 실제 값과 대조합니다."""
    errors = []
    for rule in rules:
        kind = rule["kind"]
        target = rule["visit"]
        expected = rule["minutes"]
        matched = False
        for day in plan["days"]:
            if rule["day"] is not None and rule["day"] != day["day"]:
                continue
            if not day["items"]:
                continue
            if rule["day"] is None and has_day_specific_rule(rules, rule, day["day"]):
                matched = True
                continue

            groups = []
            if target is None:
                groups.append(day["items"])
            else:
                for item in day["items"]:
                    if item_matches_target(item, target):
                        groups.append([item])

            for rows in groups:
                matched = True
                value = read_rule_value(kind, rows)
                if is_rule_satisfied(kind, value, expected):
                    continue
                errors.append(
                    f"{day['day']}일차 {rows[0]['title']}: 사용자 지정 {RULE_LABELS[kind]} "
                    f"{_format_rule_value(kind, expected)}를 지켜 주세요. 실제 값은 {_format_rule_value(kind, value)}입니다."
                )
        if not matched and target is not None:
            errors.append(f"시간 조건이 지정된 방문({target})을 일정에서 확인할 수 없습니다.")
    return errors


def validate_visit_orders(plan, orders):
    errors = []
    for order in orders:
        actual_order = []
        for day in plan["days"]:
            if day["day"] != order["day"]:
                continue
            for item in day["items"]:
                if item["item_type"] == "visit" and item.get("place_id") in order["place_ids"]:
                    actual_order.append(item.get("place_id"))
        if actual_order != order["place_ids"]:
            errors.append(
                f"{order['day']}일차: 요청한 방문 순서 {order['place_ids']}를 지켜 주세요. 실제 순서는 {actual_order}입니다."
            )
    return errors


def validate_schedule_constraints(previous, plan, constraints):
    """확정된 실제 방문 행과 사용자 시간 제약을 최종 일정에 대조한다."""
    actual = group_visits_by_day(plan)
    previous_groups = group_visits_by_day(previous)
    last_day = 0
    for day in plan["days"]:
        last_day = max(last_day, day["day"])

    errors, dropped_keys = validate_previous_visits(previous_groups, actual, constraints, last_day)
    errors.extend(validate_added_visits(previous_groups, actual, constraints, dropped_keys))
    errors.extend(validate_schedule_rules(plan, constraints.limits))
    errors.extend(validate_visit_orders(plan, constraints.orders))
    return errors


def validate_plan_closed_days(
    plan: dict,
    start_date: date,
    closed_days_by_place_id: dict,
) -> list[str]:
    """일정을 수정하지 않고 휴무일에 방문하는 장소를 찾아 반환합니다.
    N일차 날짜는 start_date + (N - 1)일이고, 판정은 services.closed_days 와 같습니다."""
    errors = []

    for day in plan["days"]:
        visit_date = start_date + timedelta(days=day["day"] - 1)
        weekday_name = WEEKDAY_NAMES[visit_date.weekday()]

        for index, item in enumerate(day["items"], start=1):
            if item.get("item_type") != "visit":
                continue
            place_id = item.get("place_id")
            if place_id not in closed_days_by_place_id:
                continue

            closed_days = closed_days_by_place_id[place_id]
            status = judge_closed_on_date(closed_days, visit_date)
            if status != STATUS_CLOSED:
                continue

            label = f"{day['day']}일차 {index}번째 일정"
            errors.append(
                f"{label}: {item.get('title')}은(는) "
                f"{visit_date.isoformat()}({weekday_name}) 휴무일({closed_days})입니다."
            )

    return errors


def validate_plan_opening_hours(
    plan: dict,
    start_date: date,
    opening_hours_by_place_id: dict,
) -> list[str]:
    """개장 전 방문·입장 마감 이후 도착·폐장 이후 종료를 검사합니다.

    N일차 날짜의 달로 운영시간을 선택하며 입력 일정은 변경하지 않습니다.
    개장·입장 마감·폐장 정각은 허용하고, 미확인·상시 개방은 검사하지 않습니다.
    """
    errors = []

    for day in plan["days"]:
        visit_date = start_date + timedelta(days=day["day"] - 1)

        for index, item in enumerate(day["items"], start=1):
            if item.get("item_type") != "visit":
                continue
            place_id = item.get("place_id")
            hours = find_opening_hours(
                opening_hours_by_place_id.get(place_id), visit_date
            )
            if hours is None:
                continue

            start_time = item.get("start_time")
            end_time = item.get("end_time")
            try:
                _to_minutes(start_time)
                _to_minutes(end_time)
            except ValueError:
                continue

            opening_time = hours["opening_time"]
            closing_time = hours["closing_time"]
            last_entry_time = hours["last_entry_time"]
            label = (
                f"{day['day']}일차 {index}번째 일정: "
                f"{item.get('title')}은(는) {visit_date.isoformat()}"
            )
            before_opening, after_last_entry, after_closing = check_visit_time_window(
                hours, start_time, end_time,
            )
            if before_opening:
                errors.append(
                    f"{label} {opening_time}에 여는데 "
                    f"{start_time}에 방문을 시작합니다."
                )
            if after_last_entry:
                errors.append(
                    f"{label} 입장 마감은 {last_entry_time}인데 "
                    f"{start_time}에 방문을 시작합니다."
                )
            if after_closing:
                errors.append(
                    f"{label} {closing_time}에 닫는데 {end_time}에 끝납니다."
                )

    return errors
