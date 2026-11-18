"""최종 일정과 명시적인 귀환 안내·숙소 미정 메모를 대조합니다."""

import re

from .lodging_planning import UNDECIDED_LODGING_NOTE, _night_choices

_CLOCK = r"(?:오전|오후)?\s*\d{1,2}\s*(?::\s*\d{2}|시\s*\d{1,2}\s*분)"
_RETURN_AFTER = re.compile(
    r"(?:귀환|복귀)(?:편)?"
    r"(?:\s*(?:은|는|이|가|의|시각|시간|도착|예정|예상|약|:))*\s*"
    rf"(?P<clock>{_CLOCK})"
    rf"(?:\s*(?:~|-|부터)\s*(?P<end>{_CLOCK}))?(?!\d)"
)
_RETURN_BEFORE = re.compile(
    rf"(?<!\d)(?P<clock>{_CLOCK})\s*(?:에|경|쯤)?\s*(?:귀환|복귀)"
)
_VISIT_START = re.compile(
    r"(?:방문|관람)\s*시작\s*(?:시각|시간)"
    r"(?:\s*(?:은|는|이|가|:))*"
    r"(?:\s*(?:별도(?:로)?\s*)?"
    r"(?:지정되지\s*않아|지정하지\s*않아|지정이\s*없어|정하지\s*않아)\s*)?"
    rf"(?P<clock>{_CLOCK})(?!\d)"
)
_DAY = re.compile(r"(?P<day>\d+)\s*일차|(?P<last>마지막\s*날)")
_NON_EXACT = re.compile(
    r"\s*(?:이전|이후|까지|부터|보다|사이|전후|"
    r"(?:이|가|은|는)?\s*(?:아니|아닙|아닌|불가능|불가|미확인))"
)
_UNDECIDED_LODGING = re.compile(
    r"(?:숙소|숙박)[^.!?\n]{0,80}(?:미정|정하지\s*않|정해지지\s*않)"
    r"|미정(?:인)?\s*(?:숙소|숙박)"
)
_MISSING_LODGING_CLAIM = re.compile(
    r"(?:숙소|숙박지)(?:\s*(?:정보|위치))?(?:가|는|이|은)?\s*"
    r"(?:제공되지\s*않(?:은|아)|제공하지\s*않(?:은|아)|"
    r"없(?:어|으므로|습니다)|미정(?:입니다|이라|이므로|으로))"
)


def _missing_lodging_claim(text):
    if text.strip() == UNDECIDED_LODGING_NOTE:
        return True
    for match in _MISSING_LODGING_CLAIM.finditer(text):
        if re.match(
            r"\s*(?:경우|상황|때|도|면|고|것(?:은|이)?\s*아(?:니|닙|닌))",
            text[match.end():],
        ):
            continue
        return True
    return False

def _origin_return_pattern(options):
    names = ["출발지"]
    departure = options.get("departure")
    if isinstance(departure, str) and departure.strip():
        departure = departure.strip()
        names.append(departure)
        if departure.endswith("센터"):
            names.append("센터")
    target = "|".join(re.escape(name) for name in sorted(set(names), key=len, reverse=True))
    return re.compile(
        rf"(?<!\d)(?P<clock>{_CLOCK})\s*(?:에|경|쯤)?\s*"
        rf"(?:같은\s*)?(?:{target})\s*(?:으로|로|에(?:는)?)\s*"
        r"돌아(?:옵니다|와요|오겠습니다|올\s*예정(?:입니다)?)"
    )


def _clock(value):
    match = re.fullmatch(
        r"\s*(?:(오전|오후)\s*)?(\d{1,2})\s*"
        r"(?::\s*(\d{2})|시\s*(\d{1,2})\s*분)\s*",
        value,
    )
    if match is None:
        return None
    period, hour, colon_minutes, korean_minutes = match.groups()
    hour, minute = int(hour), int(colon_minutes or korean_minutes)
    if minute > 59 or hour > 23:
        return None
    if period:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if period == "오후" else 0)
    return f"{hour:02d}:{minute:02d}"


def _day_context(text, position, last_day):
    paragraph = text.rfind("\n\n", 0, position)
    prefix = text[paragraph + 2 if paragraph >= 0 else 0:position]
    matches = list(_DAY.finditer(prefix))
    if not matches:
        return None
    match = matches[-1]
    return int(match["day"]) if match["day"] is not None else last_day


