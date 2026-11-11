def _is_valid_path(points: object) -> bool:
    if not isinstance(points, list) or len(points) < 2:
        return False

    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            return False

        longitude, latitude = point
        if (
            type(longitude) not in (int, float)
            or type(latitude) not in (int, float)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            return False

    return True


def extract_walking_geometry(steps: object) -> dict:
    """도보 안내 단계에서 지도용 좌표를 추출합니다."""
    if not isinstance(steps, list):
        return {"geometry_status": "missing", "paths": []}

    paths = []
    incomplete = False

    for step in steps:
        points = step.get("path_points") if isinstance(step, dict) else None

        if not _is_valid_path(points):
            incomplete = True
            continue

        paths.append([list(point) for point in points])

    status = "missing" if not paths else "partial" if incomplete else "ok"
    return {"geometry_status": status, "paths": paths}