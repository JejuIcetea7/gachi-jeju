import math
import sqlite3
from pathlib import Path

DATABASE_FILE = Path(__file__).resolve().parents[3] / "data" / "processed" / "gachi_jeju.db"
EARTH_RADIUS_KM = 6371.0

def calculate_distance_km(
    first_latitude: float,
    first_longitude: float,
    second_latitude: float,
    second_longitude: float,
) -> float:
    """두 좌표 사이의 직선거리(km)를 하버사인 공식으로 계산합니다."""
    first_latitude_radian = math.radians(first_latitude)
    second_latitude_radian = math.radians(second_latitude)
    latitude_difference = math.radians(second_latitude - first_latitude)
    longitude_difference = math.radians(second_longitude - first_longitude)

    latitude_part = math.sin(latitude_difference / 2) ** 2
    longitude_part = (
        math.cos(first_latitude_radian)
        * math.cos(second_latitude_radian)
        * math.sin(longitude_difference / 2) ** 2
    )
    central_angle = 2 * math.asin(math.sqrt(latitude_part + longitude_part))

    distance_km = EARTH_RADIUS_KM * central_angle
    return distance_km

def read_rows(query: str, parameters: tuple) -> list[sqlite3.Row]:
    database_uri = f"file:{DATABASE_FILE}?mode=ro"
    connection = sqlite3.connect(database_uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(query, parameters).fetchall()
    finally:
        connection.close()
    return rows

def read_distance_km(place: dict) -> float:
    return place["distance_km"]

def search_places(
    *,
    center_latitude: float,
    center_longitude: float,
    radius_km: float = 15.0,
    category: str | None = None,
    max_count: int = 10,
) -> dict:
    """기준 좌표에서 반경 안에 있는 무장애 여행지를 가까운 순으로 돌려줍니다.
    category는 공원, 기타, 박물관·전시, 산책로, 시장, 자연, 해변 중 하나이고, 비우면 전체에서 찾습니다.
    distance_km는 직선거리이며 실제 이동시간이 아닙니다."""
    query = "SELECT place_id, name, category, latitude, longitude FROM places"
    parameters = ()
    if category:
        query = query + " WHERE category = ?"
        parameters = (category,)

    try:
        rows = read_rows(query, parameters)
    except sqlite3.Error:
        return {"status": "db_error"}

    nearby_places = []
    for row in rows:
        distance_km = calculate_distance_km(
            center_latitude,
            center_longitude,
            row["latitude"],
            row["longitude"],
        )
        if distance_km > radius_km:
            continue

        place = {
            "place_id": row["place_id"],
            "name": row["name"],
            "category": row["category"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "distance_km": round(distance_km, 1),
        }
        nearby_places.append(place)

    nearby_places.sort(key=read_distance_km)
    selected_places = nearby_places[:max_count]

    result = {"status": "ok", "places": selected_places}
    if len(selected_places) == 0:
        result = {"status": "not_found", "places": []}
    return result

def get_place_details(*, place_id: str) -> dict:
    """여행지 하나의 관람 동선 지점별 접근성 설명(경사, 계단, 화장실, 대여 등)을 돌려줍니다.
    place_id는 search_places 결과의 place_id를 씁니다."""
    place_query = "SELECT place_id, name, category FROM places WHERE place_id = ?"
    spot_query = (
        'SELECT name, description FROM place_spots WHERE place_id = ? ORDER BY "order"'
    )

    try:
        place_rows = read_rows(place_query, (place_id,))
        spot_rows = read_rows(spot_query, (place_id,))
    except sqlite3.Error:
        return {"status": "db_error"}

    if len(place_rows) == 0:
        return {"status": "not_found"}

    spots = []
    seen_spots = set()
    for row in spot_rows:
        description = row["description"]
        if not description:
            continue

        spot_key = (row["name"], description)
        if spot_key in seen_spots:
            continue
        seen_spots.add(spot_key)

        spot = {"name": row["name"], "description": description}
        spots.append(spot)

    place_row = place_rows[0]
    return {
        "status": "ok",
        "place_id": place_row["place_id"],
        "name": place_row["name"],
        "category": place_row["category"],
        "spots": spots,
    }
