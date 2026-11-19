"""필수 시설의 확인 결과와 일정의 접근 조건을 대조합니다."""

import unicodedata


REQUIREMENT_LABELS = {
    "accessible_toilet": "장애인 화장실",
    "accessible_parking": "장애인 주차구역",
    "step_free": "계단 없는 방문 동선",
}
DIRECT_FIELDS = {
    "accessible_toilet": {"장애인화장실"},
    "accessible_parking": {"장애인주차장유무", "주차장장애인주차유무"},
}
MOBILITY_LABELS = {
    "wheelchair": "휠체어로 이용할 전체 동선",
    "stroller": "유모차로 이용할 전체 동선",
}


def _text(value):
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", "".join(value.split()))


def resolve_facility_status(details: dict, requirement: str) -> str:
    """집계와 명시적인 시설 유무 원문이 일치할 때만 Y/N으로 판정합니다."""
    if requirement not in DIRECT_FIELDS:
        return "unknown"

    evidence = set()
    flags = details.get("accessibility")
    if isinstance(flags, dict):
        flag = _text(flags.get(requirement))
        if flag in ("Y", "N"):
            evidence.add(flag)

    facts = details.get("accessibility_facts")
    for fact in facts if isinstance(facts, list) else []:
        if not isinstance(fact, dict):
            continue
        if _text(fact.get("section")) != "기본정보":
            continue
        if _text(fact.get("description")) not in DIRECT_FIELDS[requirement]:
            continue
        value = _text(fact.get("raw_value"))
        if value in ("있음", "유", "Y"):
            evidence.add("Y")
        elif value in ("없음", "무", "N"):
            evidence.add("N")

    return next(iter(evidence)) if len(evidence) == 1 else "unknown"


def describe_place_accessibility(details, topics, evidence_labels):
    name = details["name"]
    lines = []
    for topic in dict.fromkeys(topics):
        if topic in DIRECT_FIELDS:
            status = resolve_facility_status(details, topic)
            label = REQUIREMENT_LABELS[topic]
            finding = {"Y": "있음", "N": "없음", "unknown": "미확인"}[status]
            lines.append(f"{name}의 {label} 설치 여부는 장소 기본정보 기준 {finding}입니다.")
        elif topic != "other":
            label = (REQUIREMENT_LABELS | MOBILITY_LABELS)[topic]
            lines.append(f"{name}의 {label}은 미확인입니다. 방문 전 이용할 구간을 확인해 주세요.")

    labels = set(evidence_labels)
    facilities = [_text(REQUIREMENT_LABELS[topic]) for topic in topics if topic in DIRECT_FIELDS]
    if "accessible_parking" in topics:
        facilities.append("장애인주차")
    related_spots = 0
    for spot in details.get("spots") or []:
        description = spot.get("description")
        if not description or description == spot.get("name"):
            continue
        if spot.get("name") not in labels:
            if related_spots >= 2 or not any(term in _text(description) for term in facilities):
                continue
            related_spots += 1
        lines.append(f"{name} 현장 조사 · {spot['name']} — {description}")
    for fact in details.get("accessibility_facts") or []:
        if fact.get("description") in labels and fact.get("raw_value"):
            lines.append(f"{name} 현장 조사 · {fact.get('facility_name') or fact['section']} · {fact['description']} — {fact['raw_value']}")
    if not lines:
        lines.append(f"{name}의 요청하신 접근 조건은 조사 자료에서 확인하지 못했습니다.")
    return lines


def validate_plan_accessibility(
    plan: dict,
    places_by_id: dict,
    *,
    requirements: list[str],
    mobility_aid: str | None = None,
) -> tuple[list[str], list[str]]:
    """DB 조회 결과를 기준으로 위반과 미확인을 나누며 입력은 변경하지 않습니다.

    places_by_id에는 서버가 직접 조회한 장소 상세 결과를 전달합니다.
    AI가 생성한 시설 값이나 설명을 판정 근거로 사용하지 않습니다.
    계단·경사 집계와 시설 설치 여부만으로 전체 동선 이용을 보장하지 않습니다.
    """
    selected = list(dict.fromkeys(requirements))
    if any(value not in REQUIREMENT_LABELS for value in selected):
        raise ValueError("지원하지 않는 필수 이용 조건입니다.")
    if mobility_aid not in (None, "none", "wheelchair", "stroller"):
        raise ValueError("지원하지 않는 이동 보조기구입니다.")

    errors, warnings = [], []
    if not selected and mobility_aid in (None, "none"):
        return errors, warnings

    for day in plan["days"]:
        for index, item in enumerate(day["items"], start=1):
            if item["item_type"] != "visit":
                continue
            place_id = item.get("place_id")
            details = places_by_id.get(place_id)
            if (
                not isinstance(place_id, str) or not place_id.strip()
                or not isinstance(details, dict)
                or details.get("place_id") != place_id
            ):
                details = {}

            title = details.get("name") or item["title"]
            label = f"{day['day']}일차 {index}번째 방문 '{title}'"
            unknown = []
            for requirement in selected:
                status = resolve_facility_status(details, requirement)
                name = REQUIREMENT_LABELS[requirement]
                if status == "N":
                    errors.append(
                        f"{label}: 필수 조건 '{name}'이 없다고 확인되어 "
                        "이 장소를 일정에 포함할 수 없습니다."
                    )
                elif status != "Y":
                    unknown.append(name)

            if mobility_aid in MOBILITY_LABELS:
                unknown.append(MOBILITY_LABELS[mobility_aid])
            if unknown:
                warnings.append(
                    f"{label}: {', '.join(unknown)} 미확인입니다. "
                    "조건 충족이 보장된 일정이 아니므로 방문 전에 확인해 주세요."
                )

    return errors, warnings