def validate_plan_reply_consistency(reply, plan, *, options, user_message=""):
    """확실히 해석할 수 있는 표현만 검사하며 원본 일정·문구를 변경하지 않습니다.

    귀환·복귀·출발지로 돌아오는 안내와 방문 시작의 HH:MM 또는 시·분 표기를 검사합니다.
    귀환 범위는 종료 시각을 사용하고, 이전·이후 같은 경계 표현은 추정하지 않습니다.
    일차·방문 대상이 불분명하고 시각이 여럿이면 판정하지 않습니다.
    """
    if plan is None:
        return []
    days = plan.get("days", [])
    last_day = max((day["day"] for day in days), default=0)
    visit_times = {
        day["day"]: {
            _clock(item.get("start_time", ""))
            for item in day.get("items", [])
            if item.get("item_type") == "visit"
        } - {None}
        for day in days
    }
    return_times = {}
    if options.get("return_to_origin") is True:
        for day in days:
            items = day.get("items", [])
            if day["day"] != last_day and options.get("lodging_status") != "not_needed":
                continue
            if items and items[-1].get("item_type") == "move":
                value = _clock(items[-1].get("end_time", ""))
                if value is not None:
                    return_times[day["day"]] = value

    texts = [("reply", reply), *[
        (f"assumptions[{index}]", text)
        for index, text in enumerate(plan.get("assumptions", []))
    ]]
    errors = []
    origin_return = _origin_return_pattern(options)
    check_lodging = options.get("lodging_status") == "not_needed" or (
        options.get("trip_days") == 1
        and re.search(r"숙소|숙박", user_message) is None
    )
    trip_days = options.get("trip_days")
    stays = _night_choices(options.get("lodging_stays"))
    all_lodgings_selected = (
        type(trip_days) is int and trip_days > 1
        and len(stays) == trip_days - 1
        and all(1 <= night < trip_days for night in stays)
    )
    for field, text in texts:
        if not isinstance(text, str):
            continue
        if all_lodgings_selected and _missing_lodging_claim(text):
            errors.append(
                f"{field}: 모든 숙박일의 숙소가 선택돼 있는데 숙소가 미정이거나 제공되지 않았다고 설명했습니다. "
                "선택한 숙소와 최종 이동을 반영하고 이전 숙소 미정 안내를 수정하세요."
            )
        if check_lodging and _UNDECIDED_LODGING.search(text):
            errors.append(
                f"{field}: 숙박이 필요하지 않은 일정에 숙소 미정 메모가 있습니다. "
                "여행 조건과 접근성 확인사항은 유지하고 불필요한 숙소 메모를 제외하세요."
            )
        for match in _VISIT_START.finditer(text):
            tail = text[match.end():]
            if (
                _NON_EXACT.match(tail)
                or re.match(r"\s*(?:~|-|부터)", tail)
                or re.match(r"\s*(?:에|으로|로)?\s*출발", tail)
            ):
                continue
            day = _day_context(text, match.start(), last_day)
            expected = (
                visit_times.get(day, set()) if day is not None
                else set().union(*visit_times.values())
            )
            if len(expected) != 1:
                continue
            raw = match["clock"]
            if _clock(raw) not in expected:
                wanted = next(iter(expected))
                errors.append(
                    f"{field}: 방문 시작 안내({raw.strip()})가 최종 일정의 방문 시작 시각"
                    f"({wanted})과 다릅니다. 출발 시각과 방문 시작 시각을 구분해 설명하세요."
                )
        if options.get("return_to_origin") is not True:
            continue
        for pattern in (_RETURN_AFTER, _RETURN_BEFORE, origin_return):
            for match in pattern.finditer(text):
                if _NON_EXACT.match(text[match.end():]):
                    continue
                raw = match.groupdict().get("end") or match["clock"]
                value = _clock(raw)
                day = _day_context(text, match.start(), last_day)
                if day is None:
                    expected = set(return_times.values())
                    if len(expected) > 1:
                        continue
                else:
                    expected = {return_times[day]} if day in return_times else set()
                if value is None or value not in expected:
                    wanted = ", ".join(sorted(expected)) or "확인 가능한 귀환 이동 없음"
                    errors.append(
                        f"{field}: 귀환 안내({raw.strip()})가 최종 일정의 귀환 시각"
                        f"({wanted})과 다릅니다. 최종 일정에 맞게 설명을 수정하세요."
                    )
    return list(dict.fromkeys(errors))